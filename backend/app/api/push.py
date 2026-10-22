"""Notifications sur ce téléphone (Web Push) : la clé publique du serveur, les appareils abonnés, s'abonner,
se désabonner, envoyer une notification d'essai."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthDep, DbDep, SettingsDep, client_ip
from app.services import push

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscribeIn(BaseModel):
    subscription: dict[str, Any]
    label: str = Field(default="Cet appareil", max_length=80)


def _raise(exc: push.PushError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def get_status(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return push.status(db)


@router.post("/subscribe", status_code=201)
def subscribe(body: SubscribeIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if not request.app.state.limiters["assistant"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une minute")
    try:
        device = push.subscribe(
            db,
            raw=body.subscription,
            label=body.label,
            actor=ctx.user.username,
            resolver=request.app.state.watch_resolver,
        )
    except push.PushError as exc:
        raise _raise(exc) from exc
    return {"device": device, **push.status(db)}


@router.delete("/{device_id}")
def unsubscribe(device_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        push.unsubscribe(db, device_id, actor=ctx.user.username)
    except push.PushError as exc:
        raise _raise(exc) from exc
    return push.status(db)


@router.post("/{device_id}/test")
async def test_device(
    device_id: int, request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    if not request.app.state.limiters["assistant"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une minute")
    try:
        return await run_in_threadpool(
            push.probe_device,
            db,
            device_id,
            actor=ctx.user.username,
            public_url=settings.public_url,
            http=request.app.state.push_http,
            resolver=request.app.state.watch_resolver,
        )
    except push.PushError as exc:
        db.commit()  # un abonnement expiré est retiré même si l'essai a échoué
        raise _raise(exc) from exc
