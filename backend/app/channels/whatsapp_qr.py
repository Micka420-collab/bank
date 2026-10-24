"""WhatsApp « appareil lié » : ton numéro personnel relié par QR code, comme WhatsApp Web, au travers d'une
passerelle qui tourne à côté de Bank (GOWA, go-whatsapp-web-multidevice, bâtie sur whatsmeow).

Choix assumé par le propriétaire : client non officiel (interdit par les conditions de WhatsApp, suspension
possible) et session qui voit toutes tes conversations. D'où des garde-fous stricts, côté Bank :

- la passerelle n'est jointe qu'en local ou sur un réseau privé (adresse vérifiée à l'enregistrement), avec son
  identifiant et son mot de passe ; elle n'est jamais exposée, et le navigateur ne voit jamais son adresse ;
- elle remet les messages par un webhook interne signé (HMAC-SHA256, comparé en temps constant) ;
- Bank ne garde QUE la conversation « Moi » (messages que tu t'envoies à toi-même) : tout le reste est écarté
  avant d'être lu, sans journal ni écriture en base ;
- ses propres réponses ne sont jamais relues : elles commencent par un marqueur invisible, et les identifiants
  des messages envoyés sont retenus (en nombre borné) ;
- Bank n'écrit qu'à toi-même : un envoi vers un autre numéro est refusé.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.channels.base import AdapterError, Deliver, Inbound, SendError
from app.channels.text import to_whatsapp

PLATFORM = "whatsapp_qr"
DEVICE = "bank"  # emplacement d'appareil créé dans la passerelle pour Bank
MARK = "⁣"  # séparateur invisible en tête de chaque réponse de Bank : on ne se relit jamais
SELF_LABEL = "Toi (conversation « Moi »)"
QR_TTL = 20.0  # la passerelle donne un QR valable 30 s : on le renouvelle avant
MAX_QR_BYTES = 200_000
MAX_SENT = 500
_QR_PATH = re.compile(r"/statics/(?:images/)?qrcode/scan-qr-[A-Za-z0-9-]{8,64}\.png")
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")


# ------------------------------------------------------------------ adresse de la passerelle
def check_bridge_url(url: str) -> str:
    """http(s) vers localhost, une adresse privée, ou un nom de réseau local (service Docker « whatsapp-bridge »,
    *.lan, *.internal…) : jamais une adresse publique. Lève ValueError avec un message montrable."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ValueError("Adresse de la passerelle : http(s)://hôte:port, sans identifiant dans l'adresse")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("Adresse de la passerelle : juste http(s)://hôte:port, sans chemin")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES):
            return url.rstrip("/")
        raise ValueError(
            "La passerelle WhatsApp doit rester sur ton réseau : localhost, une adresse privée ou un nom local "
            "(par exemple http://whatsapp-bridge:3000)"
        ) from None
    if ip.is_loopback or (ip.is_private and not ip.is_unspecified) or ip.is_link_local:
        return url.rstrip("/")
    raise ValueError("La passerelle WhatsApp doit rester sur ton réseau : adresse privée ou localhost seulement")


def bare_jid(jid: str) -> str:
    """« 33612345678:12@s.whatsapp.net » → « 33612345678@s.whatsapp.net » (sans le numéro d'appareil)."""
    user, _, server = jid.strip().partition("@")
    user = user.split(":", 1)[0]
    return f"{user}@{server}" if user and server else ""


def phone_hint(jid: str) -> str:
    digits = jid.partition("@")[0].split(":", 1)[0]
    return f"+{digits[:2]}…{digits[-2:]}" if len(digits) >= 6 and digits.isdigit() else "ton numéro"


class WhatsAppQrAdapter:
    platform = PLATFORM

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        webhook_secret: str,
        http: httpx.AsyncClient,
        *,
        status_every: float = 30.0,
        clock: Any = time.monotonic,
    ) -> None:
        self._url = check_bridge_url(url)
        self._auth = httpx.BasicAuth(username or "bank", password)
        self._secret = webhook_secret.encode()
        self._http = http
        self._status_every = status_every
        self._clock = clock
        self._device_ready = False
        self._sent: OrderedDict[str, None] = OrderedDict()
        self._qr: tuple[str, float] | None = None
        self.jid: str | None = None  # notre propre numéro, une fois relié (« …@s.whatsapp.net »)

    # ------------------------------------------------------------ appels à la passerelle
    async def _call(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = await self._http.request(
                method,
                f"{self._url}{path}",
                json=body,
                auth=self._auth,
                headers={"X-Device-Id": DEVICE},
                timeout=20.0,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise AdapterError("Passerelle WhatsApp injoignable : vérifie son adresse et qu'elle tourne") from None
        if resp.status_code == 401:
            raise AdapterError("Passerelle WhatsApp : identifiant ou mot de passe refusé")
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise AdapterError(f"Passerelle WhatsApp : réponse illisible (HTTP {resp.status_code})")
        if not resp.is_success or data.get("code") not in (None, "SUCCESS"):
            detail = str(data.get("message") or data.get("code") or resp.status_code)[:160]
            raise AdapterError(f"Passerelle WhatsApp : {detail}")
        return data

    async def _ensure_device(self) -> None:
        """La passerelle sert plusieurs appareils : Bank a le sien, créé au premier appel."""
        if self._device_ready:
            return
        # Une passerelle lancée sans --basic-auth donnerait la main sur ton compte à tout ce qui la joint : refusée.
        try:
            bare = await self._http.get(f"{self._url}/app/status", timeout=10.0, follow_redirects=False)
        except httpx.HTTPError:
            raise AdapterError("Passerelle WhatsApp injoignable : vérifie son adresse et qu'elle tourne") from None
        if bare.status_code != 401:
            raise AdapterError("La passerelle WhatsApp répond sans mot de passe : lance-la avec --basic-auth")
        data = await self._call("GET", "/devices")
        known = data.get("results") or []
        if not any(isinstance(d, dict) and d.get("id") == DEVICE for d in known):
            await self._call("POST", "/devices", body={"device_id": DEVICE})
        self._device_ready = True

    async def status(self) -> dict[str, Any]:
        """{"logged_in": bool, "jid": "…@s.whatsapp.net" ou ""} : rien d'autre ne sort de la passerelle."""
        await self._ensure_device()
        results = (await self._call("GET", "/app/status")).get("results") or {}
        logged_in = bool(results.get("is_logged_in")) if isinstance(results, dict) else False
        jid = bare_jid(str(results.get("jid") or "")) if isinstance(results, dict) else ""
        return {"logged_in": logged_in and bool(jid), "jid": jid if logged_in else ""}

    async def qr_image(self) -> str | None:
        """Le QR code à scanner, en data URI (la passerelle n'est jamais montrée au navigateur) ; None si déjà relié.
        Gardé 20 s : chaque demande à la passerelle en fabrique un nouveau."""
        now = self._clock()
        if self._qr is not None and self._qr[1] > now:
            return self._qr[0]
        st = await self.status()
        if st["logged_in"]:
            self._qr = None
            return None
        results = (await self._call("GET", "/app/login")).get("results") or {}
        link = str(results.get("qr_link") or "") if isinstance(results, dict) else ""
        path = urlsplit(link).path
        if not _QR_PATH.fullmatch(path):
            raise AdapterError("Passerelle WhatsApp : QR code introuvable")
        try:
            # Toujours chez la passerelle configurée : on ne suit jamais l'hôte écrit dans le lien.
            resp = await self._http.get(f"{self._url}{path}", auth=self._auth, timeout=10.0, follow_redirects=False)
        except httpx.HTTPError:
            raise AdapterError("Passerelle WhatsApp injoignable") from None
        png = resp.content
        if resp.status_code != 200 or len(png) > MAX_QR_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdapterError("Passerelle WhatsApp : image du QR code illisible")
        uri = "data:image/png;base64," + base64.b64encode(png).decode()
        self._qr = (uri, now + QR_TTL)
        return uri

    async def logout(self) -> None:
        await self._ensure_device()
        await self._call("GET", "/app/logout")
        self.jid, self._qr = None, None

    # ------------------------------------------------------------ contrat des adaptateurs
    async def check(self) -> dict[str, Any]:
        st = await self.status()
        if not st["logged_in"]:
            self.jid = None
            raise AdapterError("WhatsApp n'est pas relié : scanne le QR code dans Bank › Messageries")
        self.jid = st["jid"]
        return {"name": f"WhatsApp {phone_hint(st['jid'])}", "account": st["jid"]}

    async def run(self, deliver: Deliver) -> None:
        # Les messages arrivent par le webhook interne ; ici, on surveille que l'appareil reste relié (un
        # « Déconnecter » depuis ton téléphone doit se voir dans Bank).
        while True:
            await asyncio.sleep(self._status_every)
            st = await self.status()
            if not st["logged_in"] or st["jid"] != self.jid:
                self.jid = None
                raise AdapterError("WhatsApp a été délié (depuis ton téléphone ?) : scanne un nouveau QR code")

    async def send(self, chat_id: str, text: str) -> None:
        if not self.jid or bare_jid(chat_id) != self.jid:
            raise SendError("Bank n'écrit que dans ta conversation « Moi »", permanent=True)
        try:
            data = await self._call(
                "POST", "/send/message", body={"phone": self.jid, "message": MARK + to_whatsapp(text)}
            )
        except AdapterError as exc:
            raise SendError(str(exc)) from None
        results = data.get("results") or {}
        mid = str(results.get("message_id") or "") if isinstance(results, dict) else ""
        if mid:
            self._remember(mid)

    async def typing(self, chat_id: str) -> None:
        if not self.jid or bare_jid(chat_id) != self.jid:
            return
        try:
            await self._call("POST", "/send/chat-presence", body={"phone": self.jid, "action": "start"})
        except AdapterError:
            return

    def _remember(self, message_id: str) -> None:
        self._sent[message_id] = None
        while len(self._sent) > MAX_SENT:
            self._sent.popitem(last=False)

    # ------------------------------------------------------------ webhook interne
    def verify_signature(self, body: bytes, header: str | None) -> bool:
        if not header or not header.startswith("sha256="):
            return False
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode(), header[7:].strip().encode())

    def parse_webhook(self, payload: Any) -> list[Inbound]:
        """Le seul message retenu : un texte que tu t'es écrit dans ta conversation « Moi ». Tout le reste (autres
        conversations, groupes, accusés, réactions, médias, nos propres réponses) est écarté sans être lu."""
        jid = self.jid
        if not jid or not isinstance(payload, dict) or payload.get("event") != "message":
            return []
        if bare_jid(str(payload.get("device_id") or "")) != jid:
            return []
        msg = payload.get("payload")
        if not isinstance(msg, dict) or msg.get("is_from_me") is not True:
            return []
        if not _is_self_chat(msg, jid):
            return []
        mid = msg.get("id")
        if not isinstance(mid, str) or not mid or mid in self._sent:
            return []
        text = msg.get("body")
        if not isinstance(text, str) or not text.strip() or text.startswith(MARK):
            return []
        return [Inbound(platform=PLATFORM, user_id=jid, chat_id=jid, text=text, message_id=mid, label=SELF_LABEL)]


def _is_self_chat(msg: dict[str, Any], jid: str) -> bool:
    chat = str(msg.get("chat_id") or "")
    sender = bare_jid(str(msg.get("from") or ""))
    if sender.endswith("@s.whatsapp.net") and sender != jid:
        return False
    if bare_jid(chat) == jid:
        return True
    # Certains téléphones désignent la conversation « Moi » par ton identifiant masqué (@lid) : on ne l'accepte
    # que s'il est exactement celui de l'expéditeur, c'est-à-dire toi.
    lid = bare_jid(str(msg.get("from_lid") or ""))
    return chat.endswith("@lid") and bool(lid) and bare_jid(chat) == lid
