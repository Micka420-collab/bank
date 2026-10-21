"""Détection des virements internes (compte courant -> livret…) pour ne pas les compter
comme dépense puis comme revenu : sans ça, épargner 500 € « coûterait » 500 € au budget."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Movement:
    id: int
    account_id: int
    date: date
    amount_cents: int
    looks_like_transfer: bool = True  # libellé de type « VIR… », « VERSEMENT LIVRET »…


TRANSFER_HINTS = ("VIR", "VIREMENT", "TRANSFERT", "VERSEMENT", "EPARGNE", "LIVRET", "LDDS", "LEP", "PEA")


def looks_like_transfer(searchable_label: str) -> bool:
    words = set(searchable_label.split())
    return any(h in words for h in TRANSFER_HINTS)


def match_internal_transfers(movements: Sequence[Movement], max_days: int = 3) -> list[tuple[int, int]]:
    """Apparie débit/crédit de même montant absolu, sur deux comptes différents, à <= max_days d'écart.

    Appariement glouton par écart de dates croissant : déterministe, et chaque opération n'est utilisée
    qu'une fois. Retourne des paires (id_debit, id_credit)."""
    by_amount: dict[int, tuple[list[Movement], list[Movement]]] = defaultdict(lambda: ([], []))
    for m in movements:
        if m.amount_cents == 0:
            continue
        debits, credits = by_amount[abs(m.amount_cents)]
        (debits if m.amount_cents < 0 else credits).append(m)

    pairs: list[tuple[int, int]] = []
    for debits, credits in by_amount.values():
        if not debits or not credits:
            continue
        candidates = sorted(
            (abs((d.date - c.date).days), d.id, c.id)
            for d in debits
            for c in credits
            if d.account_id != c.account_id
            and abs((d.date - c.date).days) <= max_days
            # Un remboursement CB de 30 € le jour d'un achat de 30 € ailleurs n'est pas un virement.
            and (d.looks_like_transfer or c.looks_like_transfer)
        )
        used: set[int] = set()
        for _, did, cid in candidates:
            if did in used or cid in used:
                continue
            used.update((did, cid))
            pairs.append((did, cid))
    return sorted(pairs)
