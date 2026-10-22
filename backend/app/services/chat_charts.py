"""Graphiques du chat : des jeux de données calculés par Bank, jamais des chiffres du modèle. Le modèle choisit un
jeu (et une période) ; Bank construit le bloc avec ses propres données, et le modèle le commente."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import chatblocks as blocks
from app.domain.recurring import add_months
from app.services import analytics, crypto, energy, history, yearahead
from app.services.history import MONTHS_FR

PERIODS = {"month": "30d", "quarter": "3m", "year": "1y"}
PERIOD_LABEL = {"month": "30 derniers jours", "quarter": "3 derniers mois", "year": "12 derniers mois"}

DATASETS: dict[str, str] = {
    "spend_by_category": "répartition des dépenses par catégorie (anneau), période month | quarter | year",
    "income_vs_spend": "reçu et dépensé par mois (barres), période quarter | year",
    "balance": "solde jour par jour (courbe), période month | quarter | year",
    "aids": "aides reçues par organisme sur douze mois (barres)",
    "year_ahead": "solde projeté mois par mois sur l'année qui vient (barres)",
    "net_worth": "patrimoine net dans le temps (courbe)",
    "crypto": "répartition crypto par actif (anneau)",
    "energy": "consommation électrique par mois en kWh (barres)",
}


class ChartError(ValueError):
    pass


def prompt_lines() -> list[str]:
    return [f"- {k} : {v}" for k, v in DATASETS.items()]


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTHS_FR[int(m) - 1]} {y[2:]}"


def build(db: Session, today: date, dataset: str, period: str | None = None) -> dict[str, Any]:
    """Le bloc graphique d'un jeu de données. Lève ChartError si le jeu n'existe pas ou s'il est vide."""
    if dataset not in DATASETS:
        raise ChartError("Jeu de données inconnu")
    per = period if period in PERIODS else None
    if dataset == "spend_by_category":
        per = per or "month"
        h = history.history(db, today, PERIODS[per])
        items = [{"label": c["name"], "value": c["cents"], "color": c["color"]} for c in h["categories"]]
        if not items:
            raise ChartError("Aucune dépense sur la période")
        return blocks.chart_block(
            "donut", f"Dépenses par catégorie · {PERIOD_LABEL[per]}", items=items,
            source=f"Bank · opérations des {PERIOD_LABEL[per]}", dataset=dataset,
        )  # fmt: skip
    if dataset == "income_vs_spend":
        per = per if per in ("quarter", "year") else "year"
        h = history.history(db, today, PERIODS[per])
        items = [{"label": p["label"], "a": p["income_cents"], "b": p["spent_cents"]} for p in h["periods"]]
        if not items:
            raise ChartError("Aucune opération sur la période")
        return blocks.chart_block(
            "pairs", f"Reçu et dépensé · {PERIOD_LABEL[per]}", items=items,
            source=f"Bank · opérations des {PERIOD_LABEL[per]}", dataset=dataset,
        )  # fmt: skip
    if dataset == "balance":
        per = per or "month"
        h = history.history(db, today, PERIODS[per])
        pts = h["balances"]
        step = max(1, len(pts) // blocks.MAX_CHART_ITEMS)
        picked = pts[::step][-blocks.MAX_CHART_ITEMS :]
        if picked and picked[-1] is not pts[-1]:
            picked[-1] = pts[-1]
        items = [{"label": p["date"], "value": p["balance_cents"]} for p in picked]  # ISO : le navigateur formate
        if not items:
            raise ChartError("Aucun solde sur la période")
        return blocks.chart_block(
            "line", f"Solde · {PERIOD_LABEL[per]}", items=items, source="Bank · comptes du budget", dataset=dataset
        )
    if dataset == "aids":
        summary = analytics.aids_summary(db, today)
        by_org: dict[str, int] = defaultdict(int)
        for s in summary.streams:
            by_org[s.organism_name] += sum(s.monthly_totals.values())
        items = [{"label": k, "value": v} for k, v in sorted(by_org.items(), key=lambda kv: -kv[1]) if v > 0]
        if not items:
            raise ChartError("Aucune aide reconnue sur douze mois")
        return blocks.chart_block(
            "bars",
            "Aides reçues par organisme · 12 mois",
            items=items,
            source="Bank · versements reconnus",
            dataset=dataset,
        )
    if dataset == "year_ahead":
        y = yearahead.compute(db, today)
        items = [{"label": p.label, "value": p.end_balance_cents} for p in y.points]
        return blocks.chart_block(
            "bars", "L'année qui vient · solde projeté en fin de mois", items=items,
            source="Bank · projection (ordre de grandeur, pas une promesse)", dataset=dataset,
        )  # fmt: skip
    if dataset == "net_worth":
        series = history.net_worth_history(db, today)
        if not series:
            raise ChartError("Pas encore d'historique de patrimoine")
        step = max(1, len(series) // blocks.MAX_CHART_ITEMS)
        picked = series[::step][-blocks.MAX_CHART_ITEMS :]
        items = [{"label": d.isoformat(), "value": v} for d, v in picked]
        return blocks.chart_block(
            "line", "Patrimoine net", items=items, source="Bank · relevés quotidiens", dataset=dataset
        )
    if dataset == "crypto":
        v = crypto.view(db, today=today, now=utcnow())
        items = [{"label": a["name"], "value": a["value_cents"]} for a in v["assets"] if a["value_cents"] > 0]
        if not items:
            raise ChartError("Aucune crypto suivie")
        return blocks.chart_block(
            "donut", "Crypto par actif", items=items, source="Bank · cours CoinGecko", dataset=dataset
        )
    # energy
    since = add_months(today.replace(day=1), -11)
    by_month: dict[str, int] = defaultdict(int)
    for r in energy.readings(db, since):
        by_month[r.day.strftime("%Y-%m")] += r.wh
    items = [{"label": _month_label(k), "value": round(wh / 1000)} for k, wh in sorted(by_month.items())]
    if not items:
        raise ChartError("Aucun relevé Linky")
    return blocks.chart_block(
        "bars", "Électricité consommée par mois", unit="kwh", items=items, source="Bank · relevés Linky",
        dataset=dataset,
    )  # fmt: skip
