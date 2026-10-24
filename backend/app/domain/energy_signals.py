"""Signaux d'énergie (#33) : la puissance maximale atteinte chaque jour (Linky) face aux kVA souscrits, et la couleur
Tempo du jour et du lendemain.

Puissance : Conso API relaie l'API Enedis « Mesure synchrone V2 » (type `puissance_conso_max_quotidienne`, pas P1D,
grandeur PMA, en VA). Bank ne parle d'une puissance trop haute qu'avec douze mois de relevés (un hiver compris) et
seulement si le maximum de l'année reste au moins 1 kVA sous la puissance normalisée inférieure. L'économie vient de
la grille de la CRE (abonnement et prix du kWh de chaque puissance) ; quand le prix du kWh change avec la puissance,
il est compté sur ta consommation réelle des douze mois, au plus défavorable si l'option a plusieurs prix.

Tempo : la couleur vient d'api-couleur-tempo.fr (relais gratuit et sans clé de la publication de RTE ;
codeJour 1 bleu, 2 blanc, 3 rouge, 0 pas encore publiée). Module pur : les entrées sont déjà lues.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from app.domain.money import format_eur

STANDARD_KVA = (3, 6, 9, 12, 15, 18, 24, 30, 36)  # puissances normalisées du tarif réglementé
OPTIONS = {"base": "Base", "hphc": "Heures creuses", "tempo": "Tempo"}
# Composantes du prix du kWh de chaque option dans la grille de la CRE (séries « cre.<option>.<kVA>.<composante> »).
KWH_PARTS = {
    "base": ("kwh_ttc",),
    "hphc": ("hc_ttc", "hp_ttc"),
    "tempo": tuple(f"{c}_{h}_ttc" for c in ("bleu", "blanc", "rouge") for h in ("hc", "hp")),
}
MARGIN_VA = 1_000  # le maximum doit rester 1 kVA sous la puissance visée
YEAR_DAYS = 365
MAX_MISSING_DAYS = 31  # au plus un mois de trous dans l'année : au-delà, un pic d'hiver peut manquer
MAX_VA = 100_000  # au-delà de 100 kVA, ce n'est pas un compteur de particulier mais une donnée corrompue
MAX_POINTS = 5_000

TEMPO_URL = "https://www.api-couleur-tempo.fr/api/joursTempo"
TEMPO_SOURCE = "api-couleur-tempo.fr (relais de la couleur publiée par RTE)"
TEMPO_COLORS = {1: "bleu", 2: "blanc", 3: "rouge"}
TEMPO_LABELS = {"bleu": "Bleu", "blanc": "Blanc", "rouge": "Rouge"}


class SignalFormatError(ValueError):
    """Réponse sans série reconnaissable : le format de la source a changé."""


# ------------------------------------------------------------------ puissance maximale : lecture
@dataclass(frozen=True)
class MaxPower:
    day: date
    va: int
    at: str | None = None  # heure du maximum (HH:MM:SS), quand la source la donne


def _day_and_time(raw: Any) -> tuple[date, str | None] | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        day = date.fromisoformat(raw[:10])
    except ValueError:
        return None
    rest = raw[10:].strip().lstrip("T")
    at = rest[:8] if len(rest) >= 5 and rest[:2].isdigit() and rest[2] == ":" else None
    return day, at


_VA_UNITS = {"va": 1.0, "kva": 1000.0}


def parse_max_power(payload: Any) -> list[MaxPower]:
    """Puissance maximale de chaque jour, en VA, triée. Format Enedis V2 : `grandeur[].points[] = {v, d}` avec
    `unite` « VA ». Une unité inconnue est refusée (on ne devine pas entre W, VA et kVA) ; un point vide (v à null,
    jour sans mesure) est ignoré ; une valeur négative, infinie ou démesurée aussi."""
    if not isinstance(payload, Mapping):
        raise SignalFormatError("réponse inattendue (objet JSON attendu)")
    grandeurs = payload.get("grandeur")
    if not isinstance(grandeurs, list):
        raise SignalFormatError("champ « grandeur » absent")
    chosen = [
        g for g in grandeurs if isinstance(g, Mapping) and str(g.get("grandeurPhysique") or "PMA").upper() == "PMA"
    ]
    if not chosen:
        raise SignalFormatError("aucune grandeur PMA (puissance maximale) dans la réponse")
    by_day: dict[date, MaxPower] = {}
    seen = 0
    for g in chosen:
        factor = _VA_UNITS.get(str(g.get("unite") or "").strip().lower())
        if factor is None:
            raise SignalFormatError(f"unité inconnue ({str(g.get('unite'))[:10]!r})")
        points = g.get("points")
        if not isinstance(points, list):
            raise SignalFormatError("champ « points » absent")
        for p in points:
            seen += 1
            if seen > MAX_POINTS:
                raise SignalFormatError("réponse trop volumineuse")
            if not isinstance(p, Mapping) or p.get("v") is None:
                continue
            when = _day_and_time(p.get("d"))
            try:
                value = float(str(p.get("v")).replace(",", "."))
            except ValueError:
                continue
            if when is None or not math.isfinite(value):
                continue
            va = round(value * factor)
            if not 0 <= va <= MAX_VA:
                continue
            day, at = when
            prev = by_day.get(day)
            if prev is None or va > prev.va:  # deux points le même jour : le plus haut est le maximum du jour
                by_day[day] = MaxPower(day, va, at)
    return [by_day[d] for d in sorted(by_day)]


# ------------------------------------------------------------------ grille de la CRE
@dataclass(frozen=True)
class GridPower:
    """Une puissance de la grille d'une option : abonnement annuel TTC (€) et prix du kWh TTC (€) par composante."""

    kva: int
    fixe: Decimal
    kwh: Mapping[str, Decimal]
    as_of: str  # AAAA-MM-JJ : début de validité de la grille
    source: str
    source_url: str


def _cents(euros: Decimal) -> int:
    return int((euros * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def kva_label(va: int) -> str:
    return f"{Decimal(va) / 1000:.1f}".replace(".", ",") + " kVA"


def fr_date(d: date) -> str:
    return f"{d:%d/%m/%Y}"


def _fr_iso(iso: str) -> str:
    try:
        return fr_date(date.fromisoformat(iso))
    except ValueError:
        return iso


def euro_kwh(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}".replace(".", ",") + " €"


# ------------------------------------------------------------------ puissance souscrite : le constat
@dataclass(frozen=True)
class PowerCheck:
    status: str  # too_high | fits | no_saving | too_few_months | no_contract | no_grid | no_data | none_lower
    message: str
    subscribed_kva: int | None = None
    option: str | None = None
    max_va: int | None = None
    max_day: date | None = None
    max_at: str | None = None
    days: int = 0
    months_available: int = 0
    since: date | None = None
    target_kva: int | None = None
    yearly_saving_cents: int | None = None
    subscription_saving_cents: int | None = None
    kwh_effect_cents: int | None = None  # > 0 : le kWh coûte plus cher à la puissance visée
    yearly_kwh: int | None = None
    grid_as_of: str | None = None
    grid_source: str | None = None
    grid_url: str | None = None


def _full_months(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + end.month - start.month
    return max(0, months - (1 if end.day < start.day else 0))


def _kwh_delta(
    option: str, current: GridPower, target: GridPower, yearly_wh: int | None
) -> tuple[Decimal | None, str | None]:
    """Surcoût annuel du kWh à la puissance visée (€), au plus défavorable si l'option a plusieurs prix (heures
    pleines, couleurs) : sans la répartition de ta consommation, Bank prend le prix qui augmente le plus."""
    parts = KWH_PARTS[option]
    if any(p not in current.kwh or p not in target.kwh for p in parts):
        return None, "le prix du kWh d'une des deux puissances manque dans la grille"
    deltas = [target.kwh[p] - current.kwh[p] for p in parts]
    worst = max(deltas)
    if worst <= 0 and min(deltas) >= 0:
        return Decimal(0), None  # mêmes prix : la consommation ne change rien
    if yearly_wh is None:
        return None, "ta consommation sur douze mois manque pour compter l'écart de prix du kWh"
    return Decimal(yearly_wh) / 1000 * max(worst, Decimal(0)), None


def power_check(
    readings: Sequence[MaxPower],
    *,
    today: date,
    subscribed_kva: int | None,
    option: str | None,
    grid: Mapping[int, GridPower],
    yearly_wh: int | None,
) -> PowerCheck:
    """La puissance maximale des douze derniers mois face aux kVA souscrits. Chaque chiffre du message vient des
    relevés ou de la grille, avec sa date ; ce qui manque est dit au lieu d'être deviné."""
    start = today - timedelta(days=YEAR_DAYS)
    year = [r for r in readings if start <= r.day < today]
    known = subscribed_kva is not None and option in OPTIONS
    out = PowerCheck("", "", subscribed_kva=subscribed_kva if known else None, option=option if known else None)
    if not year:
        return replace(
            out,
            status="no_data",
            message="Pas encore de puissance maximale relevée par ton compteur : elle arrive avec la prochaine "
            "synchronisation Linky.",
        )
    first = year[0].day
    months = _full_months(first, today)
    peak = max(year, key=lambda r: (r.va, r.day))
    out = replace(
        out,
        max_va=peak.va,
        max_day=peak.day,
        max_at=peak.at,
        days=len(year),
        months_available=months,
        since=first,
        yearly_kwh=None if yearly_wh is None else round(yearly_wh / 1000),
    )
    peak_text = f"{kva_label(peak.va)} (le {fr_date(peak.day)})"
    if subscribed_kva is None or option is None or option not in OPTIONS:
        return replace(
            out,
            status="no_contract",
            message=f"Ta puissance maximale relevée sur {len(year)} jours est de {peak_text}. Indique ton option "
            "(Base, Heures creuses ou Tempo) et ta puissance souscrite (sur ta facture) : Bank les compare alors.",
        )
    if first > start + timedelta(days=7) or YEAR_DAYS - len(year) > MAX_MISSING_DAYS:
        return replace(
            out,
            status="too_few_months",
            message=f"Bank a {months} mois de puissance maximale (depuis le {fr_date(first)}, {len(year)} jours "
            f"relevés) ; il en faut 12 pour couvrir un hiver. Maximum relevé jusqu'ici : {peak_text}, pour "
            f"{subscribed_kva} kVA souscrits.",
        )
    lower = [p for p in STANDARD_KVA if p < subscribed_kva]
    if not lower:
        return replace(
            out,
            status="none_lower",
            message=f"En 12 mois, ta puissance maximale a été de {peak_text} ; {subscribed_kva} kVA est déjà la plus "
            "petite puissance proposée.",
        )
    fitting = [p for p in lower if peak.va <= p * 1000 - MARGIN_VA]
    if not fitting:
        return replace(
            out,
            status="fits",
            message=f"En 12 mois, ta puissance maximale a atteint {peak_text} : {subscribed_kva} kVA correspond à ton "
            f"usage (la puissance inférieure, {max(lower)} kVA, laisserait moins de 1 kVA de marge).",
        )
    current = grid.get(subscribed_kva)
    targets = [p for p in sorted(fitting) if p in grid]
    if current is None or not targets:
        missing = subscribed_kva if current is None else min(fitting)
        return replace(
            out,
            status="no_grid",
            target_kva=min(fitting),
            message=f"En 12 mois, ta puissance maximale n'a pas dépassé {peak_text}, pour {subscribed_kva} kVA "
            f"souscrits. Bank n'a pas la grille de la CRE de l'option {OPTIONS[option]} en {missing} kVA pour chiffrer "
            "l'économie : active « Barèmes tenus à jour » (Réglages › Barèmes).",
        )
    target_kva = targets[0]
    target = grid[target_kva]
    sub_saving = current.fixe - target.fixe
    kwh_extra, why = _kwh_delta(option, current, target, yearly_wh)
    as_of = max(current.as_of, target.as_of)
    out = replace(
        out,
        target_kva=target_kva,
        subscription_saving_cents=_cents(sub_saving),
        grid_as_of=as_of,
        grid_source=target.source if target.as_of >= current.as_of else current.source,
        grid_url=target.source_url or None,
    )
    head = f"En 12 mois, ta puissance maximale n'a pas dépassé {peak_text}. Tu as souscrit {subscribed_kva} kVA"
    tail = (
        " Le changement de puissance se demande à ton fournisseur ; Enedis peut facturer l'intervention (montant "
        "non vérifié par Bank)."
    )
    grid_note = f"grille de la CRE du {_fr_iso(as_of)}"
    if kwh_extra is None:
        return replace(
            out,
            status="too_high",
            message=f"{head} ; en {target_kva} kVA, l'abonnement {OPTIONS[option]} coûte "
            f"{format_eur(_cents(sub_saving))} de moins par an ({grid_note}). Bank ne chiffre pas l'économie totale : "
            f"{why}.{tail}",
        )
    saving = sub_saving - kwh_extra
    out = replace(out, kwh_effect_cents=_cents(kwh_extra))
    if saving <= 0:
        return replace(
            out,
            status="no_saving",
            message=f"{head} ; en {target_kva} kVA, l'abonnement baisse de {format_eur(_cents(sub_saving))} par an "
            f"mais le kWh coûte plus cher, ce qui l'annule sur ta consommation ({grid_note}).",
        )
    several = kwh_extra > 0 and len(KWH_PARTS[option]) > 1
    detail = ""
    if kwh_extra > 0:
        detail = (
            f" : {format_eur(_cents(sub_saving))} d'abonnement, moins {format_eur(_cents(kwh_extra))} de kWh plus cher "
            f"sur tes {round((yearly_wh or 0) / 1000)} kWh de l'année"
            + (" (au prix qui augmente le plus, faute de répartition de ta consommation)" if several else "")
        )
    amount = f"{'au moins' if several else 'environ'} {format_eur(_cents(saving))}"
    return replace(
        out,
        status="too_high",
        yearly_saving_cents=_cents(saving),
        message=f"{head} ; en {target_kva} kVA, tu économiserais {amount} par an{detail} ({grid_note}).{tail}",
    )


def grid_from_observations(
    option: str, latest: Mapping[str, tuple[str, str, str]], source: str
) -> dict[int, GridPower]:
    """La grille d'une option à partir des dernières valeurs relevées à la CRE : série → (période, valeur, adresse).
    Une puissance n'y entre que si l'abonnement et tous les prix du kWh sont de la même grille (même date)."""
    out: dict[int, GridPower] = {}
    for kva in STANDARD_KVA:
        fixe = latest.get(f"cre.{option}.{kva}.fixe_ttc")
        if fixe is None:
            continue
        period, value, url = fixe
        kwh: dict[str, Decimal] = {}
        for part in KWH_PARTS[option]:
            row = latest.get(f"cre.{option}.{kva}.{part}")
            if row is None or row[0] != period:
                break
            try:
                kwh[part] = Decimal(row[1])
            except InvalidOperation:
                break
        else:
            try:
                out[kva] = GridPower(kva, Decimal(value), kwh, period, source, url)
            except InvalidOperation:
                continue
    return out


def grid_from_rates(option: str, rates: Mapping[str, Any]) -> dict[int, GridPower]:
    """Repli sur rates.json (relevé recouvert compris) : seulement l'option Base, dont chaque ligne donne
    l'abonnement et le prix unique du kWh. Les lignes Heures creuses n'y donnent qu'un prix moyen supposé (65 % en
    heures pleines) : Bank ne s'en sert pas pour chiffrer une économie."""
    if option != "base":
        return {}
    trv = rates.get("electricity_trv") or {}
    out: dict[int, GridPower] = {}
    for o in trv.get("options") or []:
        oid = str(o.get("id") or "")
        if not oid.startswith("base_") or not oid[5:].isdigit():
            continue
        kva = int(oid[5:])
        out[kva] = GridPower(
            kva,
            Decimal(int(o["yearly_subscription_cents"])) / 100,
            {"kwh_ttc": Decimal(int(o["cents_per_100kwh"])) / 10_000},
            str(trv.get("as_of") or ""),
            str(trv.get("source") or "rates.json"),
            "",
        )
    return out


# ------------------------------------------------------------------ Tempo
def tempo_url(days: Iterable[date]) -> str:
    query = "&".join(f"dateJour%5B%5D={d.isoformat()}" for d in days)
    return f"{TEMPO_URL}?{query}"


def parse_tempo(body: bytes) -> dict[date, str | None]:
    """Couleur de chaque jour demandé : « bleu », « blanc », « rouge », ou None si RTE ne l'a pas encore publiée
    (codeJour 0, « Inconnu »). Accepte la liste de /api/joursTempo ou l'objet seul de /api/jourTempo/<jour>."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise SignalFormatError("réponse Tempo illisible (JSON attendu)") from exc
    items = data if isinstance(data, list) else [data]
    if len(items) > 400:
        raise SignalFormatError("réponse Tempo trop longue")
    out: dict[date, str | None] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise SignalFormatError("réponse Tempo inattendue")
        try:
            day = date.fromisoformat(str(item.get("dateJour"))[:10])
        except ValueError as exc:
            raise SignalFormatError("date Tempo illisible") from exc
        code = item.get("codeJour")
        if isinstance(code, bool) or not isinstance(code, int) or code not in (0, 1, 2, 3):
            raise SignalFormatError(f"code couleur inattendu ({str(code)[:6]!r})")
        out[day] = TEMPO_COLORS.get(code)
    return out


def tempo_prices(option_kva: int | None, latest: Mapping[str, tuple[str, str, str]]) -> dict[str, Any] | None:
    """Prix du kWh Tempo TTC d'une puissance (heures creuses et pleines de chaque couleur), avec la date de la
    grille de la CRE, ou None si Bank ne l'a pas relevée."""
    if option_kva is None:
        return None
    rows = {p: latest.get(f"cre.tempo.{option_kva}.{p}") for p in KWH_PARTS["tempo"]}
    if any(r is None for r in rows.values()):
        return None
    periods = {r[0] for r in rows.values() if r is not None}
    if len(periods) != 1:
        return None
    try:
        values = {p: Decimal(r[1]) for p, r in rows.items() if r is not None}
    except InvalidOperation:
        return None
    return {"as_of": periods.pop(), "kwh": values}
