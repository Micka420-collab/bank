"""Opportunités : ce que tu pourrais récupérer, réduire, gagner, mieux placer ou anticiper — classé par gain net
attendu, avec ce qui est constaté, ce qui est estimé et ce qui est supposé, et d'où viennent les règles.

Gain net attendu (par an) = montant annualisé × probabilité − coût − effort × valeur de ton temps.
Rien n'est promis : une estimation reste une estimation, une aide reste à confirmer par l'organisme.
Module pur : aucune base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

CATEGORIES = {
    "recuperer": "Récupérer",
    "reduire": "Réduire tes dépenses",
    "revenus": "Augmenter tes revenus",
    "epargne": "Mieux épargner",
    "anticiper": "Anticiper",
}
PERIOD_FACTOR = {"once": 1, "month": 12, "year": 1}
DEFAULT_TIME_VALUE_CENTS_PER_HOUR = 1_500  # hypothèse réglable : une heure de ton temps vaut 15 €


@dataclass(frozen=True)
class Source:
    title: str
    url: str | None = None
    verified_on: str | None = None


@dataclass(frozen=True)
class Opportunity:
    key: str
    category: str
    title: str
    summary: str
    amount_cents: int  # gain par période (0 : pas d'argent en jeu, mais un risque évité ou une échéance)
    period: str  # once | month | year
    probability: float  # chance que le gain se concrétise (0 à 1)
    effort_minutes: int
    confidence: str  # élevée | moyenne | faible
    facts: tuple[str, ...] = ()  # constaté dans tes données
    estimates: tuple[str, ...] = ()  # calculé par Bank (ou OpenFisca)
    hypotheses: tuple[str, ...] = ()  # supposé, à vérifier
    sources: tuple[Source, ...] = ()
    action: dict[str, Any] = field(default_factory=dict)  # {"template", "initial"} ou {"link", "label"}
    cost_cents: int = 0
    deadline: date | None = None
    urgency: int = 0  # 0 à 3 : passe devant quand l'échéance est proche


def yearly(amount_cents: int, period: str) -> int:
    return amount_cents * PERIOD_FACTOR.get(period, 1)


def expected_net(opp: Opportunity, time_value_cents_per_hour: int = DEFAULT_TIME_VALUE_CENTS_PER_HOUR) -> int:
    """Gain net attendu sur un an, en centimes (peut être négatif : l'effort coûte plus que le gain probable)."""
    effort = round(opp.effort_minutes * time_value_cents_per_hour / 60)
    return round(yearly(opp.amount_cents, opp.period) * opp.probability) - opp.cost_cents - effort


def rank(
    opps: list[Opportunity], today: date, time_value_cents_per_hour: int = DEFAULT_TIME_VALUE_CENTS_PER_HOUR
) -> list[Opportunity]:
    """Ce qui a une échéance proche d'abord (urgence 2 ou plus, ou échéance dans les 14 jours), puis le gain net
    attendu ; une urgence légère (1) ne départage qu'à gain égal — elle ne passe jamais devant un gain chiffré."""

    def tier(o: Opportunity) -> int:
        if o.deadline is not None and (o.deadline - today).days <= 14:
            return max(o.urgency, 2)
        return o.urgency if o.urgency >= 2 else 0

    return sorted(opps, key=lambda o: (-tier(o), -expected_net(o, time_value_cents_per_hour), -o.urgency, o.key))


# ============================================================================ frais bancaires
# Plafonds réglementaires (Code monétaire et financier) — vérifiés le 21/10/2026, voir docs/COPILOTE.md.
INTERVENTION_PER_OP_CENTS = 800  # R. 312-4-1
INTERVENTION_PER_MONTH_CENTS = 8_000
INTERVENTION_FRAGILE_PER_OP_CENTS = 400  # R. 312-4-2
INTERVENTION_FRAGILE_PER_MONTH_CENTS = 2_000
DIRECT_DEBIT_REJECT_MAX_CENTS = 2_000  # D. 133-6 (et jamais plus que le prélèvement rejeté)
FEE_SOURCES = (
    Source(
        "Code monétaire et financier, art. R. 312-4-1 et R. 312-4-2 (commissions d'intervention), D. 133-6 (rejet "
        "de prélèvement)"
    ),
    Source(
        "INC — délais de réclamation et médiateur bancaire (recommandation ACPR 2022-R-01)",
        "https://www.inc-conso.fr/content/mediation-bancaire-et-assurantielle-amelioration-de-la-procedure-de-traitement",
        "2026-10-21",
    ),
)

_INTERVENTION = re.compile(r"COMMISSION\S* (?:D )?INTERV|COM\.? ?INTERV|FRAIS (?:D )?INTERVENTION")
_REJECT = re.compile(r"REJET|IMPAYE|FRAIS (?:SUR |DE )?(?:PRLV|PRELEVEMENT) (?:REJETE|IMPAYE)")
_OVERDRAFT = re.compile(r"AGIOS|INTERETS? DEBITEURS?")
_PACKAGE = re.compile(r"COTISATION|OFFRE GROUPEE|PACKAGE|TENUE DE COMPTE|CARTE")


@dataclass(frozen=True)
class Fee:
    on: date
    label: str
    amount_cents: int  # positif


def fee_kind(label: str) -> str:
    text = label.upper()
    if _INTERVENTION.search(text):
        return "intervention"
    if _REJECT.search(text):
        return "rejet"
    if _OVERDRAFT.search(text):
        return "agios"
    if _PACKAGE.search(text):
        return "cotisation"
    return "autre"


@dataclass(frozen=True)
class FeeReview:
    month: str
    incident_total_cents: int  # interventions, rejets, agios : ce qui se conteste
    excess_cents: int  # part au-delà des plafonds réglementaires
    reasons: tuple[str, ...]
    fees: tuple[Fee, ...]


def review_fees(month: str, fees: list[Fee], *, fragile: bool = False) -> FeeReview:
    """Ce qu'on peut contester un mois donné, et ce qui dépasse les plafonds réglementaires."""
    per_op = INTERVENTION_FRAGILE_PER_OP_CENTS if fragile else INTERVENTION_PER_OP_CENTS
    per_month = INTERVENTION_FRAGILE_PER_MONTH_CENTS if fragile else INTERVENTION_PER_MONTH_CENTS
    interventions = [f for f in fees if fee_kind(f.label) == "intervention"]
    rejects = [f for f in fees if fee_kind(f.label) == "rejet"]
    agios = [f for f in fees if fee_kind(f.label) == "agios"]
    excess = 0
    reasons: list[str] = []
    over_op = sum(max(0, f.amount_cents - per_op) for f in interventions)
    if over_op:
        excess += over_op
        reasons.append(f"commission d'intervention au-delà de {per_op / 100:.0f} € par opération")
    capped_total = sum(min(f.amount_cents, per_op) for f in interventions)
    if capped_total > per_month:
        excess += capped_total - per_month
        reasons.append(f"commissions d'intervention au-delà de {per_month / 100:.0f} € sur le mois")
    over_reject = sum(max(0, f.amount_cents - DIRECT_DEBIT_REJECT_MAX_CENTS) for f in rejects)
    if over_reject:
        excess += over_reject
        reasons.append("frais de rejet de prélèvement au-delà de 20 €")
    incident = sum(f.amount_cents for f in (*interventions, *rejects, *agios))
    return FeeReview(month, incident, excess, tuple(reasons), tuple(sorted(fees, key=lambda f: f.on)))


def fees_detail(fees: tuple[Fee, ...]) -> str:
    return "\n".join(
        f"{f.on.strftime('%d/%m/%Y')} — {f.label} — {f.amount_cents // 100},{f.amount_cents % 100:02d} €" for f in fees
    )
