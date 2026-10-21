"""Veille : sources publiques interrogées régulièrement (ministère de l'Économie, versions du moteur OpenFisca,
flux que tu ajoutes), changements détectés et datés, rapprochement avec ta situation fait ici, et rappel des
données de référence à revérifier. Aucune donnée te concernant ne sort : on ne fait que lire des pages publiques.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import procedures as dp
from app.domain import watch as dw
from app.domain.opportunities import FEE_SOURCES
from app.models import (
    Category,
    EnergyConnection,
    FiscalSimulation,
    Transaction,
    WatchItem,
    WatchSource,
)
from app.security import audit, netfetch
from app.services import analytics, consents

Fetcher = Callable[[str, str | None, str | None], netfetch.FetchResult]
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"
MAX_USER_SOURCES = 20
MAX_ITEMS_PER_SOURCE = 300
ITEM_RETENTION_DAYS = 365
ENGINE_SLUG = "openfisca-france"

BUILTIN_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "slug": "economie-gouv",
        "kind": "rss",
        "name": "Ministère de l'Économie — toutes les actualités",
        "url": "https://www.economie.gouv.fr/rss/toutesactualites",
        "trust": "officiel",
        "interval_hours": 12,
    },
    {
        "slug": ENGINE_SLUG,
        "kind": "rss",
        "name": "OpenFisca-France — versions du moteur de calcul des droits",
        "url": "https://pypi.org/rss/project/openfisca-france/releases.xml",
        "trust": "technique",
        "interval_hours": 24,
    },
)


class WatchError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def default_fetcher(url: str, etag: str | None, last_modified: str | None) -> netfetch.FetchResult:
    return netfetch.fetch(url, accept=FEED_ACCEPT, etag=etag, last_modified=last_modified)


# ------------------------------------------------------------------ sources
def ensure_sources(db: Session) -> None:
    existing = set(db.scalars(select(WatchSource.slug)))
    for spec in BUILTIN_SOURCES:
        if spec["slug"] not in existing:
            db.add(WatchSource(builtin=True, enabled=True, **spec))
    db.flush()


def _slug(name: str, db: Session) -> str:
    base = "".join(ch if ch.isalnum() else "-" for ch in dw.normalize(name))[:30].strip("-") or "source"
    slug, n = base, 1
    while db.scalar(select(WatchSource.id).where(WatchSource.slug == slug)) is not None:
        n += 1
        slug = f"{base}-{n}"[:40]
    return slug


def add_source(
    db: Session, url: str, *, name: str | None, actor: str, resolver: netfetch.Resolver | None = None
) -> WatchSource:
    try:
        parts, _ip = netfetch.check_url(url, resolver)
    except netfetch.FetchError as exc:
        raise WatchError(str(exc)) from exc
    clean = url.strip()
    if db.scalar(select(WatchSource.id).where(WatchSource.url == clean)) is not None:
        raise WatchError("Cette source est déjà suivie", 409)
    if (db.scalar(select(func.count()).select_from(WatchSource).where(WatchSource.builtin.is_(False))) or 0) >= (
        MAX_USER_SOURCES
    ):
        raise WatchError(f"{MAX_USER_SOURCES} sources personnelles au plus", 409)
    label = dw.clean_text(name, 120) or (parts.hostname or "Source")
    source = WatchSource(
        slug=_slug(label, db),
        kind="rss",
        name=label,
        url=clean,
        trust="perso",
        interval_hours=24,
        enabled=True,
        builtin=False,
    )
    db.add(source)
    db.flush()
    audit.record(db, actor, "watch.source_add", source=source.id)
    return source


def update_source(db: Session, source_id: int, *, enabled: bool, actor: str) -> WatchSource:
    source = db.get(WatchSource, source_id)
    if source is None:
        raise WatchError("Source introuvable", 404)
    source.enabled = enabled
    db.flush()
    audit.record(db, actor, "watch.source_update", source=source.id, enabled=enabled)
    return source


def delete_source(db: Session, source_id: int, *, actor: str) -> None:
    source = db.get(WatchSource, source_id)
    if source is None:
        raise WatchError("Source introuvable", 404)
    if source.builtin:
        raise WatchError("Une source intégrée se désactive, elle ne se supprime pas", 409)
    db.delete(source)
    db.flush()
    audit.record(db, actor, "watch.source_delete", source=source_id)


# ------------------------------------------------------------------ relève
def _due(source: WatchSource, now: datetime) -> bool:
    return source.last_checked_at is None or now - source.last_checked_at >= timedelta(hours=source.interval_hours)


def _store(db: Session, source: WatchSource, entries: list[dw.Entry], now: datetime) -> tuple[int, int]:
    new = changed = 0
    for entry in entries:
        ext = dw.external_id(source.id, entry.guid)
        digest = dw.content_hash(entry)
        topics = ["moteur"] if source.slug == ENGINE_SLUG else dw.topics_of(entry.title, entry.summary)
        item = db.scalar(select(WatchItem).where(WatchItem.source_id == source.id, WatchItem.external_id == ext))
        if item is None:
            db.add(
                WatchItem(
                    source_id=source.id,
                    external_id=ext,
                    title=entry.title,
                    summary=entry.summary,
                    url=entry.link or "",
                    published_at=entry.published,
                    first_seen_at=now,
                    last_verified_at=now,
                    content_hash=digest,
                    topics=json.dumps(topics),
                    status="new",
                )
            )
            new += 1
            continue
        item.last_verified_at = now
        if item.content_hash != digest:  # la page a changé depuis la dernière lecture : nouvelle version
            item.title, item.summary, item.url = entry.title, entry.summary, entry.link or ""
            item.content_hash, item.topics = digest, json.dumps(topics)
            item.version += 1
            item.changed_at = now
            item.published_at = entry.published or item.published_at
            if item.status == "seen":
                item.status = "new"
            changed += 1
    return new, changed


def refresh(
    db: Session, *, now: datetime, fetcher: Fetcher, force: bool = False, source_id: int | None = None
) -> dict[str, int]:
    """Interroge les sources dues (ou toutes si `force`), enregistre nouveautés et changements, et l'état de chaque
    source (dernière relève, dernière réussite, erreur lisible)."""
    report = {"checked": 0, "new": 0, "changed": 0, "errors": 0, "skipped": 0}
    if not consents.granted(db, "watch"):
        report["skipped"] = 1
        return report
    ensure_sources(db)
    query = select(WatchSource).where(WatchSource.enabled.is_(True)).order_by(WatchSource.id)
    if source_id is not None:
        query = query.where(WatchSource.id == source_id)
    for source in db.scalars(query).all():
        if not force and not _due(source, now):
            continue
        report["checked"] += 1
        previous_ok = source.last_ok_at
        source.last_checked_at = now
        try:
            result = fetcher(source.url, source.etag, source.last_modified)
            if result.status == 304:  # rien n'a bougé : ce qui était dans le flux y est toujours
                if previous_ok is not None:
                    for item in db.scalars(
                        select(WatchItem).where(
                            WatchItem.source_id == source.id, WatchItem.last_verified_at == previous_ok
                        )
                    ):
                        item.last_verified_at = now
            else:
                new, changed = _store(db, source, dw.parse_feed(result.body), now)
                report["new"] += new
                report["changed"] += changed
                source.etag = (result.etag or "")[:200] or None
                source.last_modified = (result.last_modified or "")[:80] or None
            source.last_ok_at, source.last_error = now, None
        except (netfetch.FetchError, dw.FeedError) as exc:
            source.last_error = str(exc)[:300]
            report["errors"] += 1
        db.flush()
    return report


def prune(db: Session, now: datetime) -> int:
    """Les éléments de plus d'un an, et au-delà de 300 par source, sont oubliés."""
    stale = set(
        db.scalars(select(WatchItem.id).where(WatchItem.first_seen_at < now - timedelta(days=ITEM_RETENTION_DAYS)))
    )
    for source_id in db.scalars(select(WatchSource.id)).all():
        stale.update(
            db.scalars(
                select(WatchItem.id)
                .where(WatchItem.source_id == source_id)
                .order_by(WatchItem.first_seen_at.desc(), WatchItem.id.desc())
                .offset(MAX_ITEMS_PER_SOURCE)
            )
        )
    if stale:
        db.execute(delete(WatchItem).where(WatchItem.id.in_(stale)))
        db.flush()
    return len(stale)


def housekeeping(db: Session, *, now: datetime, fetcher: Fetcher) -> dict[str, int]:
    report = refresh(db, now=now, fetcher=fetcher)
    report["pruned"] = prune(db, now)
    return {f"watch_{k}": v for k, v in report.items()}


# ------------------------------------------------------------------ lecture
def interests(db: Session, today: date) -> set[str]:
    """Les thèmes qui te concernent, déduits ici de tes données (rien n'est envoyé aux sources)."""
    out = set(dw.ALWAYS_INTERESTING)
    profile = analytics.load_profile(db) or {}
    streams = analytics.aids_summary(db, today).streams
    if profile or streams:
        out.add("aides")
    if str(profile.get("housing") or "").startswith("locataire") or any(s.kind == "caf_logement" for s in streams):
        out.add("logement")
    since = today - timedelta(days=120)
    slugs = set(
        db.scalars(
            select(Category.slug)
            .join(Transaction, Transaction.category_id == Category.id)
            .where(Transaction.booking_date >= since)
            .distinct()
        )
    )
    if "salaire" in slugs or any(s.organism == "france_travail" for s in streams):
        out.add("emploi")
    if "energie" in slugs or db.scalar(select(EnergyConnection.id).limit(1)) is not None:
        out.add("energie")
    birth = profile.get("birth_date")
    age = (today - date.fromisoformat(birth)).days // 365 if isinstance(birth, str) else 0
    if age >= 58 or any(s.organism == "retraite" for s in streams):
        out.add("retraite")
    return out


def _engine(db: Session, items: list[WatchItem], engine_source: WatchSource | None) -> dict[str, Any] | None:
    sim = db.scalar(select(FiscalSimulation).order_by(FiscalSimulation.id.desc()).limit(1))
    used = str(json.loads(sim.result).get("engine_version") or "") if sim is not None else ""
    titles = [i.title for i in items if engine_source is not None and i.source_id == engine_source.id]
    latest = dw.latest_version(titles)
    if latest is None:
        return None
    published = next(
        (
            i.published_at
            for i in items
            if engine_source is not None and i.source_id == engine_source.id and i.title.strip() == latest
        ),
        None,
    )
    outdated = bool(dw.version_key(used)) and dw.version_key(latest) > dw.version_key(used)
    return {
        "used": used or None,
        "latest": latest,
        "latest_published_at": published.isoformat() if published else None,
        "outdated": outdated,
        "advice": "Mets à jour le conteneur du moteur fiscal (make fiscal-test puis redéploiement) pour profiter "
        "des derniers barèmes, puis relance la simulation de tes aides."
        if outdated
        else None,
    }


def _rates() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(resources.files("app.domain").joinpath("rates.json").read_text("utf-8"))
    return raw


def references(today: date) -> list[dict[str, Any]]:
    sources: dict[str, tuple[str, str, str | None]] = {}
    for tpl in dp.TEMPLATES.values():
        for n, tsrc in enumerate(tpl.sources):
            if tsrc.verified_on and tsrc.url:
                title = f"Démarche « {tpl.title} » — {tsrc.title}"
                sources.setdefault(tsrc.url, (f"{tpl.key}-{n}", title, tsrc.verified_on))
    for n, fsrc in enumerate(FEE_SOURCES):
        if fsrc.verified_on and fsrc.url:
            sources.setdefault(fsrc.url, (f"fees-{n}", f"Frais bancaires — {fsrc.title}", fsrc.verified_on))
    checks = dw.reference_checks(_rates(), list(sources.values()), today)
    return [
        {
            "key": r.key,
            "title": r.title,
            "as_of": r.as_of.isoformat() if r.as_of else None,
            "next_check": r.next_check.isoformat(),
            "status": r.status,
            "detail": r.detail,
            "source": r.source,
        }
        for r in sorted(checks, key=lambda r: (r.status != "stale", r.next_check))
    ]


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def listing(db: Session, today: date, *, limit: int = 60) -> dict[str, Any]:
    ensure_sources(db)
    mine = interests(db, today)
    sources = list(db.scalars(select(WatchSource).order_by(WatchSource.builtin.desc(), WatchSource.id)))
    by_id = {s.id: s for s in sources}
    engine_source = next((s for s in sources if s.slug == ENGINE_SLUG), None)
    rows = list(
        db.scalars(
            select(WatchItem)
            .where(WatchItem.status != "dismissed")
            .order_by(func.coalesce(WatchItem.published_at, WatchItem.first_seen_at).desc())
            .limit(400)
        )
    )
    engine_items = [i for i in rows if engine_source is not None and i.source_id == engine_source.id]
    news = [i for i in rows if engine_source is None or i.source_id != engine_source.id]
    views = []
    for item in news:
        topics = json.loads(item.topics)
        source = by_id.get(item.source_id)
        views.append(
            {
                "id": item.id,
                "title": item.title,
                "summary": item.summary,
                "url": item.url or None,
                "published_at": _iso(item.published_at),
                "first_seen_at": _iso(item.first_seen_at),
                "last_verified_at": _iso(item.last_verified_at),
                "changed_at": _iso(item.changed_at),
                "version": item.version,
                "topics": topics,
                "topic_labels": [dw.TOPIC_LABELS.get(t, t) for t in topics],
                "relevant": dw.relevant(topics, mine),
                "status": item.status,
                "source": {"name": source.name, "trust": source.trust} if source else None,
            }
        )
    views.sort(key=lambda v: (v["status"] != "new", not v["relevant"]))  # tri stable : la date reste l'ordre second
    counts = {
        "new_relevant": sum(1 for v in views if v["status"] == "new" and v["relevant"]),
        "new": sum(1 for v in views if v["status"] == "new"),
    }
    return {
        "enabled": consents.granted(db, "watch"),
        "interests": sorted(mine),
        "interest_labels": {t: dw.TOPIC_LABELS[t] for t in sorted(mine)},
        "items": views[:limit],
        "counts": counts,
        "engine": _engine(db, engine_items, engine_source),
        "references": references(today),
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "trust": s.trust,
                "builtin": s.builtin,
                "enabled": s.enabled,
                "interval_hours": s.interval_hours,
                "last_checked_at": _iso(s.last_checked_at),
                "last_ok_at": _iso(s.last_ok_at),
                "last_error": s.last_error,
            }
            for s in sources
        ],
    }


def set_item_status(db: Session, item_id: int, status: str, *, actor: str) -> None:
    if status not in ("new", "seen", "dismissed"):
        raise WatchError("Statut inconnu")
    item = db.get(WatchItem, item_id)
    if item is None:
        raise WatchError("Élément introuvable", 404)
    item.status = status
    db.flush()


def mark_all_seen(db: Session, *, actor: str) -> int:
    count = 0
    for item in db.scalars(select(WatchItem).where(WatchItem.status == "new")):
        item.status = "seen"
        count += 1
    db.flush()
    audit.record(db, actor, "watch.all_seen", count=count)
    return count


def due_now(db: Session) -> bool:
    """Au moins une source à relever : pour déclencher une relève à l'ouverture de l'écran."""
    now = utcnow()
    return any(_due(s, now) for s in db.scalars(select(WatchSource).where(WatchSource.enabled.is_(True))))


def summary(db: Session, today: date) -> dict[str, Any]:
    """Pour l'écran Aujourd'hui et la navigation : combien de nouveautés te concernent, ce qui est à revérifier."""
    mine = interests(db, today)
    engine_source = db.scalar(select(WatchSource).where(WatchSource.slug == ENGINE_SLUG))
    new_items = db.scalars(select(WatchItem).where(WatchItem.status == "new"))
    new_relevant = sum(
        1
        for i in new_items
        if (engine_source is None or i.source_id != engine_source.id) and dw.relevant(json.loads(i.topics), mine)
    )
    engine_items = (
        list(db.scalars(select(WatchItem).where(WatchItem.source_id == engine_source.id)))
        if engine_source is not None
        else []
    )
    engine = _engine(db, engine_items, engine_source)
    return {
        "enabled": consents.granted(db, "watch"),
        "new_relevant": new_relevant,
        "stale_references": sum(1 for r in references(today) if r["status"] == "stale"),
        "engine_outdated": bool(engine and engine["outdated"]),
    }
