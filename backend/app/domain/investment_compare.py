"""Comparateur pédagogique : mêmes capital/horizon, prix courants sourcés et scénario non garanti.

Aucune prévision de performance pour actions, PEA, assurance vie ou crypto. Les rendements
saisis sont des hypothèses personnelles ; l'impôt appliqué à la sortie est simplifié.
"""

from __future__ import annotations

import math
from typing import Any

MAX_CAPITAL = 100_000_000
MAX_MONTHS = 360


def project(
    capital_cents: int,
    horizon_months: int,
    *,
    gross_pct: float,
    fees_pct: float,
    tax_pct: float,
    inflation_pct: float,
    loss_pct: float = 0,
) -> dict[str, float | int]:
    """Capitalise chaque mois à partir de taux annuels effectifs ; taxe seulement un gain positif à la sortie."""
    values = (gross_pct, fees_pct, tax_pct, inflation_pct, loss_pct)
    if (
        not 10_000 <= capital_cents <= MAX_CAPITAL
        or not 1 <= horizon_months <= MAX_MONTHS
        or not all(math.isfinite(x) for x in values)
        or not -99 < gross_pct <= 200
        or not 0 <= fees_pct <= 30
        or not 0 <= tax_pct <= 100
        or not -29 < inflation_pct <= 100
        or not 0 <= loss_pct <= 100
    ):
        raise ValueError("Capital, durée ou taux hors des limites du comparateur")
    years = horizon_months / 12
    after_fees_factor = (1 + gross_pct / 100) * (1 - fees_pct / 100)
    before_tax = capital_cents * after_fees_factor**years
    tax = max(0.0, before_tax - capital_cents) * tax_pct / 100
    net = before_tax - tax
    purchasing_factor = (1 + inflation_pct / 100) ** years
    real = net / purchasing_factor
    # Un choc instantané de marché, distinct de toute projection de rendement.
    downside = capital_cents * (1 - loss_pct / 100)
    return {
        "future_nominal_cents": round(net),
        "future_real_cents": round(real),
        "net_gain_cents": round(net - capital_cents),
        "real_gain_cents": round(real - capital_cents),
        "inflation_only_threshold_cents": round(capital_cents * purchasing_factor),
        "hypothetical_shock_value_cents": round(downside),
        "hypothetical_shock_loss_cents": round(capital_cents - downside),
        "effective_annual_net_pct": round(((net / capital_cents) ** (1 / years) - 1) * 100, 2),
        "effective_annual_real_pct": round(((real / capital_cents) ** (1 / years) - 1) * 100, 2),
    }


def assess(
    *,
    reserve_gap_cents: int | None,
    overdraft_risk: bool | None,
    risk_label: str,
    horizon_months: int,
    locked_months: int,
    loss_pct: float,
    investment_kind: str,
    eligible: bool | None,
    full_room: bool,
) -> dict[str, Any]:
    """Les incompatibilités priment sur le gain théorique, même si l'utilisateur saisit 99 % de rendement."""
    blockers: list[str] = []
    cautions: list[str] = []
    if eligible is False:
        blockers.append("Conditions d'accès non remplies.")
    elif eligible is None and investment_kind == "lep":
        blockers.append("Éligibilité au LEP non vérifiée : consulter le revenu fiscal de référence.")
    if not full_room:
        blockers.append("Plafond ou place disponible insuffisante pour le montant comparé.")
    if locked_months > horizon_months:
        blockers.append("L'argent serait bloqué après la date où tu en as besoin.")
    if overdraft_risk is True:
        blockers.append("Un découvert est prévu : préserver le compte courant d'abord.")
    if reserve_gap_cents is not None and reserve_gap_cents > 0 and investment_kind == "market":
        blockers.append("Constituer l'épargne de précaution avant un placement à perte possible.")
    if investment_kind == "market":
        if horizon_months < 60:
            blockers.append("Horizon inférieur à cinq ans pour ce scénario à perte possible.")
        if risk_label in ("prudent", "inconnu"):
            cautions.append("Profil prudent ou incomplet : vérifier la capacité à supporter une perte.")
        if loss_pct == 0:
            cautions.append("Aucun choc négatif saisi : l'absence de stress n'implique pas l'absence de risque.")
    if reserve_gap_cents is None:
        if investment_kind == "market":
            blockers.append("Réserve de sécurité inconnue : relier ses comptes avant d'évaluer le risque.")
        else:
            cautions.append("La réserve de sécurité ne peut pas encore être vérifiée dans Bank.")
    if overdraft_risk is None and investment_kind == "market":
        blockers.append("Trésorerie à court terme inconnue : vérifier le risque de découvert.")
    if risk_label == "inconnu" and investment_kind == "market":
        blockers.append("Profil de risque incomplet : répondre aux questions de capacité de perte.")
    return {"eligible_to_consider": not blockers, "blockers": blockers, "cautions": cautions}
