"""Telegram : écoute par long polling (getUpdates) — aucune adresse publique à exposer, idéal sur un serveur de
maison. Envoi en HTML (on n'échappe que & < >), repli en texte brut si Telegram refuse la mise en forme.

Le jeton du robot fait partie de l'URL de l'API Telegram : il n'apparaît dans aucun message d'erreur (on ne
recopie jamais l'exception httpx) et le journal de httpx est réduit au silence (voir app/channels/gateway.py)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.channels.base import AdapterError, Deliver, Inbound, SendError
from app.channels.text import to_plain, to_telegram_html

API = "https://api.telegram.org"
POLL_SECONDS = 50


class TelegramAdapter:
    platform = "telegram"

    def __init__(
        self,
        token: str,
        http: httpx.AsyncClient,
        *,
        cursor: int | None = None,
        on_cursor: Callable[[int], Awaitable[None]] | None = None,
        api: str = API,
    ) -> None:
        self._token = token
        self._http = http
        self._api = api
        self.cursor = cursor
        self._on_cursor = on_cursor

    async def _call(self, method: str, payload: dict[str, Any], *, timeout: float = 20.0) -> Any:
        try:
            resp = await self._http.post(f"{self._api}/bot{self._token}/{method}", json=payload, timeout=timeout)
        except httpx.TimeoutException:
            raise SendError("Telegram ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("Telegram injoignable") from None
        try:
            data = resp.json()
        except ValueError:
            raise SendError(f"Réponse illisible de Telegram (HTTP {resp.status_code})") from None
        if not isinstance(data, dict):
            data = {}
        if data.get("ok"):
            return data.get("result")
        raw_code = data.get("error_code")
        code = raw_code if isinstance(raw_code, int) else resp.status_code
        desc = str(data.get("description") or "")[:200]
        params = data.get("parameters")
        retry = params.get("retry_after") if isinstance(params, dict) else None
        raise SendError(
            f"Telegram {code} : {desc}" if desc else f"Telegram {code}",
            permanent=code in (401, 403, 404) or (code == 400 and "parse" not in desc.lower()),
            retry_after=float(retry) if isinstance(retry, int | float) else None,
        )

    async def check(self) -> dict[str, Any]:
        try:
            me = await self._call("getMe", {})
        except SendError as exc:
            raise AdapterError(
                "Jeton Telegram refusé : vérifie-le auprès de @BotFather" if exc.permanent else str(exc)
            ) from None
        if not isinstance(me, dict) or not me.get("is_bot"):
            raise AdapterError("Ce jeton n'est pas celui d'un robot Telegram")
        return {"name": f"@{me.get('username')}", "username": me.get("username")}

    async def run(self, deliver: Deliver) -> None:
        # Un webhook laissé par un autre usage du robot empêcherait le long polling (erreur 409).
        try:
            await self._call("deleteWebhook", {"drop_pending_updates": False})
        except SendError as exc:
            if exc.permanent:
                raise AdapterError("Jeton Telegram refusé") from None
        failures = 0
        while True:
            payload: dict[str, Any] = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
            if self.cursor is not None:
                payload["offset"] = self.cursor
            try:
                updates = await self._call("getUpdates", payload, timeout=POLL_SECONDS + 15)
            except SendError as exc:
                if exc.permanent or "409" in str(exc):
                    raise AdapterError(f"Écoute Telegram impossible : {exc}") from None
                failures += 1
                if failures >= 5:
                    raise AdapterError(str(exc)) from None
                await asyncio.sleep(min(30.0, exc.retry_after or 2.0 * failures))
                continue
            failures = 0
            for update in updates or []:
                if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                    continue
                self.cursor = int(update["update_id"]) + 1
                inbound = parse_update(update)
                if inbound is not None:
                    await deliver(inbound)
            if updates and self._on_cursor is not None and self.cursor is not None:
                await self._on_cursor(self.cursor)

    async def send(self, chat_id: str, text: str) -> None:
        base = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
        try:
            await self._call("sendMessage", {**base, "text": to_telegram_html(text), "parse_mode": "HTML"})
        except SendError as exc:
            if "parse" not in str(exc).lower() and "entit" not in str(exc).lower():
                raise
            await self._call("sendMessage", {**base, "text": to_plain(text)})

    async def typing(self, chat_id: str) -> None:
        try:
            await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=5.0)
        except SendError:
            return


def parse_update(update: dict[str, Any]) -> Inbound | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    if not isinstance(chat, dict) or not isinstance(sender, dict) or "id" not in sender or "id" not in chat:
        return None
    username = sender.get("username")
    label = f"@{username}" if username else " ".join(str(sender.get(k) or "") for k in ("first_name", "last_name"))
    return Inbound(
        platform="telegram",
        user_id=str(sender["id"]),
        chat_id=str(chat["id"]),
        text=str(msg.get("text") or ""),
        message_id=f"{chat['id']}:{msg.get('message_id')}",
        direct=chat.get("type") == "private",
        label=label.strip(),
        from_bot=bool(sender.get("is_bot")),
    )
