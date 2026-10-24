"""Corrélations : ce que tes contrats, tes relevés et tes aides disent ensemble. Lecture, décision (écarter),
démarche à préparer. Bank n'envoie rien à personne."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import correlations, credit_watch, procedures

router = APIRouter(prefix="/api/correlations", tags=["correlations"])


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=150)


class DecideIn(KeyIn):
    decision: str = Field(pattern="^(dismissed|new)$")


class CommuteIn(BaseModel):
    one_way_km: Decimal = Field(gt=0, le=500, max_digits=5, decimal_places=1)
    fiscal_hp: int = Field(ge=1, le=50)
    electric: bool = False


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


@router.put("/commute")
def save_commute(body: CommuteIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    """Ton trajet domicile-travail (frais réels) : distance d'un aller, puissance fiscale, électrique. Chiffré."""
    try:
        credit_watch.save_commute(
            db, one_way_km=body.one_way_km, fiscal_hp=body.fiscal_hp, electric=body.electric, actor=ctx.user.username
        )
    except credit_watch.CreditWatchError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return correlations.view(db, today)


@router.delete("/commute")
def clear_commute(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        credit_watch.clear_commute(db, actor=ctx.user.username)
    except credit_watch.CreditWatchError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return correlations.view(db, today)
