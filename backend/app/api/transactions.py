"""Transactions, catégories, règles de catégorisation."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import AuthDep, DbDep
from app.domain.categorize import category_tone
from app.models import Category, CategoryRule, MerchantHint, Transaction
from app.security import audit
from app.services.categories import category_ids, load_categorizer, reapply_rules, recategorize

router = APIRouter(prefix="/api", tags=["transactions"])


class TransactionOut(BaseModel):
    id: int
    account_id: int
    booking_date: date
    amount_cents: int
    currency: str
    label: str
    merchant: str
    category: str | None
    category_source: str
    is_transfer: bool
    aid_kind: str | None
    note: str | None


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int


class TransactionPatch(BaseModel):
    category: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)
    learn: bool = True


class CategoryOut(BaseModel):
    slug: str
    name: str
    kind: str
    color: str
    tone: str | None  # teinte du thème (--c1…) tant que la couleur est celle par défaut
    monthly_budget_cents: int | None


class CategoryPatch(BaseModel):
    monthly_budget_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    name: str | None = Field(default=None, min_length=1, max_length=80)


class RuleIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=120)
    match: str = Field(default="contains", pattern="^(contains|merchant)$")
    direction: str = Field(default="any", pattern="^(in|out|any)$")
    category: str = Field(max_length=40)
    apply_to_history: bool = True


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/transactions")
def list_transactions(
    _: AuthDep,
    db: DbDep,
    account_id: int | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> TransactionPage:
    stmt = select(Transaction, Category.slug).outerjoin(Category, Transaction.category_id == Category.id)
    conds = []
    if account_id is not None:
        conds.append(Transaction.account_id == account_id)
    if date_from:
        conds.append(Transaction.booking_date >= date_from)
    if date_to:
        conds.append(Transaction.booking_date <= date_to)
    if category:
        conds.append(Category.slug == category)
    if q:
        pattern = f"%{_like_escape(q.strip())}%"
        conds.append(
            or_(
                Transaction.raw_label.ilike(pattern, escape="\\"),
                Transaction.merchant.ilike(pattern, escape="\\"),
                Transaction.note.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.where(*conds)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Transaction.booking_date.desc(), Transaction.id.desc()).limit(limit).offset(offset))
    return TransactionPage(
        total=total,
        items=[
            TransactionOut(
                id=t.id,
                account_id=t.account_id,
                booking_date=t.booking_date,
                amount_cents=t.amount_cents,
                currency=t.currency,
                label=t.raw_label,
                merchant=t.merchant,
                category=slug,
                category_source=t.category_source,
                is_transfer=t.is_transfer,
                aid_kind=t.aid_kind,
                note=t.note,
            )
            for t, slug in rows
        ],
    )


@router.patch("/transactions/{txn_id}")
def update_transaction(txn_id: int, body: TransactionPatch, ctx: AuthDep, db: DbDep) -> dict[str, int]:
    txn = db.get(Transaction, txn_id)
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction introuvable")
    propagated = 0
    if body.category is not None:
        cat = db.scalar(select(Category).where(Category.slug == body.category))
        if cat is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Catégorie inconnue")
        propagated = recategorize(db, txn, cat, learn=body.learn)
        if body.learn and txn.merchant:
            # La proposition de l'assistant pour ce marchand a servi (ou a été corrigée) : la règle prend le relais.
            hint = db.get(MerchantHint, txn.merchant)
            if hint is not None:
                db.delete(hint)
    if "note" in body.model_fields_set:
        txn.note = body.note
    audit.record(db, ctx.user.username, "transaction.update", txn=txn.id, propagated=propagated)
    return {"updated": 1, "propagated": propagated}


@router.get("/categories")
def list_categories(_: AuthDep, db: DbDep) -> list[CategoryOut]:
    return [
        CategoryOut(
            slug=c.slug,
            name=c.name,
            kind=c.kind,
            color=c.color,
            tone=category_tone(c.slug, c.color),
            monthly_budget_cents=c.monthly_budget_cents,
        )
        for c in db.scalars(select(Category).order_by(Category.sort_order))
    ]


@router.patch("/categories/{slug}")
def update_category(slug: str, body: CategoryPatch, ctx: AuthDep, db: DbDep) -> CategoryOut:
    cat = db.scalar(select(Category).where(Category.slug == slug))
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
    if "monthly_budget_cents" in body.model_fields_set:
        cat.monthly_budget_cents = body.monthly_budget_cents or None
    if body.name:
        cat.name = body.name
    audit.record(db, ctx.user.username, "category.update", category=slug)
    return CategoryOut(
        slug=cat.slug,
        name=cat.name,
        kind=cat.kind,
        color=cat.color,
        tone=category_tone(cat.slug, cat.color),
        monthly_budget_cents=cat.monthly_budget_cents,
    )


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, ctx: AuthDep, db: DbDep) -> dict[str, int]:
    ids = category_ids(db)
    if body.category not in ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Catégorie inconnue")
    pattern = " ".join(body.pattern.upper().split())
    existing = db.scalar(
        select(CategoryRule).where(
            CategoryRule.pattern == pattern,
            CategoryRule.match == body.match,
            CategoryRule.direction == body.direction,
        )
    )
    if existing:
        existing.category_id, existing.source = ids[body.category], "user"
    else:
        db.add(
            CategoryRule(
                pattern=pattern,
                match=body.match,
                direction=body.direction,
                category_id=ids[body.category],
                source="user",
            )
        )
    db.flush()
    updated = reapply_rules(db, load_categorizer(db), ids) if body.apply_to_history else 0
    audit.record(db, ctx.user.username, "rule.create", pattern=pattern, category=body.category, updated=updated)
    return {"updated": updated}
