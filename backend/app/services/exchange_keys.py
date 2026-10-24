"""Clés d'API de plateforme en lecture seule : vérifiées à la connexion (droits renvoyés par la plateforme, ou
opérations inoffensives qui doivent échouer), chiffrées, jamais renvoyées ni exportées ni journalisées ; les
soldes relevés rejoignent le portefeuille avec les adresses suivies (lignes « plateforme », jamais modifiées à la
main) et se relèvent avec elles. Révocation en un geste : la clé et ses lignes disparaissent."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain import crypto as dc
from app.models import CryptoHolding, ExchangeKey
from app.providers import exchanges as ex
from app.security import audit
from app.services import consents
from app.services.crypto import CONSENT, MAX_HOLDINGS, snapshot

log = logging.getLogger("bank.crypto.exchanges")
MAX_KEYS = 10
MAX_KEY_CHARS = 400
MAX_SECRET_CHARS = 4_000  # une clé EC en PEM tient en quelques centaines de caractères


class ExchangeKeyError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _hint(api_key: str) -> str:
    tail = api_key.strip().rsplit("/", 1)[-1]
    return "…" + tail[-4:] if len(tail) >= 4 else "…"


def _clean_label(raw: str, platform: str) -> str:
    if dc.secret_kind(raw or ""):
        raise ExchangeKeyError("Ce nom contient un secret : ne colle jamais ta phrase de récupération ni ta clé")
    label = " ".join((raw or "").split())[:60]
    return label or ex.PLATFORMS[platform]["label"]


def _check_inputs(platform: str, api_key: str, api_secret: str) -> None:
    if platform not in ex.PLATFORMS:
        raise ExchangeKeyError("Plateforme inconnue")
    if not api_key.strip() or not api_secret.strip():
        raise ExchangeKeyError("Il faut la clé et son secret")
    if len(api_key) > MAX_KEY_CHARS or len(api_secret) > MAX_SECRET_CHARS:
        raise ExchangeKeyError("Clé ou secret trop longs")
    kind = dc.secret_kind(api_key) or (None if platform == "coinbase" else dc.secret_kind(api_secret))
    if kind:
        raise ExchangeKeyError(
            f"Ceci ressemble à {kind}, pas à une clé d'API : Bank ne garde jamais une clé de portefeuille. Crée une "
            "clé d'API en lecture seule sur la plateforme."
        )


def _refusal(rights: ex.Rights) -> str:
    what = (
        "retirer des fonds et passer des ordres"
        if rights.withdraw and rights.trade
        else "retirer des fonds"
        if rights.withdraw
        else "passer des ordres"
    )
    return (
        f"Cette clé peut {what} : Bank la refuse et ne la garde pas. Supprime-la sur la plateforme et crée une clé "
        "avec les seuls droits de lecture."
    )


def _apply_balances(db: Session, key: ExchangeKey, found: dict[str, Decimal], now: datetime) -> int:
    existing = {h.asset: h for h in db.scalars(select(CryptoHolding).where(CryptoHolding.exchange_key_id == key.id))}
    total = db.scalar(select(func.count()).select_from(CryptoHolding)) or 0
    written = 0
    for sym, qty in found.items():
        row = existing.pop(sym, None)
        if row is None:
            if total >= MAX_HOLDINGS:
                break  # le portefeuille est plein : on garde ce qui est relevé, le reste attend
            row = CryptoHolding(exchange_key_id=key.id, asset=sym)
            db.add(row)
            total += 1
        row.quantity, row.platform, row.foreign, row.updated_at = dc.qty_text(qty), key.label, key.foreign, now
        row.coingecko_id = None
        written += 1
    for row in existing.values():  # actif vendu ou retiré depuis
        row.quantity, row.updated_at = "0", now
    key.synced_at, key.sync_error = now, None
    db.flush()
    return written


def connect(
    db: Session,
    *,
    actor: str,
    platform: str,
    label: str,
    api_key: str,
    api_secret: str,
    foreign: bool,
    transport: ex.Transport,
    now: datetime,
) -> ExchangeKey:
    """Vérifie les droits, relève les soldes, puis seulement garde la clé (chiffrée)."""
    consents.require(db, CONSENT)
    _check_inputs(platform, api_key, api_secret)
    name = _clean_label(label, platform)
    if (db.scalar(select(func.count()).select_from(ExchangeKey)) or 0) >= MAX_KEYS:
        raise ExchangeKeyError(f"{MAX_KEYS} clés au plus")
    api_key, api_secret = api_key.strip(), api_secret.strip()
    try:
        client = ex.make(platform, api_key, api_secret, transport)
        rights = client.rights()
        if not rights.read:
            raise ex.ExchangeError("Cette clé ne peut pas lire les soldes : donne-lui le droit de lecture seulement")
        if not rights.read_only:
            audit.record(
                db, actor, "crypto.exchange.refused", platform=platform, trade=rights.trade, withdraw=rights.withdraw
            )
            raise ExchangeKeyError(_refusal(rights))
        found = client.balances()
    except ex.ExchangeError as exc:
        audit.record(db, actor, "crypto.exchange.failed", platform=platform)
        raise ExchangeKeyError(str(exc), 400) from exc
    key = ExchangeKey(
        platform=platform,
        label=name,
        api_key=api_key,
        api_secret=api_secret,
        key_hint=_hint(api_key),
        foreign=bool(foreign),
        rights=json.dumps(rights.to_dict()),
        verified_at=now,
    )
    db.add(key)
    db.flush()
    written = _apply_balances(db, key, found, now)
    snapshot(db, now.date())
    audit.record(db, actor, "crypto.exchange.add", platform=platform, key=key.id, assets=written, source=rights.source)
    return key


def revoke(db: Session, key_id: int, *, actor: str, now: datetime) -> None:
    key = db.get(ExchangeKey, key_id)
    if key is None:
        raise ExchangeKeyError("Clé introuvable", 404)
    db.execute(delete(CryptoHolding).where(CryptoHolding.exchange_key_id == key_id))
    db.delete(key)
    db.flush()
    snapshot(db, now.date())
    audit.record(db, actor, "crypto.exchange.revoke", platform=key.platform, key=key_id)


def sync_one(db: Session, key: ExchangeKey, *, transport: ex.Transport, now: datetime) -> bool:
    try:
        client = ex.make(key.platform, key.api_key, key.api_secret, transport)
        found = client.balances()
    except ex.ExchangeError as exc:
        key.sync_error = str(exc)[:200]  # jamais la clé : les messages du fournisseur n'en citent rien
        db.flush()
        return False
    _apply_balances(db, key, found, now)
    return True


def sync_all(db: Session, *, transport: ex.Transport, now: datetime, actor: str) -> dict[str, int]:
    """Relève toutes les clés ; sans accord « crypto_network », rien ne part."""
    if not consents.granted(db, CONSENT):
        return {"exchanges_synced": 0, "exchanges_failed": 0}
    ok = failed = 0
    for key in list(db.scalars(select(ExchangeKey).order_by(ExchangeKey.id))):
        if sync_one(db, key, transport=transport, now=now):
            ok += 1
        else:
            failed += 1
    if ok or failed:
        snapshot(db, now.date())
        audit.record(db, actor, "crypto.exchange.sync", synced=ok, failed=failed)
    return {"exchanges_synced": ok, "exchanges_failed": failed}


def _key_view(db: Session, key: ExchangeKey) -> dict[str, Any]:
    rows = list(db.scalars(select(CryptoHolding).where(CryptoHolding.exchange_key_id == key.id)))
    try:
        rights = json.loads(key.rights)
    except ValueError:
        rights = {}
    return {
        "id": key.id,
        "platform": key.platform,
        "platform_label": ex.PLATFORMS.get(key.platform, {}).get("label", key.platform),
        "label": key.label,
        "key_hint": key.key_hint,
        "foreign": key.foreign,
        "rights": rights,
        "verified_at": key.verified_at.isoformat(),
        "synced_at": key.synced_at.isoformat() if key.synced_at else None,
        "error": key.sync_error,
        "assets": [{"asset": h.asset, "quantity": h.quantity} for h in rows if h.quantity not in ("0", "")],
    }


def view(db: Session) -> dict[str, Any]:
    return {
        "keys": [_key_view(db, k) for k in db.scalars(select(ExchangeKey).order_by(ExchangeKey.id))],
        "platforms": [{"key": k, **v} for k, v in ex.PLATFORMS.items()],
        "network_consent": consents.granted(db, CONSENT),
        "max_keys": MAX_KEYS,
    }


def export(db: Session) -> list[dict[str, Any]]:
    """Pour l'export RGPD : les plateformes reliées, jamais la clé ni le secret."""
    return [
        {"platform": k.platform, "label": k.label, "foreign": k.foreign, "verified_at": k.verified_at.isoformat()}
        for k in db.scalars(select(ExchangeKey).order_by(ExchangeKey.id))
    ]
