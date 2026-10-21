"""ntfy : des alertes sur ton téléphone, sans compte ni messagerie (serveur public ntfy.sh ou le tien). Envoi
seulement — on ne peut pas répondre. Le sujet est généré par Bank, impossible à deviner : sur un serveur public,
qui connaît le sujet peut lire les alertes ; un jeton d'accès protège un serveur privé."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.channels.base import AdapterError, Deliver, SendError, retry_after_of
from app.channels.text import to_plain


class NtfyAdapter:
    platform = "ntfy"

    def __init__(self, server: str, token: str | None, http: httpx.AsyncClient, *, click: str | None = None) -> None:
        self._server = server.rstrip("/")
        self._token = token
        self._http = http
        self._click = click

    def _headers(self) -> dict[str, str]:
        headers = {"Title": "Bank", "Tags": "moneybag"}
        if self._click:
            headers["Click"] = self._click
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def check(self) -> dict[str, Any]:
        try:
            resp = await self._http.get(f"{self._server}/v1/health", timeout=10.0)
        except httpx.HTTPError:
            raise AdapterError("Serveur ntfy injoignable") from None
        if resp.status_code != 200:
            raise AdapterError(f"Serveur ntfy : réponse {resp.status_code}")
        return {"name": self._server.split("//", 1)[-1]}

    async def run(self, deliver: Deliver) -> None:
        await asyncio.Event().wait()  # rien à écouter

    async def send(self, chat_id: str, text: str) -> None:
        try:
            resp = await self._http.post(
                f"{self._server}/{chat_id}", content=to_plain(text).encode(), headers=self._headers(), timeout=20.0
            )
        except httpx.TimeoutException:
            raise SendError("ntfy ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("Serveur ntfy injoignable") from None
        if resp.status_code == 429:
            raise SendError("ntfy : trop d'alertes", retry_after=retry_after_of(resp) or 10.0)
        if not resp.is_success:
            raise SendError(f"ntfy {resp.status_code}", permanent=resp.status_code in (400, 401, 403, 404, 413))

    async def typing(self, chat_id: str) -> None:
        return
