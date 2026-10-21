"""Couche « shell impératif » : lit la base et alimente les fonctions pures du domaine."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.aids import AidPayment, AidsSummary, summarize_aids
from app.domain.forecast import MonthForecast, build_forecast, month_bounds, suggest_budgets
from app.domain.recurring import RecurringSeries, Txn, add_months, detect_recurring
from app.domain.wealth import AccountBalance
from app.models import Account, AccountKind, Category, HouseholdProfile, Transaction, WealthSnapshot
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


def month_forecast(db: Session, today: date) -> tuple[MonthForecast, dict[str, int]]:
    accounts = budget_accounts(db)
    ids = [a.id for a in accounts]
    txns = load_txns(db, ids, today - timedelta(days=HISTORY_DAYS))
    infos = category_infos(db)
    series = detect_recurring(txns, today)
    forecast = build_forecast(
        today=today,
        txns=txns,
        current_balance_cents=sum(a.balance_cents for a in accounts),
        series=series,
        categories=infos,
        savings_goal_cents=int(get_setting(db, "savings_goal_cents")),
        buffer_cents=int(get_setting(db, "buffer_cents")),
    )
    return forecast, suggest_budgets(txns, today, infos)


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


def load_profile(db: Session) -> dict[str, object] | None:
    row = db.get(HouseholdProfile, 1)
    return json.loads(row.data) if row else None


def current_month_bounds(today: date) -> tuple[date, date]:
    return month_bounds(today)
