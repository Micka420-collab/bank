"""Barèmes et indices relus aux sources publiques : lecture des fichiers (CSV de la CRE, JSON de la Caisse des Dépôts
et d'OpenFisca, SDMX de l'INSEE) et différence datée avec les valeurs en vigueur dans Bank. Module pur : aucune base,
aucun réseau.

Règles : une valeur n'est proposée que si la source la date au moins du même jour que celle en vigueur (une source en
retard ne fait jamais reculer un taux) ; une valeur absente ou illisible n'est jamais devinée, elle est signalée.
Tout ce qui vient d'ailleurs est une donnée : bornée, vérifiée, jamais exécutée (XML sans DTD ni entités).
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

import defusedxml.ElementTree as SafeET
from defusedxml.common import DefusedXmlException

MAX_ROWS = 5_000
MAX_VALUE = Decimal(1_000_000)
# Les options « heures pleines / creuses » de rates.json donnent un prix moyen du kWh, à 65 % en heures pleines
# (c'est écrit dans leur libellé) : on garde la même règle pour comparer avec la grille de la CRE.
HP_SHARE = Decimal("0.65")

CRE_DIR = "https://www.cre.fr/fileadmin/Documents/Open_data/Marches_de_detail"
CDC_URL = (
    "https://opendata.caissedesdepots.fr/api/explore/v2.1/catalog/datasets/flux-et-taux-la-ldds-lep/records"
    "?order_by=date%20desc&limit=3"
)
INSEE_URL = "https://api.insee.fr/series/BDM/data/SERIES_BDM/{idbank}?lastNObservations=6"
OPENFISCA_URL = "https://api.fr.openfisca.org/latest/parameter/{name}"
INSEE_SERIES = {"011814056": "insee.ipc", "011814133": "insee.ipc_glissement", "001515333": "insee.irl"}
OPENFISCA_VALUES = {
    "marche_travail.salaire_minimum.smic.smic_b_horaire": "openfisca.smic_horaire",
    "marche_travail.salaire_minimum.smic.smic_b_mensuel": "openfisca.smic_mensuel",
}
OPENFISCA_SCALE = "impot_revenu.bareme_ir_depuis_1945.bareme"

CSV_ACCEPT = "text/csv, */*;q=0.1"
JSON_ACCEPT = "application/json"
XML_ACCEPT = "application/xml, text/xml;q=0.9, */*;q=0.1"


class BaremeError(ValueError):
    """Fichier illisible ou inattendu : message lisible, la valeur en vigueur reste."""


@dataclass(frozen=True)
class Observation:
    series: str
    period: str  # AAAA-MM-JJ, AAAA-MM ou AAAA-Qn, tel que la source le date
    value: str  # décimal en texte, ou JSON (barème)
    source_url: str
    note: str | None = None


@dataclass(frozen=True)
class Change:
    path: str  # chemin dans rates.json (voir domain.rates)
    label: str | None  # None : date ou source de la section (suit les chiffres, n'est pas montrée seule)
    old: Any
    new: Any
    shown_old: str
    shown_new: str
    effective_from: str  # AAAA-MM-JJ
    source: str
    source_url: str
    section: str  # livrets | electricity | income_tax


@dataclass
class Proposal:
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ petits outils
def period_start(period: str) -> date:
    """Le premier jour de la période : « 2026-08-01 », « 2026-03 » (mois), « 2026-Q3 » (trimestre)."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if m:
        return date(int(m[1]), (int(m[2]) - 1) * 3 + 1, 1)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return date.fromisoformat(f"{period}-01")
    return date.fromisoformat(period)


def decimal_of(raw: object) -> Decimal:
    """Un nombre de la source (« 190,81 », 1.7000000000000002, "103.07") en décimal fini et borné, sinon erreur."""
    if isinstance(raw, bool) or raw is None:
        raise BaremeError("Valeur absente")
    if isinstance(raw, float) and not math.isfinite(raw):
        raise BaremeError("Valeur non finie")
    text = str(raw).strip().replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"-?\d{1,9}(?:\.\d{1,12})?(?:[eE]-?\d{1,2})?", text):
        raise BaremeError(f"Valeur illisible : {text[:20]!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - le motif ci-dessus l'écarte déjà
        raise BaremeError("Valeur illisible") from exc
    if not value.is_finite() or abs(value) >= MAX_VALUE:
        raise BaremeError("Valeur hors bornes")
    return value


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_EVEN)


def cents(euros: Decimal) -> int:
    return int((euros * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def fr_number(value: Decimal, places: int = 2) -> str:
    """« 1 234,56 » : espace fine insécable entre les milliers, virgule décimale."""
    return f"{value:,.{places}f}".replace(",", "\u202f").replace(".", ",")


def euros(cents_value: int) -> str:
    return f"{fr_number(Decimal(cents_value) / 100)} €"


def kwh_price(cents_per_100kwh: int) -> str:
    return f"{fr_number(Decimal(cents_per_100kwh) / 100)} c€/kWh"


def percent(value: Decimal | str) -> str:
    return f"{fr_number(Decimal(str(value)))} %"


def fr_date(iso: str) -> str:
    return period_start(iso).strftime("%d/%m/%Y")


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("latin-1")


# ------------------------------------------------------------------ CRE : tarifs réglementés de l'électricité
CRE_OPTIONS: dict[str, dict[str, str]] = {
    "base": {"PART_FIXE_TTC": "fixe_ttc", "PART_VARIABLE_TTC": "kwh_ttc"},
    "hphc": {"PART_FIXE_TTC": "fixe_ttc", "PART_VARIABLE_HC_TTC": "hc_ttc", "PART_VARIABLE_HP_TTC": "hp_ttc"},
    "tempo": {"PART_FIXE_TTC": "fixe_ttc"}
    | {
        f"PART_VARIABLE_{h}{color}_TTC": f"{color.lower()}_{h.lower()}_ttc"
        for color in ("Bleu", "Blanc", "Rouge")
        for h in ("HC", "HP")
    },
}
CRE_FILES = {"base": "Option_Base.csv", "hphc": "Option_HPHC.csv", "tempo": "Option_Tempo.csv"}


def cre_url(option: str) -> str:
    return f"{CRE_DIR}/{CRE_FILES[option]}"


def parse_cre(body: bytes, url: str, today: date, option: str) -> list[Observation]:
    """La grille en vigueur aujourd'hui (la plus récente dont la date de début est passée) : une observation par
    puissance et par composante, TTC. Une grille annoncée pour plus tard n'est pas encore lue."""
    columns = CRE_OPTIONS[option]
    reader = csv.DictReader(io.StringIO(_decode(body)), delimiter=";")
    missing = {"DATE_DEBUT", "P_SOUSCRITE", *columns} - set(reader.fieldnames or [])
    if missing:
        raise BaremeError(f"CRE : colonnes absentes ({', '.join(sorted(missing))})")
    grids: dict[date, list[dict[str, str]]] = {}
    for n, row in enumerate(reader):
        if n >= MAX_ROWS:
            raise BaremeError("CRE : fichier trop long")
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (row.get("DATE_DEBUT") or "").strip())
        try:
            start = date(int(m[3]), int(m[2]), int(m[1])) if m else None
        except ValueError:
            start = None
        if start is None:
            continue
        if start <= today:
            grids.setdefault(start, []).append(row)
    if not grids:
        raise BaremeError("CRE : aucune grille en vigueur dans le fichier")
    start = max(grids)
    out: list[Observation] = []
    for row in grids[start]:
        kva = (row.get("P_SOUSCRITE") or "").strip()
        if not kva.isdigit() or not 1 <= int(kva) <= 36:
            continue
        for column, name in columns.items():
            value = decimal_of(row.get(column))
            if value < 0:
                raise BaremeError("CRE : montant négatif")
            out.append(Observation(f"cre.{option}.{int(kva)}.{name}", start.isoformat(), str(value), url))
    if not out:
        raise BaremeError("CRE : grille vide")
    return out


# ------------------------------------------------------------------ Caisse des Dépôts : livrets réglementés
def parse_cdc(body: bytes, url: str, today: date) -> list[Observation]:
    """Le mois le plus récent publié (le jeu a souvent plusieurs mois de retard : c'est la comparaison des dates qui
    décide). Livret A et LDDS partagent leur taux."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise BaremeError("Caisse des Dépôts : réponse illisible (JSON attendu)") from exc
    rows = data.get("results") if isinstance(data, dict) else None
    months = [r for r in rows or [] if isinstance(r, dict) and re.fullmatch(r"\d{4}-\d{2}", str(r.get("date") or ""))]
    months = [r for r in months if period_start(str(r["date"])) <= today]
    if not months:
        raise BaremeError("Caisse des Dépôts : aucun mois publié")
    row = max(months, key=lambda r: str(r["date"]))
    period = str(row["date"])
    out = []
    for key, series in (("tla_tldds_percent", ("cdc.livret_a", "cdc.ldds")), ("tlep_percent", ("cdc.lep",))):
        value = _q(decimal_of(row.get(key)))
        if not Decimal(0) <= value <= Decimal(20):
            raise BaremeError("Caisse des Dépôts : taux hors bornes")
        out += [Observation(s, period, str(value), url) for s in series]
    return out


# ------------------------------------------------------------------ INSEE : indices (SDMX)
def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def parse_insee(body: bytes, url: str, today: date) -> list[Observation]:
    """Les dernières observations d'une série de la BDM (IPC base 2025, glissement annuel, IRL)."""
    try:
        root = SafeET.fromstring(body)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise BaremeError("INSEE : réponse illisible (XML attendu)") from exc
    out: list[Observation] = []
    for series in root.iter():
        if _local(series.tag) != "Series":
            continue
        name = INSEE_SERIES.get(str(series.get("IDBANK") or ""))
        if name is None:
            continue
        for obs in series:
            period = str(obs.get("TIME_PERIOD") or "")
            if _local(obs.tag) != "Obs" or not re.fullmatch(r"\d{4}-(?:\d{2}|Q[1-4])", period):
                continue
            if period_start(period) > today:
                continue
            out.append(Observation(name, period, str(decimal_of(obs.get("OBS_VALUE"))), url))
    if not out:
        raise BaremeError("INSEE : aucune observation dans la réponse")
    return out[:50]


# ------------------------------------------------------------------ OpenFisca : paramètres datés
def _reference(meta: Mapping[str, Any], key: str) -> str | None:
    ref = (meta.get("reference") or {}) if isinstance(meta, Mapping) else {}
    entry = ref.get(key) if isinstance(ref, Mapping) else None
    items = entry if isinstance(entry, list) else [entry] if entry else []
    titles = [str(i.get("title") or "")[:140] for i in items if isinstance(i, Mapping) and i.get("title")]
    return " ; ".join(titles)[:300] or None


def _json(body: bytes, who: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise BaremeError(f"{who} : réponse illisible (JSON attendu)") from exc
    if not isinstance(data, dict):
        raise BaremeError(f"{who} : réponse inattendue")
    return data


def parse_openfisca_values(body: bytes, url: str, today: date, series: str) -> list[Observation]:
    """Un paramètre à valeur simple (SMIC) : les quatre dernières valeurs entrées en vigueur, avec leur texte."""
    data = _json(body, "OpenFisca")
    values = data.get("values")
    if not isinstance(values, dict):
        raise BaremeError("OpenFisca : paramètre sans valeurs")
    keys = sorted(k for k in values if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k)) and date.fromisoformat(k) <= today)
    out = [
        Observation(series, k, str(decimal_of(values[k])), url, _reference(data.get("metadata") or {}, k))
        for k in keys[-4:]
        if values[k] is not None
    ]
    if not out:
        raise BaremeError("OpenFisca : aucune valeur en vigueur")
    return out


def parse_openfisca_scale(body: bytes, url: str, today: date) -> list[Observation]:
    """Le barème de l'impôt sur le revenu le plus récent : seuils (en euros) et taux, rangés. OpenFisca le date par
    année des revenus (« 2025-01-01 » : revenus 2025, imposés en 2026)."""
    data = _json(body, "OpenFisca")
    brackets = data.get("brackets")
    if not isinstance(brackets, dict):
        raise BaremeError("OpenFisca : barème sans tranches")
    keys = sorted(k for k in brackets if re.fullmatch(r"\d{4}-01-01", str(k)) and date.fromisoformat(k) <= today)
    if not keys:
        raise BaremeError("OpenFisca : aucun barème en vigueur")
    key = keys[-1]
    scale = brackets[key]
    if not isinstance(scale, dict) or not 2 <= len(scale) <= 12:
        raise BaremeError("OpenFisca : barème illisible")
    pairs = sorted((decimal_of(t), decimal_of(r)) for t, r in scale.items())
    if pairs[0][0] != 0 or any(not Decimal(0) <= r < 1 for _t, r in pairs):
        raise BaremeError("OpenFisca : barème hors bornes")
    value = json.dumps([[str(t), str(r)] for t, r in pairs])
    return [Observation("openfisca.bareme_ir", key, value, url, _reference(data.get("metadata") or {}, key))]


# ------------------------------------------------------------------ différence avec ce qui est en vigueur
def latest(observations: Iterable[Observation]) -> dict[str, Observation]:
    """La dernière observation de chaque série."""
    out: dict[str, Observation] = {}
    for o in observations:
        if o.series not in out or period_start(o.period) > period_start(out[o.series].period):
            out[o.series] = o
    return out


def _meta(
    section: str,
    path_as_of: str,
    path_source: str,
    old_as_of: str,
    new_as_of: str,
    old_source: str,
    source: str,
    url: str,
) -> list[Change]:
    """La date et la source de la section suivent les chiffres : « tarif du 01/08/2026, d'après la CRE »."""
    out = []
    if new_as_of > old_as_of:
        out.append(
            Change(path_as_of, None, old_as_of, new_as_of, old_as_of, new_as_of, new_as_of, source, url, section)
        )
    if source != old_source:
        out.append(Change(path_source, None, old_source, source, "", "", new_as_of, source, url, section))
    return out


def electricity(rates: Mapping[str, Any], obs: Mapping[str, Observation], fetched_on: date) -> Proposal:
    """Les options Base et Heures creuses de rates.json face à la grille de la CRE : abonnement annuel TTC et prix du
    kWh TTC (moyenne à 65 % d'heures pleines pour l'option Heures creuses, comme dans le libellé)."""
    prop = Proposal()
    trv = rates.get("electricity_trv") or {}
    as_of = str(trv.get("as_of") or "")
    period: str | None = None
    values: list[Change] = []
    for opt in trv.get("options") or []:
        m = re.fullmatch(r"(base|hphc)_(\d+)", str(opt.get("id") or ""))
        if not m:
            continue
        kind, kva = m[1], m[2]
        fixe = obs.get(f"cre.{kind}.{kva}.fixe_ttc")
        if kind == "base":
            kwh = obs.get(f"cre.base.{kva}.kwh_ttc")
            price = Decimal(kwh.value) if kwh else None
        else:
            hp, hc = obs.get(f"cre.hphc.{kva}.hp_ttc"), obs.get(f"cre.hphc.{kva}.hc_ttc")
            price = HP_SHARE * Decimal(hp.value) + (1 - HP_SHARE) * Decimal(hc.value) if hp and hc else None
        if fixe is None or price is None:
            prop.notes.append(f"{opt.get('label')} : absent de la grille de la CRE, valeur en vigueur gardée.")
            continue
        period = fixe.period
        if period < as_of:
            continue
        label = str(opt.get("label") or opt["id"])
        new_sub = cents(Decimal(fixe.value))
        new_kwh = int((price * 10_000).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        source = (
            f"CRE, tarifs réglementés de vente de l'électricité (données ouvertes), grille TTC en vigueur depuis le "
            f"{fr_date(period)}, relue le {fetched_on:%d/%m/%Y}"
        )
        old_sub, old_kwh = int(opt["yearly_subscription_cents"]), int(opt["cents_per_100kwh"])
        base = f"electricity_trv/options:{opt['id']}"
        if new_sub != old_sub:
            values.append(
                Change(
                    f"{base}/yearly_subscription_cents",
                    f"Abonnement {label}",
                    old_sub,
                    new_sub,
                    f"{euros(old_sub)} par an",
                    f"{euros(new_sub)} par an",
                    period,
                    source,
                    cre_url(kind),
                    "electricity",
                )
            )
        if new_kwh != old_kwh:
            values.append(
                Change(
                    f"{base}/cents_per_100kwh",
                    f"Prix du kWh {label}",
                    old_kwh,
                    new_kwh,
                    kwh_price(old_kwh),
                    kwh_price(new_kwh),
                    period,
                    source,
                    cre_url(kind),
                    "electricity",
                )
            )
    if period is None:
        return prop
    if period < as_of:
        prop.notes.append(
            f"CRE : la grille lue date du {fr_date(period)}, plus ancienne que celle en vigueur dans Bank "
            f"({fr_date(as_of)}) : rien de changé."
        )
        return prop
    if values or period > as_of:
        source = values[0].source if values else f"CRE, grille TTC en vigueur depuis le {fr_date(period)}"
        prop.changes += values + _meta(
            "electricity",
            "electricity_trv/as_of",
            "electricity_trv/source",
            as_of,
            period,
            str(trv.get("source") or ""),
            source,
            cre_url("base"),
        )
    return prop


SAVINGS = {"livret_a": "cdc.livret_a", "ldds": "cdc.ldds", "lep": "cdc.lep"}


def savings(rates: Mapping[str, Any], obs: Mapping[str, Observation], fetched_on: date) -> Proposal:
    """Taux du Livret A, du LDDS et du LEP face au dernier mois publié par la Caisse des Dépôts."""
    prop = Proposal()
    as_of = str(rates.get("as_of") or "")
    found = [o for o in (obs.get(s) for s in SAVINGS.values()) if o is not None]
    if not found:
        return prop
    month = max(found, key=lambda o: o.period).period
    start = period_start(month).isoformat()
    if start < as_of:
        prop.notes.append(
            f"Caisse des Dépôts : dernier mois publié {period_start(month):%m/%Y}, plus ancien que les taux en vigueur "
            f"dans Bank (depuis le {fr_date(as_of)}) : rien de changé."
        )
        return prop
    source = (
        f"Caisse des Dépôts, flux et taux du Livret A, du LDDS et du LEP (données ouvertes), mois de "
        f"{period_start(month):%m/%Y}, relu le {fetched_on:%d/%m/%Y}"
    )
    values = []
    for product in rates.get("products") or []:
        o = obs.get(SAVINGS.get(str(product.get("kind")), ""))
        if o is None:
            continue
        old, new = Decimal(str(product["rate_percent"])), Decimal(o.value)
        if old != new:
            values.append(
                Change(
                    f"products:{product['kind']}/rate_percent",
                    f"Taux du {product['name']}",
                    str(product["rate_percent"]),
                    str(_q(new)),
                    percent(old),
                    percent(new),
                    start,
                    source,
                    CDC_URL,
                    "livrets",
                )
            )
    if values or start > as_of:
        prop.changes += values + _meta(
            "livrets", "as_of", "source", as_of, start, str(rates.get("source") or ""), source, CDC_URL
        )
    return prop


def _brackets(raw: str) -> list[dict[str, int | None]] | None:
    """Seuils bas d'OpenFisca → tranches de rates.json (plafond en centimes, taux entier en %). None si un taux n'est
    pas un pourcentage entier : Bank ne l'arrondit pas en silence."""
    pairs = [(Decimal(t), Decimal(r)) for t, r in json.loads(raw)]
    out: list[dict[str, int | None]] = []
    for i, (_t, r) in enumerate(pairs):
        pct = r * 100
        if pct != pct.to_integral_value():
            return None
        up_to = cents(pairs[i + 1][0]) if i + 1 < len(pairs) else None
        out.append({"up_to_cents": up_to, "rate_percent": int(pct)})
    return out


def brackets_text(brackets: Sequence[Mapping[str, Any]]) -> str:
    """« 11 % au-delà de 11 600 €, 30 % au-delà de 29 579 €… »"""
    parts, low = [], 0
    for b in brackets:
        if b["rate_percent"]:
            parts.append(f"{b['rate_percent']} % au-delà de {fr_number(Decimal(low) / 100, 0)} €")
        low = int(b["up_to_cents"] or 0)
    return ", ".join(parts)


def income_tax(rates: Mapping[str, Any], obs: Mapping[str, Observation], fetched_on: date) -> Proposal:
    """Le barème de l'impôt sur le revenu face à celui d'OpenFisca (daté par année des revenus)."""
    prop = Proposal()
    o = obs.get("openfisca.bareme_ir")
    tax = rates.get("income_tax") or {}
    if o is None or not tax.get("incomes_year"):
        return prop
    year, current = int(o.period[:4]), int(tax["incomes_year"])
    if year < current:
        prop.notes.append(
            f"OpenFisca : barème des revenus {year}, plus ancien que celui en vigueur dans Bank (revenus {current}) : "
            "rien de changé."
        )
        return prop
    new = _brackets(o.value)
    if new is None:
        prop.notes.append("OpenFisca : un taux du barème n'est pas un pourcentage entier, valeur en vigueur gardée.")
        return prop
    old = list(tax.get("brackets") or [])
    if new == old and year == current:
        return prop
    source = (
        f"OpenFisca-France, paramètre {OPENFISCA_SCALE} (revenus {year}"
        + (f" ; {o.note}" if o.note else "")
        + f"), relu le {fetched_on:%d/%m/%Y}"
    )[:300]
    url = OPENFISCA_URL.format(name=OPENFISCA_SCALE)
    start = f"{year:04d}-01-01"
    section = "income_tax"
    if new != old:
        prop.changes.append(
            Change(
                "income_tax/brackets",
                f"Barème de l'impôt sur le revenu (revenus {year})",
                old,
                new,
                brackets_text(old),
                brackets_text(new),
                start,
                source,
                url,
                section,
            )
        )
    if year > current:
        for path, old_v, new_v in (
            ("income_tax/incomes_year", current, year),
            ("income_tax/year", int(tax.get("year") or current + 1), year + 1),
        ):
            prop.changes.append(Change(path, None, old_v, new_v, "", "", start, source, url, section))
    if source != str(tax.get("source") or ""):
        prop.changes.append(
            Change("income_tax/source", None, tax.get("source"), source, "", "", start, source, url, section)
        )
    return prop


PROPOSERS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Observation], date], Proposal]] = {
    "electricity": electricity,
    "livrets": savings,
    "income_tax": income_tax,
}


def propose(rates: Mapping[str, Any], observations: Iterable[Observation], fetched_on: date) -> Proposal:
    """Toutes les valeurs à changer, datées et sourcées, et ce qui n'a pas pu l'être (dit en clair)."""
    obs = latest(observations)
    out = Proposal()
    for proposer in PROPOSERS.values():
        p = proposer(rates, obs, fetched_on)
        out.changes += p.changes
        out.notes += p.notes
    return out
