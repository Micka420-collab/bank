"""Ton logement face aux données publiques (#34) : les DPE de ton immeuble (ADEME) et celui que tu reconnais comme le
tien, la carte des loyers de ta commune (ANIL), l'encadrement des loyers (F1314, Paris en open data), les taux de taxe
foncière de ta commune, la disponibilité de Géorisques, les constats qui en découlent et ce qui manque pour en dire
plus."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.services import consents, correlations
from app.services import housing as svc

router = APIRouter(prefix="/api/housing", tags=["housing"])


class DetailsIn(BaseModel):
    epoque: str | None = Field(default=None, max_length=20, description="époque de construction (encadrement)")
    rooms: int | None = Field(default=None, ge=1, le=20, description="pièces principales")


class ChooseIn(BaseModel):
    number: str | None = Field(default=None, pattern=r"^[0-9A-Z]{13}$", description="numéro du DPE ; vide : aucun")


def _fetch(request: Request) -> svc.Fetch:
    return svc.default_fetch(http=request.app.state.housing_http, resolver=request.app.state.housing_resolver)


def _limit(request: Request) -> None:
    if not request.app.state.limiters["housing"].hit(client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Déjà demandé il y a peu : réessaie dans quelques minutes"
        )


def _view(db: Any, today: Any) -> dict[str, Any]:
    return svc.view(db, today) | correlations.housing_checks(db, today)


@router.get("")
def overview(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return _view(db, today)


async def _refresh(request: Request, today: Any, job: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """La relève tourne hors de la boucle principale (fichier de quelques Mo), dans sa propre session."""
    _limit(request)

    def run() -> dict[str, Any]:
        with request.app.state.sessionmaker() as db:
            result = job(db)
            db.commit()
            return result

    try:
        result = await asyncio.to_thread(run)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except svc.HousingError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    with request.app.state.sessionmaker() as db:
        return {"result": result, "view": _view(db, today)}


@router.post("/dpe/refresh")
async def refresh_dpe(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    fetch, actor = _fetch(request), ctx.user.username
    return await _refresh(request, today, lambda db: svc.refresh_dpe(db, fetch=fetch, now=utcnow(), actor=actor))


@router.post("/dpe/choose")
def choose(body: ChooseIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.choose_dpe(db, body.number, actor=ctx.user.username, now=utcnow())
    except svc.HousingError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _view(db, today)


@router.post("/market/refresh")
async def refresh_market(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    fetch, actor = _fetch(request), ctx.user.username
    return await _refresh(
        request, today, lambda db: svc.refresh_market(db, fetch=fetch, now=utcnow(), today=today, actor=actor)
    )


@router.post("/details")
def save_details(body: DetailsIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.save_details(db, epoque=body.epoque, rooms=body.rooms, actor=ctx.user.username)
    except svc.HousingError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _view(db, today)


@router.post("/control/refresh")
async def refresh_control(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    fetch, actor = _fetch(request), ctx.user.username
    return await _refresh(request, today, lambda db: svc.refresh_control(db, fetch=fetch, now=utcnow(), actor=actor))


@router.post("/taxes/refresh")
async def refresh_taxes(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    fetch, actor = _fetch(request), ctx.user.username
    return await _refresh(request, today, lambda db: svc.refresh_taxes(db, fetch=fetch, now=utcnow(), actor=actor))


@router.post("/risks/refresh")
async def refresh_risks(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    fetch, actor = _fetch(request), ctx.user.username
    return await _refresh(request, today, lambda db: svc.refresh_risks(db, fetch=fetch, now=utcnow(), actor=actor))
