"""Retraite : où tu en es (trimestres acquis, âge légal et durée requise de ta génération), ce que donnerait un départ à
tel âge (pension de base en fourchette : salaire annuel moyen × taux × proratisation ; complémentaire Agirc-Arrco en
points), et ce qu'un versement sur un PER change à l'impôt (plafond, tranche). Pur : aucune lecture de la base.

Paramètres datés (vérifiés le 2026-10-24 ; Bank dit quand les revérifier). Le chiffre opposable est celui du relevé
de carrière et du simulateur officiel (info-retraite.fr) : Bank donne un ordre de grandeur et renvoie là."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.recurring import add_months

RULES_CHECKED = "2026-10-24"
RECHECK_AFTER_DAYS = 180
FULL_RATE_AGE_YEARS = 67  # taux plein automatique (CSS, art. L351-8)
FULL_RATE_PERCENT = Decimal("50")
DECOTE_POINTS_PER_QUARTER = Decimal("0.625")  # 1,25 % du taux de 50 % par trimestre manquant, 20 trimestres au plus
MAX_DECOTE_QUARTERS = 20
SURCOTE_PER_QUARTER = Decimal("0.0125")  # +1,25 % par trimestre au-delà de l'âge légal ET de la durée requise
MAX_BASE_YEARS = 25  # salaire annuel moyen : les 25 meilleures années (plafonnées au PASS)
PASS_2026_CENTS = 4_806_000  # plafond annuel de la sécurité sociale 2026
PASS_2025_CENTS = 4_710_000
QUARTER_SALARY_2026_CENTS = 180_300  # 150 h de SMIC (12,02 € au 1er janvier 2026) valident un trimestre
AGIRC_ARRCO_POINT_VALUE_CENTS = Decimal("143.86")  # valeur de service du point depuis le 1er novembre 2024 (gelée)
AGIRC_ARRCO_POINT_NOV_2026_CENTS = Decimal("145.87")  # décision du 14 octobre 2026 : +1,4 % au 1er novembre 2026
AGIRC_ARRCO_POINT_PRICE_CENTS = Decimal("2018.77")  # salaire de référence (prix d'achat du point) 2025-2026
AGIRC_ARRCO_T1_RATE = Decimal("0.0620")  # taux contractuel d'acquisition de points, tranche 1 (jusqu'au PASS)
AGIRC_ARRCO_T2_RATE = Decimal("0.17")  # tranche 2 (de 1 à 8 PASS)
NET_TO_GROSS = Decimal("1.28")  # salarié du privé : le net versé vaut environ 78 % du brut
PER_CEILING_2026_CENTS = 3_768_000  # versements 2026 : 10 % des revenus 2025, au plus 10 % de 8 PASS 2025
PER_FLOOR_2026_CENTS = 471_000  # au moins 10 % du PASS 2025
PER_INCOME_RATE = Decimal("0.10")

RULES: dict[str, dict[str, Any]] = {
    "age": {
        "title": "Âge légal et durée requise par génération (CSS art. L161-17-2 et L161-17-3 ; loi n° 2025-1403 du "
        "30 décembre 2025, art. 105 : calendrier de 2023 suspendu — 62 ans 9 mois et 170 trimestres jusqu'aux "
        "générations nées avant avril 1965)",
        "url": "https://www.service-public.fr/particuliers/vosdroits/F14043",
        "checked": RULES_CHECKED,
    },
    "formula": {
        "title": "Pension de base = salaire annuel moyen (25 meilleures années, plafonnées) × taux (50 % au taux "
        "plein, −1,25 % par trimestre manquant dans la limite de 20, +1,25 % par trimestre au-delà) × trimestres "
        "validés / durée requise ; taux plein à 67 ans (CSS art. L351-1, L351-8, R351-27)",
        "url": "https://www.service-public.fr/particuliers/vosdroits/F21552",
        "checked": RULES_CHECKED,
    },
    "quarters": {
        "title": "Un trimestre validé pour 150 heures de SMIC de salaire brut (1 803 € en 2026), quatre par an au plus",
        "url": "https://www.lassuranceretraite.fr/portail-info/home/salaries/mes-droits-retraite/trimestres.html",
        "checked": RULES_CHECKED,
    },
    "agirc_arrco": {
        "title": "Agirc-Arrco : points acquis = cotisations contractuelles (6,20 % tranche 1, 17 % tranche 2) ÷ "
        "prix d'achat du point (20,1877 €) ; pension = points × valeur du point (1,4386 € depuis le 1er novembre "
        "2024, 1,4587 € au 1er novembre 2026)",
        "url": "https://www.agirc-arrco.fr/",
        "checked": RULES_CHECKED,
    },
    "per": {
        "title": "PER : versements déductibles du revenu imposable dans la limite de 10 % des revenus professionnels "
        "de l'année précédente, plafonnés à 10 % de 8 PASS (37 680 € pour 2026) et au moins 10 % du PASS "
        "(4 710 €) ; l'économie vaut le versement × ta tranche marginale (CGI art. 163 quatervicies)",
        "url": "https://www.service-public.fr/particuliers/vosdroits/F34982",
        "checked": RULES_CHECKED,
    },
    "simulator": {
        "title": "Le chiffre opposable : ton relevé de carrière et le simulateur officiel (info-retraite.fr)",
        "url": "https://www.info-retraite.fr/",
        "checked": RULES_CHECKED,
    },
}


@dataclass(frozen=True)
class Generation:
    born_from: date
    born_to: date | None
    legal_age: tuple[int, int]  # (années, mois)
    quarters_required: int


GENERATIONS: tuple[Generation, ...] = (
    Generation(date(1900, 1, 1), date(1960, 12, 31), (62, 0), 167),
    Generation(date(1961, 1, 1), date(1961, 8, 31), (62, 0), 168),
    Generation(date(1961, 9, 1), date(1961, 12, 31), (62, 3), 169),
    Generation(date(1962, 1, 1), date(1962, 12, 31), (62, 6), 169),
    Generation(date(1963, 1, 1), date(1965, 3, 31), (62, 9), 170),
    Generation(date(1965, 4, 1), date(1965, 12, 31), (63, 0), 171),
    Generation(date(1966, 1, 1), date(1966, 12, 31), (63, 3), 172),
    Generation(date(1967, 1, 1), date(1967, 12, 31), (63, 6), 172),
    Generation(date(1968, 1, 1), date(1968, 12, 31), (63, 9), 172),
    Generation(date(1969, 1, 1), None, (64, 0), 172),
)


class RetirementError(ValueError):
    pass


def generation(birth: date) -> Generation:
    for g in GENERATIONS:
        if g.born_from <= birth and (g.born_to is None or birth <= g.born_to):
            return g
    raise RetirementError("Date de naissance hors des générations connues")


def legal_age_date(birth: date) -> date:
    years, months = generation(birth).legal_age
    return add_months(birth.replace(year=birth.year + years), months)


def full_rate_age_date(birth: date) -> date:
    return birth.replace(year=birth.year + FULL_RATE_AGE_YEARS)


def quarters_between(start: date, end: date) -> int:
    """Trimestres civils entiers entre deux dates (au plus quatre par an)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)
    return max(0, months // 3)


def age_at(birth: date, when: date) -> tuple[int, int]:
    months = (when.year - birth.year) * 12 + (when.month - birth.month) - (1 if when.day < birth.day else 0)
    return months // 12, months % 12


# ------------------------------------------------------------------ carrière
@dataclass(frozen=True)
class Career:
    birth: date
    quarters_acquired: int  # au relevé de carrière, à la date ``as_of``
    as_of: date
    gross_annual_cents: int  # salaire brut annuel actuel (ou estimé depuis le net)
    best_years_average_cents: int | None = None  # salaire annuel moyen des meilleures années, si tu le connais
    agirc_arrco_points: Decimal | None = None  # points déjà acquis (relevé Agirc-Arrco)
    career_start_year: int | None = None
    working: bool = True  # continue de cotiser jusqu'au départ

    def __post_init__(self) -> None:
        if not 0 <= self.quarters_acquired <= 200:
            raise RetirementError("Trimestres acquis : entre 0 et 200")
        if not 0 <= self.gross_annual_cents <= 100_000_000:
            raise RetirementError("Salaire brut annuel : entre 0 et 1 000 000 €")
        if self.birth > self.as_of:
            raise RetirementError("Date de naissance dans le futur")


def gross_from_net_monthly(net_monthly_cents: int) -> int:
    return int((Decimal(net_monthly_cents) * 12 * NET_TO_GROSS).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def points_per_year(gross_annual_cents: int, pass_cents: int = PASS_2026_CENTS) -> Decimal:
    """Points Agirc-Arrco acquis sur une année de salaire brut (tranche 1 jusqu'au PASS, tranche 2 jusqu'à 8 PASS)."""
    gross = Decimal(max(0, gross_annual_cents))
    t1 = min(gross, Decimal(pass_cents))
    t2 = max(Decimal(0), min(gross, Decimal(pass_cents) * 8) - Decimal(pass_cents))
    cents = t1 * AGIRC_ARRCO_T1_RATE + t2 * AGIRC_ARRCO_T2_RATE
    return (cents / AGIRC_ARRCO_POINT_PRICE_CENTS).quantize(Decimal("0.01"))


# ------------------------------------------------------------------ estimation d'un départ
@dataclass(frozen=True)
class Departure:
    at: date
    age: tuple[int, int]
    quarters_total: int
    quarters_required: int
    missing_quarters: int
    extra_quarters: int
    rate_percent: Decimal
    status: str  # before_legal | decote | full | surcote
    base_monthly_low_cents: int
    base_monthly_high_cents: int
    complementary_monthly_low_cents: int
    complementary_monthly_high_cents: int
    points: Decimal
    replacement_percent: int | None  # pension / dernier net mensuel estimé
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_low_cents(self) -> int:
        return self.base_monthly_low_cents + self.complementary_monthly_low_cents

    @property
    def total_high_cents(self) -> int:
        return self.base_monthly_high_cents + self.complementary_monthly_high_cents


def _rate(missing: int, extra: int, at_full_rate_age: bool) -> tuple[Decimal, str]:
    if missing > 0 and not at_full_rate_age:
        rate = FULL_RATE_PERCENT - DECOTE_POINTS_PER_QUARTER * min(missing, MAX_DECOTE_QUARTERS)
        return rate, "decote"
    if extra > 0:
        return FULL_RATE_PERCENT * (1 + SURCOTE_PER_QUARTER * extra), "surcote"
    return FULL_RATE_PERCENT, "full"


def estimate(career: Career, at: date) -> Departure:
    """Un départ à la date ``at`` : trimestres (acquis + 4 par an jusqu'au départ si tu travailles), taux (décote,
    plein, surcote), base en fourchette (salaire moyen plafonné : connu, sinon 75 % à 100 % du brut actuel),
    complémentaire en points (acquis + acquis d'ici là)."""
    gen = generation(career.birth)
    legal = legal_age_date(career.birth)
    notes: list[str] = []
    if at < legal:
        months = f" {gen.legal_age[1]} mois" if gen.legal_age[1] else ""
        notes.append(
            f"Avant l'âge légal de ta génération ({gen.legal_age[0]} ans{months}) : départ impossible hors carrière "
            "longue, handicap ou inaptitude."
        )
    future = quarters_between(career.as_of, at) if career.working else 0
    total = min(career.quarters_acquired + future, gen.quarters_required + 40)
    missing = max(0, gen.quarters_required - total)
    at_full_age = at >= full_rate_age_date(career.birth)
    extra = 0
    if total >= gen.quarters_required and at >= legal:
        # surcote : trimestres cotisés au-delà de l'âge légal et de la durée requise
        extra = min(quarters_between(legal, at), total - gen.quarters_required) if career.working else 0
    rate, status = _rate(missing, extra, at_full_age)
    if at < legal:
        status = "before_legal"
    proration = Decimal(min(total, gen.quarters_required)) / Decimal(gen.quarters_required)
    pass_cents = Decimal(PASS_2026_CENTS)
    if career.best_years_average_cents is not None:
        avg_low = avg_high = min(Decimal(career.best_years_average_cents), pass_cents)
        notes.append("Salaire annuel moyen : celui que tu as indiqué (relevé de carrière).")
    else:
        current = min(Decimal(career.gross_annual_cents), pass_cents)
        avg_low, avg_high = current * Decimal("0.75"), current
        notes.append(
            "Salaire annuel moyen inconnu : fourchette de 75 % à 100 % de ton brut actuel plafonné (les 25 "
            "meilleures années pèsent moins que la dernière)."
        )
    base_low = (avg_low * rate / 100 * proration / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    base_high = (avg_high * rate / 100 * proration / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    years_ahead = Decimal(future) / 4
    acquired = career.agirc_arrco_points
    if acquired is None:
        # points inconnus : estimés depuis la carrière (années déjà cotisées × points par an au salaire actuel)
        years_done = Decimal(career.quarters_acquired) / 4
        per_year = points_per_year(career.gross_annual_cents)
        acquired_low, acquired_high = per_year * years_done * Decimal("0.6"), per_year * years_done
        notes.append(
            "Points Agirc-Arrco inconnus : estimés depuis tes années cotisées au salaire actuel (fourchette 60 % à "
            "100 %)."
        )
    else:
        acquired_low = acquired_high = acquired
    future_points = points_per_year(career.gross_annual_cents) * years_ahead
    points_low, points_high = acquired_low + future_points, acquired_high + future_points
    value = AGIRC_ARRCO_POINT_VALUE_CENTS if at < date(2026, 11, 1) else AGIRC_ARRCO_POINT_NOV_2026_CENTS
    comp_low = (points_low * value / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    comp_high = (points_high * value / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if status == "decote":
        anticipation = 1 - Decimal("0.0125") * min(missing, MAX_DECOTE_QUARTERS)
        comp_low = (comp_low * anticipation).quantize(Decimal(1))
        comp_high = (comp_high * anticipation).quantize(Decimal(1))
        notes.append(
            "La complémentaire baisse aussi quand la base est minorée (coefficient d'anticipation Agirc-Arrco)."
        )
    net_monthly = (Decimal(career.gross_annual_cents) / NET_TO_GROSS / 12) if career.gross_annual_cents else Decimal(0)
    replacement = int(((base_high + comp_high) / net_monthly * 100).quantize(Decimal(1))) if net_monthly else None
    return Departure(
        at=at,
        age=age_at(career.birth, at),
        quarters_total=total,
        quarters_required=gen.quarters_required,
        missing_quarters=missing,
        extra_quarters=extra,
        rate_percent=rate.quantize(Decimal("0.001")),
        status=status,
        base_monthly_low_cents=int(base_low),
        base_monthly_high_cents=int(base_high),
        complementary_monthly_low_cents=int(comp_low),
        complementary_monthly_high_cents=int(comp_high),
        points=points_high.quantize(Decimal("0.01")),
        replacement_percent=replacement,
        notes=tuple(notes),
    )


def milestones(career: Career) -> dict[str, date]:
    """Les dates qui comptent : âge légal, taux plein par la durée (au rythme de 4 trimestres par an), 67 ans."""
    gen = generation(career.birth)
    legal = legal_age_date(career.birth)
    missing_now = max(0, gen.quarters_required - career.quarters_acquired)
    by_duration = add_months(career.as_of, missing_now * 3) if career.working else full_rate_age_date(career.birth)
    full_by_duration = max(legal, by_duration)
    age_67 = full_rate_age_date(career.birth)
    return {"legal": legal, "full_rate": min(full_by_duration, age_67), "age_67": age_67}


# ------------------------------------------------------------------ PER
@dataclass(frozen=True)
class PerEffect:
    payment_cents: int
    ceiling_cents: int
    deductible_cents: int
    marginal_rate_percent: int
    tax_saved_cents: int
    effort_cents: int  # ce que le versement coûte vraiment, impôt déduit
    notes: tuple[str, ...]


def per_ceiling(professional_income_cents: int) -> int:
    """Plafond de déduction des versements de l'année : 10 % des revenus professionnels de l'année précédente,
    entre 10 % du PASS et 10 % de 8 PASS (sans les plafonds non utilisés des trois années précédentes)."""
    raw = int((Decimal(max(0, professional_income_cents)) * PER_INCOME_RATE).quantize(Decimal(1)))
    return max(PER_FLOOR_2026_CENTS, min(raw, PER_CEILING_2026_CENTS))


def per_effect(payment_cents: int, *, professional_income_cents: int, marginal_rate_percent: int) -> PerEffect:
    if not 0 < payment_cents <= 100_000_000:
        raise RetirementError("Versement : entre 1 € et 1 000 000 €")
    ceiling = per_ceiling(professional_income_cents)
    deductible = min(payment_cents, ceiling)
    saved = int((Decimal(deductible) * marginal_rate_percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    notes = [
        "L'économie d'impôt est une avance : la sortie en capital ou en rente sera imposée (barème ou prélèvement).",
    ]
    if payment_cents > ceiling:
        ceiling_eur = f"{ceiling / 100:,.0f}".replace(",", " ")
        notes.append(
            f"Au-dessus du plafond de l'année ({ceiling_eur} €) : le surplus n'est pas déductible, sauf plafonds des "
            "trois années précédentes non utilisés (voir ton avis d'impôt)."
        )
    if marginal_rate_percent <= 11:
        notes.append(
            "À 0 % ou 11 % d'impôt, la déduction rapporte peu : un PEA ou un livret peut mieux convenir ; tu peux "
            "aussi choisir de ne pas déduire (sortie moins imposée)."
        )
    return PerEffect(
        payment_cents, ceiling, deductible, marginal_rate_percent, saved, payment_cents - saved, tuple(notes)
    )


# ------------------------------------------------------------------ vues
def departure_to_dict(d: Departure) -> dict[str, Any]:
    return {
        "at": d.at.isoformat(),
        "age_years": d.age[0],
        "age_months": d.age[1],
        "quarters_total": d.quarters_total,
        "quarters_required": d.quarters_required,
        "missing_quarters": d.missing_quarters,
        "extra_quarters": d.extra_quarters,
        "rate_percent": str(d.rate_percent),
        "status": d.status,
        "base_monthly_low_cents": d.base_monthly_low_cents,
        "base_monthly_high_cents": d.base_monthly_high_cents,
        "complementary_monthly_low_cents": d.complementary_monthly_low_cents,
        "complementary_monthly_high_cents": d.complementary_monthly_high_cents,
        "total_low_cents": d.total_low_cents,
        "total_high_cents": d.total_high_cents,
        "points": str(d.points),
        "replacement_percent": d.replacement_percent,
        "notes": list(d.notes),
    }
