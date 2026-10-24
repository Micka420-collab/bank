"""« Demande à Bank » (OpenRouter, ta clé) : réglages, modèles, conversations (avec mémoire si tu l'as permis),
retours sur les réponses, catégories proposées."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import session_scope
from app.models import Conversation
from app.providers.openrouter import OpenRouterClient, OpenRouterError
from app.security import audit
from app.security.auth import verify_password
from app.services import assistant
from app.services import memory as memory_service

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
log = logging.getLogger("bank")

MODELS_CACHE_SECONDS = 12 * 3600
_reviewing: set[int] = set()  # une seule relecture à la fois par conversation (pas de doublons en mémoire)
_reviewing_lock = threading.Lock()


def review_in_background(app: FastAPI, conversation_id: int, actor: str, today: date) -> None:
    """Relecture qui apprend, hors requête, dans sa propre transaction. Une erreur ne remonte jamais à l'écran."""
    with _reviewing_lock:
        if conversation_id in _reviewing:
            return
        _reviewing.add(conversation_id)
    try:
        with app.state.sessionmaker() as db:
            assistant.run_review(
                db,
                conversation_id=conversation_id,
                actor=actor,
                today=today,
                make_client=app.state.llm_client_factory,
            )
            db.commit()
    except Exception:
        log.exception("relecture de la mémoire en échec")
    finally:
        with _reviewing_lock:
            _reviewing.discard(conversation_id)


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
    conversation_id: int | None = Field(default=None, ge=1)
    history: list[Turn] = Field(default_factory=list, max_length=12)  # clients sans conversation enregistrée


class FeedbackIn(BaseModel):
    value: str | None = Field(default=None, pattern="^(up|down)$")
    note: str | None = Field(default=None, max_length=300)


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
async def ask(
    body: AskIn, request: Request, background: BackgroundTasks, ctx: AuthDep, db: DbDep, today: TodayDep
) -> dict[str, Any]:
    _limited(request)
    try:
        result: dict[str, Any] = await run_in_threadpool(
            assistant.ask,
            db,
            actor=ctx.user.username,
            today=today,
            question=body.question,
            conversation_id=body.conversation_id,
            history=[t.model_dump() for t in body.history],
            make_client=_factory(request),
            search_http=request.app.state.search_http,
            fiscal=getattr(request.app.state, "fiscal_client", None),
            search_resolver=request.app.state.watch_resolver,
        )
    except assistant.AssistantError as exc:
        raise _raise(db, exc) from exc
    if result.get("review_due"):
        db.commit()  # la relecture lit la conversation dans sa propre transaction : elle doit être écrite
        background.add_task(review_in_background, request.app, result["conversation_id"], ctx.user.username, today)
    return result


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask/stream")
async def ask_stream(body: AskIn, request: Request, ctx: AuthDep, today: TodayDep) -> StreamingResponse:
    """La même question, répondue en direct (SSE) : « status » (ce que l'assistant fait), « delta » (un morceau de
    texte), « reset » (le texte montré précédait un outil), puis « done » avec la réponse complète et ses blocs,
    ou « error ». La transaction vit dans le fil de travail, le temps de la réponse."""
    _limited(request)
    app = request.app
    actor, question, conversation_id = ctx.user.username, body.question, body.conversation_id
    history = [t.model_dump() for t in body.history]
    q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def work() -> None:
        review_due_for: int | None = None
        try:
            with contextmanager(session_scope)(app.state.sessionmaker) as db:
                try:
                    result: dict[str, Any] = assistant.ask(
                        db,
                        actor=actor,
                        today=today,
                        question=question,
                        conversation_id=conversation_id,
                        history=history,
                        make_client=app.state.llm_client_factory,
                        search_http=app.state.search_http,
                        fiscal=getattr(app.state, "fiscal_client", None),
                        search_resolver=app.state.watch_resolver,
                        events=lambda event, data: q.put((event, data)),
                    )
                except assistant.AssistantError as exc:
                    q.put(("error", {"detail": str(exc), "status": exc.status}))
                    return  # la session est validée (last_error, journal) comme pour la réponse en un bloc
                if result.get("review_due"):
                    review_due_for = int(result["conversation_id"])
                q.put(("done", result))
        except Exception:  # jamais une trace à l'écran ; le journal du serveur l'a
            log.exception("réponse en direct en échec")
            q.put(("error", {"detail": "Erreur interne : réessaie dans un instant", "status": 500}))
        finally:
            q.put(None)
        if review_due_for is not None:
            review_in_background(app, review_due_for, actor, today)

    async def events() -> AsyncIterator[str]:
        worker = threading.Thread(target=work, name="assistant-stream", daemon=True)
        worker.start()
        yield _sse("status", {"text": "lit ton dossier"})
        while True:
            item = await run_in_threadpool(q.get)
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/opening")
def opening(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    """Le premier message d'une conversation : l'initiative de Bank (relier ce qui manque) et des questions prêtes."""
    return assistant.opening(db, today)


@router.get("/conversations")
def conversations(_: AuthDep, db: DbDep, limit: int = Query(default=30, ge=1, le=100)) -> list[dict[str, Any]]:
    return memory_service.conversation_list(db, limit)


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: int, _: AuthDep, db: DbDep) -> dict[str, Any]:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation introuvable")
    return memory_service.conversation_view(conv)


@router.post("/conversations/{conversation_id}/close", status_code=204)
def close(
    conversation_id: int, request: Request, background: BackgroundTasks, ctx: AuthDep, db: DbDep, today: TodayDep
) -> None:
    found, review_due = assistant.close_conversation(db, conversation_id=conversation_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation introuvable")
    if review_due:
        db.commit()
        background.add_task(review_in_background, request.app, conversation_id, ctx.user.username, today)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, ctx: AuthDep, db: DbDep) -> None:
    if not assistant.delete_conversation(db, conversation_id=conversation_id, actor=ctx.user.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation introuvable")


@router.put("/messages/{message_id}/feedback", status_code=204)
def feedback(message_id: int, body: FeedbackIn, ctx: AuthDep, db: DbDep) -> None:
    try:
        memory_service.set_feedback(db, message_id, body.value, body.note, actor=ctx.user.username)
    except memory_service.MemoryServiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


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
