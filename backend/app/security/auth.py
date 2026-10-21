"""Authentification : Argon2id, TOTP anti-rejeu, codes de secours, sessions opaques, verrouillage, rate-limit."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import AuthSession, User
from app.security.crypto import get_keyring, sha256_hex

# Paramètres OWASP 2024+ pour Argon2id (64 MiB, t=3, p=2) : ~50 ms sur un CPU de homelab.
_PH = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, salt_len=16)
_DUMMY_HASH = _PH.hash(secrets.token_urlsafe(24))

MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 256
_COMMON = {
    "motdepasse123",
    "azertyuiop123",
    "password1234",
    "123456789012",
    "qwertyuiop12",
    "soleil123456",
    "bonjour12345",
    "changeme1234",
    "administrateur",
    "motdepasse!!",
}


# ------------------------------------------------------------------ mots de passe
def password_problems(password: str, username: str) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LEN:
        problems.append(f"au moins {MIN_PASSWORD_LEN} caractères (une phrase de passe est idéale)")
    if len(password) > MAX_PASSWORD_LEN:
        problems.append(f"au plus {MAX_PASSWORD_LEN} caractères")
    lowered = password.lower()
    if username and username.lower() in lowered:
        problems.append("ne doit pas contenir l'identifiant")
    if lowered in _COMMON or len(set(password)) < 5:
        problems.append("trop prévisible")
    return problems


def hash_password(password: str) -> str:
    return _PH.hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    """Retourne (valide, à_rehasher)."""
    try:
        _PH.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    return True, _PH.check_needs_rehash(stored_hash)


def burn_verification_time(password: str) -> None:
    """Utilisateur inconnu : on paie quand même le coût Argon2 -> pas d'énumération par chronométrage."""
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _PH.verify(_DUMMY_HASH, password)


# ------------------------------------------------------------------ TOTP
TOTP_ISSUER = "Bank"


def new_totp_secret() -> str:
    return pyotp.random_base32(length=32)


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def verify_totp(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Vérifie un code TOTP (fenêtre ±30 s) et refuse tout pas déjà consommé (anti-rejeu).
    Retourne le pas temporel utilisé, à mémoriser, ou None."""
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current = int((time.time() if now is None else now) // totp.interval)
    for step in (current - 1, current, current + 1):
        if step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * totp.interval), digits):
            return step
    return None


def new_recovery_codes(count: int = 10) -> tuple[list[str], str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # sans caractères ambigus
    codes = ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(count)]
    return codes, json.dumps([sha256_hex(c) for c in codes])


def consume_recovery_code(stored_json: str, code: str) -> tuple[bool, str]:
    hashes: list[str] = json.loads(stored_json or "[]")
    candidate = sha256_hex(code.strip().lower())
    for h in hashes:
        if hmac.compare_digest(h, candidate):
            hashes.remove(h)
            return True, json.dumps(hashes)
    return False, stored_json


# ------------------------------------------------------------------ verrouillage du compte
def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def register_failure(user: User, settings: Settings, now: datetime) -> None:
    user.failed_attempts += 1
    over = user.failed_attempts - settings.login_max_attempts
    if over >= 0:
        # Back-off exponentiel : 15 min, 30 min, 1 h… plafonné à 24 h.
        minutes = min(settings.lockout_minutes * (2**over), 24 * 60)
        user.locked_until = now + timedelta(minutes=minutes)


def register_success(user: User) -> None:
    user.failed_attempts = 0
    user.locked_until = None


# ------------------------------------------------------------------ sessions
@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf: str
    expires_at: datetime


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db: Session, user: User, settings: Settings, ip: str, user_agent: str) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + timedelta(hours=settings.session_absolute_hours)
    db.add(
        AuthSession(
            id=_session_id(token),
            user_id=user.id,
            csrf_hash=sha256_hex(csrf),
            created_at=now,
            last_seen_at=now,
            expires_at=expires,
            ip_hash=get_keyring().blind_index(ip or "?", "session.ip"),
            user_agent=user_agent[:200],
        )
    )
    return IssuedSession(token=token, csrf=csrf, expires_at=expires)


def load_session(db: Session, token: str, settings: Settings) -> tuple[AuthSession, User] | None:
    if not token or len(token) > 128:
        return None
    sess = db.get(AuthSession, _session_id(token))
    if sess is None:
        return None
    now = utcnow()
    idle_limit = sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if now > sess.expires_at or now > idle_limit:
        db.delete(sess)
        return None
    user = db.get(User, sess.user_id)
    if user is None:
        return None
    # Une session ouverte avant un changement de mot de passe n'est plus valable.
    if sess.created_at < user.password_changed_at - timedelta(seconds=1):
        db.delete(sess)
        return None
    if (now - sess.last_seen_at) > timedelta(seconds=30):
        sess.last_seen_at = now
    return sess, user


def check_csrf(sess: AuthSession, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(sess.csrf_hash, sha256_hex(header_value or ""))


def revoke_session(db: Session, token: str) -> None:
    db.execute(delete(AuthSession).where(AuthSession.id == _session_id(token)))


def revoke_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


# ------------------------------------------------------------------ rate limit (process unique)
class SlidingWindowLimiter:
    """Limiteur à fenêtre glissante, thread-safe, en mémoire (une instance = un worker uvicorn)."""

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.setdefault(key, deque())
            while q and q[0] <= t - self.window:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(t)
            if len(self._events) > 10_000:  # borne mémoire face à un balayage d'IP
                self._events = {k: v for k, v in self._events.items() if v and v[-1] > t - self.window}
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
