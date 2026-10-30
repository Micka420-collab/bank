"""Comparateur de placements : même capital de départ sur chaque produit, taux réglementés datés, hypothèse de
marché saisie par la personne. La situation (réserve, dettes chères, profil, lignes rouges, découvert, éligibilité
LEP) est celle que lit le plan de placement de Bank (`decisions.plan_context`) : une seule lecture."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.domain import invest
from app.domain import investment_compare as domain
from app.domain.wealth import load_regulated_products, regulated_status
from app.services import decisions


def view(
    db: Session,
    today: date,
    *,
    capital_cents: int,
    horizon_months: int,
    inflation_pct: float,
    custom_gross_pct: float | None = None,
    custom_fees_pct: float = 0,
    custom_tax_pct: float = 0,
    custom_loss_pct: float = 25,
    custom_locked_months: int = 0,
) -> dict[str, Any]:
    """Identical starting capital across products; an inaccessible cap cannot silently be ignored."""
    if not 0 <= custom_locked_months <= domain.MAX_MONTHS:
        raise ValueError("Durée de blocage hors limites")
    ctx = decisions.plan_context(db, today)
    values, accounts, essentials = ctx.values, ctx.balances, ctx.monthly_essentials_cents
    reserve_gap = ctx.reserve_gap_cents
    risk = ctx.risk
    horizon = values.get("risk_horizon")
    overdraft_risk = ctx.overdraft
    excluded = invest.excluded_by_red_lines(ctx.red_lines)
    products = load_regulated_products()
    room_by_kind = {x.kind: x.room_cents for x in regulated_status(accounts, products)}
    records: list[dict[str, Any]] = []
    for p in products:
        result = domain.project(
            capital_cents,
            horizon_months,
            gross_pct=float(p.rate_percent),
            fees_pct=0,
            tax_pct=0,
            inflation_pct=inflation_pct,
        )
        fitting = capital_cents <= room_by_kind[p.kind]
        suitability = domain.assess(
            reserve_gap_cents=reserve_gap,
            overdraft_risk=overdraft_risk,
            risk_label=risk.label,
            horizon_months=horizon_months,
            locked_months=0,
            loss_pct=0,
            investment_kind=p.kind,
            eligible=ctx.lep_eligible if p.kind == "lep" else True,
            full_room=fitting,
        )
        records.append(
            {
                "kind": p.kind,
                "label": p.name,
                "status": "taux_dated",
                "gross_pct": float(p.rate_percent),
                "fees_pct": 0,
                "tax_pct": 0,
                "risk_level": "capital_protege_sous_conditions",
                "locked_months": 0,
                "rates_as_of": p.as_of,
                "source": p.source,
                "rate_note": p.note,
                "room_cents": room_by_kind[p.kind],
                "projection": result,
                "suitability": suitability,
                "warning": "Taux connu à la date indiquée, supposé constant sur toute la simulation. "
                "Il peut être révisé ; intérêts et calcul des quinzaines simplifiés.",
            }
        )
    if custom_gross_pct is not None:
        blocked = "Ligne rouge du mandat : exposition boursière exclue." if "actions" in excluded else None
        result = domain.project(
            capital_cents,
            horizon_months,
            gross_pct=custom_gross_pct,
            fees_pct=custom_fees_pct,
            tax_pct=custom_tax_pct,
            inflation_pct=inflation_pct,
            loss_pct=custom_loss_pct,
        )
        suitability = domain.assess(
            reserve_gap_cents=reserve_gap,
            overdraft_risk=overdraft_risk,
            risk_label=risk.label,
            horizon_months=horizon_months,
            locked_months=custom_locked_months,
            loss_pct=custom_loss_pct,
            investment_kind="market",
            eligible=True,
            full_room=True,
        )
        if blocked:
            suitability["blockers"].append(blocked)
            suitability["eligible_to_consider"] = False
        records.append(
            {
                "kind": "market",
                "label": "Placement de marché (hypothèse utilisateur)",
                "status": "hypothese",
                "gross_pct": custom_gross_pct,
                "fees_pct": custom_fees_pct,
                "tax_pct": custom_tax_pct,
                "risk_level": "perte_possible",
                "locked_months": custom_locked_months,
                "rates_as_of": None,
                "source": "Saisie manuelle",
                "room_cents": None,
                "projection": result,
                "suitability": suitability,
                "warning": "Rendement, frais, fiscalité, durée de blocage et choc négatif sont tes hypothèses. "
                "Le choc isolé n'est ni une borne maximale ni une probabilité ; une perte totale reste possible.",
            }
        )
    return {
        "capital_cents": capital_cents,
        "horizon_months": horizon_months,
        "inflation_pct": inflation_pct,
        "context": {
            "risk_profile": risk.label,
            "risk_capacity_limited": risk.capacity_limited,
            "risk_reason": risk.reason,
            "declared_horizon": horizon,
            "reserve_gap_cents": reserve_gap,
            "overdraft_risk": overdraft_risk,
            "data_complete": bool(accounts) and essentials > 0,
        },
        "scenarios": records,
        "note": "Comparaison informative, pas une recommandation d'achat ni un classement par gain. "
        "Aucun capital n'est déplacé. Les produits de marché sont exclus de la comparaison "
        "tant qu'aucune hypothèse n'est saisie ; les chiffres réels d'un produit doivent être vérifiés.",
    }
