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
    Consent,
    Conversation,
    EnergyReading,
    InitiativeState,
    LlmUsage,
    MemoryEntry,
    MemoryProposal,
    MerchantHint,
    OfficialFact,
    OpportunityState,
    Procedure,
    SavingsGoal,
    Transaction,
    ValueEvent,
    VaultDocument,
    WatchSource,
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
        # Assistant : les catégories proposées et la consommation ; jamais la clé OpenRouter.
        "merchant_hints": [
            {"merchant": h.merchant, "category": h.category_slug} for h in db.scalars(select(MerchantHint))
        ],
        "llm_usage": [
            {
                "ts": u.ts.isoformat(),
                "feature": u.feature,
                "model": u.model,
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "cost_microusd": u.cost_microusd,
            }
            for u in db.scalars(select(LlmUsage).order_by(LlmUsage.id))
        ],
        # Mémoire de l'assistant : tout ce qu'il retient de toi, et vos conversations conservées.
        "memory": [
            {
                "store": m.store,
                "content": m.content,
                "pinned": m.pinned,
                "origin": m.origin,
                "created_at": m.created_at.isoformat(),
                "updated_at": m.updated_at.isoformat(),
            }
            for m in db.scalars(select(MemoryEntry).order_by(MemoryEntry.id))
        ],
        "memory_proposals": [
            {
                "store": p.store,
                "action": p.action,
                "target": p.target_snapshot,
                "new_text": p.new_text,
                "reason": p.reason,
                "status": p.status,
            }
            for p in db.scalars(select(MemoryProposal).order_by(MemoryProposal.id))
        ],
        "conversations": [
            {
                "started_at": c.started_at.isoformat(),
                "last_at": c.last_at.isoformat(),
                "title": c.title,
                "summary": c.summary,
                "model": c.model,
                "messages": [
                    {
                        "ts": m.ts.isoformat(),
                        "role": m.role,
                        "content": m.content,
                        "feedback": m.feedback,
                        "feedback_note": m.feedback_note,
                    }
                    for m in c.messages
                ],
            }
            for c in db.scalars(select(Conversation).order_by(Conversation.id))
        ],
        "consents": [
            {"purpose": c.purpose, "granted": c.granted, "version": c.wording_version, "at": c.updated_at.isoformat()}
            for c in db.scalars(select(Consent))
        ],
        # Coffre : les métadonnées et les valeurs lues ou corrigées ; les fichiers eux-mêmes sont dans l'export
        # du coffre (archive zip, Documents › Exporter).
        "vault_documents": [
            {
                "kind": d.kind,
                "title": d.title,
                "status": d.status,
                "sensitive": d.sensitive,
                "document_date": d.document_date.isoformat() if d.document_date else None,
                "expires_on": d.expires_on.isoformat() if d.expires_on else None,
                "created_at": d.created_at.isoformat(),
                "fields": {f.key: json.loads(f.value) for f in d.fields},
            }
            for d in db.scalars(select(VaultDocument).order_by(VaultDocument.id))
        ],
        # Démarches : valeurs, pièces (identifiants du coffre), validations, envois déclarés, résultats, historique.
        "procedures": [
            {
                "template": pr.template,
                "title": pr.title,
                "stage": pr.stage,
                "data": json.loads(pr.data),
                "approval": json.loads(pr.approval) if pr.approval else None,
                "reference": pr.reference,
                "opportunity_key": pr.opportunity_key,
                "deadline": pr.deadline.isoformat() if pr.deadline else None,
                "next_check": pr.next_check.isoformat() if pr.next_check else None,
                "created_at": pr.created_at.isoformat(),
                "events": [{"ts": e.ts.isoformat(), "kind": e.kind, "detail": json.loads(e.detail)} for e in pr.events],
            }
            for pr in db.scalars(select(Procedure).order_by(Procedure.id))
        ],
        "opportunity_states": [
            {"key": o.key, "status": o.status, "until": o.until.isoformat() if o.until else None}
            for o in db.scalars(select(OpportunityState))
        ],
        "value_events": [
            {
                "key": v.opportunity_key,
                "kind": v.kind,
                "amount_cents": v.amount_cents,
                "period": v.period,
                "on": v.on_date.isoformat(),
                "label": v.label,
                "evidence": v.evidence,
            }
            for v in db.scalars(select(ValueEvent).order_by(ValueEvent.id))
        ],
        "watch_sources": [
            {"name": w.name, "url": w.url, "trust": w.trust, "enabled": w.enabled, "builtin": w.builtin}
            for w in db.scalars(select(WatchSource).order_by(WatchSource.id))
        ],
    }
    audit.record(db, ctx.user.username, "export.done", transactions=len(payload["transactions"]))
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bank-export.json"'},
    )
