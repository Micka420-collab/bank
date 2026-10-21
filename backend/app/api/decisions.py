"""« Je peux l'acheter ? » et « où placer ? » — les mêmes calculs que ceux offerts à l'agent, pour toi."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, TodayDep
from app.models import Category
from app.security import audit
from app.services import decisions

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class PurchaseIn(BaseModel):
    amount_cents: int = Field(gt=0, le=100_000_000_00)
    label: str = Field(default="Achat", max_length=80)
    category: str | None = Field(default=None, max_length=40)


@router.post("/purchase")
def purchase(body: PurchaseIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if body.category and db.scalar(select(Category.id).where(Category.slug == body.category)) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Catégorie inconnue")
    plan = decisions.purchase(db, today, amount_cents=body.amount_cents, label=body.label, category=body.category)
    audit.record(db, ctx.user.username, "decision.purchase", amount_cents=body.amount_cents, answer=plan.answer)
    return asdict(plan)


@router.get("/investment-plan")
def investment_plan(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return decisions.investment_plan_dict(db, today)
