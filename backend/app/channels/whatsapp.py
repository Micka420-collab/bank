"""WhatsApp, par l'API officielle de Meta (WhatsApp Cloud API) — pas de passerelle « WhatsApp Web » non officielle :
pas de risque de bannissement, pas de processus de plus à surveiller.

Réception : Meta appelle un webhook. C'est la seule messagerie qui demande une adresse publique ; on n'expose que
ce chemin, et chaque appel est vérifié AVANT d'être lu : corps borné, signature HMAC-SHA256 (X-Hub-Signature-256,
clé secrète de l'application) comparée en temps constant, numéro destinataire qui doit être le nôtre. Meta réessaie
pendant des jours un appel qui n'a pas reçu 200 : on répond 200 dès que l'appel est authentique, et les réessais
sont dédoublonnés par identifiant de message.

Envoi : Graph API. Hors des 24 h qui suivent ton dernier message, Meta refuse un message libre (code 131047) :
l'alerte attend que tu écrives au robot — c'est expliqué dans l'écran Messageries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any

import httpx

from app.channels.base import AdapterError, Deliver, Inbound, SendError, retry_after_of
from app.channels.text import to_whatsapp

GRAPH = "https://graph.facebook.com/v20.0"
MAX_WEBHOOK_BYTES = 1_000_000
_RATE_CODES = {4, 80007, 130429, 131048, 131056}
_PERMANENT_CODES = {
    131047: (
        "La fenêtre de 24 h est fermée : écris un message au robot sur WhatsApp pour recevoir à nouveau ses alertes"
    ),
    131026: "Message impossible à remettre à ce numéro",
    131051: "Type de message non pris en charge",
    190: "Jeton d'accès WhatsApp expiré ou révoqué",
    10: "Permission refusée par Meta",
    200: "Permission refusée par Meta",
    100: "Paramètre refusé par Meta (identifiant du numéro ?)",
}


class WhatsAppAdapter:
    platform = "whatsapp"

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        app_secret: str,
        verify_token: str,
        http: httpx.AsyncClient,
        *,
        graph: str = GRAPH,
    ) -> None:
        self.phone_number_id = phone_number_id
        self._token = access_token
        self._secret = app_secret.encode()
        self._verify = verify_token.encode()
        self._http = http
        self._graph = graph
        self._last_wamid: dict[str, str] = {}  # dernier message reçu par conversation (pour « lu » + « écrit… »)

    # ------------------------------------------------------------ vérifications du webhook
    def verify_challenge(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if mode != "subscribe" or not token or not challenge:
            return None
        if not hmac.compare_digest(token.encode(), self._verify):
            return None
        return challenge[:200]

    def verify_signature(self, body: bytes, header: str | None) -> bool:
        if not header or not header.startswith("sha256="):
            return False
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode(), header[7:].strip().encode())

    def parse_webhook(self, payload: Any) -> list[Inbound]:
        """Messages adressés à NOTRE numéro ; accusés de lecture et statuts ignorés."""
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            return []
        out: list[Inbound] = []
        for entry in payload.get("entry") or []:
            for change in (entry or {}).get("changes") or []:
                if not isinstance(change, dict) or change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                if str((value.get("metadata") or {}).get("phone_number_id")) != self.phone_number_id:
                    continue
                names = {
                    str(c.get("wa_id")): str((c.get("profile") or {}).get("name") or "")
                    for c in value.get("contacts") or []
                    if isinstance(c, dict)
                }
                for msg in value.get("messages") or []:
                    inbound = _parse_message(msg, names)
                    if inbound is not None:
                        self._last_wamid[inbound.chat_id] = inbound.message_id
                        out.append(inbound)
        return out

    # ------------------------------------------------------------ Graph API
    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            resp = await self._http.post(
                f"{self._graph}/{path}", json=payload, headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx.TimeoutException:
            raise SendError("WhatsApp ne répond pas") from None
        except httpx.HTTPError:
            raise SendError("WhatsApp injoignable") from None
        return _result(resp)

    async def check(self) -> dict[str, Any]:
        try:
            resp = await self._http.get(
                f"{self._graph}/{self.phone_number_id}",
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {self._token}"},
            )
            data = _result(resp)
        except httpx.HTTPError:
            raise AdapterError("Graph API de Meta injoignable") from None
        except SendError as exc:
            raise AdapterError(f"WhatsApp : {exc}") from None
        digits = "".join(ch for ch in str(data.get("display_phone_number") or "") if ch.isdigit())
        return {"name": str(data.get("verified_name") or "WhatsApp Business"), "display_phone": digits or None}

    async def run(self, deliver: Deliver) -> None:
        # Rien à écouter : Meta appelle le webhook, qui remet les messages à la passerelle.
        await asyncio.Event().wait()

    async def send(self, chat_id: str, text: str) -> None:
        await self._post(
            f"{self.phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "text",
                "text": {"body": to_whatsapp(text), "preview_url": False},
            },
        )

    async def typing(self, chat_id: str) -> None:
        wamid = self._last_wamid.get(chat_id)
        if not wamid:
            return
        try:
            await self._post(
                f"{self.phone_number_id}/messages",
                {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": wamid,
                    "typing_indicator": {"type": "text"},
                },
            )
        except SendError:
            return


def _result(resp: httpx.Response) -> Any:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.is_success and isinstance(data, dict) and "error" not in data:
        return data
    error = (data.get("error") if isinstance(data, dict) else None) or {}
    code = error.get("code") if isinstance(error, dict) else None
    if code in _RATE_CODES or resp.status_code == 429:
        raise SendError("WhatsApp : trop de messages, on réessaie", retry_after=retry_after_of(resp) or 5.0)
    if isinstance(code, int) and code in _PERMANENT_CODES:
        raise SendError(_PERMANENT_CODES[code], permanent=True)
    message = str(error.get("message") or "")[:160] if isinstance(error, dict) else ""
    raise SendError(
        f"WhatsApp {code or resp.status_code} : {message}" if message else f"WhatsApp {resp.status_code}",
        permanent=resp.status_code in (400, 401, 403, 404),
    )


def _parse_message(msg: Any, names: dict[str, str]) -> Inbound | None:
    if not isinstance(msg, dict) or not msg.get("from") or not msg.get("id"):
        return None
    kind = msg.get("type")
    if kind in ("system", "reaction", "unsupported", "unknown", "ephemeral"):
        return None
    text = ""
    if kind == "text":
        text = str((msg.get("text") or {}).get("body") or "")
    elif kind == "button":
        text = str((msg.get("button") or {}).get("text") or "")
    elif kind == "interactive":
        inter = msg.get("interactive") or {}
        text = str((inter.get("button_reply") or inter.get("list_reply") or {}).get("title") or "")
    sender = str(msg["from"])
    return Inbound(
        platform="whatsapp",
        user_id=sender,
        chat_id=sender,
        text=text,
        message_id=str(msg["id"]),
        direct="group_id" not in msg and "chat" not in msg,
        label=names.get(sender) or "WhatsApp",
    )
