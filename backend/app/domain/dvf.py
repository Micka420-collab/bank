"""Valeur de ton logement par les ventes voisines : fichiers DVF géolocalisés (Etalab, DGFiP), lus ligne à ligne.

Une mutation DVF peut couvrir plusieurs biens (un appartement et un local commercial, deux maisons…) : la
valeur foncière est celle de toute la mutation. On regroupe donc les lignes par ``id_mutation`` et on ne garde que
les ventes d'un seul logement (dépendances admises : une cave ou un garage vont souvent avec). Le reste se calcule
ici, sur le prix au m² des ventes comparables : médiane, quartiles, valeurs aberrantes écartées (barrières de
Tukey). Une fourchette, jamais un chiffre unique ; pas assez de ventes, pas d'estimation.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.domain.recurring import add_months

KINDS = ("appartement", "maison")
KIND_LABELS = {"appartement": "Appartement", "maison": "Maison"}
OF_KIND = {"appartement": "d'appartement", "maison": "de maison"}
DVF_TYPES = {"Appartement": "appartement", "Maison": "maison"}
OTHER_LOCALS = {"Local industriel. commercial ou assimilé"}  # un local d'activité dans la vente : prix mélangé
WINDOW_MONTHS = 24
MIN_COMPARABLES = 5
TOLERANCES = (0.30, 0.45, 0.60)  # ±30 % de surface, élargi par paliers s'il manque des ventes
MIN_SURFACE, MAX_SURFACE = 9, 1_000
MIN_ROOMS, MAX_ROOMS = 1, 20
# Garde-fous avant toute statistique : une « vente » à 1 € ou à 80 000 €/m² est une erreur de saisie ou un
# transfert particulier, pas un prix de marché.
MIN_PRICE_EUR, MAX_PRICE_EUR = 5_000, 50_000_000
MIN_PRICE_M2, MAX_PRICE_M2 = 200, 40_000
REQUIRED = ("id_mutation", "date_mutation", "nature_mutation", "valeur_fonciere", "type_local", "surface_reelle_bati")
SOURCE = "DVF, Etalab / DGFiP, Licence Ouverte 2.0"
SOURCE_URL = "https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/"
CODE_RE = re.compile(r"^(?:\d{5}|2[AB]\d{3})$")
SECTION_RE = re.compile(r"^[0-9A-Z]{1,2}$")
# Paris, Lyon et Marseille n'ont pas de fichier communal : DVF les découpe par arrondissement.
CITIES_BY_DISTRICT = {
    "75056": ("Paris", "75101", "75120"),
    "69123": ("Lyon", "69381", "69389"),
    "13055": ("Marseille", "13201", "13216"),
}
# Le livre foncier d'Alsace-Moselle et Mayotte ne sont pas dans DVF.
NOT_COVERED = {"57": "la Moselle", "67": "le Bas-Rhin", "68": "le Haut-Rhin", "976": "Mayotte"}


class DvfError(ValueError):
    """Message lisible : ce qui ne va pas et quoi faire."""


@dataclass(frozen=True)
class Sale:
    """Une vente réduite à ce qui sert : ni adresse, ni parcelle (la section seule, pour le filtre local)."""

    day: date
    price_eur: int
    kind: str  # appartement | maison
    surface: int
    rooms: int | None
    section: str | None

    @property
    def per_m2(self) -> float:
        return self.price_eur / self.surface

    def to_row(self) -> list[Any]:
        return [self.day.isoformat(), self.price_eur, self.kind, self.surface, self.rooms, self.section]

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> Sale:
        day, price, kind, surface, rooms, section = row
        return cls(date.fromisoformat(str(day)), int(price), str(kind), int(surface), rooms, section)


# ------------------------------------------------------------------ commune et section
def normalize_code(code: str) -> str:
    clean = code.strip().upper()
    if not CODE_RE.match(clean):
        raise DvfError("Code INSEE : 5 caractères, ex. 45234 pour Orléans (différent du code postal)")
    if clean in CITIES_BY_DISTRICT:
        name, first, last = CITIES_BY_DISTRICT[clean]
        raise DvfError(f"{name} se découpe par arrondissement dans DVF : choisis le tien ({first} à {last})")
    dep = department(clean)
    if dep in NOT_COVERED:
        raise DvfError(f"DVF ne couvre pas {NOT_COVERED[dep]} (livre foncier) : pas d'estimation possible ici")
    return clean


def department(code: str) -> str:
    return code[:3] if code.startswith("97") else code[:2]


def normalize_section(section: str | None) -> str | None:
    """« ab » → « AB », « A » → « 0A » (les sections d'une lettre sont complétées d'un zéro dans le cadastre)."""
    if section is None or not section.strip():
        return None
    clean = section.strip().upper()
    if not SECTION_RE.match(clean):
        raise DvfError("Section cadastrale : une ou deux lettres ou chiffres, ex. AB")
    return clean.rjust(2, "0")


# ------------------------------------------------------------------ lecture du fichier
def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _rows(data: bytes) -> Iterator[dict[str, str]]:
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", errors="replace", newline="")
    header = text.readline()
    delimiter = "|" if header.count("|") > header.count(",") else ","
    columns = [c.strip() for c in next(csv.reader([header], delimiter=delimiter), [])]
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise DvfError(f"Fichier DVF illisible : colonnes absentes ({', '.join(missing)})")
    yield from csv.DictReader(text, fieldnames=columns, delimiter=delimiter)


def parse_commune(data: bytes, *, since: date | None = None) -> list[Sale]:
    """Les ventes d'un seul logement d'un fichier communal, une par mutation. Lecture en flux : les lignes d'une
    mutation se suivent dans le fichier, mais on regroupe par identifiant sans le supposer."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in _rows(data):
        mid = (row.get("id_mutation") or "").strip()
        if not mid or (row.get("nature_mutation") or "").strip() != "Vente":
            continue
        groups.setdefault(mid, []).append(row)
    sales = [s for rows in groups.values() if (s := _single_home(rows)) is not None]
    if since is not None:
        sales = [s for s in sales if s.day >= since]
    return sorted(sales, key=lambda s: s.day)


def _single_home(rows: list[dict[str, str]]) -> Sale | None:
    homes: set[tuple[str, str, str, str, str]] = set()
    for r in rows:
        kind = (r.get("type_local") or "").strip()
        if kind in OTHER_LOCALS:
            return None
        if kind in DVF_TYPES:
            # Une même maison revient sur plusieurs lignes (une par nature de terrain) : on la compte une fois.
            homes.add(
                (
                    (r.get("id_parcelle") or "").strip(),
                    kind,
                    (r.get("surface_reelle_bati") or "").strip(),
                    (r.get("nombre_pieces_principales") or "").strip(),
                    (r.get("lot1_numero") or "").strip(),
                )
            )
    if len(homes) != 1:
        return None  # aucun logement, ou plusieurs : la valeur foncière ne dit pas le prix d'un seul
    parcel, kind, surface_raw, rooms_raw, _lot = next(iter(homes))
    first = rows[0]
    price = _number(first.get("valeur_fonciere"))
    surface = _number(surface_raw)
    try:
        day = date.fromisoformat((first.get("date_mutation") or "").strip()[:10])
    except ValueError:
        return None
    if price is None or surface is None or not MIN_SURFACE <= surface <= MAX_SURFACE:
        return None
    if not MIN_PRICE_EUR <= price <= MAX_PRICE_EUR or not MIN_PRICE_M2 <= price / surface <= MAX_PRICE_M2:
        return None
    rooms = _number(rooms_raw)
    # id_parcelle : commune (5) + préfixe de section (3) + section (2) + numéro (4), ex. 75115000AB0012.
    section = parcel[8:10].upper() if len(parcel) == 14 else None
    return Sale(day, round(price), DVF_TYPES[kind], round(surface), int(rooms) if rooms else None, section)


# ------------------------------------------------------------------ statistiques
def quantile(values: Sequence[float], q: float) -> float:
    """Interpolation linéaire entre les deux valeurs encadrantes (la méthode par défaut des tableurs)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("série vide")
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def drop_outliers(sales: Sequence[Sale]) -> tuple[list[Sale], int]:
    """Barrières de Tukey sur le prix au m² (Q1 − 1,5 × écart interquartile, Q3 + 1,5 × écart) : une vente
    familiale à prix d'ami ou un bien d'exception ne tire pas la médiane."""
    if len(sales) < 4:
        return list(sales), 0
    per_m2 = [s.per_m2 for s in sales]
    q1, q3 = quantile(per_m2, 0.25), quantile(per_m2, 0.75)
    spread = q3 - q1
    low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
    kept = [s for s in sales if low <= s.per_m2 <= high]
    return kept, len(sales) - len(kept)


@dataclass
class Estimate:
    ok: bool
    kind: str
    surface: int
    count: int
    window_start: str | None
    window_end: str | None
    message: str
    median_cents: int = 0
    low_cents: int = 0
    high_cents: int = 0
    median_m2_cents: int = 0
    low_m2_cents: int = 0
    high_m2_cents: int = 0
    first_sale: str | None = None
    last_sale: str | None = None
    tolerance_percent: int = 30
    scope: str = "commune"  # section | commune
    rooms_used: bool = False
    outliers: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick(
    sales: Iterable[Sale], *, kind: str, surface: int, tol: float, rooms: int | None, section: str | None
) -> list[Sale]:
    lo, hi = surface * (1 - tol), surface * (1 + tol)
    return [
        s
        for s in sales
        if s.kind == kind
        and lo <= s.surface <= hi
        and (rooms is None or (s.rooms is not None and abs(s.rooms - rooms) <= 1))
        and (section is None or s.section == section)
    ]


def estimate(
    sales: Sequence[Sale],
    *,
    kind: str,
    surface: int,
    rooms: int | None = None,
    section: str | None = None,
    months: int = WINDOW_MONTHS,
) -> Estimate:
    """Surface × prix médian au m² des ventes comparables des ``months`` derniers mois de la base (DVF paraît avec
    plusieurs mois de retard : la fenêtre finit à la vente la plus récente, pas à aujourd'hui). Fourchette = 1er
    et 3e quartiles. Section, puis pièces, puis surface : chaque critère se relâche par paliers, et la note le dit."""
    if kind not in KINDS:
        raise DvfError("Type de logement : appartement ou maison")
    if not MIN_SURFACE <= surface <= MAX_SURFACE:
        raise DvfError(f"Surface habitable : entre {MIN_SURFACE} et {MAX_SURFACE} m²")
    if rooms is not None and not MIN_ROOMS <= rooms <= MAX_ROOMS:
        raise DvfError(f"Nombre de pièces : entre {MIN_ROOMS} et {MAX_ROOMS}")
    same_kind = [s for s in sales if s.kind == kind]
    label = OF_KIND[kind]
    if not same_kind:
        return Estimate(
            False,
            kind,
            surface,
            0,
            None,
            None,
            f"Pas assez de ventes comparables : aucune vente {label} (logement seul) dans les données de ta commune.",
        )
    end = max(s.day for s in sales)  # la vente la plus récente de la base, tous types confondus
    start = add_months(end, -months)
    recent = [s for s in same_kind if s.day > start]
    notes: list[str] = []
    scopes: list[str | None] = [section, None] if section else [None]
    best: list[Sale] = []
    for scope in scopes:
        for use_rooms in (True, False) if rooms is not None else (False,):
            for tol in TOLERANCES:
                picked = _pick(
                    recent, kind=kind, surface=surface, tol=tol, rooms=rooms if use_rooms else None, section=scope
                )
                kept, outliers = drop_outliers(picked)
                if len(kept) > len(best):
                    best = kept
                if len(kept) < MIN_COMPARABLES:
                    continue
                if section and scope is None:
                    notes.append(f"Trop peu de ventes dans la section {section} : comparaison sur toute la commune.")
                if rooms is not None and not use_rooms:
                    notes.append("Trop peu de ventes au même nombre de pièces (± 1) : critère abandonné.")
                if tol > TOLERANCES[0]:
                    notes.append(f"Surface élargie à ± {round(tol * 100)} % faute de ventes à ± 30 %.")
                return _result(
                    kept,
                    kind=kind,
                    surface=surface,
                    start=start,
                    end=end,
                    tol=tol,
                    scope="section" if scope else "commune",
                    rooms_used=use_rooms,
                    outliers=outliers,
                    notes=notes,
                )
    return Estimate(
        False,
        kind,
        surface,
        len(best),
        start.isoformat(),
        end.isoformat(),
        f"Pas assez de ventes comparables : {len(best)} vente{'s' if len(best) > 1 else ''} {label} de taille "
        f"proche en {months} mois (il en faut {MIN_COMPARABLES}). Bank préfère ne rien inventer.",
    )


def _result(
    kept: list[Sale],
    *,
    kind: str,
    surface: int,
    start: date,
    end: date,
    tol: float,
    scope: str,
    rooms_used: bool,
    outliers: int,
    notes: list[str],
) -> Estimate:
    per_m2 = [s.per_m2 for s in kept]
    med, q1, q3 = quantile(per_m2, 0.5), quantile(per_m2, 0.25), quantile(per_m2, 0.75)

    # Arrondi à la centaine d'euros : une estimation au centime laisserait croire à une précision qu'elle n'a pas.
    def hundred(eur: float) -> int:
        return round(eur / 100) * 100 * 100

    if outliers:
        notes.append(
            f"{outliers} vente{'s' if outliers > 1 else ''} hors norme écartée{'s' if outliers > 1 else ''}"
            " (prix au m² très éloigné des autres)."
        )
    return Estimate(
        ok=True,
        kind=kind,
        surface=surface,
        count=len(kept),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        message=f"Estimation sur {len(kept)} ventes comparables.",
        median_cents=hundred(surface * med),
        low_cents=hundred(surface * q1),
        high_cents=hundred(surface * q3),
        median_m2_cents=round(med * 100),
        low_m2_cents=round(q1 * 100),
        high_m2_cents=round(q3 * 100),
        first_sale=min(s.day for s in kept).isoformat(),
        last_sale=max(s.day for s in kept).isoformat(),
        tolerance_percent=round(tol * 100),
        scope=scope,
        rooms_used=rooms_used,
        outliers=outliers,
        notes=notes,
    )
