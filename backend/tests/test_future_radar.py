"""Radar prudent : une échéance future doit être justifiée et ne prédit pas le cours d'une action."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import db as future_radar_db
from app.domain import watch as dw
from app.domain.yearahead import MonthPoint, Spike, YearAhead
from app.services import crypto_value, future_radar
from app.services.future_radar import build_signals


def sample() -> YearAhead:
    return YearAhead(
        start_month="2026-10",
        start_balance_cents=20_000,
        points=(
            MonthPoint("2026-11", "novembre", 120_000, 75_000, 25_000, 32_000, ("Taxe",), -12_000),
            MonthPoint("2026-12", "décembre", 120_000, 75_000, 25_000, 0, (), 8_000),
        ),
        spikes=(
            Spike("tax", "Taxe foncière", 22_000, date(2026, 11, 28), "custom", "planned"),
            Spike("annual", "Cotisation annuelle", 15_000, date(2026, 11, 14), "yearly", "detected"),
            Spike("estimate", "Impôt estimé", 18_000, date(2026, 11, 20), "yearly", "estimated"),
            Spike("later", "Après la fenêtre", 35_000, date(2026, 12, 24), "custom", "planned"),
        ),
        ignored=(),
        income_monthly_cents=120_000,
        fixed_monthly_cents=75_000,
        variable_monthly_cents=25_000,
        yearly_spikes_cents=37_000,
        provision_monthly_cents=3_100,
        lowest_month="2026-11",
        lowest_cents=-12_000,
        status="risk",
    )


def test_risk_requires_month_projection_and_dated_evidence() -> None:
    results = build_signals(sample(), today=date(2026, 10, 28), buffer_cents=10_000)
    assert [row["label"] for row in results] == ["Cotisation annuelle", "Taxe foncière"]
    assert [row["days"] for row in results] == [17, 31]
    assert all(row["level"] == "risk" for row in results)
    assert results[0]["confidence"] == "estimated"
    assert results[1]["confidence"] == "declared"
    assert all(row["projected_month_end_cents"] == -12_000 for row in results)
    assert "Impôt estimé" not in [row["label"] for row in results]


def test_radar_does_not_invent_problems_when_month_stays_above_buffer() -> None:
    y = sample()
    from dataclasses import replace

    changed = replace(
        y,
        points=(replace(y.points[0], end_balance_cents=48_000), *y.points[1:]),
    )
    rows = build_signals(changed, today=date(2026, 10, 28), buffer_cents=10_000)
    assert rows and all(row["level"] == "watch" for row in rows)
    assert build_signals(y, today=date(2027, 1, 1)) == []


def test_radar_sees_the_rest_of_the_current_month_with_three_levels() -> None:
    """yearahead commence au mois suivant : les jours qui restent viennent de la prévision du mois, une dépense
    saisie y est « déclarée », une sortie prévue « Prévision Bank » ; référence : le point bas du mois."""
    from app.domain.forecast import UpcomingEvent
    from tests.test_decisions import TODAY, runner

    run = runner()
    planned = [UpcomingEvent(date(2026, 10, 28), "Pneus hiver", -40_000, "divers", "fixed", 1.0, late=False)]
    forecast = run(extra=planned)
    rows = build_signals(sample(), today=TODAY, buffer_cents=10_000, forecast=forecast, planned=planned)
    tyres = next(r for r in rows if r["label"] == "Pneus hiver")
    assert tyres["source"] == "planned" and tyres["confidence"] == "declared" and tyres["days"] == 7
    assert tyres["projected_month_end_cents"] == forecast.lowest_balance_cents == 36_651
    assert tyres["level"] == "watch", "le point bas du mois reste au-dessus du matelas"
    assert "NETFLIX" not in [r["label"] for r in rows], "sous 100 €, pas un gros prélèvement"
    assert [r["label"] for r in rows[:2]] == ["Cotisation annuelle", "Taxe foncière"], "les mois sous zéro d'abord"
    # Le même mois avec un petit solde : « tight » sous le matelas, « risk » sous zéro — comme yearahead.
    tight = runner(balance=-20_000)(extra=planned)
    row = next(
        r
        for r in build_signals(sample(), today=TODAY, buffer_cents=10_000, forecast=tight, planned=planned)
        if r["label"] == "Pneus hiver"
    )
    assert row["level"] == ("risk" if tight.lowest_balance_cents < 0 else "tight")
    assert build_signals(sample(), today=TODAY, buffer_cents=10_000, forecast=run(), planned=[]) == build_signals(
        sample(), today=TODAY, buffer_cents=10_000
    ), "sans grosse sortie dans le mois, rien de plus que yearahead"


def test_radar_delivers_only_planned_alerts_without_duplicate_detected_pics(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 10, 28)
    base = build_signals(sample(), today=today, buffer_cents=10_000)
    extra = {
        **base[1],
        "key": "radar:month:2026-10-30:Pneus:40000",
        "label": "Pneus",
        "days": 2,
        "level": "tight",
        "projected_month_end_cents": 5_000,
    }
    monkeypatch.setattr(future_radar, "signals", lambda db, today, y=None: [*base, extra])
    events = future_radar.alerts(
        cast(Session, None), today=today, privacy="aggregates", public_url="https://bank.example"
    )
    assert [e[0] for e in events] == ["radar:tax:2026-11-28", "radar:month:2026-10-30:Pneus:40000"], (
        "YearAhead already sends a separate alert for detected recurrent costs"
    )
    assert "22 000" not in events[0][1] and "sous zéro" in events[0][1] and events[0][2] is True
    assert "Dans 31 jours" in events[0][1] and "https://bank.example/#/veille" in events[0][1]
    assert "Dans 2 jours" in events[1][1] and "matelas" in events[1][1] and events[1][2] is False
    full = future_radar.alerts(cast(Session, None), today=today, privacy="full", public_url="https://bank.example")
    assert "220,00" in full[0][1].replace("\u202f", "").replace("\xa0", "") or "220" in full[0][1]
    # Un signal « watch » (mois au-dessus du matelas) ne dérange personne.
    monkeypatch.setattr(future_radar, "signals", lambda db, today, y=None: [{**base[1], "level": "watch"}])
    assert future_radar.alerts(cast(Session, None), today=today, privacy="full", public_url="https://x") == []


def test_crypto_hypothetical_losses_ignore_unpriced_or_old_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crypto_value,
        "holdings_value",
        lambda db: {"BTC": {"value_cents": 100_000}, "ETH": {"value_cents": 40_000}, "DOGE": {"value_cents": 0}},
    )
    monkeypatch.setattr(
        crypto_value,
        "latest_prices",
        lambda db: {
            "BTC": SimpleNamespace(fetched_at=datetime(2026, 10, 27, 12, tzinfo=UTC)),  # 20 h : récent
            "ETH": SimpleNamespace(
                fetched_at=datetime(2026, 10, 27, 7, tzinfo=UTC)
            ),  # 25 h : périmé, comme la page Crypto
        },
    )
    monkeypatch.setattr(future_radar_db, "utcnow", lambda: datetime(2026, 10, 28, 8, tzinfo=UTC))
    info = future_radar.crypto_stress(cast(Session, None), date(2026, 10, 28))
    assert info["tracked_count"] == 3
    assert info["priced_count"] == 1 and info["stale_count"] == 2
    assert info["valued_cents"] == 100_000
    assert info["shock_10_cents"] == 10_000 and info["shock_25_cents"] == 25_000
    assert "fictifs" in info["note"]


def test_rss_topics_mark_sources_without_predicting_investments() -> None:
    assert "marches" in dw.topics_of("BCE : politique monétaire et taux directeurs")
    assert "marches" in dw.topics_of("Federal Reserve interest rates announcement")
    assert "geopolitique" in dw.topics_of("Nouveaux droits de douane sur certains produits")
    assert "transparence" in dw.topics_of("Common Reporting Standard CRS")
    assert "marches" not in dw.topics_of("Un concours de pâtisserie à Lyon")


def test_authenticated_radar_endpoint_keeps_financial_and_public_context_separate(world: TestClient) -> None:
    r = world.get("/api/watch/radar")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["horizon_days"] == 45
    assert isinstance(data["signals"], list)
    assert isinstance(data["macro_context"], list)
    assert isinstance(data["crypto_stress"], dict)
    assert "ne prouve" in data["disclaimer"]
