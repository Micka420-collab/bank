"""Passkeys : en ajouter une (mot de passe exigé), se connecter avec, les lister, en retirer une (mot de passe
exigé). Le défi est signé par Bank et vit cinq minutes ; l'origine et le domaine sont ceux de l'adresse publique."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.auth import GENERIC_FAILURE, _limit, _set_session_cookies
from app.api.deps import AuthDep, DbDep, SettingsDep, client_ip
from app.db import utcnow, write_lock
from app.models import Passkey, User
from app.security import audit, passkeys
from app.security.auth import (
    burn_verification_time,
    is_locked,
    issue_session,
    register_failure,
    register_success,
    verify_password,
)
from app.security.crypto import CryptoError, get_keyring

router = APIRouter(prefix="/api/auth/passkeys", tags=["auth"])
MAX_PASSKEYS = 10


class _UsedChallenges:
    """Un défi ne sert qu'une fois : rejouer un état signé encore valable avec une nouvelle réponse est refusé.
    En mémoire, comme le limiteur de débit (un seul worker)."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, challenge: str, ttl: float) -> bool:
        now = time.monotonic()
        with self._lock:
            for key, until in list(self._seen.items()):
                if until <= now:
                    del self._seen[key]
            if challenge in self._seen:
                return False
            self._seen[challenge] = now + ttl
            return True


used_challenges = _UsedChallenges()


class RegisterIn(BaseModel):
    state: str = Field(min_length=10, max_length=2048)
    credential: dict[str, Any]
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=1024)


class LoginIn(BaseModel):
    state: str = Field(min_length=10, max_length=2048)
    credential: dict[str, Any]


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _rp(settings: Any) -> passkeys.RelyingParty:
    try:
        return passkeys.relying_party(settings.public_url)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None


def _view(k: Passkey) -> dict[str, Any]:
    return {
        "id": k.id,
        "name": k.name,
        "created_at": k.created_at.isoformat(),
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "backed_up": k.backed_up,
        "transports": json.loads(k.transports or "[]"),
    }


@router.get("")
def listing(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    rows = db.scalars(select(Passkey).where(Passkey.user_id == ctx.user.id).order_by(Passkey.id))
    return {"passkeys": [_view(k) for k in rows], "rp_id": _rp(settings).rp_id, "max": MAX_PASSKEYS}


@router.post("/register/options")
def register_options(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    rp = _rp(settings)
    existing = list(db.scalars(select(Passkey.credential_id).where(Passkey.user_id == ctx.user.id)))
    if len(existing) >= MAX_PASSKEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{MAX_PASSKEYS} passkeys au plus")
    challenge = passkeys.new_challenge()
    options = passkeys.registration_options(
        rp, user_id=ctx.user.id, username=ctx.user.username, challenge=challenge, existing_ids=existing
    )
    state = get_keyring().sign_token(
        {"uid": ctx.user.id, "c": passkeys.bytes_to_base64url(challenge)},
        ttl_seconds=passkeys.CHALLENGE_TTL,
        purpose="webauthn-register",
    )
    return {"options": json.loads(options), "state": state}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    _limit(request, "login")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "passkey.denied", why="password")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        payload = get_keyring().verify_token(body.state, "webauthn-register")
    except CryptoError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Défi expiré : recommence") from None
    if int(payload.get("uid", -1)) != ctx.user.id or not used_challenges.claim(
        str(payload["c"]), passkeys.CHALLENGE_TTL
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Défi expiré : recommence")
    rp = _rp(settings)
    try:
        key = passkeys.verify_registration(
            rp, credential=body.credential, challenge=passkeys.base64url_to_bytes(str(payload["c"]))
        )
    except passkeys.PasskeyError as exc:
        audit.record(db, ctx.user.username, "passkey.denied", why="verification")
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    write_lock(db)
    if db.scalar(select(Passkey).where(Passkey.credential_id == key.credential_id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette passkey est déjà enregistrée")
    row = Passkey(
        user_id=ctx.user.id,
        credential_id=key.credential_id,
        public_key=key.public_key,
        sign_count=key.sign_count,
        aaguid=key.aaguid,
        transports=json.dumps(key.transports),
        backed_up=key.backed_up,
        name=" ".join(body.name.split())[:60],
    )
    db.add(row)
    db.flush()
    audit.record(db, ctx.user.username, "passkey.added", passkey=row.id, backed_up=key.backed_up)
    return {"passkey": _view(row)}


@router.post("/{passkey_id}/delete")
def remove(passkey_id: int, body: PasswordIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limit(request, "login")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "passkey.denied", why="password")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    row = db.get(Passkey, passkey_id)
    if row is None or row.user_id != ctx.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey introuvable")
    db.delete(row)
    audit.record(db, ctx.user.username, "passkey.removed", passkey=passkey_id)
    return {"ok": True}


@router.post("/login/options")
def login_options(request: Request, settings: SettingsDep) -> dict[str, Any]:
    _limit(request, "passkey")
    rp = _rp(settings)
    challenge = passkeys.new_challenge()
    options = passkeys.authentication_options(rp, challenge=challenge)
    # Le défi n'est pas lié à l'adresse IP : l'origine et le domaine signés par l'authentificateur suffisent, et un
    # téléphone change d'adresse entre le défi et la réponse plus souvent qu'on ne croit.
    state = get_keyring().sign_token(
        {"c": passkeys.bytes_to_base64url(challenge)},
        ttl_seconds=passkeys.CHALLENGE_TTL,
        purpose="webauthn-login",
    )
    return {"options": json.loads(options), "state": state}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    _limit(request, "passkey")
    try:
        payload = get_keyring().verify_token(body.state, "webauthn-login")
    except CryptoError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE) from None
    if not used_challenges.claim(str(payload.get("c", "")), passkeys.CHALLENGE_TTL):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    rp = _rp(settings)
    try:
        cred_id = passkeys.credential_id_of(body.credential)
    except passkeys.PasskeyError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE) from None
    write_lock(db)
    row = db.scalar(select(Passkey).where(Passkey.credential_id == cred_id))
    if row is None:
        burn_verification_time("passkey")
        audit.record(db, "?", "login.passkey_unknown")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    user = db.get(User, row.user_id)
    now = utcnow()
    if user is None or is_locked(user, now):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    try:
        assertion = passkeys.verify_authentication(
            rp,
            credential=body.credential,
            challenge=passkeys.base64url_to_bytes(str(payload["c"])),
            public_key=row.public_key,
            sign_count=row.sign_count,
        )
    except passkeys.PasskeyError:
        register_failure(user, settings, now)
        audit.record(db, user.username, "login.passkey_failed", attempts=user.failed_attempts)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE) from None
    expected_handle = passkeys.user_handle(user.id, user.username)
    if assertion.user_handle is not None and assertion.user_handle != expected_handle:
        register_failure(user, settings, now)
        audit.record(db, user.username, "login.passkey_failed", attempts=user.failed_attempts, why="handle")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)
    row.sign_count, row.last_used_at = assertion.new_sign_count, now
    register_success(user)
    db.flush()
    _set_session_cookies(
        response,
        issue_session(db, user, settings, client_ip(request), request.headers.get("user-agent", "")),
        settings,
    )
    audit.record(db, user.username, "login.success", mfa=True, passkey=row.id)
    return {"ok": True, "username": user.username}
