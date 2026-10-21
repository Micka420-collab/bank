"""Slack : Socket Mode (connexion sortante par websocket, aucune adresse publique, donc pas de secret de signature
à gérer). Chaque enveloppe est acquittée tout de suite (Slack attend la réponse en moins de 3 s), puis traitée.
Seuls les messages privés (channel_type « im ») d'humains sont retenus ; envoi par chat.postMessage, sans aperçu
de lien."""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect

from app.channels.base import AdapterError, Deliver, Inbound, SendError, check_ws_url, retry_after_of
from app.channels.text import to_slack

API = "https://slack.com/api"
_PERMANENT = {
    "invalid_auth",
    "not_authed",
    "account_inactive",
    "token_revoked",
    "token_expired",
    "missing_scope",
    "channel_not_found",
    "not_in_channel",
    "is_archived",
    "user_not_found",
    "cannot_dm_bot",
    "not_allowed_token_type",
}


class SlackAdapter:
    platform = "slack"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        http: httpx.AsyncClient,
        *,
        api: str = API,
        connector: Callable[..., Any] = connect,
    ) -> None:
        self._bot = bot_token
        self._app = app_token
        self._http = http
        self._api = api
        self._connect = connector
        self._me: str | None = None

    async def _call(self, method: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = await self._http.post(
                f"{self._api}/{method}", json=payload or {}, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TimeoutException:
            raise SendError("Slack ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("Slack injoignable") from None
        if resp.status_code == 429:
            raise SendError("Slack : trop de messages", retry_after=retry_after_of(resp) or 5.0)
        try:
            data = resp.json()
        except ValueError:
            raise SendError(f"Réponse illisible de Slack (HTTP {resp.status_code})") from None
        if not isinstance(data, dict):
            raise SendError("Réponse illisible de Slack")
        if not data.get("ok"):
            error = str(data.get("error") or "erreur inconnue")[:80]
            raise SendError(f"Slack : {error}", permanent=error in _PERMANENT)
        return data

    async def check(self) -> dict[str, Any]:
        try:
            me = await self._call("auth.test", self._bot)
            await self._call("apps.connections.open", self._app)
        except SendError as exc:
            raise AdapterError(
                f"Jetons Slack refusés ({exc}) : vérifie le jeton du robot et celui de l'application (Socket Mode)"
                if exc.permanent
                else str(exc)
            ) from None
        self._me = str(me.get("user_id") or "")
        return {"name": str(me.get("user") or "Bank"), "team": str(me.get("team") or "")}

    async def run(self, deliver: Deliver) -> None:
        if self._me is None:
            await self.check()
        opened = await self._call("apps.connections.open", self._app)
        url = check_ws_url(str(opened.get("url") or ""))
        async with self._connect(url, max_size=2**21, open_timeout=20) as ws:
            async for raw in ws:
                envelope = json.loads(raw)
                if envelope.get("envelope_id"):
                    await ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
                kind = envelope.get("type")
                if kind == "disconnect":
                    return  # Slack renouvelle la connexion : le superviseur en rouvre une
                if kind == "events_api":
                    event = (envelope.get("payload") or {}).get("event") or {}
                    inbound = parse_event(event, self._me)
                    if inbound is not None:
                        await deliver(inbound)

    async def send(self, chat_id: str, text: str) -> None:
        await self._call(
            "chat.postMessage",
            self._bot,
            {"channel": chat_id, "text": to_slack(text), "unfurl_links": False, "unfurl_media": False},
        )

    async def typing(self, chat_id: str) -> None:
        return  # pas d'indicateur de saisie pour les robots dans l'API Slack actuelle


def parse_event(event: dict[str, Any], me: str | None) -> Inbound | None:
    if not isinstance(event, dict) or event.get("type") != "message" or "user" not in event:
        return None
    from_bot = bool(
        event.get("bot_id")
        or event.get("bot_profile")
        or event.get("subtype")
        or (event.get("app_id") and not event.get("client_msg_id"))
        or (me is not None and str(event.get("user")) == me)
    )
    user = str(event["user"])
    return Inbound(
        platform="slack",
        user_id=user,
        chat_id=str(event.get("channel") or ""),
        text=html.unescape(str(event.get("text") or "")),
        message_id=str(event.get("client_msg_id") or event.get("ts") or ""),
        direct=event.get("channel_type") == "im",
        label=f"membre {user}",
        from_bot=from_bot,
    )
