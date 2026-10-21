"""Types communs aux importeurs et empreinte de déduplication."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from app.domain.normalize import searchable


@dataclass(frozen=True)
class ParsedTransaction:
    booking_date: date
    amount_cents: int
    label: str
    value_date: date | None = None
    external_id: str | None = None  # FITID (OFX), entry_reference (DSP2)…
    currency: str = "EUR"
    status: str = "booked"


@dataclass(frozen=True)
class SkippedRow:
    line: int
    reason: str


@dataclass
class ParseResult:
    format: str
    transactions: list[ParsedTransaction] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    detected: dict[str, str] = field(default_factory=dict)  # mapping colonnes, encodage…
    account_hint: str | None = None  # ACCTID (OFX) -> rapprochement de compte
    balance_cents: int | None = None
    balance_date: date | None = None


class ImportFormatError(ValueError):
    pass


def external_ref(provider: str, external_id: str) -> str:
    return hashlib.sha256(f"{provider}:{external_id}".encode()).hexdigest()


def fingerprints(account_id: int, txns: Iterable[ParsedTransaction]) -> list[str]:
    """Empreinte stable et idempotente.

    Deux cafés identiques le même jour sont deux opérations distinctes : on ajoute un rang
    d'occurrence (#0, #1…) calculé dans le fichier. Réimporter le même fichier — ou un fichier
    qui chevauche le précédent — redonne exactement les mêmes empreintes."""
    seen: Counter[tuple[date, int, str]] = Counter()
    out: list[str] = []
    for t in txns:
        key = (t.booking_date, t.amount_cents, searchable(t.label))
        rank = seen[key]
        seen[key] += 1
        raw = f"{account_id}|{t.booking_date.isoformat()}|{t.amount_cents}|{key[2]}|{rank}"
        out.append(hashlib.sha256(raw.encode()).hexdigest())
    return out
