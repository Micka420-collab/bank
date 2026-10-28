"""Real purchasing-power calculations: no missing salary months, no false causal inflation claim."""

from __future__ import annotations

import json
from datetime import date
from typing import cast

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.domain import purchasing_power as p
from app.domain.forecast import CategoryInfo
from app.domain.recurring import Txn
from app.services import assistant
from app.services import purchasing_power as svc

TODAY = date(2026, 10, 28)
CATS = {
    "salaire": CategoryInfo("salaire", "Salaire", "income"),
    "courses": CategoryInfo("courses", "Courses", "variable"),
    "epargne": CategoryInfo("epargne", "Épargne", "savings"),
}


def sample(*, missing: str | None = None) -> list[Txn]:
    items: list[Txn] = []
    index = 0
    for year, salary, costs in [(2025, 200_000, 100_000), (2026, 205_000, 112_000)]:
        for month in (7, 8, 9):
            period = f"{year}-{month:02d}"
            if period != missing:
                index += 1
                items.append(Txn(index, date(year, month, 25), salary, "EMPLOYEUR", "salaire"))
            index += 1
            items.append(Txn(index, date(year, month, 5), -costs, "COURSES", "courses"))
            index += 1
            items.append(Txn(index, date(year, month, 6), -50_000, "VIREMENT", "epargne", is_transfer=False))
            index += 1
            items.append(Txn(index, date(year, month, 8), -80_000, "INTERNE", "courses", is_transfer=True))
    return items


def test_salary_up_but_real_purchasing_power_down() -> None:
    result = p.compare(sample(), CATS, TODAY, inflation_pct=3.1)
    assert result["ready"]
    assert result["salary_months_found"] == 6
    assert result["current_salary_avg_cents"] == 205_000
    assert result["previous_salary_avg_cents"] == 200_000
    assert result["nominal_salary_change_pct"] == 2.5
    assert result["real_salary_change_pct"] == p.real_change_pct(2.5, 3.1)
    assert result["real_salary_change_pct"] < 0
    assert result["salary_to_keep_up_cents"] == 206_200
    assert result["monthly_real_gap_cents"] == -1_200
    assert result["current_spending_avg_cents"] == 112_000
    assert result["previous_spending_avg_cents"] == 100_000
    assert result["spending_change_pct"] == 12
    assert result["spending_share_change_points"] > 0
    assert result["spending_drivers"][0]["category"] == "courses"
    assert result["spending_drivers"][0]["delta_cents"] == 12_000


def test_absent_salary_is_unknown_not_a_salary_collapse() -> None:
    result = p.compare(sample(missing="2025-09"), CATS, TODAY, inflation_pct=3.1)
    assert not result["ready"]
    assert result["salary_months_found"] == 5
    for key in ("real_salary_change_pct", "current_salary_avg_cents", "monthly_real_gap_cents"):
        assert result[key] is None


def test_price_adjustment_and_investment_real_net_return_arent_simple_subtractions() -> None:
    assert p.real_change_pct(2.5, 3.1) == -0.58
    result = p.real_yield_pct(gross_pct=6, fees_pct=1, tax_pct=30, inflation_pct=3.1)
    assert result["nominal_after_fees_and_tax_pct"] == 3.46
    assert result["real_annual_pct"] == 0.35
    assert p.real_yield_pct(-10, 0.5, 30, 3.1)["real_annual_pct"] < -10
    with pytest.raises(ValueError):
        p.real_yield_pct(500, 0, 0, 3)


def test_salary_alert_only_on_supported_real_loss_and_privacy(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "benchmark": {**p.INSEE_2026_09, "stale": False},
        "comparison": {"ready": True, "real_salary_change_pct": -3.0, "monthly_real_gap_cents": -12_000},
    }
    monkeypatch.setattr(svc, "view", lambda db, today: payload)
    messages = svc.alerts(cast(Session, None), today=TODAY, privacy="aggregates", public_url="https://bank.example")
    assert len(messages) == 1
    assert messages[0][0] == "pouvoirachat:2026-09:2026-10"
    assert "-120" not in messages[0][1]
    assert "https://bank.example/#/veille" in messages[0][1]
    full = svc.alerts(cast(Session, None), today=TODAY, privacy="full", public_url="https://bank.example")
    assert "120" in full[0][1]
    payload["benchmark"]["stale"] = True
    assert svc.alerts(cast(Session, None), today=TODAY, privacy="full", public_url="https://bank.example") == []


def test_authenticated_purchasing_power_and_real_yield_routes(world: TestClient) -> None:
    r = world.get("/api/watch/purchasing-power")
    assert r.status_code == 200, r.text
    assert r.json()["benchmark"]["as_of"] == "2026-09"
    assert isinstance(r.json()["benchmark"]["stale"], bool)
    good = world.post(
        "/api/watch/purchasing-power/yield",
        json={
            "gross_pct": 6,
            "fees_pct": 1,
            "tax_pct": 30,
            "inflation_pct": 3.1,
        },
    )
    assert good.status_code == 200, good.text
    assert good.json()["real_annual_pct"] == 0.35
    assert (
        world.post(
            "/api/watch/purchasing-power/yield",
            json={
                "gross_pct": 300,
                "fees_pct": 0,
                "tax_pct": 30,
                "inflation_pct": 3.1,
            },
        ).status_code
        == 422
    )



def test_assistant_can_ground_salary_and_yield_in_bank_not_guesswork(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc,
        "view",
        lambda db, today: {
            "available": True,
            "benchmark": p.INSEE_2026_09,
            "comparison": {"ready": False, "real_salary_change_pct": None, "salary_months_found": 1},
        },
    )
    ctx = assistant._ToolContext(cast(Session, None), "test", TODAY, 1, False)
    salary = json.loads(assistant._execute_tool(ctx, "analyze_purchasing_power", '{"mode":"salary"}'))
    assert salary["comparison"]["real_salary_change_pct"] is None
    assert salary["comparison"]["salary_months_found"] == 1
    result = json.loads(
        assistant._execute_tool(
            ctx,
            "analyze_purchasing_power",
            '{"mode":"yield","gross_pct":6,"fees_pct":1,"tax_pct":30,"inflation_pct":3.1}',
        )
    )
    assert result["nominal_after_fees_and_tax_pct"] == 3.46
    assert result["real_annual_pct"] == 0.35
    for invalid in (
        '{"mode":"yield","gross_pct":1000}',
        '{"mode":"yield","gross_pct":"nan","fees_pct":0,"tax_pct":0,"inflation_pct":3}',
        '{"mode":"missing"}',
    ):
        assert "error" in json.loads(assistant._execute_tool(ctx, "analyze_purchasing_power", invalid))
    assert assistant.TOOL_PURCHASING_POWER["function"]["parameters"]["additionalProperties"] is False
