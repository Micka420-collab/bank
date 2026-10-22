"""Corrélations : ce que tes contrats, tes relevés et tes aides disent ensemble. Lecture, décision (écarter),
démarche à préparer. Bank n'envoie rien à personne."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import correlations, procedures

router = APIRouter(prefix="/api/correlations", tags=["correlations"])


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=150)


class DecideIn(KeyIn):
    decision: str = Field(pattern="^(dismissed|new)$")


@router.get("")
def listing(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return correlations.view(db, today)


@router.post("/decide")
def decide(body: DecideIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        correlations.decide(db, body.key, body.decision, actor=ctx.user.username)
    except correlations.CorrelationError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return correlations.view(db, today)


@router.post("/start", status_code=201)
def start(body: KeyIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        proc_id = correlations.start(db, body.key, actor=ctx.user.username, today=today)
    except correlations.CorrelationError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except procedures.ProcedureError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {"procedure_id": proc_id}
