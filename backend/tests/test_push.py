"""Notifications sur le téléphone (Web Push) de bout en bout : un faux service de notification déchiffre ce que
Bank envoie avec la clé privée du navigateur (vecteur RFC 8291) — titre, texte, écran à ouvrir, rien de plus — ;
l'abonnement exige l'accord et une adresse publique https ; un abonnement mort (410) retire l'appareil ; les
alertes planifiées passent par le même circuit que les messageries (heures calmes, une seule fois)."""

from __future__ import annotations

import hmac
import json
import struct
import time
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from sqlalchemy import select
from starlette.testclient import TestClient

from app.channels.gateway import Gateway
from app.config import Settings
from app.main import create_app
from app.models import AuditLog, ChannelConfig, ChannelLink, Procedure
from app.security import webpush
from tests.conftest import do_login, do_setup
from tests.scenario import seed_household
from tests.test_watch import _resp  # réponse livrée par morceaux, comme sur le réseau

UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/dFhY-abc123"
SUBSCRIPTION = {"endpoint": ENDPOINT, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
CLOCK: dict[str, datetime] = {"now": NOW}
PUSHES: list[dict[str, Any]] = []
DEAD: set[str] = set()


def decrypt(body: bytes) -> bytes:
    """Ce que fait le navigateur (RFC 8291, côté réception)."""
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public_raw = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    ua_private = ec.derive_private_key(int.from_bytes(webpush.b64u_decode(UA_PRIVATE), "big"), ec.SECP256R1())
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public_raw))
    prk_key = hmac.new(webpush.b64u_decode(AUTH), shared, sha256).digest()
    info = b"WebPush: info\x00" + webpush.b64u_decode(UA_PUBLIC) + as_public_raw
    ikm = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=info).derive(prk_key)
    prk = hmac.new(salt, ikm, sha256).digest()
    cek = HKDFExpand(algorithm=hashes.SHA256(), length=16, info=b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDFExpand(algorithm=hashes.SHA256(), length=12, info=b"Content-Encoding: nonce\x00").derive(prk)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def _push_service(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST" and request.url.scheme == "https"
    if str(request.url) in DEAD:
        return _resp(410)
    auth = request.headers["authorization"]
    assert auth.startswith("vapid t=") and ", k=" in auth
    assert request.headers["content-encoding"] == "aes128gcm" and request.headers["ttl"] == "86400"
    PUSHES.append(
        {
            "url": str(request.url),
            "urgency": request.headers["urgency"],
            "payload": json.loads(decrypt(request.content)),
        }
    )
    return _resp(201)


@pytest.fixture(autouse=True)
def _reset() -> None:
    PUSHES.clear()
    DEAD.clear()
    CLOCK["now"] = NOW


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    application = create_app(settings)
    application.state.push_http = httpx.Client(transport=httpx.MockTransport(_push_service))
    application.state.watch_resolver = lambda host: ["142.250.74.78"]  # une adresse publique, comme fcm.googleapis.com
    application.state.channels_options = {
        "poll_seconds": 0.05,
        "alert_first_delay": 3600.0,
        "clock": lambda: CLOCK["now"],
    }
    return application


@pytest.fixture
def world(app, client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    with app.state.sessionmaker() as db:
        seed_household(db)
    return client


def test_subscribe_needs_consent_and_a_public_https_endpoint_then_a_test_notification_is_readable(
    world: TestClient, app
) -> None:  # type: ignore[no-untyped-def]
    st = world.get("/api/push").json()
    assert st["devices"] == [] and st["consent"] is False and len(webpush.b64u_decode(st["public_key"])) == 65
    assert world.get("/api/push").json()["public_key"] == st["public_key"], "la clé du serveur ne change pas"
    r = world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION, "label": "iPhone de Micka"})
    assert r.status_code == 403 and "accord" in r.json()["detail"]
    assert world.put("/api/consents/push", json={"granted": True}).status_code == 200
    bad = {**SUBSCRIPTION, "endpoint": "http://fcm.googleapis.com/x"}
    assert world.post("/api/push/subscribe", json={"subscription": bad}).status_code == 422
    app.state.watch_resolver = lambda host: ["192.168.1.9"]  # un service qui résoudrait vers ton réseau : refusé
    assert world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION}).status_code == 422
    app.state.watch_resolver = lambda host: ["142.250.74.78"]
    r = world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION, "label": "  iPhone   de Micka "})
    assert r.status_code == 201, r.text
    device = r.json()["device"]
    assert device["label"] == "iPhone de Micka" and r.json()["devices"][0]["id"] == device["id"]
    # Le même appareil qui se réabonne ne fait pas un doublon.
    r2 = world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION, "label": "iPhone"})
    assert r2.status_code == 201 and len(r2.json()["devices"]) == 1 and r2.json()["device"]["id"] == device["id"]
    with app.state.sessionmaker() as db:
        cfg = db.get(ChannelConfig, "webpush")
        assert cfg is not None and cfg.enabled
        raw = db.execute(select(ChannelLink.__table__.c.user_index)).scalars().all()
        assert all(ENDPOINT not in v for v in raw), "l'adresse d'abonnement n'est jamais en clair dans l'index"
    # Une notification d'essai, déchiffrée par le « navigateur » du test.
    t = world.post(f"/api/push/{device['id']}/test")
    assert t.status_code == 200, t.text
    assert len(PUSHES) == 1 and PUSHES[0]["url"] == ENDPOINT and PUSHES[0]["urgency"] == "normal"
    payload = PUSHES[0]["payload"]
    assert (
        payload["title"] == "Bank"
        and payload["body"].startswith("Ça marche")
        and payload["url"] == "https://testserver/#/aujourdhui"
    )
    assert set(payload) == {"title", "body", "url", "tag"}
    assert world.get("/api/push").json()["devices"][0]["last_notified_at"] is not None
    # Retrait : l'appareil disparaît, la plateforme s'éteint.
    assert world.delete(f"/api/push/{device['id']}").json()["devices"] == []
    assert world.delete(f"/api/push/{device['id']}").status_code == 404
    with app.state.sessionmaker() as db:
        cfg = db.get(ChannelConfig, "webpush")
        assert cfg is not None and not cfg.enabled
        actions = [a for (a,) in db.execute(select(AuditLog.action))]
        assert {"push.subscribe", "push.test", "push.unsubscribe"} <= set(actions)
        for entry in db.scalars(select(AuditLog).where(AuditLog.action.like("push.%"))):
            assert ENDPOINT not in entry.details and "fcm" not in entry.details


def test_a_dead_subscription_is_removed_and_too_many_devices_are_refused(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    world.put("/api/consents/push", json={"granted": True})
    device = world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION}).json()["device"]
    DEAD.add(ENDPOINT)
    t = world.post(f"/api/push/{device['id']}/test")
    assert t.status_code == 410 and "expiré" in t.json()["detail"]
    assert world.get("/api/push").json()["devices"] == [], "un 410 du service retire l'appareil"
    DEAD.clear()
    for i in range(8):
        sub = {**SUBSCRIPTION, "endpoint": f"{ENDPOINT}-{i}"}
        assert (
            world.post("/api/push/subscribe", json={"subscription": sub, "label": f"appareil {i}"}).status_code == 201
        )
    extra = {**SUBSCRIPTION, "endpoint": f"{ENDPOINT}-9"}
    assert world.post("/api/push/subscribe", json={"subscription": extra}).status_code == 409
    assert len(world.get("/api/push").json()["devices"]) == 8


def test_planned_alerts_reach_the_phone_once_outside_quiet_hours(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    world.put("/api/consents/push", json={"granted": True})
    world.post("/api/push/subscribe", json={"subscription": SUBSCRIPTION, "label": "Pixel"})
    with app.state.sessionmaker() as db:
        db.add(
            Procedure(
                template="prime_activite",
                title="Prime d'activité de Micka",
                stage="waiting",
                data="{}",
                next_check=date(2026, 10, 20),
            )
        )
        db.commit()
    gateway: Gateway = app.state.channels
    end = time.monotonic() + 5
    while "webpush" not in gateway.adapters and time.monotonic() < end:
        time.sleep(0.05)
    assert "webpush" in gateway.adapters, "la plateforme des notifications tourne dès qu'un appareil est abonné"
    CLOCK["now"] = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)  # 0 h 30 à Paris : heures calmes
    assert world.portal.call(gateway.run_alerts) == 0  # type: ignore[union-attr]
    CLOCK["now"] = NOW
    assert world.portal.call(gateway.run_alerts) == 1  # type: ignore[union-attr]
    assert len(PUSHES) == 1
    body = PUSHES[0]["payload"]["body"]
    assert "Demander la prime d'activité" in body and "Micka" not in body and "relancer" in body
    assert world.portal.call(gateway.run_alerts) == 0, "une seule fois"  # type: ignore[union-attr]
    # Accord retiré : la plateforme s'arrête, plus rien ne part.
    world.put("/api/consents/push", json={"granted": False})
    end = time.monotonic() + 5
    while "webpush" in gateway.adapters and time.monotonic() < end:
        time.sleep(0.05)
    assert "webpush" not in gateway.adapters
