"""Ton territoire, lu aux sources publiques : l'adresse géocodée (Géoplateforme de l'IGN : code INSEE, coordonnées),
l'académie de ton département (annuaire de l'Éducation nationale), les vacances scolaires de cette académie
(data.education.gouv.fr) et les jours fériés de ta zone (calendrier.api.gouv.fr). Plus le jour ouvré suivant, qui
décale un prélèvement tombant un dimanche ou un jour férié.

Module pur : aucune base, aucun réseau. Tout ce qui vient d'ailleurs est une donnée bornée et vérifiée ; une réponse
inattendue lève une erreur lisible, jamais une valeur devinée. Adresses relevées en direct le 24 octobre 2026.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

GEOCODE_URL = "https://data.geopf.fr/geocodage/search"
HOLIDAYS_URL = "https://calendrier.api.gouv.fr/jours-feries/{zone}/{year}.json"
EDUCATION = "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets"
SCHOOL_URL = f"{EDUCATION}/fr-en-calendrier-scolaire/records"
ACADEMY_URL = f"{EDUCATION}/fr-en-annuaire-education/records"
SCHOOL_PAGE = "https://www.education.gouv.fr/calendrier-scolaire-100148"
PARIS = ZoneInfo("Europe/Paris")
MAX_BREAKS = 60

# Les zones de calendrier.api.gouv.fr (toutes vérifiées en direct) ; la métropole hors Alsace-Moselle par défaut.
HOLIDAY_ZONES: dict[str, str] = {
    "57": "alsace-moselle",
    "67": "alsace-moselle",
    "68": "alsace-moselle",
    "971": "guadeloupe",
    "972": "martinique",
    "973": "guyane",
    "974": "la-reunion",
    "975": "saint-pierre-et-miquelon",
    "976": "mayotte",
    "977": "saint-barthelemy",
    "978": "saint-martin",
    "986": "wallis-et-futuna",
    "987": "polynesie-francaise",
    "988": "nouvelle-caledonie",
}
ZONE_LABELS = {
    "metropole": "métropole",
    "alsace-moselle": "Alsace-Moselle",
    "guadeloupe": "Guadeloupe",
    "martinique": "Martinique",
    "guyane": "Guyane",
    "la-reunion": "La Réunion",
    "saint-pierre-et-miquelon": "Saint-Pierre-et-Miquelon",
    "mayotte": "Mayotte",
    "saint-barthelemy": "Saint-Barthélemy",
    "saint-martin": "Saint-Martin",
    "wallis-et-futuna": "Wallis-et-Futuna",
    "polynesie-francaise": "Polynésie française",
    "nouvelle-caledonie": "Nouvelle-Calédonie",
}
# L'annuaire et le calendrier scolaire ne nomment pas tout à fait pareil deux académies.
ACADEMY_ALIASES = {"La Réunion": "Réunion", "Polynésie Française": "Polynésie"}
_CITYCODE = re.compile(r"(?:\d{5}|2[AB]\d{3})")
_DEPCODE = re.compile(r"(?:\d{2,3}|2[AB])")
_NAME = re.compile(r"[^\W\d_][\w'’ .()-]{0,59}")
_BAN_ID = re.compile(r"(?:\d{5}|2[AB]\d{3})_[0-9a-z]{4,5}_\d{5}(?:_[0-9a-z]{1,6})?", re.I)


class TerritoryError(ValueError):
    """Réponse illisible ou inattendue : message lisible, la valeur précédente reste."""


@dataclass(frozen=True)
class Place:
    label: str  # l'adresse telle que la Géoplateforme l'a reconnue
    citycode: str  # code INSEE de la commune (arrondissement à Paris, Lyon, Marseille)
    postcode: str | None
    city: str
    depcode: str
    lat: float
    lon: float
    score: float  # 0 à 1 : confiance du géocodeur
    precision: str  # housenumber | street | locality | municipality
    # Identifiant de l'adresse dans la Base adresse nationale (« 31555_2468_00014 ») : seulement au numéro près. Il
    # retrouve le DPE de ton immeuble dans la base de l'ADEME (#34).
    ban_id: str | None = None


@dataclass(frozen=True)
class SchoolBreak:
    label: str  # « Vacances de Noël »
    start: date  # premier jour sans classe
    end: date  # dernier jour sans classe (la veille de la reprise)
    year: str  # année scolaire « 2026-2027 »


def _json(body: bytes, who: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:  # RecursionError : JSON imbriqué à l'excès
        raise TerritoryError(f"{who} : réponse illisible (JSON attendu)") from exc


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


# ------------------------------------------------------------------ géocodage (Géoplateforme)
def clean_query(raw: str) -> str:
    """L'adresse que tu as tapée, seule chose envoyée : bornée, sans caractère de contrôle."""
    text = " ".join(raw.split())
    if not 3 <= len(text) <= 200 or any(ord(c) < 32 for c in raw if c not in "\t\n\r"):
        raise TerritoryError("Une adresse ou une commune, entre 3 et 200 caractères")
    return text


def geocode_url(query: str) -> str:
    return f"{GEOCODE_URL}?{urlencode({'q': clean_query(query), 'limit': '1'})}"


def parse_geocode(body: bytes) -> Place | None:
    """Le meilleur résultat, ou None si l'adresse n'est pas reconnue. Code INSEE et coordonnées vérifiés."""
    data = _json(body, "Géoplateforme")
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise TerritoryError("Géoplateforme : réponse inattendue")
    if not data["features"]:
        return None
    feature = data["features"][0]
    props = feature.get("properties") if isinstance(feature, dict) else None
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(props, dict) or not isinstance(coords, list) or len(coords) != 2:
        raise TerritoryError("Géoplateforme : résultat incomplet")
    citycode, depcode = str(props.get("citycode") or ""), str(props.get("depcode") or "")
    if not _CITYCODE.fullmatch(citycode) or not _DEPCODE.fullmatch(depcode):
        raise TerritoryError("Géoplateforme : code de commune inattendu")
    try:
        lon, lat, score = float(coords[0]), float(coords[1]), float(props.get("score") or 0)
    except (TypeError, ValueError) as exc:
        raise TerritoryError("Géoplateforme : coordonnées illisibles") from exc
    if not (-180 <= lon <= 180 and -90 <= lat <= 90 and 0 <= score <= 1):
        raise TerritoryError("Géoplateforme : coordonnées hors bornes")
    postcode = str(props.get("postcode") or "")
    precision = _text(props.get("type"), 20) or "inconnue"
    ban_id = str(props.get("id") or "")
    return Place(
        label=_text(props.get("label"), 200),
        citycode=citycode,
        postcode=postcode if re.fullmatch(r"\d{5}", postcode) else None,
        city=_text(props.get("city"), 80),
        depcode=depcode,
        lat=round(lat, 6),
        lon=round(lon, 6),
        score=round(score, 3),
        precision=precision,
        ban_id=ban_id if precision == "housenumber" and _BAN_ID.fullmatch(ban_id) else None,
    )


# ------------------------------------------------------------------ jours fériés (calendrier.api.gouv.fr)
def holiday_zone(depcode: str | None) -> str:
    return HOLIDAY_ZONES.get(str(depcode or ""), "metropole")


def holidays_url(zone: str, year: int) -> str:
    if zone not in ZONE_LABELS:
        raise TerritoryError("Zone de jours fériés inconnue")
    return HOLIDAYS_URL.format(zone=zone, year=year)


def parse_holidays(body: bytes, year: int) -> dict[date, str]:
    """{date: nom} pour l'année demandée : une date hors de l'année, ou un nombre de jours improbable, est refusé."""
    data = _json(body, "Jours fériés")
    if not isinstance(data, dict) or not 5 <= len(data) <= 20:
        raise TerritoryError("Jours fériés : réponse inattendue")
    out: dict[date, str] = {}
    for key, name in data.items():
        try:
            day = date.fromisoformat(str(key))
        except ValueError as exc:
            raise TerritoryError("Jours fériés : date illisible") from exc
        label = _text(name, 60)
        if day.year != year or not label:
            raise TerritoryError("Jours fériés : date hors de l'année demandée")
        out[day] = label
    return dict(sorted(out.items()))


# ------------------------------------------------------------------ académie et vacances scolaires
def academy_url(depcode: str) -> str:
    if not _DEPCODE.fullmatch(depcode):
        raise TerritoryError("Département inconnu")
    params = {"select": "libelle_academie", "where": f'code_departement="{depcode.zfill(3)}"', "limit": "1"}
    return f"{ACADEMY_URL}?{urlencode(params)}"


def parse_academy(body: bytes) -> str | None:
    """Le nom de l'académie tel que l'écrit le calendrier scolaire, ou None si l'annuaire ne la connaît pas."""
    data = _json(body, "Annuaire de l'Éducation")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise TerritoryError("Annuaire de l'Éducation : réponse inattendue")
    if not results or not isinstance(results[0], dict):
        return None
    name = _text(results[0].get("libelle_academie"), 60)
    if not _NAME.fullmatch(name):
        raise TerritoryError("Annuaire de l'Éducation : académie illisible")
    return ACADEMY_ALIASES.get(name, name)


def school_url(academy: str, since: date) -> str:
    if not _NAME.fullmatch(academy):
        raise TerritoryError("Académie inconnue")
    params = {
        "select": "description,population,start_date,end_date,location,zones,annee_scolaire",
        "where": f'location="{academy}" and end_date>="{since.isoformat()}"',
        "order_by": "start_date",
        "limit": "40",
    }
    return f"{SCHOOL_URL}?{urlencode(params)}"


def _local_day(raw: Any) -> date:
    """Les dates du jeu sont en UTC (« 2026-10-16T22:00:00+00:00 ») : c'est le 17 octobre à Paris."""
    moment = datetime.fromisoformat(str(raw))
    if moment.tzinfo is None:
        raise ValueError("date sans fuseau")
    return moment.astimezone(PARIS).date()


def parse_school(body: bytes, academy: str) -> tuple[str | None, list[SchoolBreak]]:
    """(zone, vacances) de l'académie. La date de fin du jeu est le jour de la reprise : le dernier jour sans classe
    est la veille. Les lignes réservées aux enseignants sont écartées (les élèves reprennent plus tard)."""
    data = _json(body, "Calendrier scolaire")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise TerritoryError("Calendrier scolaire : réponse inattendue")
    zone: str | None = None
    out: dict[tuple[str, date], SchoolBreak] = {}
    for row in results[:MAX_BREAKS]:
        if not isinstance(row, dict) or _text(row.get("location"), 60) != academy:
            continue
        if _text(row.get("population"), 20) == "Enseignants":
            continue
        try:
            start, reprise = _local_day(row.get("start_date")), _local_day(row.get("end_date"))
        except (TypeError, ValueError) as exc:
            raise TerritoryError("Calendrier scolaire : date illisible") from exc
        end = reprise - timedelta(days=1) if reprise > start else start
        if (end - start).days > 100:
            raise TerritoryError("Calendrier scolaire : période improbable")
        label = _text(row.get("description"), 60) or "Vacances scolaires"
        zone = zone or (_text(row.get("zones"), 40) or None)
        out[(label, start)] = SchoolBreak(label, start, end, _text(row.get("annee_scolaire"), 9))
    return zone, sorted(out.values(), key=lambda b: b.start)


# ------------------------------------------------------------------ jours ouvrés
def is_business_day(d: date, holidays: Collection[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def next_business_day(d: date, holidays: Collection[date]) -> date:
    """Un prélèvement qui tombe un samedi, un dimanche ou un jour férié part le jour ouvré suivant."""
    guard = 0
    while not is_business_day(d, holidays) and guard < 10:
        d += timedelta(days=1)
        guard += 1
    return d


def breaks_between(breaks: Iterable[SchoolBreak], start: date, end: date) -> list[SchoolBreak]:
    return [b for b in breaks if b.end >= start and b.start <= end]


def day_fr(d: date) -> str:
    return f"{d:%d/%m/%Y}"
