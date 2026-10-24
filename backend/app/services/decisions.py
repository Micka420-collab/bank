"""Décisions chiffrées à partir de TA base : « je peux l'acheter ? » et « où placer mon argent ? »."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.decisions import PurchasePlan, plan_purchase
from app.domain.invest import (
    DebtLine,
    InvestmentPlan,
    PlanInputs,
    ProjectLine,
    TaxEstimate,
    build_plan,
    estimate_tax,
    load_references,
)
from app.domain.onboarding import recommended_emergency_months, risk_profile
from app.domain.wealth import emergency_fund, load_regulated_products, regulated_status
from app.models import FiscalSimulation
from app.services import advisor, analytics, documents

EXPENSIVE_RATE = Decimal("5")


def _capacity(db: Session, today: date, values: dict[str, Any]) -> int:
    declared = values.get("savings_capacity")
    if isinstance(declared, int) and declared > 0:
        return declared
    return max(0, int(advisor.cash_flow(db, today)["left_avg_cents"]))


def _committed_monthly(db: Session, today: date, values: dict[str, Any], goals: list[dict[str, Any]]) -> int:
    """Ce qui est déjà engagé chaque mois : épargne visée, versements d'objectifs, provisions des dépenses prévues."""
    total = int(analytics.forecast_inputs(db, today).savings_goal_cents)
    total += sum(int(g.get("monthly_contribution_cents") or 0) for g in goals)
    for p in values.get("planned_expenses") or []:
        d = date.fromisoformat(p["date"])
        if d >= today:
            months = max(1, (d.year - today.year) * 12 + d.month - today.month)
            total += -(-int(p["amount_cents"]) // months)
    return total


def _emergency(db: Session, today: date, values: dict[str, Any]) -> tuple[int, int, int, float]:
    balances = analytics.account_balances(db, today)
    essentials = analytics.monthly_essentials(db, today)
    ef = emergency_fund(balances, essentials)
    target_months = int(values.get("emergency_target_months") or recommended_emergency_months(values))
    return ef.available_cents, target_months * essentials, essentials, ef.months_covered


def purchase(db: Session, today: date, *, amount_cents: int, label: str, category: str | None) -> PurchasePlan:
    values = advisor.plain_answers(db)
    inputs = analytics.forecast_inputs(db, today)
    goals = analytics.savings_goals_status(db, today, load_regulated_products())
    liquid, target, _, _ = _emergency(db, today, values)
    return plan_purchase(
        inputs.run,
        today=today,
        amount_cents=amount_cents,
        label=label,
        buffer_cents=inputs.buffer_cents,
        monthly_capacity_cents=_capacity(db, today, values),
        committed_monthly_cents=_committed_monthly(db, today, values, goals),
        liquid_savings_cents=liquid,
        emergency_target_cents=target,
        category=category,
    )


def _tax(db: Session, today: date, values: dict[str, Any], household: dict[str, Any]) -> TaxEstimate:
    doc = documents.latest(db, "avis_impot")
    net_income = values.get("net_monthly_income") or advisor.detected_facts(db, today).salary_monthly_cents
    kids = len(household.get("children_birth_dates") or []) if household else int(values.get("dependents") or 0)
    hh = str(values.get("household") or "")
    couple = bool(household.get("couple")) if household else hh.startswith("couple")
    return estimate_tax(
        declared_bracket=str(values["tax_bracket"]) if values.get("tax_bracket") not in (None, "inconnue") else None,
        document_taxable_cents=(doc or {}).get("taxable_income_cents") or (doc or {}).get("rfr_cents"),
        document_parts=Decimal(str(doc["parts"])) if doc and doc.get("parts") else None,
        net_monthly_income_cents=net_income,
        yearly_extras_cents=values.get("yearly_extras"),
        couple=couple,
        children=kids,
        single_parent=hh == "parent_solo",
        refs=load_references(),
    )


def investment_plan(db: Session, today: date) -> InvestmentPlan:
    values = advisor.plain_answers(db)
    household = analytics.load_profile(db) or {}
    liquid, target, essentials, months_covered = _emergency(db, today, values)
    balances = analytics.account_balances(db, today)
    products = load_regulated_products()
    debts = [
        DebtLine(
            str(d.get("label", "Crédit")),
            int(d.get("remaining_cents") or 0),
            int(d.get("monthly_cents") or 0),
            Decimal(str(d.get("rate_percent") or 0)),
        )
        for d in values.get("debts") or []
    ]
    tracked = analytics.savings_goals_status(db, today, products)
    projects = [
        ProjectLine(
            str(g["name"]),
            int(g["target_cents"]),
            g["target_date"] if isinstance(g["target_date"], date) else None,
            2,
            int(g["current_cents"]),
        )
        for g in tracked
    ]
    names = {p.label.lower() for p in projects}
    for g in values.get("life_goals") or []:
        if str(g.get("label", "")).lower() in names or not g.get("amount_cents"):
            continue
        projects.append(
            ProjectLine(
                str(g["label"]),
                int(g["amount_cents"]),
                date.fromisoformat(g["target_date"]) if g.get("target_date") else None,
                int(g.get("priority") or 2),
            )
        )
    sim_row = db.scalar(select(FiscalSimulation).order_by(FiscalSimulation.id.desc()).limit(1))
    sim = json.loads(sim_row.result) if sim_row else None
    expensive = any(d.rate_percent >= EXPENSIVE_RATE and d.remaining_cents > 0 for d in debts)
    risk = risk_profile(values, months_covered, expensive)
    forecast = analytics.forecast_inputs(db, today).run()
    outside: dict[str, int] = {}
    for a in values.get("outside_assets") or []:
        outside[str(a.get("kind", "autre"))] = outside.get(str(a.get("kind", "autre")), 0) + int(
            a.get("value_cents") or 0
        )
    inputs = PlanInputs(
        today=today,
        monthly_capacity_cents=max(0, _capacity(db, today, values) - _committed_monthly(db, today, values, tracked)),
        liquid_savings_cents=liquid,
        emergency_target_cents=target,
        monthly_essentials_cents=essentials,
        debts=debts,
        projects=projects,
        regulated=regulated_status(balances, products),
        lep_eligible=bool(sim.get("lep_eligible")) if sim else None,
        risk_label=risk.label,
        risk_horizon=values.get("risk_horizon"),
        red_lines=[str(r.get("rule", "")) for r in values.get("red_lines") or []],
        tax=_tax(db, today, values, household),
        age=advisor.age_from(household.get("birth_date"), today) if household else None,
        overdraft=forecast.overdraft_date is not None,
        outside_assets=outside,
    )
    return build_plan(inputs, load_references())


def investment_plan_dict(db: Session, today: date) -> dict[str, Any]:
    return asdict(investment_plan(db, today))
