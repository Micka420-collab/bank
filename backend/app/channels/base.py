"""Contrat commun des adaptateurs de messagerie (inspiré du BasePlatformAdapter d'Hermes, réduit à l'essentiel)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class Inbound:
    """Un message reçu. Tout ce qui vient de la plateforme (texte, nom affiché) est une donnée non fiable."""

    platform: str
    user_id: str  # identifiant de l'expéditeur sur la plateforme
    chat_id: str  # où répondre (conversation privée)
    text: str
    message_id: str
    direct: bool = True  # message privé (les groupes sont ignorés : on ne parle d'argent qu'en tête-à-tête)
    label: str = ""  # nom affiché ou pseudo, pour te montrer qui est relié
    from_bot: bool = False


class SendError(Exception):
    """Envoi refusé par la plateforme. ``permanent`` : inutile de réessayer (jeton invalide, destinataire qui a
    bloqué le robot, fenêtre WhatsApp de 24 h fermée…) ; ``retry_after`` : délai demandé par la plateforme."""

    def __init__(self, message: str, *, permanent: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


class AdapterError(Exception):
    """Connexion impossible avec cette configuration (jeton refusé, URL injoignable) : message montrable."""


Deliver = Callable[[Inbound], Awaitable[None]]


class Adapter(Protocol):
    platform: str

    async def check(self) -> dict[str, Any]:
        """Vérifie les identifiants ; renvoie au moins {"name": …} (le nom du robot), sans secret."""
        ...

    async def run(self, deliver: Deliver) -> None:
        """Écoute les messages jusqu'à l'annulation ; lève une exception en cas de panne (le superviseur relance)."""
        ...

    async def send(self, chat_id: str, text: str) -> None:
        """Envoie un morceau déjà mis en forme et découpé ; lève SendError."""
        ...

    async def typing(self, chat_id: str) -> None:
        """Indicateur « écrit… » : au mieux, jamais bloquant."""
        ...


def check_ws_url(url: str) -> str:
    """URL de websocket fournie par une API (Discord, Slack) : wss obligatoire, ws seulement en local (tests)."""
    parts = urlsplit(url)
    if (parts.scheme == "wss" and parts.hostname) or (
        parts.scheme == "ws" and parts.hostname in ("127.0.0.1", "localhost")
    ):
        return url
    raise AdapterError("Adresse de connexion inattendue renvoyée par la plateforme")


def retry_after_of(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return min(60.0, float(raw)) if raw else None
    except ValueError:
        return None


async def backoff_sleep(attempt: int, *, base: float = 2.0, cap: float = 300.0) -> None:
    """Attente avant une nouvelle tentative : doublée à chaque échec, plafonnée, avec 20 % de gigue."""
    delay = min(cap, base * (2 ** min(attempt, 10)))
    await asyncio.sleep(delay * random.uniform(0.8, 1.2))  # noqa: S311  # nosec B311
