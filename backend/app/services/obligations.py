"""Calendrier des obligations : ce que tu dois déclarer, quand, et la preuve que c'est fait.

Bank propose les obligations qu'il devine d'après tes versements (France Travail → actualisation, RSA ou prime
d'activité → déclaration trimestrielle), suit chaque fenêtre, constate la déclaration par le versement qui suit, te
prévient (carte du jour, messagerie, agent) et prépare les chiffres de la déclaration trimestrielle. Il ne déclare
jamais à ta place."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import obligations as dom
from app.domain.money import format_eur
from app.domain.recurring import add_months
from app.models import Category, Obligation, ObligationDone, Transaction, VaultDocument, VaultField
from app.security import audit
from app.services import analytics
from app.services.settings_store import get_setting, set_setting


class ObligationError(Exception):
    pass


# ------------------------------------------------------------------ lecture
def _payments(db: Session, since: date) -> list[dom.PaymentHint]:
    rows = db.execute(
        select(Transaction.id, Transaction.booking_date, Transaction.amount_cents, Transaction.aid_kind).where(
            Transaction.aid_kind.is_not(None),
            Transaction.booking_date >= since,
            Transaction.amount_cents > 0,
        )
    )
    return [dom.PaymentHint(i, d, a, k) for i, d, a, k in rows if k]


def _insee(db: Session) -> str | None:
    profile = analytics.load_profile(db) or {}
    code = profile.get("postal_insee_code")
    return str(code) if code else None


def _settings_of(row: Obligation) -> dict[str, Any]:
    try:
        data = json.loads(row.settings) if row.settings else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def periods_of(row: Obligation, today: date, *, insee: str | None) -> list[dom.Period]:
    return dom.schedule(
        row.kind,
        today,
        anchor=row.anchor,
        insee_code=insee,
        window_days=row.window_days,
        cadence_months=row.cadence_months,
    )


def _done_map(row: Obligation) -> dict[str, ObligationDone]:
    return {d.period: d for d in row.done}


def _period_view(
    spec: dom.Spec, p: dom.Period, today: date, done: ObligationDone | None, payments: list[Any]
) -> dict[str, Any]:
    proof = dom.proof_for(p, spec, payments)
    is_done = done is not None or proof is not None
    state = dom.state_of(p, today, is_done)
    return {
        "key": p.key,
        "label": p.label,
        "opens": p.opens.isoformat(),
        "deadline": p.deadline.isoformat(),
        "reference_months": list(p.reference_months),
        "state": state,
        "done_on": done.done_on.isoformat() if done else (proof.day.isoformat() if proof else None),
        "how": done.how if done else (f"payment:{proof.id}" if proof else None),
        "proof": None
        if proof is None
        else {"day": proof.day.isoformat(), "amount_cents": proof.amount_cents, "kind": proof.kind},
        "awaiting_payment": dom.awaiting_payment(p, spec, today, done is not None, proof),
        "estimated": p.estimated,
        "note": p.note,
        "declared": json.loads(done.declared) if done is not None and done.declared else None,
    }


def _current(views: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La période qui compte maintenant : en retard, ouverte, bientôt… sinon la prochaine à venir."""
    order = {"late": 0, "soon": 1, "open": 2, "upcoming": 3, "done": 4, "missed": 5}
    live = [v for v in views if v["state"] in ("late", "soon", "open")]
    if live:
        return min(live, key=lambda v: (order[v["state"]], v["deadline"]))
    coming = [v for v in views if v["state"] == "upcoming"]
    if coming:
        return min(coming, key=lambda v: v["opens"])
    done = [v for v in views if v["state"] == "done"]
    return max(done, key=lambda v: v["deadline"]) if done else None


def _row_view(row: Obligation, today: date, *, insee: str | None, payments: list[Any]) -> dict[str, Any]:
    spec = dom.SPECS.get(row.kind, dom.SPECS["custom"])
    done = _done_map(row)
    views = [_period_view(spec, p, today, done.get(p.key), payments) for p in periods_of(row, today, insee=insee)]
    visible = [v for v in views if v["state"] != "missed"]
    needs_anchor = spec.anchor is not None and row.anchor is None
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label or spec.label,
        "organism": spec.organism,
        "organism_name": spec.organism_name,
        "active": row.active,
        "anchor": row.anchor.isoformat() if row.anchor else None,
        "anchor_kind": spec.anchor,
        "anchor_label": spec.anchor_label,
        "needs_anchor": needs_anchor,
        "cadence_months": row.cadence_months,
        "window_days": row.window_days,
        "what": spec.what,
        "consequence": spec.consequence if row.kind != "custom" else (_settings_of(row).get("consequence") or ""),
        "url": spec.url if row.kind != "custom" else (_settings_of(row).get("url") or ""),
        "source": spec.source,
        "automatic": bool(_settings_of(row).get("automatic")),
        "current": _current(visible),
        "periods": visible,
    }


def suggestions(db: Session, today: date) -> list[str]:
    existing = {r.kind for r in db.scalars(select(Obligation))}
    dismissed = [str(k) for k in (get_setting(db, "obligations_dismissed") or [])]
    has_data = bool(db.scalar(select(func.count()).select_from(Transaction)))
    payments = _payments(db, today - timedelta(days=120))
    return dom.suggest(payments, today, existing=existing, dismissed=dismissed, has_data=has_data)


def view(db: Session, today: date) -> dict[str, Any]:
    insee = _insee(db)
    payments = _payments(db, today - timedelta(days=400))
    rows = [
        _row_view(r, today, insee=insee, payments=payments)
        for r in db.scalars(select(Obligation).order_by(Obligation.id))
    ]
    rows.sort(key=lambda r: (not r["active"], _urgency(r), r["id"]))
    return {
        "obligations": rows,
        "suggestions": [dom.spec_to_dict(dom.SPECS[k]) for k in suggestions(db, today)],
        "catalog": [dom.spec_to_dict(s) for s in dom.SPECS.values()],
        "checked_on": dom.CHECKED_ON,
        "disclaimer": "Bank rappelle, prépare les chiffres et constate ; déclarer reste ton geste. Les règles sont "
        "datées et sourcées : vérifie-les si l'organisme te dit autre chose.",
    }


def _urgency(r: dict[str, Any]) -> int:
    cur = r.get("current")
    if cur is None:
        return 9
    if cur.get("awaiting_payment"):
        return 0
    return {"late": 1, "soon": 2, "open": 3, "upcoming": 5, "done": 6}.get(str(cur["state"]), 7)


# ------------------------------------------------------------------ écriture
def activate(
    db: Session,
    kind: str,
    *,
    actor: str,
    anchor: date | None = None,
    label: str | None = None,
    cadence_months: int | None = None,
    window_days: int | None = None,
    settings: dict[str, Any] | None = None,
) -> Obligation:
    if kind not in dom.SPECS:
        raise ObligationError("Obligation inconnue")
    spec = dom.SPECS[kind]
    if kind != "custom":
        existing = db.scalar(select(Obligation).where(Obligation.kind == kind))
        if existing is not None:
            existing.active = True
            if anchor is not None:
                existing.anchor = anchor
            audit.record(db, actor, "obligation.enable", kind=kind)
            return existing
    if kind == "custom" and not (label or "").strip():
        raise ObligationError("Donne un nom à cette obligation")
    if kind == "custom" and anchor is None:
        raise ObligationError("Indique la prochaine échéance")
    row = Obligation(
        kind=kind,
        label=" ".join((label or "").split())[:80] or None,
        active=True,
        anchor=anchor,
        cadence_months=max(1, min(60, cadence_months or spec.cadence_months)),
        window_days=max(1, min(120, window_days or 30)),
        settings=json.dumps(settings or {}) if settings else None,
    )
    db.add(row)
    db.flush()
    dismissed = [k for k in (get_setting(db, "obligations_dismissed") or []) if k != kind]
    set_setting(db, "obligations_dismissed", dismissed)
    audit.record(db, actor, "obligation.add", kind=kind, obligation=row.id)
    return row


def dismiss_suggestion(db: Session, kind: str, *, actor: str) -> None:
    if kind not in dom.SPECS:
        raise ObligationError("Obligation inconnue")
    dismissed = [str(k) for k in (get_setting(db, "obligations_dismissed") or [])]
    if kind not in dismissed:
        dismissed.append(kind)
        set_setting(db, "obligations_dismissed", dismissed)
    audit.record(db, actor, "obligation.dismiss", kind=kind)


def _get(db: Session, obligation_id: int) -> Obligation:
    row = db.get(Obligation, obligation_id)
    if row is None:
        raise ObligationError("Obligation introuvable")
    return row


def update(
    db: Session,
    obligation_id: int,
    *,
    actor: str,
    anchor: date | None = None,
    active: bool | None = None,
    label: str | None = None,
    cadence_months: int | None = None,
    window_days: int | None = None,
    settings: dict[str, Any] | None = None,
) -> Obligation:
    row = _get(db, obligation_id)
    if anchor is not None:
        row.anchor = anchor
    if active is not None:
        row.active = active
    if label is not None:
        row.label = " ".join(label.split())[:80] or None
    if cadence_months is not None:
        row.cadence_months = max(1, min(60, cadence_months))
    if window_days is not None:
        row.window_days = max(1, min(120, window_days))
    if settings is not None:
        row.settings = json.dumps({**_settings_of(row), **settings})
    db.flush()
    audit.record(db, actor, "obligation.update", obligation=row.id, active=row.active)
    return row


def delete(db: Session, obligation_id: int, *, actor: str) -> None:
    row = _get(db, obligation_id)
    db.delete(row)
    audit.record(db, actor, "obligation.delete", obligation=obligation_id, kind=row.kind)


def mark_done(
    db: Session,
    obligation_id: int,
    period: str,
    *,
    actor: str,
    today: date,
    how: str = "user",
    declared: dict[str, Any] | None = None,
) -> ObligationDone:
    row = _get(db, obligation_id)
    known = {p.key: p for p in periods_of(row, today, insee=_insee(db))}
    if period not in known:
        raise ObligationError("Cette période n'est pas au calendrier")
    write_lock(db)
    existing = next((d for d in row.done if d.period == period), None)
    if existing is not None:
        if how == "user":
            existing.how, existing.done_on = "user", today
            if declared is not None:
                existing.declared = json.dumps(declared)
        return existing
    done = ObligationDone(
        obligation_id=row.id,
        period=period,
        done_on=today,
        how=how[:40],
        declared=json.dumps(declared) if declared is not None else None,
    )
    db.add(done)
    # Une obligation à échéance fixe (fin de droits, échéance libre) avance d'un cycle une fois réglée.
    if row.kind == "css_renewal" and row.anchor is not None and row.anchor.strftime("%Y-%m") == period:
        row.anchor = add_months(row.anchor, 12)
    elif row.kind == "custom" and row.anchor is not None and row.anchor == known[period].deadline:
        row.anchor = add_months(row.anchor, max(1, row.cadence_months))
    db.flush()
    audit.record(db, actor, "obligation.done", obligation=row.id, period=period, how=how.split(":")[0])
    return done


def undo_done(db: Session, obligation_id: int, period: str, *, actor: str) -> None:
    row = _get(db, obligation_id)
    done = next((d for d in row.done if d.period == period), None)
    if done is None:
        raise ObligationError("Rien à annuler pour cette période")
    db.delete(done)
    audit.record(db, actor, "obligation.undo", obligation=row.id, period=period)


def auto_detect(db: Session, today: date) -> int:
    """Grave les constats : le versement qui suit la fenêtre vaut déclaration faite (la vue le montre déjà sans rien
    écrire ; ici on l'inscrit pour l'historique)."""
    insee = _insee(db)
    payments = _payments(db, today - timedelta(days=400))
    n = 0
    for row in db.scalars(select(Obligation).where(Obligation.active.is_(True))):
        spec = dom.SPECS.get(row.kind, dom.SPECS["custom"])
        done = _done_map(row)
        for p in periods_of(row, today, insee=insee):
            if p.key in done:
                continue
            proof = dom.proof_for(p, spec, payments)
            if proof is None:
                continue
            db.add(ObligationDone(obligation_id=row.id, period=p.key, done_on=proof.day, how=f"payment:{proof.id}"))
            n += 1
    if n:
        db.flush()
        audit.record(db, "system", "obligation.auto", count=n)
    return n


# ------------------------------------------------------------------ déclaration trimestrielle : les chiffres
def _income_rows(db: Session, months: tuple[str, ...]) -> list[dom.IncomeRow]:
    first = date.fromisoformat(months[0] + "-01")
    last = add_months(date.fromisoformat(months[-1] + "-01"), 1)
    rows = db.execute(
        select(
            Transaction.id,
            Transaction.booking_date,
            Transaction.amount_cents,
            Transaction.merchant,
            Transaction.raw_label,
            Category.slug,
            Transaction.aid_kind,
            Transaction.is_transfer,
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.booking_date >= first,
            Transaction.booking_date < last,
            Transaction.amount_cents > 0,
        )
        .order_by(Transaction.booking_date)
    )
    return [
        dom.IncomeRow(i, d, a, (merchant or raw or "")[:80], slug, aid, bool(tr))
        for i, d, a, merchant, raw, slug, aid, tr in rows
    ]


def _payslips(db: Session, months: tuple[str, ...]) -> list[dom.Payslip]:
    out: list[dom.Payslip] = []
    docs = db.scalars(
        select(VaultDocument).where(
            VaultDocument.kind == "payslip", VaultDocument.status.in_(("to_review", "verified"))
        )
    )
    for doc in docs:
        values: dict[str, Any] = {}
        for f in db.scalars(select(VaultField).where(VaultField.document_id == doc.id)):
            try:
                values[f.key] = json.loads(f.value)
            except ValueError:
                continue
        month = values.get("periode")
        if not isinstance(month, str) or month not in months:
            continue
        net_social = values.get("net_social")
        net_paid = values.get("net_paye")
        out.append(
            dom.Payslip(
                doc.id,
                month,
                int(net_social) if isinstance(net_social, int) and net_social > 0 else None,
                int(net_paid) if isinstance(net_paid, int) and net_paid > 0 else None,
                str(values.get("employeur") or "") or None,
            )
        )
    return out


def prefill(db: Session, obligation_id: int, period: str, today: date) -> dict[str, Any]:
    row = _get(db, obligation_id)
    if row.kind not in ("caf_dtr", "msa_dtr"):
        raise ObligationError("Pas de chiffres à préparer pour cette obligation")
    known = {p.key: p for p in periods_of(row, today, insee=None)}
    p = known.get(period)
    if p is None:
        raise ObligationError("Cette période n'est pas au calendrier")
    months = p.reference_months
    data = dom.dtr_prefill(months, _income_rows(db, months), _payslips(db, months))
    out = dom.prefill_to_dict(data)
    out["period"] = period
    out["summary"] = dom.summary_line(data)
    return out


# ------------------------------------------------------------------ alertes, cartes, agent
def _page(public_url: str) -> str:
    return f"{public_url}/#/aides"


def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    """(clé, texte, urgent) : fenêtre qui s'ouvre, échéance dans trois jours, retard, versement absent."""
    out: list[tuple[str, str, bool]] = []
    for r in view(db, today)["obligations"]:
        if not r["active"]:
            continue
        cur = r.get("current")
        if cur is None:
            continue
        name = r["label"] if privacy == "full" or r["kind"] != "custom" else "Une obligation"
        deadline = dom.fmt(date.fromisoformat(cur["deadline"]), today)
        key = f"obl:{r['id']}:{cur['key']}"
        if cur["awaiting_payment"]:
            out.append(
                (
                    f"{key}:nopay",
                    f"🚨 {name} : aucun versement vu alors qu'il devrait être arrivé. Déclaration faite ? Vérifie "
                    f"vite sur le site de l'organisme. {_page(public_url)}",
                    True,
                )
            )
        elif cur["state"] == "late":
            out.append(
                (
                    f"{key}:late",
                    f"🚨 {name} : la date limite ({deadline}) est passée et je n'ai rien vu. Fais-la maintenant : "
                    f"{r['consequence']} {_page(public_url)}",
                    True,
                )
            )
        elif cur["state"] == "soon":
            out.append(
                (f"{key}:soon", f"⏰ {name} : plus que quelques jours, jusqu'au {deadline}. {_page(public_url)}", False)
            )
        elif cur["state"] == "open" and date.fromisoformat(cur["opens"]) >= today - timedelta(days=2):
            extra = ""
            if r["kind"] in ("caf_dtr", "msa_dtr"):
                extra = " Tes chiffres sont prêts dans Bank."
            out.append(
                (f"{key}:open", f"📝 {name} : c'est ouvert, jusqu'au {deadline}.{extra} {_page(public_url)}", False)
            )
    return out


def agent_view(db: Session, today: date, privacy: str) -> dict[str, Any]:
    data = view(db, today)
    rows = []
    for r in data["obligations"]:
        if not r["active"]:
            continue
        cur = r.get("current")
        item: dict[str, Any] = {
            "id": r["id"],
            "kind": r["kind"],
            "label": r["label"] if privacy == "full" or r["kind"] != "custom" else "Obligation personnelle",
            "organism": r["organism_name"],
            "needs_anchor": r["needs_anchor"],
            "current": None,
        }
        if cur is not None:
            item["current"] = {
                k: cur[k] for k in ("key", "label", "opens", "deadline", "state", "done_on", "awaiting_payment")
            }
            if r["kind"] in ("caf_dtr", "msa_dtr") and cur["state"] in ("open", "soon", "late"):
                try:
                    pre = prefill(db, int(r["id"]), str(cur["key"]), today)
                    item["prefill_totals_cents"] = pre["totals"] if privacy != "aggregates" else None
                    item["prefill_summary"] = pre["summary"]
                except ObligationError:
                    pass
        rows.append(item)
    return {
        "obligations": rows,
        "suggestions": [s["kind"] for s in data["suggestions"]],
        "rule": "Rappelle, explique, donne les chiffres préparés par Bank ; ne déclare jamais à la place de la "
        "personne et n'invente aucune date : celles de Bank sont datées et sourcées.",
    }


def chat_lines(db: Session, today: date, privacy: str, public_url: str) -> list[str]:
    """Pour le point du matin et la commande /declarations."""
    lines: list[str] = []
    for r in view(db, today)["obligations"]:
        cur = r.get("current")
        if not r["active"] or cur is None:
            continue
        name = r["label"] if privacy == "full" or r["kind"] != "custom" else "Obligation"
        d = dom.fmt(date.fromisoformat(cur["deadline"]), today)
        if cur["awaiting_payment"]:
            lines.append(f"🚨 {name} : versement attendu, rien vu — vérifie.")
        elif cur["state"] == "late":
            lines.append(f"🚨 {name} : en retard (limite {d}).")
        elif cur["state"] in ("open", "soon"):
            lines.append(f"📝 {name} : à faire avant le {d}.")
        elif cur["state"] == "upcoming":
            lines.append(f"📅 {name} : s'ouvre le {dom.fmt(date.fromisoformat(cur['opens']), today)}.")
        elif cur["state"] == "done":
            lines.append(f"✅ {name} : fait ({cur['label']}).")
    if lines:
        lines.append(f"Le détail : {_page(public_url)}")
    return lines


def export(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "kind": r.kind,
            "label": r.label,
            "active": r.active,
            "anchor": r.anchor.isoformat() if r.anchor else None,
            "cadence_months": r.cadence_months,
            "done": [
                {"period": d.period, "on": d.done_on.isoformat(), "how": d.how.split(":")[0], "declared": d.declared}
                for d in r.done
            ],
        }
        for r in db.scalars(select(Obligation).order_by(Obligation.id))
    ]


def amounts_text(totals: dict[str, int]) -> str:
    return ", ".join(f"{dom.RESOURCE_KINDS[k]} {format_eur(v)}" for k, v in totals.items() if v)
