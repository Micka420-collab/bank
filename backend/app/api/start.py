"""Démarrer avec Bank : le parcours de première ouverture et la liste « Bien démarrer » (DESIGN.md § 3.10).

`/api/onboarding` est déjà le questionnaire de Ma situation : le parcours vit sous `/api/start`. Session humaine
seulement (cookie + CSRF), jamais exposé aux jetons d'agent ni au serveur MCP.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep
from app.services import start

router = APIRouter(prefix="/api/start", tags=["start"])


class StepIn(BaseModel):
    action: Literal["skip", "resume", "current"]


class FinishIn(BaseModel):
    tour_done: bool = False


class ChecklistIn(BaseModel):
    action: Literal["dismiss", "restore"]


class PublicConsentsIn(BaseModel):
    purposes: list[str] = Field(min_length=1, max_length=12)


def _refuse(exc: start.StartError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("")
def overview(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return start.view(db)


@router.post("/steps/{key}")
def step(key: str, body: StepIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if key not in start.STEP_KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Étape inconnue")
    try:
        return start.step_action(db, key, body.action, actor=ctx.user.username)
    except start.StartError as exc:
        raise _refuse(exc) from exc


@router.post("/finish")
def finish(ctx: AuthDep, db: DbDep, body: FinishIn | None = None) -> dict[str, Any]:
    return start.finish(db, tour_done=bool(body and body.tour_done), actor=ctx.user.username)


@router.post("/checklist")
def checklist(body: ChecklistIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    return start.checklist_action(db, body.action, actor=ctx.user.username)


@router.post("/public-consents")
def public_consents(body: PublicConsentsIn, ctx: AuthDep, db: DbDep) -> list[dict[str, Any]]:
    try:
        return start.grant_public(db, body.purposes, actor=ctx.user.username)
    except start.StartError as exc:
        raise _refuse(exc) from exc
