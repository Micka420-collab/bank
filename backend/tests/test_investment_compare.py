"""Investment comparator: arithmetic, non-fabrication, safety, API and fiscal boundaries."""

from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.domain import investment_compare as dom
from app.services import investment_compare as svc


def test_real_value_compounds_fees_once_yearly_and_tax_only_positive_gain() -> None:
    result = dom.project(
        100_000, 12, gross_pct=6, fees_pct=1, tax_pct=30, inflation_pct=3.1, loss_pct=25
    )
    assert result["future_nominal_cents"] == 103_458
    assert result["future_real_cents"] == 100_347
    assert result["hypothetical_shock_loss_cents"] == 25_000
    losing = dom.project(
        100_000, 12, gross_pct=-20, fees_pct=1, tax_pct=30, inflation_pct=3
    )
    assert losing["future_nominal_cents"] == 79_200
    assert losing["net_gain_cents"] == -20_800
    assert dom.project(
        100_000, 60, gross_pct=0, fees_pct=0, tax_pct=0, inflation_pct=3
    )["future_real_cents"] < 100_000


@pytest.mark.parametrize(
    "override",
    [
        {"gross_pct": math.nan},
        {"inflation_pct": math.inf},
        {"capital_cents": -1},
        {"horizon_months": 0},
        {"fees_pct": 40},
        {"tax_pct": -1},
        {"loss_pct": 150},
    ],
)
def test_invalid_scenarios_are_rejected(override: dict[str, float | int]) -> None:
    args = dict(capital_cents=100_000, horizon_months=12, gross_pct=3, fees_pct=0, tax_pct=0, inflation_pct=2)
    args.update(override)
    with pytest.raises(ValueError):
        dom.project(**args)


def test_suitability_is_separate_from_paper_profit_and_checks_restrictions() -> None:
    wrong = dom.assess(
        reserve_gap_cents=200_000, overdraft_risk=True, risk_label="dynamique",
        horizon_months=12, locked_months=18, loss_pct=25,
        investment_kind="market", eligible=True, full_room=True,
    )
    assert not wrong["eligible_to_consider"]
    assert len(wrong["blockers"]) >= 4
    savings = dom.assess(
        reserve_gap_cents=200_000, overdraft_risk=False, risk_label="prudent",
        horizon_months=12, locked_months=0, loss_pct=0,
        investment_kind="livret_a", eligible=True, full_room=True,
    )
    assert savings["eligible_to_consider"]
    lep = dom.assess(
        reserve_gap_cents=None, overdraft_risk=None, risk_label="inconnu",
        horizon_months=24, locked_months=0, loss_pct=0,
        investment_kind="lep", eligible=None, full_room=False,
    )
    assert not lep["eligible_to_consider"]
    assert any("éligibilité" in item.lower() for item in lep["cautions"])


def test_personal_reserve_red_lines_and_cap_block_unsupported_investments(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import advisor, analytics

    monkeypatch.setattr(
        advisor, "plain_answers",
        lambda db: {"emergency_target_months": 3, "red_lines": [{"rule": "Pas d'actions ni d'ETF"}]},
    )
    monkeypatch.setattr(
        analytics, "account_balances",
        lambda db, today: [SimpleNamespace(kind="livret_a", balance_cents=10_000)],
    )
    monkeypatch.setattr(analytics, "monthly_essentials", lambda db, today: 100_000)
    monkeypatch.setattr(analytics, "budget_accounts", lambda db: [])
    result = svc.view(
        cast(Session, None), date(2026, 10, 28), capital_cents=100_000,
        horizon_months=120, inflation_pct=3.1, custom_gross_pct=12,
    )
    assert result["context"]["reserve_gap_cents"] == 290_000
    market = next(row for row in result["scenarios"] if row["kind"] == "market")
    assert not market["suitability"]["eligible_to_consider"]
    assert any("Ligne rouge" in msg for msg in market["suitability"]["blockers"])
    lep = next(row for row in result["scenarios"] if row["kind"] == "lep")
    assert lep["rates_as_of"]
    assert lep["source"] != "Saisie manuelle"
    assert len(result["scenarios"]) == 4


def test_authenticated_route_rate_sourcing_and_invalid_payload(world: TestClient) -> None:
    response = world.post(
        "/api/watch/investments/compare",
        json={"capital_cents": 100_000, "horizon_months": 12, "inflation_pct": 3.1},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["scenarios"]) == 3
    assert all(item["status"] == "taux_dated" for item in payload["scenarios"])
    assert all(item["rates_as_of"] for item in payload["scenarios"])
    assert payload["context"]["data_complete"] is False
    custom = world.post(
        "/api/watch/investments/compare",
        json={
            "capital_cents": 100_000, "horizon_months": 12, "inflation_pct": 3.1,
            "custom_gross_pct": 20, "custom_fees_pct": 0.5,
            "custom_tax_pct": 30, "custom_loss_pct": 50, "custom_locked_months": 24,
        },
    )
    assert custom.status_code == 200, custom.text
    assert len(custom.json()["scenarios"]) == 4
    market = custom.json()["scenarios"][-1]
    assert market["status"] == "hypothese"
    assert not market["suitability"]["eligible_to_consider"]
    assert any("bloqué" in item for item in market["suitability"]["blockers"])
    for invalid in (
        {"capital_cents": 0, "horizon_months": 12, "inflation_pct": 3.1},
        {"capital_cents": 100_000, "horizon_months": 0, "inflation_pct": 3.1},
        {"capital_cents": 100_000, "horizon_months": 12, "inflation_pct": 3.1, "custom_fees_pct": 200},
    ):
        assert world.post("/api/watch/investments/compare", json=invalid).status_code == 422
