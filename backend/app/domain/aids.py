"""Aides de l'État : reconnaissance des versements (CAF, France Travail, CPAM, MSA, DGFIP…),
calendrier attendu, alertes de retard / variation, et écart avec les droits estimés (non-recours).

Rappel utile : en France, une part importante des aides auxquelles les foyers ont droit n'est jamais
réclamée. Comparer « ce que tu touches » à « ce que le moteur officiel OpenFisca estime » est
précisément ce que cette brique automatise.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from app.domain.normalize import searchable
from app.domain.recurring import add_months


@dataclass(frozen=True)
class Organism:
    code: str
    name: str
    patterns: tuple[str, ...]
    usual_day: int | None  # jour de versement habituel, si connu
    portal: str


ORGANISMS: tuple[Organism, ...] = (
    Organism(
        "caf",
        "CAF",
        (r"\bCAF\b", r"\bC\.A\.F\b", r"CAISSE D.?ALLOC", r"ALLOCATIONS? FAMILIALES"),
        5,
        "https://caf.fr",
    ),
    Organism(
        "france_travail",
        "France Travail",
        (r"FRANCE TRAVAIL", r"POLE EMPLOI", r"\bFT\b.*\bARE\b"),
        None,
        "https://www.francetravail.fr",
    ),
    Organism("msa", "MSA", (r"\bMSA\b", r"MUTUALITE SOCIALE AGRICOLE"), 5, "https://www.msa.fr"),
    Organism(
        "cpam",
        "Assurance Maladie",
        (r"\bCPAM\b", r"AMELI", r"ASSURANCE MALADIE", r"CAISSE PRIMAIRE"),
        None,
        "https://www.ameli.fr",
    ),
    Organism(
        "dgfip",
        "Impôts (remboursements, crédits d'impôt)",
        (
            r"\bDGFIP\b",
            r"\bDRFIP\b",
            r"FINANCES PUBLIQUES",
            r"TRESOR PUBLIC",
            r"REMB\w* IMPOT",
            r"CREDIT D.?IMPOT",
        ),
        None,
        "https://www.impots.gouv.fr",
    ),
    Organism(
        "retraite",
        "Retraite",
        (r"\bCARSAT\b", r"\bCNAV\b", r"AGIRC", r"ARRCO", r"\bCNRACL\b"),
        9,
        "https://www.info-retraite.fr",
    ),
    Organism("crous", "CROUS / bourse", (r"\bCROUS\b", r"BOURSE"), 5, "https://www.messervices.etudiant.gouv.fr"),
    Organism(
        "departement",
        "Département (APA, PCH…)",
        (r"CONSEIL DEPARTEMENTAL", r"\bAPA\b", r"\bPCH\b"),
        None,
        "https://www.service-public.fr",
    ),
)


@dataclass(frozen=True)
class AidKind:
    code: str
    organism: str
    name: str
    patterns: tuple[str, ...]
    openfisca: str | None = None  # variable OpenFisca correspondante


AID_KINDS: tuple[AidKind, ...] = (
    AidKind("caf_ppa", "caf", "Prime d'activité", (r"PRIME D?.?ACTIVITE", r"\bPPA\b"), "ppa"),
    AidKind("caf_rsa", "caf", "RSA", (r"\bRSA\b",), "rsa"),
    AidKind(
        "caf_logement",
        "caf",
        "Aide au logement (APL/ALS/ALF)",
        (r"\bAPL\b", r"\bALS\b", r"\bALF\b", r"ALLOC\w* LOGEMENT", r"AIDE AU LOGEMENT"),
        "aide_logement",
    ),
    AidKind("caf_af", "caf", "Allocations familiales", (r"ALLOCATIONS FAMILIALES", r"\bAF\b"), "af"),
    AidKind("caf_asf", "caf", "Allocation de soutien familial", (r"\bASF\b", r"SOUTIEN FAMILIAL"), "asf"),
    AidKind("caf_aah", "caf", "AAH", (r"\bAAH\b",), None),
    AidKind("caf_paje", "caf", "PAJE", (r"\bPAJE\b", r"\bCMG\b"), None),
    AidKind("caf_autre", "caf", "Autre prestation CAF", (), None),
    AidKind("ft_are", "france_travail", "Allocation chômage (ARE)", (r"\bARE\b", r"ALLOCATION"), None),
    AidKind("ft_autre", "france_travail", "Autre versement France Travail", (), None),
    AidKind("msa_prestation", "msa", "Prestation MSA", (), None),
    AidKind("cpam_rembt", "cpam", "Remboursement de soins", (), None),
    AidKind("dgfip_rembt", "dgfip", "Remboursement / crédit d'impôt", (), None),
    AidKind("retraite_pension", "retraite", "Pension de retraite", (), None),
    AidKind("crous_bourse", "crous", "Bourse", (), None),
    AidKind("departement_aide", "departement", "Aide départementale", (), None),
)
_KIND_BY_CODE = {k.code: k for k in AID_KINDS}
_DEFAULT_KIND = {
    "caf": "caf_autre",
    "france_travail": "ft_autre",
    "msa": "msa_prestation",
    "cpam": "cpam_rembt",
    "dgfip": "dgfip_rembt",
    "retraite": "retraite_pension",
    "crous": "crous_bourse",
    "departement": "departement_aide",
}
_COMPILED_ORG = [(o, [re.compile(p) for p in o.patterns]) for o in ORGANISMS]
_COMPILED_KIND = [(k, [re.compile(p) for p in k.patterns]) for k in AID_KINDS if k.patterns]
# Aides de subsistance (comptées dans les revenus réguliers) vs remboursements ponctuels.
RECURRING_ORGS = {"caf", "france_travail", "msa", "retraite", "crous", "departement"}


def organism_of(kind_code: str) -> str:
    return _KIND_BY_CODE[kind_code].organism


def income_category_for(kind_code: str) -> str:
    """Catégorie budgétaire d'un versement public : revenu régulier ou remboursement ponctuel."""
    return "aides" if organism_of(kind_code) in RECURRING_ORGS else "remboursements"


def classify_aid(raw_label: str, amount_cents: int) -> str | None:
    """Retourne le code d'aide (ex. 'caf_ppa') pour un crédit venant d'un organisme public, sinon None."""
    if amount_cents <= 0:
        return None
    text = searchable(raw_label)
    org = next((o for o, pats in _COMPILED_ORG if any(p.search(text) for p in pats)), None)
    if org is None:
        return None
    for kind, pats in _COMPILED_KIND:
        if kind.organism == org.code and any(p.search(text) for p in pats):
            return kind.code
    return _DEFAULT_KIND[org.code]


@dataclass(frozen=True)
class AidPayment:
    date: date
    amount_cents: int
    kind: str


@dataclass(frozen=True)
class AidStream:
    kind: str
    name: str
    organism: str
    organism_name: str
    portal: str
    monthly_totals: dict[str, int]  # "YYYY-MM" -> centimes
    average_3m_cents: int
    last_date: date
    last_amount_cents: int
    next_expected: date | None
    status: str  # ok | late | changed | ended
    message: str | None


@dataclass(frozen=True)
class AidGap:
    kind: str
    name: str
    estimated_cents: int
    received_cents: int
    gap_cents: int
    message: str


@dataclass
class AidsSummary:
    streams: list[AidStream] = field(default_factory=list)
    total_last_12m_cents: int = 0
    monthly_average_cents: int = 0
    alerts: list[str] = field(default_factory=list)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def summarize_aids(payments: Iterable[AidPayment], today: date) -> AidsSummary:
    cutoff = add_months(today.replace(day=1), -12)
    grouped: dict[str, list[AidPayment]] = defaultdict(list)
    for p in payments:
        if p.date >= cutoff and p.date <= today and p.kind in _KIND_BY_CODE:
            grouped[p.kind].append(p)

    summary = AidsSummary()
    for code, items in grouped.items():
        items.sort(key=lambda p: p.date)
        kind = _KIND_BY_CODE[code]
        org = next(o for o in ORGANISMS if o.code == kind.organism)
        totals: dict[str, int] = defaultdict(int)
        for p in items:
            totals[_month_key(p.date)] += p.amount_cents
        # Moyenne sur les 3 derniers mois connus : on inclut le mois courant s'il a déjà été versé.
        offset = 0 if _month_key(today) in totals else 1
        recent_months = [_month_key(add_months(today.replace(day=1), -(offset + i))) for i in range(3)]
        avg3 = round(mean(totals.get(m, 0) for m in recent_months))
        last = items[-1]
        summary.total_last_12m_cents += sum(p.amount_cents for p in items)

        next_expected: date | None = None
        status, message = "ok", None
        is_monthly = org.code in RECURRING_ORGS and len({_month_key(p.date) for p in items}) >= 2
        if is_monthly:
            day = org.usual_day or round(mean(p.date.day for p in items[-3:]))
            candidate = add_months(last.date.replace(day=1), 1, day)
            next_expected = candidate
            if today > candidate + timedelta(days=4):
                if today > add_months(candidate, 1) + timedelta(days=4):
                    status, message = (
                        "ended",
                        f"{kind.name} : plus de versement depuis le {last.date:%d/%m/%Y}.",
                    )
                else:
                    status = "late"
                    message = (
                        f"{kind.name} : versement attendu vers le {candidate:%d/%m} non reçu. "
                        f"Vérifie ton espace {org.name} (déclaration trimestrielle, pièce manquante ?)."
                    )
            elif len(items) >= 2:
                prev = [p.amount_cents for p in items[:-1]][-3:]
                ref = mean(prev)
                if ref and abs(last.amount_cents - ref) / ref > 0.2:
                    status = "changed"
                    sign = "hausse" if last.amount_cents > ref else "baisse"
                    message = (
                        f"{kind.name} : {sign} de {abs(last.amount_cents - ref) / ref:.0%} sur le dernier versement."
                    )
        if message:
            summary.alerts.append(message)
        summary.streams.append(
            AidStream(
                kind=code,
                name=kind.name,
                organism=org.code,
                organism_name=org.name,
                portal=org.portal,
                monthly_totals=dict(sorted(totals.items())),
                average_3m_cents=avg3,
                last_date=last.date,
                last_amount_cents=last.amount_cents,
                next_expected=next_expected,
                status=status,
                message=message,
            )
        )
    summary.streams.sort(key=lambda s: -s.average_3m_cents)
    summary.monthly_average_cents = round(summary.total_last_12m_cents / 12)
    return summary


def compute_gaps(
    estimates: Mapping[str, int], streams: Sequence[AidStream], threshold_cents: int = 1_000
) -> list[AidGap]:
    """Compare les droits estimés (variable OpenFisca -> centimes/mois) aux montants reçus (moyenne 3 mois)."""
    received = {s.kind: s.average_3m_cents for s in streams}
    gaps: list[AidGap] = []
    for kind in AID_KINDS:
        if kind.openfisca is None or kind.openfisca not in estimates:
            continue
        est = estimates[kind.openfisca]
        got = received.get(kind.code, 0)
        gap = est - got
        if gap >= threshold_cents:
            msg = (
                f"Droit estimé à {est / 100:.0f} €/mois de {kind.name}, tu en perçois {got / 100:.0f} €. "
                "À vérifier sur le simulateur officiel puis à demander si confirmé."
            )
            gaps.append(AidGap(kind.code, kind.name, est, got, gap, msg))
        elif gap <= -threshold_cents and est > 0:
            msg = (
                f"Tu perçois {got / 100:.0f} €/mois de {kind.name}, l'estimation n'en prévoit que {est / 100:.0f} €. "
                "Vérifie que ta situation déclarée (revenus, foyer) est à jour pour éviter un rappel."
            )
            gaps.append(AidGap(kind.code, kind.name, est, got, gap, msg))
        elif gap <= -threshold_cents and est == 0:
            msg = (
                f"Tu perçois {got / 100:.0f} €/mois de {kind.name} mais la simulation n'en prévoit plus : "
                "ta situation a peut-être changé — pense à la déclarer pour éviter un indu."
            )
            gaps.append(AidGap(kind.code, kind.name, est, got, gap, msg))
    return sorted(gaps, key=lambda g: -abs(g.gap_cents))
