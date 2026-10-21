"""Jetons d'agent : identité machine pour HERMÈS (ou tout autre agent IA).

* Format ``bnk_<43 caractères>`` (256 bits) : préfixe reconnaissable par les scanners de secrets.
* Seule l'empreinte SHA-256 est stockée (l'entropie rend un hachage lent inutile).
* Portées (scopes) minimales par défaut, confidentialité « redacted » par défaut, expiration obligatoire.
* Liste d'adresses IP autorisées optionnelle (ex. 192.168.1.112 pour HERMÈS).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AgentToken

# Préfixe public qui rend un jeton repérable (scanners de secrets, journaux) : pas un secret.
TOKEN_PREFIX = "bnk_"  # nosec B105

SCOPES: dict[str, str] = {
    "read": "Lire ta situation : dossier, prévisions, alertes, aides, patrimoine",
    "read:transactions": "Lire les opérations une par une",
    "simulate": "Lancer une simulation de tes droits aux aides",
    "write:onboarding": "Enregistrer tes réponses au questionnaire « Ma situation »",
    "write:categorize": "Reclasser une opération (Bank apprend de la correction)",
    "propose": "Proposer des changements (enveloppes, objectifs, réglages) que tu valides dans Bank",
}

PRESETS: dict[str, list[str]] = {
    "observateur": ["read"],
    "conseiller": ["read", "read:transactions", "simulate", "write:onboarding", "propose"],
    "gestionnaire": list(SCOPES),
}

PRIVACY_MODES: dict[str, str] = {
    "redacted": "Libellés anonymisés (noms de particuliers, IBAN, e-mails, téléphones masqués)",
    "aggregates": "Agrégats uniquement : aucune opération individuelle",
    "full": "Libellés bruts (à réserver à un modèle qui tourne chez toi)",
}

MAX_TTL_DAYS = 365


class AgentAuthError(Exception):
    """Jeton absent, inconnu, expiré, révoqué, ou adresse non autorisée — sans préciser lequel."""


@dataclass(frozen=True)
class AgentPrincipal:
    token_id: int
    name: str
    scopes: frozenset[str]
    privacy: str

    @property
    def actor(self) -> str:
        return f"agent:{self.name}"

    def can(self, scope: str) -> bool:
        return scope in self.scopes


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _network(value: str) -> str:
    # strict : « 192.168.1.112/24 » est refusé au lieu d'ouvrir silencieusement tout le /24.
    try:
        return str(ipaddress.ip_network(value.strip(), strict=True))
    except ValueError as exc:
        raise ValueError(f"Adresse ou réseau invalide : {value!r} (ex. 192.168.1.112 ou 192.168.1.0/24)") from exc


def issue_agent_token(
    db: Session,
    *,
    name: str,
    scopes: list[str],
    privacy: str = "redacted",
    allowed_ips: list[str] | None = None,
    ttl_days: int = 90,
) -> tuple[AgentToken, str]:
    unknown = set(scopes) - set(SCOPES)
    if unknown or not scopes:
        raise ValueError(f"Portées invalides : {sorted(unknown) or 'aucune'}")
    if privacy not in PRIVACY_MODES:
        raise ValueError("Mode de confidentialité invalide")
    if not 1 <= ttl_days <= MAX_TTL_DAYS:
        raise ValueError(f"Durée de validité entre 1 et {MAX_TTL_DAYS} jours")
    networks = [_network(ip) for ip in (allowed_ips or [])]
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = AgentToken(
        name=name[:64],
        token_hash=_hash(raw),
        prefix=raw[:10],
        scopes=json.dumps(sorted(set(scopes))),
        privacy=privacy,
        allowed_ips=json.dumps(networks),
        expires_at=utcnow() + timedelta(days=ttl_days),
    )
    db.add(token)
    db.flush()
    return token, raw


def _ip_allowed(ip: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(net) for net in allowed)


def authenticate_agent(db: Session, authorization: str | None, ip: str, now: datetime | None = None) -> AgentPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AgentAuthError
    raw = authorization[7:].strip()
    if not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
        raise AgentAuthError
    token = db.scalar(select(AgentToken).where(AgentToken.token_hash == _hash(raw)))
    now = now or utcnow()
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise AgentAuthError
    if not _ip_allowed(ip, json.loads(token.allowed_ips or "[]")):
        raise AgentAuthError
    return AgentPrincipal(
        token_id=token.id, name=token.name, scopes=frozenset(json.loads(token.scopes)), privacy=token.privacy
    )


def touch(db: Session, token_id: int) -> None:
    token = db.get(AgentToken, token_id)
    if token is not None:
        token.calls += 1
        token.last_used_at = utcnow()
