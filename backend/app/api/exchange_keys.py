"""Clés d'API de plateforme (Kraken, Binance, Coinbase) en lecture seule : relier (mot de passe Bank exigé, droits
vérifiés avant tout enregistrement), relever, révoquer. L'API ne rend jamais la clé ni le secret."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.models import ExchangeKey
from app.security import audit
from app.security.auth import verify_password
from app.services import consents
from app.services import crypto as crypto_service
from app.services import exchange_keys as svc

router = APIRouter(prefix="/api/crypto/exchanges", tags=["crypto"])


class ConnectIn(BaseModel):
    password: str = Field(max_length=200, description="ton mot de passe Bank")
    platform: Literal["kraken", "binance", "coinbase"]
    label: str = Field(default="", max_length=60)
    api_key: str = Field(min_length=1, max_length=svc.MAX_KEY_CHARS)
    api_secret: str = Field(min_length=1, max_length=svc.MAX_SECRET_CHARS)
    foreign: bool = True


def _raise(exc: svc.ExchangeKeyError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def list_keys(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return svc.view(db)


@router.post("", status_code=201)
def connect(body: ConnectIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "crypto.exchange.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        key = svc.connect(
            db,
            actor=ctx.user.username,
            platform=body.platform,
            label=body.label,
            api_key=body.api_key,
            api_secret=body.api_secret,
            foreign=body.foreign,
            transport=request.app.state.exchange_transport(),
            now=utcnow(),
        )
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except svc.ExchangeKeyError as exc:
        db.commit()  # le refus est journalisé même si la clé ne l'est pas
        raise _raise(exc) from None
    out = svc.view(db)
    out["created_id"] = key.id
    out["view"] = crypto_service.view(db, today=today, now=utcnow())
    return out


@router.post("/{key_id}/sync")
def sync_key(key_id: int, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["exchange_sync"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Relève toute fraîche : réessaie dans quelques minutes")
    key = db.get(ExchangeKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clé introuvable")
    try:
        consents.require(db, crypto_service.CONSENT)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    now = utcnow()
    ok = svc.sync_one(db, key, transport=request.app.state.exchange_transport(), now=now)
    crypto_service.snapshot(db, today)
    audit.record(db, ctx.user.username, "crypto.exchange.sync", synced=int(ok), failed=int(not ok), key=key_id)
    out = svc.view(db)
    out["view"] = crypto_service.view(db, today=today, now=now)
    return out


@router.delete("/{key_id}", status_code=204)
def revoke(key_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        svc.revoke(db, key_id, actor=ctx.user.username, now=utcnow())
    except svc.ExchangeKeyError as exc:
        raise _raise(exc) from None
