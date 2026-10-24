"""Ton logement face aux données publiques (#34) : le DPE de ton immeuble dans la base de l'ADEME, la règle qui gèle
le loyer d'un logement classé F ou G, et la carte des loyers de l'ANIL pour ta commune.

Module pur : aucune base, aucun réseau. Tout ce qui vient d'ailleurs est une donnée bornée et vérifiée ; une réponse
inattendue lève une erreur lisible, jamais une valeur devinée. Adresses et règle relevées en direct le 24 octobre
2026 :
* ADEME, jeu « dpe03existant » (DPE des logements existants depuis juillet 2021) : filtre ``identifiant_ban_eq`` sans
  compte (le filtre ``qs`` n'est pas utilisé) ;
* Service-Public, fiche F1311 (« Vérifié le 08 août 2025 ») : pas de révision ni de majoration du loyer d'un logement
  classé F ou G quand le bail est signé, renouvelé ou tacitement reconduit depuis le 24 août 2022 en métropole,
  depuis le 1er juillet 2024 en Guadeloupe, Guyane, Martinique, à La Réunion et à Mayotte ;
* ANIL, « carte des loyers » 2025 (data.gouv.fr) : loyer d'annonce charges comprises au m², logements vides ; à lire
  avec prudence quand le R² ajusté est sous 0,5 ou quand la commune compte moins de 30 annonces.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise
from typing import Any
from urllib.parse import urlencode

from app.domain.recurring import add_months

# ------------------------------------------------------------------ sources
DPE_URL = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines"
DPE_PAGE = "https://data.ademe.fr/datasets/dpe03existant"
DPE_SOURCE = "ADEME, base des DPE des logements existants (dpe03existant)"
DPE_MAX = 100  # au-delà, les 100 plus récents : une grande résidence en compte rarement plus
DPE_FIELDS = (
    "numero_dpe",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "etiquette_dpe",
    "etiquette_ges",
    "type_batiment",
    "adresse_ban",
    "identifiant_ban",
    "code_insee_ban",
    "code_departement_ban",
    "numero_etage_appartement",
    "complement_adresse_logement",
    "surface_habitable_logement",
    "numero_dpe_remplace",
)
LETTERS = tuple("ABCDEFG")  # un tuple : une chaîne vide n'est pas une étiquette
FROZEN_LETTERS = frozenset({"F", "G"})

RULE_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/F1311"
RULE_SOURCE = (
    "Service-Public, fiche F1311 « Augmentation et révision du loyer en cours de bail » (vérifiée le 8 août 2025)"
)
RULE_CHECKED_ON = "24 octobre 2026"
RULE_START_METROPOLE = date(2022, 8, 24)
RULE_START_DROM = date(2024, 7, 1)
DROM = frozenset({"971", "972", "973", "974", "976"})  # Guadeloupe, Martinique, Guyane, La Réunion, Mayotte
# Reconduction tacite du bail : un an pour un meublé ; trois ans pour un logement vide loué par une personne, six
# ans par une société (loi du 6 juillet 1989, art. 10 et 25-7). Sans savoir qui loue, Bank prend six ans : la date
# à partir de laquelle la règle s'applique sûrement.
RENEWAL_MONTHS_FURNISHED = 12
RENEWAL_MONTHS_EMPTY_SURE = 72
PRESCRIPTION_MONTHS = 36  # F1311 : le juge se saisit dans les 3 ans qui suivent la révision contestée
ANNIVERSARY_DAYS = 45  # une hausse à l'anniversaire du bail, à 45 jours près (prélèvement en avance ou en retard)
CENTS_TOLERANCE = 100

DATAGOUV_DATASET = (
    "https://www.data.gouv.fr/api/1/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-{year}/"
)
MARKET_PAGE = "https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-{year}"
MARKET_STATIC = "https://static.data.gouv.fr/resources/"
MARKET_CREDIT = "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin"
# Fichier → (préfixe du nom publié, libellé, surface de référence du bien type en m², selon la fiche du jeu 2025).
MARKET_FILES: dict[str, tuple[str, str, int]] = {
    "app": ("pred-app-", "appartement (toutes tailles)", 52),
    "app12": ("pred-app12-", "appartement d'une ou deux pièces", 37),
    "app3": ("pred-app3-", "appartement de trois pièces ou plus", 72),
    "mai": ("pred-mai-", "maison", 92),
}
MARKET_COLUMNS = ("INSEE_C", "LIBGEO", "loypredm2", "lwr.IPm2", "upr.IPm2", "TYPPRED", "nbobs_com", "R2_adj")
MIN_R2 = 0.5
MIN_OBS = 30
_CITYCODE = re.compile(r"(?:\d{5}|2[AB]\d{3})")
_DPE_NUMBER = re.compile(r"[0-9A-Z]{13}")


class HousingError(ValueError):
    """Réponse illisible ou inattendue : message lisible, la valeur précédente reste."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _json(body: bytes, who: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HousingError(f"{who} : réponse illisible (JSON attendu)") from exc


# ------------------------------------------------------------------ DPE (ADEME)
@dataclass(frozen=True)
class Dpe:
    number: str  # numéro ADEME à 13 caractères
    letter: str  # étiquette énergie A à G
    ges: str | None  # étiquette climat
    established: date
    valid_until: date | None
    building: str  # appartement | maison | immeuble
    address: str  # adresse BAN de l'immeuble
    where: str  # étage, porte… tel que le diagnostiqueur l'a écrit
    floor: int | None
    surface_m2: float | None
    depcode: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["established"] = self.established.isoformat()
        d["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Dpe:
        until = d.get("valid_until")
        return cls(
            number=str(d["number"]),
            letter=str(d["letter"]),
            ges=d.get("ges"),
            established=date.fromisoformat(str(d["established"])),
            valid_until=date.fromisoformat(str(until)) if until else None,
            building=str(d.get("building") or ""),
            address=str(d.get("address") or ""),
            where=str(d.get("where") or ""),
            floor=d.get("floor"),
            surface_m2=d.get("surface_m2"),
            depcode=d.get("depcode"),
        )


def dpe_url(ban_id: str) -> str:
    """Seul l'identifiant BAN de l'adresse part (numéro, rue, commune) : il désigne l'immeuble, pas le logement."""
    if not re.fullmatch(r"(?:\d{5}|2[AB]\d{3})_[0-9a-z]{4,5}_\d{5}(?:_[0-9a-z]{1,6})?", ban_id, re.I):
        raise HousingError("Identifiant d'adresse inattendu")
    params = {
        "identifiant_ban_eq": ban_id,
        "size": str(DPE_MAX),
        "sort": "-date_etablissement_dpe",
        "select": ",".join(DPE_FIELDS),
    }
    return f"{DPE_URL}?{urlencode(params)}"


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def parse_dpe(body: bytes, ban_id: str) -> tuple[list[Dpe], int]:
    """(DPE de l'adresse, du plus récent au plus ancien ; nombre total annoncé par l'ADEME). Un DPE remplacé par un
    autre de la liste est écarté ; une ligne d'une autre adresse, une étiquette hors A–G ou une date illisible sont
    refusées sans deviner."""
    data = _json(body, "ADEME")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not isinstance(data.get("total"), int):
        raise HousingError("ADEME : réponse inattendue")
    out: list[Dpe] = []
    replaced: set[str] = set()
    for row in results[:DPE_MAX]:
        if not isinstance(row, dict):
            raise HousingError("ADEME : ligne illisible")
        if str(row.get("identifiant_ban") or "") != ban_id:
            raise HousingError("ADEME : DPE d'une autre adresse dans la réponse")
        number, letter = _text(row.get("numero_dpe"), 13), _text(row.get("etiquette_dpe"), 1)
        established = _day(row.get("date_etablissement_dpe"))
        if not _DPE_NUMBER.fullmatch(number) or letter not in LETTERS or established is None:
            raise HousingError("ADEME : DPE illisible (numéro, étiquette ou date)")
        old = _text(row.get("numero_dpe_remplace"), 13)
        if old:
            replaced.add(old)
        ges = _text(row.get("etiquette_ges"), 1)
        surface = row.get("surface_habitable_logement")
        floor = row.get("numero_etage_appartement")
        dep = _text(row.get("code_departement_ban"), 3)
        out.append(
            Dpe(
                number=number,
                letter=letter,
                ges=ges if ges in LETTERS else None,
                established=established,
                valid_until=_day(row.get("date_fin_validite_dpe")),
                building=_text(row.get("type_batiment"), 20) or "inconnu",
                address=_text(row.get("adresse_ban"), 200),
                where=_text(row.get("complement_adresse_logement"), 120),
                floor=floor if isinstance(floor, int) and -5 <= floor <= 100 else None,
                surface_m2=float(surface) if isinstance(surface, (int, float)) and 5 <= surface <= 2_000 else None,
                depcode=dep if re.fullmatch(r"\d{2,3}|2[AB]", dep) else None,
            )
        )
    kept = [d for d in out if d.number not in replaced]
    kept.sort(key=lambda d: (d.established, d.number), reverse=True)
    return kept, int(data["total"])


# ------------------------------------------------------------------ la règle : pas de révision en F ou G
def rule_start(depcode: str | None) -> date:
    return RULE_START_DROM if str(depcode or "") in DROM else RULE_START_METROPOLE


def covered_from(lease_start: date, furnished: bool | None, depcode: str | None) -> tuple[date, bool]:
    """(date à partir de laquelle le bail tombe sûrement sous la règle, exacte ?). Un bail signé après la date de la
    règle y est dès sa signature ; un bail plus ancien, à sa première reconduction après cette date. Sans savoir qui
    loue un logement vide, la reconduction prise est celle de six ans : la date est alors la plus tardive possible."""
    start = rule_start(depcode)
    if lease_start >= start:
        return lease_start, True
    months = RENEWAL_MONTHS_FURNISHED if furnished else RENEWAL_MONTHS_EMPTY_SURE
    k = 1
    while add_months(lease_start, months * k) < start:
        k += 1
    return add_months(lease_start, months * k), bool(furnished)


@dataclass(frozen=True)
class Step:
    day: date
    before: int  # montant payé juste avant la hausse (centimes)
    after: int


def anniversary_steps(history: Sequence[tuple[date, int]], lease_start: date) -> list[Step]:
    """Les hausses du montant payé qui tombent à l'anniversaire du bail : les révisions. Une hausse ailleurs dans
    l'année (régularisation des charges…) n'en est pas une."""
    out: list[Step] = []
    for (_d0, a0), (d1, a1) in pairwise(history):
        if a1 - a0 <= CENTS_TOLERANCE:
            continue
        years = round((d1 - lease_start).days / 365.25)
        if years >= 1 and abs((d1 - add_months(lease_start, 12 * years)).days) <= ANNIVERSARY_DAYS:
            out.append(Step(d1, a0, a1))
    return out


@dataclass(frozen=True)
class Overpaid:
    first: Step  # la première révision qui n'était pas due
    monthly_cents: int  # écart du dernier mois face au loyer d'avant cette révision
    total_cents: int  # payé en plus depuis cette révision
    months: int
    since: date  # premier mois compté


def frozen_steps(steps: Sequence[Step], frozen_from: date) -> list[Step]:
    return [s for s in steps if s.day >= frozen_from]


def overpaid(
    history: Sequence[tuple[date, int]], steps: Sequence[Step], frozen_from: date, today: date
) -> Overpaid | None:
    """Ce qui a été payé au-dessus du loyer d'avant la première révision gelée encore contestable, mois par mois.
    Service-Public (F1311) : le juge se saisit dans les 3 ans qui suivent la révision ; une révision plus ancienne
    n'est pas comptée, ni ce qu'elle a ajouté."""
    recent = [s for s in frozen_steps(steps, frozen_from) if s.day >= add_months(today, -PRESCRIPTION_MONTHS)]
    if not recent:
        return None
    first = recent[0]
    extra = [(d, a - first.before) for d, a in history if d >= first.day and a - first.before > CENTS_TOLERANCE]
    if not extra:
        return None
    return Overpaid(
        first=first,
        monthly_cents=max(0, history[-1][1] - first.before),
        total_cents=sum(x for _, x in extra),
        months=len(extra),
        since=extra[0][0],
    )


# ------------------------------------------------------------------ carte des loyers (ANIL)
def market_dataset_url(year: int) -> str:
    return DATAGOUV_DATASET.format(year=year)


def market_file(kind: str | None, rooms: int | None) -> str | None:
    """Le fichier qui correspond à ton logement : maison, ou appartement par taille quand le nombre de pièces est
    connu. None si le type n'est pas connu (on ne devine pas)."""
    if kind == "maison":
        return "mai"
    if kind != "appartement":
        return None
    if rooms is None:
        return "app"
    return "app12" if rooms <= 2 else "app3"


def pick_resource(body: bytes, file_key: str) -> tuple[str, str | None]:
    """(adresse du CSV, date de mise en ligne) dans la fiche data.gouv.fr du jeu : seulement un fichier publié sur
    static.data.gouv.fr dont le nom commence par le préfixe attendu."""
    data = _json(body, "data.gouv.fr")
    resources = data.get("resources") if isinstance(data, dict) else None
    if not isinstance(resources, list):
        raise HousingError("data.gouv.fr : fiche du jeu inattendue")
    prefix = MARKET_FILES[file_key][0]
    for r in resources:
        url = str(r.get("url") or "") if isinstance(r, dict) else ""
        if url.startswith(MARKET_STATIC) and url.rsplit("/", 1)[-1].startswith(prefix) and url.endswith(".csv"):
            return url, (str(r.get("last_modified") or "")[:10] or None)
    raise HousingError("data.gouv.fr : fichier de la carte des loyers introuvable")


@dataclass(frozen=True)
class Market:
    code: str
    commune: str
    file: str  # app | app12 | app3 | mai
    m2: float  # loyer prédit, €/m² charges comprises
    low: float  # borne basse de l'intervalle de prédiction
    high: float
    level: str  # commune | maille (groupe de communes voisines)
    obs: int  # annonces dans la commune
    r2: float
    year: int  # millésime de la carte

    @property
    def reliable(self) -> bool:
        return self.r2 >= MIN_R2 and self.obs >= MIN_OBS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Market:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def _decimal(raw: str) -> float:
    return float(raw.strip().replace(",", "."))


def parse_market(body: bytes, code: str, file_key: str, year: int) -> Market | None:
    """La ligne de ta commune (code INSEE, arrondissement à Paris, Lyon et Marseille), ou None si elle n'y est pas.
    Le fichier est publié en Windows-1252, séparateur « ; », virgule décimale. Une valeur hors bornes refuse tout."""
    if not _CITYCODE.fullmatch(code):
        raise HousingError("Code de commune inattendu")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("cp1252", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames or any(c not in reader.fieldnames for c in MARKET_COLUMNS):
        raise HousingError("Carte des loyers : colonnes inattendues")
    for row in reader:
        if (row.get("INSEE_C") or "").strip() != code:
            continue
        try:
            m2, low, high = _decimal(row["loypredm2"]), _decimal(row["lwr.IPm2"]), _decimal(row["upr.IPm2"])
            obs, r2 = int(row["nbobs_com"]), _decimal(row["R2_adj"])
        except (TypeError, ValueError) as exc:
            raise HousingError("Carte des loyers : valeur illisible") from exc
        level = _text(row.get("TYPPRED"), 10)
        if not (1 <= low <= m2 <= high <= 150) or not 0 <= r2 <= 1 or obs < 0 or level not in ("commune", "maille"):
            raise HousingError("Carte des loyers : valeur hors bornes")
        return Market(code, _text(row.get("LIBGEO"), 80), file_key, m2, low, high, level, obs, r2, year)
    return None


def position(rent_m2: float, market: Market) -> str:
    """below | within | above : ta place face à l'intervalle des annonces, sans qualifier le loyer."""
    if rent_m2 < market.low:
        return "below"
    if rent_m2 > market.high:
        return "above"
    return "within"


def fr2(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")
