"""« Demande à Bank » (OpenRouter, ta clé) : réglages, liste des modèles, question, catégories proposées."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.providers.openrouter import OpenRouterClient, OpenRouterError
from app.security import audit
from app.security.auth import verify_password
from app.services import assistant

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

MODELS_CACHE_SECONDS = 12 * 3600


class ConfigureIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)  # une clé ouvre tes crédits et ton dossier : confirmation
    api_key: str = Field(min_length=10, max_length=300)
    model: str = Field(min_length=3, max_length=120)
    privacy: str = Field(default="redacted", pattern="^(full|redacted|aggregates)$")
    monthly_cap_usd_cents: int = Field(default=500, ge=0, le=100_000)
    daily_calls_cap: int = Field(default=60, ge=0, le=500)


class SettingsIn(BaseModel):
    model: str | None = Field(default=None, min_length=3, max_length=120)
    privacy: str | None = Field(default=None, pattern="^(full|redacted|aggregates)$")
    monthly_cap_usd_cents: int | None = Field(default=None, ge=0, le=100_000)
    daily_calls_cap: int | None = Field(default=None, ge=0, le=500)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4_000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    history: list[Turn] = Field(default_factory=list, max_length=12)


def _raise(db: DbDep, exc: assistant.AssistantError) -> HTTPException:
    # Une erreur du fournisseur est mémorisée (last_error) et journalisée : on la garde malgré la réponse 4xx/5xx.
    db.commit()
    return HTTPException(exc.status, str(exc))


def _factory(request: Request) -> assistant.ClientFactory:
    factory: assistant.ClientFactory = request.app.state.llm_client_factory
    return factory


def _limited(request: Request) -> None:
    if not request.app.state.limiters["assistant"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une minute")


@router.get("")
def get_status(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return assistant.status(db, today)


@router.put("")
def configure(body: ConfigureIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "assistant.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        assistant.configure(
            db,
            actor=ctx.user.username,
            api_key=body.api_key,
            model=body.model,
            privacy=body.privacy,
            monthly_cap_usd_cents=body.monthly_cap_usd_cents,
            daily_calls_cap=body.daily_calls_cap,
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc
    return assistant.status(db, today)


@router.patch("")
def update(body: SettingsIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        assistant.update_settings(
            db,
            actor=ctx.user.username,
            model=body.model,
            privacy=body.privacy,
            monthly_cap_usd_cents=body.monthly_cap_usd_cents,
            daily_calls_cap=body.daily_calls_cap,
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc
    return assistant.status(db, today)


@router.delete("", status_code=204)
def remove(ctx: AuthDep, db: DbDep) -> None:
    assistant.remove(db, actor=ctx.user.username)


@router.get("/models")
async def models(request: Request, _: AuthDep) -> list[dict[str, Any]]:
    """Modèles texte d'OpenRouter avec leur prix (public, sans clé) ; liste gardée 12 h en mémoire."""
    cache = getattr(request.app.state, "llm_models_cache", None)
    now = time.monotonic()
    if cache and now - cache[0] < MODELS_CACHE_SECONDS:
        rows: list[dict[str, Any]] = cache[1]
        return rows
    client: OpenRouterClient = request.app.state.llm_client_factory(None)
    try:
        infos = await run_in_threadpool(client.list_models)
    except OpenRouterError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    rows = [
        {
            "id": m.id,
            "name": m.name,
            "context_length": m.context_length,
            "prompt_usd_per_million": m.prompt_usd_per_million,
            "completion_usd_per_million": m.completion_usd_per_million,
            "free": m.free,
        }
        for m in infos
    ]
    request.app.state.llm_models_cache = (now, rows)
    return rows


@router.post("/test")
async def test(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    _limited(request)
    try:
        return await run_in_threadpool(
            assistant.check_connection, db, actor=ctx.user.username, today=today, make_client=_factory(request)
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc


@router.post("/ask")
async def ask(body: AskIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    _limited(request)
    try:
        return await run_in_threadpool(
            assistant.ask,
            db,
            actor=ctx.user.username,
            today=today,
            question=body.question,
            history=[t.model_dump() for t in body.history],
            make_client=_factory(request),
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc


@router.get("/hints")
def get_hints(_: AuthDep, db: DbDep) -> dict[str, str]:
    return assistant.hints(db)


@router.post("/hints")
async def suggest_hints(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    _limited(request)
    try:
        return await run_in_threadpool(
            assistant.suggest_hints, db, actor=ctx.user.username, today=today, make_client=_factory(request)
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc


@router.delete("/hints/{merchant}", status_code=204)
def forget_hint(merchant: str, _: AuthDep, db: DbDep) -> None:
    assistant.forget_hint(db, merchant[:120])
