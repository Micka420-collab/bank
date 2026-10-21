"""Documents officiels : avis d'impôt, attestations CAF et France Travail -> faits utiles, rien d'autre.

Il n'existe pas d'API ouverte aux particuliers pour « connecter » impots.gouv, la CAF ou France Travail à une
application personnelle (FranceConnect est réservé aux services agréés). On lit donc les PDF que ces espaces
te laissent télécharger, on en extrait les quelques chiffres qui changent une décision, et on jette le reste :
numéro fiscal, NIR, numéro d'allocataire, adresse ne sont jamais conservés.
"""

from __future__ import annotations

import logging
import re
import subprocess  # nosec B404
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.domain import pdf_text

MAX_PAGES = pdf_text.MAX_PAGES
MAX_CHARS = pdf_text.MAX_CHARS
WALL_SECONDS = 20.0  # délai total de lecture d'un PDF (le processus enfant est tué au-delà)
MAX_PARALLEL = 2  # lectures simultanées : au-delà, on demande de réessayer plutôt que d'empiler des processus
_CHILD = Path(pdf_text.__file__)
_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
log = logging.getLogger("bank.documents")

DocumentKind = str  # avis_impot | caf | france_travail | inconnu


class DocumentError(ValueError):
    pass


def extract_text(pdf_bytes: bytes) -> str:
    """Texte des premières pages, extrait dans un processus enfant confiné (voir pdf_text).

    PDF chiffré, image sans texte, corrompu, trop lourd ou trop lent -> DocumentError, jamais une exception brute.
    """
    if not _SLOTS.acquire(timeout=5):
        raise DocumentError("Trop de documents en cours de lecture : réessaie dans un instant")
    try:
        # Commande fixe (interpréteur courant, mode isolé, script de Bank) ; le seul élément extérieur est le
        # contenu du PDF, transmis sur l'entrée standard. Environnement vide : aucun secret n'est hérité.
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [sys.executable, "-I", str(_CHILD)],
            input=pdf_bytes,
            capture_output=True,
            timeout=WALL_SECONDS,
            env={},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("lecture PDF interrompue après %.0f s", WALL_SECONDS)
        raise DocumentError("Lecture du PDF trop longue : document trop complexe") from exc
    finally:
        _SLOTS.release()
    if proc.returncode == pdf_text.EXIT_ENCRYPTED:
        raise DocumentError("PDF protégé par mot de passe : retire-le avant d'importer")
    if proc.returncode != pdf_text.EXIT_OK:
        # Seul le nom de la classe d'exception remonte de l'enfant (jamais le contenu du document).
        log.warning("lecture PDF refusée (code %s, %s)", proc.returncode, proc.stderr[-80:].decode("ascii", "replace"))
        raise DocumentError("Fichier PDF illisible")
    text = proc.stdout.decode("utf-8", "replace")[:MAX_CHARS]
    if len(text.strip()) < 40:
        raise DocumentError("Aucun texte dans ce PDF (scan ou image) : exporte le document depuis ton espace en ligne")
    return text


# ------------------------------------------------------------------ outils
# Un montant à la française : « 23 456 », « 23 456,78 », « 1.234.567 », « 23456 » — jamais tronqué à 3 chiffres.
_AMOUNT = r"(-?\d{1,3}(?:[   .]\d{3})*(?:[,.]\d{1,2})?|-?\d+(?:[,.]\d{1,2})?)(?!\d)"


def _cents(raw: str) -> int | None:
    s = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    if s.count(",") == 1:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3):
        s = s.replace(".", "")
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation:
        return None


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None


def _norm(text: str) -> str:
    """Majuscules sans accents, espaces normalisés, sur TOUT le texte (borné en amont par MAX_CHARS)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper().split())


def _find_amount(text: str, *labels: str, after: int = 120) -> int | None:
    for label in labels:
        m = re.search(label + rf"[^0-9\-]{{0,{after}}}" + _AMOUNT, text)
        if m:
            cents = _cents(m.group(1))
            if cents is not None:
                return cents
    return None


def _find_date(text: str, *labels: str, after: int = 60) -> date | None:
    for label in labels:
        m = re.search(label + rf"[^0-9]{{0,{after}}}(\d{{2}})/(\d{{2}})/(\d{{4}})", text)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                continue
    return None


# ------------------------------------------------------------------ détection
def detect_kind(text: str) -> DocumentKind:
    t = _norm(text)
    if (
        "REVENU FISCAL DE REFERENCE" in t
        or "AVIS D'IMPOT" in t
        or "IMPOT SUR LES REVENUS" in t
        or "IMPOT SUR LE REVENU" in t
    ):
        return "avis_impot"
    if ("ALLOCATIONS FAMILIALES" in t or "CAF" in t.split() or "CAF DE" in t or "CAF DU" in t) and (
        "ATTESTATION" in t or "DROITS" in t or "PAIEMENT" in t
    ):
        return "caf"
    if ("FRANCE TRAVAIL" in t or "POLE EMPLOI" in t) and ("ATTESTATION" in t or "ALLOCATION" in t):
        return "france_travail"
    return "inconnu"


# ------------------------------------------------------------------ résultats
@dataclass(frozen=True)
class ParsedDocument:
    kind: DocumentKind
    title: str
    fields: dict[str, Any] = field(default_factory=dict)  # faits gardés
    warnings: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)  # identifiants vus et jetés


_SENSITIVE: tuple[tuple[str, str], ...] = (
    ("numéro fiscal", r"NUMERO FISCAL|N° FISCAL|NUMERO FISCAL DE REFERENCE|IDENTIFIANT FISCAL"),
    ("numéro de sécurité sociale", r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),
    ("numéro d'allocataire", r"ALLOCATAIRE\s*(?:N°|NUMERO|:)"),
    ("identifiant France Travail", r"IDENTIFIANT\s+(?:FRANCE TRAVAIL|POLE EMPLOI)|N°\s*IDENTIFIANT"),
    ("adresse postale", r"\b\d{5}\s+[A-Z][A-Z\- ]{2,}\b"),
    ("IBAN", r"\bFR\d{2}(?:\s?[A-Z0-9]{4}){5}"),
)


def _discarded(t: str) -> list[str]:
    return [name for name, pattern in _SENSITIVE if re.search(pattern, t)]


# ------------------------------------------------------------------ avis d'impôt
def parse_avis_impot(text: str) -> ParsedDocument:
    t = _norm(text)
    fields: dict[str, Any] = {}
    warnings: list[str] = []
    year = None
    m = re.search(r"REVENUS (?:DE L'ANNEE |DE )?(20\d{2})", t) or re.search(
        r"IMPOT (?:SUR LES REVENUS|SUR LE REVENU) (20\d{2})", t
    )
    if m:
        year = int(m.group(1))
    else:
        m = re.search(r"AVIS D'IMPOT (20\d{2})", t)
        if m:
            year = int(m.group(1)) - 1
            warnings.append("Année des revenus déduite de l'année de l'avis.")
    if year is None:
        warnings.append("Année des revenus introuvable : indique-la avant de valider.")
    fields["incomes_year"] = year

    rfr = _find_amount(t, r"REVENU FISCAL DE REFERENCE")
    if rfr is None:
        warnings.append("Revenu fiscal de référence introuvable.")
    fields["rfr_cents"] = rfr
    fields["taxable_income_cents"] = _find_amount(t, r"REVENU NET IMPOSABLE", r"REVENU IMPOSABLE") or rfr
    parts_m = re.search(r"NOMBRE DE PARTS?[^0-9]{0,40}(\d{1,2}(?:[,.]\d{1,2})?)", t)
    fields["parts"] = str(_decimal(parts_m.group(1))) if parts_m else None
    if parts_m is None:
        warnings.append("Nombre de parts introuvable : 1 part sera supposée.")
    fields["income_tax_cents"] = _find_amount(
        t, r"IMPOT SUR LE REVENU NET", r"MONTANT DE L'IMPOT NET", r"IMPOT NET", r"TOTAL DE L'IMPOT NET", r"MONTANT NET"
    )
    rate_m = re.search(
        r"TAUX (?:DE PRELEVEMENT A LA SOURCE|PERSONNALISE|DU FOYER)[^0-9]{0,60}(\d{1,2}(?:[,.]\d{1,2})?)\s?%", t
    )
    fields["withholding_rate_percent"] = str(_decimal(rate_m.group(1))) if rate_m else None
    fields["declarants"] = 2 if re.search(r"DECLARANT 2|CONJOINT|MARIES|PACSES", t) else 1
    if rfr is not None and rfr > 50_000_000:
        warnings.append("Montant inhabituel : vérifie le revenu fiscal de référence lu.")
    title = f"Avis d'impôt sur les revenus {year}" if year else "Avis d'impôt"
    return ParsedDocument("avis_impot", title, fields, warnings, _discarded(t))


# ------------------------------------------------------------------ CAF
_CAF_PRESTATIONS: tuple[tuple[str, str], ...] = (
    (
        "aide_logement",
        r"AIDE PERSONNALISEE AU LOGEMENT|APL\b|ALLOCATION DE LOGEMENT(?: SOCIALE| FAMILIALE)?|ALS\b|ALF\b",
    ),
    ("prime_activite", r"PRIME D'ACTIVITE"),
    ("rsa", r"REVENU DE SOLIDARITE ACTIVE|\bRSA\b"),
    ("af", r"(?<!CAISSE D')(?<!CAISSE DES )ALLOCATIONS? FAMILIALES?(?! DU| DE| DES)"),
    ("asf", r"ALLOCATION DE SOUTIEN FAMILIAL|\bASF\b"),
    ("aah", r"ALLOCATION AUX ADULTES HANDICAPES|\bAAH\b"),
    ("paje", r"PRESTATION D'ACCUEIL DU JEUNE ENFANT|\bPAJE\b|ALLOCATION DE BASE"),
    ("ars", r"ALLOCATION DE RENTREE SCOLAIRE|\bARS\b"),
    ("cf", r"COMPLEMENT FAMILIAL"),
)


ALLOWANCES = ("ARE", "ASS")  # seules valeurs admises pour « allowance » (France Travail)

CAF_LABELS: dict[str, str] = {
    "aide_logement": "Aide au logement",
    "prime_activite": "Prime d'activité",
    "rsa": "RSA",
    "af": "Allocations familiales",
    "asf": "Allocation de soutien familial",
    "aah": "AAH",
    "paje": "PAJE",
    "ars": "Allocation de rentrée scolaire",
    "cf": "Complément familial",
}


def parse_caf(text: str) -> ParsedDocument:
    t = _norm(text)
    fields: dict[str, Any] = {"prestations": []}
    warnings: list[str] = []
    seen: set[str] = set()
    for code, pattern in _CAF_PRESTATIONS:
        # Un montant CAF est toujours suivi de sa devise : cela évite de lire un numéro de dossier comme un montant.
        for m in re.finditer(r"(" + pattern + r")[^0-9\n]{0,80}" + _AMOUNT + r"\s?(?:€|EUR\b|EUROS\b)", t):
            cents = _cents(m.group(2))
            if cents is None or cents <= 0 or cents > 500_000 or code in seen:
                continue
            seen.add(code)
            fields["prestations"].append({"code": code, "label": CAF_LABELS[code], "monthly_cents": cents})
    month = re.search(r"(?:MOIS DE|DROITS? (?:AU|DU|POUR)|PERIODE DU)\s+(?:\d{2}/)?(\d{2})/(\d{4})", t)
    fields["month"] = f"{month.group(2)}-{month.group(1)}" if month else None
    qf = re.search(r"QUOTIENT FAMILIAL[^0-9]{0,40}" + _AMOUNT, t)
    fields["quotient_familial_cents"] = _cents(qf.group(1)) if qf else None
    kids = re.search(r"(\d{1,2})\s+ENFANTS? A CHARGE", t)
    fields["children"] = int(kids.group(1)) if kids else None
    if not fields["prestations"]:
        warnings.append(
            "Aucune prestation avec montant reconnue : vérifie que c'est une attestation de paiement ou de droits."
        )
    return ParsedDocument("caf", "Attestation CAF", fields, warnings, _discarded(t))


# ------------------------------------------------------------------ France Travail
def parse_france_travail(text: str) -> ParsedDocument:
    t = _norm(text)
    fields: dict[str, Any] = {}
    warnings: list[str] = []
    daily = _find_amount(t, r"MONTANT (?:NET )?JOURNALIER", r"ALLOCATION JOURNALIERE", r"PAR JOUR")
    fields["daily_allowance_cents"] = daily
    fields["monthly_estimate_cents"] = round(daily * 30.4) if daily else None
    fields["end_of_rights"] = _find_date(t, r"FIN DE DROITS?", r"JUSQU'AU", r"DATE DE FIN") or None
    fields["end_of_rights"] = fields["end_of_rights"].isoformat() if fields["end_of_rights"] else None
    days_m = re.search(
        r"(?<![/\d])(\d{1,4})\s+JOURS? (?:RESTANTS?|D'INDEMNISATION RESTANTS?|DE DROITS? RESTANTS?)", t
    ) or re.search(r"JOURS? (?:RESTANTS?|D'INDEMNISATION RESTANTS?)[^0-9]{0,20}(\d{1,4})", t)
    fields["days_left"] = int(days_m.group(1)) if days_m else None
    if re.search(r"\bARE\b|AIDE AU RETOUR A L'EMPLOI", t):
        fields["allowance"] = "ARE"
    elif re.search(r"\bASS\b|SOLIDARITE SPECIFIQUE", t):
        fields["allowance"] = "ASS"
    else:
        fields["allowance"] = None
    if daily is None:
        warnings.append("Montant journalier introuvable.")
    if fields["end_of_rights"] is None and fields["days_left"] is None:
        warnings.append("Fin de droits introuvable.")
    return ParsedDocument("france_travail", "Attestation France Travail", fields, warnings, _discarded(t))


def parse_document(pdf_bytes: bytes) -> ParsedDocument:
    text = extract_text(pdf_bytes)
    kind = detect_kind(text)
    if kind == "avis_impot":
        return parse_avis_impot(text)
    if kind == "caf":
        return parse_caf(text)
    if kind == "france_travail":
        return parse_france_travail(text)
    return ParsedDocument(
        "inconnu",
        "Document non reconnu",
        {},
        ["Bank lit les avis d'impôt (impots.gouv), les attestations CAF (de paiement ou de droits) et France Travail."],
        _discarded(_norm(text)),
    )
