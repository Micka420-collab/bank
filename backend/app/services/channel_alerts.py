"""« Bank te prévient » : ce qui mérite d'arriver dans ta messagerie, sans te noyer.

Une alerte a une clé stable (la même démarche à relancer le même jour = la même clé) : elle n'est envoyée qu'une
fois par compte relié. Les alertes d'un passage sont regroupées en un seul message, rien ne part pendant tes heures
calmes, et un compte qui refuse durablement nos messages (robot bloqué, fenêtre WhatsApp fermée) est mis en pause
au lieu d'être relancé sans fin. Le contenu suit la confidentialité de la messagerie."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import procedures as dp
from app.domain import watch as dw
from app.domain.money import format_eur
from app.models import ChannelConfig, ChannelLink, ChannelNotice, Procedure, WatchItem, WatchSource
from app.services import (
    analytics,
    autonomy,
    correlations,
    obligations,
    opportunities,
    price_alerts,
    procedures,
    yearahead,
)
from app.services import backup as backup_service
from app.services import watch as watch_service
from app.services.settings_store import get_setting

MAX_ITEMS_PER_MESSAGE = 5
MAX_FAILURES = 3  # refus définitifs d'affilée avant de mettre le compte en pause


@dataclass(frozen=True)
class Notice:
    key: str
    text: str
    urgent: bool = False  # passe même en heures calmes (pilote automatique arrêté, mouvement non rapporté)


def is_quiet(local: datetime, hours: list[int] | tuple[int, int]) -> bool:
    """Heures calmes [début, fin[ : 21-8 couvre la nuit, 13-14 la sieste ; début = fin : jamais calme."""
    start, end = int(hours[0]), int(hours[1])
    h = local.hour
    if start == end:
        return False
    return h >= start or h < end if start > end else start <= h < end


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


def _proc_title(p: Procedure, privacy: str) -> str:
    template = dp.TEMPLATES.get(p.template)
    return p.title if privacy == "full" or template is None else template.title


def collect(
    db: Session, *, today: date, local: datetime, privacy: str, public_url: str, now: datetime | None = None
) -> list[Notice]:
    """Tout ce qui mérite une alerte maintenant, du plus pressant au plus doux."""
    now = now or local.astimezone(UTC)
    out: list[Notice] = [
        Notice(key, text, urgent)
        for key, text, urgent in autonomy.alerts(db, now=now, privacy=privacy, public_url=public_url)
    ]
    # Déclarations récurrentes : une fenêtre qui s'ouvre, une échéance proche, un retard (urgent : l'aide est en jeu).
    out += [
        Notice(key, text, urgent)
        for key, text, urgent in obligations.alerts(db, today=today, privacy=privacy, public_url=public_url)
    ]
    # L'année qui vient : un pic dans les deux mois, un passage sous zéro prévu.
    out += [
        Notice(key, text, urgent)
        for key, text, urgent in yearahead.alerts(db, today=today, privacy=privacy, public_url=public_url)
    ]
    # Corrélations : seulement les constats sérieux (doublon d'assurance, loyer au-dessus de l'IRL), une fois.
    out += [
        Notice(key, text, urgent)
        for key, text, urgent in correlations.alerts(db, today=today, privacy=privacy, public_url=public_url)
    ]
    # Alertes de cours que tu as fixées : un constat par franchissement, jamais un conseil.
    out += [
        Notice(key, text, urgent)
        for key, text, urgent in price_alerts.alerts(
            db, now=now, privacy=privacy, public_url=public_url, tz=local.tzinfo or UTC
        )
    ]
    # Sauvegardes : un échec ou deux jours sans copie — la seule alerte qui parle du serveur, pas de l'argent.
    out += [
        Notice(key, text, urgent) for key, text, urgent in backup_service.alerts(db, now=now, public_url=public_url)
    ]
    forecast, _ = analytics.month_forecast(db, today)
    if forecast.overdraft_date is not None:
        out.append(
            Notice(
                f"overdraft:{forecast.month}:{forecast.overdraft_date.isoformat()}",
                f"⚠️ Ton solde pourrait passer sous zéro le {_date_fr(forecast.overdraft_date)} "
                f"(point bas {format_eur(forecast.lowest_balance_cents)}). Regarde tes échéances : "
                f"{public_url}/#/aujourdhui",
            )
        )
    for p in procedures.due(db, today):
        assert p.next_check is not None  # garanti par la requête  # noqa: S101  # nosec B101
        out.append(
            Notice(
                f"proc:{p.id}:{p.next_check.isoformat()}",
                f"📌 « {_proc_title(p, privacy)} » : pas de réponse depuis l'envoi, c'est le moment de relancer. "
                f"{public_url}/#/demarches/{p.id}",
            )
        )
    for c in opportunities.confirmations(db, today):
        proc = db.get(Procedure, int(c["procedure_id"]))
        title = _proc_title(proc, privacy) if proc is not None else "ta démarche"
        out.append(
            Notice(
                f"confirm:{c['key']}",
                f"🪙 Un gain à confirmer : {format_eur(int(c['amount_cents']))} ({title}). Confirme-le pour remplir "
                f"ta tirelire : {public_url}/#/opportunites",
            )
        )
    out.extend(_watch_notices(db, today, public_url))
    if bool(get_setting(db, "channel_weekly")) and local.weekday() == 6 and local.hour >= 18:
        year, week, _ = local.isocalendar()
        out.append(Notice(f"weekly:{year}-W{week:02d}", weekly_text(db, today, privacy, public_url, forecast)))
    return out


def _watch_notices(db: Session, today: date, public_url: str) -> list[Notice]:
    if not watch_service.summary(db, today)["new_relevant"]:
        return []
    mine = watch_service.interests(db, today)
    engine = db.scalar(select(WatchSource).where(WatchSource.slug == watch_service.ENGINE_SLUG))
    items = db.scalars(select(WatchItem).where(WatchItem.status == "new").order_by(WatchItem.first_seen_at.desc()))
    out = []
    for item in items:
        if engine is not None and item.source_id == engine.id:
            continue
        if not dw.relevant(json.loads(item.topics), mine):
            continue
        source = db.get(WatchSource, item.source_id)
        name = source.name if source is not None else "Source officielle"
        out.append(Notice(f"watch:{item.id}:{item.version}", f"📰 {name} : {item.title[:160]} — {public_url}/#/veille"))
        if len(out) >= 3:
            break
    return out


def weekly_text(db: Session, today: date, privacy: str, public_url: str, forecast: Any) -> str:
    value = opportunities.value_summary(db)
    top = opportunities.top(db, today, limit=1)
    lines = [
        "☀️ Ta semaine avec Bank",
        f"- Tu peux dépenser {format_eur(forecast.safe_to_spend_per_day_cents)} par jour en ce moment.",
        f"- Ta tirelire : {format_eur(int(value['confirmed_yearly_cents']))} confirmés sur un an.",
    ]
    if top:
        o = top[0]
        title = o["category_label"] if privacy == "aggregates" else o["title"]
        lines.append(f"- Mission du moment : {title} (jusqu'à {format_eur(int(o['yearly_cents']))} par an).")
    waiting = list(db.scalars(select(Procedure).where(Procedure.stage.in_(("sent", "waiting")))))
    if waiting:
        n = len(waiting)
        lines.append(f"- {n} démarche{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}, en attente de réponse.")
    for line in obligations.chat_lines(db, today, privacy, public_url)[:-1]:
        if line.startswith(("📝", "🚨", "📅")):
            lines.append(f"- {line}")
    lines.append(f"Bonne semaine ! {public_url}/#/aujourdhui")
    return "\n".join(lines)


def pending_for(db: Session, link: ChannelLink, notices: list[Notice]) -> list[Notice]:
    sent = set(db.scalars(select(ChannelNotice.key).where(ChannelNotice.link_id == link.id)))
    return [n for n in notices if n.key not in sent]


def compose(notices: list[Notice]) -> str:
    """Un seul message par passage : les alertes les plus pressantes d'abord, le reste résumé."""
    shown = notices[:MAX_ITEMS_PER_MESSAGE]
    if len(shown) == 1:
        return shown[0].text
    body = "Bank te prévient :\n\n" + "\n\n".join(n.text for n in shown)
    rest = len(notices) - len(shown)
    if rest:
        body += f"\n\n… et {rest} autre{'s' if rest > 1 else ''} chose{'s' if rest > 1 else ''} dans l'application."
    return body


def mark_sent(db: Session, link: ChannelLink, notices: list[Notice], *, now: datetime) -> None:
    for n in notices:
        db.add(ChannelNotice(link_id=link.id, key=n.key, sent_at=now))
    link.last_notified_at, link.failures, link.last_error = now, 0, None
    db.flush()


def mark_failed(db: Session, link: ChannelLink, error: str, *, permanent: bool) -> None:
    link.last_error = error[:300]
    if permanent:
        link.failures += 1
        if link.failures >= MAX_FAILURES:
            link.notify = False  # cible morte : on arrête d'insister, l'écran Messageries dit pourquoi


@dataclass(frozen=True)
class Delivery:
    link_id: int
    platform: str
    chat_ref: str
    privacy: str
    text: str
    keys: tuple[str, ...]


def plan(
    db: Session,
    *,
    platforms: set[str],
    today: date,
    local: datetime,
    public_url: str,
) -> list[Delivery]:
    """Ce qu'il faut envoyer maintenant à chaque compte relié qui reçoit les alertes (en heures calmes, seulement
    l'urgent : le pilote automatique qui s'arrête sur une anomalie ne peut pas attendre le matin)."""
    if not platforms:
        return []
    quiet = is_quiet(local, get_setting(db, "channel_quiet_hours"))
    links = list(
        db.scalars(
            select(ChannelLink).where(
                ChannelLink.revoked_at.is_(None), ChannelLink.notify.is_(True), ChannelLink.platform.in_(platforms)
            )
        )
    )
    if not links:
        return []
    by_privacy: dict[str, list[Notice]] = {}
    out: list[Delivery] = []
    for link in links:
        cfg = db.get(ChannelConfig, link.platform)
        privacy = cfg.privacy if cfg else "redacted"
        if privacy not in by_privacy:
            found = collect(db, today=today, local=local, privacy=privacy, public_url=public_url)
            by_privacy[privacy] = [n for n in found if n.urgent] if quiet else found
        todo = pending_for(db, link, by_privacy[privacy])
        if todo:
            out.append(
                Delivery(link.id, link.platform, link.chat_ref, privacy, compose(todo), tuple(n.key for n in todo))
            )
    return out


def record(
    db: Session, delivery: Delivery, *, ok: bool, error: str = "", permanent: bool = False, now: datetime
) -> None:
    link = db.get(ChannelLink, delivery.link_id)
    if link is None:
        return
    if ok:
        # Tout ce qui a été résumé (« … et 2 autres choses ») compte comme annoncé : pas de rappel en boucle.
        mark_sent(db, link, [Notice(k, "") for k in delivery.keys], now=now)
    else:
        mark_failed(db, link, error, permanent=permanent)
