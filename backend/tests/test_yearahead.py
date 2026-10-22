"""L'année qui vient : les pics trimestriels et annuels posés à leur mois, douze mois projetés depuis la fin du mois
en cours, le point bas, la provision mensuelle qui lisse — et la carte qui propose de la réserver."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from app.api import deps
from app.domain import yearahead as dom
from app.domain.forecast import CategoryInfo
from app.domain.recurring import RecurringSeries, Txn, add_months
from app.services import channel_alerts
from app.services import yearahead as svc
from app.services.settings_store import get_setting, set_setting
from tests.scenario import TODAY, seed_household
from tests.test_mcp import make_agent

CATS = {
    "salaire": CategoryInfo("salaire", "Salaire", "income"),
    "logement": CategoryInfo("logement", "Logement", "fixed"),
    "assurances": CategoryInfo("assurances", "Assurances", "fixed"),
    "energie": CategoryInfo("energie", "Énergie & eau", "fixed"),
    "epargne": CategoryInfo("epargne", "Épargne", "savings"),
    "courses": CategoryInfo("courses", "Courses", "variable"),
}


def series(
    merchant: str, cents: int, cadence: str, next_date: date, *, category: str, occurrences: int = 3
) -> RecurringSeries:
    days = {"monthly": 30.44, "quarterly": 91.31, "yearly": 365.25, "weekly": 7.0}[cadence]
    return RecurringSeries(
        key=f"{merchant}:{'in' if cents > 0 else 'out'}", merchant=merchant, direction="in" if cents > 0 else "out",
        cadence=cadence, period_days=days, amount_cents=cents, amount_min_cents=cents, amount_max_cents=cents,
        variable=False, occurrences=occurrences, last_date=add_months(next_date, -12 if cadence == "yearly" else -1),
        next_date=next_date, day_of_month=next_date.day, confidence=0.9, category=category, late=False,
    )  # fmt: skip


def variable_txns(today: date, per_day: int = 2_000, days: int = 90) -> list[Txn]:
    return [Txn(i, today - timedelta(days=i + 1), -per_day, "CARREFOUR", "courses") for i in range(days)]


def test_twelve_months_from_the_end_of_this_month_with_spikes_at_their_month() -> None:
    today = date(2026, 10, 22)
    s = [
        series("BRGM", 215_000, "monthly", date(2026, 10, 28), category="salaire"),
        series("FONCIA", -65_000, "monthly", date(2026, 11, 3), category="logement"),
        series("MAIF AUTO", -54_000, "yearly", date(2027, 1, 15), category="assurances", occurrences=2),
        series("VEOLIA", -9_000, "quarterly", date(2026, 12, 10), category="energie"),
        series("LIVRET", -30_000, "monthly", date(2026, 11, 1), category="epargne"),
        series(
            "PETIT", -2_000, "yearly", date(2027, 3, 1), category="assurances", occurrences=2
        ),  # trop petit pour un pic
    ]
    y = dom.build_year_ahead(
        today=today,
        start_balance_cents=100_000,
        series=s,
        categories=CATS,
        txns=variable_txns(today),
        buffer_cents=10_000,
    )
    assert y.start_month == "2026-11" and len(y.points) == 12 and y.points[-1].month == "2027-10"
    assert y.income_monthly_cents == 215_000 and y.fixed_monthly_cents == 95_000, "loyer + épargne, pas les pics"
    assert y.variable_monthly_cents == round(2_000 * 30.44)
    spikes = {(s.label, s.day.isoformat()) for s in y.spikes}
    assert (
        ("Maif Auto", "2027-01-15") in spikes
        and ("Veolia", "2026-12-10") in spikes
        and ("Veolia", "2027-09-10") in spikes
    )
    assert not any(s.label == "Petit" for s in y.spikes)
    assert y.yearly_spikes_cents == 54_000 + 4 * 9_000 and y.provision_monthly_cents == 7_500
    jan = next(p for p in y.points if p.month == "2027-01")
    assert jan.spikes_cents == 54_000 and jan.spikes == ("Maif Auto 540 €",)
    nov = y.points[0]
    assert nov.end_balance_cents == 100_000 + 215_000 - 95_000 - y.variable_monthly_cents
    assert y.status == "ok" and y.lowest_cents == min(p.end_balance_cents for p in y.points)


def test_weekly_groceries_stay_in_the_daily_rhythm() -> None:
    """Les courses du samedi sont une série récurrente, mais de catégorie variable : elles restent dans le rythme
    quotidien (comme dans la prévision du mois) au lieu de disparaître de la projection."""
    today = date(2026, 10, 22)
    s = [
        series("BRGM", 215_000, "monthly", date(2026, 10, 28), category="salaire"),
        series("FONCIA", -65_000, "monthly", date(2026, 11, 3), category="logement"),
        series("CARREFOUR", -14_000, "weekly", date(2026, 10, 24), category="courses", occurrences=12),
    ]
    y = dom.build_year_ahead(
        today=today, start_balance_cents=100_000, series=s, categories=CATS, txns=variable_txns(today), buffer_cents=0
    )
    assert y.fixed_monthly_cents == 65_000, "le loyer seulement : les courses ne sont pas une charge fixe"
    assert y.variable_monthly_cents == round(2_000 * 30.44), "et elles ne sortent pas non plus du rythme quotidien"


def test_custom_planned_and_ignored_spikes() -> None:
    today = date(2026, 10, 22)
    s = [series("BRGM", 120_000, "monthly", date(2026, 10, 28), category="salaire"), series("FONCIA", -65_000, "monthly", date(2026, 11, 3), category="logement"),
         series("MAIF AUTO", -54_000, "yearly", date(2027, 1, 15), category="assurances", occurrences=2)]  # fmt: skip
    custom = [
        dom.CustomSpike("custom:abc", "Taxe foncière", 90_000, date(2025, 10, 15), 12)
    ]  # date passée : avancée d'un an
    planned = [(date(2027, 6, 1), "Vacances", 120_000)]
    y = dom.build_year_ahead(today=today, start_balance_cents=50_000, series=s, categories=CATS, txns=variable_txns(today, 1_500),
                             custom=custom, planned=planned, ignored=["MAIF AUTO:out"], buffer_cents=10_000)  # fmt: skip
    labels = [(sp.label, sp.day.isoformat(), sp.source) for sp in y.spikes]
    assert ("Taxe foncière", "2027-10-15", "custom") in labels and ("Vacances", "2027-06-01", "planned") in labels
    assert [sp.label for sp in y.ignored] == ["Maif Auto"] and not any(sp.label == "Maif Auto" for sp in y.spikes)
    assert y.provision_monthly_cents == -(-(90_000 + 120_000) // 12)
    assert y.status == "risk" and y.lowest_cents < 0 and y.lowest_month in {p.month for p in y.points}
    assert dom.to_dict(y)["lowest_label"].endswith("27")


def test_without_any_income_series_the_projection_says_so() -> None:
    today = date(2026, 10, 22)
    y = dom.build_year_ahead(
        today=today, start_balance_cents=5_000, series=[], categories=CATS, txns=[], buffer_cents=0
    )
    assert "Aucun revenu régulier" in y.notes[0] and "dépenses variables" in y.notes[1]
    assert y.provision_monthly_cents == 0 and y.spikes == () and y.status == "ok"


# ------------------------------------------------------------------ service, API, carte, agent
@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def test_year_view_custom_spikes_provision_card_and_agent(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    data = world.get("/api/forecast/year").json()
    assert len(data["points"]) == 12 and data["start_month"] == "2026-11" and data["income_monthly_cents"] >= 215_000
    assert data["provision_monthly_cents"] == 0 and data["spikes"] == [], "le foyer de test n'a que des mensuels"
    r = world.post(
        "/api/forecast/year/spikes", json={"label": "Assurance auto", "amount_cents": 60_000, "next_date": "2026-12-15"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["spikes"][0]["label"] == "Assurance auto" and data["provision_monthly_cents"] == 5_000
    key = data["custom"][0]["key"]
    dec = next(p for p in data["points"] if p["month"] == "2026-12")
    assert dec["spikes_cents"] == 60_000
    # La carte du jour propose de réserver la provision ; « oui » règle l'épargne visée.
    cards = {c["key"]: c for c in world.get("/api/initiatives?limit=20").json()["cards"]}
    card = next(c for k, c in cards.items() if k.startswith("yearahead:provision:"))
    assert "50 €" in card["title"] and card["actions"][0]["effect"] == {
        "type": "setting",
        "key": "savings_goal_cents",
        "value": 5_000,
    }
    r = world.post(f"/api/initiatives/{card['key']}/act", json={"action": "yes"})
    assert r.status_code == 200
    with app.state.sessionmaker() as s:
        assert int(get_setting(s, "savings_goal_cents")) == 5_000
    assert not any(
        k.startswith("yearahead:provision:")
        for k in {c["key"] for c in world.get("/api/initiatives?limit=20").json()["cards"]}
    )
    # Alerte deux mois avant le pic, et l'agent lit la projection (pics anonymes en mode agrégats).
    with app.state.sessionmaker() as s:
        notices = svc.alerts(s, today=TODAY, privacy="full", public_url="https://b.example")
        assert any(k.startswith(f"spike:{key}:2026-12-15") and "Assurance auto" in t for k, t, _ in notices)
        redacted = svc.alerts(s, today=TODAY, privacy="redacted", public_url="https://b.example")
        assert all("Assurance auto" not in t for _, t, _ in redacted)
        from datetime import UTC, datetime

        all_notices = channel_alerts.collect(
            s, today=TODAY, local=datetime(2026, 10, 21, 12, tzinfo=UTC), privacy="full", public_url="https://b.example"
        )
        assert any(n.key.startswith("spike:") for n in all_notices)
    agent = make_agent(app, world)
    out = agent.ok("get_year_ahead")
    assert (
        out["provision_monthly_cents"] == 5_000
        and out["spikes"][0]["label"] == "Assurance auto"
        and "ignored" not in out
    )
    discreet = make_agent(app, world, preset="observateur", privacy="aggregates", name="discret")
    assert discreet.ok("get_year_ahead")["spikes"][0]["label"] == "Pic"
    # Retirer la dépense ajoutée ; écarter puis reprendre un pic.
    assert (
        world.post("/api/forecast/year/ignore", json={"key": key, "ignored": True}).json()["ignored"][0]["key"] == key
    )
    assert world.post("/api/forecast/year/ignore", json={"key": key, "ignored": False}).json()["ignored"] == []
    assert world.delete(f"/api/forecast/year/spikes/{key}").json()["custom"] == []
    assert world.delete(f"/api/forecast/year/spikes/{key}").status_code == 404
    bad = world.post(
        "/api/forecast/year/spikes", json={"label": " ", "amount_cents": 60_000, "next_date": "2026-12-15"}
    )
    assert bad.status_code == 422
    with app.state.sessionmaker() as s:
        set_setting(
            s, "year_spikes", {"custom": [{"key": "x", "label": "cassé"}], "ignored": []}
        )  # entrée abîmée : ignorée
        s.commit()
    assert world.get("/api/forecast/year").json()["custom"] == []
