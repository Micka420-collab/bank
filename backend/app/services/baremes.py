"""Barèmes tenus à jour : une fois par mois (et à la demande), Bank relit les tarifs réglementés de l'électricité
(CRE), les taux des livrets (Caisse des Dépôts), l'IPC et l'IRL (INSEE), le SMIC et le barème de l'impôt
(OpenFisca) ; il garde chaque valeur relevée, datée et sourcée, compare avec ce qui est en vigueur et applique ce qui
a changé par une couche au-dessus de rates.json (domain.rates), le fichier restant le repli.

Rien ne change en silence : chaque mise à jour est notée dans la Veille (ancienne → nouvelle valeur, source) et au
journal d'activité, et se voit dans Réglages › Barèmes, où tu peux l'écarter. Une relève ratée garde les valeurs
précédentes et le dit. Rien te concernant ne part : seules des pages publiques sont lues, par netfetch, avec ton
accord « Barèmes tenus à jour »."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import baremes as dom
from app.domain import contracts, rates
from app.domain import watch as dw
from app.models import RateObservation, RateOverride, WatchItem, WatchSource
from app.security import audit, netfetch
from app.services import consents
from app.services.settings_store import get_setting, set_setting

CONSENT = "baremes"
INTERVAL = timedelta(days=30)  # une relève par mois
RETRY_AFTER = timedelta(hours=20)  # une source en échec est réessayée le lendemain, pas dans un mois
MAX_BYTES = 1_000_000
Fetch = Callable[[str, str], bytes]  # (adresse, Accept) → corps ; lève netfetch.FetchError


@dataclass(frozen=True)
class Feed:
    url: str
    accept: str
    parse: Callable[[bytes, str, date], list[dom.Observation]]
    label: str  # ce que ce fichier apporte, pour dire lequel a échoué


# Ce qu'un fichier de source peut lever sur une réponse inattendue : jamais une panne de toute la relève.
FEED_ERRORS = (netfetch.FetchError, ValueError, csv.Error, RecursionError)
CRE_LABELS = {"base": "option Base", "hphc": "option Heures creuses", "tempo": "option Tempo"}
INSEE_LABELS = {"011814056": "IPC", "011814133": "inflation sur un an", "001515333": "IRL"}
OPENFISCA_LABELS = {"openfisca.smic_horaire": "SMIC horaire", "openfisca.smic_mensuel": "SMIC mensuel"}


@dataclass(frozen=True)
class Spec:
    slug: str
    name: str
    url: str
    feeds: tuple[Feed, ...]


SOURCES: tuple[Spec, ...] = (
    Spec(
        "bareme-cre",
        "CRE — tarifs réglementés de l'électricité",
        dom.cre_url("base"),
        tuple(
            Feed(dom.cre_url(o), dom.CSV_ACCEPT, partial(dom.parse_cre, option=o), CRE_LABELS[o]) for o in dom.CRE_FILES
        ),
    ),
    Spec(
        "bareme-cdc",
        "Caisse des Dépôts — taux du Livret A, du LDDS et du LEP",
        dom.CDC_URL,
        (Feed(dom.CDC_URL, dom.JSON_ACCEPT, dom.parse_cdc, "taux des livrets"),),
    ),
    Spec(
        "bareme-insee",
        "INSEE — inflation (IPC base 2025) et indice de référence des loyers",
        dom.INSEE_URL.format(idbank="011814056"),
        tuple(
            Feed(dom.INSEE_URL.format(idbank=i), dom.XML_ACCEPT, dom.parse_insee, INSEE_LABELS[i])
            for i in dom.INSEE_SERIES
        ),
    ),
    Spec(
        "bareme-openfisca",
        "OpenFisca — SMIC et barème de l'impôt sur le revenu",
        dom.OPENFISCA_URL.format(name=dom.OPENFISCA_SCALE),
        (
            *(
                Feed(
                    dom.OPENFISCA_URL.format(name=p),
                    dom.JSON_ACCEPT,
                    partial(dom.parse_openfisca_values, series=s),
                    OPENFISCA_LABELS[s],
                )
                for p, s in dom.OPENFISCA_VALUES.items()
            ),
            Feed(
                dom.OPENFISCA_URL.format(name=dom.OPENFISCA_SCALE),
                dom.JSON_ACCEPT,
                dom.parse_openfisca_scale,
                "barème de l'impôt",
            ),
        ),
    ),
)
# La source qui porte, dans la Veille, la mise à jour d'une section ; le thème de veille qui la rend pertinente.
SECTION_SOURCE = {"electricity": "bareme-cre", "livrets": "bareme-cdc", "income_tax": "bareme-openfisca"}
SECTION_TOPIC = {"electricity": "energie", "livrets": "epargne", "income_tax": "impots"}
SECTION_TITLE = {
    "electricity": "tarif réglementé de l'électricité",
    "livrets": "taux des livrets réglementés",
    "income_tax": "barème de l'impôt sur le revenu",
}
INDICES: tuple[tuple[str, str, str], ...] = (
    ("insee.ipc", "Indice des prix à la consommation (base 2025, hors tabac)", ""),
    ("insee.ipc_glissement", "Inflation sur un an", " %"),
    ("insee.irl", "Indice de référence des loyers (IRL)", ""),
    ("openfisca.smic_horaire", "SMIC brut horaire", " €"),
    ("openfisca.smic_mensuel", "SMIC brut mensuel (35 h)", " €"),
    ("cdc.livret_a", "Livret A et LDDS, dernier mois publié par la Caisse des Dépôts", " %"),
    ("cdc.lep", "LEP, dernier mois publié par la Caisse des Dépôts", " %"),
)


class BaremesError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str, accept: str) -> bytes:
        return netfetch.fetch(url, http=http, resolver=resolver, accept=accept, max_bytes=MAX_BYTES, seconds=30.0).body

    return get


# ------------------------------------------------------------------ sources (dans la Veille)
def ensure_sources(db: Session) -> dict[str, WatchSource]:
    """Les quatre sources, visibles dans Veille › Sources (une source en pause n'est plus relue)."""
    slugs = [s.slug for s in SOURCES]
    rows = {s.slug: s for s in db.scalars(select(WatchSource).where(WatchSource.slug.in_(slugs)))}
    for spec in SOURCES:
        if spec.slug not in rows:
            rows[spec.slug] = WatchSource(
                slug=spec.slug,
                kind="bareme",
                name=spec.name,
                url=spec.url,
                trust="officiel",
                interval_hours=int(INTERVAL.total_seconds() // 3600),
                enabled=True,
                builtin=True,
            )
            db.add(rows[spec.slug])
    db.flush()
    return rows


def _due(source: WatchSource, now: datetime) -> bool:
    stale = source.last_ok_at is None or now - source.last_ok_at >= INTERVAL
    resting = source.last_checked_at is not None and now - source.last_checked_at < RETRY_AFTER
    return stale and not resting


# ------------------------------------------------------------------ couche au-dessus de rates.json
def _active(db: Session) -> list[RateOverride]:
    return list(db.scalars(select(RateOverride).where(RateOverride.reverted_at.is_(None))))


def overlay(db: Session) -> dict[str, tuple[Any, str]]:
    return {r.path: (json.loads(r.value), r.effective_from) for r in _active(db)}


def effective(db: Session) -> dict[str, Any]:
    """Les barèmes en vigueur d'après la base (la couche installée en mémoire en est la copie)."""
    return rates.apply(rates.static(), overlay(db))


def install(db: Session) -> int:
    """Pose la couche pour tous les lecteurs (démarrage, après une relève ou un retour au fichier)."""
    layer = overlay(db)
    rates.install(layer)
    install_irl(db)
    return len(layer)


def install_irl(db: Session) -> None:
    """L'IRL relu à l'INSEE devient celui qui vérifie ton loyer (domain.contracts) ; la table livrée reste le repli."""
    values: dict[tuple[int, int], float] = {}
    latest: datetime | None = None
    for r in db.scalars(select(RateObservation).where(RateObservation.series == "insee.irl")):
        start = dom.period_start(r.period)
        try:
            values[(start.year, (start.month - 1) // 3 + 1)] = float(r.value)
        except ValueError:
            continue
        latest = max(latest, r.fetched_at) if latest else r.fetched_at
    contracts.install_irl(values, f"{latest:%d/%m/%Y}" if latest else None)


# ------------------------------------------------------------------ relève
def _store(db: Session, observations: Iterable[dom.Observation], now: datetime) -> None:
    for o in observations:
        row = db.scalar(
            select(RateObservation).where(RateObservation.series == o.series, RateObservation.period == o.period)
        )
        if row is None:
            db.add(
                RateObservation(
                    series=o.series,
                    period=o.period,
                    value=o.value,
                    note=o.note,
                    source_url=o.source_url,
                    fetched_at=now,
                )
            )
            continue
        row.value, row.note, row.source_url, row.fetched_at = o.value, o.note, o.source_url, now
    db.flush()


def _apply(db: Session, changes: list[dom.Change], now: datetime) -> tuple[list[dom.Change], list[str]]:
    """Écrit les valeurs dans la couche. Une valeur que tu as écartée n'est pas réappliquée tant que la source
    redonne la même ; la date et la source d'une section ne suivent que si tous ses chiffres ont suivi."""
    applied: list[dom.Change] = []
    notes: list[str] = []
    for section in dict.fromkeys(c.section for c in changes):
        block = [c for c in changes if c.section == section]
        refused = False
        for c in (c for c in block if c.label):
            row = db.get(RateOverride, c.path)
            if row is not None and row.reverted_at is not None and json.loads(row.value) == c.new:
                refused = True
                notes.append(
                    f"{c.label} : la source donne toujours {c.shown_new}, que tu as écarté le "
                    f"{row.reverted_at:%d/%m/%Y} ; {c.shown_old} reste en vigueur."
                )
                continue
            _upsert(db, c, now)
            applied.append(c)
        if not refused:
            for c in (c for c in block if not c.label):
                _upsert(db, c, now)
    db.flush()
    return applied, notes


def _upsert(db: Session, c: dom.Change, now: datetime) -> None:
    row = db.get(RateOverride, c.path) or RateOverride(path=c.path)
    row.label = c.label
    row.value, row.previous = json.dumps(c.new), json.dumps(c.old)
    row.shown_old, row.shown_new = c.shown_old[:200], c.shown_new[:200]
    row.effective_from, row.source, row.source_url = c.effective_from, c.source[:300], c.source_url[:500]
    row.applied_at, row.reverted_at = now, None
    db.add(row)


def _journal(db: Session, applied: list[dom.Change], sources: dict[str, WatchSource], now: datetime) -> None:
    """Une entrée « Barèmes mis à jour » par section dans la Veille : ancienne → nouvelle valeur, et la source."""
    for section in dict.fromkeys(c.section for c in applied):
        block = [c for c in applied if c.section == section]
        source = sources[SECTION_SOURCE[section]]
        lines = " ; ".join(f"{c.label} : {c.shown_old} → {c.shown_new}" for c in block)
        summary = f"{lines}. Source : {block[0].source}."[: dw.MAX_SUMMARY]
        digest = hashlib.sha256(summary.encode()).hexdigest()
        ext = dw.external_id(source.id, f"bareme:{section}:{digest}")
        if db.scalar(select(WatchItem.id).where(WatchItem.source_id == source.id, WatchItem.external_id == ext)):
            continue
        db.add(
            WatchItem(
                source_id=source.id,
                external_id=ext,
                title=f"Barèmes mis à jour : {SECTION_TITLE[section]}",
                summary=summary,
                url=block[0].source_url,
                published_at=now,
                first_seen_at=now,
                last_verified_at=now,
                content_hash=digest,
                topics=json.dumps([SECTION_TOPIC[section]]),
                status="new",
            )
        )
    db.flush()


def refresh(
    db: Session, *, now: datetime, today: date, fetch: Fetch, actor: str, force: bool = False
) -> dict[str, Any]:
    """Relit les sources dues (toutes si `force`), garde les valeurs, applique ce qui a changé.

    Le réseau est interrogé hors transaction (le verrou d'écriture n'attend pas une source lente) ; une source en
    échec garde ses valeurs précédentes et l'erreur s'affiche avec elle."""
    consents.require(db, CONSENT)
    write_lock(db)
    sources = ensure_sources(db)
    todo = [s for s in SOURCES if sources[s.slug].enabled and (force or _due(sources[s.slug], now))]
    if not todo:
        return {
            "status": "fresh",
            "checked": 0,
            "errors": 0,
            "changes": [],
            "notes": [],
            "message": "Rien à relire : chaque source a été relue il y a moins d'un mois (ou est en pause).",
        }
    for spec in todo:
        sources[spec.slug].last_checked_at = now
    db.commit()
    # Chaque fichier à part : un fichier en échec (le Tempo de la CRE, l'IPC…) n'écarte que ses propres valeurs.
    fetched: dict[str, tuple[list[dom.Observation], list[str]]] = {}
    for spec in todo:
        obs: list[dom.Observation] = []
        failures: list[str] = []
        for feed in spec.feeds:
            try:
                obs += feed.parse(fetch(feed.url, feed.accept), feed.url, today)
            except FEED_ERRORS as exc:
                failures.append(f"{feed.label} : {_reason(exc)}")
        fetched[spec.slug] = (obs, failures)
    write_lock(db)
    db.expire_all()
    sources = ensure_sources(db)
    errors: list[str] = []
    failed = 0
    observations: list[dom.Observation] = []
    for slug, (obs, failures) in fetched.items():
        source = sources[slug]
        if failures:
            # Pas de last_ok_at : la source est relue le lendemain, jusqu'à ce que tous ses fichiers passent.
            source.last_error = " ; ".join(failures)[:300]
            kept = (
                "les valeurs précédentes restent en vigueur"
                if not obs
                else "les valeurs précédentes de ce fichier restent, le reste de la source est lu"
            )
            errors.append(f"{source.name} : {' ; '.join(failures)} ; {kept}.")
            failed += not obs
        if not obs:
            continue
        _store(db, obs, now)
        observations += obs
        if not failures:
            source.last_ok_at, source.last_error = now, None
    proposal = dom.propose(effective(db), observations, today)
    applied, refused = _apply(db, proposal.changes, now)
    _journal(db, applied, sources, now)
    notes = errors + proposal.notes + refused
    set_setting(db, "baremes_notes", [{"at": now.isoformat(), "text": n[:400]} for n in notes[:20]])
    if applied:
        audit.record(
            db,
            actor,
            "rates.update",
            changes=len(applied),
            sections=",".join(dict.fromkeys(c.section for c in applied)),
        )
    db.commit()
    install(db)
    if applied:
        message = f"{len(applied)} valeur{'s' if len(applied) > 1 else ''} mise{'s' if len(applied) > 1 else ''} à jour"
        message += " : détail ci-dessous et dans la Veille."
    elif failed == len(todo):
        message = "Relève en échec : les valeurs en vigueur restent celles d'avant."
    else:
        message = "Tout est à jour : aucune valeur n'a changé."
    return {
        "status": "failed" if failed == len(todo) else "checked",
        "checked": len(todo),
        "errors": len(errors),
        "changes": [_change_view(c) for c in applied],
        "notes": notes,
        "message": message,
    }


def _reason(exc: BaseException) -> str:
    """Le message d'une erreur attendue ; une erreur de format brute n'est pas recopiée (elle ne dirait rien)."""
    if isinstance(exc, (netfetch.FetchError, dom.BaremeError)):
        return str(exc)[:200]
    return "réponse illisible"


def _change_view(c: dom.Change) -> dict[str, Any]:
    return {
        "path": c.path,
        "label": c.label,
        "shown_old": c.shown_old,
        "shown_new": c.shown_new,
        "effective_from": c.effective_from,
        "source": c.source,
        "source_url": c.source_url,
    }


def revert(db: Session, path: str, *, now: datetime, actor: str) -> None:
    """Revenir à la valeur du fichier pour une valeur relevée. Si plus aucune valeur de la section ne vient d'une
    relève, sa date et sa source reviennent aussi à celles du fichier."""
    write_lock(db)
    row = db.get(RateOverride, path)
    if row is None or row.label is None or row.reverted_at is not None:
        raise BaremesError("Valeur introuvable ou déjà écartée", 404)
    row.reverted_at = now
    db.flush()
    section = rates.section_of(path)
    rest = [r for r in _active(db) if rates.section_of(r.path) == section]
    if not any(r.label for r in rest):
        for r in rest:
            r.reverted_at = now
    db.flush()
    audit.record(db, actor, "rates.revert", path=path)
    install(db)


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond (toutes les heures) : ne fait rien sans ton accord ni avant le mois écoulé."""
    install(db)  # la couche en mémoire recopie la base (un retour au fichier annulé par un échec d'écriture, etc.)
    sources = ensure_sources(db)  # visibles dans Veille › Sources dès le démarrage, même sans accord
    if not consents.granted(db, CONSENT):
        return {}
    if not any(s.enabled and _due(s, now) for s in sources.values()):
        return {}
    result = refresh(db, now=now, today=today, fetch=fetch, actor="system")
    return {
        "baremes_checked": int(result["checked"]),
        "baremes_changes": len(result["changes"]),
        "baremes_errors": int(result["errors"]),
    }


# ------------------------------------------------------------------ lecture (Réglages › Barèmes)
def period_label(period: str) -> str:
    start = dom.period_start(period)
    if "Q" in period:
        q = (start.month - 1) // 3 + 1
        return f"{q}{'er' if q == 1 else 'e'} trimestre {start.year}"
    if len(period) == 7:
        return f"{start:%m/%Y}"
    return f"{start:%d/%m/%Y}"


def _latest(db: Session, series: str) -> RateObservation | None:
    rows = list(db.scalars(select(RateObservation).where(RateObservation.series == series)))
    return max(rows, key=lambda r: dom.period_start(r.period), default=None)


def _indices(db: Session) -> list[dict[str, Any]]:
    out = []
    for series, label, unit in INDICES:
        row = _latest(db, series)
        if row is None:
            continue
        out.append(
            {
                "key": series,
                "label": label,
                "value": f"{dom.fr_number(dom.decimal_of(row.value), len(row.value.partition('.')[2]))}{unit}",
                "period": row.period,
                "period_label": period_label(row.period),
                "note": row.note,
                "source_url": row.source_url,
                "fetched_at": row.fetched_at.isoformat(),
            }
        )
    return out


def _irl_check(db: Session) -> dict[str, Any] | None:
    """L'IRL relu à l'INSEE face à la table livrée avec Bank (domain.contracts.IRL). Les valeurs relues priment déjà
    sur cette table pour vérifier ton loyer (install_irl) : un écart dit que la table était en retard ou fausse, pas
    que la vérification l'est."""
    rows = list(db.scalars(select(RateObservation).where(RateObservation.series == "insee.irl")))
    if not rows:
        return None
    gaps, missing = [], []
    for r in sorted(rows, key=lambda r: r.period):
        known = contracts.IRL.get(_quarter(r.period))
        if known is None:
            missing.append(period_label(r.period))
        elif abs(known - float(r.value)) > 0.005:
            gaps.append(f"{period_label(r.period)} : INSEE {r.value}, table livrée {known}")
    applied = all(contracts.irl_table().get(_quarter(r.period)) == float(r.value) for r in rows)
    return {"checked": len(rows), "gaps": gaps, "missing": missing, "applied": applied}


def _quarter(period: str) -> tuple[int, int]:
    start = dom.period_start(period)
    return start.year, (start.month - 1) // 3 + 1


def _in_effect(eff: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    trv, tax = eff.get("electricity_trv") or {}, eff.get("income_tax") or {}
    old_trv, old_tax = base.get("electricity_trv") or {}, base.get("income_tax") or {}
    return [
        {
            "key": "livrets",
            "title": "Livrets réglementés",
            "as_of": eff.get("as_of"),
            "source": eff.get("source"),
            "updated": eff.get("source") != base.get("source"),
            "items": [{"label": p["name"], "value": dom.percent(p["rate_percent"])} for p in eff.get("products") or []],
        },
        {
            "key": "electricity",
            "title": "Tarif réglementé de l'électricité (TTC)",
            "as_of": trv.get("as_of"),
            "source": trv.get("source"),
            "updated": trv.get("source") != old_trv.get("source"),
            "items": [
                {
                    "label": o["label"],
                    "value": f"{dom.euros(int(o['yearly_subscription_cents']))} par an + "
                    f"{dom.kwh_price(int(o['cents_per_100kwh']))}",
                }
                for o in trv.get("options") or []
            ],
        },
        {
            "key": "income_tax",
            "title": f"Barème de l'impôt sur le revenu (revenus {tax.get('incomes_year')})",
            "as_of": f"{tax.get('year')}-01-01" if tax.get("year") else None,
            "source": tax.get("source"),
            "updated": tax.get("source") != old_tax.get("source"),
            "items": [{"label": "Tranches", "value": dom.brackets_text(tax.get("brackets") or [])}],
        },
    ]


def view(db: Session) -> dict[str, Any]:
    sources = ensure_sources(db)
    static = rates.static()
    updates = []
    for r in db.scalars(
        select(RateOverride).where(RateOverride.label.is_not(None)).order_by(RateOverride.applied_at.desc())
    ):
        as_of = rates.section_as_of(static, rates.section_of(r.path))
        state = "reverted" if r.reverted_at else "file_newer" if as_of and r.effective_from < as_of else "active"
        updates.append(
            {
                "path": r.path,
                "label": r.label,
                "shown_old": r.shown_old,
                "shown_new": r.shown_new,
                "effective_from": r.effective_from,
                "source": r.source,
                "source_url": r.source_url,
                "applied_at": r.applied_at.isoformat(),
                "reverted_at": r.reverted_at.isoformat() if r.reverted_at else None,
                "state": state,
            }
        )
    return {
        "consent": consents.granted(db, CONSENT),
        "sources": [_source_view(spec, sources[spec.slug]) for spec in SOURCES],
        "in_effect": _in_effect(effective(db), static),
        "updates": updates,
        "indices": _indices(db),
        "irl": _irl_check(db),
        "notes": list(get_setting(db, "baremes_notes") or []),
    }


def _source_view(spec: Spec, row: WatchSource) -> dict[str, Any]:
    return {
        "slug": spec.slug,
        "name": spec.name,
        "url": spec.url,
        "enabled": row.enabled,
        "last_checked_at": _iso(row.last_checked_at),
        "last_ok_at": _iso(row.last_ok_at),
        "last_error": row.last_error,
        "next_check": _iso(row.last_ok_at + INTERVAL) if row.last_ok_at else None,
    }


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None
