"""Connexion des banques via DSP2 (Enable Banking)."""

from __future__ import annotations

import contextlib
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, SettingsDep, TodayDep, client_ip
from app.models import BankConnection, ConnectionStatus
from app.providers.enablebanking import EnableBankingClient, ProviderError
from app.security import audit
from app.services.bank_sync import BankFlowError, complete_connection, start_connection, sync_connection

router = APIRouter(prefix="/api/banks", tags=["banks"])


class ConnectIn(BaseModel):
    aspsp_name: str = Field(min_length=1, max_length=120)
    country: str = Field(default="FR", pattern=r"^[A-Z]{2}$")


class ConnectionOut(BaseModel):
    id: int
    aspsp_name: str
    aspsp_country: str
    status: str
    valid_until: datetime | None
    last_sync_at: datetime | None
    last_error: str | None
    accounts: list[str]


def _client(request: Request) -> EnableBankingClient:
    client: EnableBankingClient | None = request.app.state.enablebanking
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Enable Banking non configuré (BANK_ENABLEBANKING_APP_ID / _KEY_FILE)",
        )
    return client


@router.get("")
def list_connections(request: Request, _: AuthDep, db: DbDep) -> dict[str, object]:
    conns = db.scalars(
        select(BankConnection).where(BankConnection.status != ConnectionStatus.PENDING).order_by(BankConnection.id)
    )
    return {
        "enabled": request.app.state.enablebanking is not None,
        "connections": [
            ConnectionOut(
                id=c.id,
                aspsp_name=c.aspsp_name,
                aspsp_country=c.aspsp_country,
                status=c.status,
                valid_until=c.valid_until,
                last_sync_at=c.last_sync_at,
                last_error=c.last_error,
                accounts=[a.name for a in c.accounts],
            )
            for c in conns
        ],
    }


@router.get("/aspsps")
def aspsps(
    request: Request, _: AuthDep, country: str = Query(default="FR", pattern=r"^[A-Z]{2}$")
) -> list[dict[str, object]]:
    try:
        return _client(request).list_aspsps(country)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/connect")
def connect(body: ConnectIn, request: Request, _: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, str]:
    client = _client(request)
    try:
        url = start_connection(db, client, body.aspsp_name, body.country, f"{settings.public_url}/api/banks/callback")
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"redirect_url": url}


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    code: str | None = Query(default=None, max_length=2048),
    state: str | None = Query(default=None, max_length=200),
    error: str | None = Query(default=None, max_length=200),
) -> RedirectResponse:
    """Retour de la banque. Pas de cookie de session ici (SameSite=Strict ne suit pas une navigation
    venue du site de la banque) : la preuve, c'est le `state` aléatoire à usage unique et à durée courte,
    émis pour une demande faite par l'utilisateur authentifié."""
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")

    def done(result: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.public_url}/#/banques?{urlencode({'result': result})}", status_code=303)

    if error or not code or not state:
        return done("refused")
    try:
        complete_connection(db, _client(request), code, state)
    except (BankFlowError, ProviderError):
        db.commit()  # garde la trace de l'échec (statut, audit)
        return done("error")
    return done("connected")


@router.post("/{connection_id}/sync")
def sync(connection_id: int, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, object]:
    conn = db.get(BankConnection, connection_id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connexion introuvable")
    report = sync_connection(db, _client(request), conn, today)
    return {
        "accounts": report.accounts,
        "inserted": report.inserted,
        "duplicates": report.duplicates,
        "reconciled": report.reconciled,
        "errors": report.errors,
    }


@router.delete("/{connection_id}", status_code=204)
def revoke(connection_id: int, request: Request, ctx: AuthDep, db: DbDep) -> None:
    conn = db.get(BankConnection, connection_id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connexion introuvable")
    if conn.session_id and request.app.state.enablebanking is not None:
        # révocation côté banque au mieux ; localement on coupe quoi qu'il arrive
        with contextlib.suppress(ProviderError):
            _client(request).delete_session(conn.session_id)
    conn.session_id = None
    conn.status = ConnectionStatus.REVOKED
    audit.record(db, ctx.user.username, "bank.revoke", connection=conn.id)
