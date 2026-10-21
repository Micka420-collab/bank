"""« Bank te demande » : les cartes du moment, et ce que tu en fais (une action = un geste)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.domain.onboarding import AnswerError
from app.services import initiatives

router = APIRouter(prefix="/api/initiatives", tags=["initiatives"])


@router.get("")
def list_cards(_: AuthDep, db: DbDep, today: TodayDep, limit: int = Query(default=3, ge=1, le=20)) -> dict[str, Any]:
    return initiatives.listing(db, today, limit)


class ActIn(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    value: Any = None


@router.post("/{key:path}/act")
def act(
    key: str, body: ActIn, ctx: AuthDep, db: DbDep, today: TodayDep, limit: int = Query(default=3, ge=1, le=20)
) -> dict[str, Any]:
    if len(key) > 160:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Clé invalide")
    try:
        result = initiatives.act(
            db, key=key, action_id=body.action, value=body.value, actor=f"user:{ctx.user.username}", today=today
        )
    except (initiatives.InitiativeError, AnswerError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    return {"result": result, **initiatives.listing(db, today, limit)}
