"""Ingestion idempotente : déduplication, rapprochement inter-sources, catégorisation, aides, virements internes.

Même chemin pour un import de fichier et pour une synchronisation DSP2 : une seule logique à tester.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.aids import classify_aid, income_category_for
from app.domain.normalize import merchant_key, searchable
from app.domain.transfers import Movement, looks_like_transfer, match_internal_transfers
from app.importers.base import ParsedTransaction, external_ref, fingerprints
from app.models import Account, CategorySource, ImportBatch, Transaction
from app.services.categories import category_ids, load_categorizer

_CHUNK = 500


def _chunks(items: Sequence[str], size: int = _CHUNK) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _tokens(label: str) -> set[str]:
    return {t for t in searchable(label).split() if len(t) > 2}


@dataclass
class IngestReport:
    inserted: int = 0
    duplicates: int = 0
    reconciled: int = 0
    transfers_detected: int = 0
    aids_detected: int = 0
    inserted_ids: list[int] = field(default_factory=list)


def ingest(
    db: Session,
    account: Account,
    parsed: Sequence[ParsedTransaction],
    *,
    provider: str,
    batch: ImportBatch | None = None,
) -> IngestReport:
    report = IngestReport()
    if not parsed:
        return report
    fps = fingerprints(account.id, parsed)
    refs = [external_ref(provider, p.external_id) if p.external_id else None for p in parsed]

    known_fps: set[str] = set()
    for chunk in _chunks(fps):
        known_fps.update(
            db.scalars(
                select(Transaction.fingerprint).where(
                    Transaction.account_id == account.id, Transaction.fingerprint.in_(chunk)
                )
            )
        )
    wanted_refs = [r for r in refs if r]
    known_refs: set[str] = set()
    for chunk in _chunks(wanted_refs):
        known_refs.update(
            r
            for r in db.scalars(
                select(Transaction.external_ref).where(
                    Transaction.account_id == account.id, Transaction.external_ref.in_(chunk)
                )
            )
            if r
        )

    fresh: list[tuple[ParsedTransaction, str, str | None]] = []
    seen_in_file: set[str] = set()
    for p, fp, ref in zip(parsed, fps, refs, strict=True):
        if fp in known_fps or (ref and ref in known_refs) or fp in seen_in_file:
            report.duplicates += 1
            continue
        seen_in_file.add(fp)
        fresh.append((p, fp, ref))

    fresh = _reconcile(db, account, fresh, report)

    categorizer = load_categorizer(db)
    ids = category_ids(db)
    created: list[Transaction] = []
    for p, fp, ref in fresh:
        merchant = merchant_key(p.label)
        decision = categorizer.decide(p.label, merchant, p.amount_cents)
        aid = classify_aid(p.label, p.amount_cents)
        category, source = decision.category, decision.source
        if aid and source in ("builtin", "none", "bayes"):
            category, source = income_category_for(aid), "builtin"
        txn = Transaction(
            account_id=account.id,
            booking_date=p.booking_date,
            value_date=p.value_date,
            amount_cents=p.amount_cents,
            currency=p.currency,
            raw_label=p.label,
            merchant=merchant,
            category_id=ids.get(category),
            category_source=CategorySource(source),
            status=p.status,
            fingerprint=fp,
            external_ref=ref,
            aid_kind=aid,
            import_batch_id=batch.id if batch else None,
        )
        db.add(txn)
        created.append(txn)
        report.inserted += 1
        report.aids_detected += aid is not None
    db.flush()
    report.inserted_ids = [t.id for t in created]
    if fresh:
        lo = min(p.booking_date for p, _, _ in fresh)
        hi = max(p.booking_date for p, _, _ in fresh)
        report.transfers_detected = detect_transfers(db, lo, hi)
    if batch is not None:
        batch.inserted, batch.duplicates, batch.reconciled = (
            report.inserted,
            report.duplicates,
            report.reconciled,
        )
    return report


def _reconcile(
    db: Session,
    account: Account,
    fresh: list[tuple[ParsedTransaction, str, str | None]],
    report: IngestReport,
) -> list[tuple[ParsedTransaction, str, str | None]]:
    """Rapproche une opération déjà connue par une AUTRE source (CSV puis DSP2, ou l'inverse) :
    même compte, même montant, <= 3 jours d'écart. Appariement glouton par coût croissant
    (écart de dates, puis proximité des libellés) ; chaque opération existante ne sert qu'une fois."""
    if not fresh:
        return fresh
    lo = min(p.booking_date for p, _, _ in fresh) - timedelta(days=3)
    hi = max(p.booking_date for p, _, _ in fresh) + timedelta(days=3)
    existing = list(
        db.scalars(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.booking_date >= lo,
                Transaction.booking_date <= hi,
            )
        )
    )
    if not existing:
        return fresh
    candidates: list[tuple[float, int, int]] = []
    for i, (p, _fp, ref) in enumerate(fresh):
        ptoks = _tokens(p.label)
        for j, e in enumerate(existing):
            cross_source = (ref is not None) != (e.external_ref is not None)
            if not cross_source or e.amount_cents != p.amount_cents:
                continue
            gap = abs((e.booking_date - p.booking_date).days)
            if gap > 3:
                continue
            etoks = _tokens(e.raw_label)
            jaccard = len(ptoks & etoks) / len(ptoks | etoks) if ptoks | etoks else 0.0
            candidates.append((gap * 10 - jaccard * 5, i, j))
    candidates.sort()
    used_fresh: set[int] = set()
    used_existing: set[int] = set()
    for _cost, i, j in candidates:
        if i in used_fresh or j in used_existing:
            continue
        used_fresh.add(i)
        used_existing.add(j)
        ref = fresh[i][2]
        if ref and existing[j].external_ref is None:
            existing[j].external_ref = ref  # l'opération CSV gagne l'identifiant bancaire officiel
        report.reconciled += 1
    return [f for i, f in enumerate(fresh) if i not in used_fresh]


def detect_transfers(db: Session, lo: date, hi: date) -> int:
    """Marque les virements internes entre tes comptes sur la période (élargie de 3 jours)."""
    lo, hi = lo - timedelta(days=3), hi + timedelta(days=3)
    rows = list(
        db.scalars(
            select(Transaction).where(
                Transaction.booking_date >= lo,
                Transaction.booking_date <= hi,
                Transaction.is_transfer.is_(False),
            )
        )
    )
    if len(rows) < 2:
        return 0
    by_id = {t.id: t for t in rows}
    moves = [
        Movement(t.id, t.account_id, t.booking_date, t.amount_cents, looks_like_transfer(searchable(t.raw_label)))
        for t in rows
    ]
    pairs = match_internal_transfers(moves)
    transfer_cat = category_ids(db).get("virements_internes")
    for debit_id, credit_id in pairs:
        for tid, peer in ((debit_id, credit_id), (credit_id, debit_id)):
            t = by_id[tid]
            t.is_transfer = True
            t.transfer_peer_id = peer
            if t.category_source != CategorySource.MANUAL:
                t.category_id = transfer_cat
                t.category_source = CategorySource.BUILTIN
    return len(pairs)
