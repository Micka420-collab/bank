"""Bank dans tes messageries : ce qu'on décide quand un message arrive, et comment chaque messagerie est réglée.

Aucune E/S réseau ici : les adaptateurs (app/channels) écoutent et envoient, ce module décide —
- configuration : jetons chiffrés en base, jamais renvoyés (un préfixe sert à les reconnaître) ;
- appairage : un code à usage unique, généré dans Bank après confirmation du mot de passe, envoyé par toi au
  robot depuis ton compte ; un inconnu n'obtient aucune réponse (il ne sait même pas que Bank existe) ;
- message reçu : dédoublonné (les plateformes réessaient), refusé s'il contient un secret, puis commande
  (/budget, /missions, /demarches…) ou question à l'assistant — avec la confidentialité la plus stricte des deux
  (celle du modèle, celle de la messagerie) ;
- rien ne se transmet, ne se confirme ni ne se règle depuis une messagerie : on renvoie vers l'application.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets as pysecrets
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.channels.base import Inbound
from app.channels.text import secret_kind
from app.db import utcnow
from app.domain import procedures as dp
from app.domain.money import format_eur
from app.models import ChannelConfig, ChannelLink, ChannelSeen, Procedure
from app.security import audit
from app.security.auth import SlidingWindowLimiter
from app.security.crypto import get_keyring
from app.services import analytics, consents, opportunities
from app.services import assistant as assistant_service
from app.services.settings_store import get_setting, set_setting

PRIVACY_MODES = ("full", "redacted", "aggregates")
PAIRING_TTL = timedelta(minutes=15)
PAIRING_MAX_ATTEMPTS = 5
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans I, O, 0, 1 : pas de confusion à la lecture
MAX_INBOUND_CHARS = 2_000
SEEN_RETENTION = timedelta(days=3)
_IN_PROGRESS = (*sorted(dp.OPEN_STAGES), "sent", "waiting")  # tout ce qui n'est ni fini ni abandonné


class ChannelError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    secret: bool
    pattern: str
    example: str
    required: bool = True


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    inbound: bool = True  # on peut lui parler (sinon : alertes seulement)
    e2e: bool = False  # chiffré de bout en bout : ce qui y passe n'est lisible que par toi


_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?"
PLATFORMS: dict[str, PlatformSpec] = {
    "telegram": PlatformSpec(
        "telegram",
        "Telegram",
        (FieldSpec("token", "Jeton du robot", True, r"\d{5,12}:[A-Za-z0-9_-]{30,64}", "123456789:AAH…"),),
    ),
    "whatsapp": PlatformSpec(
        "whatsapp",
        "WhatsApp",
        (
            FieldSpec("phone_number_id", "Identifiant du numéro", False, r"\d{6,20}", "109876543210987"),
            FieldSpec("access_token", "Jeton d'accès", True, r"[A-Za-z0-9_-]{40,600}", "EAAG…"),
            FieldSpec(
                "app_secret", "Clé secrète de l'application", True, r"[a-f0-9]{32}", "32 caractères hexadécimaux"
            ),
        ),
    ),
    "signal": PlatformSpec(
        "signal",
        "Signal",
        (
            FieldSpec("url", "Adresse du service signal-cli", False, rf"https?://{_HOST}/?", "http://signal:8080"),
            FieldSpec("account", "Numéro du compte Signal de Bank", False, r"\+\d{8,15}", "+33612345678"),
        ),
        e2e=True,
    ),
    "discord": PlatformSpec(
        "discord",
        "Discord",
        (
            FieldSpec(
                "token",
                "Jeton du robot",
                True,
                r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}",
                "MTA4…",
            ),
        ),
    ),
    "slack": PlatformSpec(
        "slack",
        "Slack",
        (
            FieldSpec("bot_token", "Jeton du robot (xoxb-…)", True, r"xoxb-[A-Za-z0-9-]{20,200}", "xoxb-…"),
            FieldSpec("app_token", "Jeton de l'application (xapp-…)", True, r"xapp-[A-Za-z0-9-]{20,200}", "xapp-…"),
        ),
    ),
    "ntfy": PlatformSpec(
        "ntfy",
        "ntfy",
        (
            FieldSpec("server", "Serveur ntfy", False, rf"https://{_HOST}/?", "https://ntfy.sh", required=False),
            FieldSpec("token", "Jeton d'accès (facultatif)", True, r"tk_[A-Za-z0-9]{20,40}", "tk_…", required=False),
        ),
        inbound=False,
    ),
}


# ------------------------------------------------------------------ configuration
def _load(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def secrets_of(row: ChannelConfig) -> dict[str, str]:
    return {k: str(v) for k, v in _load(row.secrets).items()}


def options_of(row: ChannelConfig) -> dict[str, Any]:
    return _load(row.options)


def _hint(value: str) -> str:
    return f"{value[:6]}…" if len(value) > 12 else "•••"


def spec(platform: str) -> PlatformSpec:
    found = PLATFORMS.get(platform)
    if found is None:
        raise ChannelError("Messagerie inconnue", 404)
    return found


def missing_fields(row: ChannelConfig | None, platform: str) -> list[str]:
    s = spec(platform)
    if row is None:
        return [f.label for f in s.fields if f.required]
    sec, opt = secrets_of(row), options_of(row)
    return [f.label for f in s.fields if f.required and not (sec if f.secret else opt).get(f.key)]


def configure(
    db: Session,
    platform: str,
    *,
    values: dict[str, str],
    privacy: str | None,
    enabled: bool | None,
    actor: str,
) -> ChannelConfig:
    """Enregistre les réglages d'une messagerie. Un champ laissé vide garde la valeur enregistrée ; un secret
    n'est jamais renvoyé. Changer un jeton relance la connexion."""
    s = spec(platform)
    row = db.get(ChannelConfig, platform)
    if row is None:
        row = ChannelConfig(platform=platform, secrets="{}", options="{}", hints="{}")
        db.add(row)
    sec, opt, hints = secrets_of(row), options_of(row), _load(row.hints)
    changed: list[str] = []
    known = {f.key for f in s.fields}
    for key in values:
        if key not in known:
            raise ChannelError(f"Champ inconnu : {key}")
    for f in s.fields:
        raw = (values.get(f.key) or "").strip()
        if not raw:
            continue
        if not re.fullmatch(f.pattern, raw):
            raise ChannelError(f"{f.label} : format inattendu (exemple : {f.example})")
        value = raw.rstrip("/") if f.key in ("url", "server") else raw
        if f.secret:
            if sec.get(f.key) != value:
                sec[f.key] = value
                hints[f.key] = _hint(value)
                changed.append(f.key)
        elif opt.get(f.key) != value:
            opt[f.key] = value
            changed.append(f.key)
    # Ce que Bank choisit pour toi : le jeton de vérification du webhook WhatsApp (à recopier chez Meta) et un
    # sujet ntfy impossible à deviner (sur ntfy.sh, qui connaît le sujet peut lire les alertes).
    if platform == "whatsapp" and not opt.get("verify_token"):
        opt["verify_token"] = pysecrets.token_urlsafe(24)
    if platform == "ntfy":
        opt.setdefault("server", "https://ntfy.sh")
        if not sec.get("topic"):
            sec["topic"] = "bank-" + pysecrets.token_urlsafe(18).replace("_", "x").replace("-", "y")
            changed.append("topic")
    if privacy is not None:
        if privacy not in PRIVACY_MODES:
            raise ChannelError("Confidentialité : full, redacted ou aggregates")
        row.privacy = privacy
    row.secrets, row.options, row.hints = json.dumps(sec), json.dumps(opt), json.dumps(hints)
    if enabled is not None:
        if enabled and (missing := missing_fields(row, platform)):
            raise ChannelError("Il manque : " + ", ".join(missing))
        row.enabled = enabled
    if changed:
        row.status, row.last_error, row.bot_name = "off", None, None
        if platform == "telegram":
            row.cursor = None  # nouveau robot : on repart de ses messages à lui
    # Un autre robot (ou un autre numéro) n'est plus celui que tes comptes ont vérifié : ils sont déliés et se
    # relient par un nouveau code — donc avec ton mot de passe. Un jeton WhatsApp renouvelé, lui, ne délie rien.
    if set(changed) & IDENTITY_FIELDS.get(platform, set()):
        revoked = _revoke_links(db, platform, now=utcnow())
        if revoked:
            audit.record(db, actor, "channel.links_reset", platform=platform, links=revoked)
    db.flush()
    if platform == "ntfy":
        _sync_ntfy_link(db, row)
    audit.record(
        db,
        actor,
        "channel.configured",
        platform=platform,
        fields=sorted(changed),
        enabled=row.enabled,
        privacy=row.privacy,
    )
    return row


def _sync_ntfy_link(db: Session, row: ChannelConfig) -> None:
    """ntfy ne sait qu'afficher des alertes : son « compte relié » est le sujet lui-même, créé avec la config."""
    topic = secrets_of(row).get("topic", "")
    index = get_keyring().blind_index(topic, "channel.ntfy")
    for link in db.scalars(select(ChannelLink).where(ChannelLink.platform == "ntfy")):
        if link.user_index != index:
            db.delete(link)
    if topic and db.scalar(select(ChannelLink).where(ChannelLink.platform == "ntfy")) is None:
        db.add(ChannelLink(platform="ntfy", user_index=index, user_ref=topic, chat_ref=topic, label="Alertes ntfy"))
    db.flush()


# Ce qui fait l'identité du robot (ou du numéro) auquel tes comptes se sont reliés.
IDENTITY_FIELDS: dict[str, set[str]] = {
    "telegram": {"token"},
    "discord": {"token"},
    "slack": {"bot_token"},
    "whatsapp": {"phone_number_id"},
    "signal": {"account", "url"},
}


def _revoke_links(db: Session, platform: str, *, now: datetime) -> int:
    links = list(
        db.scalars(select(ChannelLink).where(ChannelLink.platform == platform, ChannelLink.revoked_at.is_(None)))
    )
    for link in links:
        link.revoked_at, link.conversation_id = now, None
    return len(links)


def remove(db: Session, platform: str, *, actor: str) -> None:
    spec(platform)
    row = db.get(ChannelConfig, platform)
    if row is not None:
        db.delete(row)
    db.execute(delete(ChannelLink).where(ChannelLink.platform == platform))
    db.flush()
    audit.record(db, actor, "channel.removed", platform=platform)


def record_check(db: Session, platform: str, info: dict[str, Any], *, now: datetime) -> None:
    """Ce que la plateforme dit du robot (son nom, de quoi bâtir un lien direct) : rien de secret."""
    row = db.get(ChannelConfig, platform)
    if row is None:
        return
    row.bot_name = str(info.get("name") or "")[:120] or None
    opt = options_of(row)
    for key in ("username", "app_id", "display_phone", "team"):
        if info.get(key):
            opt[key] = str(info[key])[:120]
    row.options = json.dumps(opt)
    row.last_ok_at = now


def set_status(db: Session, platform: str, status: str, *, error: str | None = None, now: datetime) -> None:
    row = db.get(ChannelConfig, platform)
    if row is None:
        return
    row.status = status
    if status == "online":
        row.last_ok_at, row.last_error = now, None
    elif error:
        row.last_error = error[:300]


def active_platforms(db: Session) -> dict[str, ChannelConfig]:
    """Messageries à faire tourner : accord donné, activées, complètes."""
    if not consents.granted(db, "channels"):
        return {}
    rows = db.scalars(select(ChannelConfig).where(ChannelConfig.enabled.is_(True)))
    return {r.platform: r for r in rows if r.platform in PLATFORMS and not missing_fields(r, r.platform)}


# ------------------------------------------------------------------ vue pour l'écran « Messageries »
def _links_view(db: Session, platform: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ChannelLink)
        .where(ChannelLink.platform == platform, ChannelLink.revoked_at.is_(None))
        .order_by(ChannelLink.id)
    )
    return [
        {
            "id": r.id,
            "label": r.label,
            "notify": r.notify,
            "linked_at": r.linked_at.isoformat(),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "last_notified_at": r.last_notified_at.isoformat() if r.last_notified_at else None,
            "failures": r.failures,
            "last_error": r.last_error,
        }
        for r in rows
    ]


def view(
    db: Session, *, public_url: str, server_enabled: bool, now: datetime, webhook_base: str | None = None
) -> dict[str, Any]:
    state = get_setting(db, "channel_pairing") or {}
    expires = _expiry(state)
    out: list[dict[str, Any]] = []
    for key, s in PLATFORMS.items():
        row = db.get(ChannelConfig, key)
        sec = secrets_of(row) if row else {}
        opt = options_of(row) if row else {}
        hints = _load(row.hints) if row else {}
        extra: dict[str, Any] = {}
        if key == "whatsapp":
            extra = {
                "webhook_url": f"{webhook_base or public_url}/api/channels/whatsapp/webhook",
                "verify_token": opt.get("verify_token"),
                "display_phone": opt.get("display_phone"),
            }
        elif key == "telegram":
            extra = {"username": opt.get("username")}
        elif key == "discord":
            extra = {"app_id": opt.get("app_id")}
        elif key == "signal":
            extra = {"account": opt.get("account")}
        elif key == "ntfy" and sec.get("topic"):
            # Le sujet est l'adresse d'abonnement : tu dois le recopier dans l'application ntfy de ton téléphone.
            extra = {"subscribe_url": f"{opt.get('server', 'https://ntfy.sh')}/{sec['topic']}"}
        out.append(
            {
                "key": key,
                "label": s.label,
                "inbound": s.inbound,
                "e2e": s.e2e,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "secret": f.secret,
                        "required": f.required,
                        "example": f.example,
                        "set": bool((sec if f.secret else opt).get(f.key)),
                        "hint": hints.get(f.key) if f.secret else None,
                        "value": None if f.secret else opt.get(f.key),
                    }
                    for f in s.fields
                ],
                "configured": row is not None and not missing_fields(row, key),
                "enabled": bool(row and row.enabled),
                "status": row.status if row else "off",
                "bot_name": row.bot_name if row else None,
                "privacy": row.privacy if row else "redacted",
                "last_error": row.last_error if row else None,
                "last_ok_at": row.last_ok_at.isoformat() if row and row.last_ok_at else None,
                "ignored": row.ignored if row else 0,
                "links": _links_view(db, key),
                "extra": extra,
            }
        )
    return {
        "server_enabled": server_enabled,
        "consent": consents.granted(db, "channels"),
        "pairing": {
            "active": expires is not None and expires > now,
            "expires_at": expires.isoformat() if expires else None,
        },
        "quiet_hours": get_setting(db, "channel_quiet_hours"),
        "weekly": bool(get_setting(db, "channel_weekly")),
        "platforms": out,
    }


def set_preferences(db: Session, *, quiet_hours: list[int] | None, weekly: bool | None, actor: str) -> None:
    if quiet_hours is not None:
        if len(quiet_hours) != 2 or not all(0 <= h <= 23 for h in quiet_hours):
            raise ChannelError("Heures calmes : deux heures entre 0 et 23")
        set_setting(db, "channel_quiet_hours", list(quiet_hours))
    if weekly is not None:
        set_setting(db, "channel_weekly", weekly)
    audit.record(db, actor, "channel.preferences", quiet_hours=quiet_hours, weekly=weekly)


def update_link(db: Session, link_id: int, *, notify: bool | None, actor: str) -> ChannelLink:
    link = db.get(ChannelLink, link_id)
    if link is None or link.revoked_at is not None:
        raise ChannelError("Compte relié introuvable", 404)
    if notify is not None:
        link.notify = notify
        if notify:
            link.failures, link.last_error = 0, None
    audit.record(db, actor, "channel.link_updated", platform=link.platform, link=link.id, notify=link.notify)
    return link


def unlink(db: Session, link_id: int, *, actor: str, now: datetime) -> None:
    link = db.get(ChannelLink, link_id)
    if link is None or link.revoked_at is not None:
        raise ChannelError("Compte relié introuvable", 404)
    link.revoked_at = now
    link.conversation_id = None
    audit.record(db, actor, "channel.unlinked", platform=link.platform, link=link.id)


# ------------------------------------------------------------------ appairage
def _code_hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _expiry(state: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(state["expires_at"])) if state.get("hash") else None
    except (KeyError, ValueError):
        return None


def start_pairing(db: Session, *, now: datetime, actor: str) -> dict[str, Any]:
    """Un code à usage unique (8 caractères, 15 minutes, 5 essais) : tu l'envoies au robot depuis ton compte. Seule
    une empreinte salée est gardée ; un nouveau code remplace l'ancien."""
    if not consents.granted(db, "channels"):
        raise consents.ConsentError("channels")
    code = "".join(pysecrets.choice(_ALPHABET) for _ in range(8))
    salt = pysecrets.token_hex(16)
    expires = now + PAIRING_TTL
    set_setting(
        db,
        "channel_pairing",
        {"salt": salt, "hash": _code_hash(salt, code), "expires_at": expires.isoformat(), "attempts": 0},
    )
    audit.record(db, actor, "channel.pairing_started", expires_at=expires.isoformat())
    return {"code": f"{code[:4]}-{code[4:]}", "raw": code, "expires_at": expires.isoformat()}


def cancel_pairing(db: Session, *, actor: str) -> None:
    set_setting(db, "channel_pairing", {})
    audit.record(db, actor, "channel.pairing_cancelled")


def normalize_code(text: str) -> str | None:
    t = re.sub(r"^/(?:start|lier|link|pair)(?:@\w+)?\s*", "", text.strip(), flags=re.IGNORECASE)
    t = re.sub(r"[\s\-]", "", t).upper()
    return t if re.fullmatch(f"[{_ALPHABET}]{{8}}", t) else None


def _try_pair(db: Session, code: str, now: datetime) -> str:
    """« ok », « bad » (mauvais code : un essai de moins) ou « none » (aucun code en cours)."""
    state = dict(get_setting(db, "channel_pairing") or {})
    expires = _expiry(state)
    if expires is None:
        return "none"
    if expires <= now:
        set_setting(db, "channel_pairing", {})
        return "none"
    if not hmac.compare_digest(str(state["hash"]), _code_hash(str(state["salt"]), code)):
        state["attempts"] = int(state.get("attempts", 0)) + 1
        set_setting(db, "channel_pairing", {} if state["attempts"] >= PAIRING_MAX_ATTEMPTS else state)
        return "bad"
    set_setting(db, "channel_pairing", {})  # usage unique
    return "ok"


# ------------------------------------------------------------------ message reçu
@dataclass
class Admission:
    """Ce que la passerelle doit faire d'un message : répondre tout de suite, et/ou poser la question à
    l'assistant (en affichant « écrit… »), et/ou prévenir tes autres comptes (nouveau compte relié)."""

    replies: list[str] = field(default_factory=list)
    ask: str | None = None
    link_id: int | None = None
    privacy: str = "redacted"
    announce: str | None = None


def _seen(db: Session, platform: str, message_id: str, now: datetime) -> bool:
    key = hashlib.sha256(f"{platform}\x00{message_id}".encode()).hexdigest()
    if db.get(ChannelSeen, key) is not None:
        return True
    db.add(ChannelSeen(key=key, ts=now))
    db.flush()
    return False


def prune_seen(db: Session, now: datetime) -> int:
    result = db.execute(delete(ChannelSeen).where(ChannelSeen.ts < now - SEEN_RETENTION))
    return int(getattr(result, "rowcount", 0) or 0)


def _find_link(db: Session, platform: str, user_id: str) -> ChannelLink | None:
    index = get_keyring().blind_index(user_id, f"channel.{platform}")
    return db.scalar(select(ChannelLink).where(ChannelLink.platform == platform, ChannelLink.user_index == index))


def _clean_label(raw: str) -> str:
    text = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:60] or "Compte sans nom"


def admit(
    db: Session,
    inbound: Inbound,
    *,
    now: datetime,
    today: date,
    public_url: str,
    limiter: SlidingWindowLimiter | None = None,
) -> Admission:
    if not consents.granted(db, "channels"):
        return Admission()
    cfg = db.get(ChannelConfig, inbound.platform)
    if cfg is None or not cfg.enabled or not spec(inbound.platform).inbound:
        return Admission()
    # Groupes et robots : jamais. On ne parle d'argent qu'en tête-à-tête, et pas avec une autre machine.
    if inbound.from_bot or not inbound.direct or not inbound.user_id:
        return Admission()
    text = inbound.text.strip()[:MAX_INBOUND_CHARS]
    link = _find_link(db, inbound.platform, inbound.user_id)
    stranger = link is None or link.revoked_at is not None
    # Un inconnu qui insiste (ou un balayage) : ignoré avant la moindre écriture en base.
    if stranger and limiter is not None and not _stranger_allowed(limiter, inbound):
        return Admission()
    if inbound.message_id and _seen(db, inbound.platform, inbound.message_id, now):
        return Admission()
    if stranger:
        return _stranger(db, cfg, inbound, text, link, now)
    assert link is not None  # pour le typage : « stranger » couvre l'absence  # noqa: S101  # nosec B101
    link.last_seen_at = now
    if inbound.chat_id and link.chat_ref != inbound.chat_id:
        link.chat_ref = inbound.chat_id
    if limiter is not None and not limiter.hit(f"link:{link.id}"):
        return Admission(replies=["Doucement 🙂 Beaucoup de messages d'un coup : réessaie dans quelques minutes."])
    kind = secret_kind(text)
    if kind:
        audit.record(db, f"canal:{inbound.platform}", "channel.secret_refused", link=link.id, kind=kind)
        return Admission(
            replies=[
                f"Stop : ton message contient {kind}. Je ne l'ai ni lu ni gardé, et l'assistant ne l'a pas vu. "
                "Supprime-le de cette conversation. Bank ne te demandera jamais un mot de passe, un code ou un "
                "numéro de carte — ni ici, ni ailleurs."
            ]
        )
    command = _command(text)
    if command is not None:
        name, args = command
        return Admission(replies=_run_command(db, cfg, link, name, args, now=now, today=today, public_url=public_url))
    if not text:
        return Admission(
            replies=["Je ne lis que le texte ici. Pour un document, prends-le en photo dans Bank › Papiers."]
        )
    return Admission(ask=text, link_id=link.id, privacy=cfg.privacy)


def _stranger_allowed(limiter: SlidingWindowLimiter, inbound: Inbound) -> bool:
    """20 inconnus par 5 minutes et par messagerie, et autant de messages par inconnu : au-delà, silence sans
    écriture (le compteur « ignorés » ne grossit plus)."""
    return limiter.hit(f"stranger:{inbound.platform}") and limiter.hit(f"stranger:{inbound.platform}:{inbound.user_id}")


def _stranger(
    db: Session, cfg: ChannelConfig, inbound: Inbound, text: str, revoked: ChannelLink | None, now: datetime
) -> Admission:
    code = normalize_code(text)
    outcome = _try_pair(db, code, now) if code else "none"
    if outcome == "none":
        cfg.ignored += 1  # silence : un inconnu n'apprend même pas que ce robot est relié à Bank
        return Admission()
    if outcome == "bad":
        audit.record(db, f"canal:{inbound.platform}", "channel.pairing_failed")
        return Admission(
            replies=["Ce code ne correspond pas ou a expiré. Génère-en un nouveau dans Bank › Messageries."]
        )
    label = _clean_label(inbound.label)
    if revoked is not None:
        link = revoked
        link.revoked_at, link.failures, link.last_error = None, 0, None
        link.chat_ref, link.label, link.notify, link.linked_at = inbound.chat_id, label, True, now
    else:
        link = ChannelLink(
            platform=inbound.platform,
            user_index=get_keyring().blind_index(inbound.user_id, f"channel.{inbound.platform}"),
            user_ref=inbound.user_id,
            chat_ref=inbound.chat_id,
            label=label,
            linked_at=now,
        )
        db.add(link)
    link.last_seen_at = now
    db.flush()
    audit.record(db, f"canal:{inbound.platform}", "channel.linked", link=link.id)
    platform = spec(inbound.platform).label
    return Admission(
        replies=[
            f"C'est fait : ce compte {platform} est relié à Bank 🎉\n\n" + HELP,
        ],
        link_id=link.id,
        announce=(
            f"Nouveau compte relié à Bank : {platform} ({label}). Si ce n'est pas toi, délie-le tout de suite dans "
            "Bank › Messageries."
        ),
    )


# ------------------------------------------------------------------ commandes
HELP = (
    "Pose-moi une question sur ton argent, ou essaie :\n"
    "- /budget : ce que tu peux dépenser aujourd'hui\n"
    "- /missions : tes meilleures pistes pour gagner ou économiser\n"
    "- /demarches : où en sont tes démarches\n"
    "- /nouveau : repartir d'une conversation vierge\n"
    "- /pause ou /reprendre : mes alertes ici\n"
    "- /delier : ne plus me parler depuis ce compte\n\n"
    "Rien ne se valide ici : transmettre, confirmer, régler, c'est dans l'application."
)
_BARE_COMMANDS = {"aide", "help", "budget", "missions", "demarches", "pause", "reprendre", "nouveau"}
_ALIASES = {
    "help": "aide",
    "start": "aide",
    "menu": "aide",
    "new": "nouveau",
    "reset": "nouveau",
    "stop": "pause",
    "resume": "reprendre",
    "unlink": "delier",
    "mission": "missions",
    "demarche": "demarches",
}


def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn")


def _command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if stripped.startswith("/"):
        head, _, args = stripped[1:].partition(" ")
        name = _fold(head.split("@", 1)[0])
    else:
        name, args = _fold(stripped.rstrip("!?. ")), ""
        if name not in _BARE_COMMANDS:
            return None
    name = _ALIASES.get(name, name)
    return name, args.strip()


def _date_fr(d: date) -> str:
    months = (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    )
    return f"{d.day} {months[d.month - 1]}"


def _run_command(
    db: Session,
    cfg: ChannelConfig,
    link: ChannelLink,
    name: str,
    args: str,
    *,
    now: datetime,
    today: date,
    public_url: str,
) -> list[str]:
    if name == "aide":
        return [HELP]
    if name == "budget":
        return [budget_text(db, today, public_url)]
    if name == "missions":
        return [missions_text(db, today, cfg.privacy, public_url)]
    if name == "demarches":
        return [procedures_text(db, today, cfg.privacy, public_url)]
    if name == "nouveau":
        if link.conversation_id:
            assistant_service.close_conversation(db, conversation_id=link.conversation_id)
            link.conversation_id = None
        return ["C'est reparti d'une page blanche ✨ (ce que j'ai appris de toi reste, si la mémoire est activée)."]
    if name == "pause":
        link.notify = False
        return ["Alertes en pause ici. Écris /reprendre pour les réactiver."]
    if name == "reprendre":
        link.notify, link.failures, link.last_error = True, 0, None
        return ["Alertes réactivées : je te préviens ici quand quelque chose mérite ton attention."]
    if name == "delier":
        link.revoked_at, link.conversation_id = now, None
        audit.record(db, f"canal:{link.platform}", "channel.unlinked", link=link.id, by="chat")
        return ["C'est fait : ce compte n'est plus relié à Bank. Pour revenir, génère un code dans Bank › Messageries."]
    return [f"Je ne connais pas /{name[:30]}.\n\n{HELP}"]


def budget_text(db: Session, today: date, public_url: str) -> str:
    forecast, _ = analytics.month_forecast(db, today)
    end = date.fromisoformat(f"{forecast.month}-{forecast.days_in_month:02d}")
    lines = [
        f"Tu peux dépenser **{format_eur(forecast.safe_to_spend_per_day_cents)} par jour** jusqu'au {_date_fr(end)} "
        f"({forecast.days_left} jour{'s' if forecast.days_left > 1 else ''})."
    ]
    if forecast.overdraft_date is not None:
        lines.append(
            f"⚠️ Ton solde pourrait passer sous zéro le {_date_fr(forecast.overdraft_date)} : regarde tes échéances "
            "dans l'application."
        )
    elif forecast.status == "tight":
        lines.append(
            f"Ça se resserre : point bas {format_eur(forecast.lowest_balance_cents)} le "
            f"{_date_fr(forecast.lowest_balance_date)}."
        )
    else:
        lines.append(
            f"Mois sans découvert, point bas {format_eur(forecast.lowest_balance_cents)} le "
            f"{_date_fr(forecast.lowest_balance_date)}."
        )
    lines.append(f"Le détail : {public_url}/#/aujourdhui")
    return "\n".join(lines)


def missions_text(db: Session, today: date, privacy: str, public_url: str) -> str:
    top = opportunities.top(db, today, limit=3)
    value = opportunities.value_summary(db)
    if not top:
        head = "Aucune mission pour l'instant : Bank continue de chercher dans tes comptes et tes droits."
    else:
        rows = []
        for i, o in enumerate(top, 1):
            gain = format_eur(int(o["yearly_cents"]))
            title = str(o["category_label"]) if privacy == "aggregates" else str(o["title"])
            rows.append(f"{i}. {title} — jusqu'à {gain} par an")
        head = "Tes meilleures missions :\n" + "\n".join(rows)
    piggy = format_eur(int(value["confirmed_yearly_cents"]))
    return f"{head}\n\nTa tirelire : {piggy} confirmés sur un an. Lance une mission : {public_url}/#/opportunites"


def procedures_text(db: Session, today: date, privacy: str, public_url: str) -> str:
    procs = list(
        db.scalars(select(Procedure).where(Procedure.stage.in_(_IN_PROGRESS)).order_by(Procedure.updated_at.desc()))
    )[:5]
    if not procs:
        return f"Aucune démarche en cours. Le catalogue : {public_url}/#/demarches"
    rows = []
    for p in procs:
        template = dp.TEMPLATES.get(p.template)
        title = p.title if privacy == "full" or template is None else template.title
        stage = dp.STAGE_LABELS.get(p.stage, p.stage)
        due = " — à relancer aujourd'hui" if p.stage == "waiting" and p.next_check and p.next_check <= today else ""
        rows.append(f"- {title} : {stage}{due}")
    return "Tes démarches :\n" + "\n".join(rows) + f"\n\nPour avancer ou transmettre : {public_url}/#/demarches"


NO_ASSISTANT = (
    "L'assistant n'est pas encore branché : ajoute ta clé OpenRouter dans Bank › Connecteurs. En attendant, "
    "/budget, /missions et /demarches marchent."
)


def answer(db: Session, adm: Admission, *, today: date, make_client: assistant_service.ClientFactory) -> list[str]:
    """La question à l'assistant, avec la confidentialité la plus stricte et la conversation de ce compte."""
    if adm.ask is None or adm.link_id is None:
        return []
    link = db.get(ChannelLink, adm.link_id)
    if link is None or link.revoked_at is not None:
        return []
    if assistant_service.provider(db) is None:
        return [NO_ASSISTANT]
    try:
        out = assistant_service.ask(
            db,
            actor=f"canal:{link.platform}",
            today=today,
            question=adm.ask,
            conversation_id=link.conversation_id,
            make_client=make_client,
            privacy=adm.privacy,
            channel=spec(link.platform).label,
        )
    except assistant_service.AssistantError as exc:
        return [str(exc)]
    link.conversation_id = int(out["conversation_id"])
    return [str(out["answer"])]
