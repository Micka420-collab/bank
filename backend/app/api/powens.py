"""Powens (sur contrat) : identifiants saisis dans Bank (mot de passe exigé), vérification, webview de connexion et de
reconnexion, synchronisation, coupure. Le client est reconstruit à chaud, comme pour Enable Banking."""

from __future__ import annotations

import contextlib
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, SettingsDep, TodayDep, client_ip
from app.db import utcnow
from app.models import BankConnection, ConnectionStatus
from app.providers.powens import PowensClient, PowensError
from app.security import audit
from app.security.auth import verify_password
from app.services import powens as svc
from app.services.bank_setup import SetupError

router = APIRouter(prefix="/api/banks/powens", tags=["banks"])


class CredentialsIn(BaseModel):
    password: str = Field(max_length=200, description="ton mot de passe Bank")
    domain: str = Field(min_length=1, max_length=120)
    client_id: str = Field(min_length=1, max_length=80)
    client_secret: str = Field(min_length=1, max_length=300)


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


def _limit(request: Request) -> None:
    if not request.app.state.limiters["bank_setup"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives : réessaie dans quelques minutes")


def _password(ctx: Any, db: Any, password: str) -> None:
    ok, _ = verify_password(ctx.user.password_hash, password)
    if not ok:
        audit.record(db, ctx.user.username, "bank.setup.denied", provider=svc.PROVIDER)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")


def _client(request: Request) -> PowensClient:
    client: PowensClient | None = request.app.state.powens
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Powens n'est pas configuré : saisis d'abord les identifiants du contrat",
        )
    return client


def _view(request: Request, db: Any) -> dict[str, Any]:
    st = request.app.state
    return svc.status(db, st.settings.public_url, enabled=st.powens is not None)


def _connection(db: Any, connection_id: int) -> BankConnection:
    conn: BankConnection | None = db.get(BankConnection, connection_id)
    if conn is None or conn.provider != svc.PROVIDER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connexion introuvable")
    return conn


@router.get("")
def get_status(request: Request, _: AuthDep, db: DbDep) -> dict[str, Any]:
    return _view(request, db)


@router.put("/setup")
def save(body: CredentialsIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limit(request)
    _password(ctx, db, body.password)
    try:
        domain = svc.clean_domain(body.domain)
        client_id = svc.clean_client_id(body.client_id)
        secret = svc.clean_secret(body.client_secret)
    except SetupError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    row = svc.stored(db)
    if row is not None and (row.domain != domain or row.client_id != client_id) and svc.has_live_connections(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tes banques sont reliées à l'application Powens actuelle : coupe-les avant de changer de domaine ou de "
            "client_id (changer seulement le secret garde tes connexions).",
        )
    st = request.app.state
    candidate = svc.make_client(domain, client_id, secret, http=st.powens_http, resolver=st.powens_resolver)
    try:
        user_ref = svc.enroll(candidate, row)
    except PowensError as exc:
        if svc.refusal(exc):  # un secret que Powens refuse n'est jamais gardé
            audit.record(db, ctx.user.username, "bank.setup.refused", provider=svc.PROVIDER)
            db.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, svc.BAD_KEY) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, svc.UNREACHABLE) from None
    result = svc.check(candidate, utcnow())
    svc.save(db, candidate, secret=secret, user_ref=user_ref, now=utcnow())
    svc.remember_check(db, result)
    codes = [i["code"] for i in result["issues"]]
    audit.record(db, ctx.user.username, "bank.setup.saved", provider=svc.PROVIDER, ok=result["ok"], issues=codes)
    db.commit()  # gardé avant de servir : le client en service correspond toujours à la base
    st.powens = candidate
    return _view(request, db)


@router.post("/setup/check")
def run_check(request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limit(request)
    client: PowensClient | None = request.app.state.powens
    if client is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Powens n'est pas configuré : commence par tes identifiants")
    result = svc.check(client, utcnow())
    svc.remember_check(db, result)
    audit.record(
        db,
        ctx.user.username,
        "bank.setup.check",
        provider=svc.PROVIDER,
        ok=result["ok"],
        issues=[i["code"] for i in result["issues"]],
    )
    return _view(request, db)


@router.delete("/setup")
def remove(body: PasswordIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limit(request)
    _password(ctx, db, body.password)
    client: PowensClient | None = request.app.state.powens
    if client is not None:
        # Effacement chez Powens au mieux (RGPD : utilisateur, connexions, opérations) ; localement on coupe toujours.
        with contextlib.suppress(PowensError):
            client.delete_user()
    if not svc.remove(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucune application Powens enregistrée")
    audit.record(db, ctx.user.username, "bank.setup.removed", provider=svc.PROVIDER)
    db.commit()
    request.app.state.powens = None
    return _view(request, db)


@router.post("/connect")
def connect(request: Request, _: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, str]:
    client = _client(request)
    try:
        url = svc.start_connection(db, client, settings.public_url)
    except PowensError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"redirect_url": url}


@router.post("/{connection_id}/reconnect")
def reconnect(connection_id: int, request: Request, _: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, str]:
    conn = _connection(db, connection_id)
    if conn.status not in (ConnectionStatus.ACTIVE, ConnectionStatus.EXPIRED) or not conn.session_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette connexion est coupée : ajoute la banque à nouveau")
    try:
        url = svc.start_connection(db, _client(request), settings.public_url, conn)
    except PowensError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"redirect_url": url}


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    state: str | None = Query(default=None, max_length=200),
    connection_id: str | None = Query(default=None, max_length=20),
    error: str | None = Query(default=None, max_length=200),
) -> RedirectResponse:
    """Retour de la webview Powens. Comme pour Enable Banking, pas de cookie de session ici : la preuve est le
    `state` aléatoire, à usage unique et à durée courte, émis pour une demande de l'utilisateur authentifié."""
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")

    def done(result: str) -> RedirectResponse:
        query = urlencode({"result": result, "provider": svc.PROVIDER})
        return RedirectResponse(f"{settings.public_url}/#/banques?{query}", status_code=303)

    if not state:
        return done("refused")
    if error:
        svc.fail_connection(db, state, error)
        db.commit()
        return done("refused")
    try:
        svc.complete_connection(db, _client(request), state, connection_id)
    except (svc.PowensFlowError, PowensError, HTTPException):
        db.commit()  # garde la trace de l'échec (statut, audit)
        return done("error")
    return done("connected")


@router.post("/{connection_id}/sync")
def sync(connection_id: int, request: Request, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, object]:
    conn = _connection(db, connection_id)
    report = svc.sync_connection(db, _client(request), conn, today)
    return {
        "accounts": report.accounts,
        "inserted": report.inserted,
        "duplicates": report.duplicates,
        "reconciled": report.reconciled,
        "errors": report.errors,
    }


@router.delete("/{connection_id}", status_code=204)
def revoke(connection_id: int, request: Request, ctx: AuthDep, db: DbDep) -> None:
    conn = _connection(db, connection_id)
    client: PowensClient | None = request.app.state.powens
    if conn.session_id and client is not None:
        # suppression chez Powens au mieux (identifiants bancaires et données) ; localement on coupe quoi qu'il arrive
        with contextlib.suppress(PowensError):
            client.delete_connection(conn.session_id)
    conn.session_id = None
    conn.status = ConnectionStatus.REVOKED
    audit.record(db, ctx.user.username, "bank.revoke", connection=conn.id, provider=svc.PROVIDER)
