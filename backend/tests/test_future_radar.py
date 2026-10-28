"""Radar prudent : une échéance future doit être justifiée et ne prédit pas le cours d'une action."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.domain import watch as dw
from app.domain.yearahead import MonthPoint, Spike, YearAhead
from app.services import crypto_value, future_radar, settings_store, yearahead
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


def test_radar_delivers_only_planned_alerts_without_duplicate_detected_pics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yearahead.analytics, "budget_accounts", lambda db: [object()])
    monkeypatch.setattr(yearahead, "compute", lambda db, today: sample())
    monkeypatch.setattr(settings_store, "get_setting", lambda db, key: 10_000)
    events = future_radar.alerts(
        cast(Session, None), today=date(2026, 10, 28), privacy="aggregates", public_url="https://bank.example"
    )
    assert len(events) == 1, "YearAhead already sends a separate alert for detected recurrent costs"
    assert events[0][0] == "radar:tax:2026-11-28"
    assert "22 000" not in events[0][1]
    assert "https://bank.example/#/veille" in events[0][1]
    assert events[0][2] is False


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
            "BTC": SimpleNamespace(fetched_at=datetime(2026, 10, 27, tzinfo=UTC)),
            "ETH": SimpleNamespace(fetched_at=datetime(2026, 10, 20, tzinfo=UTC)),
        },
    )
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
