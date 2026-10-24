"""Encadrement des loyers (#34) : les communes où le loyer est plafonné, d'après la fiche F1314 de Service-Public, et
les loyers de référence de Paris publiés en open data par la Ville.

Module pur : aucune base, aucun réseau. Relevé en direct le 24 octobre 2026 :
* Service-Public, fiche F1314 (« Vérifié le 01 août 2026 ») : liste des territoires, date d'entrée de chacun, liens
  vers les simulateurs officiels ; l'expérimentation y prend fin le 24 novembre 2026 (l'arrêté parisien du 12 juin
  2026 le dit aussi : « prend fin le 24 novembre 2026 à minuit ») ;
* Ville de Paris, jeu « logement-encadrement-des-loyers » (opendata.paris.fr, licence ODbL) : loyers de référence,
  majorés et minorés par quartier, nombre de pièces (1 à 4, « 4 et + » selon l'arrêté), époque de construction et
  type de location, années 2019 à 2025 ; « mis à jour chaque année le 1er juillet » d'après sa fiche ; valeur
  indicative, la valeur opposable est celle de l'arrêté préfectoral ;
* les autres territoires n'ont pas d'API ouverte vérifiée : Bank renvoie vers le simulateur officiel.

Rien de toi ne part : la fiche, les quartiers et les loyers de référence de Paris sont des fichiers entiers ; le
quartier de ton adresse se trouve ici, point dans polygone.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

# ------------------------------------------------------------------ la fiche F1314 et sa liste
PAGE_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/F1314"
PAGE_SOURCE = "Service-Public, fiche F1314 « En quoi consiste l'encadrement des loyers à respecter en zone tendue ? »"
LIST_VERIFIED = date(2026, 8, 1)  # « Vérifié le 01 août 2026 » sur la fiche relue
LIST_CHECKED_ON = "24 octobre 2026"
END = date(2026, 11, 24)  # fin de l'expérimentation écrite sur la fiche et dans l'arrêté parisien
CDC_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/F1216"
CDC_SOURCE = "Service-Public, fiche F1216 (commission départementale de conciliation)"
MAX_PAGE = 2_000_000
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre")  # fmt: skip
_VERIFIED = re.compile(r"Vérifié le (\d{1,2}) (" + "|".join(_MONTHS) + r") (\d{4})")


@dataclass(frozen=True)
class Territory:
    key: str
    name: str
    marker: str  # nom tel qu'il figure sur la fiche (vérifié à chaque relecture)
    codes: frozenset[str]  # codes INSEE couverts en entier (arrondissements compris)
    partial: frozenset[str]  # une partie seulement du territoire de la commune
    start: date  # bail signé ou renouvelé depuis cette date
    simulator: str  # simulateur officiel cité par la fiche
    simulator_label: str


def _codes(*codes: str) -> frozenset[str]:
    return frozenset(codes)


PARIS_CODES = _codes("75056", *(f"751{i:02d}" for i in range(1, 21)))
TERRITORIES: tuple[Territory, ...] = (
    Territory("paris", "Paris", "Paris", PARIS_CODES, frozenset(), date(2019, 7, 1),
              "https://teleservices.paris.fr/encadrementdesloyers/", "Paris : tester mon loyer"),
    Territory("est_ensemble", "Est Ensemble", "Est Ensemble",
              _codes("93006", "93008", "93010", "93061", "93045", "93048", "93053", "93055", "93063"), frozenset(),
              date(2021, 12, 1),
              "http://www.referenceloyer.drihl.ile-de-france.developpement-durable.gouv.fr/est-ensemble/",
              "Est Ensemble : estimer les loyers de référence"),
    # Pierrefitte-sur-Seine fait partie de Saint-Denis (93066) depuis le 1er janvier 2025 (geo.api.gouv.fr).
    Territory("plaine_commune", "Plaine Commune", "Plaine Commune",
              _codes("93001", "93027", "93031", "93039", "93066", "93070", "93072", "93079"), frozenset(),
              date(2021, 6, 1),
              "http://www.referenceloyer.drihl.ile-de-france.developpement-durable.gouv.fr/plaine-commune/",
              "Plaine Commune : estimer les loyers de référence"),
    Territory("bordeaux", "Bordeaux", "Bordeaux", _codes("33063"), frozenset(), date(2022, 7, 15),
              "https://geo.bordeaux-metropole.fr/encadrement-des-loyers/",
              "Bordeaux : estimer les loyers de référence"),
    Territory("grenoble", "Grenoble-Alpes Métropole", "Grenoble-Alpes Métropole",
              _codes("38057", "38111", "38150", "38158", "38170", "38179", "38229", "38271", "38309", "38516",
                     "38486", "38524", "38533"),
              _codes("38151", "38169", "38185", "38317", "38382", "38421", "38474", "38485"),
              date(2026, 1, 20), "https://www.service-public.gouv.fr/particuliers/vosdroits/R72371",
              "Grenoble-Alpes Métropole : savoir si le logement est concerné et tester son loyer"),
    # Hellemmes et Lomme, communes associées, ont le code de Lille (59350) dans la Base adresse nationale.
    Territory("lille", "Lille, Hellemmes et Lomme", "Lille, Hellemmes et Lomme", _codes("59350"), frozenset(),
              date(2020, 3, 1), "https://www.service-public.gouv.fr/particuliers/vosdroits/R55687",
              "Lille, Hellemmes et Lomme : estimer les loyers de référence"),
    Territory("lyon", "Lyon et Villeurbanne", "Lyon et Villeurbanne",
              _codes("69123", *(f"6938{i}" for i in range(1, 10)), "69266"), frozenset(), date(2021, 11, 1),
              "https://demarches.toodego.com/logement/encadrement-des-loyers/",
              "Lyon et Villeurbanne : tester mon loyer"),
    Territory("montpellier", "Montpellier", "Montpellier", _codes("34172"), frozenset(), date(2022, 7, 1),
              "https://eservices.montpellier3m.fr/habitat/encadrement-des-loyers/",
              "Montpellier : estimer les loyers de référence"),
    Territory("pays_basque", "Pays Basque", "Pays Basque",
              _codes("64009", "64024", "64035", "64038", "64065", "64100", "64102", "64122", "64125", "64130",
                     "64140", "64189", "64249", "64260", "64282", "64304", "64317", "64407", "64483", "64496",
                     "64540", "64545", "64547", "64558"),
              frozenset(), date(2024, 11, 25), "https://www.service-public.gouv.fr/particuliers/vosdroits/R70609",
              "Pays Basque : estimer les loyers de référence"),
)  # fmt: skip
BY_KEY = {t.key: t for t in TERRITORIES}


def territory_of(code: str | None) -> tuple[Territory, bool] | None:
    """(territoire encadré, en partie seulement ?) pour un code INSEE, ou None : hors de la liste de la fiche."""
    for t in TERRITORIES:
        if code in t.codes:
            return t, False
        if code in t.partial:
            return t, True
    return None


class RentControlError(ValueError):
    """Réponse illisible ou inattendue : message lisible, la relève précédente reste."""


@dataclass(frozen=True)
class PageCheck:
    verified: date  # « Vérifié le … » lu sur la fiche
    missing: tuple[str, ...]  # territoires de la liste de Bank absents de la fiche

    @property
    def changed(self) -> bool:
        """La fiche a été revue depuis la liste de Bank, ou un territoire n'y figure plus : à relire."""
        return self.verified > LIST_VERIFIED or bool(self.missing)


def parse_page(body: bytes) -> PageCheck:
    """La date « Vérifié le » de la fiche et les territoires de Bank qui n'y figurent plus : une liste datée qui dit
    quand elle est dépassée, plutôt qu'une liste devinée dans une page qui change de forme."""
    try:
        text = html.unescape(re.sub(r"<[^>]+>", " ", body.decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise RentControlError("Service-Public : page illisible") from exc
    text = " ".join(text.split())
    if "encadrement des loyers" not in text.lower():
        raise RentControlError("Service-Public : ce n'est pas la fiche de l'encadrement des loyers")
    m = _VERIFIED.search(text)
    if not m:
        raise RentControlError("Service-Public : date de vérification introuvable")
    try:
        verified = date(int(m.group(3)), _MONTHS.index(m.group(2)) + 1, int(m.group(1)))
    except ValueError as exc:  # « 31 février »
        raise RentControlError("Service-Public : date de vérification impossible") from exc
    missing = tuple(t.name for t in TERRITORIES if t.marker not in text)
    return PageCheck(verified, missing)


# ------------------------------------------------------------------ Paris : loyers de référence (open data)
PARIS_API = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/logement-encadrement-des-loyers"
PARIS_PAGE = "https://opendata.paris.fr/explore/dataset/logement-encadrement-des-loyers/"
PARIS_SOURCE = "Ville de Paris, jeu « Logement - Encadrement des loyers » (opendata.paris.fr, ODbL)"
PARIS_ARRETE_2026 = (
    "https://cdn.paris.fr/paris/2026/06/17/arrete-prefectoral-encadrement-des-loyers-12-06-2026-publie-n4wk.pdf"
)
MAX_PARIS_ROWS = 8_000_000  # toutes les années, sans géométrie : un peu plus de 3 Mo
MAX_PARIS_SHAPES = 2_000_000  # les 80 quartiers : environ 330 Ko
# Époques du jeu, telles qu'écrites dans ses lignes, et leur libellé dans l'arrêté.
EPOQUES: dict[str, str] = {
    "Avant 1946": "avant 1946",
    "1946-1970": "de 1946 à 1970",
    "1971-1990": "de 1971 à 1990",
    "Apres 1990": "après 1990",
}
MAX_ROOMS = 4  # « 4 et + » dans l'arrêté
_REF_FIELDS = "annee,id_quartier,nom_quartier,piece,epoque,meuble_txt,ref,max,min"


def paris_meta_url() -> str:
    return PARIS_API


def paris_rows_url() -> str:
    """Toutes les lignes, toutes années, sans géométrie : aucun paramètre ne dit où tu habites."""
    return f"{PARIS_API}/exports/json?{urlencode({'select': _REF_FIELDS})}"


def paris_shapes_url(year: int) -> str:
    """Les contours des 80 quartiers (une ligne par quartier pour une année et une catégorie quelconques)."""
    where = f'annee="{year}" and piece=1 and epoque="Avant 1946" and meuble_txt="non meublé"'
    return f"{PARIS_API}/exports/geojson?{urlencode({'select': 'id_quartier,nom_quartier,geo_shape', 'where': where})}"


def _json(body: bytes, who: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise RentControlError(f"{who} : réponse illisible (JSON attendu)") from exc


def parse_paris_meta(body: bytes) -> str:
    """La date de mise à jour du jeu : on ne retélécharge les fichiers que si elle change."""
    data = _json(body, "opendata.paris.fr")
    try:
        modified = str(data["metas"]["default"]["modified"])
    except (KeyError, TypeError) as exc:
        raise RentControlError("opendata.paris.fr : fiche du jeu inattendue") from exc
    if data.get("dataset_id") != "logement-encadrement-des-loyers" or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T.*", modified
    ):
        raise RentControlError("opendata.paris.fr : fiche du jeu inattendue")
    return modified


Ring = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Quartier:
    id: int
    name: str
    rings: tuple[Ring, ...]  # contours extérieurs (lon, lat)


def parse_quartiers(body: bytes) -> list[Quartier]:
    data = _json(body, "opendata.paris.fr")
    feats = data.get("features") if isinstance(data, dict) else None
    if not isinstance(feats, list) or not feats:
        raise RentControlError("opendata.paris.fr : contours des quartiers inattendus")
    out: list[Quartier] = []
    for f in feats:
        try:
            props, geom = f["properties"], f["geometry"]
            qid, name = int(props["id_quartier"]), " ".join(str(props["nom_quartier"]).split())[:80]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            rings = tuple(tuple((float(x), float(y)) for x, y in poly[0]) for poly in polys)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RentControlError("opendata.paris.fr : contour illisible") from exc
        if not 1 <= qid <= 80 or any(len(r) < 4 for r in rings):
            raise RentControlError("opendata.paris.fr : contour hors bornes")
        out.append(Quartier(qid, name, rings))
    return out


def _inside(lon: float, lat: float, ring: Ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def quartier_at(lon: float, lat: float, quartiers: Sequence[Quartier]) -> Quartier | None:
    """Le quartier administratif qui contient ton adresse, calculé ici : ni l'adresse ni les coordonnées ne partent."""
    return next((q for q in quartiers if any(_inside(lon, lat, r) for r in q.rings)), None)


@dataclass(frozen=True)
class Reference:
    year: int  # année du jeu : en vigueur à partir du 1er juillet
    rooms: int  # 1 à 4 (« 4 et + »)
    epoque: str  # clé de EPOQUES
    furnished: bool
    ref: float  # €/m² de surface habitable, hors charges
    high: float  # loyer de référence majoré
    low: float  # minoré

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Reference:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def load_rows(body: bytes) -> list[dict[str, Any]]:
    rows = _json(body, "opendata.paris.fr")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise RentControlError("opendata.paris.fr : loyers de référence inattendus")
    return rows


def dataset_years(rows: Sequence[dict[str, Any]]) -> list[int]:
    """Les années publiées (2019 à 2025 le 24 octobre 2026 : celle de 2026 n'y est pas encore)."""
    years: set[int] = set()
    for r in rows:
        try:
            years.add(int(str(r["annee"])))
        except (KeyError, ValueError) as exc:
            raise RentControlError("opendata.paris.fr : année illisible") from exc
    out = sorted(y for y in years if 2019 <= y <= 2100)
    if not out:
        raise RentControlError("opendata.paris.fr : aucune année exploitable")
    return out


def parse_references(rows: Sequence[dict[str, Any]], quartier: int) -> list[Reference]:
    """Les lignes du quartier, toutes années. Une valeur hors bornes ou une catégorie inconnue refuse tout."""
    out: list[Reference] = []
    for r in rows:
        if r.get("id_quartier") != quartier:
            continue
        try:
            year, rooms, ref, high, low = (
                int(r["annee"]),
                int(r["piece"]),
                float(r["ref"]),
                float(r["max"]),
                float(r["min"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RentControlError("opendata.paris.fr : ligne illisible") from exc
        kind, epoque = r.get("meuble_txt"), r.get("epoque")
        if (
            not 2019 <= year <= 2100
            or not 1 <= rooms <= MAX_ROOMS
            or epoque not in EPOQUES
            or kind not in ("meublé", "non meublé")
            or not 5 <= low <= ref <= high <= 100
        ):
            raise RentControlError("opendata.paris.fr : valeur hors bornes")
        out.append(Reference(year, rooms, str(epoque), kind == "meublé", ref, high, low))
    return out


def reference_year(signed: date) -> int:
    """L'année du jeu en vigueur à la signature du bail : mise à jour chaque 1er juillet (fiche du jeu)."""
    return signed.year if signed >= date(signed.year, 7, 1) else signed.year - 1


def reference_for(refs: Sequence[Reference], year: int, rooms: int, epoque: str, furnished: bool) -> Reference | None:
    rooms = min(rooms, MAX_ROOMS)
    return next((r for r in refs if (r.year, r.rooms, r.epoque, r.furnished) == (year, rooms, epoque, furnished)), None)


@dataclass(frozen=True)
class Above:
    rent_m2: float
    monthly_cents: int  # au-dessus du loyer de référence majoré, par mois
    yearly_cents: int


def above_cap(base_rent_cents: int, surface_m2: float, ref: Reference) -> Above | None:
    """Le loyer de base (hors charges et hors complément) face au plafond : None s'il est en dessous ou à l'euro
    près."""
    cap_cents = round(ref.high * surface_m2 * 100)
    over = base_rent_cents - cap_cents
    if over <= 100:
        return None
    return Above(base_rent_cents / 100 / surface_m2, over, over * 12)


@dataclass(frozen=True)
class ControlLink:
    """L'encadrement tel que Bank l'a gardé pour ta commune : rien n'est relu ici."""

    territory: Territory
    partial: bool  # une partie seulement de la commune est encadrée
    page: PageCheck | None  # la fiche F1314 relue (None : pas encore)
    located: bool  # adresse située (coordonnées connues)
    quartier: str | None  # à Paris : le quartier de ton adresse
    years: tuple[int, ...]  # années publiées du jeu parisien
    refs: tuple[Reference, ...]
    epoque: str | None  # ce que tu as indiqué, jamais deviné
    rooms: int | None
    rooms_source: str | None


# ------------------------------------------------------------------ Géorisques
# Le 24 octobre 2026, depuis ce serveur, toute adresse en /api/ de georisques.gouv.fr coupait la connexion (HTTP/2
# INTERNAL_ERROR, puis « connection reset » en HTTP/1.1), alors que le site répondait. Bank sonde la description
# publique de l'API (aucun paramètre) et renvoie vers la page officielle.
GEORISQUES_PROBE = "https://www.georisques.gouv.fr/api/v3/api-docs/georisques-api-v1"
GEORISQUES_PAGE = "https://www.georisques.gouv.fr/"


def rooms_label(rooms: int) -> str:
    return "4 pièces et plus" if rooms >= MAX_ROOMS else f"{rooms} pièce{'s' if rooms > 1 else ''}"
