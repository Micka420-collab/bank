"""Anonymisation des libellés avant de les confier à un modèle de langage.

Ce qui sert à décider (montant, date, marchand, catégorie) reste ; ce qui identifie une personne
(nom d'un particulier dans un virement, IBAN, e-mail, téléphone, longs numéros) disparaît.

Conçu pour échouer du côté sûr : la contrepartie d'un VIREMENT est un particulier jusqu'à preuve du
contraire (forme juridique, organisme public, banque, épargne, salaire…). On perd parfois le nom d'une
entreprise ; on ne laisse jamais filer celui d'une personne.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.domain.normalize import merchant_key, searchable

# IBAN : séparateurs espace, point ou tiret tolérés (« FR76-3000-6000-… »).
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s.\-]?[A-Z0-9]{4}){2,7}(?:[\s.\-]?[A-Z0-9]{1,3})?\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b")
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_TITLE = r"(?:M\.?|MR|MME|MLLE|MONSIEUR|MADAME|MADEMOISELLE|M ET MME|MR ET MME)"
# Mots de motif qui terminent un nom : on masque la personne, pas la raison du virement (utile pour décider).
_MOTIVE_WORDS = [
    "LOYER",
    "CADEAU",
    "REMBOURSEMENT",
    "REMB",
    "MOTIF",
    "REF",
    "FACTURE",
    "SALAIRE",
    "PRET",
    "ECH",
    "ECHEANCE",
    "PENSION",
    "ALIMENTAIRE",
    "COURSES",
    "VACANCES",
    "RESTAU",
    "RESTAURANT",
    "ANNIVERSAIRE",
    "NOEL",
    "PART",
    "PARTAGE",
    "DETTE",
    "AVANCE",
    "CAUTION",
    "DEPOT",
    "EPARGNE",
]
_MOTIVE = "(?:" + "|".join(_MOTIVE_WORDS) + ")"
_NAME = rf"(?!{_MOTIVE}\b)[A-Z][A-Z'\-.]*"
_PERSON = re.compile(rf"\b{_TITLE}\s+{_NAME}(?:\s+{_NAME}){{0,2}}")
_P2P = re.compile(rf"\b(LYDIA|PAYLIB|WERO|PAYPAL)\b(\s+(?:DE|A|POUR|VERS))?\s+{_NAME}(?:\s+{_NAME}){{0,2}}")

_TRANSFER = re.compile(r"^(?:VIR(?:EMENT)?|VIRT|VRT)\b")
# Indices qu'une contrepartie de virement est une organisation (cherchés dans tout le libellé).
_ORG_HINT = re.compile(
    r"\b(?:SAS|SASU|SARL|SA|SCI|SCM|SCP|EURL|SNC|SCOP|SEM|GIE|EI|EIRL|ASSOCIATION|ASSO|FONDATION|SYNDIC|"
    r"MUTUELLE|BANQUE|BANK|CAISSE|CREDIT|ASSURANCES?|CAF|CPAM|CNAM|URSSAF|DGFIP|TRESOR|IMPOTS?|FINANCES|"
    r"FRANCE\s+TRAVAIL|POLE\s+EMPLOI|MSA|CARSAT|CNAV|AGIRC|ARRCO|IRCANTEC|RETRAITE|CROUS|DEPARTEMENT|REGION|"
    r"MAIRIE|COMMUNE|PREFECTURE|ANTS|LIVRET|LDDS|LEP|PEL|CEL|PEA|SALAIRE|PAIE|REMUNERATION|INTERESSEMENT|"
    r"PARTICIPATION|INDEMNITES?|ALLOCATIONS?|PRESTATIONS?|BOURSE|SECU|SECURITE\s+SOCIALE)\b"
)
# Mots qui ne désignent jamais une personne dans la contrepartie (marqueurs d'anonymisation compris).
_NOT_A_NAME = set(_MOTIVE_WORDS) | {"PARTICULIER", "IBAN", "EMAIL", "TEL", "NUM", "VERS", "RECU", "EMIS", "INST"}

# Marqueur d'anonymisation, pas un secret (faux positif bandit B105).
PERSON_TOKEN = "[PARTICULIER]"  # nosec B105
_RUN_OF_TOKENS = re.compile(r"(?:\[PARTICULIER\]\s*)+")


@lru_cache(maxsize=8192)
def _transfer_counterparty(text: str) -> tuple[str, ...]:
    """Mots du nom de la contrepartie d'un virement présumé entre particuliers ; () sinon."""
    if not _TRANSFER.match(text) or _ORG_HINT.search(text):
        return ()
    words = tuple(w for w in merchant_key(text).split() if w.isalpha() and w not in _NOT_A_NAME)
    # Un seul mot (« BRGM », « EDF ») : sigle ou marque. Deux à trois mots sans indice d'organisation : une personne.
    return words if len(words) >= 2 else ()


def is_person_transfer(raw_label: str) -> bool:
    text = searchable(raw_label)
    return bool(_PERSON.search(text) or _P2P.search(text) or _transfer_counterparty(text))


def redact_label(raw_label: str) -> str:
    text = searchable(raw_label)
    text = _IBAN.sub("[IBAN]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[TEL]", text)
    text = _PERSON.sub(PERSON_TOKEN, text)
    text = _P2P.sub(lambda m: f"{m.group(1)}{m.group(2) or ''} {PERSON_TOKEN}", text)
    names = _transfer_counterparty(text)
    if names:
        text = re.sub(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", PERSON_TOKEN, text)
    text = _RUN_OF_TOKENS.sub(PERSON_TOKEN + " ", text).strip()
    return _LONG_NUMBER.sub("[NUM]", text)


def redact_merchant(merchant: str, raw_label: str) -> str:
    """Le marchand d'un virement entre particuliers EST un nom de personne : on le masque."""
    return PERSON_TOKEN if is_person_transfer(raw_label) else merchant
