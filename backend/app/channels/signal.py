"""Signal, par le démon HTTP de signal-cli (« signal-cli daemon --http ») qui tourne à côté de Bank : chiffré de
bout en bout, aucune adresse publique, aucun tiers qui lit tes chiffres. Réception par flux SSE
(/api/v1/events), envoi et indicateur de saisie par JSON-RPC (/api/v1/rpc)."""

from __future__ import annotations

import itertools
import json
from typing import Any
from urllib.parse import quote

import httpx

from app.channels.base import AdapterError, Deliver, Inbound, SendError
from app.channels.text import to_plain

_IDS = itertools.count(1)


class SignalAdapter:
    platform = "signal"

    def __init__(self, url: str, account: str, http: httpx.AsyncClient) -> None:
        self._url = url.rstrip("/")
        self._account = account
        self._http = http

    async def check(self) -> dict[str, Any]:
        try:
            resp = await self._http.get(f"{self._url}/api/v1/check", timeout=10.0)
        except httpx.HTTPError:
            raise AdapterError("Service signal-cli injoignable : vérifie son adresse et qu'il tourne") from None
        if resp.status_code != 200:
            raise AdapterError(f"Service signal-cli : réponse {resp.status_code}")
        return {"name": f"Signal {self._account[:4]}…{self._account[-2:]}"}

    async def run(self, deliver: Deliver) -> None:
        url = f"{self._url}/api/v1/events?account={quote(self._account)}"
        timeout = httpx.Timeout(10.0, read=None)
        async with self._http.stream("GET", url, headers={"Accept": "text/event-stream"}, timeout=timeout) as resp:
            if resp.status_code != 200:
                raise AdapterError(f"Flux Signal refusé ({resp.status_code})")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue  # commentaires « : » (maintien de connexion), event:, id:
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                inbound = parse_event(event, self._account)
                if inbound is not None:
                    await deliver(inbound)
        raise AdapterError("Flux Signal interrompu")  # le superviseur se reconnecte

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        body = {"jsonrpc": "2.0", "method": method, "id": f"{method}-{next(_IDS)}", "params": params}
        try:
            resp = await self._http.post(f"{self._url}/api/v1/rpc", json=body, timeout=30.0)
        except httpx.TimeoutException:
            raise SendError("Signal ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("Service signal-cli injoignable") from None
        try:
            data = resp.json()
        except ValueError:
            raise SendError(f"Réponse illisible de signal-cli (HTTP {resp.status_code})") from None
        if not isinstance(data, dict):
            raise SendError("Réponse illisible de signal-cli")
        if data.get("error"):
            error = data["error"] if isinstance(data["error"], dict) else {}
            message = str(error.get("message") or "erreur")[:160]
            raise SendError(f"Signal : {message}", permanent="Unregistered" in message or "not registered" in message)
        return data.get("result")

    async def send(self, chat_id: str, text: str) -> None:
        result = await self._rpc("send", {"account": self._account, "recipient": [chat_id], "message": to_plain(text)})
        results = result.get("results") if isinstance(result, dict) else None
        if (
            isinstance(results, list)
            and results
            and all(r.get("type") != "SUCCESS" for r in results if isinstance(r, dict))
        ):
            kind = str(results[0].get("type") if isinstance(results[0], dict) else "")
            raise SendError(f"Signal : {kind or 'échec'}", permanent=kind == "UNREGISTERED_FAILURE")

    async def typing(self, chat_id: str) -> None:
        try:
            await self._rpc("sendTyping", {"account": self._account, "recipient": [chat_id]})
        except SendError:
            return


def parse_event(event: Any, account: str) -> Inbound | None:
    if not isinstance(event, dict):
        return None
    if isinstance(event.get("params"), dict):  # variante JSON-RPC : {"method": "receive", "params": {...}}
        event = event["params"]
    env = event.get("envelope")
    if not isinstance(env, dict):
        return None
    sender = str(env.get("sourceNumber") or env.get("sourceUuid") or env.get("source") or "")
    if not sender or sender == account:
        return None
    data = env.get("dataMessage") or (env.get("editMessage") or {}).get("dataMessage")
    if not isinstance(data, dict):
        return None  # accusés de réception, saisie, synchronisation entre tes appareils
    return Inbound(
        platform="signal",
        user_id=sender,
        chat_id=sender,
        text=str(data.get("message") or ""),
        message_id=f"{sender}:{env.get('timestamp')}",
        direct=not (data.get("groupInfo") or data.get("groupV2")),
        label=str(env.get("sourceName") or "Signal"),
    )
