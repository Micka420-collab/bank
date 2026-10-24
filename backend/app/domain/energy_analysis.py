"""Analyse de l'énergie (#33, suite) : consommation corrigée du climat, simulation Base / Heures creuses / Tempo sur
ta vraie consommation, et prix repère du gaz.

Climat : degrés-jours de chauffe en base 18 °C (pour chaque jour, 18 °C moins la température moyenne du jour quand
elle est plus basse, sinon 0), à partir de la température moyenne journalière de l'archive Open-Meteo aux
coordonnées de ta commune. Ta sensibilité au froid (Wh de plus par degré-jour) est mesurée sur tes douze derniers
mois de relevés (régression linéaire jour par jour) ; la consommation d'un mois est ramenée au climat du même mois
de l'an dernier avant d'être comparée.

Simulation : chaque option est chiffrée sur tes relevés des douze derniers mois aux prix de la grille de la CRE en
vigueur. Avec la courbe de charge (pas de 30 min), le partage heures pleines / creuses de chaque jour est exact ;
sans elle, c'est une estimation dont l'hypothèse est écrite à côté du résultat. Module pur : les entrées sont déjà
lues.
"""

from __future__ import annotations

import calendar
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from app.domain.energy import DailyReading
from app.domain.energy_signals import KWH_PARTS, OPTIONS, GridPower, SignalFormatError, fr_date
from app.domain.money import format_eur

# ------------------------------------------------------------------ météo : Open-Meteo
METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
METEO_SOURCE = "Open-Meteo, archive météo (température moyenne journalière, licence CC BY 4.0)"
METEO_TERMS_URL = "https://open-meteo.com/en/terms"
HDD_BASE_C = 18.0  # base des degrés-jours de chauffe : au-dessus de 18 °C de moyenne, un logement ne chauffe pas
CELL_DECIMALS = 1  # coordonnées arrondies au dixième de degré (environ 10 km) avant de partir
MAX_METEO_DAYS = 1_000


def meteo_cell(lat: Any, lon: Any) -> tuple[float, float]:
    """Les coordonnées envoyées à Open-Meteo : celles de ta commune, arrondies au dixième de degré."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError) as exc:
        raise SignalFormatError("coordonnées absentes") from exc
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        raise SignalFormatError("coordonnées hors limites")
    return round(la, CELL_DECIMALS), round(lo, CELL_DECIMALS)


def meteo_url(cell: tuple[float, float], start: date, end: date) -> str:
    query = {
        "latitude": f"{cell[0]:.1f}",
        "longitude": f"{cell[1]:.1f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_mean",
        "timezone": "Europe/Paris",
    }
    return f"{METEO_URL}?{urlencode(query)}"


def parse_meteo(body: bytes) -> dict[date, float]:
    """Température moyenne de chaque jour (°C). Un jour sans valeur (null : l'archive n'a pas encore le jour) est
    ignoré, une valeur hors de toute plausibilité aussi ; une réponse sans série est refusée."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise SignalFormatError("réponse météo illisible (JSON attendu)") from exc
    if not isinstance(data, Mapping):
        raise SignalFormatError("réponse météo inattendue")
    if data.get("error"):
        raise SignalFormatError(f"Open-Meteo refuse la demande ({str(data.get('reason'))[:80]})")
    daily = data.get("daily")
    if not isinstance(daily, Mapping):
        raise SignalFormatError("champ « daily » absent")
    if str((data.get("daily_units") or {}).get("temperature_2m_mean") or "°C") != "°C":
        raise SignalFormatError("unité de température inattendue")
    days, temps = daily.get("time"), daily.get("temperature_2m_mean")
    if not isinstance(days, list) or not isinstance(temps, list) or len(days) != len(temps):
        raise SignalFormatError("série de températures absente")
    if len(days) > MAX_METEO_DAYS:
        raise SignalFormatError("réponse météo trop longue")
    out: dict[date, float] = {}
    for raw_day, raw_t in zip(days, temps, strict=True):
        if raw_t is None or isinstance(raw_t, bool) or not isinstance(raw_t, int | float):
            continue
        try:
            day = date.fromisoformat(str(raw_day)[:10])
        except ValueError:
            continue
        try:
            t = float(raw_t)  # un entier JSON de 400 chiffres ne tient pas dans un float : OverflowError
        except (OverflowError, ValueError):
            continue
        if math.isfinite(t) and -60 <= t <= 60:
            out[day] = t
    return out


def hdd(temp_c: float) -> float:
    """Degrés-jours de chauffe d'un jour, base 18 °C."""
    return max(0.0, HDD_BASE_C - temp_c)


# ------------------------------------------------------------------ consommation corrigée du climat
DRIFT_PCT = 10  # au-delà de +10 % à climat égal sur trois mois, Bank le signale
DRIFT_MIN_KWH = 30  # … et seulement si l'écart dépasse 30 kWh (un petit logement l'été bouge vite en %)
WINDOW_MONTHS = 3  # la comparaison porte sur les trois derniers mois complets (un mois seul est trop bruité)
MIN_FIT_DAYS = 90  # jours relevés avec leur température pour mesurer ta sensibilité au froid
MIN_R2 = 0.2  # en dessous, ta consommation ne suit pas la température : aucune correction
MIN_HDD = 10.0  # moins de 10 degrés-jours sur la période l'an dernier : pas de « % plus froid » qui ait un sens
MONTH_SLACK = 3  # un mois compte s'il lui manque au plus 3 jours de relevés
MONTHS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
    "décembre",
)  # fmt: skip


@dataclass(frozen=True)
class ClimateMonth:
    month: str  # AAAA-MM
    kwh: float
    prev_kwh: float
    hdd: float
    prev_hdd: float


@dataclass(frozen=True)
class ClimateCheck:
    status: str  # drift | steady | lower | no_weather | too_few
    message: str
    months: tuple[ClimateMonth, ...] = ()
    wh_per_hdd: float | None = None  # ta sensibilité au froid, mesurée (None : non mesurable)
    r2: float | None = None
    fit_days: int = 0
    raw_pct: int | None = None
    cold_pct: int | None = None  # > 0 : plus froid que l'an dernier
    corrected_pct: int | None = None
    extra_kwh: int | None = None  # écart à climat égal sur la période, en kWh
    period: str | None = None  # « d'août à octobre 2026 »


def _pct(value: float) -> str:
    return f"{value:+.0f} %".replace("-", "−")


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    n = year * 12 + month - 1 + delta
    return n // 12, n % 12 + 1


def _fit(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Pente (Wh par degré-jour) et r² de la droite des moindres carrés ; None si la température n'a pas varié."""
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    syy = sum((y - my) ** 2 for _, y in points)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in points)
    return sxy / sxx, (sxy * sxy) / (sxx * syy)


def _month_stats(
    year: int, month: int, wh: Mapping[date, int], temps: Mapping[date, float]
) -> tuple[float, float] | None:
    """kWh et degrés-jours d'un mois, ramenés au mois entier à partir des jours relevés (au plus 3 jours manquants)."""
    dim = calendar.monthrange(year, month)[1]
    days = [date(year, month, i) for i in range(1, dim + 1)]
    both = [d for d in days if d in wh and d in temps]
    if len(both) < dim - MONTH_SLACK:
        return None
    scale = dim / len(both)
    return sum(wh[d] for d in both) / 1000 * scale, sum(hdd(temps[d]) for d in both) * scale


def _period_label(first: str, last: str) -> str:
    fy, fm = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    if first == last:
        return f"en {MONTHS_FR[fm - 1]} {fy}"
    left = MONTHS_FR[fm - 1] + ("" if fy == ly else f" {fy}")
    de = "d'" if left[0] in "aeiouéo" else "de "
    return f"{de}{left} à {MONTHS_FR[lm - 1]} {ly}"


def climate_check(readings: Sequence[DailyReading], temps: Mapping[date, float], *, today: date) -> ClimateCheck:
    """Les trois derniers mois complets face aux mêmes mois de l'an dernier, à climat égal. Chaque pourcentage
    vient de tes relevés Linky et des températures d'Open-Meteo ; ce qui manque est dit."""
    if not temps:
        return ClimateCheck(
            "no_weather",
            "Pas encore de températures pour ta commune : la comparaison à climat égal arrive avec la première relève "
            "météo.",
        )
    wh = {r.day: r.wh for r in readings}
    year_start = today - timedelta(days=365)
    points = [(hdd(temps[d]), float(w)) for d, w in wh.items() if year_start <= d < today and d in temps]
    fitted = _fit(points) if len(points) >= MIN_FIT_DAYS else None
    slope = fitted[0] if fitted and fitted[0] > 0 and fitted[1] >= MIN_R2 else 0.0
    r2 = round(fitted[1], 2) if fitted else None
    enough = len(points) >= MIN_FIT_DAYS
    base = ClimateCheck("", "", wh_per_hdd=round(slope) if enough else None, r2=r2, fit_days=len(points))
    if not enough:
        return replace(
            base,
            status="too_few",
            message=f"Bank a {len(points)} jours de consommation avec leur température sur les douze derniers mois ; "
            f"il en faut {MIN_FIT_DAYS} pour mesurer l'effet du froid sur ta consommation.",
        )
    months: list[ClimateMonth] = []
    y, m = _shift_month(today.year, today.month, -1)
    for _ in range(12):
        cur, prev = _month_stats(y, m, wh, temps), _month_stats(y - 1, m, wh, temps)
        if cur and prev:
            months.append(ClimateMonth(f"{y:04d}-{m:02d}", round(cur[0], 1), round(prev[0], 1), cur[1], prev[1]))
        y, m = _shift_month(y, m, -1)
    months.reverse()
    wanted = [_month_key(date(*_shift_month(today.year, today.month, -i), 1)) for i in range(WINDOW_MONTHS, 0, -1)]
    window = [mo for mo in months if mo.month in wanted]
    if len(window) < WINDOW_MONTHS:
        return replace(
            base,
            status="too_few",
            months=tuple(months),
            message=f"Il faut les {WINDOW_MONTHS} derniers mois et les mêmes mois de l'an dernier, presque complets "
            f"(au plus {MONTH_SLACK} jours manquants chacun), pour comparer à climat égal : Bank ne les a pas encore.",
        )
    c, cp = sum(mo.kwh for mo in window), sum(mo.prev_kwh for mo in window)
    h, hp = sum(mo.hdd for mo in window), sum(mo.prev_hdd for mo in window)
    if cp <= 0:
        return replace(base, status="too_few", months=tuple(months), message="Consommation nulle l'an dernier.")
    adjusted = c - slope * (h - hp) / 1000
    raw_pct, corrected_pct = (c / cp - 1) * 100, (adjusted / cp - 1) * 100
    # Un % de degrés-jours n'a de sens que si l'an dernier en comptait assez ; sinon les deux nombres sont donnés.
    cold_pct = (h / hp - 1) * 100 if hp >= MIN_HDD else None
    period = _period_label(window[0].month, window[-1].month)
    head = f"{period.capitalize()} : {_pct(raw_pct)} sur un an ({round(c)} kWh contre {round(cp)})"
    if slope == 0:
        why = (
            "ta consommation ne varie pas avec la température sur l'année"
            if fitted is not None and r2 is not None and r2 < MIN_R2
            else "ta consommation n'augmente pas quand il fait froid"
        )
        climate = f", et {why} : à climat égal, l'écart reste {_pct(corrected_pct)}"
    elif cold_pct is None and h < MIN_HDD:
        climate = (
            f" ; il n'a presque pas fallu chauffer ({round(h)} degrés-jours de chauffe contre {round(hp)}) : à climat "
            f"égal, {_pct(corrected_pct)}"
        )
    elif cold_pct is None:
        climate = f" ; {round(h)} degrés-jours de chauffe contre {round(hp)} : à climat égal, {_pct(corrected_pct)}"
    else:
        more = "en plus" if cold_pct > 0 else "en moins"
        climate = (
            f", {'mais' if (cold_pct > 0) == (raw_pct > 0) else 'et'} avec {abs(round(cold_pct))} % de degrés-jours "
            f"de chauffe {more} ({round(h)} contre {round(hp)}) : à climat égal, {_pct(corrected_pct)}"
        )
    extra = round(adjusted - cp)
    out = replace(
        base,
        months=tuple(months),
        raw_pct=round(raw_pct),
        cold_pct=None if cold_pct is None else round(cold_pct),
        corrected_pct=round(corrected_pct),
        extra_kwh=extra,
        period=period,
    )
    if corrected_pct >= DRIFT_PCT and extra >= DRIFT_MIN_KWH:
        return replace(
            out,
            status="drift",
            message=f"{head}{climate}, soit {extra} kWh de plus. Au-delà de +{DRIFT_PCT} % à climat "
            "égal, Bank le signale : un appareil qui consomme davantage (chauffe-eau, radiateur, congélateur) peut "
            "l'expliquer.",
        )
    return replace(out, status="lower" if corrected_pct <= -DRIFT_PCT else "steady", message=f"{head}{climate}.")


# ------------------------------------------------------------------ courbe de charge (pas de 30 min)
SLOTS = 48  # demi-heures d'une journée ; le créneau i couvre [i × 30 min, (i + 1) × 30 min[
MAX_CURVE_POINTS = 48 * 8 + 10
MAX_SLOT_WH = 50_000  # 100 kW pendant 30 min : au-delà, ce n'est pas un logement


def parse_load_curve(payload: Any) -> dict[date, list[int | None]]:
    """Énergie de chaque demi-heure (Wh), par jour civil. Format Enedis V2 : `grandeur[].points[] = {v, d, p}` où `v`
    est la puissance moyenne (W) de l'intervalle qui se termine à `d` (« 2026-04-01 00:30:00 » couvre 00:00-00:30) et
    `p` sa durée (PT30M). Une demi-heure sans mesure reste None ; un pas autre que 30 min est refusé."""
    if not isinstance(payload, Mapping):
        raise SignalFormatError("réponse inattendue (objet JSON attendu)")
    grandeurs = payload.get("grandeur")
    if not isinstance(grandeurs, list):
        raise SignalFormatError("champ « grandeur » absent")
    out: dict[date, list[int | None]] = {}
    seen = 0
    for g in grandeurs:
        if not isinstance(g, Mapping) or str(g.get("grandeurPhysique") or "PA").upper() != "PA":
            continue
        if str(g.get("unite") or "").strip().upper() != "W":
            raise SignalFormatError(f"unité inconnue ({str(g.get('unite'))[:10]!r})")
        points = g.get("points")
        if not isinstance(points, list):
            raise SignalFormatError("champ « points » absent")
        for p in points:
            seen += 1
            if seen > MAX_CURVE_POINTS:
                raise SignalFormatError("réponse trop volumineuse")
            if not isinstance(p, Mapping) or p.get("v") is None:
                continue
            if str(p.get("p") or "PT30M").upper() != "PT30M":
                raise SignalFormatError(f"pas de mesure inattendu ({str(p.get('p'))[:8]!r})")
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?", str(p.get("d") or "").strip())
            if not m:
                continue
            try:
                end_day = date.fromisoformat(m[1])
                watts = float(str(p["v"]).replace(",", "."))
            except ValueError:
                continue
            end_min = int(m[2]) * 60 + int(m[3])
            if end_min % 30 or not math.isfinite(watts) or watts < 0 or end_min > 24 * 60:
                continue
            start = end_min - 30  # l'intervalle qui se termine à minuit commence la veille à 23:30
            day, slot = (end_day - timedelta(days=1), SLOTS - 1) if start < 0 else (end_day, start // 30)
            wh = round(watts / 2)
            if wh > MAX_SLOT_WH:
                continue
            out.setdefault(day, [None] * SLOTS)[slot] = wh
    return out


def complete(slots: Sequence[int | None] | None) -> bool:
    return slots is not None and len(slots) == SLOTS and all(s is not None for s in slots)


# ------------------------------------------------------------------ heures creuses
def parse_hc_ranges(text: str) -> list[tuple[int, int]]:
    """« 22:00-06:00 » ou « 01:30-07:30, 12:30-14:30 » → créneaux [début, fin[ en demi-heures. Les heures creuses
    d'un compteur sont fixées par Enedis à la demi-heure : elles sont écrites sur ta facture."""
    ranges: list[tuple[int, int]] = []
    total = 0
    for part in [p.strip() for p in re.split(r"[,;]", text) if p.strip()]:
        m = re.fullmatch(r"(\d{1,2})[:h](\d{2})\s*[-–à]\s*(\d{1,2})[:h](\d{2})", part)
        if not m:
            raise ValueError(f"Créneau illisible : « {part[:20]} » (écris par exemple 22:00-06:00)")
        a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
        if a % 30 or b % 30 or a >= 24 * 60 or b > 24 * 60 or a == b:
            raise ValueError("Les heures creuses commencent et finissent à l'heure ou à la demi-heure")
        start, end = a // 30, b // 30
        ranges.append((start, end))
        total += (end - start) % SLOTS
    if not ranges:
        raise ValueError("Aucun créneau d'heures creuses")
    if total > 24:
        raise ValueError("Plus de 12 heures creuses par jour : vérifie ta facture")
    return ranges


def hc_slots(ranges: Sequence[tuple[int, int]]) -> frozenset[int]:
    out: set[int] = set()
    for start, end in ranges:
        i = start
        while i != end:
            out.add(i)
            i = (i + 1) % SLOTS
    return frozenset(out)


# ------------------------------------------------------------------ simulation des options
# Hypothèse de l'estimation sans courbe : 35 % de la consommation en heures creuses, soit les 65 % d'heures pleines
# que Bank applique déjà à l'option Heures creuses (rates.json, « moyenne 65 % HP »). Le seuil de bascule calculé à
# côté dit à partir de quelle part l'option devient moins chère, pour juger soi-même de l'hypothèse.
HC_SHARE = Decimal("0.35")
TEMPO_HP_SLOTS = range(12, 44)  # Tempo : heures pleines de 6 h à 22 h, heures creuses de 22 h à 6 h le lendemain
TEMPO_HOURS_URL = "https://particulier.edf.fr/fr/accueil/gestion-contrat/options/tempo/details.html"
SIM_MAX_MISSING_DAYS = 31


@dataclass(frozen=True)
class OptionCost:
    option: str
    label: str
    total_cents: int | None
    subscription_cents: int | None
    energy_cents: int | None
    missing: str | None = None  # pourquoi l'option n'est pas chiffrée


@dataclass(frozen=True)
class Simulation:
    status: str  # ok | no_contract | too_few | no_grid
    message: str
    kva: int | None = None
    mode: str | None = None  # estimation | exacte | en partie exacte
    days: int = 0
    exact_days: int = 0
    kwh: int | None = None
    since: date | None = None
    options: tuple[OptionCost, ...] = ()
    assumptions: tuple[str, ...] = ()
    grid_as_of: str | None = None
    grid_url: str | None = None
    hc_breakeven_pct: int | None = None
    red_days: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


class _Shares:
    """Part de la consommation d'un jour dans des demi-heures données, d'après sa courbe de charge complète."""

    def __init__(self, slots: Sequence[int | None]) -> None:
        self.slots = [s or 0 for s in slots]
        self.total = Decimal(sum(self.slots))

    def of(self, idx: Iterable[int]) -> Decimal:
        return Decimal(sum(self.slots[i] for i in idx)) / self.total


def _whole(cents: int) -> str:
    """Un total annuel à l'euro près : « 1 234 € »."""
    return format_eur(round(cents / 100) * 100).replace(",00\u00a0€", "\u00a0€")


def _price(g: GridPower, part: str) -> Decimal:
    return g.kwh[part]


def _cents(euros: Decimal) -> int:
    return int((euros * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def simulate(
    readings: Sequence[DailyReading],
    curve: Mapping[date, Sequence[int | None]],
    colors: Mapping[date, str],
    *,
    today: date,
    kva: int | None,
    grids: Mapping[str, Mapping[int, GridPower]],
    hc_ranges: Sequence[tuple[int, int]] | None,
    tempo_colors_known: bool,
) -> Simulation:
    """Ce que chaque option t'aurait coûté sur tes douze derniers mois de relevés, abonnement compris, aux prix de la
    grille de la CRE en vigueur. Jour par jour : exact quand la courbe de 30 min du jour est complète, estimé sinon
    (hypothèses écrites avec le résultat)."""
    if kva is None:
        return Simulation(
            "no_contract", "Indique ta puissance souscrite (sur ta facture) : la simulation se fait à cette puissance."
        )
    start = today - timedelta(days=365)
    year = [r for r in readings if start <= r.day < today]
    if 365 - len(year) > SIM_MAX_MISSING_DAYS:
        return Simulation(
            "too_few",
            f"Bank a {len(year)} jours de consommation sur les douze derniers mois ; il en faut au moins "
            f"{365 - SIM_MAX_MISSING_DAYS} pour comparer les options sur une année entière.",
            kva=kva,
            days=len(year),
        )
    hc = hc_slots(hc_ranges) if hc_ranges else None
    exact_hphc = exact_tempo = 0
    energy: dict[str, Decimal] = {o: Decimal(0) for o in OPTIONS}
    missing: dict[str, str] = {}
    grid_here = {o: grids.get(o, {}).get(kva) for o in OPTIONS}
    for o, g in grid_here.items():
        if g is None:
            missing[o] = f"grille de la CRE de l'option {OPTIONS[o]} en {kva} kVA non relevée"
        elif any(p not in g.kwh for p in KWH_PARTS[o]):
            missing[o] = "prix du kWh incomplet dans la grille"
    if "tempo" not in missing and not tempo_colors_known:
        missing["tempo"] = "couleurs Tempo de l'année inconnues : active « Couleur Tempo »"
    no_color = 0
    red_days = 0
    for r in year:
        kwh = Decimal(r.wh) / 1000
        slots = curve.get(r.day)
        exact_day = slots is not None and complete(slots) and sum(s or 0 for s in slots) > 0
        share = _Shares(slots) if exact_day and slots is not None else None
        g = grid_here["base"]
        if g is not None and "base" not in missing:
            energy["base"] += kwh * _price(g, "kwh_ttc")
        g = grid_here["hphc"]
        if g is not None and "hphc" not in missing:
            if share is not None and hc is not None:
                s_hc = share.of(hc)
                exact_hphc += 1
            else:
                s_hc = HC_SHARE
            energy["hphc"] += kwh * (s_hc * _price(g, "hc_ttc") + (1 - s_hc) * _price(g, "hp_ttc"))
        g = grid_here["tempo"]
        if g is not None and "tempo" not in missing:
            color, before = colors.get(r.day), colors.get(r.day - timedelta(days=1))
            if color is None:
                no_color += 1
                continue
            red_days += color == "rouge"
            if share is not None and before is not None:
                # 0 h-6 h : heures creuses du jour Tempo de la veille ; 6 h-22 h pleines, 22 h-24 h creuses du jour.
                night, day_hp, evening = share.of(range(12)), share.of(TEMPO_HP_SLOTS), share.of(range(44, 48))
                cost = (
                    night * _price(g, f"{before}_hc_ttc")
                    + day_hp * _price(g, f"{color}_hp_ttc")
                    + evening * _price(g, f"{color}_hc_ttc")
                )
                exact_tempo += 1
            else:
                cost = HC_SHARE * _price(g, f"{color}_hc_ttc") + (1 - HC_SHARE) * _price(g, f"{color}_hp_ttc")
            energy["tempo"] += kwh * cost
    if no_color and "tempo" not in missing:
        missing["tempo"] = f"couleur Tempo inconnue pour {no_color} jour{'s' if no_color > 1 else ''} de l'année"
    options: list[OptionCost] = []
    for o, label in OPTIONS.items():
        g = grid_here[o]
        if o in missing or g is None:
            options.append(OptionCost(o, label, None, None, None, missing.get(o)))
            continue
        options.append(OptionCost(o, label, _cents(g.fixe + energy[o]), _cents(g.fixe), _cents(energy[o])))
    priced = [x for x in options if x.total_cents is not None]
    total_kwh = round(sum(r.wh for r in year) / 1000)
    if not priced:
        return Simulation(
            "no_grid",
            f"Bank n'a pas la grille de la CRE en {kva} kVA pour chiffrer les options : active « Barèmes tenus à "
            "jour » (Réglages › Barèmes).",
            kva=kva,
            days=len(year),
            options=tuple(options),
        )
    days = len(year)
    # Base est toujours exacte (un seul prix) ; les options à plusieurs prix le sont jour par jour.
    counts = [n for o, n in (("hphc", exact_hphc), ("tempo", exact_tempo)) if o not in missing]
    exact = min(counts) if counts else days
    mode = "exacte" if exact == days else ("en partie exacte" if any(counts) else "estimation")
    assumptions: list[str] = []
    if exact_hphc < days and "hphc" not in missing:
        assumptions.append(
            f"Heures creuses : 35 % de ta consommation supposée en heures creuses (les 65 % d'heures pleines de la "
            f"ligne Heures creuses de Bank) sur {days - exact_hphc} jour"
            f"{'s' if days - exact_hphc > 1 else ''}"
            + (
                ""
                if hc is not None
                else " ; indique tes heures creuses (sur ta facture) pour un calcul exact avec la courbe de charge"
            )
        )
    if exact_tempo < days and "tempo" not in missing:
        assumptions.append(
            f"Tempo : même part de 35 % en heures creuses (22 h-6 h) sur {days - exact_tempo} jour"
            f"{'s' if days - exact_tempo > 1 else ''}, et chaque jour compté de minuit à minuit alors qu'un jour "
            "Tempo va de 6 h à 6 h"
        )
    if "tempo" not in missing:
        assumptions.append("Tempo : tes habitudes inchangées les jours rouges (aucun report de consommation)")
    as_of = max(g.as_of for o, g in grid_here.items() if g is not None and o not in missing)
    priced_grids = [g for o, g in grid_here.items() if g is not None and o not in missing]
    url = next((g.source_url for g in priced_grids if g.source_url), None)
    breakeven = _breakeven(year, grid_here, missing) if exact_hphc == 0 else None
    ordered = sorted(priced, key=lambda x: x.total_cents or 0)
    parts = ", ".join(f"{x.label} {_whole(x.total_cents or 0)}" for x in ordered)
    kwh_text = f"{total_kwh:,} kWh".replace(",", "\u202f")
    coverage = f"{days} jours relevés, {kwh_text}" if days < 365 else kwh_text
    label = "estimation" if mode == "estimation" else f"calcul {mode}"
    message = (
        f"Sur tes 12 derniers mois ({coverage}), aux prix de la grille de la CRE du {_fr_iso(as_of)} en {kva} kVA, "
        f"abonnement compris ({label}) : {parts}."
    )
    if len(ordered) >= 2:
        gap = (ordered[1].total_cents or 0) - (ordered[0].total_cents or 0)
        message += f" L'écart entre les deux premières est de {_whole(gap)} sur l'année."
    if "tempo" not in missing:
        # Les jours rouges de l'année se comptent sur les couleurs, pas sur les seuls jours relevés par le compteur.
        known = [c for d, c in colors.items() if start <= d < today]
        if len(known) >= 365:
            red_days = sum(c == "rouge" for c in known)
            message += f" L'année comptait {red_days} jours rouges."
        else:
            message += f" Tes {days} jours relevés comptaient {red_days} jours rouges."
    notes = tuple(f"{OPTIONS[o]} non chiffrée : {why}." for o, why in missing.items())
    return Simulation(
        "ok",
        message,
        kva=kva,
        mode=mode,
        days=days,
        exact_days=exact,
        kwh=total_kwh,
        since=year[0].day if year else None,
        options=tuple(options),
        assumptions=tuple(assumptions),
        grid_as_of=as_of,
        grid_url=url,
        hc_breakeven_pct=breakeven,
        red_days=red_days if "tempo" not in missing else None,
        notes=notes,
    )


def _breakeven(
    year: Sequence[DailyReading], grid: Mapping[str, GridPower | None], missing: Mapping[str, str]
) -> int | None:
    """Part des heures creuses (en %) à partir de laquelle l'option Heures creuses coûte moins que Base sur ta
    consommation de l'année ; None si l'une des deux n'est pas chiffrée ou si la bascule tombe hors de 0-100 %."""
    base, hphc = grid.get("base"), grid.get("hphc")
    if base is None or hphc is None or "base" in missing or "hphc" in missing:
        return None
    kwh = Decimal(sum(r.wh for r in year)) / 1000
    hp, hc = _price(hphc, "hp_ttc"), _price(hphc, "hc_ttc")
    if kwh <= 0 or hp <= hc:
        return None
    s = (hphc.fixe - base.fixe + kwh * (hp - _price(base, "kwh_ttc"))) / (kwh * (hp - hc))
    if not Decimal(0) <= s <= Decimal(1):
        return None
    return int((s * 100).to_integral_value(rounding=ROUND_CEILING))


def _fr_iso(iso: str) -> str:
    try:
        return fr_date(date.fromisoformat(iso))
    except ValueError:
        return iso


# ------------------------------------------------------------------ gaz : prix repère de la CRE
# Le prix repère moyen (€/MWh) est un prix « abonnement compris, tous consommateurs confondus » : il ne se compare pas
# au prix du kWh d'une facture. La grille de la CRE donne, pour chaque type de foyer, l'abonnement annuel et le prix
# du kWh : c'est elle qui se compare à ta facture.
GAS_PROFILES = ("chauffage", "cuisson")


@dataclass(frozen=True)
class GasGrid:
    """Grille du prix repère d'un type de foyer (zone GRDF), TTC : abonnement annuel et prix du kWh."""

    profile: str
    label: str
    reference_mwh: Decimal | None  # consommation annuelle de référence de la CRE pour ce type de foyer
    abonnement: Decimal
    kwh: Decimal
    kwh_low: Decimal | None = None
    kwh_high: Decimal | None = None


@dataclass(frozen=True)
class GasReference:
    eur_per_mwh: Decimal  # prix moyen, abonnement compris, tous consommateurs confondus
    since: date
    source: str
    source_url: str
    published: str | None
    next_price: Decimal | None = None
    next_since: date | None = None
    grid: tuple[GasGrid, ...] = ()
    next_grid: tuple[GasGrid, ...] = ()
    data_url: str | None = None


def _dec(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def _gas_grid(raw: Any, profiles: Mapping[str, Any]) -> tuple[GasGrid, ...]:
    """La grille d'un mois ; un type de foyer dont l'abonnement ou le kWh manque n'y entre pas."""
    if not isinstance(raw, Mapping):
        return ()
    out: list[GasGrid] = []
    for key in GAS_PROFILES:
        row, meta = raw.get(key), profiles.get(key)
        if not isinstance(row, Mapping) or not isinstance(meta, Mapping):
            continue
        abo, kwh = _dec(row.get("abonnement_ttc")), _dec(row.get("kwh_ttc"))
        if abo is None or kwh is None:
            continue
        out.append(
            GasGrid(
                key,
                str(meta.get("label") or key),
                _dec(meta.get("reference_mwh")),
                abo,
                kwh,
                _dec(row.get("kwh_low_ttc")),
                _dec(row.get("kwh_high_ttc")),
            )
        )
    return tuple(out)


def gas_reference(rates: Mapping[str, Any], today: date) -> GasReference | None:
    """Le prix repère en vigueur aujourd'hui (et le suivant s'il est déjà publié), d'après la section datée de
    rates.json ; None si la section manque ou si aucun prix n'est encore en vigueur."""
    section = rates.get("gas_reference")
    if not isinstance(section, Mapping):
        return None
    profiles = section.get("profiles")
    profiles = profiles if isinstance(profiles, Mapping) else {}
    rows: list[tuple[date, Decimal, tuple[GasGrid, ...]]] = []
    for row in section.get("prices") or []:
        try:
            day, price = date.fromisoformat(str(row["from"])), Decimal(str(row["eur_per_mwh_ttc"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        rows.append((day, price, _gas_grid(row.get("grid"), profiles)))
    rows.sort(key=lambda r: r[0])
    current = [r for r in rows if r[0] <= today]
    if not current:
        return None
    since, price, grid = current[-1]
    upcoming = [r for r in rows if r[0] > today]
    nxt = upcoming[0] if upcoming else None
    return GasReference(
        price,
        since,
        str(section.get("source") or ""),
        str(section.get("source_url") or ""),
        str(section.get("published") or "") or None,
        nxt[1] if nxt else None,
        nxt[0] if nxt else None,
        grid,
        nxt[2] if nxt else (),
        str(section.get("data_url") or "") or None,
    )


def euro_mwh(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}".replace(".", ",") + " €/MWh"


def month_year(d: date) -> str:
    return f"{MONTHS_FR[d.month - 1]} {d.year}"
