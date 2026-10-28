"""Contextual investment scenarios grounded in Bank balances and dated regulated rates."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.domain import invest
from app.domain import investment_compare as domain
from app.domain.onboarding import risk_profile
from app.domain.wealth import emergency_fund, load_regulated_products, regulated_status
from app.services import advisor, analytics


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
    values = advisor.plain_answers(db)
    accounts = analytics.account_balances(db, today)
    essentials = analytics.monthly_essentials(db, today) if accounts else 0
    ef = emergency_fund(accounts, essentials)
    target_months = int(values.get("emergency_target_months") or 3)
    reserve_gap = max(0, target_months * essentials - ef.available_cents) if essentials > 0 else None
    debts = values.get("debts") or []
    expensive = any(float(item.get("rate_percent") or 0) >= 5 for item in debts)
    risk = risk_profile(values, ef.months_covered, expensive)
    horizon = values.get("risk_horizon")
    overdraft_risk: bool | None = None
    if analytics.budget_accounts(db):
        forecast = analytics.forecast_inputs(db, today).run()
        overdraft_risk = forecast.overdraft_date is not None
    red_lines = [str(x.get("rule") or "") for x in values.get("red_lines") or []]
    excluded = invest.excluded_by_red_lines(red_lines)
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
            eligible=None if p.kind == "lep" else True,
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
