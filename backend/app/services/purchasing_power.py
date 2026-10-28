"""Link factual salary/payment history and dated Insee benchmark to the purchasing-power engine."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.domain import purchasing_power as domain
from app.domain.recurring import add_months
from app.services import analytics
from app.services.categories import category_infos


def view(db: Session, today: date) -> dict[str, Any]:
    benchmark = {**domain.INSEE_2026_09}
    published = date.fromisoformat(str(benchmark["published_on"]))
    benchmark["stale"] = (today - published).days > 60
    benchmark["note"] = (
        "Dernier IPC définitif documenté dans cette version de Bank. "
        "Ce taux n'est pas récupéré en direct : suivre la publication officielle pour les mois suivants."
    )
    accounts = analytics.budget_accounts(db)
    if not accounts:
        return {
            "available": False,
            "benchmark": benchmark,
            "comparison": None,
            "note": "Relie ou importe un compte courant pour établir une comparaison de salaire.",
        }
    first = today.replace(day=1)
    history = analytics.load_txns(db, [a.id for a in accounts], add_months(first, -15))
    comparison = domain.compare(history, category_infos(db), today, inflation_pct=float(benchmark["annual_pct"]))
    return {
        "available": True,
        "benchmark": benchmark,
        "comparison": comparison,
        "note": "Aucun achat, virement ou arbitrage automatique. "
        "Le plan d'investissement existant reste prioritaire pour l'horizon, le risque et l'épargne de précaution.",
    }


def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    """Opt-in channels inherit quiet hours, revocation and deduplication from channel_alerts."""
    data = view(db, today)
    benchmark = data["benchmark"]
    comp = data.get("comparison")
    if benchmark["stale"] or not isinstance(comp, dict) or not comp.get("ready"):
        return []
    if comp["real_salary_change_pct"] is None or comp["real_salary_change_pct"] > -2:
        return []
    gap = comp.get("monthly_real_gap_cents")
    if gap is None or gap >= -5_000:
        return []
    reference = benchmark["as_of"]
    month_key = today.strftime("%Y-%m")
    text = (
        f"📉 Ton salaire net moyen observé ne suit pas l'inflation nationale de {reference} "
        f"({benchmark['annual_pct']:.1f} %/an). "
        "Vérifie tes mois de paie et tes dépenses ; le résultat est indicatif, pas une prédiction."
    )
    if privacy == "full":
        from app.domain.money import format_eur

        text += f" Écart de pouvoir d'achat estimé : {format_eur(gap)} par mois."
    text += f" {public_url}/#/veille"
    return [(f"pouvoirachat:{reference}:{month_key}", text, False)]
