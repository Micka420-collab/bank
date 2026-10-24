"""Ton logement : sa description (chiffrée), l'estimation par les ventes voisines (DVF), l'extraction trimestrielle
et la recherche de commune. Seuls le fichier communal et ce que tu tapes pour chercher ta commune partent."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.domain import dvf
from app.services import consents
from app.services import home_value as svc

router = APIRouter(prefix="/api/home", tags=["home"])


class HomeIn(BaseModel):
    kind: Literal["appartement", "maison"]
    surface: int = Field(ge=dvf.MIN_SURFACE, le=dvf.MAX_SURFACE, description="surface habitable en m²")
    code: str = Field(min_length=5, max_length=5, description="code INSEE de la commune (arrondissement à Paris…)")
    commune: str | None = Field(default=None, max_length=80)
    section: str | None = Field(default=None, max_length=2)
    rooms: int | None = Field(default=None, ge=dvf.MIN_ROOMS, le=dvf.MAX_ROOMS)
    in_wealth: bool = True


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, consents.ConsentError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, svc.HomeValueError):
        return HTTPException(exc.status, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _fetch(request: Request) -> svc.Fetch:
    return svc.default_fetch(http=request.app.state.dvf_http, resolver=request.app.state.dvf_resolver)


@router.get("")
def overview(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today)


@router.put("/profile")
def save(body: HomeIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.save_profile(db, actor=ctx.user.username, **body.model_dump())
    except svc.HomeValueError as exc:
        raise _fail(exc) from None
    result = None
    # Commune nouvelle ou trimestre passé : l'extraction suit, avec ton accord (mêmes bornes que le bouton).
    if consents.granted(db, svc.CONSENT) and not svc.view(db, today)["data"]["fresh"]:
        if request.app.state.limiters["home"].hit(client_ip(request)):
            result = svc.refresh(db, today=today, now=utcnow(), fetch=_fetch(request), actor=ctx.user.username)
        else:
            result = {"status": "capped", "message": "Logement enregistré ; l'extraction attend quelques minutes."}
    return {"result": result, "view": svc.view(db, today)}


@router.delete("/profile")
def remove(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    svc.remove_profile(db, actor=ctx.user.username)
    return svc.view(db, today)


@router.post("/refresh")
def refresh(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["home"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Doucement : réessaie dans quelques minutes")
    try:
        result = svc.refresh(db, today=today, now=utcnow(), fetch=_fetch(request), actor=ctx.user.username)
    except (consents.ConsentError, svc.HomeValueError) as exc:
        raise _fail(exc) from None
    if result["status"] == "capped":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, result["message"])
    return {"result": result, "view": svc.view(db, today)}


@router.get("/communes")
def communes(
    request: Request, _: AuthDep, db: DbDep, q: str = Query(min_length=2, max_length=60)
) -> list[dict[str, Any]]:
    if not request.app.state.limiters["home"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Doucement : réessaie dans quelques minutes")
    try:
        return svc.search_communes(db, q, http=request.app.state.dvf_http, resolver=request.app.state.dvf_resolver)
    except (consents.ConsentError, svc.HomeValueError) as exc:
        raise _fail(exc) from None
