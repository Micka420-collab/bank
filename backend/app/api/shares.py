"""Partage d'un papier par lien : création (mot de passe redemandé), journal, révocation ; et la seule route du
coffre ouverte sans session — l'ouverture du lien par son destinataire."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.api.vault import FILE_HEADERS
from app.db import utcnow
from app.security import audit
from app.security.auth import verify_password
from app.services import shares
from app.services.vault import VaultError

router = APIRouter(tags=["vault"])

# Une seule réponse pour tout lien qui ne donne rien (inconnu, expiré, consommé, révoqué) : rien à en déduire.
GONE = {"detail": "Ce lien n'est plus valable"}
PUBLIC_HEADERS = {
    **FILE_HEADERS,
    "Content-Disposition": 'attachment; filename="document.pdf"',
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
}


class ShareIn(BaseModel):
    recipient: str = Field(min_length=1, max_length=200)
    hours: int = Field(ge=1, le=48)
    password: str = Field(min_length=1, max_length=1024)


def _http(exc: VaultError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.post("/api/vault/{doc_id}/shares", status_code=201)
def create(doc_id: int, body: ShareIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "vault.share_denied", document=doc_id)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    now = utcnow()
    try:
        share, token = shares.create(
            db, doc_id, recipient=body.recipient, hours=body.hours, actor=ctx.user.username, now=now, today=today
        )
    except VaultError as exc:
        raise _http(exc) from exc
    # Le jeton voyage dans le fragment (#…) : jamais envoyé au serveur, ni dans les journaux, ni dans un Referer.
    link = f"{request.app.state.settings.public_url}/#/recu/{token}"
    return {"share": shares.summary(share, now), "link": link}


@router.get("/api/vault/{doc_id}/shares")
def journal(doc_id: int, _: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return {"shares": shares.journal(db, doc_id, now=utcnow())}
    except VaultError as exc:
        raise _http(exc) from exc


@router.post("/api/vault/{doc_id}/shares/{share_id}/revoke")
def revoke(doc_id: int, share_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return shares.revoke(db, doc_id, share_id, actor=ctx.user.username, now=utcnow())
    except VaultError as exc:
        raise _http(exc) from exc


async def _token(request: Request) -> str:
    # Corps lu à la main : un corps illisible, un jeton trop long ou qui n'est pas du texte reçoit le même 404 que
    # les autres, pas le 422 détaillé de la validation.
    try:
        body = json.loads(await request.body())
    except (ValueError, RecursionError):
        return ""
    token = body.get("token") if isinstance(body, dict) else None
    return token if isinstance(token, str) else ""


@router.post("/api/share/open")
def open_link(request: Request, db: DbDep, token: Annotated[str, Depends(_token)]) -> Response:
    """Sans session : le jeton est la seule clé. Débit limité par adresse, réponse identique pour tout refus."""
    if not request.app.state.limiters["share"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives : réessayez dans quelques minutes")
    data = shares.open_link(db, token, now=utcnow(), ip=client_ip(request))
    if data is None:
        return JSONResponse(GONE, status_code=404, headers={"X-Robots-Tag": "noindex, nofollow, noarchive"})
    return Response(data, media_type="application/pdf", headers=PUBLIC_HEADERS)
