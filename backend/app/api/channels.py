"""Messageries : réglages (jetons chiffrés, jamais renvoyés), code d'appairage (mot de passe exigé), comptes
reliés, préférences d'alertes — et le webhook public de WhatsApp, seul point d'entrée sans session, authentifié par
la signature de Meta."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, SettingsDep, client_ip
from app.channels.base import SendError
from app.channels.gateway import Gateway
from app.channels.whatsapp import WhatsAppAdapter
from app.db import utcnow
from app.models import ChannelLink
from app.security import audit
from app.security.auth import verify_password
from app.services import channels, consents

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ConfigIn(BaseModel):
    values: dict[str, Annotated[str, Field(max_length=1_000)]] = Field(default_factory=dict, max_length=10)
    privacy: Literal["full", "redacted", "aggregates"] | None = None
    enabled: bool | None = None


class PairIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)  # relier un compte = lui ouvrir ta situation


class LinkIn(BaseModel):
    notify: bool | None = None


class PreferencesIn(BaseModel):
    quiet_hours: list[Annotated[int, Field(ge=0, le=23)]] | None = Field(default=None, min_length=2, max_length=2)
    weekly: bool | None = None


def _gateway(request: Request) -> Gateway:
    gateway: Gateway | None = getattr(request.app.state, "channels", None)
    if gateway is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Messageries désactivées sur ce serveur")
    return gateway


def _fail(exc: channels.ChannelError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def listing(_: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    return _view(db, settings)


def _view(db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    return channels.view(
        db,
        public_url=settings.public_url,
        server_enabled=settings.channels_enabled,
        now=utcnow(),
        webhook_base=settings.channels_public_url,
    )


@router.put("/preferences")
def preferences(body: PreferencesIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        channels.set_preferences(db, quiet_hours=body.quiet_hours, weekly=body.weekly, actor=ctx.user.username)
    except channels.ChannelError as exc:
        raise _fail(exc) from None
    return {"ok": True}


@router.post("/pairing", status_code=201)
def pairing(body: PairIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "channel.pairing_denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        return channels.start_pairing(db, now=utcnow(), actor=ctx.user.username)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.delete("/pairing", status_code=204)
def cancel_pairing(ctx: AuthDep, db: DbDep) -> Response:
    channels.cancel_pairing(db, actor=ctx.user.username)
    return Response(status_code=204)


@router.patch("/links/{link_id}")
def update_link(link_id: int, body: LinkIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        link = channels.update_link(db, link_id, notify=body.notify, actor=ctx.user.username)
    except channels.ChannelError as exc:
        raise _fail(exc) from None
    return {"id": link.id, "notify": link.notify}


@router.delete("/links/{link_id}", status_code=204)
def unlink(link_id: int, ctx: AuthDep, db: DbDep) -> Response:
    try:
        channels.unlink(db, link_id, actor=ctx.user.username, now=utcnow())
    except channels.ChannelError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@router.post("/links/{link_id}/test")
async def test_link(link_id: int, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    """« Envoyer un essai » : prouve que les alertes arrivent bien sur ce compte."""
    gateway = _gateway(request)
    link = db.get(ChannelLink, link_id)
    if link is None or link.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte relié introuvable")
    adapter = gateway.adapters.get(link.platform)
    if adapter is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette messagerie n'est pas connectée en ce moment")
    platform, chat = link.platform, link.chat_ref
    audit.record(db, ctx.user.username, "channel.test_sent", platform=platform, link=link.id)
    db.commit()
    try:
        await gateway.send(
            adapter,
            chat,
            "👋 Essai réussi : les alertes de Bank arriveront ici. Écris /aide pour voir ce que je sais faire.",
            "redacted",
        )
    except SendError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    return {"ok": True}


@router.put("/{platform}")
def configure(platform: str, body: ConfigIn, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    try:
        channels.configure(
            db, platform, values=body.values, privacy=body.privacy, enabled=body.enabled, actor=ctx.user.username
        )
    except channels.ChannelError as exc:
        raise _fail(exc) from None
    return _view(db, settings)


@router.delete("/{platform}", status_code=204)
def remove(platform: str, ctx: AuthDep, db: DbDep) -> Response:
    try:
        channels.remove(db, platform, actor=ctx.user.username)
    except channels.ChannelError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@router.post("/{platform}/test")
async def test_platform(platform: str, request: Request, ctx: AuthDep) -> dict[str, Any]:
    if platform not in channels.PLATFORMS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Messagerie inconnue")
    try:
        return await _gateway(request).test(platform)
    except channels.ChannelError as exc:
        raise _fail(exc) from None


# ------------------------------------------------------------------ webhook WhatsApp (public, signé par Meta)
def _whatsapp(request: Request) -> WhatsAppAdapter:
    gateway: Gateway | None = getattr(request.app.state, "channels", None)
    adapter = gateway.adapters.get("whatsapp") if gateway is not None else None
    if not isinstance(adapter, WhatsAppAdapter):
        # Rien d'activé (ou accord retiré) : on ne dit pas pourquoi à un inconnu.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return adapter


@router.get("/whatsapp/webhook", include_in_schema=False)
def whatsapp_verify(
    request: Request,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    answer = _whatsapp(request).verify_challenge(mode, token, challenge)
    if answer is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return PlainTextResponse(answer)


@router.post("/whatsapp/webhook", include_in_schema=False)
async def whatsapp_webhook(
    request: Request, signature: Annotated[str | None, Header(alias="x-hub-signature-256")] = None
) -> dict[str, bool]:
    adapter = _whatsapp(request)
    raw = await request.body()  # borné en amont (BodyLimitMiddleware) ; vérifié avant toute lecture
    if not adapter.verify_signature(raw, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature invalide")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON invalide") from None
    gateway = _gateway(request)
    for inbound in adapter.parse_webhook(payload):
        await gateway.submit(inbound)
    return {"ok": True}  # toujours 200 une fois authentique : Meta réessaierait pendant des jours
