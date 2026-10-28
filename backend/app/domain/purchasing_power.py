"""Purchasing power: salary reality vs national inflation and individual spending trends.

Only completed calendar months, always compare the *same three months* a year apart.
A spending increase is not a personal price index: consumption quantities can change.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.domain.forecast import CategoryInfo
from app.domain.recurring import Txn, add_months

MONTH_COUNT = 3
INSEE_2026_09 = {
    "as_of": "2026-09",
    "published_on": "2026-10-15",
    "annual_pct": 3.1,
    "status": "definitif",
    "source": "Insee — IPC ensemble des ménages, septembre 2026",
    "url": "https://www.insee.fr/fr/statistiques/9080209",
}


def real_change_pct(nominal_pct: float, inflation_pct: float) -> float:
    """Real percentage-point change is multiplicative, not nominal minus inflation."""
    if nominal_pct <= -100 or inflation_pct <= -100:
        raise ValueError("Variation incompatible avec l'indice des prix")
    return round(((1 + nominal_pct / 100) / (1 + inflation_pct / 100) - 1) * 100, 2)


def real_yield_pct(gross_pct: float, fees_pct: float, tax_pct: float, inflation_pct: float) -> dict[str, float]:
    """Illustrative annual rates; taxation of *positive* gains only, no tax-wrapper claims."""
    if not (-100 < gross_pct <= 200 and 0 <= fees_pct <= 30 and 0 <= tax_pct <= 100 and -30 < inflation_pct <= 100):
        raise ValueError("Hypothèses de placement hors des limites")
    after_fees = (1 + gross_pct / 100) * (1 - fees_pct / 100) - 1
    after_tax = after_fees * (1 - tax_pct / 100) if after_fees > 0 else after_fees
    net = after_tax * 100
    return {
        "nominal_after_fees_and_tax_pct": round(net, 2),
        "real_annual_pct": real_change_pct(net, inflation_pct),
    }


def detect_pressures(
    *,
    nominal_salary_pct: float | None,
    real_salary_pct: float | None,
    spending_pct: float | None,
    spending_share_points: float | None,
) -> list[dict[str, str]]:
    """Rapprochements observables, jamais une affirmation de cause à effet ni une prédiction boursière."""
    findings: list[dict[str, str]] = []
    if (
        nominal_salary_pct is not None
        and real_salary_pct is not None
        and nominal_salary_pct > 0
        and real_salary_pct < 0
    ):
        findings.append(
            {
                "key": "salary_vs_inflation",
                "level": "watch",
                "title": "Ton salaire augmente, mais ton pouvoir d'achat estimé recule",
                "evidence": (
                    f"Salaire nominal : +{nominal_salary_pct:.2f} % ; "
                    f"évolution réelle sur le repère national : {real_salary_pct:.2f} %."
                ),
                "action": "Comparer les salaires et l'IPC sur leurs périodes et vérifier les relevés.",
            }
        )
    if (
        spending_pct is not None
        and spending_share_points is not None
        and spending_pct > 0
        and spending_share_points >= 3
    ):
        findings.append(
            {
                "key": "spending_share_rises",
                "level": "watch",
                "title": "Tes dépenses occupent une place plus grande dans ton salaire",
                "evidence": (
                    f"Dépenses observées : +{spending_pct:.2f} % ; "
                    f"part du salaire : +{spending_share_points:.2f} points."
                ),
                "action": "Examiner les catégories et les achats ponctuels avant d'en déduire une hausse des prix.",
            }
        )
    return findings


def compare(
    txns: Sequence[Txn], categories: Mapping[str, CategoryInfo], today: date, *, inflation_pct: float
) -> dict[str, Any]:
    first = today.replace(day=1)
    recent = [add_months(first, -i).strftime("%Y-%m") for i in (3, 2, 1)]
    old = [add_months(first, -i).strftime("%Y-%m") for i in (15, 14, 13)]
    watched = set(recent + old)
    salaries: dict[str, int] = defaultdict(int)
    spent: dict[str, int] = defaultdict(int)
    spent_by_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        period = t.date.strftime("%Y-%m")
        if period not in watched or t.is_transfer:
            continue
        kind = categories.get(t.category)
        if t.amount_cents > 0 and t.category == "salaire":
            salaries[period] += t.amount_cents
        elif t.amount_cents < 0 and (kind is None or kind.kind not in ("savings", "transfer")):
            amount = -t.amount_cents
            spent[period] += amount
            spent_by_kind[t.category][period] += amount

    # All six salary months needed: missing statements must not become a 0 € salary.
    enough = all(salaries[m] > 0 for m in recent + old)
    current_salary = round(sum(salaries[m] for m in recent) / MONTH_COUNT) if enough else None
    previous_salary = round(sum(salaries[m] for m in old) / MONTH_COUNT) if enough else None
    nominal: float | None = None
    real: float | None = None
    equivalent: int | None = None
    gap: int | None = None
    if current_salary is not None and previous_salary is not None and previous_salary > 0:
        nominal = round((current_salary / previous_salary - 1) * 100, 2)
        real = real_change_pct(nominal, inflation_pct)
        equivalent = round(previous_salary * (1 + inflation_pct / 100))
        gap = current_salary - equivalent

    recent_spent = round(sum(spent[m] for m in recent) / MONTH_COUNT)
    old_spent = round(sum(spent[m] for m in old) / MONTH_COUNT)
    spending_change: float | None = None
    if old_spent > 0 and all(spent[m] > 0 for m in old + recent):
        spending_change = round((recent_spent / old_spent - 1) * 100, 2)

    pressure: float | None = None
    if current_salary and previous_salary:
        pressure = round((recent_spent / current_salary - old_spent / previous_salary) * 100, 2)

    drivers: list[dict[str, str | int]] = []
    if spending_change is not None:
        for slug, months in spent_by_kind.items():
            old_avg = round(sum(months[m] for m in old) / MONTH_COUNT)
            cur_avg = round(sum(months[m] for m in recent) / MONTH_COUNT)
            difference = cur_avg - old_avg
            if abs(difference) >= 1_000:
                drivers.append(
                    {
                        "category": slug,
                        "old_avg_cents": old_avg,
                        "current_avg_cents": cur_avg,
                        "delta_cents": difference,
                    }
                )
        drivers.sort(key=lambda r: -abs(int(r["delta_cents"])))

    return {
        "ready": enough,
        "recent_months": recent,
        "comparison_months": old,
        "salary_months_found": sum(salaries[m] > 0 for m in recent + old),
        "current_salary_avg_cents": current_salary,
        "previous_salary_avg_cents": previous_salary,
        "nominal_salary_change_pct": nominal,
        "real_salary_change_pct": real,
        "salary_to_keep_up_cents": equivalent,
        "monthly_real_gap_cents": gap,
        "current_spending_avg_cents": recent_spent if spending_change is not None else None,
        "previous_spending_avg_cents": old_spent if spending_change is not None else None,
        "spending_change_pct": spending_change,
        "spending_share_change_points": pressure if spending_change is not None else None,
        "spending_drivers": drivers[:5],
        "correlations": detect_pressures(
            nominal_salary_pct=nominal,
            real_salary_pct=real,
            spending_pct=spending_change,
            spending_share_points=pressure if spending_change is not None else None,
        ),
        "note": "Salaire net observé uniquement si six mois de salaires classés sont présents. "
        "Comparaison de trois mois complets avec les mêmes trois mois un an plus tôt. "
        "L'IPC national publié pour septembre sert de repère annuel indicatif, et non "
        "d'indice personnel exact de ces trois mois. Une hausse des dépenses peut aussi "
        "venir de quantités ou d'achats différents.",
    }
