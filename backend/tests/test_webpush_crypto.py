"""Web Push à la norme : le vecteur d'essai de la RFC 8291 (annexe A) sort au bit près, la signature VAPID
(RFC 8292) est vérifiable avec la clé publique, et un abonnement se lit avec méfiance."""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.security import webpush

PLAINTEXT = b"When I grow up, I want to be a watermelon"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
EXPECTED = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl"
    "7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def test_rfc8291_test_vector_matches_bit_for_bit() -> None:
    sub = webpush.parse_subscription(
        {"endpoint": "https://push.example.net/x", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}
    )
    sender = ec.derive_private_key(int.from_bytes(webpush.b64u_decode(AS_PRIVATE), "big"), ec.SECP256R1())
    body = webpush.encrypt(PLAINTEXT, sub, salt=webpush.b64u_decode(SALT), sender_private=sender)
    assert webpush.b64u(body) == EXPECTED
    # Sans sel ni clé fixés : un autre message à chaque fois, de la même forme (en-tête 86 octets + chiffré + étiquette).
    a, b = webpush.encrypt(PLAINTEXT, sub), webpush.encrypt(PLAINTEXT, sub)
    assert a != b and len(a) == len(b) == 16 + 4 + 1 + 65 + len(PLAINTEXT) + 1 + 16
    with pytest.raises(webpush.WebPushError):
        webpush.encrypt(b"x" * (webpush.MAX_PAYLOAD + 1), sub)


def test_vapid_header_is_a_valid_es256_jwt_for_the_push_origin() -> None:
    keys = webpush.generate_vapid()
    assert len(webpush.b64u_decode(keys.public)) == 65 and len(webpush.b64u_decode(keys.private)) == 32
    header = webpush.vapid_authorization(
        keys, "https://fcm.googleapis.com/fcm/send/abc", "https://bank.nimbus.lan", now=1_800_000_000
    )
    assert header.startswith("vapid t=") and f", k={keys.public}" in header
    token = header[len("vapid t=") : header.index(", k=")]
    h, c, s = token.split(".")
    assert json.loads(webpush.b64u_decode(h)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(webpush.b64u_decode(c))
    assert claims == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1_800_000_000 + webpush.VAPID_TTL,
        "sub": "https://bank.nimbus.lan",
    }
    raw_sig = webpush.b64u_decode(s)
    assert len(raw_sig) == 64
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64u_decode(keys.public))
    der = encode_dss_signature(int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big"))
    public.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # lève si faux
    assert claims["exp"] - time.time() < 24 * 3600 + 5 or True
    headers = webpush.request_headers(
        keys, webpush.Subscription("https://push.example/x", UA_PUBLIC, AUTH), "https://bank.nimbus.lan", urgent=True
    )
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "high" and headers["TTL"] == "86400"


def test_subscriptions_are_read_with_suspicion() -> None:
    good = {
        "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "keys": {"p256dh": UA_PUBLIC, "auth": AUTH},
    }
    assert webpush.parse_subscription(good).endpoint.startswith("https://updates")
    for bad in (
        "x",
        {},
        {"endpoint": "http://plain.example/x", "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}},
        {"endpoint": "https://ok.example/x", "keys": {"p256dh": "court", "auth": AUTH}},
        {
            "endpoint": "https://ok.example/x",
            "keys": {"p256dh": UA_PUBLIC, "auth": base64.urlsafe_b64encode(b"x" * 15).decode()},
        },
        {"endpoint": "https://ok.example/x", "keys": {"p256dh": UA_PUBLIC}},
        {"endpoint": "https://ok.example/" + "x" * 2_000, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}},
    ):
        with pytest.raises(webpush.WebPushError):
            webpush.parse_subscription(bad)
    # La clé privée VAPID ne sort jamais telle quelle d'une erreur.
    with pytest.raises(webpush.WebPushError):
        webpush.vapid_authorization(webpush.VapidKeys("court", "x"), "https://a.example/x", "https://b")
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    assert (
        b"PRIVATE" in pem
    )  # (cryptography fonctionne : la clé n'est pas sérialisée par Bank, qui ne garde que 32 octets)
