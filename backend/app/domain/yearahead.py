"""L'année qui vient : les pics (assurance annuelle, solde d'impôt, abonnement annuel, facture trimestrielle)
posés à leur mois, une projection mois par mois sur douze mois, le point bas de l'année et la provision mensuelle
qui lisse les pics. Le mois en cours reste à la prévision du mois (jour par jour) ; ici on part de sa fin.

Tout est pur : séries récurrentes détectées, opérations, catégories, pics ajoutés ou écartés par toi.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.forecast import CategoryInfo, _occurrences, daily_variable_rate, is_planned_outflow
from app.domain.recurring import RecurringSeries, Txn, add_months

MONTHS = 12
MIN_SPIKE_CENTS = 3_000  # en dessous, un prélèvement trimestriel ou annuel n'est pas un « pic »
MONTH_DAYS = 30.44
MONTHS_FR = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)


@dataclass(frozen=True)
class CustomSpike:
    key: str
    label: str
    amount_cents: int
    next_date: date
    every_months: int = 12


@dataclass(frozen=True)
class Spike:
    key: str
    label: str
    amount_cents: int
    day: date
    cadence: str  # quarterly | yearly | custom | planned
    source: str  # detected | custom | planned
    category: str | None = None
    occurrences: int = 0
    confidence: float = 1.0


@dataclass(frozen=True)
class MonthPoint:
    month: str
    label: str
    income_cents: int
    fixed_cents: int
    variable_cents: int
    spikes_cents: int
    spikes: tuple[str, ...]
    end_balance_cents: int


@dataclass(frozen=True)
class YearAhead:
    start_month: str
    start_balance_cents: int
    points: tuple[MonthPoint, ...]
    spikes: tuple[Spike, ...]
    ignored: tuple[Spike, ...]
    income_monthly_cents: int
    fixed_monthly_cents: int
    variable_monthly_cents: int
    yearly_spikes_cents: int
    provision_monthly_cents: int
    lowest_month: str
    lowest_cents: int
    status: str  # ok | tight | risk
    notes: tuple[str, ...] = field(default_factory=tuple)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{MONTHS_FR[int(m) - 1]} {y[2:]}"


def _monthly_equivalent(series: RecurringSeries) -> int:
    per_year = {"weekly": 52, "biweekly": 26, "monthly": 12}.get(series.cadence)
    return round(abs(series.amount_cents) * per_year / 12) if per_year else 0


def detected_spikes(
    series: Sequence[RecurringSeries], categories: Mapping[str, CategoryInfo], today: date, until: date
) -> list[Spike]:
    """Les sorties à cadence trimestrielle ou annuelle, posées à chacune de leurs échéances d'ici `until`."""
    out: list[Spike] = []
    for s in series:
        if s.direction != "out" or s.cadence not in ("quarterly", "yearly") or abs(s.amount_cents) < MIN_SPIKE_CENTS:
            continue
        info = categories.get(s.category)
        if info is not None and info.kind in ("transfer", "savings"):
            continue
        for day, _late in _occurrences(s, today, until):
            if day < today:
                continue
            out.append(
                Spike(
                    key=s.key,
                    label=s.merchant.title(),
                    amount_cents=abs(s.amount_cents),
                    day=day,
                    cadence=s.cadence,
                    source="detected",
                    category=s.category,
                    occurrences=s.occurrences,
                    confidence=s.confidence,
                )
            )
    return out


def custom_occurrences(custom: Sequence[CustomSpike], today: date, until: date) -> list[Spike]:
    out: list[Spike] = []
    for c in custom:
        d = c.next_date
        guard = 0
        while d < today and guard < 120:
            d = add_months(d, max(1, c.every_months))
            guard += 1
        while d <= until and guard < 240:
            out.append(Spike(c.key, c.label, c.amount_cents, d, "custom", "custom"))
            d = add_months(d, max(1, c.every_months))
            guard += 1
    return out


def build_year_ahead(
    *,
    today: date,
    start_balance_cents: int,
    series: Sequence[RecurringSeries],
    categories: Mapping[str, CategoryInfo],
    txns: Sequence[Txn],
    custom: Sequence[CustomSpike] = (),
    planned: Sequence[tuple[date, str, int]] = (),
    ignored: Sequence[str] = (),
    buffer_cents: int = 0,
    months: int = MONTHS,
) -> YearAhead:
    first = add_months(today.replace(day=1), 1)  # le mois en cours est à la prévision du mois
    until = add_months(first, months) - timedelta(days=1)
    # Même partage que la prévision du mois : une sortie récurrente est soit une charge planifiée (sortie du rythme
    # quotidien, comptée à part), soit une dépense variable (les courses du samedi restent dans le rythme).
    planned_out = [s for s in series if s.direction == "out" and is_planned_outflow(s, categories)]
    planned_merchants = {s.merchant for s in planned_out}
    variable = round(daily_variable_rate(txns, today, categories, planned_merchants) * MONTH_DAYS)
    income_monthly = sum(abs(s.amount_cents) for s in series if s.direction == "in" and s.cadence == "monthly")
    income_other = [s for s in series if s.direction == "in" and s.cadence != "monthly"]
    fixed_monthly = sum(_monthly_equivalent(s) for s in planned_out if s.cadence in ("weekly", "biweekly", "monthly"))
    skip = set(ignored)
    all_spikes = detected_spikes(series, categories, today, until) + custom_occurrences(custom, today, until)
    all_spikes += [
        Spike(f"planned:{d.isoformat()}:{c}", label, c, d, "planned", "planned")
        for d, label, c in planned
        if today <= d <= until
    ]
    kept = [s for s in all_spikes if s.key not in skip and s.day >= first]
    dropped = [s for s in all_spikes if s.key in skip and s.day >= first]
    kept.sort(key=lambda s: (s.day, s.key))
    by_month: dict[str, list[Spike]] = {}
    for s in kept:
        by_month.setdefault(_month_key(s.day), []).append(s)
    income_by_month: dict[str, int] = {}
    for inc in income_other:
        for day, _late in _occurrences(inc, today, until):
            if day >= first:
                k = _month_key(day)
                income_by_month[k] = income_by_month.get(k, 0) + abs(inc.amount_cents)
    points: list[MonthPoint] = []
    balance = start_balance_cents
    m = first
    for _ in range(months):
        key = _month_key(m)
        spikes_here = by_month.get(key, [])
        spikes_cents = sum(s.amount_cents for s in spikes_here)
        income = income_monthly + income_by_month.get(key, 0)
        balance = balance + income - fixed_monthly - variable - spikes_cents
        points.append(
            MonthPoint(
                key,
                month_label(key),
                income,
                fixed_monthly,
                variable,
                spikes_cents,
                tuple(f"{s.label} {s.amount_cents / 100:.0f} €" for s in spikes_here),
                balance,
            )
        )
        m = add_months(m, 1)
    yearly_spikes = sum(s.amount_cents for s in kept)
    provision = -(-yearly_spikes // max(1, months)) if yearly_spikes else 0
    lowest = min(points, key=lambda p: p.end_balance_cents) if points else None
    lowest_cents = lowest.end_balance_cents if lowest else start_balance_cents
    status = "risk" if lowest_cents < 0 else "tight" if lowest_cents < buffer_cents else "ok"
    notes: list[str] = []
    if not any(s.direction == "in" for s in series):
        notes.append("Aucun revenu régulier reconnu : la projection ne compte que les sorties.")
    if variable == 0:
        notes.append("Pas assez d'historique pour estimer tes dépenses variables : elles comptent pour zéro.")
    return YearAhead(
        start_month=_month_key(first),
        start_balance_cents=start_balance_cents,
        points=tuple(points),
        spikes=tuple(kept),
        ignored=tuple(dropped),
        income_monthly_cents=income_monthly,
        fixed_monthly_cents=fixed_monthly,
        variable_monthly_cents=variable,
        yearly_spikes_cents=yearly_spikes,
        provision_monthly_cents=provision,
        lowest_month=lowest.month if lowest else _month_key(first),
        lowest_cents=lowest_cents,
        status=status,
        notes=tuple(notes),
    )


def spike_to_dict(s: Spike) -> dict[str, Any]:
    return {
        "key": s.key,
        "label": s.label,
        "amount_cents": s.amount_cents,
        "day": s.day.isoformat(),
        "cadence": s.cadence,
        "source": s.source,
        "category": s.category,
        "occurrences": s.occurrences,
        "confidence": round(s.confidence, 2),
    }


def to_dict(y: YearAhead) -> dict[str, Any]:
    return {
        "start_month": y.start_month,
        "start_balance_cents": y.start_balance_cents,
        "points": [
            {
                "month": p.month,
                "label": p.label,
                "income_cents": p.income_cents,
                "fixed_cents": p.fixed_cents,
                "variable_cents": p.variable_cents,
                "spikes_cents": p.spikes_cents,
                "spikes": list(p.spikes),
                "end_balance_cents": p.end_balance_cents,
            }
            for p in y.points
        ],
        "spikes": [spike_to_dict(s) for s in y.spikes],
        "ignored": [spike_to_dict(s) for s in y.ignored],
        "income_monthly_cents": y.income_monthly_cents,
        "fixed_monthly_cents": y.fixed_monthly_cents,
        "variable_monthly_cents": y.variable_monthly_cents,
        "yearly_spikes_cents": y.yearly_spikes_cents,
        "provision_monthly_cents": y.provision_monthly_cents,
        "lowest_month": y.lowest_month,
        "lowest_label": month_label(y.lowest_month),
        "lowest_cents": y.lowest_cents,
        "status": y.status,
        "notes": list(y.notes),
    }
