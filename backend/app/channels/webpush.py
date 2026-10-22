"""Adaptateur Web Push pour la passerelle des alertes : envoi seulement (rien n'entre), par appareil abonné.
Le chiffrement et la signature VAPID sont dans app.security.webpush ; ici, on branche sur le circuit commun
(planification, heures calmes, dédoublonnage, échecs)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.channels.base import Deliver, SendError
from app.channels.text import to_plain

Sender = Callable[[str, str], None]  # (chat_ref, texte) -> envoie ou lève


class WebPushAdapter:
    platform = "webpush"

    def __init__(self, sender: Sender, *, label: str = "ce téléphone") -> None:
        self._sender = sender
        self._label = label

    async def check(self) -> dict[str, Any]:
        return {"name": self._label}

    async def run(self, deliver: Deliver) -> None:
        await asyncio.Event().wait()  # rien à écouter : un téléphone ne répond pas ici

    async def send(self, chat_id: str, text: str) -> None:
        try:
            await asyncio.to_thread(self._sender, chat_id, to_plain(text))
        except SendError:
            raise
        except Exception as exc:  # une erreur du service devient un échec d'envoi, jamais une trace
            status = getattr(exc, "status", 0)
            raise SendError(str(exc)[:200], permanent=status in (404, 410, 413)) from None

    async def typing(self, chat_id: str) -> None:
        return
