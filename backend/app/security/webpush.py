"""Web Push, à la main et à la norme : RFC 8291 (chiffrement du message, « aes128gcm ») et RFC 8292 (VAPID, la
signature qui prouve au service de notification que c'est bien ton serveur qui envoie).

Le service de notification (Google, Apple, Mozilla — celui du navigateur) relaie un message qu'il ne peut pas
lire : chiffré pour la clé de ton navigateur, avec un secret que seul lui connaît. Bank n'a aucune dépendance de
plus : des courbes et un HKDF de `cryptography`, et un JSON. Rien ici ne touche au réseau."""

from __future__ import annotations

import base64
import hmac
import json
import os
import struct
import time
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

RECORD_SIZE = 4096
MAX_PAYLOAD = 3_000  # au-delà, Apple et Mozilla refusent ; une alerte tient en bien moins
VAPID_TTL = 12 * 3600  # une signature vaut au plus 24 h selon la norme ; on reste large en dessous


class WebPushError(ValueError):
    pass


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------ clés du serveur (VAPID)
@dataclass(frozen=True)
class VapidKeys:
    private: str  # base64url des 32 octets du scalaire
    public: str  # base64url du point non compressé (65 octets) : ce que le navigateur reçoit


def generate_vapid() -> VapidKeys:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return VapidKeys(b64u(private), b64u(public))


def _private_key(private_b64u: str) -> ec.EllipticCurvePrivateKey:
    try:
        raw = b64u_decode(private_b64u)
    except (ValueError, TypeError) as exc:
        raise WebPushError("Clé VAPID invalide") from exc
    if len(raw) != 32:
        raise WebPushError("Clé VAPID invalide")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def vapid_authorization(keys: VapidKeys, endpoint: str, subject: str, now: float | None = None) -> str:
    """L'en-tête Authorization : un JWT ES256 pour l'origine du service de notification, et la clé publique."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        raise WebPushError("Point de terminaison invalide")
    audience = f"https://{parts.hostname}" + (f":{parts.port}" if parts.port and parts.port != 443 else "")
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    exp = int((now if now is not None else time.time()) + VAPID_TTL)
    claims = b64u(json.dumps({"aud": audience, "exp": exp, "sub": subject}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    der = _private_key(keys.private).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={keys.public}"


# ------------------------------------------------------------------ chiffrement du message (RFC 8291)
@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str  # clé publique du navigateur (base64url, 65 octets)
    auth: str  # secret d'authentification (base64url, 16 octets)


def parse_subscription(raw: object) -> Subscription:
    """Ce que le navigateur renvoie (PushSubscription.toJSON()) : vérifié champ par champ."""
    if not isinstance(raw, dict):
        raise WebPushError("Abonnement illisible")
    endpoint = raw.get("endpoint")
    raw_keys = raw.get("keys")
    keys: dict[object, object] = raw_keys if isinstance(raw_keys, dict) else {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2_000:
        raise WebPushError("Point de terminaison invalide (https attendu)")
    if not isinstance(p256dh, str) or not isinstance(auth, str):
        raise WebPushError("Clés d'abonnement manquantes")
    try:
        if len(b64u_decode(p256dh)) != 65 or len(b64u_decode(auth)) != 16:
            raise WebPushError("Clés d'abonnement invalides")
    except (ValueError, TypeError) as exc:
        raise WebPushError("Clés d'abonnement invalides") from exc
    return Subscription(endpoint, p256dh.strip(), auth.strip())


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(prk)


def encrypt(
    plaintext: bytes,
    sub: Subscription,
    *,
    salt: bytes | None = None,
    sender_private: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Le corps d'une requête Web Push (« aes128gcm ») : sel, taille d'enregistrement, clé éphémère, puis le
    message chiffré pour le navigateur. ``salt`` et ``sender_private`` ne se fixent qu'en test (vecteur RFC)."""
    if len(plaintext) > MAX_PAYLOAD:
        raise WebPushError("Message trop long pour une notification")
    ua_public_raw = b64u_decode(sub.p256dh)
    auth_secret = b64u_decode(sub.auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_raw)
    as_private = sender_private or ec.generate_private_key(ec.SECP256R1())
    as_public_raw = as_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = as_private.exchange(ec.ECDH(), ua_public)
    prk_key = _hkdf_extract(auth_secret, shared)
    ikm = _hkdf_expand(prk_key, b"WebPush: info\x00" + ua_public_raw + as_public_raw, 32)
    salt = salt if salt is not None else os.urandom(16)
    if len(salt) != 16:
        raise WebPushError("Sel invalide")
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)  # 0x02 : dernier enregistrement
    header = salt + struct.pack(">I", RECORD_SIZE) + bytes([len(as_public_raw)]) + as_public_raw
    return header + ciphertext


def request_headers(
    keys: VapidKeys, sub: Subscription, subject: str, *, ttl: int = 86_400, urgent: bool = False
) -> dict[str, str]:
    return {
        "Authorization": vapid_authorization(keys, sub.endpoint, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "high" if urgent else "normal",
    }
