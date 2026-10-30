"""Pouvoir d'achat : l'historique réel des paies et des dépenses face à l'inflation nationale datée.

Le repère d'inflation est l'inflation sur un an relevée à l'INSEE par la relève mensuelle des barèmes
(`RateObservation`, série `insee.ipc_glissement`, accord « barèmes ») quand elle est plus récente que la valeur
embarquée dans cette version de Bank (`domain.purchasing_power.INSEE_2026_09`, repli daté)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import purchasing_power as domain
from app.domain.baremes import period_start
from app.domain.recurring import add_months
from app.models import RateObservation
from app.services import analytics
from app.services.categories import category_infos

IPC_SERIES = "insee.ipc_glissement"
STALE_AFTER_DAYS = 60


def benchmark(db: Session, today: date) -> dict[str, Any]:
    """L'inflation sur un an la plus récente que Bank connaît : relevée à l'INSEE, sinon la valeur embarquée."""
    built_in = {**domain.INSEE_2026_09}
    rows = list(db.scalars(select(RateObservation).where(RateObservation.series == IPC_SERIES)))
    observed = max(rows, key=lambda r: period_start(r.period), default=None)
    out = built_in
    if observed is not None and period_start(observed.period) > period_start(str(built_in["as_of"])):
        try:
            value = float(Decimal(observed.value))
        except (InvalidOperation, ValueError):
            value = None
        if value is not None:
            out = {
                "as_of": observed.period[:7],
                "published_on": observed.fetched_at.date().isoformat(),
                "annual_pct": value,
                "status": "releve",
                "source": f"Insee — inflation sur un an, {observed.period[:7]} (relevé par Bank)",
                "url": observed.source_url,
            }
    published = date.fromisoformat(str(out["published_on"]))
    out["stale"] = (today - published).days > STALE_AFTER_DAYS
    out["note"] = (
        "Inflation sur un an relevée à l'INSEE par la mise à jour mensuelle des barèmes."
        if out["status"] == "releve"
        else "Dernier IPC définitif documenté dans cette version de Bank ; la relève mensuelle des barèmes "
        "(accord « barèmes ») le remplace dès qu'un mois plus récent est publié."
    )
    return out


def view(db: Session, today: date) -> dict[str, Any]:
    benchmark_ = benchmark(db, today)
    accounts = analytics.budget_accounts(db)
    if not accounts:
        return {
            "available": False,
            "benchmark": benchmark_,
            "comparison": None,
            "note": "Relie ou importe un compte courant pour établir une comparaison de salaire.",
        }
    first = today.replace(day=1)
    history = analytics.load_txns(db, [a.id for a in accounts], add_months(first, -15))
    comparison = domain.compare(history, category_infos(db), today, inflation_pct=float(str(benchmark_["annual_pct"])))
    return {
        "available": True,
        "benchmark": benchmark_,
        "comparison": comparison,
        "note": "Aucun achat, virement ou arbitrage automatique. "
        "Le plan d'investissement existant reste prioritaire pour l'horizon, le risque et l'épargne de précaution.",
    }


def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    """Opt-in channels inherit quiet hours, revocation and deduplication from channel_alerts."""
    data = view(db, today)
    bench = data["benchmark"]
    comp = data.get("comparison")
    if bench["stale"] or not isinstance(comp, dict) or not comp.get("ready"):
        return []
    if comp["real_salary_change_pct"] is None or comp["real_salary_change_pct"] > -2:
        return []
    gap = comp.get("monthly_real_gap_cents")
    if gap is None or gap >= -5_000:
        return []
    reference = bench["as_of"]
    month_key = today.strftime("%Y-%m")
    text = (
        f"📉 Ton salaire net moyen observé ne suit pas l'inflation nationale de {reference} "
        f"({bench['annual_pct']:.1f} %/an). "
        "Vérifie tes mois de paie et tes dépenses ; le résultat est indicatif, pas une prédiction."
    )
    if privacy == "full":
        from app.domain.money import format_eur

        text += f" Écart de pouvoir d'achat estimé : {format_eur(gap)} par mois."
    text += f" {public_url}/#/veille"
    return [(f"pouvoirachat:{reference}:{month_key}", text, False)]
