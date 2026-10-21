"""Couche « shell impératif » : lit la base et alimente les fonctions pures du domaine."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.aids import AidPayment, AidsSummary, summarize_aids
from app.domain.forecast import (
    CategoryInfo,
    MonthForecast,
    UpcomingEvent,
    build_forecast,
    month_bounds,
    suggest_budgets,
)
from app.domain.recurring import RecurringSeries, Txn, add_months, detect_recurring
from app.domain.wealth import AccountBalance, RegulatedProduct, monthly_needed, months_to_goal
from app.models import (
    Account,
    AccountKind,
    Category,
    FinancialProfile,
    HouseholdProfile,
    SavingsGoal,
    Transaction,
    WealthSnapshot,
)
from app.services.categories import category_infos
from app.services.settings_store import get_setting

HISTORY_DAYS = 400


def budget_accounts(db: Session) -> list[Account]:
    return list(
        db.scalars(
            select(Account).where(
                Account.archived.is_(False),
                Account.include_in_budget.is_(True),
                Account.kind == AccountKind.CHECKING,
            )
        )
    )


def load_txns(db: Session, account_ids: list[int], since: date) -> list[Txn]:
    if not account_ids:
        return []
    rows = db.execute(
        select(
            Transaction.id,
            Transaction.booking_date,
            Transaction.amount_cents,
            Transaction.merchant,
            Category.slug,
            Transaction.account_id,
            Transaction.is_transfer,
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.account_id.in_(account_ids),
            Transaction.booking_date >= since,
            Transaction.status == "booked",
        )
        .order_by(Transaction.booking_date)
    )
    return [Txn(i, d, a, m, c or "a_categoriser", acc, tr) for i, d, a, m, c, acc, tr in rows]


def recurring_for_budget(db: Session, today: date) -> list[RecurringSeries]:
    accounts = budget_accounts(db)
    return detect_recurring(load_txns(db, [a.id for a in accounts], today - timedelta(days=HISTORY_DAYS)), today)


@dataclass(frozen=True)
class ForecastInputs:
    today: date
    txns: list[Txn]
    balance_cents: int
    series: list[RecurringSeries]
    categories: dict[str, CategoryInfo]
    savings_goal_cents: int
    buffer_cents: int
    planned: list[UpcomingEvent]

    def run(self, extra: Sequence[UpcomingEvent] = (), savings_goal_cents: int | None = None) -> MonthForecast:
        return build_forecast(
            today=self.today,
            txns=self.txns,
            current_balance_cents=self.balance_cents,
            series=self.series,
            categories=self.categories,
            savings_goal_cents=self.savings_goal_cents if savings_goal_cents is None else savings_goal_cents,
            buffer_cents=self.buffer_cents,
            extra_events=[*self.planned, *extra],
        )


def planned_expense_events(db: Session, today: date) -> list[UpcomingEvent]:
    """Les grosses dépenses déclarées à l'onboarding entrent dans la prévision du mois concerné."""
    profile = load_financial_profile(db)
    items = (profile.get("planned_expenses") or {}).get("value") or []
    _, end = month_bounds(today)
    out = []
    for item in items:
        d = date.fromisoformat(item["date"])
        if today <= d <= end:
            out.append(UpcomingEvent(d, item["label"], -int(item["amount_cents"]), "divers", "fixed", 1.0, late=False))
    return out


def forecast_inputs(db: Session, today: date) -> ForecastInputs:
    accounts = budget_accounts(db)
    txns = load_txns(db, [a.id for a in accounts], today - timedelta(days=HISTORY_DAYS))
    return ForecastInputs(
        today=today,
        txns=txns,
        balance_cents=sum(a.balance_cents for a in accounts),
        series=detect_recurring(txns, today),
        categories=category_infos(db),
        savings_goal_cents=int(get_setting(db, "savings_goal_cents")),
        buffer_cents=int(get_setting(db, "buffer_cents")),
        planned=planned_expense_events(db, today),
    )


def month_forecast(db: Session, today: date) -> tuple[MonthForecast, dict[str, int]]:
    inputs = forecast_inputs(db, today)
    return inputs.run(), suggest_budgets(inputs.txns, today, inputs.categories)


def load_financial_profile(db: Session) -> dict[str, Any]:
    row = db.get(FinancialProfile, 1)
    data: dict[str, Any] = json.loads(row.data) if row else {}
    return data


def aid_payments(db: Session, since: date) -> list[AidPayment]:
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents, Transaction.aid_kind).where(
            Transaction.aid_kind.is_not(None), Transaction.booking_date >= since
        )
    )
    return [AidPayment(d, a, k) for d, a, k in rows if k]


def aids_summary(db: Session, today: date) -> AidsSummary:
    return summarize_aids(aid_payments(db, add_months(today.replace(day=1), -12)), today)


def monthly_income_series(db: Session, today: date) -> dict[str, dict[str, int]]:
    """Revenus mensuels par nature (salaire, chômage, retraite) sur 24 mois, pour la simulation fiscale."""
    since = add_months(today.replace(day=1), -24)
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents, Category.slug, Transaction.aid_kind)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.booking_date >= since,
            Transaction.booking_date < today.replace(day=1),
            Transaction.amount_cents > 0,
            Transaction.is_transfer.is_(False),
        )
    )
    out: dict[str, dict[str, int]] = {
        "salary": defaultdict(int),
        "unemployment": defaultdict(int),
        "pension": defaultdict(int),
    }
    for d, amount, slug, aid in rows:
        key = f"{d.year:04d}-{d.month:02d}"
        if aid == "ft_are":
            out["unemployment"][key] += amount
        elif aid == "retraite_pension":
            out["pension"][key] += amount
        elif slug == "salaire":
            out["salary"][key] += amount
    return {k: dict(v) for k, v in out.items()}


def account_balances(db: Session) -> list[AccountBalance]:
    return [
        AccountBalance(a.id, a.name, a.kind, a.balance_cents)
        for a in db.scalars(select(Account).where(Account.archived.is_(False)))
    ]


def monthly_essentials(db: Session, today: date) -> int:
    """Dépenses essentielles moyennes (charges fixes + courses, santé, transport) sur 3 mois complets."""
    start = add_months(today.replace(day=1), -3)
    end = today.replace(day=1)
    essential = {
        "logement",
        "energie",
        "telecom",
        "assurances",
        "credits",
        "impots",
        "courses",
        "sante",
        "transport",
        "enfants",
    }
    rows = db.execute(
        select(Transaction.amount_cents, Category.slug)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.booking_date >= start,
            Transaction.booking_date < end,
            Transaction.amount_cents < 0,
            Transaction.is_transfer.is_(False),
        )
    )
    total = -sum(a for a, slug in rows if slug in essential)
    return round(total / 3)


def snapshot_wealth(db: Session, today: date, assets: int, liabilities: int) -> None:
    snap = db.scalar(select(WealthSnapshot).where(WealthSnapshot.day == today))
    if snap is None:
        db.add(
            WealthSnapshot(
                day=today, assets_cents=assets, liabilities_cents=liabilities, net_cents=assets - liabilities
            )
        )
    else:
        snap.assets_cents, snap.liabilities_cents, snap.net_cents = assets, liabilities, assets - liabilities
    db.flush()


def wealth_history(db: Session, today: date, days: int = 730) -> list[tuple[date, int]]:
    since = today - timedelta(days=days)
    return list(
        db.execute(
            select(WealthSnapshot.day, WealthSnapshot.net_cents)
            .where(WealthSnapshot.day >= since)
            .order_by(WealthSnapshot.day)
        )
    )


def load_profile(db: Session) -> dict[str, Any] | None:
    row = db.get(HouseholdProfile, 1)
    return json.loads(row.data) if row else None


def current_month_bounds(today: date) -> tuple[date, date]:
    return month_bounds(today)


def savings_goals_status(db: Session, today: date, products: Sequence[RegulatedProduct]) -> list[dict[str, Any]]:
    goals: list[dict[str, Any]] = []
    for g in db.scalars(select(SavingsGoal).order_by(SavingsGoal.id)):
        acc = db.get(Account, g.account_id) if g.account_id else None
        current = max(0, acc.balance_cents) if acc else 0
        # Objectif adossé à un livret : on projette avec son taux réglementé.
        rate = next((p.rate_percent for p in products if acc is not None and p.kind == acc.kind), Decimal(0))
        goals.append(
            {
                "id": g.id,
                "name": g.name,
                "target_cents": g.target_cents,
                "current_cents": current,
                "target_date": g.target_date,
                "monthly_contribution_cents": g.monthly_contribution_cents,
                "progress": round(min(1.0, current / g.target_cents), 3),
                "months_to_goal": months_to_goal(current, g.target_cents, g.monthly_contribution_cents, rate),
                "monthly_needed_cents": monthly_needed(current, g.target_cents, g.target_date, today)
                if g.target_date
                else None,
            }
        )
    return goals
