"""Comptes de l'État : aucun organisme n'envoie ses PDF ni n'ouvre d'API aux particuliers (constat d'octobre 2026).
Le mieux possible : un mail de notification sans pièce jointe (« votre avis est disponible ») devient une tâche
« document à récupérer », avec la page d'entrée de l'espace officiel, le document attendu et une échéance ; des
attentes de saison relancent si le document n'arrive pas ; la tâche se ferme seule quand le PDF est déposé dans
Papiers. Bank ne garde aucun identifiant d'un compte de l'État et ne s'y connecte jamais.

Module pur : aucune base, aucun réseau. Seuls l'expéditeur et l'objet d'un mail sont lus, jamais son texte. Pages
d'entrée vérifiées en direct le 24 octobre 2026 (les chemins après connexion ne peuvent pas l'être : ils sont donnés
comme repères).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.recurring import add_months

CHECKED_ON = "24 octobre 2026"
DUE_DAYS = 14  # un document annoncé par mail : à récupérer dans les deux semaines (repère de Bank, réglable plus tard)
RENEW_DAYS = 400  # un document de plus d'un an (et un mois) est « à renouveler »


@dataclass(frozen=True)
class Body:
    slug: str
    name: str
    domains: tuple[str, ...]
    entry_url: str  # page d'entrée de l'espace officiel (vérifiée)
    path: str  # où cliquer une fois connecté (repère, non vérifiable sans compte)
    doc_kind: str  # genre de papier dans le coffre qui ferme la tâche
    default_expected: str


BODIES: tuple[Body, ...] = (
    Body(
        "impots",
        "Impôts (espace particulier)",
        ("impots.gouv.fr", "dgfip.finances.gouv.fr"),
        "https://cfspart.impots.gouv.fr/",
        "Espace particulier › Documents (et « Mes évènements » sur l'accueil)",
        "tax_notice",
        "document de ton espace particulier",
    ),
    Body(
        "caf",
        "CAF",
        ("caf.fr",),
        "https://wwwd.caf.fr/wps/portal/caffr/login",
        "Mon Compte › Mes attestations",
        "caf",
        "attestation de paiement",
    ),
    Body(
        "france_travail",
        "France Travail",
        ("francetravail.fr", "pole-emploi.fr"),
        "https://candidat.francetravail.fr/espacepersonnel/",
        "Mes échanges et documents › Mes documents › Mes attestations",
        "france_travail",
        "attestation France Travail",
    ),
    Body(
        "ameli",
        "Assurance Maladie (ameli)",
        ("ameli.fr", "assurance-maladie.fr"),
        "https://assure.ameli.fr/",
        "Mes paiements (décomptes conservés 27 mois)",
        "health",
        "relevé de remboursements",
    ),
    Body(
        "retraite",
        "Retraite (info-retraite)",
        ("info-retraite.fr", "lassuranceretraite.fr", "agirc-arrco.fr"),
        "https://www.info-retraite.fr/",
        "Mon compte retraite › Ma carrière (FranceConnect)",
        "pension",
        "relevé de carrière",
    ),
)
BY_SLUG = {b.slug: b for b in BODIES}

# Un mail qui annonce un document (et pas une lettre d'information) : au moins un de ces mots dans l'objet.
_DOC_WORDS = re.compile(
    r"\b(avis|document|attestation|disponible|message|decompte|releve|courrier|notification|droits?|paiement|"
    r"remboursement|declaration|taux|nouveau|nouvelle)\b"
)
# (organisme, motif de l'objet replié, document attendu). Le premier qui correspond gagne.
_EXPECTED: tuple[tuple[str, str, str], ...] = (
    ("impots", r"taxe fonciere", "avis de taxe foncière"),
    ("impots", r"taxe d.habitation", "avis de taxe d'habitation"),
    ("impots", r"\bavis\b", "avis d'impôt sur le revenu"),
    ("impots", r"messagerie|\bmessage\b|reponse", "réponse dans ta messagerie sécurisée"),
    ("impots", r"\btaux\b|prelevement a la source", "avis de taux de prélèvement à la source"),
    ("caf", r"quotient", "attestation de quotient familial"),
    ("caf", r"droits?|vous informe|changement", "attestation de paiement (tes droits ont changé)"),
    ("caf", r"attestation", "attestation de paiement"),
    ("france_travail", r"fiscal", "attestation fiscale"),
    ("france_travail", r"prelevement a la source", "attestation de prélèvement à la source"),
    ("france_travail", r"paiement|attestation", "attestation de paiement"),
    ("ameli", r"decompte|remboursement|paiement|releve", "relevé de remboursements"),
    ("retraite", r"releve|carriere|situation", "relevé de situation individuelle"),
)
# Ces attentes ne se ferment pas d'elles-mêmes : la réponse se lit en ligne, sans PDF.
NO_PDF = frozenset({"réponse dans ta messagerie sécurisée"})


@dataclass(frozen=True)
class Notice:
    body: str
    expected: str
    due: date
    key: str


@dataclass(frozen=True)
class Expectation:
    key: str
    body: str
    expected: str
    opens: date  # la tâche apparaît
    due: date


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def body_of(domain: str) -> Body | None:
    d = domain.lower().strip()
    for b in BODIES:
        if any(d == x or d.endswith("." + x) for x in b.domains):
            return b
    return None


def notice_from_mail(domain: str, subject: str, received: date, message_id: str, has_pdf: bool) -> Notice | None:
    """Un mail d'un organisme, sans PDF joint, qui annonce un document : la tâche à créer. None sinon (PDF joint :
    il est déjà proposé dans Papiers ; lettre d'information : rien à récupérer)."""
    body = body_of(domain)
    if body is None or has_pdf:
        return None
    folded = fold(subject)
    if not _DOC_WORDS.search(folded):
        return None
    expected = next(
        (e for slug, pattern, e in _EXPECTED if slug == body.slug and re.search(pattern, folded)),
        body.default_expected,
    )
    ident = message_id or f"{domain}|{subject}|{received.isoformat()}"
    key = "mail:" + hashlib.sha256(ident.encode()).hexdigest()[:40]
    return Notice(body.slug, expected, received + timedelta(days=DUE_DAYS), key)


def seasonal(today: date, paid_months: dict[str, set[str]]) -> list[Expectation]:
    """Les attentes de saison du moment.

    * Avis d'impôt sur le revenu : il paraît en ligne pendant l'été, à une date qui dépend de ta déclaration
      (impots.gouv.fr, « Quelle est la date de mon avis d'impôt sur le revenu ? ») ; repère de Bank : la tâche
      apparaît le 20 juillet, l'avis est attendu au 31 août, puis rappelé jusqu'au 31 décembre.
    * Attestation de paiement CAF ou France Travail : chaque mois où un versement est arrivé, attendue avant la fin
      du mois suivant (`paid_months` : organisme → mois AAAA-MM des versements vus)."""
    out: list[Expectation] = []
    year = today.year
    opens, due = date(year, 7, 20), date(year, 8, 31)
    if opens <= today <= date(year, 12, 31):
        out.append(Expectation(f"season:avis_ir:{year}", "impots", f"avis d'impôt sur le revenu {year}", opens, due))
    last_month = add_months(today.replace(day=1), -1)
    key = f"{last_month.year:04d}-{last_month.month:02d}"
    for slug, label in (
        ("caf", "attestation de paiement CAF"),
        ("france_travail", "attestation de paiement France Travail"),
    ):
        if key in paid_months.get(slug, set()):
            month_start = today.replace(day=1)
            month_end = add_months(month_start, 1) - timedelta(days=1)
            out.append(
                Expectation(f"season:{slug}:{key}", slug, f"{label} de {last_month:%m/%Y}", month_start, month_end)
            )
    return out


def closes(expected: str, body: str, doc_kind: str) -> bool:
    """Ce papier déposé ferme-t-il la tâche ? Même organisme (genre de papier), sauf une réponse sans PDF."""
    spec = BY_SLUG.get(body)
    return spec is not None and expected not in NO_PDF and doc_kind == spec.doc_kind
