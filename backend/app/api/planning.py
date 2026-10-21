"""Planning de travail : agenda iCal relié à la paie, modèle de paie, paie prévue."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.domain.planning import MAX_ICS_BYTES, PlanningError
from app.security import audit
from app.services import analytics, planning

router = APIRouter(prefix="/api/planning", tags=["planning"])


def _salary(db: Any, today: Any) -> Any:
    return analytics.salary_series(analytics.recurring_for_budget(db, today))


@router.get("")
def get_status(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return planning.status(db, today, _salary(db, today))


class UrlIn(BaseModel):
    url: str = Field(min_length=12, max_length=2000)


@router.post("/url", status_code=201)
async def connect_url(body: UrlIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    fetch = request.app.state.ics_fetcher
    try:
        # Téléchargement + lecture dans un thread : la boucle d'événements reste libre.
        _, count = await run_in_threadpool(
            lambda: planning.connect(db, actor=ctx.user.username, today=today, url=body.url, fetch=fetch)
        )
    except PlanningError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {**planning.status(db, today, _salary(db, today)), "imported": count}


@router.post("/file", status_code=201)
async def connect_file(file: Annotated[UploadFile, File()], ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    data = await file.read(MAX_ICS_BYTES + 1)
    try:
        _, count = await run_in_threadpool(
            lambda: planning.connect(db, actor=ctx.user.username, today=today, data=data)
        )
    except PlanningError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {**planning.status(db, today, _salary(db, today)), "imported": count}


@router.post("/sync")
def sync(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if planning.plan(db) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun agenda relié")
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    count = planning.sync(db, today, fetch=request.app.state.ics_fetcher)
    audit.record(db, ctx.user.username, "planning.sync_manual", shifts=count)
    return {**planning.status(db, today, _salary(db, today)), "imported": count}


@router.delete("", status_code=204)
def disconnect(ctx: AuthDep, db: DbDep) -> None:
    planning.disconnect(db, ctx.user.username)


class RuleIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=40)
    unit: str = Field(max_length=12)
    amount_cents: int = Field(ge=-1_000_000, le=1_000_000)


class ModelIn(BaseModel):
    base_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    offset_months: int = Field(default=0, ge=0, le=1)
    rules: list[RuleIn] = Field(default_factory=list, max_length=8)


@router.put("/model")
def put_model(body: ModelIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        planning.set_model(
            db,
            actor=ctx.user.username,
            base_cents=body.base_cents,
            rules=[r.model_dump() for r in body.rules],
            offset_months=body.offset_months,
            today=today,
        )
    except PlanningError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return planning.status(db, today, _salary(db, today))
