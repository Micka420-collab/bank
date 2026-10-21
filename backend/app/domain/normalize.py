"""Normalisation des libellés bancaires français -> clé marchand stable.

« PAIEMENT PAR CARTE X1234 LECLERC ORLEANS 15/10 », « CB LECLERC 0510 ORLEANS » et
« FACTURE CARTE DU 151024 LECLERC ORLEANS CARTE 4974XXXXXXXX1234 » donnent tous « LECLERC ORLEANS ».
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_MAX_LABEL = 500

_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b")
_CARD = re.compile(
    r"\bCARTE\s+(?:NUMERO\s+)?[X*\d]{4,19}\b|\b\d{4}X{2,}\d{2,4}\b|\bX{1,}\d{4}\b|\bCB\s*\*\s*\d+\b|\bCB\d{2,}\b"
)
_TAIL_MARKERS = re.compile(
    r"(?:\s|/)(?:ECH|ID\s+EMETTEUR|MDT|REF|RUM|ICS|LIB|MOTIF|NUM|N°|ID|DATE|REFERENCE)\s*[/:.]"
    r"|\s/(?:MOTIF|REF|LIB|ID)\b"
)
_FROM = re.compile(r"(?:/DE\s+|\bDE:\s*)([^/]+)")
_PREFIXES = re.compile(
    r"^(?:PAIEMENT\s+(?:PAR\s+)?CARTE|PAIEMENT\s+CB|ACHAT\s+CB|ACHAT|FACTURE\s+CARTE(?:\s+DU\s+\d+)?"
    r"|AVOIR\s+(?:CARTE|CB)|REMBOURSEMENT\s+CB|CARTE|CB|"
    r"PRLV\s+SEPA|PRELEVEMENT(?:\s+SEPA)?(?:\s+EUROPEEN)?|PRLV|"
    r"VIR(?:EMENT)?\s+(?:SEPA\s+)?(?:INST(?:ANTANE)?\s+)?(?:RECU|EMIS|PERMANENT)?|VIR(?:EMENT)?|"
    r"RETRAIT\s+(?:DAB|GAB)|RETRAIT|ECHEANCE\s+PRET|REMISE\s+CHEQUE|CHEQUE|CHQ|DE|A|POUR|DU)\b[\s:.\-/]*"
)
_AGGREGATOR = re.compile(r"\b(?:PAYPAL|SUMUP|ZETTLE|IZETTLE|STRIPE|SQ|LYDIA)\b[^*]*\*\s*([A-Z0-9&][A-Z0-9& .\-]*)")
_NOISY_TOKEN = re.compile(r"^(?=.*\d)[A-Z0-9]{4,}$")
_STOP = {
    "DU",
    "DE",
    "DES",
    "LA",
    "LE",
    "LES",
    "D",
    "L",
    "ET",
    "SAS",
    "SARL",
    "SA",
    "SCA",
    "EU",
    "EUR",
    "SNC",
    "CIE",
    "EURL",
    "SASU",
    "S",
    "A",
    "AU",
    "AUX",
    "EN",
    "PAR",
    "POUR",
    "M",
    "MME",
    "MR",
    "MLLE",
    "WWW",
    "COM",
}


def strip_accents_upper(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text[:_MAX_LABEL])
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


def searchable(raw: str) -> str:
    """Libellé complet en majuscules sans accents, espaces normalisés : pour la recherche par mots-clés."""
    return " ".join(strip_accents_upper(raw).split())


@lru_cache(maxsize=8192)
def merchant_key(raw: str) -> str:
    s = searchable(raw)
    if not s:
        return ""
    agg = _AGGREGATOR.search(s)
    if agg:
        s = agg.group(1)
    else:
        sender = _FROM.search(s)
        if sender:
            s = sender.group(1)
    tail = _TAIL_MARKERS.search(s)
    if tail and tail.start() > 3:
        s = s[: tail.start()]
    s = _DATE.sub(" ", s)
    for _ in range(5):  # préfixes et n° de carte s'empilent : « PAIEMENT PAR CARTE X1234 … »
        stripped = _CARD.sub(" ", _PREFIXES.sub("", s.strip())).strip()
        if stripped == s.strip():
            break
        s = stripped
    s = re.sub(r"[^A-Z0-9&+ ]", " ", s)
    s = _PREFIXES.sub("", s.strip())
    tokens = [t for t in s.split() if not t.isdigit() and not _NOISY_TOKEN.match(t)]
    significant = [t for t in tokens if (t not in _STOP and len(t) > 1) or t in {"U", "&"}]
    if not significant:
        significant = tokens[:1]
    return " ".join(significant[:3])
