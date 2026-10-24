"""Coffre-fort : reconnaître un document, en extraire les valeurs utiles AVEC leur preuve (la ligne lue), dire
combien de temps le garder et quand il expire. Module pur : aucune base, aucun réseau.

Règle d'or : rien n'est inventé. Une valeur n'existe que si elle est lue dans le texte — la ligne où elle a été
lue est gardée comme preuve — ou si tu la saisis. Ce qui manque s'affiche « non trouvé ». Une incohérence entre
valeurs (net payé ≠ net avant impôt − prélèvement) baisse la confiance et s'affiche.

Pièces d'identité, titres de séjour et documents de santé sont « sensibles » : jamais envoyés à un modèle externe,
conservés seulement avec ton accord, et on n'en extrait que le type et la date d'expiration (jamais un numéro).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain import contracts
from app.domain import documents as official

# Durées minimales de conservation : Service-Public.fr, fiche F19134, vérifiée le 13 octobre 2026 (DILA).
RETENTION_SOURCE = {
    "title": "Service-Public.fr — durée de conservation des papiers (F19134)",
    "url": "https://www.service-public.gouv.fr/particuliers/vosdroits/F19134",
    "verified_on": "2026-10-13",
}
MONTHS = (
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
_MONTHS_FOLDED = ("JANVIER", "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOUT", "SEPTEMBRE", "OCTOBRE",
                  "NOVEMBRE", "DECEMBRE")  # fmt: skip
FIELD_TYPES = ("amount", "date", "month", "percent", "number", "text")
MAX_TEXT_FIELD = 200


# ============================================================================ texte
def fold(text: str) -> str:
    """Majuscules sans accents, espaces (y compris insécables) réduits : pour reconnaître, jamais pour afficher."""
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper().split())


@dataclass(frozen=True)
class Line:
    raw: str  # tel que lu (preuve affichée)
    folded: str


def lines_of(text: str) -> list[Line]:
    out = []
    for raw in text.splitlines():
        stripped = " ".join(raw.split())
        if stripped:
            out.append(Line(stripped[:MAX_TEXT_FIELD], fold(stripped)))
    return out


# Montant à la française : « 2 064,60 », « 2.064,60 », « 2064,60 », « -95,41 », « 1 969,19 € » ; deux décimales
# exigées pour ne jamais confondre avec une année, une quantité d'heures entières ou un numéro.
_AMOUNT = re.compile(r"(?<![\d,.])(-\s?)?(\d{1,3}(?:[ .]\d{3})+|\d+)[,.](\d{2})(?![\d])")
PERCENT = re.compile(r"(?<![\d,.])(\d{1,2}(?:[,.]\d{1,3})?)\s?%")
_DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
_DATE_TXT = re.compile(r"\b(\d{1,2})(?:ER)?\s+(" + "|".join(_MONTHS_FOLDED) + r")\s+(\d{4})\b")
_MONTH_TXT = re.compile(r"\b(" + "|".join(_MONTHS_FOLDED) + r")\s+(\d{4})\b")
_MONTH_NUM = re.compile(r"(?<![\d/])(\d{1,2})/(\d{4})(?!\d)")
_IBAN = re.compile(r"\b([A-Z]{2}\d{2})(?:\s?[A-Z0-9]{4}){3,7}\s?([A-Z0-9]{1,4})\b")
_BIC = re.compile(r"\b(?:BIC|SWIFT)\s*:?\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")


def amounts(folded: str) -> Iterator[tuple[int, int]]:
    """(centimes, position) de chaque montant de la ligne, dans l'ordre."""
    for m in _AMOUNT.finditer(folded):
        euros = int(m.group(2).replace(" ", "").replace(".", ""))
        cents = euros * 100 + int(m.group(3))
        yield (-cents if m.group(1) else cents), m.start()


_AMOUNT_LOOSE = re.compile(r"(?<![\d,.])(-\s?)?(\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.](\d{2}))?(?![\d,.])")


def amounts_loose(folded: str) -> Iterator[int]:
    """Montants avec ou sans centimes (« 23 456 € » sur un avis d'impôt) : pour vérifier une citation, pas pour
    lire un document (une année ou un numéro y ressemblent trop)."""
    for m in _AMOUNT_LOOSE.finditer(folded):
        euros = int(m.group(2).replace(" ", "").replace(".", ""))
        cents = euros * 100 + int(m.group(3) or 0)
        yield -cents if m.group(1) else cents


def _valid_date(year: int, month: int, day: int) -> date | None:
    if not 1990 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def dates(folded: str) -> Iterator[tuple[date, int]]:
    found: list[tuple[date, int]] = []
    for m in _DATE_NUM.finditer(folded):
        d = _valid_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            found.append((d, m.start()))
    for m in _DATE_TXT.finditer(folded):
        d = _valid_date(int(m.group(3)), _MONTHS_FOLDED.index(m.group(2)) + 1, int(m.group(1)))
        if d:
            found.append((d, m.start()))
    yield from sorted(found, key=lambda x: x[1])


def month_of(folded: str) -> str | None:
    """« 2026-09 » d'après « période du 01/09/2026 au 30/09/2026 », « septembre 2026 » ou « 09/2026 »."""
    period = re.search(r"\bDU\s+(\S+)\s+AU\s+(\S+(?:\s+\S+){0,2})", folded)
    if period:
        ends = list(dates(period.group(2)))
        if ends:
            return ends[0][0].strftime("%Y-%m")
    m = _MONTH_TXT.search(folded)
    if m and 1990 <= int(m.group(2)) <= 2100:
        return f"{m.group(2)}-{_MONTHS_FOLDED.index(m.group(1)) + 1:02d}"
    m = _MONTH_NUM.search(folded)
    if m and 1 <= int(m.group(1)) <= 12 and 1990 <= int(m.group(2)) <= 2100:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    return None


def month_label(value: str) -> str:
    year, month = value.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def mask_iban(prefix: str, suffix: str) -> str:
    return f"{prefix} •••• {suffix}"


# ============================================================================ émetteurs connus
_ISSUERS: tuple[tuple[str, str], ...] = (
    (r"\bEDF\b|ELECTRICITE DE FRANCE", "EDF"),
    (r"\bENGIE\b", "Engie"),
    (r"TOTAL ?ENERGIES", "TotalEnergies"),
    (r"\bENEDIS\b", "Enedis"),
    (r"\bGRDF\b", "GRDF"),
    (r"\bVEOLIA\b", "Veolia"),
    (r"\bSUEZ\b", "Suez"),
    (r"\bSAUR\b", "Saur"),
    (r"\bFREE\b(?! ?(?:DE|DU)\b)|FREE MOBILE", "Free"),
    (r"\bORANGE\b", "Orange"),
    (r"\bSOSH\b", "Sosh"),
    (r"\bSFR\b", "SFR"),
    (r"BOUYGUES TELECOM", "Bouygues Telecom"),
    (r"CAISSE D'ALLOCATIONS FAMILIALES|\bCAF\b", "CAF"),
    (r"FRANCE TRAVAIL|POLE EMPLOI", "France Travail"),
    (r"FINANCES PUBLIQUES|\bDGFIP\b|IMPOTS\.GOUV", "Finances publiques"),
    (r"ASSURANCE MALADIE|\bCPAM\b|\bAMELI\b", "Assurance Maladie"),
    (r"\bMSA\b|MUTUALITE SOCIALE AGRICOLE", "MSA"),
    (r"AGIRC.?ARRCO", "Agirc-Arrco"),
    (r"\bCARSAT\b|ASSURANCE RETRAITE", "Assurance retraite"),
    (r"\bMAIF\b", "MAIF"),
    (r"\bMACIF\b", "MACIF"),
    (r"\bMATMUT\b", "Matmut"),
    (r"\bAXA\b", "AXA"),
    (r"\bALLIANZ\b", "Allianz"),
    (r"\bGROUPAMA\b", "Groupama"),
    (r"\bGMF\b", "GMF"),
    (r"\bMMA\b", "MMA"),
    (r"CREDIT AGRICOLE", "Crédit Agricole"),
    (r"SOCIETE GENERALE", "Société Générale"),
    (r"BNP PARIBAS", "BNP Paribas"),
    (r"\bLCL\b", "LCL"),
    (r"CREDIT MUTUEL", "Crédit Mutuel"),
    (r"\bCIC\b", "CIC"),
    (r"BANQUE POPULAIRE", "Banque Populaire"),
    (r"CAISSE D'EPARGNE", "Caisse d'Épargne"),
    (r"BANQUE POSTALE", "La Banque Postale"),
    (r"BOURSORAMA|BOURSOBANK", "BoursoBank"),
    (r"\bFORTUNEO\b", "Fortuneo"),
    (r"HELLO ?BANK", "Hello bank!"),
    (r"\bREVOLUT\b", "Revolut"),
    (r"\bN26\b", "N26"),
)


def issuer_of(folded_text: str) -> str | None:
    for pattern, name in _ISSUERS:
        if re.search(pattern, folded_text):
            return name
    return None


# ============================================================================ types de documents
@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str


@dataclass(frozen=True)
class Retention:
    label: str  # ce que dit la règle, en clair
    years: int | None = None  # None : pas de date de fin fixe (jusqu'à la retraite, fin du bail…)
    rule: str = "document"  # document : date du document + années ; tax_year : 31/12 de l'année + années


@dataclass(frozen=True)
class KindSpec:
    key: str
    label: str
    sensitive: bool
    fields: tuple[FieldSpec, ...]
    retention: Retention
    signals: tuple[tuple[str, int], ...]
    expiry_field: str | None = None
    date_field: str | None = None
    official: str | None = None  # analyseur des documents officiels (« utiliser ces chiffres dans Bank »)


def _f(key: str, label: str, type_: str) -> FieldSpec:
    return FieldSpec(key, label, type_)


KINDS: dict[str, KindSpec] = {
    spec.key: spec
    for spec in (
        KindSpec(
            "payslip",
            "Bulletin de paie",
            False,
            (
                _f("periode", "Période", "month"),
                _f("employeur", "Employeur", "text"),
                _f("brut", "Salaire brut", "amount"),
                _f("net_avant_impot", "Net à payer avant impôt", "amount"),
                _f("pas_taux", "Taux du prélèvement à la source", "percent"),
                _f("pas_montant", "Impôt prélevé à la source", "amount"),
                _f("net_paye", "Net payé", "amount"),
                _f("net_social", "Montant net social", "amount"),
                _f("net_imposable", "Net imposable", "amount"),
                _f("date_paiement", "Date de paiement", "date"),
            ),
            Retention("Jusqu'à la liquidation de ta retraite"),
            (
                (r"BULLETIN DE (?:PAIE|SALAIRE)", 5),
                (r"NET A PAYER", 3),
                (r"MONTANT NET SOCIAL", 3),
                (r"SALAIRE (?:BRUT|DE BASE)", 2),
                (r"NET IMPOSABLE", 2),
                (r"COTISATIONS", 1),
                (r"PRELEVE(?:MENT)? A LA SOURCE", 1),
                (r"CONVENTION COLLECTIVE", 1),
            ),
            date_field="date_paiement",
        ),
        KindSpec(
            "tax_notice",
            "Avis d'impôt",
            False,
            (
                _f("annee_revenus", "Revenus de l'année", "number"),
                _f("rfr", "Revenu fiscal de référence", "amount"),
                _f("revenu_imposable", "Revenu net imposable", "amount"),
                _f("parts", "Nombre de parts", "number"),
                _f("impot_net", "Impôt net", "amount"),
                _f("taux_pas", "Taux de prélèvement à la source", "percent"),
            ),
            Retention("Jusqu'au 31 décembre de la 2ᵉ année suivant l'année d'imposition", 2, "tax_year"),
            (
                (r"REVENU FISCAL DE REFERENCE", 6),
                (r"AVIS D'IMPOT", 5),
                (r"IMPOT SUR LES REVENUS", 2),
                (r"NOMBRE DE PARTS", 2),
                (r"FINANCES PUBLIQUES", 2),
            ),
            official="avis_impot",
        ),
        KindSpec(
            "caf",
            "Attestation CAF",
            False,
            (_f("mois", "Mois", "month"), _f("quotient_familial", "Quotient familial", "amount")),
            Retention("5 ans (avis de paiement des prestations familiales)", 5),
            (
                (r"CAISSE D'ALLOCATIONS FAMILIALES", 5),
                (r"\bCAF\b", 2),
                (r"ATTESTATION DE (?:PAIEMENT|DROITS?)", 3),
                (r"PRIME D'ACTIVITE|AIDE PERSONNALISEE AU LOGEMENT|ALLOCATION DE LOGEMENT", 2),
                (r"QUOTIENT FAMILIAL", 2),
            ),
            official="caf",
        ),
        KindSpec(
            "france_travail",
            "Attestation France Travail",
            False,
            (
                _f("allocation_journaliere", "Allocation journalière", "amount"),
                _f("type_allocation", "Allocation", "text"),
                _f("jours_restants", "Jours d'indemnisation restants", "number"),
                _f("fin_droits", "Fin des droits", "date"),
            ),
            Retention("5 ans (justificatifs de paiement)", 5),
            (
                (r"FRANCE TRAVAIL|POLE EMPLOI", 4),
                (r"AIDE AU RETOUR A L'EMPLOI|\bARE\b", 2),
                (r"ALLOCATION JOURNALIERE|MONTANT (?:NET )?JOURNALIER", 3),
                (r"ATTESTATION", 1),
            ),
            expiry_field="fin_droits",
            official="france_travail",
        ),
        KindSpec(
            "invoice",
            "Facture",
            False,
            (
                _f("fournisseur", "Fournisseur", "text"),
                _f("montant_ttc", "Montant TTC", "amount"),
                _f("date_facture", "Date de la facture", "date"),
                _f("echeance", "À payer avant le", "date"),
                _f("consommation_kwh", "Consommation (kWh)", "number"),
            ),
            Retention("5 ans (énergie, eau, téléphone, internet)", 5),
            (
                (r"\bFACTURE\b", 4),
                (r"(?:TOTAL|MONTANT) TTC", 3),
                (r"\bTVA\b", 1),
                (r"A PAYER (?:AVANT|AU PLUS TARD)", 2),
                (r"\bKWH\b", 2),
                (r"ABONNEMENT", 1),
            ),
            date_field="date_facture",
        ),
        KindSpec(
            "rent_receipt",
            "Quittance de loyer",
            False,
            (
                _f("periode", "Période", "month"),
                _f("montant", "Montant payé", "amount"),
                _f("bailleur", "Bailleur", "text"),
            ),
            Retention("Durée de la location, puis 3 ans"),
            ((r"QUITTANCE DE LOYER", 6), (r"\bLOYER\b", 2), (r"BAILLEUR", 2), (r"CHARGES", 1)),
        ),
        KindSpec(
            "lease",
            "Bail / état des lieux",
            False,
            (
                _f("date_debut", "Prise d'effet", "date"),
                _f("loyer", "Loyer mensuel hors charges", "amount"),
                _f("charges", "Provision pour charges", "amount"),
                _f("depot_garantie", "Dépôt de garantie", "amount"),
                _f("revision", "Clause de révision", "text"),
                _f("preavis", "Préavis (mois)", "number"),
                _f("meuble", "Meublé", "text"),
                _f("assurance_exigee", "Assurance exigée", "text"),
                _f("assureur", "Assureur nommé", "text"),
                _f("bailleur", "Bailleur", "text"),
                _f("classe_dpe", "Classe énergie (DPE)", "text"),
            ),
            Retention("Durée de la location, puis 3 ans"),
            (
                (r"CONTRAT DE LOCATION|BAIL D'HABITATION", 6),
                (r"ETAT DES LIEUX", 5),
                (r"DEPOT DE GARANTIE", 3),
                (r"\bBAIL\b", 2),
                (r"LOCATAIRE", 1),
            ),
            date_field="date_debut",
        ),
        KindSpec(
            "bank_statement",
            "Relevé de compte",
            False,
            (_f("banque", "Banque", "text"), _f("periode", "Période", "month"), _f("solde", "Solde final", "amount")),
            Retention("5 ans à compter de chaque opération", 5),
            (
                (r"RELEVE DE COMPTE|RELEVE DES OPERATIONS", 6),
                (r"(?:ANCIEN|NOUVEAU) SOLDE", 3),
                (r"SOLDE (?:CREDITEUR|DEBITEUR)", 2),
                (r"TOTAL DES OPERATIONS", 2),
            ),
        ),
        KindSpec(
            "rib",
            "RIB",
            False,
            (
                _f("banque", "Banque", "text"),
                _f("iban_masque", "IBAN (masqué)", "text"),
                _f("bic", "BIC", "text"),
            ),
            Retention("Tant que le compte existe"),
            (
                (r"RELEVE D'IDENTITE BANCAIRE", 7),
                (r"\bRIB\b", 3),
                (r"\bIBAN\b", 2),
                (r"\bBIC\b", 2),
                (r"TITULAIRE DU COMPTE", 2),
                (r"DOMICILIATION", 1),
            ),
        ),
        KindSpec(
            "insurance",
            "Assurance",
            False,
            (
                _f("assureur", "Assureur", "text"),
                _f("garantie", "Garantie", "text"),
                _f("type", "Type", "text"),
                _f("cotisation", "Cotisation", "amount"),
                _f("periodicite", "Périodicité", "text"),
                _f("valable_jusqu", "Valable jusqu'au", "date"),
            ),
            Retention("Avis d'échéance : 2 ans ; contrat : 2 ans après sa fin", 2),
            (
                (r"ATTESTATION D'ASSURANCE", 6),
                (r"AVIS D'ECHEANCE", 5),
                (r"ASSURANCE (?:HABITATION|AUTO|MULTIRISQUE)", 3),
                (r"RESPONSABILITE CIVILE", 2),
                (r"\bASSURE\b|SOUSCRIPTEUR", 1),
                (r"COTISATION", 1),
            ),
            expiry_field="valable_jusqu",
        ),
        KindSpec(
            "pension",
            "Pension de retraite",
            False,
            (_f("periode", "Période", "month"), _f("montant", "Montant net", "amount")),
            Retention("À conserver sans limite"),
            ((r"PENSION DE RETRAITE|RETRAITE DE BASE", 5), (r"AGIRC.?ARRCO|CARSAT", 3), (r"RETRAITE", 1)),
        ),
        KindSpec(
            "identity",
            "Pièce d'identité",
            True,
            (_f("type_piece", "Type de pièce", "text"), _f("date_expiration", "Date d'expiration", "date")),
            Retention("Tant qu'elle est valable"),
            (
                (r"CARTE NATIONALE D'IDENTITE|IDENTITY CARD", 7),
                (r"\bPASSEPORT\b|\bPASSPORT\b", 6),
                (r"TITRE DE SEJOUR|RESIDENCE PERMIT", 7),
                (r"PERMIS DE CONDUIRE|DRIVING LICENCE", 6),
                (r"IDFRA|P<FRA|[A-Z0-9<]{25,}", 4),
                (r"REPUBLIQUE FRANCAISE", 1),
            ),
            expiry_field="date_expiration",
        ),
        KindSpec(
            "health",
            "Document de santé",
            True,
            (_f("date", "Date", "date"),),
            Retention("2 ans (décomptes de remboursement)", 2),
            (
                (r"ORDONNANCE", 5),
                (r"FEUILLE DE SOINS", 5),
                (r"DECOMPTE (?:DE|DES) (?:PRESTATIONS|REMBOURSEMENTS?)", 4),
                (r"COMPLEMENTAIRE SANTE|MUTUELLE", 2),
                (r"MEDECIN|PATIENT|PHARMACIE", 2),
                (r"ASSURANCE MALADIE", 1),
            ),
            date_field="date",
        ),
        KindSpec("other", "Autre document", False, (), Retention("Selon le document"), ()),
    )
}
SENSITIVE_KINDS = frozenset(k for k, s in KINDS.items() if s.sensitive)
CLASSIFY_THRESHOLD = 4


def classify(text: str) -> tuple[str, float]:
    """Type le plus probable et confiance (0 à 1). En dessous du seuil : « autre », à toi de préciser."""
    folded = fold(text[:50_000])
    scores: list[tuple[int, str]] = []
    for key, spec in KINDS.items():
        score = sum(weight for pattern, weight in spec.signals if re.search(pattern, folded))
        if score:
            scores.append((score, key))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < CLASSIFY_THRESHOLD:
        return "other", 0.3
    best, kind = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0
    confidence = min(0.97, 0.45 + 0.05 * best + 0.04 * (best - runner_up))
    return kind, round(confidence, 2)


# ============================================================================ extraction
@dataclass(frozen=True)
class Found:
    key: str
    value: Any  # centimes (int), « AAAA-MM-JJ », « AAAA-MM », « 4.5 », entier ou texte
    evidence: str | None  # la ligne lue
    confidence: float


Picker = Callable[[Line, re.Match[str]], Any]


def _amount_last(line: Line, m: re.Match[str]) -> int | None:
    found = list(amounts(line.folded))
    return abs(found[-1][0]) if found else None


def _amount_after(line: Line, m: re.Match[str]) -> int | None:
    after = [c for c, pos in amounts(line.folded) if pos >= m.end()]
    return abs(after[0]) if after else None


def _signed_balance(line: Line, m: re.Match[str]) -> int | None:
    found = list(amounts(line.folded))
    if not found:
        return None
    value = found[-1][0]
    return -abs(value) if "DEBIT" in line.folded else value


def _date_after(line: Line, m: re.Match[str]) -> str | None:
    after = [d for d, pos in dates(line.folded) if pos >= m.start()]
    return after[0].isoformat() if after else None


def _percent(line: Line, m: re.Match[str]) -> str | None:
    p = PERCENT.search(line.folded)
    if not p:
        return None
    value = Decimal(p.group(1).replace(",", "."))
    return str(value.normalize()) if 0 <= value <= 60 else None


def _month(line: Line, m: re.Match[str]) -> str | None:
    return month_of(line.folded)


def _number_after(line: Line, m: re.Match[str]) -> int | None:
    found = re.search(r"(?<![\d,.])(\d{1,3}(?:[ .]\d{3})*|\d+)(?![\d,])", line.folded[m.end() :])
    return int(found.group(1).replace(" ", "").replace(".", "")) if found else None


def _number_before(line: Line, m: re.Match[str]) -> int | None:
    found = re.findall(r"(?<![\d,.])(\d{1,3}(?: \d{3})*|\d+)\s*$", line.folded[: m.start()].rstrip())
    return int(found[-1].replace(" ", "")) if found else None


def _text_after(line: Line, m: re.Match[str]) -> str | None:
    raw_after = line.raw[min(len(line.raw), m.end()) :]
    value = raw_after.lstrip(" :.-–—").strip()
    return value[:80] if re.search(r"[A-Za-zÀ-ÿ]{2}", value) else None


def _employer(line: Line, m: re.Match[str]) -> str | None:
    before = line.raw[: m.start()].strip(" -–—:,;|")
    return before[:80] if re.search(r"[A-Za-zÀ-ÿ]{3}", before) else None


# « prélevé à la source » tel que l'OCR le lit parfois (« à le source », « a la source »)
_PAS = r"PRELEV\w*\s+A\s+L\w?\s+SOURCE|IMPOT SUR LE REVENU PRELEV|\bPAS\b.*%"


def _pas_amount(line: Line, m: re.Match[str]) -> int | None:
    label = re.search(r"\bMONTANT\b", line.folded)
    if label:
        after = [c for c, pos in amounts(line.folded) if pos >= label.end()]
        if after:
            return abs(after[0])
    return _amount_last(line, m)


def _const(value: str) -> Picker:
    return lambda line, m: value


def _irl_clause(line: Line, m: re.Match[str]) -> str | None:
    """« IRL » ou « IRL 2e trimestre 2025 » : la clause de révision et son trimestre de référence s'il est écrit."""
    q = contracts.quarter_in(line.folded)
    return f"IRL {contracts.quarter_label(q)}" if q else "IRL"


def _dpe_letter(line: Line, m: re.Match[str]) -> str | None:
    """« Classe énergie : F », « DPE classe G » : la lettre qui suit, seule (A à G), sinon rien (#34). « à définir »
    replié en « A DEFINIR » n'est pas une classe A."""
    found = re.match(
        r"[\s:=«\"(-]*(?:DU LOGEMENT\s*)?(?:EST\s*)?(?:CLASSE\s*)?[:=«\"(]?\s*(?:([B-G])\b|(A)\b(?!\s+[A-Z]))",
        line.folded[m.end() :],
    )
    return (found.group(1) or found.group(2)) if found else None


def _months_after(line: Line, m: re.Match[str]) -> int | None:
    found = re.search(r"\b(UN|DEUX|TROIS|SIX|[1-9]|1[0-2])\s*MOIS\b", line.folded[m.end() :])
    if not found:
        return None
    words = {"UN": 1, "DEUX": 2, "TROIS": 3, "SIX": 6}
    token = found.group(1)
    return words.get(token, int(token) if token.isdigit() else None)


def _insurer_in_line(line: Line, m: re.Match[str]) -> str | None:
    ins = contracts.insurer_of(line.folded)
    return ins.name if ins else None


def _insurance_type(line: Line, m: re.Match[str]) -> str | None:
    return contracts.insurance_type(line.folded)


@dataclass(frozen=True)
class Rule:
    key: str
    pattern: str
    pick: Picker
    exclude: str | None = None
    confidence: float = 0.9


_RULES: dict[str, tuple[Rule, ...]] = {
    "payslip": (
        Rule("net_avant_impot", r"NET (?:A PAYER )?AVANT IMPOT", _amount_last),
        Rule("net_paye", r"\bNET (?:PAYE|A PAYER)\b", _amount_last, exclude=r"AVANT"),
        Rule("net_social", r"(?:MONTANT )?NET SOCIAL", _amount_after),
        Rule("net_imposable", r"NET IMPOSABLE", _amount_after, exclude=r"^CUMUL"),
        Rule("brut", r"(?:SALAIRE|TOTAL|REMUNERATION) BRUTE?\b", _amount_last, exclude=r"FISCAL|CUMUL"),
        Rule("pas_taux", _PAS, _percent),
        Rule("pas_taux", r"TAUX (?:PERSONNALISE|NEUTRE|NON PERSONNALISE|DU FOYER)", _percent),
        Rule("pas_montant", _PAS, _pas_amount),
        Rule("periode", r"PERIODE|\bDU \d{1,2}/\d{1,2}/\d{4} AU|MOIS DE|BULLETIN DE (?:PAIE|SALAIRE)", _month),
        Rule("date_paiement", r"(?:DATE DE )?PAIEMENT|PAYE LE|VIREMENT LE|VERSE LE", _date_after),
        Rule("employeur", r"\bSIRET\b", _employer, confidence=0.7),
        Rule("employeur", r"EMPLOYEUR\s*:", _text_after, confidence=0.75),
    ),
    "invoice": (
        Rule("montant_ttc", r"(?:TOTAL|MONTANT) TTC|(?:TOTAL|MONTANT) A PAYER|NET A PAYER|MONTANT TOTAL", _amount_last),
        Rule("date_facture", r"DATE (?:DE (?:LA )?)?FACTURE|FACTURE DU|EMISE? LE|DATE D'EMISSION", _date_after),
        Rule(
            "echeance", r"AVANT LE|ECHEANCE|DATE LIMITE|PRELEVE(?:MENT)? (?:LE|A PARTIR DU)|SERA PRELEVE", _date_after
        ),
        Rule("consommation_kwh", r"\bKWH\b", _number_before, confidence=0.7),
    ),
    "rent_receipt": (
        Rule("montant", r"\bTOTAL\b|SOMME DE|MONTANT", _amount_last),
        Rule("montant", r"\bLOYER\b", _amount_last, confidence=0.75),
        Rule("periode", r"PERIODE|\bDU \d{1,2}/\d{1,2}/\d{4} AU|MOIS DE|LOYER", _month),
        Rule("bailleur", r"BAILLEUR\s*:|PROPRIETAIRE\s*:", _text_after, confidence=0.7),
    ),
    "lease": (
        Rule("date_debut", r"PREND(?:RA)? EFFET|A COMPTER DU|PRISE D'EFFET|DATE D'ENTREE", _date_after),
        Rule("loyer", r"LOYER (?:MENSUEL|HORS CHARGES|HC|PRINCIPAL)", _amount_after),
        Rule(
            "charges",
            r"PROVISIONS? (?:POUR|SUR) CHARGES|CHARGES (?:MENSUELLES|LOCATIVES|FORFAITAIRES|RECUPERABLES)",
            _amount_after,
            confidence=0.8,
        ),
        Rule("depot_garantie", r"DEPOT DE GARANTIE", _amount_after),
        Rule("revision", r"INDICE DE REFERENCE DES LOYERS|\bIRL\b", _irl_clause, confidence=0.8),
        Rule("preavis", r"PREAVIS|DELAI DE CONGE", _months_after, confidence=0.7),
        Rule(
            "meuble",
            r"LOCATION MEUBLEE|LOGEMENT MEUBLE|BAIL (?:DE LOCATION )?MEUBLE|\bMEUBLE\b",
            _const("oui"),
            exclude=r"NON MEUBLE|VIDE",
            confidence=0.7,
        ),
        Rule("meuble", r"LOGEMENT VIDE|LOCATION VIDE|LOCATION NUE|NON MEUBLE", _const("non"), confidence=0.7),
        Rule(
            "assurance_exigee",
            r"ASSUR\w*.*(?:OBLIGATOIRE|JUSTIFIER|ATTESTATION|SOUSCRIRE)|JUSTIFIER D'UNE ASSURANCE",
            _const("oui"),
            confidence=0.75,
        ),
        Rule("assureur", r"ASSUR", _insurer_in_line, confidence=0.7),
        Rule("bailleur", r"BAILLEUR\s*:|PROPRIETAIRE\s*:|LE BAILLEUR,?\s", _text_after, confidence=0.6),
        Rule(
            "classe_dpe",
            r"(?:CLASSE|ETIQUETTE|CLASSEMENT) (?:ENERGIE|ENERGETIQUE|DPE)|\bDPE\b\W*(?=CLASSE)",
            _dpe_letter,
            confidence=0.7,
        ),
    ),
    "bank_statement": (
        Rule("solde", r"NOUVEAU SOLDE|SOLDE (?:FINAL|AU \d)|SOLDE (?:CREDITEUR|DEBITEUR)", _signed_balance),
        Rule("periode", r"\bDU \d{1,2}/\d{1,2}/\d{4} AU|PERIODE|RELEVE", _month),
    ),
    "rib": (Rule("bic", r"\b(?:BIC|SWIFT)\b", lambda line, m: _bic(line)),),
    "insurance": (
        Rule("valable_jusqu", r"VALABLE JUSQU|JUSQU'AU|DATE D'ECHEANCE|EXPIRE|\bAU \d{1,2}/\d{1,2}/\d{4}", _date_after),
        Rule("cotisation", r"COTISATION|PRIME (?:ANNUELLE|TTC)|MONTANT TTC", _amount_last),
        Rule(
            "garantie",
            r"ASSURANCE (?:HABITATION|AUTO|MULTIRISQUE[A-Z ]*|SCOLAIRE|EMPRUNTEUR)",
            lambda line, m: m.group(0).title(),
        ),
        Rule(
            "type",
            r"HABITATION|MULTIRISQUE|\bMRH\b|\bAUTO\b|VEHICULE|EMPRUNTEUR|\bSANTE\b|MUTUELLE|PREVOYANCE",
            _insurance_type,
            confidence=0.75,
        ),
        Rule("periodicite", r"PAR MOIS|MENSUEL|MENSUALITE|\bMOIS\b", _const("mensuelle"), confidence=0.6),
        Rule("periodicite", r"PAR AN\b|ANNUEL|ANNUELLE|\bAN\b", _const("annuelle"), confidence=0.6),
        Rule(
            "assureur",
            r"ASSUR|MUTUELLE|\bMAIF\b|\bMACIF\b|\bAXA\b|\bALLIANZ\b|\bPACIFICA\b",
            _insurer_in_line,
            confidence=0.7,
        ),
    ),
    "pension": (
        Rule("montant", r"MONTANT NET|NET A PAYER|NET VERSE|PENSION NETTE", _amount_last),
        Rule("periode", r"PERIODE|MOIS DE|\bDU \d{1,2}/\d{1,2}/\d{4} AU|PENSION", _month),
    ),
    "identity": (
        Rule("type_piece", r"CARTE NATIONALE D'IDENTITE|IDENTITY CARD", _const("Carte d'identité")),
        Rule("type_piece", r"\bPASSEPORT\b|\bPASSPORT\b", _const("Passeport")),
        Rule("type_piece", r"TITRE DE SEJOUR|RESIDENCE PERMIT", _const("Titre de séjour")),
        Rule("type_piece", r"PERMIS DE CONDUIRE|DRIVING LICENCE", _const("Permis de conduire")),
        Rule("date_expiration", r"EXPIR|VALABLE JUSQU|DATE DE FIN DE VALIDITE|VALID UNTIL", _date_after),
    ),
    "health": (Rule("date", r"\bLE \d|DATE|DU \d", _date_after, confidence=0.6),),
}


def _bic(line: Line) -> str | None:
    m = _BIC.search(line.folded)
    return m.group(1) if m else None


def _apply_rules(kind: str, lines: list[Line]) -> list[Found]:
    out: dict[str, Found] = {}
    for rule in _RULES.get(kind, ()):
        if rule.key in out:
            continue
        rx = re.compile(rule.pattern)
        for line in lines:
            m = rx.search(line.folded)
            if not m or (rule.exclude and re.search(rule.exclude, line.folded)):
                continue
            value = rule.pick(line, m)
            if value is None or value == "":
                continue
            out[rule.key] = Found(rule.key, value, line.raw, rule.confidence)
            break
    return list(out.values())


def _line_with(lines: list[Line], *needles: str) -> str | None:
    """Première ligne qui contient l'une des formes (repliées) d'une valeur : la preuve d'une valeur officielle."""
    for line in lines:
        compact = re.sub(r"(?<=\d)[ .](?=\d{3}\b)", "", line.folded)
        if any(n and (n in line.folded or n in compact) for n in needles):
            return line.raw
    return None


def _amount_needles(cents: int) -> tuple[str, ...]:
    euros, rest = divmod(abs(cents), 100)
    plain = str(euros)
    return (f"{plain},{rest:02d}", f"{plain}.{rest:02d}", plain) if rest else (plain,)


def _official(kind: str, text: str, lines: list[Line]) -> tuple[list[Found], list[str]]:
    """Avis d'impôt, CAF, France Travail : les analyseurs éprouvés des documents officiels, avec leur preuve."""
    if kind == "tax_notice":
        parsed = official.parse_avis_impot(text)
        mapping = {
            "incomes_year": ("annee_revenus", "number"),
            "rfr_cents": ("rfr", "amount"),
            "taxable_income_cents": ("revenu_imposable", "amount"),
            "parts": ("parts", "number"),
            "income_tax_cents": ("impot_net", "amount"),
            "withholding_rate_percent": ("taux_pas", "percent"),
        }
    elif kind == "caf":
        parsed = official.parse_caf(text)
        mapping = {"month": ("mois", "month"), "quotient_familial_cents": ("quotient_familial", "amount")}
    else:
        parsed = official.parse_france_travail(text)
        mapping = {
            "daily_allowance_cents": ("allocation_journaliere", "amount"),
            "allowance": ("type_allocation", "text"),
            "days_left": ("jours_restants", "number"),
            "end_of_rights": ("fin_droits", "date"),
        }
    found: list[Found] = []
    for src, (key, type_) in mapping.items():
        value = parsed.fields.get(src)
        if value is None:
            continue
        if key == "parts":
            value = str(value)
        needles: tuple[str, ...]
        if type_ == "amount":
            needles = _amount_needles(int(value))
        elif type_ == "date":
            d = date.fromisoformat(str(value))
            needles = (d.strftime("%d/%m/%Y"),)
        elif type_ == "month":
            year, month = str(value).split("-")
            needles = (f"{month}/{year}", f"{_MONTHS_FOLDED[int(month) - 1]} {year}")
        else:
            needles = (fold(str(value)).replace(".", ","), fold(str(value)))
        evidence = _line_with(lines, *needles)
        found.append(Found(key, value, evidence, 0.9 if evidence else 0.7))
    if kind == "caf":
        for p in parsed.fields.get("prestations") or []:
            needles = _amount_needles(int(p["monthly_cents"]))
            found.append(Found(f"caf_{p['code']}", int(p["monthly_cents"]), _line_with(lines, *needles), 0.85))
    return found, list(parsed.warnings)


def _consistency(kind: str, found: list[Found]) -> tuple[list[Found], list[str]]:
    by = {f.key: f for f in found}
    warnings: list[str] = []
    suspicious: set[str] = set()
    if kind == "payslip":
        avant, pas, paye = (by.get(k) for k in ("net_avant_impot", "pas_montant", "net_paye"))
        if avant and pas and paye and abs(avant.value - pas.value - paye.value) > 100:
            warnings.append(
                "Le net payé ne correspond pas au net avant impôt moins le prélèvement : acompte, avance ou "
                "erreur de lecture ? Vérifie ces trois montants."
            )
            suspicious |= {"net_avant_impot", "pas_montant", "net_paye"}
        brut, net = by.get("brut"), by.get("net_avant_impot")
        if brut and net and brut.value < net.value:
            warnings.append("Le brut lu est inférieur au net : vérifie le salaire brut.")
            suspicious.add("brut")
    if suspicious:
        found = [Found(f.key, f.value, f.evidence, min(f.confidence, 0.5)) if f.key in suspicious else f for f in found]
    return found, warnings


@dataclass(frozen=True)
class Reading:
    kind: str
    kind_confidence: float
    fields: list[Found]
    document_date: date | None
    expires_on: date | None
    issuer: str | None
    title: str
    warnings: list[str] = field(default_factory=list)


def extract(kind: str, text: str) -> tuple[list[Found], list[str]]:
    lines = lines_of(text)
    folded_text = fold(text[:50_000])
    if KINDS[kind].official:
        found, warnings = _official(kind, text, lines)
    else:
        found, warnings = _apply_rules(kind, lines), []
    issuer = issuer_of(folded_text)
    issuer_key = {"invoice": "fournisseur", "bank_statement": "banque", "rib": "banque", "insurance": "assureur"}.get(
        kind
    )
    if issuer_key and issuer:
        found.append(Found(issuer_key, issuer, _line_with(lines, fold(issuer)), 0.8))
    if kind == "rib":
        m = _IBAN.search(folded_text)
        if m:
            found.append(Found("iban_masque", mask_iban(m.group(1), m.group(2)), None, 0.9))  # jamais l'IBAN entier
    found, more = _consistency(kind, found)
    return found, warnings + more


def document_date_of(kind: str, found: list[Found], text: str) -> date | None:
    by = {f.key: f.value for f in found}
    key = KINDS[kind].date_field
    if key and by.get(key):
        return date.fromisoformat(str(by[key]))
    for month_key in ("periode", "mois"):
        if by.get(month_key):
            year, month = (int(x) for x in str(by[month_key]).split("-"))
            return date(year, month, 1)
    for _d, _pos in dates(fold(text[:5_000])):
        return _d
    return None


def expiry_of(kind: str, found: list[Found]) -> date | None:
    key = KINDS[kind].expiry_field
    value = next((f.value for f in found if f.key == key), None) if key else None
    return date.fromisoformat(str(value)) if value else None


def retention_until(kind: str, document_date: date | None, fields: dict[str, Any] | None = None) -> date | None:
    rule = KINDS[kind].retention
    if rule.years is None:
        return None
    if rule.rule == "tax_year":
        incomes = (fields or {}).get("annee_revenus")
        if incomes:  # avis de l'année N+1 sur les revenus de N : gardé jusqu'au 31/12 de N+1+2
            return date(int(incomes) + 1 + rule.years, 12, 31)
        return date(document_date.year + rule.years, 12, 31) if document_date else None
    if document_date is None:
        return None
    try:
        return document_date.replace(year=document_date.year + rule.years)
    except ValueError:  # 29 février
        return document_date.replace(year=document_date.year + rule.years, day=28)


def title_of(kind: str, found: list[Found], issuer: str | None, document_date: date | None, filename: str) -> str:
    by = {f.key: f.value for f in found}
    label = KINDS[kind].label
    month = by.get("periode") or by.get("mois")
    when = month_label(str(month)) if month else (_date_label(document_date) if document_date else "")
    if kind == "tax_notice" and by.get("annee_revenus"):
        return f"Avis d'impôt — revenus {by['annee_revenus']}"
    if kind == "identity" and by.get("type_piece"):
        return str(by["type_piece"])
    if kind == "other":
        stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", filename).replace("_", " ").replace("-", " ").strip()
        return (" ".join(stem.split())[:80]) or label
    who = f" {issuer}" if issuer and kind in ("invoice", "bank_statement", "rib", "insurance") else ""
    return f"{label}{who}" + (f" — {when}" if when else "")


def _date_label(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def read(text: str, filename: str = "", kind: str | None = None) -> Reading:
    """Tout ce que Bank comprend d'un texte : type (sauf s'il est imposé), valeurs avec preuves, dates, titre."""
    if kind is None:
        kind, kind_confidence = classify(text)
    else:
        kind_confidence = 1.0
    found, warnings = extract(kind, text)
    issuer = issuer_of(fold(text[:50_000]))
    doc_date = document_date_of(kind, found, text)
    return Reading(
        kind=kind,
        kind_confidence=kind_confidence,
        fields=found,
        document_date=doc_date,
        expires_on=expiry_of(kind, found),
        issuer=issuer,
        title=title_of(kind, found, issuer, doc_date, filename),
        warnings=warnings,
    )


# ============================================================================ saisie et correction
def parse_value(type_: str, raw: Any) -> Any:
    """Valeur saisie par toi, contrôlée selon son type ; ValueError avec un message clair sinon."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise ValueError("Valeur vide")
    if type_ == "amount":
        s = text.replace("€", "").replace("EUR", "").replace(" ", "").replace(" ", "").replace(" ", "")
        if s.count(",") == 1:
            s = s.replace(".", "").replace(",", ".")
        try:
            value = Decimal(s)
        except InvalidOperation as exc:
            raise ValueError("Montant illisible : écris par exemple 2 064,60") from exc
        if not Decimal(0) <= abs(value) <= Decimal(100_000_000):
            raise ValueError("Montant hors limites")
        return int((value * 100).to_integral_value())
    if type_ == "date":
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
        d = (
            _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if m
            else next((x for x, _ in dates(fold(text))), None)
        )
        if d is None:
            raise ValueError("Date illisible : JJ/MM/AAAA")
        return d.isoformat()
    if type_ == "month":
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
        month = f"{m.group(1)}-{int(m.group(2)):02d}" if m and 1 <= int(m.group(2)) <= 12 else month_of(fold(text))
        if month is None:
            raise ValueError("Mois illisible : MM/AAAA")
        return month
    if type_ == "percent":
        try:
            pct = Decimal(text.replace("%", "").replace(",", ".").strip())
        except InvalidOperation as exc:
            raise ValueError("Pourcentage illisible") from exc
        if not Decimal(0) <= pct <= Decimal(100):
            raise ValueError("Pourcentage hors limites")
        return str(pct.normalize())
    if type_ == "number":
        try:
            number = Decimal(text.replace(",", ".").replace(" ", ""))
        except InvalidOperation as exc:
            raise ValueError("Nombre illisible") from exc
        if not Decimal(0) <= number <= Decimal(10_000_000):
            raise ValueError("Nombre hors limites")
        return int(number) if number == number.to_integral_value() else str(number.normalize())
    if type_ == "text":
        if re.search(r"[\x00-\x1f\x7f]", text):
            raise ValueError("Caractères non autorisés")
        return " ".join(text.split())[:MAX_TEXT_FIELD]
    raise ValueError("Type de champ inconnu")


def field_spec(kind: str, key: str) -> FieldSpec | None:
    spec = next((f for f in KINDS[kind].fields if f.key == key), None)
    if spec is None and kind == "caf" and key.startswith("caf_") and key[4:] in official.CAF_LABELS:
        return FieldSpec(key, official.CAF_LABELS[key[4:]] + " (par mois)", "amount")
    return spec
