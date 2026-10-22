"""Notifications sur ton téléphone (Web Push) : Bank te prévient par le navigateur, sans messagerie ni compte
tiers. Le message est chiffré pour ton appareil (RFC 8291) ; le service de notification de l'éditeur du
navigateur ne voit qu'un blob illisible et l'adresse de ton serveur.

Chaque appareil abonné est un « compte relié » de la plateforme interne « webpush » : il profite du même circuit
que les messageries (alertes planifiées, heures calmes, dédoublonnage, pause après trop d'échecs), sans être une
messagerie (rien n'entre, on ne peut pas répondre). Les clés VAPID sont celles de ton serveur, créées à la
première demande et conservées chiffrées."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import ChannelConfig, ChannelLink
from app.security import audit, netfetch, webpush
from app.services import consents

PLATFORM = "webpush"
MAX_DEVICES = 8
MAX_BODY_CHARS = 600


class PushError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _config(db: Session, *, create: bool) -> ChannelConfig | None:
    row = db.get(ChannelConfig, PLATFORM)
    if row is None and create:
        keys = webpush.generate_vapid()
        row = ChannelConfig(
            platform=PLATFORM,
            enabled=False,
            secrets=json.dumps({"vapid_private": keys.private}),
            options=json.dumps({"vapid_public": keys.public}),
            hints="{}",
            privacy="redacted",
            status="off",
        )
        db.add(row)
        db.flush()
    return row


def vapid_keys(db: Session) -> webpush.VapidKeys:
    row = _config(db, create=True)
    assert row is not None  # créé à l'instant  # noqa: S101  # nosec B101
    return webpush.VapidKeys(json.loads(row.secrets)["vapid_private"], json.loads(row.options)["vapid_public"])


def public_key(db: Session) -> str:
    return vapid_keys(db).public


def _index(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:32]


def active_links(db: Session) -> list[ChannelLink]:
    return list(
        db.scalars(
            select(ChannelLink)
            .where(ChannelLink.platform == PLATFORM, ChannelLink.revoked_at.is_(None))
            .order_by(ChannelLink.id)
        )
    )


def _sync_enabled(db: Session) -> None:
    row = _config(db, create=True)
    assert row is not None  # noqa: S101  # nosec B101
    live = bool(active_links(db))
    row.enabled = live
    row.status = "online" if live else "off"


def subscribe(
    db: Session, *, raw: object, label: str, actor: str, resolver: netfetch.Resolver | None = None
) -> dict[str, Any]:
    """Un appareil de plus : abonnement vérifié (https, service public), accord exigé, nombre borné."""
    if not consents.granted(db, "push"):
        raise PushError("Il faut ton accord « Notifications sur ce téléphone » (Réglages › Confidentialité)", 403)
    try:
        sub = webpush.parse_subscription(raw)
        netfetch.check_url(sub.endpoint, resolver)  # https, port 443, hôte public : jamais ton réseau
    except (webpush.WebPushError, netfetch.FetchError) as exc:
        raise PushError(str(exc), 422) from exc
    index = _index(sub.endpoint)
    link = db.scalar(select(ChannelLink).where(ChannelLink.platform == PLATFORM, ChannelLink.user_index == index))
    clean_label = " ".join(label.split())[:80] or "Cet appareil"
    chat_ref = json.dumps({"endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth})
    if link is None:
        if len(active_links(db)) >= MAX_DEVICES:
            raise PushError(f"{MAX_DEVICES} appareils au plus : retires-en un d'abord", 409)
        link = ChannelLink(
            platform=PLATFORM,
            user_index=index,
            user_ref=sub.endpoint,
            chat_ref=chat_ref,
            label=clean_label,
            notify=True,
        )
        db.add(link)
    else:
        link.chat_ref, link.label, link.revoked_at = chat_ref, clean_label, None
        link.failures, link.last_error, link.notify = 0, None, True
    db.flush()
    _sync_enabled(db)
    audit.record(db, actor, "push.subscribe", device=link.id)
    return link_view(link)


def unsubscribe(db: Session, link_id: int, *, actor: str) -> None:
    link = db.get(ChannelLink, link_id)
    if link is None or link.platform != PLATFORM or link.revoked_at is not None:
        raise PushError("Appareil introuvable", 404)
    link.revoked_at = utcnow()
    db.flush()
    _sync_enabled(db)
    audit.record(db, actor, "push.unsubscribe", device=link_id)


def link_for(db: Session, chat_ref: str) -> ChannelLink | None:
    """L'appareil correspondant à une référence d'envoi (relu en base par son empreinte, jamais pris tel quel)."""
    try:
        endpoint = str(json.loads(chat_ref)["endpoint"])
    except (ValueError, KeyError, TypeError):
        return None
    return db.scalar(
        select(ChannelLink).where(
            ChannelLink.platform == PLATFORM,
            ChannelLink.user_index == _index(endpoint),
            ChannelLink.revoked_at.is_(None),
        )
    )


def link_view(link: ChannelLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "label": link.label,
        "linked_at": link.linked_at.isoformat(),
        "last_notified_at": link.last_notified_at.isoformat() if link.last_notified_at else None,
        "failures": link.failures,
        "last_error": link.last_error,
    }


def status(db: Session) -> dict[str, Any]:
    return {
        "public_key": public_key(db),
        "consent": consents.granted(db, "push"),
        "asked": consents.decided(db, "push"),
        "devices": [link_view(x) for x in active_links(db)],
        "max": MAX_DEVICES,
    }


def payload_for(text: str, *, public_url: str, tag: str = "bank") -> bytes:
    """Ce que le navigateur affichera : un titre, un corps court, l'écran à ouvrir. Jamais plus que l'alerte."""
    body = " ".join(text.split())[:MAX_BODY_CHARS]
    return json.dumps(
        {"title": "Bank", "body": body, "url": f"{public_url}/#/aujourdhui" if public_url else "/", "tag": tag[:40]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def deliver(
    db: Session,
    link: ChannelLink,
    text: str,
    *,
    public_url: str,
    urgent: bool = False,
    tag: str = "bank",
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
) -> None:
    """Chiffre et envoie une notification à un appareil. Un 404 ou 410 veut dire « abonnement mort » (révoqué
    côté navigateur) : l'appareil est retiré sans réessai."""
    keys = vapid_keys(db)
    ref = json.loads(link.chat_ref)
    sub = webpush.Subscription(str(ref["endpoint"]), str(ref["p256dh"]), str(ref["auth"]))
    body = webpush.encrypt(payload_for(text, public_url=public_url, tag=tag), sub)
    headers = webpush.request_headers(keys, sub, public_url or "https://bank.invalid", urgent=urgent)
    try:
        out = netfetch.post_bytes(sub.endpoint, body, headers=headers, http=http, resolver=resolver)
    except netfetch.FetchError as exc:
        raise PushError(str(exc), 502) from exc
    if out.status in (404, 410):
        link.revoked_at = utcnow()
        link.last_error = "Abonnement expiré côté navigateur : à réactiver sur l'appareil"
        db.flush()
        _sync_enabled(db)
        raise PushError("Abonnement expiré : réactive les notifications sur cet appareil", 410)
    if out.status == 413:
        raise PushError("Notification trop longue", 413)
    if out.status == 429:
        raise PushError("Service de notification saturé : réessai plus tard", 429)
    if out.status >= 400:
        raise PushError(f"Service de notification : réponse {out.status}", 502)
    link.last_notified_at = utcnow()
    link.failures, link.last_error = 0, None
    db.flush()


def probe_device(
    db: Session,
    link_id: int,
    *,
    actor: str,
    public_url: str,
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    link = db.get(ChannelLink, link_id)
    if link is None or link.platform != PLATFORM or link.revoked_at is not None:
        raise PushError("Appareil introuvable", 404)
    stamp = (now or utcnow()).strftime("%H:%M")
    text = f"Ça marche : Bank peut te prévenir ici ({stamp})."
    deliver(db, link, text, public_url=public_url, tag="test", http=http, resolver=resolver)
    audit.record(db, actor, "push.test", device=link_id)
    return {"ok": True, "device": link_view(link)}
