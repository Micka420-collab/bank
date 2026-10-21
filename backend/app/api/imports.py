"""Import de relevés (CSV / OFX) : aperçu à blanc puis import idempotent."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.db import utcnow
from app.importers.base import ImportFormatError, ParseResult
from app.importers.csv_import import parse_csv
from app.importers.ofx_import import looks_like_ofx, parse_ofx
from app.models import Account, ImportBatch, Provider
from app.security import audit
from app.services.ingest import ingest

router = APIRouter(prefix="/api/imports", tags=["imports"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class PreviewRow(BaseModel):
    booking_date: date
    amount_cents: int
    label: str


class ImportResult(BaseModel):
    dry_run: bool
    format: str
    detected: dict[str, str]
    parsed: int
    skipped: list[dict[str, object]]
    preview: list[PreviewRow]
    inserted: int = 0
    duplicates: int = 0
    reconciled: int = 0
    transfers_detected: int = 0
    aids_detected: int = 0
    already_imported: bool = False
    batch_id: int | None = None


class BatchOut(BaseModel):
    id: int
    account_id: int
    filename: str
    format: str
    inserted: int
    duplicates: int
    reconciled: int
    skipped: int
    created_at: datetime


def parse_any(data: bytes, filename: str) -> ParseResult:
    if looks_like_ofx(data) or filename.lower().endswith((".ofx", ".qfx")):
        return parse_ofx(data)
    return parse_csv(data)


@router.post("")
async def import_file(
    request: Request,
    ctx: AuthDep,
    db: DbDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[int, Form()],
    dry_run: Annotated[bool, Form()] = True,
) -> ImportResult:
    account = db.get(Account, account_id)
    if account is None or account.archived:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Fichier trop volumineux")
    filename = _SAFE_NAME.sub("_", (file.filename or "releve"))[:200]
    try:
        parsed = parse_any(data, filename)
    except ImportFormatError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    digest = hashlib.sha256(data).hexdigest()
    seen = db.scalar(
        select(ImportBatch.id).where(ImportBatch.account_id == account.id, ImportBatch.file_sha256 == digest)
    )
    result = ImportResult(
        dry_run=dry_run,
        format=parsed.format,
        detected=parsed.detected,
        parsed=len(parsed.transactions),
        skipped=[{"line": s.line, "reason": s.reason} for s in parsed.skipped[:50]],
        preview=[
            PreviewRow(booking_date=t.booking_date, amount_cents=t.amount_cents, label=t.label)
            for t in parsed.transactions[:10]
        ],
        already_imported=seen is not None,
    )
    if dry_run:
        return result

    batch = ImportBatch(
        account_id=account.id,
        filename=filename,
        file_sha256=digest,
        format=parsed.format,
        skipped=len(parsed.skipped),
    )
    db.add(batch)
    db.flush()
    report = ingest(db, account, parsed.transactions, provider="file", batch=batch)
    if parsed.balance_cents is not None and account.provider != Provider.ENABLEBANKING:
        account.balance_cents, account.balance_at = parsed.balance_cents, utcnow()
    if account.provider == Provider.MANUAL:
        account.provider = Provider.FILE
    audit.record(
        db,
        ctx.user.username,
        "import.file",
        account=account.id,
        format=parsed.format,
        inserted=report.inserted,
        duplicates=report.duplicates,
        reconciled=report.reconciled,
    )
    return result.model_copy(
        update={
            "inserted": report.inserted,
            "duplicates": report.duplicates,
            "reconciled": report.reconciled,
            "transfers_detected": report.transfers_detected,
            "aids_detected": report.aids_detected,
            "batch_id": batch.id,
        }
    )


@router.get("")
def list_batches(_: AuthDep, db: DbDep) -> list[BatchOut]:
    return [
        BatchOut.model_validate(b, from_attributes=True)
        for b in db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100))
    ]
