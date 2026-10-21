"""Mise en route, connexion (mot de passe + TOTP), déconnexion, gestion du second facteur."""

from __future__ import annotations

import hmac
import io
import json

import segno
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF_COOKIE, AuthDep, DbDep, SettingsDep, client_ip, session_cookie_name
from app.config import Settings
from app.db import utcnow
from app.models import User
from app.security import audit
from app.security.auth import (
    IssuedSession,
    burn_verification_time,
    consume_recovery_code,
    hash_password,
    is_locked,
    issue_session,
    new_recovery_codes,
    new_totp_secret,
    password_problems,
    register_failure,
    register_success,
    revoke_all_sessions,
    revoke_session,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.security.crypto import CryptoError, get_keyring

router = APIRouter(prefix="/api", tags=["auth"])
GENERIC_FAILURE = "Identifiant, mot de passe ou code incorrect"


class SetupIn(BaseModel):
    setup_token: str = Field(min_length=8, max_length=200)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=1, max_length=256)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MfaIn(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=600)
    code: str = Field(min_length=6, max_length=20)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _limit(request: Request, bucket: str) -> None:
    limiter = request.app.state.limiters[bucket]
    if not limiter.hit(client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Trop de tentatives, réessaie dans quelques minutes",
            headers={"Retry-After": "300"},
        )


def _set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> None:
    max_age = settings.session_absolute_hours * 3600
    response.set_cookie(
        session_cookie_name(settings),
        issued.token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    # Lisible par le front (double-submit) — inutile à un attaquant sans la session HttpOnly.
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_cookies(response: Response, settings: Settings) -> None:
    for name in (session_cookie_name(settings), CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.cookie_secure, samesite="strict")


@router.get("/setup/status")
def setup_status(db: DbDep) -> dict[str, bool]:
    return {"needs_setup": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup", status_code=201)
def setup(body: SetupIn, request: Request, db: DbDep, settings: SettingsDep) -> dict[str, str]:
    _limit(request, "login")
    expected = settings.setup_token_value
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "BANK_SETUP_TOKEN non configuré")
    if (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Instance déjà initialisée")
    if not hmac.compare_digest(expected, body.setup_token):
        audit.record(db, "anonymous", "setup.denied")
        db.commit()  # l'échec doit être journalisé même si la requête échoue
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Jeton d'installation invalide")
    problems = password_problems(body.password, body.username)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mot de passe : " + "; ".join(problems))
    db.add(User(username=body.username, password_hash=hash_password(body.password)))
    audit.record(db, body.username, "setup.done")
    return {"status": "ok"}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> dict[str, object]:
    _limit(request, "login")
    user = db.scalar(select(User).where(User.username == body.username))
    now = utcnow()
    if user is None:
        burn_verification_time(body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    if is_locked(user, now):
        burn_verification_time(body.password)
        audit.record(db, user.username, "login.locked")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    ok, rehash = verify_password(user.password_hash, body.password)
    if not ok:
        register_failure(user, settings, now)
        audit.record(db, user.username, "login.failed", attempts=user.failed_attempts)
        db.commit()  # le compteur d'échecs DOIT survivre au rollback de la requête
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    if rehash:
        user.password_hash = hash_password(body.password)
    if user.totp_enabled:
        token = get_keyring().sign_token({"uid": user.id}, ttl_seconds=300, purpose="mfa")
        return {"mfa_required": True, "mfa_token": token}
    register_success(user)
    db.flush()
    _set_session_cookies(
        response,
        issue_session(db, user, settings, client_ip(request), request.headers.get("user-agent", "")),
        settings,
    )
    audit.record(db, user.username, "login.success", mfa=False)
    return {"mfa_required": False}


@router.post("/auth/mfa")
def login_mfa(body: MfaIn, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> dict[str, bool]:
    _limit(request, "mfa")
    try:
        payload = get_keyring().verify_token(body.mfa_token, "mfa")
    except CryptoError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE) from exc
    user = db.get(User, int(payload["uid"]))
    now = utcnow()
    if user is None or not user.totp_enabled or not user.totp_secret or is_locked(user, now):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    step = verify_totp(user.totp_secret, body.code, user.totp_last_step)
    used_recovery = False
    if step is not None:
        user.totp_last_step = step
    else:
        used_recovery, user.recovery_codes = consume_recovery_code(user.recovery_codes, body.code)
        if not used_recovery:
            register_failure(user, settings, now)
            audit.record(db, user.username, "login.mfa_failed", attempts=user.failed_attempts)
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    register_success(user)
    _set_session_cookies(
        response,
        issue_session(db, user, settings, client_ip(request), request.headers.get("user-agent", "")),
        settings,
    )
    audit.record(db, user.username, "login.success", mfa=True, recovery_code=used_recovery)
    return {"ok": True}


@router.post("/auth/logout")
def logout(ctx: AuthDep, response: Response, db: DbDep, settings: SettingsDep) -> dict[str, bool]:
    revoke_session(db, ctx.token)
    _clear_cookies(response, settings)
    audit.record(db, ctx.user.username, "logout")
    return {"ok": True}


@router.get("/auth/me")
def me(ctx: AuthDep) -> dict[str, object]:
    return {
        "username": ctx.user.username,
        "totp_enabled": ctx.user.totp_enabled,
        "recovery_codes_left": len(json.loads(ctx.user.recovery_codes or "[]")),
        "session_expires_at": ctx.session.expires_at.isoformat(),
    }


@router.post("/auth/totp/setup")
def totp_setup(ctx: AuthDep, db: DbDep) -> dict[str, str]:
    if ctx.user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Double authentification déjà active")
    secret = new_totp_secret()
    ctx.user.totp_secret = secret
    uri = totp_uri(secret, ctx.user.username)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#0f172a", light="#ffffff")
    return {"otpauth_uri": uri, "qr_svg": buf.getvalue().decode("utf-8"), "secret": secret}


@router.post("/auth/totp/enable")
def totp_enable(body: CodeIn, ctx: AuthDep, db: DbDep) -> dict[str, object]:
    user = ctx.user
    if user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lance d'abord la configuration")
    step = verify_totp(user.totp_secret, body.code, user.totp_last_step)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code incorrect — vérifie l'heure de ton téléphone")
    codes, hashes = new_recovery_codes()
    user.totp_enabled, user.totp_last_step, user.recovery_codes = True, step, hashes
    audit.record(db, user.username, "mfa.enabled")
    return {"recovery_codes": codes}


@router.post("/auth/password")
def change_password(
    body: PasswordChangeIn,
    request: Request,
    response: Response,
    ctx: AuthDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, bool]:
    _limit(request, "login")
    ok, _ = verify_password(ctx.user.password_hash, body.current_password)
    if not ok:
        audit.record(db, ctx.user.username, "password.change_denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe actuel incorrect")
    problems = password_problems(body.new_password, ctx.user.username)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mot de passe : " + "; ".join(problems))
    ctx.user.password_hash = hash_password(body.new_password)
    ctx.user.password_changed_at = utcnow()
    revoke_all_sessions(db, ctx.user.id)
    db.flush()
    _set_session_cookies(
        response,
        issue_session(db, ctx.user, settings, client_ip(request), request.headers.get("user-agent", "")),
        settings,
    )
    audit.record(db, ctx.user.username, "password.changed")
    return {"ok": True}
