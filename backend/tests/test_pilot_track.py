"""Pilote vs consigne : un calendrier de référence rejoué à la main — fins de mois, changements d'heure à Paris,
pilote en pause puis relancé, mouvements refusés par Bank, montants différents, mouvements hors consigne."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.domain import pilot_track as dom
from app.models import AutonomyEpoch, AutonomyMove
from app.services import autonomy, pilot_track
from tests.test_autonomy import BASE, ask, report, token

PARIS = ZoneInfo("Europe/Paris")
SINCE = datetime(2025, 1, 1, tzinfo=UTC)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def move(i: int, rule: str | None, at: datetime, status: str = "executed", **kw: Any) -> dom.Move:
    kw.setdefault("amount_cents", 5_000)
    kw.setdefault("executed_cents", kw["amount_cents"] if status == "executed" else None)
    return dom.Move(id=i, rule_id=rule, period=kw.pop("period", None), status=status, created_at=at, **kw)


def statuses(rule: dict[str, Any]) -> list[tuple[str, str]]:
    return [(s["period"], s["status"]) for s in rule["slots"]]


MONDAY = {"id": "rmon", "label": "Acheter 50 € de BTC chaque lundi", "every": "week", "on": 1, "amount_cents": 5_000}


def test_weekly_on_monday_across_a_pause_and_the_spring_dst_change() -> None:
    windows = [
        dom.Window(utc(2026, 3, 2, 8), utc(2026, 3, 14, 12), frozenset({"rmon"})),  # arrêté un samedi
        dom.Window(utc(2026, 3, 23, 6), utc(2026, 4, 13, 6), frozenset({"rmon"})),  # relancé un lundi matin
    ]
    moves = [
        move(1, "rmon", utc(2026, 3, 2, 9)),  # lundi : à temps
        move(2, "rmon", utc(2026, 3, 11, 9)),  # mercredi : deux jours de retard
        move(3, "rmon", utc(2026, 3, 23, 9), amount_cents=4_800),  # à temps, mais 2 € de moins
        # Dimanche 22 h 30 UTC, le soir du passage à l'heure d'été : il est déjà lundi 0 h 30 à Paris.
        move(4, "rmon", utc(2026, 3, 29, 22, 30)),
        move(5, "rmon", utc(2026, 4, 6, 9), status="refused", refusal="Plafond des 24 h atteint (reste 0,00 €)"),
    ]
    out = dom.track([MONDAY], windows, moves, now=utc(2026, 4, 13, 5), tz=PARIS, since=SINCE)
    (rule,) = out["rules"]
    assert statuses(rule) == [
        ("2026-W10", "on_time"),
        ("2026-W11", "late"),
        ("2026-W12", "off"),  # pilote en pause toute la semaine : pas un « manqué »
        ("2026-W13", "on_time"),
        ("2026-W14", "on_time"),
        ("2026-W15", "missed"),  # le seul mouvement a été refusé par Bank
        ("2026-W16", "waiting"),  # lundi 7 h à Paris : encore dans les temps
    ]
    by = {s["period"]: s for s in rule["slots"]}
    assert by["2026-W11"]["delay_days"] == 2 and by["2026-W11"]["due"] == "2026-03-09"
    assert by["2026-W11"]["done_on"] == "2026-03-11"
    assert by["2026-W13"]["amount_differs"] and by["2026-W13"]["gap_cents"] == -200
    assert by["2026-W13"]["gap_percent"] == -4.0
    assert by["2026-W14"]["done_on"] == "2026-03-30" and by["2026-W14"]["delay_days"] == 0
    assert by["2026-W15"]["refusals"] == ["Plafond des 24 h atteint (reste 0,00 €)"]
    assert by["2026-W16"]["note"] is None
    assert rule["totals"] == {
        "due": 5,
        "on_time": 3,
        "late": 1,
        "missed": 1,
        "different": 1,
        "expected_cents": 25_000,
        "done_cents": 19_800,
        "gap_cents": -5_200,
    }
    assert out["outside"] == []


def test_month_ends_fall_on_the_last_day_and_late_activation_moves_the_due_day() -> None:
    rule = {"id": "r31", "label": "le 31", "every": "month", "on": 31, "amount_cents": 2_000}
    windows = [dom.Window(utc(2025, 12, 31, 23), utc(2026, 5, 1, 0), frozenset({"r31"}))]
    moves = [
        move(1, "r31", utc(2026, 1, 31, 22, 30), amount_cents=2_000),  # 23 h 30 à Paris, le 31 : à temps
        move(2, "r31", utc(2026, 2, 28, 9), amount_cents=2_000),  # février 2026 n'a que 28 jours
        move(3, "r31", utc(2026, 4, 1, 9), amount_cents=2_000),  # en avance sur le 30 avril : à temps
    ]
    out = dom.track([rule], windows, moves, now=utc(2026, 4, 20), tz=PARIS, since=SINCE)
    (r,) = out["rules"]
    assert statuses(r) == [
        ("2026-01", "on_time"),
        ("2026-02", "on_time"),
        ("2026-03", "missed"),
        ("2026-04", "on_time"),
    ]
    assert [s["due"] for s in r["slots"]] == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
    assert dom.slot_of("month", 31, date(2028, 2, 10)).due == date(2028, 2, 29)  # année bissextile
    assert dom.slot_of("month", None, date(2026, 12, 5)).end == date(2027, 1, 1)
    # Pilote allumé un mercredi pour une consigne du lundi : le rendez-vous compte à partir du mercredi.
    late_on = [dom.Window(utc(2026, 10, 21, 10), utc(2026, 11, 1), frozenset({"rmon"}))]
    thursday = [move(1, "rmon", utc(2026, 10, 22, 10))]
    (w,) = dom.track([MONDAY], late_on, thursday, now=utc(2026, 10, 30), tz=PARIS, since=SINCE)["rules"]
    assert w["slots"][0]["due"] == "2026-10-21" and w["slots"][0]["status"] == "late"
    assert w["slots"][0]["delay_days"] == 1


def test_daily_rule_across_the_autumn_dst_night() -> None:
    rule = {"id": "rday", "label": "chaque jour", "every": "day", "amount_cents": 1_000}
    # Du samedi 24 au lundi 27 octobre 2026, minuit à Paris ; le dimanche 25 dure 25 heures.
    windows = [dom.Window(utc(2026, 10, 23, 22), utc(2026, 10, 26, 23), frozenset({"rday"}))]
    moves = [
        move(1, "rday", utc(2026, 10, 24, 22, 30), amount_cents=1_000),  # 0 h 30 heure d'été, le dimanche 25
        move(2, "rday", utc(2026, 10, 25, 23, 30), amount_cents=1_000),  # 0 h 30 heure d'hiver, le lundi 26
    ]
    (r,) = dom.track([rule], windows, moves, now=utc(2026, 10, 27, 10), tz=PARIS, since=SINCE)["rules"]
    assert statuses(r) == [("2026-10-24", "missed"), ("2026-10-25", "on_time"), ("2026-10-26", "on_time")]
    assert r["totals"]["expected_cents"] == 3_000 and r["totals"]["done_cents"] == 2_000


def test_failures_trials_extras_and_moves_outside_any_rule() -> None:
    rule = {"id": "rwk", "label": "chaque semaine", "every": "week", "amount_cents": 2_000}
    windows = [dom.Window(utc(2026, 9, 28, 6), utc(2026, 10, 30), frozenset({"rwk"}), trial=True)]
    moves = [
        move(1, "rwk", utc(2026, 9, 29), status="failed", amount_cents=2_000),
        move(2, "rwk", utc(2026, 10, 6), status="unreported", amount_cents=2_000),
        move(3, "rwk", utc(2026, 10, 13), status="simulated", amount_cents=2_000),
        move(4, "rwk", utc(2026, 10, 14), status="executed", amount_cents=1_500, period="2026-W42"),
        move(5, "rwk", utc(2026, 10, 20), status="reserved", amount_cents=2_000),
        move(6, None, utc(2026, 10, 21), status="executed", amount_cents=900),
        move(7, "rgone", utc(2026, 10, 21), status="executed", amount_cents=900),
        move(8, None, utc(2026, 10, 21), status="refused", amount_cents=900),
    ]
    out = dom.track([rule], windows, moves, now=utc(2026, 10, 21, 12), tz=PARIS, since=SINCE)
    (r,) = out["rules"]
    assert statuses(r) == [
        ("2026-W40", "missed"),
        ("2026-W41", "missed"),
        ("2026-W42", "on_time"),
        ("2026-W43", "in_progress"),
    ]
    by = {s["period"]: s for s in r["slots"]}
    assert by["2026-W40"]["note"] == "Échec rapporté par l'agent"
    assert by["2026-W41"]["note"] == "Autorisé mais jamais rapporté"
    assert by["2026-W42"]["move_id"] == 4 and not by["2026-W42"]["trial"], "l'exécuté passe avant l'essai"
    assert by["2026-W42"]["gap_percent"] == -25.0
    assert [(o["move_id"], o["why"]) for o in out["outside"]] == [
        (7, "Consigne qui n'existe plus"),
        (6, "Sans consigne (mode libre)"),
        (3, "En plus de la consigne sur la même période"),
    ]
    assert dom.gap_percent(1, 3) == 33.3 and dom.gap_percent(-1, 8) == -12.5


def test_nothing_without_windows_or_moves() -> None:
    (r,) = dom.track([MONDAY], [], [], now=utc(2026, 10, 21), tz=PARIS, since=SINCE)["rules"]
    assert r["slots"] == [] and r["totals"]["due"] == 0


# ------------------------------------------------------------------ service et API
RULE = {"kind": "buy", "asset": "BTC", "amount_cents": 2_000, "every": "week", "on": 1}


def test_rule_day_is_validated_labelled_and_keeps_old_ids(db: Session) -> None:
    assert autonomy.rule_label({**RULE}) == "Acheter 20,00\xa0€ de BTC chaque lundi"
    assert autonomy.rule_label({**RULE, "every": "month", "on": 1}).endswith("le 1er de chaque mois")
    plain = {k: v for k, v in RULE.items() if k != "on"}
    assert autonomy.rule_id({**plain, "to_asset": None, "destination": None}) == autonomy.rule_id(
        {**plain, "to_asset": None, "destination": None, "on": None}
    )
    assert autonomy.rule_id({**RULE}) != autonomy.rule_id(plain)
    tok = token(db)
    for bad in ({**RULE, "on": 8}, {**RULE, "every": "day"}, {**RULE, "on": "lundi"}):
        with pytest.raises(autonomy.AutonomyError, match="jour"):
            autonomy.configure(
                db,
                {**BASE, "mode": "rules", "rules": [bad], "enabled": True, "agent_token_id": tok.id},
                actor="micka",
                now=utc(2026, 10, 19, 8),
            )


def test_epochs_follow_enable_renew_and_stop_and_feed_the_view(db: Session) -> None:
    tok = token(db)
    t0 = utc(2026, 10, 5, 8)  # lundi 5 octobre, 10 h à Paris
    cfg = {**BASE, "mode": "rules", "rules": [RULE], "enabled": True, "agent_token_id": tok.id, "days": 30}
    autonomy.configure(db, cfg, actor="micka", now=t0)
    rid = autonomy.rule_id({**RULE, "to_asset": None, "destination": None})
    ok = ask(db, tok, now=t0 + timedelta(hours=1), rule=rid)
    report(db, tok, ok["ticket"], now=t0 + timedelta(hours=1), executed_eur="20")
    autonomy.stop(db, actor="micka", now=t0 + timedelta(days=3))
    autonomy.configure(db, cfg, actor="micka", now=t0 + timedelta(days=14))  # relancé le lundi 19
    epochs = list(db.scalars(select(AutonomyEpoch).order_by(AutonomyEpoch.id)))
    assert len(epochs) == 2 and epochs[0].stopped_at == t0 + timedelta(days=3) and epochs[1].stopped_at is None
    autonomy.configure(db, cfg, actor="micka", now=t0 + timedelta(days=15))  # renouvelé : nouvelle période
    epochs = list(db.scalars(select(AutonomyEpoch).order_by(AutonomyEpoch.id)))
    assert len(epochs) == 3 and epochs[1].stopped_at == t0 + timedelta(days=15)
    out = pilot_track.view(db, now=t0 + timedelta(days=16), tz=PARIS)
    (r,) = out["rules"]
    assert statuses(r) == [("2026-W41", "on_time"), ("2026-W42", "off"), ("2026-W43", "waiting")]
    assert r["slots"][2]["note"] == "En retard sur le jour prévu"
    assert r["label"] == "Acheter 20,00\xa0€ de BTC chaque lundi"


def test_a_pilot_enabled_before_epochs_existed_gets_one_window(db: Session) -> None:
    tok = token(db)
    t0 = utc(2026, 10, 5, 8)
    cfg = {**BASE, "mode": "rules", "rules": [RULE], "enabled": True, "agent_token_id": tok.id, "days": 30}
    autonomy.configure(db, cfg, actor="micka", now=t0)
    for e in db.scalars(select(AutonomyEpoch)):
        db.delete(e)
    db.flush()
    (r,) = pilot_track.view(db, now=t0 + timedelta(days=8), tz=PARIS)["rules"]
    assert statuses(r) == [("2026-W41", "missed"), ("2026-W42", "waiting")]
    assert db.scalar(select(AutonomyMove)) is None


def test_track_api(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    assert auth_client.get("/api/autonomy/track").json()["rules"] == []
    with app.state.sessionmaker() as db:
        tok = token(db)
        cfg = {**BASE, "mode": "rules", "rules": [RULE], "enabled": True, "agent_token_id": tok.id}
        autonomy.configure(db, cfg, actor="micka", now=datetime.now(UTC) - timedelta(days=10))
        db.commit()
    out = auth_client.get("/api/autonomy/track").json()
    (r,) = out["rules"]
    assert r["on"] == 1 and r["totals"]["due"] >= 1 and out["since"]
    auth_client.cookies.clear()
    assert auth_client.get("/api/autonomy/track").status_code == 401
