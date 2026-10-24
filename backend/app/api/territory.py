"""Ton territoire : l'adresse géocodée (seule l'adresse que tu tapes part, vers la Géoplateforme de l'IGN), les jours
fériés de ta zone et les vacances de ton académie (sources publiques), chacun avec son accord."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.services import consents
from app.services import territory as svc

router = APIRouter(prefix="/api/territory", tags=["territory"])


class GeocodeIn(BaseModel):
    query: str = Field(min_length=3, max_length=200, description="ton adresse, ou seulement ta commune")


def _fetch(request: Request) -> svc.Fetch:
    return svc.default_fetch(http=request.app.state.territory_http, resolver=request.app.state.territory_resolver)


def _limit(request: Request) -> None:
    if not request.app.state.limiters["territory"].hit(client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Déjà demandé il y a peu : réessaie dans quelques minutes"
        )


@router.get("")
def overview(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today)


@router.post("/geocode")
def geocode(body: GeocodeIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    _limit(request)
    try:
        svc.geocode(db, body.query, fetch=_fetch(request), now=utcnow(), actor=ctx.user.username)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except svc.TerritoryError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return svc.view(db, today)


@router.delete("/place")
def remove(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.remove_place(db, actor=ctx.user.username)
    except svc.TerritoryError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return svc.view(db, today)


@router.post("/calendar/refresh")
async def refresh_calendar(request: Request, _: AuthDep, today: TodayDep) -> dict[str, Any]:
    """Relit tout de suite jours fériés et vacances (hors de la boucle principale, bornée en fréquence)."""
    _limit(request)
    fetch = _fetch(request)

    def run() -> dict[str, Any]:
        with request.app.state.sessionmaker() as db:
            result = svc.refresh_calendar(db, now=utcnow(), today=today, fetch=fetch, force=True)
            db.commit()
            return result

    try:
        result = await asyncio.to_thread(run)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    with request.app.state.sessionmaker() as db:
        return {"result": result, "view": svc.view(db, today)}
