"""Assistant de connexion Enable Banking : l'identifiant d'application et la clé privée RSA se saisissent dans Bank
(plus de .env à éditer), chiffrés en base, vérifiés auprès de l'API avant d'être gardés. L'API ne rend qu'un indice
de l'identifiant et l'empreinte de la clé publique ; la clé privée ne ressort jamais (ni réponse, ni export, ni
journal, ni message d'erreur).

Une configuration du serveur (BANK_ENABLEBANKING_APP_ID + fichier de clé) prime : elle est montrée « configurée par
le serveur » et ne se modifie pas depuis l'application."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import write_lock
from app.models import BankConnection, ConnectionStatus, ProviderCredential
from app.providers.enablebanking import EnableBankingClient, ProviderError
from app.security import netfetch
from app.services.bank_sync import enablebanking_only
from app.services.settings_store import get_setting, set_setting

PROVIDER = "enablebanking"
CALLBACK_PATH = "/api/banks/callback"
CONSOLE_URL = "https://enablebanking.com/cp/applications"
MIN_PEM, MAX_PEM = 200, 16_000
MIN_BITS, MAX_BITS = 2048, 8192
APP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
# Problèmes qui empêchent de choisir sa banque ; « sandbox » n'est qu'un avertissement.
BLOCKING = frozenset({"bad_key", "unreachable", "redirect", "inactive"})


class SetupError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ saisie
def redirect_url(settings: Settings) -> str:
    """L'adresse de retour à coller dans le Control Panel : exactement l'adresse publique + /api/banks/callback."""
    return settings.public_url.rstrip("/") + CALLBACK_PATH


def clean_app_id(raw: str) -> str:
    value = (raw or "").strip()
    if value.lower().endswith(".pem"):  # nom du fichier collé tel quel
        value = value[:-4]
    if not APP_ID_RE.fullmatch(value):
        raise SetupError(
            "Cet identifiant ne ressemble pas à celui d'une application Enable Banking (lettres, chiffres et tirets, "
            "comme 1a2b3c4d-…). Recopie-le depuis la page de ton application, ou reprends le nom du fichier .pem."
        )
    return value


def clean_key(raw: str) -> bytes:
    """La clé privée RSA, relue et réécrite en PKCS#8 : seul ce qui est vraiment une clé RSA utilisable est gardé."""
    text = (raw or "").strip()
    if len(text) < MIN_PEM or len(text) > MAX_PEM:
        raise SetupError("Ce fichier n'a pas la taille d'une clé privée : dépose le .pem téléchargé à la création.")
    if "PRIVATE KEY-----" not in text:
        if "PUBLIC KEY-----" in text or "CERTIFICATE-----" in text:
            raise SetupError(
                "C'est une clé publique ou un certificat. Il faut la clé privée : le fichier .pem téléchargé quand tu "
                "as créé l'application."
            )
        raise SetupError("Ce n'est pas une clé privée au format PEM (« -----BEGIN PRIVATE KEY----- »).")
    try:
        key = serialization.load_pem_private_key(text.encode(), password=None)
    except TypeError:
        raise SetupError(
            "Cette clé est protégée par une phrase de passe : utilise le fichier .pem tel qu'Enable Banking l'a généré."
        ) from None
    except (ValueError, UnsupportedAlgorithm):
        raise SetupError("Cette clé privée est illisible : dépose le fichier .pem d'origine, tel quel.") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise SetupError("Enable Banking signe avec une clé RSA : celle-ci est d'un autre type.")
    if not MIN_BITS <= key.key_size <= MAX_BITS:
        raise SetupError(f"Clé RSA de {key.key_size} bits : il en faut entre {MIN_BITS} et {MAX_BITS}.")
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def fingerprint(pem: bytes) -> str:
    """Empreinte SHA-256 de la clé publique : reconnaître la clé sans jamais montrer la clé privée."""
    key = serialization.load_pem_private_key(pem, password=None)
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def short_fingerprint(full: str | None) -> str | None:
    return " ".join(full[i : i + 4] for i in range(0, 16, 4)) if full else None


def hint(app_id: str) -> str:
    return "…" + app_id[-6:] if len(app_id) > 6 else "…"


# ------------------------------------------------------------------ clients
def stored(db: Session) -> ProviderCredential | None:
    return db.scalar(select(ProviderCredential).where(ProviderCredential.provider == PROVIDER))


def make_client(
    settings: Settings,
    app_id: str,
    pem: bytes,
    *,
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
) -> EnableBankingClient:
    return EnableBankingClient(app_id, pem, settings.enablebanking_base_url, fetch_http=http, resolver=resolver)


def client_from_db(
    db: Session, settings: Settings, *, http: httpx.Client | None = None, resolver: netfetch.Resolver | None = None
) -> EnableBankingClient | None:
    """Le client des identifiants saisis dans l'application (au démarrage, quand le serveur n'en fournit pas)."""
    if settings.enablebanking_enabled:
        return None
    row = stored(db)
    if row is None:
        return None
    return make_client(settings, row.client_id, row.secret.encode(), http=http, resolver=resolver)


# ------------------------------------------------------------------ vérification
def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def check(client: EnableBankingClient, settings: Settings, now: datetime) -> dict[str, Any]:
    """Une requête signée (GET /application) : la clé signe-t-elle pour cet identifiant, l'application est-elle
    active, l'adresse de retour est-elle enregistrée ? Messages fixes : rien de la clé ni de la réponse brute."""
    expected = redirect_url(settings)
    issues: list[dict[str, str]] = []
    name: str | None = None
    environment: str | None = None
    try:
        info = client.get_application()
    except ProviderError as exc:
        if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
            issues.append(
                _issue(
                    "bad_key",
                    "Enable Banking ne reconnaît pas cette clé pour cet identifiant d'application. Vérifie que le "
                    "fichier .pem est bien celui téléchargé à la création de cette application (il porte son "
                    "identifiant pour nom), et que l'identifiant est recopié en entier.",
                )
            )
        else:
            issues.append(
                _issue(
                    "unreachable",
                    "Enable Banking ne répond pas pour l'instant. Tes réglages sont gardés : réessaie la vérification "
                    "dans quelques minutes.",
                )
            )
    else:
        name = str(info.get("name") or "")[:120] or None
        environment = str(info.get("environment") or "").upper()[:20] or None
        urls = info.get("redirect_urls")
        if isinstance(urls, list) and urls:
            known = {str(u).strip().rstrip("/") for u in urls}
            if expected.rstrip("/") not in known:
                issues.append(
                    _issue(
                        "redirect",
                        "L'adresse de retour de Bank n'est pas enregistrée dans ton application. Dans le Control "
                        f"Panel, ajoute exactement {expected} aux « Allowed redirect URLs », puis vérifie à nouveau.",
                    )
                )
        if info.get("active") is False:
            issues.append(
                _issue(
                    "inactive",
                    "Ton application n'est pas encore active. Dans le Control Panel, ouvre-la et clique « Activate by "
                    "linking accounts », puis lie tes comptes chez ta banque : c'est ce qui active le mode restreint, "
                    "gratuit et réservé à tes propres comptes.",
                )
            )
        if environment == "SANDBOX":
            issues.append(
                _issue(
                    "sandbox",
                    "C'est une application de test (Sandbox) : seules les banques de démonstration répondront. Pour "
                    "tes vrais comptes, crée une application Production.",
                )
            )
    return {
        "ok": not any(i["code"] in BLOCKING for i in issues),
        "issues": issues,
        "app_name": name,
        "environment": environment,
        "checked_at": now.isoformat(),
    }


def last_check(db: Session) -> dict[str, Any] | None:
    found = (get_setting(db, "bank_setup_check") or {}).get(PROVIDER)
    return found if isinstance(found, dict) else None


def remember_check(db: Session, result: dict[str, Any] | None) -> None:
    current = dict(get_setting(db, "bank_setup_check") or {})
    if result is None:
        current.pop(PROVIDER, None)
    else:
        current[PROVIDER] = result
    set_setting(db, "bank_setup_check", current)


# ------------------------------------------------------------------ état, enregistrement
def _server_identity(settings: Settings) -> tuple[str | None, str | None]:
    app_id = settings.enablebanking_app_id or ""
    fp: str | None = None
    if settings.enablebanking_key_file is not None:
        try:
            fp = fingerprint(settings.enablebanking_key_file.read_bytes())
        except (OSError, ValueError, TypeError):
            fp = None
    return (hint(app_id) if app_id else None), short_fingerprint(fp)


def status(db: Session, settings: Settings, *, enabled: bool) -> dict[str, Any]:
    row = stored(db)
    if settings.enablebanking_enabled:
        source: str | None = "server"
        app_hint, fp = _server_identity(settings)
    elif row is not None:
        source, app_hint, fp = "app", row.client_hint, short_fingerprint(row.secret_fingerprint)
    else:
        source = app_hint = fp = None
    active = db.scalars(
        select(BankConnection.id).where(BankConnection.status == ConnectionStatus.ACTIVE, enablebanking_only())
    ).all()
    return {
        "configured": source is not None and enabled,
        "source": source,
        "app_id_hint": app_hint,
        "key_fingerprint": fp,
        "updated_at": row.updated_at.isoformat() if source == "app" and row is not None else None,
        "redirect_url": redirect_url(settings),
        "console_url": CONSOLE_URL,
        "check": last_check(db) if source is not None else None,
        "connections": len(active),
    }


def save(db: Session, *, app_id: str, pem: bytes, now: datetime) -> ProviderCredential:
    write_lock(db)  # lire puis écrire : une seule ligne par agrégateur, même sous deux requêtes parallèles
    row = stored(db)
    if row is None:
        row = ProviderCredential(provider=PROVIDER, created_at=now)
        db.add(row)
    row.client_id = app_id
    row.secret = pem.decode()
    row.client_hint = hint(app_id)
    row.secret_fingerprint = fingerprint(pem)
    row.updated_at = now
    db.flush()
    return row


def remove(db: Session) -> bool:
    write_lock(db)
    row = stored(db)
    if row is None:
        return False
    db.delete(row)
    remember_check(db, None)
    db.flush()
    return True


def export(db: Session) -> dict[str, Any] | None:
    """Ce que l'export RGPD dit de l'assistant : l'indice et l'empreinte, jamais l'identifiant entier ni la clé."""
    row = stored(db)
    if row is None:
        return None
    return {
        "provider": row.provider,
        "app_id_hint": row.client_hint,
        "key_fingerprint": short_fingerprint(row.secret_fingerprint),
        "updated_at": row.updated_at.isoformat(),
    }
