"""Catégories : amorçage, construction du catégoriseur depuis la base, recatégorisation apprenante."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.categorize import BUILTIN_CATEGORIES, Categorizer, Rule, TokenBayes
from app.domain.forecast import CategoryInfo
from app.models import Category, CategoryRule, CategorySource, Transaction


def seed_categories(db: Session) -> None:
    existing = set(db.scalars(select(Category.slug)))
    for c in BUILTIN_CATEGORIES:
        if c.slug not in existing:
            db.add(Category(slug=c.slug, name=c.name, kind=c.kind, color=c.color, sort_order=c.order, is_builtin=True))
    db.flush()


def category_ids(db: Session) -> dict[str, int]:
    return {slug: cid for cid, slug in db.execute(select(Category.id, Category.slug))}


def category_infos(db: Session) -> dict[str, CategoryInfo]:
    return {c.slug: CategoryInfo(c.slug, c.name, c.kind, c.monthly_budget_cents) for c in db.scalars(select(Category))}


def load_categorizer(db: Session) -> Categorizer:
    rules = [
        Rule(r.pattern, r.match, r.direction, r.category.slug, r.source)
        for r in db.scalars(select(CategoryRule).order_by(CategoryRule.id))
    ]
    trusted = (CategorySource.MANUAL, CategorySource.USER_RULE, CategorySource.LEARNED)
    samples = db.execute(
        select(Transaction.raw_label, Category.slug)
        .join(Category, Transaction.category_id == Category.id)
        .where(Transaction.category_source.in_(trusted))
        .order_by(Transaction.id.desc())
        .limit(20_000)
    )
    return Categorizer(rules, TokenBayes().fit(samples))


def recategorize(
    db: Session,
    txn: Transaction,
    category: Category,
    *,
    learn: bool = True,
    source: CategorySource = CategorySource.MANUAL,
) -> int:
    """Catégorise une transaction à la main. Si ``learn`` : mémorise « ce marchand -> cette catégorie »
    et l'applique aux autres opérations du même marchand qui n'ont pas été choisies à la main.
    Retourne le nombre d'autres transactions mises à jour."""
    txn.category_id = category.id
    txn.category_source = source
    if not learn or not txn.merchant:
        return 0
    direction = "in" if txn.amount_cents > 0 else "out"
    rule = db.scalar(
        select(CategoryRule).where(
            CategoryRule.pattern == txn.merchant,
            CategoryRule.match == "merchant",
            CategoryRule.direction == direction,
        )
    )
    if rule is None:
        db.add(
            CategoryRule(
                pattern=txn.merchant,
                match="merchant",
                direction=direction,
                category_id=category.id,
                source="learned",
            )
        )
    else:
        rule.category_id = category.id
        rule.hits += 1
    sign_filter = Transaction.amount_cents > 0 if direction == "in" else Transaction.amount_cents < 0
    res = db.execute(
        update(Transaction)
        .where(
            Transaction.merchant == txn.merchant,
            Transaction.id != txn.id,
            sign_filter,
            Transaction.is_transfer.is_(False),
            Transaction.category_source.in_(
                [
                    CategorySource.NONE,
                    CategorySource.BUILTIN,
                    CategorySource.BAYES,
                    CategorySource.LEARNED,
                    CategorySource.AGENT,
                ]
            ),
        )
        .values(category_id=category.id, category_source=CategorySource.LEARNED)
    )
    return int(getattr(res, "rowcount", 0) or 0)


def reapply_rules(db: Session, categorizer: Categorizer, ids: dict[str, int]) -> int:
    """Rejoue la catégorisation sur l'historique — sans jamais toucher un choix manuel ni un virement interne."""
    changed = 0
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.category_source != CategorySource.MANUAL, Transaction.is_transfer.is_(False))
        .limit(50_000)
    )
    for t in rows:
        d = categorizer.decide(t.raw_label, t.merchant, t.amount_cents)
        if t.aid_kind and d.source in ("builtin", "none", "bayes"):
            continue  # la reconnaissance des aides prime sur le dictionnaire
        new_id = ids.get(d.category)
        if new_id != t.category_id:
            t.category_id, t.category_source = new_id, CategorySource(d.source)
            changed += 1
    return changed
