"""Historique pour le tableau de bord : soldes jour par jour, revenus et dépenses par période, répartition par
catégorie, comparaison avec la période précédente, dernières opérations, anomalies.

Le solde passé est reconstruit depuis le solde actuel des comptes du budget en remontant les opérations :
il est donc exact au jour près même sans historique de soldes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categorize import TONES, category_tone
from app.domain.recurring import add_months
from app.domain.wealth import net_worth
from app.models import Account, Category, Transaction, WealthSnapshot
from app.services import analytics

RANGES = ("7d", "30d", "3m", "1y", "all")
MONTHS_FR = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def range_start(today: date, rng: str, first: date | None) -> date:
    if rng == "7d":
        return today - timedelta(days=6)
    if rng == "30d":
        return today - timedelta(days=29)
    if rng == "3m":
        return add_months(today, -3) + timedelta(days=1)
    if rng == "1y":
        return add_months(today, -12) + timedelta(days=1)
    return min(first, today - timedelta(days=29)) if first else today - timedelta(days=29)


def _bucket(rng: str, d: date) -> tuple[str, date]:
    """Clé et début de période : jour (7 j), semaine (30 j, 3 mois), mois (1 an, tout)."""
    if rng == "7d":
        return d.isoformat(), d
    if rng in ("30d", "3m"):
        start = d - timedelta(days=d.weekday())
        return start.isoformat(), start
    start = d.replace(day=1)
    return f"{start.year:04d}-{start.month:02d}", start


def _label(rng: str, start: date) -> str:
    if rng == "7d":
        return f"{start.day} {MONTHS_FR[start.month - 1]}"
    if rng in ("30d", "3m"):
        return f"sem. du {start.day} {MONTHS_FR[start.month - 1]}"
    return f"{MONTHS_FR[start.month - 1]} {start.year % 100:02d}"


def _category_entry(slug: str, name: str | None, color: str | None) -> dict[str, Any]:
    """Une part du donut : la couleur stockée, et la teinte du thème (``tone``) tant qu'elle est celle par défaut."""
    if color is None:  # opération sans catégorie
        return {"slug": slug, "name": name or "À classer", "color": TONES["muted"], "tone": "muted", "cents": 0}
    return {"slug": slug, "name": name or "À classer", "color": color, "tone": category_tone(slug, color), "cents": 0}


def history(db: Session, today: date, rng: str = "30d") -> dict[str, Any]:
    if rng not in RANGES:
        rng = "30d"
    accounts = analytics.budget_accounts(db)
    ids = [a.id for a in accounts]
    current = sum(a.balance_cents for a in accounts)
    rows = (
        db.execute(
            select(
                Transaction.booking_date,
                Transaction.amount_cents,
                Transaction.is_transfer,
                Category.slug,
                Category.name,
                Category.color,
                Category.kind,
            )
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(Transaction.account_id.in_(ids), Transaction.status == "booked")
            .order_by(Transaction.booking_date)
        ).all()
        if ids
        else []
    )
    first = rows[0][0] if rows else None
    start = range_start(today, rng, first)
    span = (today - start).days + 1
    prev_start = start - timedelta(days=span)

    # Soldes jour par jour, reconstruits à rebours depuis le solde actuel.
    per_day_delta: dict[date, int] = defaultdict(int)
    for d, amount, _transfer, *_ in rows:
        per_day_delta[d] += amount
    balances: list[dict[str, Any]] = []
    balance = current
    cursor = today
    series: list[tuple[date, int]] = []
    while cursor >= start:
        series.append((cursor, balance))
        balance -= per_day_delta.get(cursor, 0)
        cursor -= timedelta(days=1)
    balances = [{"date": d.isoformat(), "balance_cents": b} for d, b in reversed(series)]

    def totals(a: date, b: date) -> dict[str, int]:
        inc = spent = 0
        for d, amount, transfer, _slug, _name, _color, kind in rows:
            if transfer or not a <= d <= b:
                continue
            if amount > 0:
                inc += amount
            elif kind != "savings":
                spent += -amount
        return {"income_cents": inc, "spent_cents": spent, "net_cents": inc - spent}

    now = totals(start, today)
    previous = totals(prev_start, start - timedelta(days=1))

    by_cat: dict[str, dict[str, Any]] = {}
    prev_by_cat: dict[str, int] = defaultdict(int)
    for d, amount, transfer, slug, name, color, kind in rows:
        if transfer or amount >= 0 or kind == "savings":
            continue
        key = slug or "a_categoriser"
        if start <= d <= today:
            entry = by_cat.setdefault(key, _category_entry(key, name, color))
            entry["cents"] += -amount
        elif prev_start <= d < start:
            prev_by_cat[key] += -amount
    cats = sorted(by_cat.values(), key=lambda c: -c["cents"])
    total_spent = sum(c["cents"] for c in cats) or 1
    # Six parts au plus, puis « Autres » : autant que de teintes de données (--c1…--c6), chaque part reste distincte.
    top = cats[:6]
    rest = sum(c["cents"] for c in cats[6:])
    if rest:
        top.append({"slug": "autres", "name": "Autres", "color": TONES["muted"], "tone": "muted", "cents": rest})
    for c in top:
        c["share"] = round(c["cents"] / total_spent, 3)

    buckets: dict[str, dict[str, Any]] = {}
    for d, amount, transfer, _slug, _name, _color, kind in rows:
        if transfer or not start <= d <= today:
            continue
        key, bstart = _bucket(rng, d)
        b = buckets.setdefault(
            key,
            {
                "key": key,
                "start": bstart.isoformat(),
                "label": _label(rng, bstart),
                "income_cents": 0,
                "spent_cents": 0,
            },
        )
        if amount > 0:
            b["income_cents"] += amount
        elif kind != "savings":
            b["spent_cents"] += -amount
    periods = [buckets[k] for k in sorted(buckets)]

    anomalies = []
    for c in cats[:12]:
        prev = prev_by_cat.get(c["slug"], 0)
        if prev >= 3_000 and c["cents"] >= prev * 1.5 and c["cents"] - prev >= 5_000:
            anomalies.append(
                {
                    "slug": c["slug"],
                    "name": c["name"],
                    "cents": c["cents"],
                    "previous_cents": prev,
                    "ratio": round(c["cents"] / prev, 2),
                }
            )

    latest = (
        db.scalars(
            select(Transaction)
            .where(Transaction.account_id.in_(ids), Transaction.status == "booked")
            .order_by(Transaction.booking_date.desc(), Transaction.id.desc())
            .limit(6)
        ).all()
        if ids
        else []
    )
    names = {c.id: c for c in db.scalars(select(Category))}
    return {
        "range": rng,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "current_balance_cents": current,
        "balances": balances,
        "now": now,
        "previous": previous,
        "categories": top,
        "periods": periods,
        "anomalies": anomalies,
        "latest": [
            {
                "id": t.id,
                "date": t.booking_date.isoformat(),
                "merchant": t.merchant or t.raw_label,
                "label": t.raw_label,
                "amount_cents": t.amount_cents,
                "category": names[t.category_id].slug if t.category_id in names else None,
                "category_name": names[t.category_id].name if t.category_id in names else None,
                "is_transfer": t.is_transfer,
            }
            for t in latest
        ],
    }


def net_worth_history(db: Session, today: date, days: int = 730) -> list[tuple[date, int]]:
    """Valeur nette jour par jour, reconstruite à rebours depuis les soldes actuels de tous les comptes.

    Les virements internes ne changent pas la valeur nette : ils sont ignorés, ce qui reste juste même quand un seul
    des deux comptes est importé. Un compte sans opération (solde saisi à la main) est supposé stable. Sans aucune
    opération, on retombe sur les instantanés pris à chaque visite de la page Patrimoine.
    """
    accounts = db.scalars(select(Account).where(Account.archived.is_(False))).all()
    current = net_worth(analytics.account_balances(db, today)).net_cents
    ids = [a.id for a in accounts]
    rows = (
        db.execute(
            select(Transaction.booking_date, Transaction.amount_cents)
            .where(Transaction.account_id.in_(ids), Transaction.status == "booked", Transaction.is_transfer.is_(False))
            .order_by(Transaction.booking_date)
        ).all()
        if ids
        else []
    )
    since = today - timedelta(days=days)
    if not rows:
        return list(
            db.execute(
                select(WealthSnapshot.day, WealthSnapshot.net_cents)
                .where(WealthSnapshot.day >= since)
                .order_by(WealthSnapshot.day)
            )
        )
    start = max(since, rows[0][0])
    per_day: dict[date, int] = defaultdict(int)
    for d, amount in rows:
        per_day[d] += amount
    series: list[tuple[date, int]] = []
    balance = current
    cursor = today
    while cursor >= start:
        series.append((cursor, balance))
        balance -= per_day.get(cursor, 0)
        cursor -= timedelta(days=1)
    series.reverse()
    return series
