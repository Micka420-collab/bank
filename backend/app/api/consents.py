"""Tes accords, finalité par finalité : lisibles, datés, révocables à tout moment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import AuthDep, DbDep
from app.services import consents

router = APIRouter(prefix="/api/consents", tags=["consents"])


class ConsentIn(BaseModel):
    granted: bool


@router.get("")
def listing(_: AuthDep, db: DbDep) -> list[dict[str, Any]]:
    return consents.listing(db)


@router.put("/{purpose}")
def set_consent(purpose: str, body: ConsentIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if purpose not in consents.PURPOSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Finalité inconnue")
    return consents.set_consent(db, purpose, body.granted, actor=ctx.user.username)
