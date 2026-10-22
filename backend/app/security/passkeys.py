"""Passkeys (WebAuthn) : se connecter avec Face ID, une empreinte ou une clé, sans mot de passe, et sans que
rien de secret ne puisse être volé par une fausse page — l'origine et le domaine sont vérifiés par le protocole.

Bank n'exige aucune attestation (pas de traçage du modèle d'authentificateur), exige la vérification de
l'utilisateur (code, biométrie) et une clé « résidente » (passkey découvrable), et contrôle le compteur de
signature. Le défi est signé par Bank et ne vit que cinq minutes. Le mot de passe reste en secours, et reste
exigé pour les gestes sensibles."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

__all__ = ["base64url_to_bytes", "bytes_to_base64url"]
RP_NAME = "Bank"
CHALLENGE_TTL = 300
ALGORITHMS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]


class PasskeyError(Exception):
    pass


@dataclass(frozen=True)
class RelyingParty:
    rp_id: str
    origin: str


def relying_party(public_url: str) -> RelyingParty:
    """Le domaine et l'origine de Bank, tirés de l'adresse publique : une passkey n'est valable que pour eux."""
    parts = urlsplit(public_url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise PasskeyError("Adresse publique invalide")
    origin = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port and parts.port not in (80, 443) else "")
    return RelyingParty(host, origin)


def user_handle(user_id: int, username: str) -> bytes:
    """Identifiant opaque et stable de l'utilisateur côté authentificateur (jamais le nom en clair)."""
    return hashlib.sha256(f"bank-user:{user_id}:{username}".encode()).digest()


def new_challenge() -> bytes:
    return secrets.token_bytes(32)


@dataclass(frozen=True)
class RegisteredKey:
    credential_id: str  # base64url
    public_key: str  # base64url (COSE)
    sign_count: int
    aaguid: str
    transports: list[str]
    backup_eligible: bool
    backed_up: bool


def registration_options(
    rp: RelyingParty, *, user_id: int, username: str, challenge: bytes, existing_ids: list[str]
) -> str:
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=RP_NAME,
        user_name=username,
        user_id=user_handle(user_id, username),
        user_display_name=username,
        challenge=challenge,
        timeout=CHALLENGE_TTL * 1000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in existing_ids],
        supported_pub_key_algs=ALGORITHMS,
    )
    return options_to_json(options)


def verify_registration(rp: RelyingParty, *, credential: Any, challenge: bytes) -> RegisteredKey:
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_verification=True,
            supported_pub_key_algs=ALGORITHMS,
        )
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise PasskeyError("Passkey refusée : réponse invalide") from exc
    transports = []
    if isinstance(credential, dict):
        raw = credential.get("response", {}).get("transports") or []
        transports = [str(t)[:16] for t in raw if isinstance(t, str)][:6]
    return RegisteredKey(
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=int(verified.sign_count),
        aaguid=str(verified.aaguid),
        transports=transports,
        backup_eligible=bool(verified.credential_backed_up or verified.credential_device_type == "multi_device"),
        backed_up=bool(verified.credential_backed_up),
    )


def authentication_options(rp: RelyingParty, *, challenge: bytes) -> str:
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        challenge=challenge,
        timeout=CHALLENGE_TTL * 1000,
        allow_credentials=[],  # passkeys découvrables : l'authentificateur propose les comptes qu'il connaît
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options)


@dataclass(frozen=True)
class Assertion:
    credential_id: str
    new_sign_count: int
    user_handle: bytes | None


def credential_id_of(credential: Any) -> str:
    if isinstance(credential, dict):
        raw = credential.get("rawId") or credential.get("id")
        if isinstance(raw, str) and raw:
            return raw[:512]
    raise PasskeyError("Passkey refusée : identifiant manquant")


def verify_authentication(
    rp: RelyingParty, *, credential: Any, challenge: bytes, public_key: str, sign_count: int
) -> Assertion:
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            credential_public_key=base64url_to_bytes(public_key),
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise PasskeyError("Passkey refusée") from exc
    handle = None
    if isinstance(credential, dict):
        raw = credential.get("response", {}).get("userHandle")
        if isinstance(raw, str) and raw:
            try:
                handle = base64url_to_bytes(raw)
            except Exception as exc:
                raise PasskeyError("Passkey refusée") from exc
    return Assertion(bytes_to_base64url(verified.credential_id), int(verified.new_sign_count), handle)
