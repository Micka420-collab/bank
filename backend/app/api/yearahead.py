"""L'année qui vient : projection sur douze mois, pics détectés, ajoutés ou écartés, provision mensuelle."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import yearahead as svc

router = APIRouter(prefix="/api/forecast/year", tags=["forecast"])


class SpikeIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    amount_cents: int = Field(ge=100, le=100_000_000)
    next_date: date
    every_months: int = Field(default=12, ge=1, le=60)


class IgnoreIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    ignored: bool = True


@router.get("")
def year(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today)


@router.post("/spikes", status_code=201)
def add_spike(body: SpikeIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.add_custom(
            db,
            label=body.label,
            amount_cents=body.amount_cents,
            next_date=body.next_date,
            every_months=body.every_months,
            actor=ctx.user.username,
        )
    except svc.YearAheadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return svc.view(db, today)


@router.delete("/spikes/{key}")
def remove_spike(key: str, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.remove_custom(db, key[:120], actor=ctx.user.username)
    except svc.YearAheadError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return svc.view(db, today)


@router.post("/ignore")
def ignore(body: IgnoreIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    svc.set_ignored(db, body.key, body.ignored, actor=ctx.user.username)
    return svc.view(db, today)
