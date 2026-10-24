"""Assistant de connexion Enable Banking : état, saisie (mot de passe Bank exigé), vérification, retrait. Le client
de l'application est reconstruit à chaud : pas de redémarrage, la synchronisation automatique prend le nouveau."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, SettingsDep, client_ip
from app.db import utcnow
from app.providers.enablebanking import EnableBankingClient
from app.security import audit
from app.security.auth import verify_password
from app.services import bank_setup as svc

router = APIRouter(prefix="/api/banks/setup", tags=["banks"])

SERVER_MANAGED = (
    "Enable Banking est configuré par le serveur (.env) : retire BANK_ENABLEBANKING_APP_ID et le fichier de clé "
    "pour le gérer ici."
)


class CredentialsIn(BaseModel):
    password: str = Field(max_length=200, description="ton mot de passe Bank")
    app_id: str = Field(min_length=1, max_length=80)
    private_key: str = Field(min_length=1, max_length=svc.MAX_PEM + 200)


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


def _limit(request: Request) -> None:
    if not request.app.state.limiters["bank_setup"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives : réessaie dans quelques minutes")


def _password(ctx: Any, db: Any, password: str) -> None:
    ok, _ = verify_password(ctx.user.password_hash, password)
    if not ok:
        audit.record(db, ctx.user.username, "bank.setup.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")


def _build(request: Request, app_id: str, pem: bytes) -> EnableBankingClient:
    st = request.app.state
    return svc.make_client(st.settings, app_id, pem, http=st.enablebanking_http, resolver=st.enablebanking_resolver)


def _view(request: Request, db: Any) -> dict[str, Any]:
    return svc.status(db, request.app.state.settings, enabled=request.app.state.enablebanking is not None)


@router.get("")
def get_status(request: Request, _: AuthDep, db: DbDep) -> dict[str, Any]:
    return _view(request, db)


@router.put("")
def save(body: CredentialsIn, request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    _limit(request)
    _password(ctx, db, body.password)
    if settings.enablebanking_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, SERVER_MANAGED)
    try:
        app_id = svc.clean_app_id(body.app_id)
        pem = svc.clean_key(body.private_key)
    except svc.SetupError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    candidate = _build(request, app_id, pem)
    result = svc.check(candidate, settings, utcnow())
    codes = [i["code"] for i in result["issues"]]
    if "bad_key" in codes:  # une clé que l'API refuse n'est jamais gardée
        audit.record(db, ctx.user.username, "bank.setup.refused", provider=svc.PROVIDER)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result["issues"][0]["message"])
    svc.save(db, app_id=app_id, pem=pem, now=utcnow())
    svc.remember_check(db, result)
    audit.record(db, ctx.user.username, "bank.setup.saved", provider=svc.PROVIDER, ok=result["ok"], issues=codes)
    db.commit()  # gardé avant de servir : le client en service correspond toujours à la base
    request.app.state.enablebanking = candidate  # à chaud : connexions et synchronisation prennent ce client
    return _view(request, db)


@router.post("/check")
def run_check(request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    _limit(request)
    client: EnableBankingClient | None = request.app.state.enablebanking
    if client is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Enable Banking n'est pas encore configuré : commence par ta clé")
    result = svc.check(client, settings, utcnow())
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


@router.delete("")
def remove(body: PasswordIn, request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    _limit(request)
    _password(ctx, db, body.password)
    if settings.enablebanking_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, SERVER_MANAGED)
    if not svc.remove(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucune application Enable Banking enregistrée")
    audit.record(db, ctx.user.username, "bank.setup.removed", provider=svc.PROVIDER)
    db.commit()
    request.app.state.enablebanking = None  # plus aucune requête signée ; les connexions restent, en attente
    return _view(request, db)
