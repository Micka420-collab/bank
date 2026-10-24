"""Barèmes tenus à jour : ce qui est en vigueur et d'où ça vient, les mises à jour appliquées (et le retour au
fichier), les indices relevés, la relève à la demande. Seules des pages publiques sont lues, avec ton accord."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.services import baremes as svc
from app.services import consents

router = APIRouter(prefix="/api/baremes", tags=["baremes"])


class RevertIn(BaseModel):
    path: str = Field(min_length=3, max_length=120)


@router.get("")
def overview(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return svc.view(db)


@router.post("/refresh")
async def refresh(request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    """« Vérifier maintenant » : toutes les sources actives, hors de la boucle principale, bornée en fréquence."""
    if not request.app.state.limiters["baremes"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Vérification déjà demandée : réessaie plus tard")
    fetch = svc.default_fetch(http=request.app.state.baremes_http, resolver=request.app.state.baremes_resolver)

    def run() -> dict[str, Any]:
        with request.app.state.sessionmaker() as db:
            result = svc.refresh(db, now=utcnow(), today=today, fetch=fetch, actor=ctx.user.username, force=True)
            db.commit()
            return result

    try:
        result = await asyncio.to_thread(run)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    with request.app.state.sessionmaker() as db:
        return {"result": result, "view": svc.view(db)}


@router.post("/revert")
def revert(body: RevertIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        svc.revert(db, body.path, now=utcnow(), actor=ctx.user.username)
    except svc.BaremesError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return svc.view(db)
