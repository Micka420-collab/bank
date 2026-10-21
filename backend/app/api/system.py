"""Santé, journal d'audit, export intégral (portabilité RGPD) protégé par re-saisie du mot de passe."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, client_ip
from app.db import utcnow
from app.models import (
    Account,
    AuditLog,
    Category,
    EnergyReading,
    InitiativeState,
    OfficialFact,
    SavingsGoal,
    Transaction,
    WorkPlan,
    WorkShift,
)
from app.security import audit
from app.security.auth import verify_password

router = APIRouter(prefix="/api", tags=["system"])


class ExportIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/audit")
def audit_log(_: AuthDep, db: DbDep, limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    chain = audit.verify_chain(db)
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return {
        "chain": {
            "ok": chain.ok,
            "entries": chain.entries,
            "broken_at": chain.broken_at,
            "reason": chain.reason,
        },
        "entries": [
            {
                "id": e.id,
                "ts": e.ts.isoformat(),
                "actor": e.actor,
                "action": e.action,
                "details": json.loads(e.details),
            }
            for e in rows
        ],
    }


@router.post("/export")
def export(body: ExportIn, request: Request, ctx: AuthDep, db: DbDep) -> Response:
    limiter = request.app.state.limiters["login"]
    if not limiter.hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "export.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    cats = {c.id: c.slug for c in db.scalars(select(Category))}
    payload: dict[str, Any] = {
        "exported_at": utcnow().isoformat(),
        "format": "bank-export/v1",
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "kind": a.kind,
                "institution": a.institution,
                "iban": a.iban,
                "currency": a.currency,
                "balance_cents": a.balance_cents,
            }
            for a in db.scalars(select(Account))
        ],
        "transactions": [
            {
                "id": t.id,
                "account_id": t.account_id,
                "date": t.booking_date.isoformat(),
                "amount_cents": t.amount_cents,
                "label": t.raw_label,
                "merchant": t.merchant,
                "category": cats.get(t.category_id or -1),
                "note": t.note,
                "is_transfer": t.is_transfer,
                "aid_kind": t.aid_kind,
            }
            for t in db.scalars(select(Transaction).order_by(Transaction.booking_date))
        ],
        "goals": [{"name": g.name, "target_cents": g.target_cents} for g in db.scalars(select(SavingsGoal))],
        "energy_readings": [
            {"day": r.day.isoformat(), "wh": r.wh}
            for r in db.scalars(select(EnergyReading).order_by(EnergyReading.day))
        ],
        "official_facts": [
            {"kind": f.kind, "period": f.period, "fields": json.loads(f.data)} for f in db.scalars(select(OfficialFact))
        ],
        # Planning : les journées et le modèle de paie, jamais l'adresse privée de l'agenda.
        "work_plan": next(
            (
                {"source": w.source, "label": w.label, "model": json.loads(w.model) if w.model else None}
                for w in db.scalars(select(WorkPlan))
            ),
            None,
        ),
        "work_shifts": [
            {"day": s.day.isoformat(), "hours": s.hours, "all_day": s.all_day, "title": s.title}
            for s in db.scalars(select(WorkShift).order_by(WorkShift.day))
        ],
        "initiative_states": [
            {"key": i.key, "status": i.status, "until": i.until.isoformat() if i.until else None, "note": i.note}
            for i in db.scalars(select(InitiativeState))
        ],
    }
    audit.record(db, ctx.user.username, "export.done", transactions=len(payload["transactions"]))
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bank-export.json"'},
    )
