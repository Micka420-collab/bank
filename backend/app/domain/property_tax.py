"""Taxe foncière de l'an prochain (#34) : le dernier montant connu (avis rangé dans Papiers, sinon tes prélèvements),
les taux votés par ta commune (data.economie.gouv.fr, « Fiscalité locale des particuliers », issus du fichier REI de
la DGFiP) et ce que leur évolution change.

Module pur : aucune base, aucun réseau. Relevé en direct le 24 octobre 2026 : dernier exercice publié 2025 (un an de
retard), taux global TFPB (commune, intercommunalité, syndicats, taxes spéciales, GEMAPI) et taux plein de TEOM.
La revalorisation annuelle des valeurs locatives n'a pas pu être relue sur une source officielle depuis ce serveur
(Légifrance refuse la connexion) : Bank ne la chiffre pas, elle dit seulement qu'elle s'ajoute (« Cette valeur
locative est revalorisée chaque année », Service-Public, fiche F59).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlencode

from app.domain.recurring import add_months

RATES_API = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/fiscalite-locale-des-particuliers"
RATES_PAGE = "https://data.economie.gouv.fr/explore/dataset/fiscalite-locale-des-particuliers/"
RATES_SOURCE = "DGFiP, « Fiscalité locale des particuliers » (data.economie.gouv.fr, fichier REI)"
BASES_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/F59"
BASES_SOURCE = "Service-Public, fiche F59 « Taxe foncière » (vérifiée le 25 septembre 2026)"
MAX_RATES = 2_000_000  # un département : quelques centaines de communes × 5 exercices, moins de 200 Ko
_FIELDS = "exercice,insee_com,libcom,taux_global_tfb,taux_plein_teom,e12vote,e32vote"
LABEL = re.compile(r"TAXES?\s+FONCIERES?|\bTFONC\b|\bTAXE FONC\b")  # libellé d'un prélèvement replié
MONTHLY_MIN = 6  # au moins six prélèvements dans l'année : mensualisée


class PropertyTaxError(ValueError):
    """Réponse illisible ou inattendue : message lisible, la relève précédente reste."""


def tax_commune(code: str) -> str:
    """Le code de la commune pour les impôts locaux : un arrondissement de Paris, Lyon ou Marseille compte pour la
    ville (une seule ligne « VILLE DE PARIS » dans le jeu)."""
    if re.fullmatch(r"751\d\d", code):
        return "75056"
    if re.fullmatch(r"6938\d", code):
        return "69123"
    if re.fullmatch(r"132\d\d", code):
        return "13055"
    return code


def department(code: str) -> str:
    """« 31 », « 2A », « 974 » : seul ce code part vers data.economie.gouv.fr, jamais la commune."""
    if not re.fullmatch(r"(?:\d{5}|2[AB]\d{3})", code):
        raise PropertyTaxError("Code de commune inattendu")
    return code[:3] if code.startswith("97") else code[:2]


def rates_url(dep: str) -> str:
    if not re.fullmatch(r"\d{2,3}|2[AB]", dep):
        raise PropertyTaxError("Code de département inattendu")
    return f"{RATES_API}/exports/json?{urlencode({'where': f'dep="{dep}"', 'select': _FIELDS})}"


@dataclass(frozen=True)
class Rates:
    year: int
    commune: str  # libellé du jeu (« TOULOUSE »)
    tfb: float  # taux global de taxe foncière sur les propriétés bâties, %
    teom: float  # taux plein de TEOM, % (0 : pas de TEOM, ou une redevance)
    tfb_commune: float | None  # part votée par la commune
    tfb_epci: float | None  # part votée par l'intercommunalité

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rates:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def _rate(raw: Any, nullable: bool = False) -> float | None:
    if raw is None and nullable:
        return None
    if not isinstance(raw, (int, float)) or not 0 <= raw <= 100:
        raise PropertyTaxError("Fiscalité locale : taux hors bornes")
    return float(raw)


def parse_rates(body: bytes, code: str) -> dict[int, Rates]:
    """Les taux de ta commune, par exercice. Une ligne d'un autre département ou un taux hors bornes refuse tout."""
    try:
        rows = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise PropertyTaxError("Fiscalité locale : réponse illisible (JSON attendu)") from exc
    if not isinstance(rows, list):
        raise PropertyTaxError("Fiscalité locale : réponse inattendue")
    dep = department(code)
    out: dict[int, Rates] = {}
    for r in rows:
        if not isinstance(r, dict):
            raise PropertyTaxError("Fiscalité locale : ligne illisible")
        insee = str(r.get("insee_com") or "")
        if not re.fullmatch(r"(?:\d{5}|2[AB]\d{3})", insee) or department(insee) != dep:
            raise PropertyTaxError("Fiscalité locale : ligne d'un autre département dans la réponse")
        if insee != code:
            continue
        try:
            year = int(str(r["exercice"]))
        except (KeyError, ValueError) as exc:
            raise PropertyTaxError("Fiscalité locale : exercice illisible") from exc
        tfb, teom = _rate(r.get("taux_global_tfb")), _rate(r.get("taux_plein_teom") or 0.0)
        if tfb is None or teom is None or tfb == 0:
            raise PropertyTaxError("Fiscalité locale : taux de taxe foncière absent")
        if not 2000 <= year <= 2100:
            raise PropertyTaxError("Fiscalité locale : exercice hors bornes")
        out[year] = Rates(
            year,
            " ".join(str(r.get("libcom") or "").split())[:80],
            tfb,
            teom,
            _rate(r.get("e12vote"), nullable=True),
            _rate(r.get("e32vote"), nullable=True),
        )
    return out


# ------------------------------------------------------------------ ce que tu as payé
@dataclass(frozen=True)
class Known:
    year: int
    amount_cents: int
    source: str  # avis | prelevements
    teom_cents: int | None = None  # part TEOM lue sur l'avis
    due: date | None = None  # date limite de l'avis, ou date du prélèvement
    monthly: bool = False  # mensualisée : déjà dans tes charges du mois
    ref: str | None = None  # doc:<id> | txns
    payments: int = 0


def from_payments(payments: Sequence[tuple[date, int]], today: date) -> Known | None:
    """Le dernier exercice complet vu dans tes prélèvements « taxe foncière » (une année civile entière, close).
    Une année en cours n'est comptée que si son paiement unique est passé (pas de mensualisation en cours)."""
    by_year: dict[int, list[tuple[date, int]]] = {}
    for d, cents in payments:
        by_year.setdefault(d.year, []).append((d, cents))
    for year in sorted(by_year, reverse=True):
        rows = sorted(by_year[year])
        monthly = len(rows) >= MONTHLY_MIN
        if year >= today.year and monthly:
            continue  # mensualisée, année pas finie : le total n'est pas connu
        return Known(year, sum(c for _, c in rows), "prelevements", None, rows[-1][0], monthly, "txns", len(rows))
    return None


def latest(known: Sequence[Known]) -> Known | None:
    """Le plus récent ; à exercice égal, l'avis (il donne la part TEOM) plutôt que les prélèvements."""
    return max(known, key=lambda k: (k.year, k.source == "avis"), default=None)


# ------------------------------------------------------------------ la prévision
@dataclass(frozen=True)
class Step:
    year: int
    tfb_from: float | None
    tfb_to: float | None
    teom_from: float | None
    teom_to: float | None

    @property
    def known(self) -> bool:
        return None not in (self.tfb_from, self.tfb_to, self.teom_from, self.teom_to)


@dataclass(frozen=True)
class Forecast:
    target: int  # exercice prévu
    base: Known
    amount_cents: int | None  # à bases inchangées ; None si l'effet des taux ne se chiffre pas
    steps: tuple[Step, ...]
    unknown_rates: tuple[int, ...]  # exercices dont les taux ne sont pas encore publiés
    due: date | None  # échéance prévue (même jour que la dernière)
    latest_rates: int | None  # dernier exercice publié
    commune: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)


def target_year(base: Known, today: date) -> int:
    return base.year + 1 if base.year >= today.year else today.year


def forecast(base: Known, rates: Mapping[int, Rates], today: date) -> Forecast:
    """Le montant de l'exercice qui vient, à bases inchangées : chaque changement de taux publié s'applique à sa
    part (taxe foncière d'un côté, TEOM de l'autre) ; un exercice sans taux publiés compte à taux inchangés et le
    dit. Sans la part TEOM de l'avis, l'effet ne se chiffre pas quand les deux taux ne bougent pas pareil."""
    target = target_year(base, today)
    steps: list[Step] = []
    unknown: list[int] = []
    notes: list[str] = []
    tfb_factor, teom_factor = 1.0, 1.0
    for y in range(base.year + 1, target + 1):
        a, b = rates.get(y - 1), rates.get(y)
        steps.append(Step(y, a.tfb if a else None, b.tfb if b else None, a.teom if a else None, b.teom if b else None))
        if a is None or b is None:
            unknown.append(y)
            continue
        tfb_factor *= b.tfb / a.tfb if a.tfb else 1.0
        teom_factor *= b.teom / a.teom if a.teom else 1.0
    amount: int | None
    if base.teom_cents is not None and 0 <= base.teom_cents <= base.amount_cents:
        amount = round((base.amount_cents - base.teom_cents) * tfb_factor + base.teom_cents * teom_factor)
    elif abs(tfb_factor - teom_factor) < 1e-9:
        amount = round(base.amount_cents * tfb_factor)
    else:
        amount = None
        notes.append(
            "La part de TEOM (ordures ménagères) de ton avis n'est pas connue et son taux n'a pas bougé comme celui "
            "de la taxe foncière : complète-la dans Papiers pour chiffrer l'effet des taux."
        )
    due = None
    if base.due is not None:
        due = add_months(base.due, 12 * (target - base.year))
    commune = next((r.commune for _, r in sorted(rates.items(), reverse=True)), None)
    return Forecast(
        target, base, amount, tuple(steps), tuple(unknown), due, max(rates) if rates else None, commune, tuple(notes)
    )


def fr_rate(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")
