"""Dépendances FastAPI : base, utilisateur courant, CSRF, origine, limiteurs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import session_scope
from app.models import AuthSession, User
from app.security.auth import check_csrf, load_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def session_cookie_name(settings: Settings) -> str:
    # Préfixe __Host- : le navigateur impose Secure, Path=/ et pas de Domain (rien ne fuit aux sous-domaines).
    return "__Host-bank_session" if settings.cookie_secure else "bank_session"


CSRF_COOKIE = "bank_csrf"
CSRF_HEADER = "x-csrf-token"


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.sessionmaker)


def today() -> date:
    return date.today()  # noqa: DTZ011 - date locale du serveur = date « bancaire » de l'utilisateur


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TodayDep = Annotated[date, Depends(today)]


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession
    token: str


def _same_origin(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # requêtes non-navigateur ; la protection CSRF repose alors sur le jeton
    expected = urlsplit(settings.public_url)
    got = urlsplit(origin)
    return (got.scheme, got.netloc) == (expected.scheme, expected.netloc)


def require_user(request: Request, db: DbDep, settings: SettingsDep) -> AuthContext:
    token = request.cookies.get(session_cookie_name(settings), "")
    loaded = load_session(db, token, settings)
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session absente ou expirée")
    sess, user = loaded
    if request.method not in SAFE_METHODS:
        if not _same_origin(request, settings):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Origine refusée")
        if not check_csrf(sess, request.headers.get(CSRF_HEADER)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Jeton CSRF invalide")
    return AuthContext(user=user, session=sess, token=token)


AuthDep = Annotated[AuthContext, Depends(require_user)]


def client_ip(request: Request) -> str:
    # Derrière le reverse proxy (nginx du compose), uvicorn est lancé avec --proxy-headers et
    # --forwarded-allow-ips limité au proxy : request.client reflète alors la vraie IP.
    return request.client.host if request.client else "?"
