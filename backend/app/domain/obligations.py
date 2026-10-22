"""Déclarations récurrentes dont dépend le versement de tes aides : l'actualisation France Travail, la déclaration
trimestrielle de ressources (CAF, MSA), le renouvellement de la Complémentaire santé solidaire, la déclaration de
revenus. Pour chacune : la fenêtre, l'échéance, ce qui se passe si on oublie, et la preuve que c'est fait (souvent
le versement qui suit). Règles datées et sourcées : Bank dit quand les revérifier, et tu peux les ajuster.

Rien ici n'envoie quoi que ce soit : Bank rappelle, prépare les chiffres, constate. Déclarer reste ton geste.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.recurring import add_months

CHECKED_ON = "22 octobre 2026"
LATE_VISIBLE_DAYS = 60  # un oubli reste affiché deux mois, puis la période est considérée manquée
SOON_DAYS = 3


@dataclass(frozen=True)
class Spec:
    kind: str
    label: str
    organism: str  # france_travail | caf | msa | cpam | dgfip | other
    organism_name: str
    cadence_months: int
    what: str
    consequence: str
    url: str
    source: str
    anchor: str | None = None  # ce qu'il faut régler : "month" (mois de déclaration), "date" (fin de droits)…
    anchor_label: str | None = None
    proof_kinds: tuple[str, ...] = ()  # versements qui prouvent que c'est fait (codes d'aides)


SPECS: dict[str, Spec] = {
    s.kind: s
    for s in (
        Spec(
            "ft_actualisation",
            "Actualisation France Travail",
            "france_travail",
            "France Travail",
            1,
            "Chaque mois, déclare ta situation (travail, formation, maladie…) entre le 28 et le 15 du mois suivant : "
            "c'est ce qui déclenche le versement de ton allocation (sous 3 à 5 jours ouvrés).",
            "Sans actualisation au 15 à minuit, tu es radié de la liste des demandeurs d'emploi et l'allocation du "
            "mois n'est pas versée ; il faut se réinscrire.",
            "https://candidat.francetravail.fr/",
            f"francetravail.fr, calendrier 2026 des paiements et de l'actualisation (relevé le {CHECKED_ON})",
            proof_kinds=("ft_are", "ft_autre"),
        ),
        Spec(
            "caf_dtr",
            "Déclaration trimestrielle de ressources (CAF)",
            "caf",
            "CAF",
            3,
            "Tous les trois mois, vérifie et valide tes ressources des trois mois de référence dans Mon Compte : "
            "salaires, chômage, indemnités journalières et pensions sont préremplis avec le montant net social "
            "(solidarité à la source) ; ajoute ce qui n'y est pas (pension alimentaire reçue, revenus non salariés, "
            "revenus de placements).",
            "Sans déclaration validée avant la fin du mois, le RSA ou la prime d'activité est suspendu ; pour le RSA, "
            "les mois non déclarés ne sont pas rattrapés.",
            "https://www.caf.fr/allocataires/mon-compte",
            f"caf.fr (solidarité à la source, 2025-2026) et service-public.fr, relevés le {CHECKED_ON}",
            anchor="month",
            anchor_label="Mois de ta prochaine déclaration",
            proof_kinds=("caf_rsa", "caf_ppa", "caf_autre"),
        ),
        Spec(
            "msa_dtr",
            "Déclaration trimestrielle de ressources (MSA)",
            "msa",
            "MSA",
            3,
            "Tous les trois mois, vérifie et valide tes ressources des trois mois de référence dans Mon espace privé "
            "MSA (préremplies avec le montant net social) ; ajoute ce qui n'y est pas.",
            "Sans déclaration validée avant la fin du mois, le RSA ou la prime d'activité est suspendu ; pour le RSA, "
            "les mois non déclarés ne sont pas rattrapés.",
            "https://www.msa.fr/",
            f"msa.fr et service-public.fr, relevés le {CHECKED_ON}",
            anchor="month",
            anchor_label="Mois de ta prochaine déclaration",
            proof_kinds=("msa_prestation",),
        ),
        Spec(
            "css_renewal",
            "Renouvellement de la Complémentaire santé solidaire",
            "cpam",
            "Assurance Maladie",
            12,
            "Demande le renouvellement entre quatre et deux mois avant la fin de tes droits (compte ameli ou "
            "formulaire) : l'instruction prend un à deux mois. Si tu touches le RSA ou l'ASPA, le renouvellement est "
            "automatique : vérifie seulement le courrier reçu trois mois avant.",
            "Sans demande avant la fin des droits, plus de complémentaire : les soins restent à ta charge jusqu'à "
            "la nouvelle attribution.",
            "https://www.ameli.fr/",
            f"ameli.fr et service-public.fr, relevés le {CHECKED_ON}",
            anchor="date",
            anchor_label="Date de fin de tes droits (sur l'attestation)",
        ),
        Spec(
            "tax_return",
            "Déclaration de revenus",
            "dgfip",
            "Impôts",
            12,
            "Vérifie, complète et signe ta déclaration en ligne entre l'ouverture (début avril) et la date limite de "
            "ton département ; si tu es éligible à la déclaration automatique, vérifier suffit. Elle sert aussi au "
            "calcul de tes aides (APL, LEP, Complémentaire santé solidaire).",
            "Majoration de 10 % de l'impôt dès le premier jour de retard (20 % après mise en demeure), et des droits "
            "calculés sur des revenus inconnus.",
            "https://www.impots.gouv.fr/accueil",
            f"impots.gouv.fr, calendrier 2026 (relevé le {CHECKED_ON}) ; les dates des années suivantes sont "
            "estimées jusqu'à la publication du calendrier officiel (mars-avril)",
        ),
        Spec(
            "custom",
            "Autre obligation",
            "other",
            "Autre",
            12,
            "Une obligation à toi : attestation à fournir, document à renouveler, déclaration propre à ta situation.",
            "Ce que tu as noté.",
            "",
            "toi",
            anchor="date",
            anchor_label="Prochaine échéance",
        ),
    )
}
SUGGESTABLE = ("ft_actualisation", "caf_dtr", "msa_dtr", "tax_return")

# Déclaration de revenus : calendrier 2026 (publié), et une estimation prudente pour les années suivantes.
TAX_CALENDAR: dict[int, dict[str, date]] = {
    2026: {
        "opens": date(2026, 4, 9),
        "paper": date(2026, 5, 19),
        "zone1": date(2026, 5, 21),
        "zone2": date(2026, 5, 28),
        "zone3": date(2026, 6, 4),
    }
}


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    opens: date
    deadline: date
    reference_months: tuple[str, ...] = ()  # mois de ressources couverts (déclaration trimestrielle)
    proof_from: date | None = None  # un versement entre ces deux dates vaut preuve
    proof_to: date | None = None
    estimated: bool = False  # dates estimées (calendrier officiel pas encore publié)
    note: str | None = None


@dataclass(frozen=True)
class Done:
    period: str
    on: date
    how: str  # user | payment:<id> | auto


MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def month_label(key: str) -> str:
    year, month = key.split("-")[:2]
    return f"{MONTHS_FR[int(month) - 1]} {year}"


def fmt(d: date, today: date | None = None) -> str:
    """« 15 novembre », l'année en plus quand elle n'est pas celle d'aujourd'hui (ou qu'on ne la connaît pas)."""
    year = "" if today is not None and d.year == today.year else f" {d.year}"
    return f"{d.day} {MONTHS_FR[d.month - 1]}{year}"


def _last_day(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def tax_zone(insee_code: str | None) -> int | None:
    """Zone de la date limite de déclaration en ligne, d'après le département du code INSEE (None : inconnu)."""
    code = (insee_code or "").strip().upper()
    if len(code) < 2:
        return None
    head = code[:2]
    if head in ("2A", "2B"):
        return 2  # Corse : département 20
    if head in ("97", "98"):
        return 3
    if not head.isdigit():
        return None
    dept = int(head)
    if 1 <= dept <= 19:
        return 1
    if 20 <= dept <= 54:
        return 2
    return 3 if dept >= 55 else None


# ------------------------------------------------------------------ fenêtres par nature
def ft_period(allocation_month: date) -> Period:
    """Allocation du mois M : actualisation du 28 de M (26 en février court) au 15 de M+1 ; versement sous 5 jours
    ouvrés, donc une preuve attendue jusqu'au 22 de M+1."""
    first = allocation_month.replace(day=1)
    days = calendar.monthrange(first.year, first.month)[1]
    open_day = 28 if first.month != 2 else min(28, days - 2)
    opens = first.replace(day=open_day)
    nxt = add_months(first, 1)
    deadline = nxt.replace(day=15)
    return Period(
        _month_key(first),
        f"Allocation de {month_label(_month_key(first))}",
        opens,
        deadline,
        proof_from=opens,
        proof_to=deadline + timedelta(days=7),
    )


def dtr_period(declaration_month: date) -> Period:
    """Déclaration faite dans le mois D sur les ressources de D-4, D-3 et D-2 ; le versement de D+1 prouve qu'elle a
    été validée (sans déclaration, il est suspendu)."""
    first = declaration_month.replace(day=1)
    months = tuple(_month_key(add_months(first, -k)) for k in (4, 3, 2))
    nxt = add_months(first, 1)
    return Period(
        _month_key(first),
        f"Ressources de {month_label(months[0])} à {month_label(months[-1])}",
        first,
        _last_day(first),
        reference_months=months,
        proof_from=nxt,
        proof_to=nxt.replace(day=15),
    )


def css_period(end_of_rights: date) -> Period:
    opens = add_months(end_of_rights, -4)
    advised = add_months(end_of_rights, -2)
    return Period(
        _month_key(end_of_rights),
        f"Droits jusqu'au {end_of_rights.day} {MONTHS_FR[end_of_rights.month - 1]} {end_of_rights.year}",
        opens,
        advised,
        note=f"Conseillé avant le {fmt(advised)} ; dernier jour de couverture le {fmt(end_of_rights)}.",
    )


def tax_period(year: int, zone: int | None) -> Period:
    cal = TAX_CALENDAR.get(year)
    estimated = cal is None
    if cal is None:
        cal = {
            "opens": date(year, 4, 10),
            "paper": date(year, 5, 20),
            "zone1": date(year, 5, 22),
            "zone2": date(year, 5, 29),
            "zone3": date(year, 6, 5),
        }
    z = zone or 1  # département inconnu : la date la plus proche, pour prévenir à temps
    note = None if zone else "Département inconnu : date limite la plus proche retenue (renseigne ta commune)."
    if estimated:
        note = (note + " " if note else "") + "Dates estimées : le calendrier officiel paraît en mars-avril."
    return Period(
        str(year),
        f"Revenus de {year - 1}",
        cal["opens"],
        cal[f"zone{z}"],
        estimated=estimated,
        note=note,
    )


def custom_period(due: date, window_days: int) -> Period:
    return Period(due.isoformat(), f"Échéance du {fmt(due)}", due - timedelta(days=max(1, window_days)), due)


def schedule(
    kind: str,
    today: date,
    *,
    anchor: date | None = None,
    insee_code: str | None = None,
    window_days: int = 30,
    cadence_months: int | None = None,
    horizon_days: int = 400,
) -> list[Period]:
    """Les périodes utiles : de la dernière encore rattrapable (60 jours après l'échéance) à l'horizon."""
    out: list[Period] = []
    floor = today - timedelta(days=LATE_VISIBLE_DAYS)
    limit = today + timedelta(days=horizon_days)
    if kind == "ft_actualisation":
        m = add_months(today.replace(day=1), -3)
        while m <= limit:
            p = ft_period(m)
            if p.deadline >= floor:
                out.append(p)
            m = add_months(m, 1)
    elif kind in ("caf_dtr", "msa_dtr"):
        if anchor is None:
            return []
        m = anchor.replace(day=1)
        while _last_day(m) >= floor:
            m = add_months(m, -3)
        while _last_day(m) < floor:
            m = add_months(m, 3)
        while m <= limit:
            out.append(dtr_period(m))
            m = add_months(m, 3)
    elif kind == "css_renewal":
        if anchor is None:
            return []
        end = anchor
        while add_months(end, -2) >= floor:
            end = add_months(end, -12)
        while add_months(end, -2) < floor:
            end = add_months(end, 12)
        while add_months(end, -4) <= limit:
            out.append(css_period(end))
            end = add_months(end, 12)
    elif kind == "tax_return":
        zone = tax_zone(insee_code)
        for year in range(today.year - 1, limit.year + 1):
            p = tax_period(year, zone)
            if floor <= p.deadline <= limit:
                out.append(p)
    elif kind == "custom":
        if anchor is None:
            return []
        step = max(1, cadence_months or 12)
        due = anchor
        while due >= floor:
            due = add_months(due, -step)
        while due < floor:
            due = add_months(due, step)
        while due <= limit:
            out.append(custom_period(due, window_days))
            due = add_months(due, step)
    return out


# ------------------------------------------------------------------ état
def state_of(period: Period, today: date, done: bool) -> str:
    """done | upcoming | open | soon | late | missed."""
    if done:
        return "done"
    if today < period.opens:
        return "upcoming"
    if today <= period.deadline:
        return "soon" if (period.deadline - today).days <= SOON_DAYS else "open"
    return "late" if (today - period.deadline).days <= LATE_VISIBLE_DAYS else "missed"


@dataclass(frozen=True)
class PaymentHint:
    id: int
    day: date
    amount_cents: int
    kind: str


def proof_for(period: Period, spec: Spec, payments: Iterable[PaymentHint]) -> PaymentHint | None:
    """Le versement qui prouve que la déclaration a été faite (le premier dans la fenêtre de preuve)."""
    if period.proof_from is None or period.proof_to is None or not spec.proof_kinds:
        return None
    hits = [p for p in payments if p.kind in spec.proof_kinds and period.proof_from <= p.day <= period.proof_to]
    return min(hits, key=lambda p: (p.day, p.id)) if hits else None


def awaiting_payment(period: Period, spec: Spec, today: date, done: bool, proof: PaymentHint | None) -> bool:
    """Déclaré (ou fenêtre passée) mais aucun versement vu alors qu'il devrait être là : à vérifier d'urgence."""
    if proof is not None or period.proof_to is None or not spec.proof_kinds:
        return False
    return today > period.proof_to and (done or today <= period.deadline + timedelta(days=LATE_VISIBLE_DAYS))


# ------------------------------------------------------------------ propositions
def suggest(
    payments: Iterable[PaymentHint], today: date, *, existing: Iterable[str], dismissed: Iterable[str], has_data: bool
) -> list[str]:
    """Les obligations qu'on devine d'après tes versements : France Travail → actualisation, RSA ou prime
    d'activité → déclaration trimestrielle ; la déclaration de revenus concerne tout le monde."""
    skip = set(existing) | set(dismissed)
    recent = [p for p in payments if p.day >= today - timedelta(days=75)]
    kinds = {p.kind for p in recent}
    out: list[str] = []
    if kinds & {"ft_are", "ft_autre"}:
        out.append("ft_actualisation")
    if kinds & {"caf_rsa", "caf_ppa"}:
        out.append("caf_dtr")
    if "msa_prestation" in kinds:
        out.append("msa_dtr")
    if has_data:
        out.append("tax_return")
    return [k for k in out if k not in skip]


# ------------------------------------------------------------------ déclaration trimestrielle : les chiffres
RESOURCE_KINDS: dict[str, str] = {
    "activite": "Salaires et revenus d'activité",
    "chomage": "Allocations chômage",
    "ij": "Indemnités journalières (maladie, maternité)",
    "pension": "Pensions, retraites, rentes",
    "autres": "Autres ressources",
}
CAF_OWN = ("caf_", "msa_")  # déjà connus de l'organisme : jamais à redéclarer
NOT_RESOURCES = ("cpam_rembt", "dgfip_rembt", "crous_bourse", "departement_aide")


@dataclass(frozen=True)
class IncomeRow:
    id: int
    day: date
    amount_cents: int
    label: str
    category: str | None
    aid_kind: str | None
    is_transfer: bool


@dataclass(frozen=True)
class Payslip:
    document_id: int
    month: str
    net_social_cents: int | None
    net_paid_cents: int | None
    employer: str | None


@dataclass
class Prefill:
    months: tuple[str, ...]
    cells: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # kind -> month -> {amount, items…}
    totals: dict[str, int] = field(default_factory=dict)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    to_check: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def classify_resource(row: IncomeRow) -> str | None:
    """activite | chomage | ij | pension | autres | None (exclu : aide déjà connue, remboursement, virement interne)."""
    if row.is_transfer or row.amount_cents <= 0:
        return None
    aid = row.aid_kind or ""
    if aid:
        if aid.startswith(CAF_OWN) or aid in NOT_RESOURCES:
            return None
        if aid in ("ft_are", "ft_autre"):
            return "chomage"
        if aid == "cpam_ij":
            return "ij"
        if aid in ("retraite_pension", "cpam_invalidite"):
            return "pension"
        return "autres"
    if row.category == "salaire":
        return "activite"
    if row.category == "autres_revenus":
        return "autres"
    return None


def dtr_prefill(months: Sequence[str], rows: Iterable[IncomeRow], payslips: Iterable[Payslip]) -> Prefill:
    """Par mois et par type, ce que tes relevés disent — et le montant net social des bulletins lus quand il existe
    (c'est lui que la CAF attend ; le net versé est souvent plus bas)."""
    months = tuple(months)
    out = Prefill(months)
    for name in RESOURCE_KINDS:
        out.cells[name] = {m: {"amount_cents": 0, "items": [], "source": "releve"} for m in months}
    slips: dict[str, list[Payslip]] = {}
    for s in payslips:
        if s.month in months and s.net_social_cents:
            slips.setdefault(s.month, []).append(s)
    for row in rows:
        m = _month_key(row.day)
        if m not in months:
            continue
        kind = classify_resource(row)
        item: dict[str, Any] = {
            "id": row.id,
            "day": row.day.isoformat(),
            "label": row.label[:80],
            "amount_cents": row.amount_cents,
        }
        if kind is None:
            if not row.is_transfer and row.amount_cents > 0:
                if row.aid_kind and (row.aid_kind.startswith(CAF_OWN) or row.aid_kind in NOT_RESOURCES):
                    out.excluded.append({**item, "why": "déjà connu de l'organisme ou pas une ressource"})
                elif row.category in ("remboursements", "epargne", "virements_internes", "aides"):
                    out.excluded.append({**item, "why": "remboursement, épargne ou virement : pas une ressource"})
                else:
                    out.to_check.append({**item, "why": "nature à préciser : pension alimentaire ? vente ? cadeau ?"})
            continue
        cell = out.cells[kind][m]
        cell["amount_cents"] += row.amount_cents
        cell["items"].append(item)
    for m, found in slips.items():
        cell = out.cells["activite"][m]
        net_social = sum(int(s.net_social_cents or 0) for s in found)
        cell["net_paid_cents"] = cell["amount_cents"]
        cell["amount_cents"] = net_social
        cell["source"] = "bulletin"
        cell["documents"] = [s.document_id for s in found]
    for name in RESOURCE_KINDS:
        out.totals[name] = sum(int(c["amount_cents"]) for c in out.cells[name].values())
    if any(c["source"] == "releve" and c["amount_cents"] for c in out.cells["activite"].values()):
        out.notes.append(
            "Pour les salaires, la CAF attend le montant net social du bulletin de paie, pas le net versé sur ton "
            "compte (souvent plus bas) : reprends la case « Montant net social » de chaque bulletin, ou dépose tes "
            "bulletins dans Papiers pour que Bank le lise."
        )
    out.notes.append(
        "Déclare chaque ressource au mois où elle a été perçue. Les prestations CAF, les remboursements et les "
        "virements entre tes comptes ne se déclarent pas."
    )
    out.notes.append(
        "À ajouter toi-même si tu en as : pension alimentaire reçue, revenus non salariés, revenus de placements, "
        "loyers perçus."
    )
    return out


def prefill_to_dict(p: Prefill) -> dict[str, Any]:
    return {
        "months": list(p.months),
        "month_labels": [month_label(m) for m in p.months],
        "kinds": [{"key": k, "label": v} for k, v in RESOURCE_KINDS.items()],
        "cells": p.cells,
        "totals": p.totals,
        "excluded": p.excluded,
        "to_check": p.to_check,
        "notes": p.notes,
    }


SHORT_KINDS = {
    "activite": "salaires",
    "chomage": "chômage",
    "ij": "indemnités journalières",
    "pension": "pensions",
    "autres": "autres",
}


def summary_line(p: Prefill) -> str:
    parts = [f"{SHORT_KINDS[k]} {v / 100:.0f} €" for k, v in p.totals.items() if v]
    return ", ".join(parts) if parts else "aucune ressource trouvée dans tes relevés"


def spec_to_dict(s: Spec) -> Mapping[str, Any]:
    return {
        "kind": s.kind,
        "label": s.label,
        "organism": s.organism,
        "organism_name": s.organism_name,
        "cadence_months": s.cadence_months,
        "what": s.what,
        "consequence": s.consequence,
        "url": s.url,
        "source": s.source,
        "anchor": s.anchor,
        "anchor_label": s.anchor_label,
    }
