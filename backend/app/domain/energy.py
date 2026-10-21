"""Énergie : lecture tolérante des données Linky, coût estimé, écart avec les mensualités, pics.

Les relevés arrivent par Conso API (https://conso.boris.sh, passerelle open source vers Enedis). Le format de
réponse a changé fin 2026 (API Enedis « Mesure synchrone V2 ») : on lit donc l'ancien format, et pour le reste
toute structure où l'on trouve des couples (date, valeur) avec une unité d'énergie, plutôt qu'un schéma figé.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

PRM_RE = re.compile(r"\A[0-9]{14}\Z")  # chiffres ASCII seulement, pas de saut de ligne final toléré
MAX_WH_PER_DAY = 2_000_000  # 2 MWh/jour : au-delà, ce n'est pas un compteur de particulier mais une donnée corrompue
_MAX_DEPTH = 8  # profondeur explorée dans une réponse ; au-delà, ce n'est pas un format connu
_MAX_NODES = 50_000  # nœuds visités au plus (listes et dictionnaires) : borne le travail quelle que soit la réponse
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DATE_KEYS = ("date", "horodatage", "jour", "timestamp", "dateDebut", "date_debut", "start", "d")
_VALUE_KEYS = ("value", "valeur", "v", "energie", "energy", "conso", "consommation", "wh", "kwh")
_UNIT_KEYS = ("unit", "unite", "unité", "unite_mesure", "unitMesure")


@dataclass(frozen=True)
class DailyReading:
    day: date
    wh: int


class EnergyFormatError(ValueError):
    """Réponse sans série (date, valeur) reconnaissable."""


def _as_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


def _unit_factor(unit: Any) -> float | None:
    """Facteur vers des Wh. None : unité inconnue (on refuse de deviner entre W et Wh)."""
    if not isinstance(unit, str):
        return None
    u = unit.strip().lower()
    return {"wh": 1.0, "kwh": 1000.0, "mwh": 1_000_000.0}.get(u)


def _walk(root: Any) -> Iterable[Any]:
    """Parcours en largeur, borné en profondeur et en nombre de nœuds, chaque nœud visité une seule fois.

    Une réponse imbriquée à l'infini ou énorme s'arrête ici au lieu d'occuper le processus : une réponse
    piégée coûte au plus _MAX_NODES étapes.
    """
    queue: deque[tuple[Any, int]] = deque([(root, 0)])
    visited = 0
    while queue:
        node, depth = queue.popleft()
        visited += 1
        if visited > _MAX_NODES:
            raise EnergyFormatError("réponse trop volumineuse")
        yield node
        if depth >= _MAX_DEPTH:
            continue
        if isinstance(node, Mapping):
            # Les clés « attendues » d'abord : à profondeur égale, une série nommée l'emporte.
            first = [node[k] for k in _SERIES_KEYS if k in node]
            rest = [v for k, v in node.items() if k not in _SERIES_KEYS]
            queue.extend((v, depth + 1) for v in first + rest)
        elif isinstance(node, list):
            queue.extend((v, depth + 1) for v in node)


_SERIES_KEYS = ("interval_reading", "mesures", "points", "data", "valeurs", "values", "releves")


def _find_unit(payload: Any) -> float | None:
    """Cherche une unité d'énergie n'importe où dans la réponse (reading_type.unit, unite, …)."""
    for node in _walk(payload):
        if isinstance(node, Mapping):
            for key in _UNIT_KEYS:
                if key in node:
                    f = _unit_factor(node[key])
                    if f is not None:
                        return f
    return None


def _point(item: Mapping[str, Any]) -> tuple[date, float] | None:
    day = next((d for k in _DATE_KEYS if (d := _as_day(item.get(k))) is not None), None)
    if day is None:
        day = next((d for v in item.values() if (d := _as_day(v)) is not None), None)
    if day is None:
        return None
    value = next((n for k in _VALUE_KEYS if k in item and (n := _as_number(item[k])) is not None), None)
    if value is None:
        candidates = [n for k, v in item.items() if k not in _DATE_KEYS and (n := _as_number(v)) is not None]
        value = candidates[0] if len(candidates) == 1 else None
    return (day, value) if value is not None else None


def _series(payload: Any) -> list[tuple[date, float]]:
    """Première liste de dictionnaires (date, valeur) rencontrée, la moins profonde d'abord."""
    for node in _walk(payload):
        if isinstance(node, list) and node and all(isinstance(x, Mapping) for x in node):
            pts = [p for x in node if (p := _point(x)) is not None]
            if len(pts) >= max(1, len(node) // 2):
                return pts
    return []


def parse_daily(payload: Any) -> list[DailyReading]:
    """Relevés quotidiens en Wh, un par jour, triés. Lève EnergyFormatError si rien n'est reconnaissable."""
    points = _series(payload)
    if not points:
        raise EnergyFormatError("aucune série (date, valeur) dans la réponse")
    factor = _find_unit(payload)
    if factor is None:
        # Ancien format Enedis : consommation quotidienne en Wh, valeurs entières à 5 chiffres ou plus.
        factor = 1.0
    by_day: dict[date, float] = {}
    for day, value in points:
        by_day[day] = value * factor  # une valeur par jour : la dernière lue l'emporte
    # Finie, positive et plausible : « Infinity », NaN, négatifs ou 10 GWh dans la journée sont écartés.
    return [
        DailyReading(d, round(v)) for d, v in sorted(by_day.items()) if math.isfinite(v) and 0 <= v <= MAX_WH_PER_DAY
    ]


# ------------------------------------------------------------------ coût et tendances
@dataclass(frozen=True)
class Tariff:
    cents_per_100kwh: int  # 2001 = 0,2001 €/kWh
    yearly_subscription_cents: int
    label: str = ""


def energy_cost_cents(wh: int, tariff: Tariff) -> int:
    return round(wh * tariff.cents_per_100kwh / 100_000)


def period_cost_cents(wh: int, days: int, tariff: Tariff) -> int:
    return energy_cost_cents(wh, tariff) + round(tariff.yearly_subscription_cents * days / 365)


@dataclass(frozen=True)
class MonthEnergy:
    month: str
    wh: int
    days: int
    cost_cents: int


@dataclass(frozen=True)
class EnergyInsights:
    data_from: date | None
    data_through: date | None
    days_of_data: int
    stale: bool
    last_30_days_wh: int
    last_30_days_cost_cents: int
    this_month_wh: int
    this_month_cost_cents: int
    months: list[MonthEnergy]
    same_month_last_year_wh: int | None
    change_vs_last_year_percent: int | None
    yearly_estimate_cents: int
    yearly_estimate_wh: int
    yearly_is_extrapolated: bool
    recommended_monthly_cents: int
    monthly_payment_cents: int | None
    regularisation_cents: int | None  # > 0 : rattrapage probable ; < 0 : remboursement probable
    spike_ratio: float | None
    spike: bool
    daily_average_wh: int


def monthly_breakdown(readings: Sequence[DailyReading], tariff: Tariff) -> list[MonthEnergy]:
    wh: dict[str, int] = defaultdict(int)
    days: dict[str, int] = defaultdict(int)
    for r in readings:
        key = f"{r.day.year:04d}-{r.day.month:02d}"
        wh[key] += r.wh
        days[key] += 1
    return [MonthEnergy(m, wh[m], days[m], period_cost_cents(wh[m], days[m], tariff)) for m in sorted(wh)]


def insights(
    readings: Iterable[DailyReading],
    *,
    today: date,
    tariff: Tariff,
    monthly_payment_cents: int | None,
) -> EnergyInsights:
    rows = sorted((r for r in readings if r.day <= today), key=lambda r: r.day)  # un relevé futur n'existe pas
    if not rows:
        return EnergyInsights(
            None, None, 0, True, 0, 0, 0, 0, [], None, None, 0, 0, True, 0, monthly_payment_cents, None, None, False, 0
        )
    by_day = {r.day: r.wh for r in rows}
    first, last = rows[0].day, rows[-1].day
    stale = (today - last).days > 7

    def window(start: date, end: date) -> tuple[int, int]:
        total = days = 0
        d = start
        while d <= end:
            if d in by_day:
                total += by_day[d]
                days += 1
            d += timedelta(days=1)
        return total, days

    last30_wh, last30_days = window(last - timedelta(days=29), last)
    month_start = today.replace(day=1)
    this_wh, this_days = window(month_start, last)
    months = monthly_breakdown(rows, tariff)
    daily_avg = round(sum(r.wh for r in rows[-90:]) / min(len(rows), 90))

    # Même mois l'an dernier : on compare le dernier mois complet connu.
    prev_month_end = month_start - timedelta(days=1)
    key_prev = f"{prev_month_end.year:04d}-{prev_month_end.month:02d}"
    key_prev_year = f"{prev_month_end.year - 1:04d}-{prev_month_end.month:02d}"
    by_month = {m.month: m for m in months}
    last_year = by_month.get(key_prev_year)
    this_prev = by_month.get(key_prev)
    change = None
    if last_year and this_prev and last_year.days >= 25 and this_prev.days >= 25 and last_year.wh > 0:
        change = round(100 * (this_prev.wh - last_year.wh) / last_year.wh)

    year_wh, year_days = window(last - timedelta(days=364), last)
    extrapolated = year_days < 330
    if extrapolated:
        year_wh = daily_avg * 365
    yearly_cost = period_cost_cents(year_wh, 365, tariff)
    recommended = -(-yearly_cost // 12)
    regularisation = yearly_cost - 12 * monthly_payment_cents if monthly_payment_cents else None

    recent_wh, recent_days = window(last - timedelta(days=6), last)
    base_wh, base_days = window(last - timedelta(days=34), last - timedelta(days=7))
    ratio = None
    spike = False
    if recent_days >= 5 and base_days >= 20:
        recent_avg, base_avg = recent_wh / recent_days, base_wh / base_days
        ratio = round(recent_avg / base_avg, 2) if base_avg > 0 else None
        spike = ratio is not None and ratio >= 1.4 and recent_avg - base_avg >= 2_000

    return EnergyInsights(
        first,
        last,
        len(rows),
        stale,
        last30_wh,
        period_cost_cents(last30_wh, last30_days, tariff),
        this_wh,
        period_cost_cents(this_wh, this_days, tariff),
        months[-13:],
        last_year.wh if last_year else None,
        change,
        yearly_cost,
        year_wh,
        extrapolated,
        recommended,
        monthly_payment_cents,
        regularisation,
        ratio,
        spike,
        daily_avg,
    )


ELECTRICITY_SUPPLIERS = (
    "EDF",
    "ENGIE",
    "TOTALENERGIES",
    "TOTAL ENERGIES",
    "DIRECT ENERGIE",
    "OHM ENERGIE",
    "EKWATEUR",
    "ILEK",
    "ENERCOOP",
    "PLANETE OUI",
    "MINT ENERGIE",
    "VATTENFALL",
    "ALPIQ",
    "PRIMEO",
    "ENI ",
    "OCTOPUS",
    "WEKIWI",
    "HAPPE",
    "ALTERNA",
    "ELMY",
    "LA BELLE ENERGIE",
    "SOWEE",
    "IBERDROLA",
    "ENDESA",
    "GEG",
    "ES ENERGIES",
    "PROXELIA",
    "URBAN SOLAR",
)


def is_electricity_supplier(merchant: str) -> bool:
    m = f" {merchant.upper()} "
    return any(f" {s.strip()} " in m or m.strip().startswith(s.strip()) for s in ELECTRICITY_SUPPLIERS)


def typical_monthly_payment(amounts_cents: Sequence[int]) -> int | None:
    """Mensualité typique à partir des prélèvements du fournisseur (médiane des montants sortants)."""
    outs = [-a for a in amounts_cents if a < 0]
    return round(median(outs)) if outs else None
