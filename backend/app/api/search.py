"""Recherche web de l'assistant : brancher un moteur (mot de passe exigé), le tester, le retirer, voir l'état."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.security import audit
from app.security.auth import verify_password
from app.services import websearch

router = APIRouter(prefix="/api/search", tags=["assistant"])


class ConfigureIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    kind: str = Field(pattern="^(searxng|brave)$")
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=120)
    daily_cap: int = Field(default=40, ge=0, le=200)


def _raise(db: DbDep, exc: websearch.SearchServiceError) -> HTTPException:
    db.commit()
    return HTTPException(exc.status, str(exc))


@router.get("")
def get_status(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return websearch.status(db, today)


@router.put("")
def configure(body: ConfigureIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "search.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        websearch.configure(
            db,
            actor=ctx.user.username,
            kind=body.kind,
            base_url=body.base_url,
            api_key=body.api_key,
            daily_cap=body.daily_cap,
        )
    except websearch.SearchServiceError as exc:
        raise _raise(db, exc) from exc
    return websearch.status(db, today)


@router.delete("")
def remove(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    websearch.remove(db, actor=ctx.user.username)
    return websearch.status(db, today)


@router.post("/test")
async def test(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["assistant"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une minute")
    try:
        return await run_in_threadpool(
            websearch.probe,
            db,
            actor=ctx.user.username,
            today=today,
            http=request.app.state.search_http,
            resolver=request.app.state.watch_resolver,
        )
    except websearch.SearchServiceError as exc:
        raise _raise(db, exc) from exc
