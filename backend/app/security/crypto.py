"""Chiffrement applicatif des champs sensibles + index aveugles + jetons signés.

Conception
----------
* Une clé maîtresse (32 octets, hors base) -> sous-clés dérivées par HKDF-SHA256, une par usage
  (chiffrement, index aveugle, signature de jetons, chaîne d'audit). Compromettre un usage
  n'expose pas les autres.
* Chiffrement AES-256-GCM, nonce aléatoire 96 bits, *AAD = contexte* ("account.iban"…) : un
  chiffré copié d'une colonne à une autre ne se déchiffre pas (attaque par permutation bloquée).
* Format versionné ``enc:<kid>:<base64url(nonce||ct||tag)>`` -> rotation de clé sans downtime :
  on déchiffre avec n'importe quelle clé connue, on chiffre toujours avec la clé active.
* Index aveugle = HMAC-SHA256 tronqué : permet de rechercher/dédoublonner un IBAN sans le
  stocker en clair.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PREFIX = "enc"
_SALT = b"bank/keyring/v1"


class CryptoError(Exception):
    """Erreur cryptographique — volontairement sans détail exploitable."""


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _derive(master: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=purpose.encode()).derive(master)


def normalize_identifier(value: str) -> str:
    """Normalise un identifiant (IBAN…) avant index : sans espaces, majuscules, NFKC."""
    return "".join(unicodedata.normalize("NFKC", value).split()).upper()


class Keyring:
    def __init__(self, master_keys: Mapping[str, bytes]) -> None:
        if not master_keys:
            raise CryptoError("Aucune clé maîtresse")
        self._active = next(iter(master_keys))
        self._enc = {kid: AESGCM(_derive(k, "field-encryption")) for kid, k in master_keys.items()}
        active_master = master_keys[self._active]
        self._bidx_key = _derive(active_master, "blind-index")
        self._token_key = _derive(active_master, "signed-tokens")
        self.audit_key = _derive(active_master, "audit-chain")

    @property
    def active_kid(self) -> str:
        return self._active

    # ------------------------------------------------------------ chiffrement
    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(12)
        ct = self._enc[self._active].encrypt(nonce, plaintext.encode("utf-8"), context.encode())
        return f"{_PREFIX}:{self._active}:{_b64e(nonce + ct)}"

    def decrypt(self, token: str, context: str) -> str:
        try:
            prefix, kid, payload = token.split(":", 2)
            if prefix != _PREFIX:
                raise ValueError
            blob = _b64d(payload)
            aead = self._enc[kid]
            return aead.decrypt(blob[:12], blob[12:], context.encode()).decode("utf-8")
        except (ValueError, KeyError, InvalidTag, UnicodeDecodeError) as exc:
            raise CryptoError("Déchiffrement impossible") from exc

    def needs_reencrypt(self, token: str) -> bool:
        return not token.startswith(f"{_PREFIX}:{self._active}:")

    # ------------------------------------------------------------ fichiers (coffre)
    # Format binaire versionné : b"BKV1" | len(kid) (1 octet) | kid | nonce (12) | chiffré+tag. Le contexte (AAD)
    # lie le chiffré à son document : un fichier recopié sous un autre identifiant ne se déchiffre pas.
    _BLOB_MAGIC = b"BKV1"

    def encrypt_bytes(self, data: bytes, context: str) -> bytes:
        kid = self._active.encode()
        nonce = os.urandom(12)
        ct = self._enc[self._active].encrypt(nonce, data, context.encode())
        return self._BLOB_MAGIC + bytes([len(kid)]) + kid + nonce + ct

    def decrypt_bytes(self, blob: bytes, context: str) -> bytes:
        try:
            if not blob.startswith(self._BLOB_MAGIC):
                raise ValueError("format")
            n = blob[4]
            kid = blob[5 : 5 + n].decode("ascii")
            body = blob[5 + n :]
            return self._enc[kid].decrypt(body[:12], body[12:], context.encode())
        except (ValueError, KeyError, IndexError, InvalidTag, UnicodeDecodeError) as exc:
            raise CryptoError("Déchiffrement impossible") from exc

    # ------------------------------------------------------------ index aveugle
    def blind_index(self, value: str, context: str) -> str:
        msg = f"{context}\x00{normalize_identifier(value)}".encode()
        return hmac.new(self._bidx_key, msg, hashlib.sha256).hexdigest()[:32]

    # ------------------------------------------------------------ jetons signés
    def sign_token(self, payload: dict[str, Any], ttl_seconds: int, purpose: str) -> str:
        body = {**payload, "_p": purpose, "_exp": int(time.time()) + ttl_seconds}
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        mac = hmac.new(self._token_key, raw, hashlib.sha256).digest()
        return f"{_b64e(raw)}.{_b64e(mac)}"

    def verify_token(self, token: str, purpose: str) -> dict[str, Any]:
        try:
            raw_b64, mac_b64 = token.split(".", 1)
            raw = _b64d(raw_b64)
            expected = hmac.new(self._token_key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64d(mac_b64)):
                raise ValueError("mac")
            body: dict[str, Any] = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CryptoError("Jeton invalide") from exc
        if body.get("_p") != purpose or int(body.get("_exp", 0)) < int(time.time()):
            raise CryptoError("Jeton invalide ou expiré")
        return body


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --------------------------------------------------------------------- registre global
# Le TypeDecorator SQLAlchemy n'a pas accès à l'app : on expose un keyring de process,
# initialisé une seule fois au démarrage (create_app) ou par les tests.
_KEYRING: Keyring | None = None


def install_keyring(keyring: Keyring) -> None:
    global _KEYRING
    _KEYRING = keyring


def get_keyring() -> Keyring:
    if _KEYRING is None:
        raise CryptoError("Keyring non initialisé")
    return _KEYRING
