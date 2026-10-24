"""Budget du mois, flux récurrents, aides de l'État (suivi + simulation), patrimoine et objectifs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, TodayDep
from app.db import utcnow
from app.domain.aids import compute_gaps
from app.domain.wealth import (
    emergency_fund,
    load_regulated_products,
    net_worth,
    project_savings,
    regulated_status,
)
from app.fiscal.client import FiscalClient, FiscalEngineError
from app.fiscal.situation import HouseholdProfileIn, build_situation
from app.models import Account, FiscalSimulation, HouseholdProfile, SavingsGoal
from app.security import audit
from app.services import analytics, documents, history
from app.services.settings_store import get_setting, set_setting

router = APIRouter(prefix="/api", tags=["insights"])


# ------------------------------------------------------------------ budget
class BudgetSettingsIn(BaseModel):
    buffer_cents: int = Field(ge=0, le=10_000_000)
    savings_goal_cents: int = Field(ge=0, le=10_000_000)


@router.get("/history")
def history_view(
    _: AuthDep,
    db: DbDep,
    today: TodayDep,
    range_: str = Query(default="30d", alias="range", pattern="^(7d|30d|3m|1y|all)$"),
) -> dict[str, Any]:
    """Soldes jour par jour, revenus / dépenses par période, répartition, comparaison et dernières opérations."""
    return history.history(db, today, range_)


@router.get("/budget")
def budget(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    forecast, suggestions = analytics.month_forecast(db, today)
    return {
        "forecast": asdict(forecast),
        "suggested_budgets": suggestions,
        "settings": {
            "buffer_cents": get_setting(db, "buffer_cents"),
            "savings_goal_cents": get_setting(db, "savings_goal_cents"),
        },
    }


@router.put("/budget/settings")
def update_budget_settings(body: BudgetSettingsIn, ctx: AuthDep, db: DbDep) -> dict[str, int]:
    set_setting(db, "buffer_cents", body.buffer_cents)
    set_setting(db, "savings_goal_cents", body.savings_goal_cents)
    audit.record(db, ctx.user.username, "budget.settings")
    return body.model_dump()


@router.get("/recurring")
def recurring(_: AuthDep, db: DbDep, today: TodayDep) -> list[dict[str, Any]]:
    return [asdict(s) for s in analytics.recurring_for_budget(db, today)]


# ------------------------------------------------------------------ aides
def _last_simulation(db: DbDep) -> dict[str, Any] | None:
    row = db.scalar(select(FiscalSimulation).order_by(FiscalSimulation.id.desc()).limit(1))
    if row is None:
        return None
    data: dict[str, Any] = json.loads(row.result)
    data["created_at"] = row.created_at.isoformat()
    return data


@router.get("/aids")
def aids(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    summary = analytics.aids_summary(db, today)
    sim = _last_simulation(db)
    gaps = []
    if sim:
        estimates = {e["variable"]: e["monthly_cents"] for e in sim.get("estimates", [])}
        for var in ("rsa", "ppa", "aide_logement", "af", "asf"):
            estimates.setdefault(var, 0)
        gaps = [asdict(g) for g in compute_gaps(estimates, summary.streams)]
    return {
        "summary": asdict(summary),
        "simulation": sim,
        "gaps": gaps,
        "disclaimer": "Estimations indicatives calculées par OpenFisca (moteur open source de l'État). "
        "Seul l'organisme payeur peut confirmer tes droits.",
    }


@router.get("/profile")
def get_profile(_: AuthDep, db: DbDep) -> dict[str, Any] | None:
    return analytics.load_profile(db)


@router.put("/profile")
def put_profile(body: HouseholdProfileIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    payload = body.model_dump(mode="json")
    row = db.get(HouseholdProfile, 1)
    if row is None:
        db.add(HouseholdProfile(id=1, data=json.dumps(payload)))
    else:
        row.data, row.updated_at = json.dumps(payload), utcnow()
    audit.record(db, ctx.user.username, "profile.update")
    return payload


@router.post("/aids/simulate")
def simulate(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    raw = analytics.load_profile(db)
    if raw is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Renseigne d'abord ton profil de foyer")
    profile = documents.with_taxable_income(db, HouseholdProfileIn.model_validate(raw), today)
    incomes = analytics.monthly_income_series(db, today)
    situation = build_situation(
        profile,
        period=today,
        salary_net_by_month=incomes["salary"],
        unemployment_by_month=incomes["unemployment"],
        pension_by_month=incomes["pension"],
    )
    client: FiscalClient = request.app.state.fiscal_client
    try:
        outcome = client.simulate(situation, today)
    except FiscalEngineError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    result = asdict(outcome)
    result["inputs"] = {
        "months_of_salary_history": len(incomes["salary"]),
        "used_declared_taxable_income": profile.annual_taxable_income_n2_cents is not None,
    }
    db.add(FiscalSimulation(period=outcome.period, result=json.dumps(result)))
    audit.record(db, ctx.user.username, "aids.simulate", period=outcome.period, engine=outcome.engine_version)
    return result


# ------------------------------------------------------------------ patrimoine
class ProjectionIn(BaseModel):
    initial_cents: int = Field(ge=0, le=10_000_000_000)
    monthly_cents: int = Field(ge=0, le=100_000_000)
    rate_percent: Decimal = Field(ge=Decimal("-20"), le=Decimal("30"))
    years: int = Field(ge=1, le=60)
    inflation_percent: Decimal = Field(default=Decimal("2.0"), ge=Decimal("-5"), le=Decimal("20"))


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_cents: int = Field(gt=0, le=10_000_000_000)
    target_date: date | None = None
    account_id: int | None = None
    monthly_contribution_cents: int = Field(default=0, ge=0, le=100_000_000)


@router.get("/wealth")
def wealth(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    balances = analytics.account_balances(db, today)
    nw = net_worth(balances)
    analytics.snapshot_wealth(db, today, nw.assets_cents, nw.liabilities_cents)
    products = load_regulated_products()
    goals = analytics.savings_goals_status(db, today, products)
    return {
        "net_worth": asdict(nw),
        "accounts": [asdict(b) for b in balances],
        "history": [{"day": d, "net_cents": v} for d, v in history.net_worth_history(db, today)],
        "emergency_fund": asdict(emergency_fund(balances, analytics.monthly_essentials(db, today))),
        "regulated": [asdict(s) for s in regulated_status(balances, products)],
        "rates_as_of": products[0].as_of if products else None,
        "rates_source": products[0].source if products else None,
        "goals": goals,
        "disclaimer": "Calculs et repères informatifs, pas un conseil en investissement.",
    }


@router.post("/wealth/projection")
def projection(body: ProjectionIn, _: AuthDep) -> list[dict[str, Any]]:
    return [
        asdict(p)
        for p in project_savings(
            body.initial_cents, body.monthly_cents, body.rate_percent, body.years, body.inflation_percent
        )
    ]


@router.post("/goals", status_code=201)
def create_goal(body: GoalIn, ctx: AuthDep, db: DbDep) -> dict[str, int]:
    if body.account_id is not None and db.get(Account, body.account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Compte inconnu")
    goal = SavingsGoal(**body.model_dump())
    db.add(goal)
    db.flush()
    audit.record(db, ctx.user.username, "goal.create", goal=goal.id)
    return {"id": goal.id}


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, ctx: AuthDep, db: DbDep) -> None:
    goal = db.get(SavingsGoal, goal_id)
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Objectif introuvable")
    db.delete(goal)
    audit.record(db, ctx.user.username, "goal.delete", goal=goal_id)
