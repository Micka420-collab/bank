"""Ton territoire : l'adresse géocodée (code INSEE, coordonnées, département ; chiffrés ici), les jours fériés de ta
zone et les vacances scolaires de ton académie, relus aux sources publiques et gardés datés et sourcés.

Deux accords, désactivés par défaut : « Ton adresse située sur la carte » (seule l'adresse que tu tapes part, vers le
géocodeur de l'IGN) et « Jours fériés et vacances scolaires » (la zone, le département ou l'académie partent, jamais
l'adresse). La prévision et l'année qui vient lisent ce qui est gardé ici, sans réseau ; une relève ratée garde la
précédente et le dit. Tout passe par netfetch."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import territory as dom
from app.models import HouseholdPlace, PublicCalendar
from app.security import audit, netfetch
from app.services import consents

CONSENT_GEO = "geocoding"
CONSENT_CAL = "calendar"
SCHOOL_EVERY = timedelta(days=30)  # le calendrier scolaire change rarement (nouvel arrêté) : une relève par mois
HOLIDAYS_EVERY = timedelta(days=180)  # les jours fériés d'une année sont connus d'avance : relus deux fois par an
RETRY_AFTER = timedelta(hours=20)  # une source en échec est réessayée le lendemain
MAX_BYTES = 500_000
GEO_SOURCE = "Géoplateforme (IGN), service de géocodage"
HOLIDAYS_SOURCE = "calendrier.api.gouv.fr (jours fériés, Etalab)"
SCHOOL_SOURCE = "Ministère de l'Éducation nationale, calendrier scolaire (data.education.gouv.fr)"

Fetch = Callable[[str], bytes]  # adresse → corps ; lève netfetch.FetchError


class TerritoryError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str) -> bytes:
        return netfetch.fetch(
            url, http=http, resolver=resolver, accept="application/json", max_bytes=MAX_BYTES, seconds=20.0
        ).body

    return get


# ------------------------------------------------------------------ ton adresse
def place(db: Session) -> dict[str, Any] | None:
    row = db.get(HouseholdPlace, 1)
    if row is None:
        return None
    try:
        data = json.loads(row.data)
    except ValueError:
        return None
    return dict(data) if isinstance(data, dict) else None


def _save_place(db: Session, data: dict[str, Any]) -> None:
    row = db.get(HouseholdPlace, 1) or HouseholdPlace(id=1, data="{}")
    row.data = json.dumps(data)
    db.add(row)
    db.flush()


def geocode(db: Session, query: str, *, fetch: Fetch, now: datetime, actor: str) -> dict[str, Any]:
    """Envoie l'adresse tapée (et elle seule) au géocodeur de l'IGN, garde le résultat chiffré. Une adresse non
    reconnue ou un géocodeur indisponible ne change rien à l'adresse gardée."""
    consents.require(db, CONSENT_GEO)
    try:
        clean = dom.clean_query(query)
        url = dom.geocode_url(clean)
    except dom.TerritoryError as exc:
        raise TerritoryError(str(exc)) from exc
    try:
        found = dom.parse_geocode(fetch(url))
    except netfetch.FetchError as exc:
        raise TerritoryError(f"Géocodage indisponible ({exc}) : rien n'a changé.", 502) from exc
    except dom.TerritoryError as exc:
        raise TerritoryError(f"{exc} : rien n'a changé.", 502) from exc
    if found is None:
        raise TerritoryError("Adresse introuvable : vérifie l'orthographe, ou tape seulement ta commune.", 404)
    write_lock(db)
    before = place(db) or {}
    data: dict[str, Any] = {
        **asdict(found),
        "query": clean,
        "geocoded_at": now.isoformat(),
        "source": GEO_SOURCE,
        "source_url": dom.GEOCODE_URL,
    }
    if before.get("depcode") == found.depcode and before.get("academy"):  # même département : même académie
        data["academy"], data["academy_checked_at"] = before["academy"], before.get("academy_checked_at")
    _save_place(db, data)
    # Le journal dit ce qui a changé, jamais l'adresse.
    audit.record(
        db, actor, "place.geocode", precision=found.precision, commune_changed=before.get("citycode") != found.citycode
    )
    return data


def remove_place(db: Session, *, actor: str) -> None:
    write_lock(db)
    row = db.get(HouseholdPlace, 1)
    if row is None:
        raise TerritoryError("Aucune adresse enregistrée", 404)
    db.delete(row)
    school = db.get(PublicCalendar, "scolaire")
    if school is not None:
        db.delete(school)  # les vacances de ton académie disent où tu habites
    db.flush()
    audit.record(db, actor, "place.remove")


def home_suggestion(db: Session) -> dict[str, str] | None:
    """La commune de ton adresse, pour pré-remplir ton logement (valeur DVF) quand il n'est pas encore décrit."""
    p = place(db)
    if not p or not p.get("citycode"):
        return None
    return {"code": str(p["citycode"]), "commune": str(p.get("city") or "")}


# ------------------------------------------------------------------ calendrier : relève
def _cal(db: Session, key: str) -> PublicCalendar | None:
    return db.get(PublicCalendar, key)


def _data(row: PublicCalendar | None) -> dict[str, Any]:
    if row is None or not row.data:
        return {}
    try:
        data = json.loads(row.data)
    except ValueError:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _resting(row: PublicCalendar | None, now: datetime) -> bool:
    return bool(row and row.last_error and row.last_attempt_at and now - row.last_attempt_at < RETRY_AFTER)


@dataclass
class _Plan:
    zone: str
    years: list[int] = field(default_factory=list)
    school: bool = False
    depcode: str | None = None
    academy: str | None = None


def _plan(db: Session, now: datetime, today: date, force: bool) -> _Plan:
    p = place(db) or {}
    plan = _Plan(zone=dom.holiday_zone(p.get("depcode")))
    for year in (today.year, today.year + 1):
        row = _cal(db, f"feries:{year}")
        stale = (
            row is None
            or row.fetched_at is None
            or _data(row).get("zone") != plan.zone
            or now - row.fetched_at >= HOLIDAYS_EVERY
        )
        if force or (stale and not _resting(row, now)):
            plan.years.append(year)
    if p.get("depcode"):
        row = _cal(db, "scolaire")
        cached = _data(row)
        plan.depcode = str(p["depcode"])
        plan.academy = str(p["academy"]) if p.get("academy") else None
        stale = (
            row is None
            or row.fetched_at is None
            or cached.get("academy") != plan.academy
            or plan.academy is None
            or now - row.fetched_at >= SCHOOL_EVERY
        )
        plan.school = force or (stale and not _resting(row, now))
    return plan


def refresh_calendar(db: Session, *, now: datetime, today: date, fetch: Fetch, force: bool = False) -> dict[str, Any]:
    """Relit ce qui est dû (tout si `force`) : jours fériés de l'année et de la suivante pour ta zone, académie de
    ton département et ses vacances. Le réseau est interrogé hors transaction ; une relève ratée garde la précédente."""
    consents.require(db, CONSENT_CAL)
    write_lock(db)
    plan = _plan(db, now, today, force)
    keys = [f"feries:{y}" for y in plan.years] + (["scolaire"] if plan.school else [])
    for key in keys:
        row = _cal(db, key) or PublicCalendar(key=key)
        row.last_attempt_at = now
        db.add(row)
    db.commit()

    holidays: dict[int, tuple[str, dict[date, str]] | Exception] = {}
    for year in plan.years:
        url = dom.holidays_url(plan.zone, year)
        try:
            holidays[year] = (url, dom.parse_holidays(fetch(url), year))
        except (netfetch.FetchError, dom.TerritoryError) as exc:
            holidays[year] = exc
    academy, school, school_error = plan.academy, None, None
    if plan.school and plan.depcode:
        try:
            if academy is None:
                academy = dom.parse_academy(fetch(dom.academy_url(plan.depcode)))
            if academy is None:
                school_error = "Académie de ton département introuvable dans l'annuaire de l'Éducation nationale"
            else:
                url = dom.school_url(academy, today - timedelta(days=31))
                school = (url, *dom.parse_school(fetch(url), academy))
        except (netfetch.FetchError, dom.TerritoryError) as exc:
            school_error = str(exc)

    write_lock(db)
    db.expire_all()
    errors: list[str] = []
    for year, result in holidays.items():
        row = _cal(db, f"feries:{year}") or PublicCalendar(key=f"feries:{year}")
        if isinstance(result, Exception):
            row.last_error = str(result)[:300]
            errors.append(f"Jours fériés {year} : {result} ; ceux d'avant restent.")
        else:
            url, days = result
            row.data = json.dumps({"zone": plan.zone, "days": {d.isoformat(): n for d, n in days.items()}})
            row.source_url, row.fetched_at, row.last_error = url, now, None
        db.add(row)
    if plan.school:
        row = _cal(db, "scolaire") or PublicCalendar(key="scolaire")
        if school is not None and academy is not None:
            url, zone, breaks = school
            row.data = json.dumps(
                {
                    "academy": academy,
                    "zone": zone,
                    "breaks": [
                        {"label": b.label, "start": b.start.isoformat(), "end": b.end.isoformat(), "year": b.year}
                        for b in breaks
                    ],
                }
            )
            row.source_url, row.fetched_at, row.last_error = url, now, None
            p = place(db)
            if p is not None and p.get("depcode") == plan.depcode and p.get("academy") != academy:
                _save_place(db, {**p, "academy": academy, "academy_checked_at": now.isoformat()})
        else:
            row.last_error = (school_error or "Calendrier scolaire indisponible")[:300]
            errors.append(f"Vacances scolaires : {row.last_error} ; celles d'avant restent.")
        db.add(row)
    db.flush()
    return {"checked": len(keys), "errors": errors}


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond (toutes les heures) : rien sans ton accord, rien tant que ce qui est gardé est frais."""
    if not consents.granted(db, CONSENT_CAL):
        return {}
    plan = _plan(db, now, today, force=False)
    if not plan.years and not plan.school:
        return {}
    result = refresh_calendar(db, now=now, today=today, fetch=fetch)
    return {"calendar_checked": int(result["checked"]), "calendar_errors": len(result["errors"])}


# ------------------------------------------------------------------ calendrier : lecture (sans réseau)
@dataclass(frozen=True)
class Calendar:
    holidays: dict[date, str]
    breaks: list[dom.SchoolBreak]
    zone: str
    academy: str | None
    school_zone: str | None

    @property
    def days(self) -> frozenset[date]:
        return frozenset(self.holidays)


def calendar(db: Session) -> Calendar | None:
    """Ce qui est gardé, si tu as donné ton accord ; None sinon (la prévision ne décale alors rien)."""
    if not consents.granted(db, CONSENT_CAL):
        return None
    holidays: dict[date, str] = {}
    zone = "metropole"
    for row in db.scalars(select(PublicCalendar).where(PublicCalendar.key.like("feries:%"))):
        data = _data(row)
        zone = str(data.get("zone") or zone)
        for k, v in (data.get("days") or {}).items():
            try:
                holidays[date.fromisoformat(k)] = str(v)[:60]
            except ValueError:
                continue
    school = _data(_cal(db, "scolaire"))
    breaks: list[dom.SchoolBreak] = []
    for b in school.get("breaks") or []:
        try:
            breaks.append(
                dom.SchoolBreak(
                    str(b["label"])[:60], date.fromisoformat(b["start"]), date.fromisoformat(b["end"]), str(b["year"])
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not holidays and not breaks:
        return None
    return Calendar(dict(sorted(holidays.items())), breaks, zone, school.get("academy"), school.get("zone"))


def _source(row: PublicCalendar | None, name: str) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "name": name,
        "url": row.source_url,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "last_error": row.last_error,
    }


def view(db: Session, today: date) -> dict[str, Any]:
    p = place(db)
    cal = calendar(db)
    horizon = today + timedelta(days=366)
    sources = [
        s
        for s in (
            *(_source(_cal(db, f"feries:{y}"), f"Jours fériés {y}") for y in (today.year, today.year + 1)),
            _source(_cal(db, "scolaire"), "Vacances scolaires"),
        )
        if s is not None
    ]
    return {
        "consents": {"geocoding": consents.granted(db, CONSENT_GEO), "calendar": consents.granted(db, CONSENT_CAL)},
        "place": None
        if p is None
        else {
            "query": p.get("query"),
            "label": p.get("label"),
            "city": p.get("city"),
            "citycode": p.get("citycode"),
            "postcode": p.get("postcode"),
            "depcode": p.get("depcode"),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
            "score": p.get("score"),
            "precision": p.get("precision"),
            "academy": p.get("academy"),
            "geocoded_at": p.get("geocoded_at"),
            "source": p.get("source"),
            "source_url": p.get("source_url"),
        },
        "zone": dom.holiday_zone((p or {}).get("depcode")),
        "zone_label": dom.ZONE_LABELS[dom.holiday_zone((p or {}).get("depcode"))],
        "holidays": []
        if cal is None
        else [{"day": d.isoformat(), "name": n} for d, n in cal.holidays.items() if today <= d <= horizon][:15],
        "school": None
        if cal is None or not cal.academy
        else {
            "academy": cal.academy,
            "zone": cal.school_zone,
            "breaks": [
                {"label": b.label, "start": b.start.isoformat(), "end": b.end.isoformat()}
                for b in dom.breaks_between(cal.breaks, today, horizon)
            ],
        },
        "sources": sources,
        "school_page": dom.SCHOOL_PAGE,
    }
