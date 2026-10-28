"""Radar prudent : une échéance future doit être justifiée et ne prédit pas le cours d'une action."""

from __future__ import annotations

from datetime import date

from app.domain.yearahead import MonthPoint, Spike, YearAhead
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
