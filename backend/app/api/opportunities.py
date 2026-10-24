"""Centre d'opportunités : pistes classées, décisions, démarche liée, gains confirmés."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import opportunities, procedures

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

KEY = Field(min_length=1, max_length=160)


class KeyIn(BaseModel):
    key: str = KEY


class DecideIn(BaseModel):
    key: str = KEY
    decision: str = Field(pattern="^(dismissed|snoozed|new)$")
    days: int = Field(default=30, ge=1, le=365)


class ConfirmIn(BaseModel):
    key: str = KEY
    amount_cents: int = Field(ge=1, le=100_000_000)
    period: str = Field(pattern="^(once|month|year)$")
    label: str = Field(default="", max_length=200)
    procedure_id: int | None = None


class SettingsIn(BaseModel):
    time_value_cents_per_hour: int | None = Field(default=None, ge=0, le=50_000)
    priorities: list[str] | None = Field(default=None, max_length=5)


def _http(exc: opportunities.OpportunityError | procedures.ProcedureError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def center(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return opportunities.center(db, today)


@router.get("/top")
def top(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return {
        "opportunities": opportunities.top(db, today),
        "value": opportunities.value_summary(db),
        "priorities": opportunities.priorities(db),  # l'étape « Tes priorités » du parcours part de ton choix
    }


@router.post("/start", status_code=201)
def start(body: KeyIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        return opportunities.start(db, body.key, actor=ctx.user.username, today=today)
    except (opportunities.OpportunityError, procedures.ProcedureError) as exc:
        raise _http(exc) from exc


@router.post("/decide", status_code=204)
def decide(body: DecideIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> None:
    try:
        opportunities.decide(db, body.key, body.decision, actor=ctx.user.username, today=today, days=body.days)
    except opportunities.OpportunityError as exc:
        raise _http(exc) from exc


@router.post("/confirm", status_code=204)
def confirm(body: ConfirmIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> None:
    try:
        opportunities.confirm(
            db,
            body.key,
            amount_cents=body.amount_cents,
            period=body.period,
            label=body.label,
            actor=ctx.user.username,
            today=today,
            procedure_id=body.procedure_id,
        )
    except (opportunities.OpportunityError, procedures.ProcedureError) as exc:
        raise _http(exc) from exc


@router.put("/settings", status_code=204)
def settings(body: SettingsIn, ctx: AuthDep, db: DbDep) -> None:
    try:
        if body.time_value_cents_per_hour is not None:
            opportunities.set_time_value(db, body.time_value_cents_per_hour, actor=ctx.user.username)
        if body.priorities is not None:
            opportunities.set_priorities(db, body.priorities, actor=ctx.user.username)
    except opportunities.OpportunityError as exc:
        raise _http(exc) from exc
