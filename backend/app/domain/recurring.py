"""Détection des flux récurrents (loyer, salaire, abonnements, aides…) et prédiction de la prochaine échéance.

Approche robuste au bruit réel des relevés :
* regroupement par (marchand, sens) ;
* cadence choisie par vote sur les intervalles, en acceptant les échéances sautées (60 j = 2 x mensuel) ;
* montant typique = médiane, stabilité = écart absolu médian (MAD) relatif -> insensible aux valeurs aberrantes ;
* une série dont la dernière échéance est trop ancienne est considérée comme terminée.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from statistics import median


@dataclass(frozen=True)
class Txn:
    id: int
    date: date
    amount_cents: int
    merchant: str
    category: str = "a_categoriser"
    account_id: int = 0
    is_transfer: bool = False


@dataclass(frozen=True)
class Cadence:
    name: str
    days: float
    tolerance: float
    min_occurrences: int


CADENCES: tuple[Cadence, ...] = (
    Cadence("weekly", 7.0, 1.5, 4),
    Cadence("biweekly", 14.0, 2.5, 3),
    Cadence("monthly", 30.44, 4.5, 3),
    Cadence("quarterly", 91.31, 10.0, 3),
    Cadence("yearly", 365.25, 20.0, 2),
)


@dataclass(frozen=True)
class RecurringSeries:
    key: str
    merchant: str
    direction: str  # in | out
    cadence: str
    period_days: float
    amount_cents: int  # signé, montant typique (médiane)
    amount_min_cents: int
    amount_max_cents: int
    variable: bool
    occurrences: int
    last_date: date
    next_date: date
    day_of_month: int | None
    confidence: float
    category: str
    late: bool


def add_months(d: date, months: int, day: int | None = None) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day or d.day, last_day))


def _score_cadence(intervals: Sequence[int], cad: Cadence) -> float:
    """Part des intervalles compatibles avec la cadence (multiples entiers tolérés, jusqu'à 3)."""
    if not intervals:
        return 0.0
    ok = 0.0
    for gap in intervals:
        k = max(1, round(gap / cad.days))
        if k <= 3 and abs(gap - k * cad.days) <= cad.tolerance * k:
            ok += 1.0 if k == 1 else 0.6  # une échéance sautée pénalise un peu
    return ok / len(intervals)


def _mad_ratio(values: Sequence[int]) -> float:
    med = median(values)
    if med == 0:
        return 1.0
    return float(median(abs(v - med) for v in values)) / abs(med)


def _series_for(group: list[Txn], today: date) -> RecurringSeries | None:
    group.sort(key=lambda t: (t.date, t.id))
    # Plusieurs opérations le même jour (2 cafés) : on garde la plus grosse, le reste est du bruit.
    by_day: dict[date, Txn] = {}
    for t in group:
        if t.date not in by_day or abs(t.amount_cents) > abs(by_day[t.date].amount_cents):
            by_day[t.date] = t
    items = sorted(by_day.values(), key=lambda t: t.date)
    if len(items) < 2:
        return None
    intervals = [(b.date - a.date).days for a, b in pairwise(items)]
    best: tuple[float, Cadence] | None = None
    for cad in CADENCES:
        if len(items) < cad.min_occurrences:
            continue
        s = _score_cadence(intervals, cad)
        if s >= 0.75 and (best is None or s > best[0] + 1e-9):
            best = (s, cad)
    if best is None:
        return None
    score, cad = best

    # Sur les 6 dernières échéances seulement : un loyer augmenté il y a 2 ans ne doit pas peser.
    recent = items[-6:]
    amounts = [t.amount_cents for t in recent]
    spread = _mad_ratio(amounts)
    last = items[-1]
    day_of_month = None
    if cad.name in ("monthly", "quarterly", "yearly"):
        day_of_month = round(median(t.date.day for t in recent))
        months = {"monthly": 1, "quarterly": 3, "yearly": 12}[cad.name]
        next_date = add_months(last.date, months, day_of_month)
    else:
        next_date = last.date + timedelta(days=round(cad.days))

    # Série terminée si l'échéance attendue est dépassée de plus d'une demi-période + tolérance.
    if today > next_date + timedelta(days=cad.days / 2 + cad.tolerance):
        return None
    late = today > next_date + timedelta(days=max(3.0, cad.tolerance))
    occ_factor = min(1.0, len(items) / 6)
    confidence = round(score * (1 - min(spread, 0.5)) * (0.5 + 0.5 * occ_factor), 3)
    categories = [t.category for t in recent if t.category != "a_categoriser"]
    category = max(set(categories), key=categories.count) if categories else "a_categoriser"
    direction = "in" if last.amount_cents > 0 else "out"
    return RecurringSeries(
        key=f"{direction}:{last.merchant}",
        merchant=last.merchant,
        direction=direction,
        cadence=cad.name,
        period_days=cad.days,
        amount_cents=int(median(amounts)),
        amount_min_cents=min(amounts),
        amount_max_cents=max(amounts),
        # MAD robuste aux valeurs aberrantes + amplitude : une facture d'énergie à ±30 % est « variable ».
        variable=spread > 0.10 or (max(amounts) - min(amounts)) / max(1, abs(int(median(amounts)))) > 0.5,
        occurrences=len(items),
        last_date=last.date,
        next_date=next_date,
        day_of_month=day_of_month,
        confidence=confidence,
        category=category,
        late=late,
    )


def detect_recurring(txns: Iterable[Txn], today: date, min_confidence: float = 0.35) -> list[RecurringSeries]:
    groups: dict[tuple[str, bool], list[Txn]] = defaultdict(list)
    for t in txns:
        if t.is_transfer or not t.merchant or t.amount_cents == 0 or t.date > today:
            continue
        groups[(t.merchant, t.amount_cents > 0)].append(t)
    found = [s for g in groups.values() if (s := _series_for(g, today)) and s.confidence >= min_confidence]
    return sorted(found, key=lambda s: (s.next_date, -abs(s.amount_cents)))
