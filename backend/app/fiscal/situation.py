"""Construction d'une situation OpenFisca France à partir du profil du foyer ET de tes vrais relevés.

Point clé (vérifié sur openfisca-france 176) : fournir seulement le salaire net fausse les aides au
logement et l'éligibilité LEP, qui lisent les revenus *fiscaux* de l'année N-2. On renseigne donc
aussi le salaire imposable sur toute la période N-2 -> mois courant, soit depuis l'historique
bancaire, soit depuis le revenu imposable déclaré si tu le fournis (plus fiable).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.recurring import add_months

Housing = Literal[
    "locataire_vide",
    "locataire_meuble",
    "locataire_hlm",
    "locataire_foyer",
    "proprietaire",
    "primo_accedant",
    "loge_gratuitement",
]
Activity = Literal["actif", "chomeur", "etudiant", "retraite", "inactif"]

# Net imposable ≈ net versé + CSG/CRDS non déductibles (~2,9 % de 98,25 % du brut) -> ~ +3,5 %.
NET_TO_TAXABLE = 1.035

FAMILLE_OUTPUTS = (
    "rsa",
    "ppa",
    "aide_logement",
    "af",
    "asf",
    "css_participation_forfaitaire",
    "css_cmu_acs_eligibilite",
)
INDIVIDU_OUTPUTS = ("livret_epargne_populaire_eligibilite",)
MENAGE_YEAR_OUTPUTS = ("cheque_energie",)  # montant annuel, demandé pour l'année en cours


class HouseholdProfileIn(BaseModel):
    birth_date: date
    postal_insee_code: str = Field(pattern=r"^(?:\d{5}|2[AB]\d{3})$", description="Code INSEE de la commune")
    housing: Housing = "locataire_vide"
    rent_cents: int = Field(default=0, ge=0, le=1_000_000)
    activity: Activity = "actif"
    couple: bool = False
    partner_birth_date: date | None = None
    partner_monthly_net_cents: int = Field(default=0, ge=0, le=10_000_000)
    children_birth_dates: list[date] = Field(default_factory=list, max_length=12)
    annual_taxable_income_n2_cents: int | None = Field(default=None, ge=0, le=100_000_000)

    @field_validator("birth_date", "partner_birth_date")
    @classmethod
    def _adult(cls, v: date | None) -> date | None:
        if v is not None and not (date(1900, 1, 1) <= v <= date.today()):  # noqa: DTZ011
            raise ValueError("date de naissance invalide")
        return v


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_since_n2(period: date) -> list[str]:
    """Tous les mois de janvier N-2 au mois précédant la période (inclus)."""
    start = date(period.year - 2, 1, 1)
    out: list[str] = []
    d = start
    while d < period.replace(day=1):
        out.append(_month(d))
        d = add_months(d, 1)
    return out


def _fill(series: Mapping[str, int], months: list[str]) -> dict[str, float]:
    """Complète les mois sans données par la moyenne des mois connus (l'historique bancaire
    couvre rarement 2 ans), en euros."""
    known = list(series.values())
    avg = sum(known) / len(known) if known else 0
    return {m: round(series.get(m, avg) / 100, 2) for m in months}


def build_situation(
    profile: HouseholdProfileIn,
    *,
    period: date,
    salary_net_by_month: Mapping[str, int],
    unemployment_by_month: Mapping[str, int] | None = None,
    pension_by_month: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    p = _month(period)
    history = months_since_n2(period)
    all_months = [*history, p]
    salaries = _fill(salary_net_by_month, all_months)

    me: dict[str, Any] = {
        "date_naissance": {"ETERNITY": profile.birth_date.isoformat()},
        "activite": {p: profile.activity},
        "salaire_net": salaries,
    }
    if profile.annual_taxable_income_n2_cents is not None:
        n2 = str(period.year - 2)
        monthly = round(profile.annual_taxable_income_n2_cents / 12 / 100, 2)
        taxable = {m: (monthly if m.startswith(n2) else round(v * NET_TO_TAXABLE, 2)) for m, v in salaries.items()}
    else:
        taxable = {m: round(v * NET_TO_TAXABLE, 2) for m, v in salaries.items()}
    me["salaire_imposable"] = taxable
    if unemployment_by_month:
        me["chomage_net"] = _fill(unemployment_by_month, all_months)
    if pension_by_month:
        me["retraite_nette"] = _fill(pension_by_month, all_months)
    for var in INDIVIDU_OUTPUTS:
        me[var] = {p: None}

    individus: dict[str, Any] = {"moi": me}
    parents = ["moi"]
    if profile.couple:
        partner_net = {m: round(profile.partner_monthly_net_cents / 100, 2) for m in all_months}
        individus["conjoint"] = {
            "date_naissance": {"ETERNITY": (profile.partner_birth_date or profile.birth_date).isoformat()},
            "salaire_net": partner_net,
            "salaire_imposable": {m: round(v * NET_TO_TAXABLE, 2) for m, v in partner_net.items()},
        }
        parents.append("conjoint")
    children = []
    for i, birth in enumerate(sorted(profile.children_birth_dates)):
        cid = f"enfant_{i + 1}"
        individus[cid] = {"date_naissance": {"ETERNITY": birth.isoformat()}}
        children.append(cid)

    famille: dict[str, Any] = {"parents": parents, "enfants": children}
    famille.update({var: {p: None} for var in FAMILLE_OUTPUTS})
    menage: dict[str, Any] = {
        "personne_de_reference": ["moi"],
        # le chèque énergie (annuel) lit le logement au premier mois de l'année
        "statut_occupation_logement": {f"{period.year:04d}-01": profile.housing, p: profile.housing},
        "loyer": {p: round(profile.rent_cents / 100, 2)},
        "depcom": {p: profile.postal_insee_code},
    }
    if profile.couple:
        menage["conjoint"] = ["conjoint"]
    if children:
        menage["enfants"] = children
    return {
        "individus": individus,
        "familles": {"famille": famille},
        "foyers_fiscaux": {"foyer": {"declarants": parents, "personnes_a_charge": children}},
        "menages": {"menage": menage},
    }
