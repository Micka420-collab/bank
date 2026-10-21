"""« Ce que Bank retient » : les deux carnets, les propositions de la relecture, le nom de l'assistant, la durée
de conservation des conversations, et l'oubli complet (protégé par ton mot de passe)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, client_ip
from app.domain import memory as memory_domain
from app.security import audit
from app.security.auth import verify_password
from app.services import consents, memory
from app.services.assistant import provider

router = APIRouter(prefix="/api/memory", tags=["memory"])


class EntryIn(BaseModel):
    store: str = Field(pattern="^(user|notes)$")
    content: str = Field(min_length=1, max_length=memory_domain.MAX_ENTRY_CHARS)


class EntryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=memory_domain.MAX_ENTRY_CHARS)
    pinned: bool | None = None


class DecisionIn(BaseModel):
    approve: bool


class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    retention_days: int | None = Field(default=None, ge=0, le=3650)


class ForgetIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _http(exc: memory.MemoryServiceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _view(db: DbDep) -> dict[str, Any]:
    row = provider(db)
    return {
        **memory.view(db),
        "consent": consents.granted(db, "ai_memory"),
        "available": row is None or row.privacy != "aggregates",
        "titles": memory_domain.TITLES,
    }


@router.get("")
def get_memory(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return _view(db)


@router.post("/entries", status_code=201)
def add_entry(body: EntryIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        memory.user_add(db, body.store, body.content, actor=ctx.user.username)
    except memory.MemoryServiceError as exc:
        raise _http(exc) from exc
    return _view(db)


@router.patch("/entries/{entry_id}")
def edit_entry(entry_id: int, body: EntryPatch, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        memory.user_update(db, entry_id, actor=ctx.user.username, content=body.content, pinned=body.pinned)
    except memory.MemoryServiceError as exc:
        raise _http(exc) from exc
    return _view(db)


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        memory.user_delete(db, entry_id, actor=ctx.user.username)
    except memory.MemoryServiceError as exc:
        raise _http(exc) from exc
    return _view(db)


@router.post("/proposals/{proposal_id}")
def decide(proposal_id: int, body: DecisionIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        memory.decide(db, proposal_id, body.approve, actor=ctx.user.username)
    except memory.MemoryServiceError as exc:
        if exc.status == status.HTTP_409_CONFLICT:
            db.commit()  # la proposition périmée est bien écartée, même si on répond par une erreur
        raise _http(exc) from exc
    return _view(db)


@router.put("/settings")
def settings(body: SettingsIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        memory.update_settings(db, actor=ctx.user.username, name=body.name, retention_days=body.retention_days)
    except memory.MemoryServiceError as exc:
        raise _http(exc) from exc
    return _view(db)


@router.post("/forget")
def forget(body: ForgetIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    """Tout oublier : carnets, propositions et conversations. Irréversible, donc mot de passe."""
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "memory.forget_denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    counts = memory.forget_all(db, actor=ctx.user.username)
    return {**_view(db), "forgotten": counts}
