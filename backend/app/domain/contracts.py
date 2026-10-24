"""Ce qu'on sait lire dans un contrat : les assureurs (y compris ceux des banques), le type d'une assurance, la
clause de révision d'un bail et l'indice de référence des loyers (IRL) qui la borne. Partagé par la lecture des
documents (coffre) et les corrélations."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.domain.recurring import add_months

IRL_CHECKED_ON = "22 octobre 2026"


@dataclass(frozen=True)
class Insurer:
    pattern: str
    name: str
    bank: bool = False  # bancassureur : l'assurance vendue par la banque


INSURERS: tuple[Insurer, ...] = (
    Insurer(r"\bMAIF\b", "MAIF"),
    Insurer(r"\bMACIF\b", "MACIF"),
    Insurer(r"\bMATMUT\b", "Matmut"),
    Insurer(r"\bAXA\b", "AXA"),
    Insurer(r"\bALLIANZ\b", "Allianz"),
    Insurer(r"\bGROUPAMA\b|\bGAN\b", "Groupama"),
    Insurer(r"\bGMF\b", "GMF"),
    Insurer(r"\bMMA\b", "MMA"),
    Insurer(r"\bMAAF\b", "MAAF"),
    Insurer(r"\bCOVEA\b", "Covéa"),
    Insurer(r"\bGENERALI\b", "Generali"),
    Insurer(r"\bTHELEM\b", "Thélem"),
    Insurer(r"DIRECT ASSURANCE", "Direct Assurance"),
    Insurer(r"\bLUKO\b", "Luko"),
    Insurer(r"\bLEMONADE\b", "Lemonade"),
    Insurer(r"\bLEOCARE\b", "Leocare"),
    Insurer(r"\bACHEEL\b", "Acheel"),
    Insurer(r"\bABEILLE\b", "Abeille Assurances"),
    Insurer(r"\bAPRIL\b", "April"),
    Insurer(r"\bEUROFIL\b", "Eurofil"),
    Insurer(r"\bL ?OLIVIER\b", "L'olivier"),
    Insurer(r"\bPACIFICA\b|\bCA ASSURANCES?\b|CREDIT AGRICOLE ASSUR", "Pacifica (Crédit Agricole)", bank=True),
    Insurer(r"ASSURANCES? DU CREDIT MUTUEL|\bACM\b", "ACM (Crédit Mutuel)", bank=True),
    Insurer(r"\bSOGESSUR\b|SOCIETE GENERALE ASSUR", "Sogessur (Société Générale)", bank=True),
    Insurer(r"\bCARDIF\b|BNP PARIBAS ASSUR", "Cardif (BNP Paribas)", bank=True),
    Insurer(r"BPCE ASSUR|CAISSE D'EPARGNE ASSUR|BANQUE POPULAIRE ASSUR", "BPCE Assurances", bank=True),
    Insurer(r"BANQUE POSTALE ASSUR|\bLBP ASSUR", "La Banque Postale Assurances", bank=True),
    Insurer(r"\bLCL ASSUR|PACIFICA LCL", "LCL Assurances", bank=True),
    Insurer(r"\bCIC ASSUR", "CIC Assurances", bank=True),
    Insurer(r"BOURSO ASSUR|BOURSORAMA ASSUR|BOURSOBANK ASSUR", "BoursoBank Assurances", bank=True),
    Insurer(r"\bCNP\b", "CNP Assurances"),
    Insurer(r"\bSWISSLIFE\b|SWISS LIFE", "SwissLife"),
    Insurer(r"\bHARMONIE MUTUELLE\b", "Harmonie Mutuelle"),
    Insurer(r"\bALAN\b", "Alan"),
    Insurer(r"\bMGEN\b", "MGEN"),
)
_COMPILED = [(re.compile(i.pattern), i) for i in INSURERS]

HOME_WORDS = re.compile(r"\bHABITATION\b|\bHABIT\b|\bMRH\b|MULTIRISQUE|\bLOGEMENT\b|\bLOCATAIRE\b|\bHAB\b")
OTHER_TYPES: tuple[tuple[str, str], ...] = (
    (r"\bAUTO\b|VEHICULE|\bMOTO\b|\bSCOOTER\b|\bVOITURE\b", "auto"),
    (r"\bEMPRUNTEUR\b|\bPRET IMMO\w*\b|ASSURANCE (?:DE )?PRET\b", "emprunteur"),
    (r"\bSANTE\b|\bMUTUELLE\b|COMPLEMENTAIRE|\bPREVOYANCE\b|\bDECES\b|\bOBSEQUES\b", "sante"),
    (r"\bVIE\b|\bEPARGNE\b|\bPER\b", "vie"),
    (r"\bANIMA(?:L|UX)\b|\bCHIEN\b|\bCHAT\b", "animaux"),
    (r"\bSCOLAIRE\b", "scolaire"),
    (r"\bVOYAGE\b|\bRAPATRIEMENT\b", "voyage"),
    (r"\bTELEPHONE\b|\bMOBILE\b|\bSMARTPHONE\b|\bAPPAREIL\b", "appareil"),
)


def insurer_of(folded: str) -> Insurer | None:
    """L'assureur nommé dans un libellé d'opération ou une ligne de document (texte replié, en capitales)."""
    for rx, ins in _COMPILED:
        if rx.search(folded):
            return ins
    return None


def insurance_type(folded: str) -> str | None:
    """habitation | auto | emprunteur | sante | vie | animaux | scolaire | voyage | appareil | None (inconnu)."""
    for pattern, kind in OTHER_TYPES:
        if re.search(pattern, folded):
            return kind
    if HOME_WORDS.search(folded):
        return "habitation"
    return None


# ------------------------------------------------------------------ indice de référence des loyers (INSEE)
# Base 100 au 4e trimestre 1998. Source : INSEE, informations rapides n° 246 (IRL du 3e trimestre 2026), relevé le
# 22 octobre 2026 ; les trimestres 2023 sont déduits des variations annuelles publiées pour 2024.
IRL: dict[tuple[int, int], float] = {
    (2023, 1): 138.61,
    (2023, 2): 140.59,
    (2023, 3): 141.03,
    (2023, 4): 142.06,
    (2024, 1): 143.46,
    (2024, 2): 145.17,
    (2024, 3): 144.51,
    (2024, 4): 144.64,
    (2025, 1): 145.47,
    (2025, 2): 146.68,
    (2025, 3): 145.77,
    (2025, 4): 145.78,
    (2026, 1): 146.60,
    (2026, 2): 148.37,
    (2026, 3): 148.06,
}
IRL_SOURCE = "INSEE, indice de référence des loyers, 3e trimestre 2026 (relevé le 22 octobre 2026)"
IRL_SERIES_URL = "https://www.insee.fr/fr/statistiques/serie/001515333"

# Les trimestres relus à l'INSEE par la relève des barèmes (série BDM 001515333), posés par services.baremes : ils
# passent devant la table ci-dessus, qui reste le repli pour les trimestres plus anciens.
_irl_lock = threading.Lock()
_irl_fetched: dict[tuple[int, int], float] = {}
_irl_fetched_on: str | None = None


def install_irl(values: Mapping[tuple[int, int], float], fetched_on: str | None) -> None:
    global _irl_fetched, _irl_fetched_on
    with _irl_lock:
        _irl_fetched, _irl_fetched_on = dict(values), fetched_on


def irl_table() -> dict[tuple[int, int], float]:
    """L'IRL que Bank applique : la série relue à l'INSEE, complétée des trimestres plus anciens de la table."""
    with _irl_lock:
        return {**IRL, **_irl_fetched}


def irl_value(quarter: tuple[int, int]) -> float | None:
    return irl_table().get(quarter)


def irl_source(*quarters: tuple[int, int]) -> str:
    """D'où viennent les indices cités : la série relue (et quand), sinon la table datée livrée avec Bank."""
    with _irl_lock:
        fetched = bool(quarters) and all(q in _irl_fetched for q in quarters)
        on = _irl_fetched_on
    if fetched:
        return f"INSEE, série 001515333 (IRL), relevée par Bank le {on}" if on else "INSEE, série 001515333 (IRL)"
    return IRL_SOURCE


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def latest_irl_quarter() -> tuple[int, int]:
    return max(irl_table())


def irl_ratio(reference: tuple[int, int], current: tuple[int, int]) -> float | None:
    """Loyer maximal après révision = loyer × IRL(trimestre courant) / IRL(trimestre de référence du bail)."""
    table = irl_table()
    a, b = table.get(reference), table.get(current)
    if a is None or b is None or a <= 0:
        return None
    return b / a


def allowed_rent_cents(
    rent_cents: int, lease_start: date, today: date, reference: tuple[int, int] | None = None
) -> tuple[int, tuple[int, int], tuple[int, int]] | None:
    """Le loyer hors charges maximal aujourd'hui pour un bail à révision annuelle sur l'IRL : loyer initial ×
    IRL(même trimestre, l'année de la dernière révision possible) / IRL(trimestre de référence du bail — celui que
    la clause nomme, sinon celui de la signature). None si l'indice manque (bail trop ancien ou trimestre pas encore
    publié). Approximation honnête : la clause exacte du bail prime."""
    ref = reference or quarter_of(lease_start)
    years = 0
    while add_months(lease_start, 12 * (years + 1)) <= today:
        years += 1
    if years == 0:
        return rent_cents, ref, ref
    current = (ref[0] + years, ref[1])
    if current not in irl_table():
        current = latest_irl_quarter()
        if current <= ref:
            return None
    ratio = irl_ratio(ref, current)
    if ratio is None:
        return None
    return round(rent_cents * ratio), ref, current


_QUARTER = re.compile(r"\b([1-4])\s*(?:ER|E|EME|ÈME)?\s*TRIMESTRE\s*(?:DE\s*)?(20\d{2})\b")


def quarter_in(folded: str) -> tuple[int, int] | None:
    """« IRL du 2e trimestre 2025 » → (2025, 2)."""
    m = _QUARTER.search(folded)
    return (int(m.group(2)), int(m.group(1))) if m else None


def quarter_label(q: tuple[int, int]) -> str:
    return f"{q[1]}{'er' if q[1] == 1 else 'e'} trimestre {q[0]}"


# ------------------------------------------------------------------ plafonds du dépôt de garantie
def deposit_cap_cents(rent_cents: int, furnished: bool) -> int:
    """Loi du 6 juillet 1989 : un mois de loyer hors charges (vide), deux mois (meublé)."""
    return rent_cents * (2 if furnished else 1)
