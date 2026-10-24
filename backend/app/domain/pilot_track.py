"""Pilote vs consigne : pour chaque consigne, les rendez-vous prévus (un par jour, semaine ou mois civil, dans ton
fuseau) et ce que l'agent a réellement rapporté — fait à temps, en retard (de combien de jours), manqué, montant
différent (écart en centimes et en %), et les mouvements faits hors consigne.

Un rendez-vous n'est attendu que si le pilote était allumé avec cette consigne à partir du jour prévu : une
semaine de pause n'est pas un « manqué ». Le jour prévu (lundi, le 15…) est facultatif ; sans lui, toute la
période compte. Un 31 tombe le dernier jour des mois plus courts. Les jours se comptent en dates civiles du
fuseau : un passage à l'heure d'été ne décale rien.

Tout est pur et déterministe : consignes, périodes d'activité, mouvements, l'heure et le fuseau sont donnés."""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

MAX_SLOTS = 92  # rendez-vous gardés par consigne (les plus récents) : trois mois de « chaque jour »
DONE = ("executed", "simulated")
# Priorité quand plusieurs mouvements tombent sur le même rendez-vous : ce qui a eu lieu d'abord.
PRIORITY = {"executed": 0, "simulated": 1, "reserved": 2, "failed": 3, "unreported": 4, "revoked": 5}
NOTES = {
    "failed": "Échec rapporté par l'agent",
    "unreported": "Autorisé mais jamais rapporté",
    "revoked": "Pilote arrêté avant le compte rendu",
}


@dataclass(frozen=True)
class Window:
    """Le pilote allumé, de ``start`` à ``end`` (exclu), avec les consignes de ce moment-là."""

    start: datetime
    end: datetime
    rule_ids: frozenset[str]
    trial: bool = False


@dataclass(frozen=True)
class Move:
    id: int
    rule_id: str | None
    period: str | None
    status: str
    amount_cents: int
    executed_cents: int | None
    created_at: datetime
    text: str = ""
    refusal: str | None = None


@dataclass(frozen=True)
class Slot:
    period: str
    start: date  # premier jour
    end: date  # lendemain du dernier jour
    due: date  # jour prévu (le dernier jour de la période s'il n'y en a pas)


def period_key(every: str, d: date) -> str:
    """Même écriture que le pilote (2026-10-21, 2026-W43, 2026-10) : un mouvement porte la période de sa demande."""
    if every == "day":
        return d.isoformat()
    if every == "week":
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{d.year}-{d.month:02d}"


def slot_of(every: str, on: int | None, d: date) -> Slot:
    if every == "day":
        return Slot(period_key(every, d), d, d + timedelta(days=1), d)
    if every == "week":
        start = d - timedelta(days=d.weekday())
        due = start + timedelta(days=(on or 7) - 1)
        return Slot(period_key(every, d), start, start + timedelta(days=7), due)
    last = calendar.monthrange(d.year, d.month)[1]
    start = d.replace(day=1)
    end = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return Slot(period_key(every, d), start, end, d.replace(day=min(on or last, last)))


def midnight(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def local_day(moment: datetime, tz: tzinfo) -> date:
    return moment.astimezone(tz).date()


def gap_percent(gap_cents: int, expected_cents: int) -> float:
    pct = (Decimal(gap_cents) * 100 / Decimal(expected_cents)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(pct)


def _slots_between(every: str, on: int | None, first: date, last: date) -> list[Slot]:
    out: list[Slot] = []
    d = first
    while d <= last:
        s = slot_of(every, on, d)
        out.append(s)
        d = s.end
    return out


def track_rule(
    rule: dict[str, Any],
    windows: Sequence[Window],
    moves: Sequence[Move],
    *,
    now: datetime,
    tz: tzinfo,
    since: datetime,
) -> tuple[dict[str, Any], list[Move]]:
    """La consigne et ses rendez-vous, plus les mouvements en trop (un deuxième mouvement sur un même rendez-vous)."""
    rid, every = str(rule["id"]), str(rule["every"])
    on = int(rule["on"]) if rule.get("on") else None
    expected_cents = int(rule["amount_cents"])
    mine = [w for w in windows if rid in w.rule_ids and w.end > since and w.start < now]
    own = [m for m in moves if m.rule_id == rid and m.created_at >= since]
    days: list[date] = []
    if mine:
        days += [local_day(max(min(w.start for w in mine), since), tz)]
        days += [local_day(min(max(w.end for w in mine), now) - timedelta(microseconds=1), tz)]
    days += [local_day(m.created_at, tz) for m in own]
    if not days:
        return _summary(rule, []), []
    slots = _slots_between(every, on, min(days), max(days))[-MAX_SLOTS:]
    by_period: dict[str, list[Move]] = {}
    for m in own:
        by_period.setdefault(m.period or period_key(every, local_day(m.created_at, tz)), []).append(m)
    extras: list[Move] = []
    rows = []
    for s in slots:
        row, extra = _slot(s, on, expected_cents, mine, by_period.get(s.period, []), now=now, tz=tz)
        rows.append(row)
        extras += extra
    return _summary(rule, rows), extras


def _slot(
    s: Slot,
    on: int | None,
    expected_cents: int,
    windows: Sequence[Window],
    moves: Sequence[Move],
    *,
    now: datetime,
    tz: tzinfo,
) -> tuple[dict[str, Any], list[Move]]:
    start_at, end_at = midnight(s.start, tz), midnight(s.end, tz)
    due_from = midnight(s.due, tz) if on else start_at
    active = [w for w in windows if w.start < end_at and w.end > due_from]
    due = s.due
    if active and on:
        # Pilote allumé après le jour prévu : le rendez-vous compte à partir du jour où il l'a été.
        due = max(due, local_day(min(w.start for w in active), tz))
    row: dict[str, Any] = {
        "period": s.period,
        "start": s.start.isoformat(),
        "end": (s.end - timedelta(days=1)).isoformat(),
        "due": due.isoformat(),
        "status": "off",
        "move_id": None,
        "done_on": None,
        "delay_days": 0,
        "amount_cents": None,
        "gap_cents": 0,
        "gap_percent": 0.0,
        "amount_differs": False,
        "trial": False,
        "note": None,
        "refusals": [m.refusal or "Refusé" for m in moves if m.status == "refused"][:3],
    }
    counted = sorted((m for m in moves if m.status in PRIORITY), key=lambda m: (PRIORITY[m.status], m.id))
    best, extras = (counted[0], counted[1:]) if counted else (None, [])
    ended = end_at <= now
    if best is None:
        if active and ended:
            row["status"] = "missed"
        elif active and any(w.start <= now < w.end for w in windows):
            row["status"] = "waiting"
            row["note"] = "En retard sur le jour prévu" if local_day(now, tz) > due else None
        return row, extras
    row["move_id"] = best.id
    if best.status == "reserved":
        row["status"] = "in_progress"
        return row, extras
    if best.status not in DONE:
        row["status"], row["note"] = "missed", NOTES.get(best.status)
        return row, extras
    done_on = local_day(best.created_at, tz)
    amount = best.executed_cents if best.executed_cents is not None else best.amount_cents
    gap = amount - expected_cents
    row.update(
        status="late" if done_on > due else "on_time",
        done_on=done_on.isoformat(),
        delay_days=max(0, (done_on - due).days),
        amount_cents=amount,
        gap_cents=gap,
        gap_percent=gap_percent(gap, expected_cents),
        amount_differs=gap != 0,
        trial=best.status == "simulated",
    )
    return row, extras


def _summary(rule: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    due = [r for r in rows if r["status"] in ("on_time", "late", "missed")]
    done = [r for r in due if r["status"] in ("on_time", "late")]
    expected = int(rule["amount_cents"]) * len(due)
    made = sum(int(r["amount_cents"]) for r in done)
    return {
        "id": rule["id"],
        "label": rule.get("label") or rule["id"],
        "every": rule["every"],
        "on": rule.get("on"),
        "amount_cents": int(rule["amount_cents"]),
        "slots": rows,
        "totals": {
            "due": len(due),
            "on_time": sum(1 for r in due if r["status"] == "on_time"),
            "late": sum(1 for r in due if r["status"] == "late"),
            "missed": sum(1 for r in due if r["status"] == "missed"),
            "different": sum(1 for r in done if r["amount_differs"]),
            "expected_cents": expected,
            "done_cents": made,
            "gap_cents": made - expected,
        },
    }


def track(
    rules: Sequence[dict[str, Any]],
    windows: Sequence[Window],
    moves: Sequence[Move],
    *,
    now: datetime,
    tz: tzinfo,
    since: datetime,
) -> dict[str, Any]:
    """Toutes les consignes, et les mouvements faits hors consigne (sans consigne, consigne inconnue, en trop)."""
    out = []
    known = {str(r["id"]) for r in rules}
    outside: list[tuple[Move, str]] = []
    for rule in rules:
        summary, extras = track_rule(rule, windows, moves, now=now, tz=tz, since=since)
        out.append(summary)
        outside += [(m, "En plus de la consigne sur la même période") for m in extras]
    for m in moves:
        if m.created_at < since or m.status not in PRIORITY:
            continue
        if m.rule_id is None:
            outside.append((m, "Sans consigne (mode libre)"))
        elif m.rule_id not in known:
            outside.append((m, "Consigne qui n'existe plus"))
    outside.sort(key=lambda x: x[0].id, reverse=True)
    return {
        "rules": out,
        "outside": [
            {
                "move_id": m.id,
                "text": m.text,
                "status": m.status,
                "amount_cents": m.executed_cents if m.executed_cents is not None else m.amount_cents,
                "created_at": m.created_at.isoformat(),
                "why": why,
            }
            for m, why in outside[:50]
        ],
    }
