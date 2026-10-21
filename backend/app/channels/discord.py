"""Discord : passerelle websocket (connexion sortante, aucune adresse publique) pour recevoir les messages privés
adressés au robot ; envoi par l'API REST. Seule l'intention DIRECT_MESSAGES est demandée : le contenu des messages
privés est lisible sans l'intention privilégiée MESSAGE_CONTENT, et les salons de serveur ne sont jamais lus.

Protocole (v10) : HELLO → IDENTIFY → battements de cœur réguliers (un battement sans accusé de réception = connexion
morte, on la ferme) ; RECONNECT / INVALID_SESSION → on rend la main au superviseur, qui se reconnecte."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect

from app.channels.base import AdapterError, Deliver, Inbound, SendError, check_ws_url, retry_after_of
from app.channels.text import to_discord

API = "https://discord.com/api/v10"
INTENT_DIRECT_MESSAGES = 1 << 12
USER_AGENT = "DiscordBot (https://github.com/Micka420-collab/bank, 1.0)"
_PERMANENT_HTTP = (400, 401, 403, 404)


class DiscordAdapter:
    platform = "discord"

    def __init__(
        self, token: str, http: httpx.AsyncClient, *, api: str = API, connector: Callable[..., Any] = connect
    ) -> None:
        self._token = token
        self._http = http
        self._api = api
        self._connect = connector
        self._me: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self._token}", "User-Agent": USER_AGENT}

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._http.request(method, f"{self._api}{path}", json=payload, headers=self._headers())
        except httpx.TimeoutException:
            raise SendError("Discord ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("Discord injoignable") from None
        if resp.status_code == 204:
            return None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.is_success:
            return data
        message = str(data.get("message", "")) if isinstance(data, dict) else ""
        retry = data.get("retry_after") if isinstance(data, dict) else None
        raise SendError(
            f"Discord {resp.status_code} : {message[:200]}" if message else f"Discord {resp.status_code}",
            permanent=resp.status_code in _PERMANENT_HTTP,
            retry_after=float(retry) if isinstance(retry, int | float) else retry_after_of(resp),
        )

    async def check(self) -> dict[str, Any]:
        try:
            me = await self._request("GET", "/users/@me")
            app = await self._request("GET", "/oauth2/applications/@me")
        except SendError as exc:
            raise AdapterError(
                "Jeton Discord refusé : copie-le depuis Bot › Reset Token" if exc.permanent else str(exc)
            ) from None
        if not isinstance(me, dict) or not me.get("bot"):
            raise AdapterError("Ce jeton n'est pas celui d'un robot Discord")
        self._me = str(me.get("id"))
        return {"name": str(me.get("username")), "app_id": str(app.get("id")) if isinstance(app, dict) else None}

    async def run(self, deliver: Deliver) -> None:
        if self._me is None:
            await self.check()
        info = await self._request("GET", "/gateway/bot")
        base = check_ws_url(str(info.get("url", "")) if isinstance(info, dict) else "")
        async with self._connect(f"{base}/?v=10&encoding=json", max_size=2**21, open_timeout=20) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if hello.get("op") != 10:
                raise AdapterError("Passerelle Discord : salut attendu")
            interval = float(hello["d"]["heartbeat_interval"]) / 1000
            state: dict[str, Any] = {"seq": None, "acked": True}
            await ws.send(
                json.dumps(
                    {
                        "op": 2,
                        "d": {
                            "token": self._token,
                            "intents": INTENT_DIRECT_MESSAGES,
                            "properties": {"os": "linux", "browser": "bank", "device": "bank"},
                        },
                    }
                )
            )
            beat = asyncio.create_task(self._heartbeat(ws, interval, state))
            try:
                async for raw in ws:
                    payload = json.loads(raw)
                    if payload.get("s") is not None:
                        state["seq"] = payload["s"]
                    op = payload.get("op")
                    if op == 11:
                        state["acked"] = True
                    elif op == 1:
                        await ws.send(json.dumps({"op": 1, "d": state["seq"]}))
                    elif op in (7, 9):
                        if op == 9:
                            await asyncio.sleep(random.uniform(1, 5))  # noqa: S311  # nosec B311
                        return  # le superviseur rouvre une session neuve
                    elif op == 0:
                        await self._dispatch(payload.get("t"), payload.get("d") or {}, deliver)
            finally:
                beat.cancel()

    async def _heartbeat(self, ws: Any, interval: float, state: dict[str, Any]) -> None:
        await asyncio.sleep(interval * random.random())  # noqa: S311  # nosec B311
        while True:
            if not state["acked"]:
                await ws.close(code=4000)  # connexion zombie : pas d'accusé depuis le dernier battement
                return
            state["acked"] = False
            await ws.send(json.dumps({"op": 1, "d": state["seq"]}))
            await asyncio.sleep(interval)

    async def _dispatch(self, event: str | None, data: dict[str, Any], deliver: Deliver) -> None:
        if event == "READY":
            user = data.get("user") or {}
            self._me = str(user.get("id")) if isinstance(user, dict) else self._me
        elif event == "MESSAGE_CREATE":
            inbound = parse_message(data, self._me)
            if inbound is not None:
                await deliver(inbound)

    async def send(self, chat_id: str, text: str) -> None:
        await self._request(
            "POST", f"/channels/{chat_id}/messages", {"content": to_discord(text), "allowed_mentions": {"parse": []}}
        )

    async def typing(self, chat_id: str) -> None:
        try:
            await self._request("POST", f"/channels/{chat_id}/typing")
        except SendError:
            return


def parse_message(data: dict[str, Any], me: str | None) -> Inbound | None:
    author = data.get("author")
    if not isinstance(author, dict) or "id" not in author or "channel_id" not in data:
        return None
    return Inbound(
        platform="discord",
        user_id=str(author["id"]),
        chat_id=str(data["channel_id"]),
        text=str(data.get("content") or ""),
        message_id=str(data.get("id") or ""),
        direct=not data.get("guild_id"),
        label=str(author.get("global_name") or author.get("username") or ""),
        from_bot=bool(author.get("bot")) or str(author["id"]) == me,
    )
