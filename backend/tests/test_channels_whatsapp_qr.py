"""WhatsApp par QR code (appareil lié, ton numéro) face à une fausse passerelle GOWA fidèle à son API : QR code
servi en data URI, état du téléphone relié, conversation « Moi » seule (le reste n'est ni lu, ni gardé, ni
journalisé), pas de boucle sur nos propres réponses, envoi, « Délier » derrière le mot de passe. Aucun réseau."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from starlette.testclient import TestClient

from app.channels.base import AdapterError, Inbound, SendError
from app.channels.gateway import Gateway
from app.channels.whatsapp_qr import MARK, WhatsAppQrAdapter, bare_jid, check_bridge_url, phone_hint
from app.config import Settings
from app.main import create_app
from app.models import AuditLog, ChannelConfig, ChannelLink, ChannelSeen
from tests.conftest import PASSWORD

BRIDGE = "http://bridge:3000"
USER, PASS, SECRET = "bank", "p" * 24, "s" * 32
ME = "33612345678@s.whatsapp.net"
LID = "251556368777322@lid"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class FakeBridge:
    """GOWA en miniature : Basic auth partout sauf /statics, un emplacement d'appareil, un QR par /app/login."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.devices: list[str] = []
        self.logged_in = False
        self.jid = ""
        self.logins = 0
        self.sent: list[dict[str, Any]] = []
        self.presence: list[dict[str, Any]] = []
        self.no_auth = False
        self.qr_host = "127.0.0.1:3000"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers["host"] != "bridge:3000":
            return httpx.Response(599, text="hôte inattendu")
        path = request.url.path
        if path.startswith("/statics/"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        expected = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()
        if not self.no_auth and request.headers.get("authorization") != expected:
            return httpx.Response(401, text="Unauthorized")
        body = json.loads(request.content) if request.content else {}
        if path == "/devices" and request.method == "GET":
            devices = [{"id": d, "state": "disconnected"} for d in self.devices] or None
            return httpx.Response(200, json={"code": "SUCCESS", "results": devices})
        if path == "/devices" and request.method == "POST":
            self.devices.append(body["device_id"])
            return httpx.Response(200, json={"code": "SUCCESS", "results": {"id": body["device_id"]}})
        if request.headers.get("x-device-id") not in self.devices:
            return httpx.Response(400, json={"code": "DEVICE_ID_REQUIRED", "message": "device_id is required"})
        if path == "/app/status":
            results = {"is_connected": self.logged_in, "is_logged_in": self.logged_in, "jid": self.jid}
            return httpx.Response(200, json={"code": "SUCCESS", "results": results})
        if path == "/app/login":
            self.logins += 1
            link = f"http://{self.qr_host}/statics/qrcode/scan-qr-{self.logins:08d}-aaaa-bbbb.png"
            return httpx.Response(200, json={"code": "SUCCESS", "results": {"qr_link": link, "qr_duration": 30}})
        if path == "/app/logout":
            self.logged_in, self.jid = False, ""
            return httpx.Response(200, json={"code": "SUCCESS", "results": {}})
        if path == "/send/message":
            if not self.logged_in:
                return httpx.Response(500, json={"code": "INVALID_WA_CLI", "message": "your WhatsApp CLI is invalid"})
            self.sent.append(body)
            mid = f"3EB0{len(self.sent):04d}"
            return httpx.Response(200, json={"code": "SUCCESS", "results": {"message_id": mid, "status": "ok"}})
        if path == "/send/chat-presence":
            self.presence.append(body)
            return httpx.Response(200, json={"code": "SUCCESS", "results": {}})
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": path})

    def scan(self, jid: str = "33612345678:7@s.whatsapp.net") -> None:
        self.logged_in, self.jid = True, jid


def event(
    body: str = "salut",
    *,
    mid: str = "3EB0AAAA",
    chat: str = ME,
    sender: str = ME,
    from_me: bool = True,
    device: str = ME,
    kind: str = "message",
    lid: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": mid,
        "chat_id": chat,
        "from": sender,
        "from_name": "Micka",
        "timestamp": "2026-10-24T10:00:00Z",
        "is_from_me": from_me,
        "body": body,
    }
    if lid:
        payload["from_lid"] = lid
    return {"event": kind, "device_id": device, "payload": payload}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _adapter(fake: FakeBridge, http: httpx.AsyncClient, *, paired: bool = True) -> WhatsAppQrAdapter:
    adapter = WhatsAppQrAdapter(BRIDGE, USER, PASS, SECRET, http)
    if paired:
        fake.scan()
        await adapter.check()
    return adapter


# ============================================================================ adresse de la passerelle
@pytest.mark.parametrize(
    "url",
    [
        "http://whatsapp-bridge:3000",
        "http://127.0.0.1:3011/",
        "http://localhost:3000",
        "http://192.168.1.20:3000",
        "https://wa.lan",
        "http://[::1]:3000",
    ],
)
def test_bridge_address_on_your_network_is_accepted(url: str) -> None:
    assert check_bridge_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://bridge.example.com",
        "http://8.8.8.8:3000",
        "http://0.0.0.0:3000",
        "ftp://127.0.0.1",
        "http://user:pw@127.0.0.1:3000",
        "http://127.0.0.1:3000/app",
    ],
)
def test_bridge_address_on_the_internet_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        check_bridge_url(url)


def test_jid_helpers() -> None:
    assert bare_jid("33612345678:12@s.whatsapp.net") == ME
    assert bare_jid("pas-un-jid") == ""
    assert phone_hint(ME) == "+33…78"


# ============================================================================ adaptateur
def test_qr_code_is_proxied_as_data_uri_cached_then_refreshed() -> None:
    fake = FakeBridge()
    fake.qr_host = "evil.example:80"  # l'hôte écrit dans le lien n'est jamais suivi
    clock = {"t": 0.0}

    async def scenario() -> tuple[str | None, str | None, str | None, str | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            adapter = WhatsAppQrAdapter(BRIDGE, USER, PASS, SECRET, http, clock=lambda: clock["t"])
            with pytest.raises(AdapterError, match="pas relié"):
                await adapter.check()
            first = await adapter.qr_image()
            again = await adapter.qr_image()
            clock["t"] = 25.0
            refreshed = await adapter.qr_image()
            fake.scan()
            clock["t"] = 50.0
            return first, again, refreshed, await adapter.qr_image()

    first, again, refreshed, done = run(scenario())
    assert first == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert again == first and refreshed == first and fake.logins == 2, "un QR neuf toutes les 20 s, pas plus"
    assert done is None, "relié : plus de QR"
    assert fake.devices == ["bank"], "Bank crée son emplacement d'appareil une seule fois"
    assert all(r.headers["host"] == "bridge:3000" for r in fake.requests)


def test_a_bridge_without_password_or_with_a_wrong_one_is_refused() -> None:
    open_bridge = FakeBridge()
    open_bridge.no_auth = True

    async def scenario(fake: FakeBridge, password: str) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            await WhatsAppQrAdapter(BRIDGE, USER, password, SECRET, http).check()

    with pytest.raises(AdapterError, match="sans mot de passe"):
        run(scenario(open_bridge, PASS))
    with pytest.raises(AdapterError, match="mot de passe refusé"):
        run(scenario(FakeBridge(), "mauvais" * 4))


def test_only_the_self_chat_gets_through() -> None:
    fake = FakeBridge()

    async def scenario() -> WhatsAppQrAdapter:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            return await _adapter(fake, http)

    adapter = run(scenario())
    assert adapter.jid == ME
    other = "33699999999@s.whatsapp.net"
    rejected = [
        event(chat=other, sender=other, from_me=False),  # quelqu'un t'écrit
        event(chat=other),  # tu écris à quelqu'un
        event(chat="120363402106@g.us"),  # tu écris dans un groupe
        event(chat="120363402106@g.us", sender=other, from_me=False),
        event(chat=ME, sender=other, from_me=False),  # usurpation : pas de toi
        event(chat=ME, sender=other),
        event(device="33600000000@s.whatsapp.net"),  # autre appareil de la passerelle
        event(kind="message.reaction"),
        event(body=""),  # média sans légende
        event(body=MARK + "réponse de Bank"),  # notre propre réponse
        event(chat="999@lid", lid=LID),  # @lid qui n'est pas toi
        {"event": "message", "device_id": ME, "payload": "x"},
        [],
    ]
    for payload in rejected:
        assert adapter.parse_webhook(payload) == [], payload
    (got,) = adapter.parse_webhook(event("/aide", mid="M1"))
    assert got == Inbound("whatsapp_qr", ME, ME, "/aide", "M1", label="Toi (conversation « Moi »)")
    (lid_chat,) = adapter.parse_webhook(event("ok", chat=LID, sender=LID, lid=LID))
    assert lid_chat.chat_id == ME, "on répond toujours à ton numéro"
    adapter.jid = None
    assert adapter.parse_webhook(event()) == [], "pas encore relié : rien ne passe"


def test_send_only_to_self_with_marker_and_never_reread_own_replies() -> None:
    fake = FakeBridge()

    async def scenario() -> WhatsAppQrAdapter:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            adapter = await _adapter(fake, http)
            await adapter.send(ME, "**Budget** : 12 €")
            await adapter.typing(ME)
            await adapter.typing("33699999999@s.whatsapp.net")
            with pytest.raises(SendError, match="conversation « Moi »") as refused:
                await adapter.send("33699999999@s.whatsapp.net", "fuite")
            assert refused.value.permanent
            return adapter

    adapter = run(scenario())
    assert fake.sent == [{"phone": ME, "message": MARK + "*Budget* : 12 €"}]
    assert fake.presence == [{"phone": ME, "action": "start"}]
    # L'écho de notre réponse revient par le webhook : écarté par son identifiant, même sans marqueur.
    assert adapter.parse_webhook(event("*Budget* : 12 €", mid="3EB00001")) == []


def test_logout_and_a_phone_unlinked_from_whatsapp_are_noticed() -> None:
    fake = FakeBridge()

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            adapter = await _adapter(fake, http)
            adapter._status_every = 0.01
            fake.logged_in = False  # « Déconnecter » depuis WhatsApp › Appareils connectés
            with pytest.raises(AdapterError, match="délié"):
                await asyncio.wait_for(adapter.run(lambda _: asyncio.sleep(0)), 2.0)
            fake.scan()
            await adapter.check()
            await adapter.logout()
            assert adapter.jid is None and not fake.logged_in
            with pytest.raises(SendError):
                await adapter.send(ME, "x")

    run(scenario())


# ============================================================================ de bout en bout dans l'application
@pytest.fixture
def fake() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def app(settings: Settings, fake: FakeBridge):  # type: ignore[no-untyped-def]
    application = create_app(settings)
    application.state.channels_options = {
        "poll_seconds": 0.05,
        "alert_first_delay": 3600.0,
        "http": httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    }
    return application


def wait_for(cond: Callable[[], Any], timeout: float = 5.0) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition jamais remplie")


def _signed(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def _post(client: TestClient, payload: Any, **headers: str) -> Any:
    body = json.dumps(payload).encode()
    return client.post("/api/channels/whatsapp_qr/webhook", content=body, headers={**_signed(body), **headers})


def _view(client: TestClient) -> dict[str, Any]:
    return next(p for p in client.get("/api/channels").json()["platforms"] if p["key"] == "whatsapp_qr")


def test_qr_pairing_self_chat_and_unlink_end_to_end(  # type: ignore[no-untyped-def]
    auth_client: TestClient, app, db, fake: FakeBridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = auth_client
    assert client.put("/api/consents/channels", json={"granted": True}).status_code == 200
    public = client.put("/api/channels/whatsapp_qr", json={"values": {"url": "https://wa.example.com"}})
    assert public.status_code == 422 and "ton réseau" in public.json()["detail"]
    values = {"url": BRIDGE, "username": USER, "password": PASS, "webhook_secret": SECRET}
    r = client.put("/api/channels/whatsapp_qr", json={"values": values, "enabled": True})
    assert r.status_code == 200, r.text
    view = _view(client)
    assert view["label"] == "WhatsApp (QR, ton numéro)" and view["e2e"] is True
    assert PASS not in r.text and SECRET not in r.text, "secrets jamais renvoyés"
    wait_for(lambda: _view(client)["status"] == "error")
    assert "scanne le QR code" in _view(client)["last_error"]

    # Pas de QR sans mot de passe, et pas de webhook tant que rien ne tourne relié.
    assert client.get("/api/channels/whatsapp_qr/qr").status_code == 409
    st = client.get("/api/channels/whatsapp_qr/status").json()
    assert st == {"bridge": True, "error": None, "paired": False, "phone": None, "linked": False, "window": False}
    bad = client.post("/api/channels/whatsapp_qr/pairing", json={"password": "mauvais"})
    assert bad.status_code == 403
    assert client.post("/api/channels/whatsapp_qr/pairing", json={"password": PASSWORD}).status_code == 201
    qr = client.get("/api/channels/whatsapp_qr/qr").json()
    assert qr["paired"] is False and qr["qr"].startswith("data:image/png;base64,") and qr["refresh_in"] == 20
    assert BRIDGE not in json.dumps(qr) and "statics" not in json.dumps(qr), "le navigateur ignore la passerelle"

    # Tu scannes : Bank te relie et se connecte tout de suite.
    fake.scan()
    st = client.get("/api/channels/whatsapp_qr/status").json()
    assert st["paired"] is True and st["phone"] == "+33…78" and st["linked"] is True and st["window"] is False
    wait_for(lambda: _view(client)["status"] == "online")
    gateway: Gateway = app.state.channels
    assert wait_for(lambda: getattr(gateway.adapters.get("whatsapp_qr"), "jid", None)) == ME
    assert client.get("/api/channels/whatsapp_qr/qr").status_code == 409, "fenêtre refermée une fois relié"

    # Webhook : venu par le frontal, non signé, ou mal signé → refusé.
    assert _post(client, event(), **{"x-forwarded-for": "203.0.113.9"}).status_code == 404
    raw = json.dumps(event()).encode()
    assert client.post("/api/channels/whatsapp_qr/webhook", content=raw).status_code == 401
    assert (
        client.post("/api/channels/whatsapp_qr/webhook", content=raw, headers=_signed(raw, "x" * 32)).status_code == 401
    )

    # Les autres conversations : rien ne répond, rien n'est gardé, rien n'est journalisé.
    seen_before = db.scalar(select(func.count()).select_from(ChannelSeen))
    audit_before = db.scalar(select(func.count()).select_from(AuditLog))
    other = "33699999999@s.whatsapp.net"
    secret_talk = "code de la porte 4721 chez Léa"
    for payload in (
        event(secret_talk, mid="O1", chat=other, sender=other, from_me=False),
        event(secret_talk, mid="O2", chat=other),
        event(secret_talk, mid="O3", chat="120363402106@g.us"),
    ):
        assert _post(client, payload).json() == {"ok": True}
    time.sleep(0.3)
    db.expire_all()
    assert fake.sent == []
    assert db.scalar(select(func.count()).select_from(ChannelSeen)) == seen_before
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audit_before
    assert db.get(ChannelConfig, "whatsapp_qr").ignored == 0
    assert secret_talk not in caplog.text and "4721" not in caplog.text

    # La conversation « Moi » : Bank répond là, une seule fois même si la passerelle réessaie.
    assert _post(client, event("/aide", mid="SELF1")).status_code == 200
    _post(client, event("/aide", mid="SELF1"))
    wait_for(lambda: fake.sent)
    time.sleep(0.2)
    assert len(fake.sent) == 1
    reply = fake.sent[0]
    assert reply["phone"] == ME and reply["message"].startswith(MARK) and "/budget" in reply["message"]
    # L'écho de cette réponse (même identifiant, même marqueur) ne relance rien : pas de boucle.
    _post(client, event(reply["message"], mid="3EB00001"))
    _post(client, event(reply["message"][1:], mid="3EB00001"))
    time.sleep(0.3)
    assert len(fake.sent) == 1

    # Délier : mot de passe, puis la passerelle déconnecte le téléphone et le compte relié est retiré.
    assert client.post("/api/channels/whatsapp_qr/logout", json={"password": "mauvais"}).status_code == 403
    assert fake.logged_in
    assert client.post("/api/channels/whatsapp_qr/logout", json={"password": PASSWORD}).status_code == 204
    assert not fake.logged_in
    db.expire_all()
    links = db.scalars(select(ChannelLink).where(ChannelLink.platform == "whatsapp_qr")).all()
    assert links and all(link.revoked_at is not None for link in links)
    wait_for(lambda: _view(client)["status"] == "error")


def test_a_phone_scanned_outside_bank_is_not_linked(auth_client: TestClient, db: Any, fake: FakeBridge) -> None:
    client = auth_client
    client.put("/api/consents/channels", json={"granted": True})
    values = {"url": BRIDGE, "password": PASS, "webhook_secret": SECRET}
    assert client.put("/api/channels/whatsapp_qr", json={"values": values, "enabled": True}).status_code == 200
    fake.devices.append("bank")
    fake.scan()  # relié sans passer par la fenêtre ouverte avec ton mot de passe
    st = client.get("/api/channels/whatsapp_qr/status").json()
    assert st["paired"] is True and st["linked"] is False
    wait_for(lambda: _view(client)["status"] == "online")
    assert _post(client, event("/aide", mid="S1")).status_code == 200
    time.sleep(0.3)
    assert fake.sent == [], "un inconnu (même toi, sans mot de passe) n'obtient aucune réponse"
    assert db.scalar(select(func.count()).select_from(ChannelLink)) == 0
