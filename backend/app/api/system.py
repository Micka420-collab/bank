"""Santé, journal d'audit, export intégral (portabilité RGPD) protégé par re-saisie du mot de passe."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthDep, DbDep, client_ip
from app.db import utcnow
from app.models import (
    Account,
    AuditLog,
    AutonomyMandate,
    AutonomyMove,
    Category,
    ChannelConfig,
    ChannelLink,
    Consent,
    Conversation,
    CryptoHolding,
    CryptoTaxEvent,
    CryptoWallet,
    DocumentTask,
    EnergyReading,
    InitiativeState,
    LlmUsage,
    Loan,
    MemoryEntry,
    MemoryProposal,
    MerchantHint,
    OfficialFact,
    OpportunityState,
    PriceAlert,
    Procedure,
    PublicCalendar,
    RateOverride,
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
from app.services import bank_setup as bank_setup_service
from app.services import exchange_keys as exchange_keys_service
from app.services import home_value as home_value_service
from app.services import obligations as obligations_service
from app.services import powens as powens_service
from app.services import retirement as retirement_service
from app.services import territory as territory_service

router = APIRouter(prefix="/api", tags=["system"])


def _autopilot(db: Session) -> dict[str, Any]:
    row = db.get(AutonomyMandate, 1)
    moves = db.scalars(select(AutonomyMove).order_by(AutonomyMove.id))
    return {
        "mandate": None
        if row is None
        else {
            "enabled": row.enabled,
            "trial": row.trial,
            "agent": row.agent_name,
            "mode": row.mode,
            "scopes": json.loads(row.scopes or "[]"),
            "assets": json.loads(row.assets or "[]"),
            "rules": json.loads(row.rules or "[]"),
            "destinations": json.loads(row.destinations or "[]"),
            "per_op_cents": row.per_op_cents,
            "per_day_cents": row.per_day_cents,
            "per_month_cents": row.per_month_cents,
            "max_moves_per_day": row.max_moves_per_day,
            "max_loss_cents": row.max_loss_cents,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
        "moves": [
            {
                "id": m.id,
                "agent": m.agent_name,
                "kind": m.kind,
                "asset": m.asset,
                "to_asset": m.to_asset,
                "chain": m.chain,
                "amount_cents": m.amount_cents,
                "executed_cents": m.executed_cents,
                "pnl_cents": m.pnl_cents,
                "destination": m.destination_label,
                "rule": m.rule,
                "reason": m.reason,
                "status": m.status,
                "refusal": m.refusal,
                "tx_ref": m.tx_ref,
                "created_at": m.created_at.isoformat(),
            }
            for m in moves
        ],
    }


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
        # Territoire (#32) : l'adresse telle que tapée et ce que Bank en a tiré, et les calendriers qui en découlent.
        "territory": {
            "place": territory_service.place(db),
            "calendars": [
                {
                    "key": c.key,
                    "data": json.loads(c.data) if c.data else None,
                    "source_url": c.source_url,
                    "fetched_at": c.fetched_at.isoformat() if c.fetched_at else None,
                }
                for c in db.scalars(select(PublicCalendar).order_by(PublicCalendar.key))
            ],
        },
        # Documents à récupérer (#32) : objet du mail d'avis et document attendu.
        "document_tasks": [
            {
                "key": t.key,
                "body": t.body,
                "origin": t.origin,
                "expected": t.expected,
                "subject": t.subject,
                "received_at": t.received_at.isoformat() if t.received_at else None,
                "due": t.due.isoformat(),
                "status": t.status,
                "document_id": t.document_id,
                "created_at": t.created_at.isoformat(),
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in db.scalars(select(DocumentTask).order_by(DocumentTask.id))
        ],
        # Barèmes (#32) : les valeurs relevées appliquées ou écartées par toi.
        "rate_overrides": [
            {
                "path": r.path,
                "label": r.label,
                "value": json.loads(r.value),
                "effective_from": r.effective_from,
                "source": r.source,
                "applied_at": r.applied_at.isoformat(),
                "reverted_at": r.reverted_at.isoformat() if r.reverted_at else None,
            }
            for r in db.scalars(select(RateOverride).order_by(RateOverride.path))
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
        # Messageries : réglages et comptes reliés, jamais un jeton ni l'identifiant d'un compte (n° de téléphone).
        "channels": [
            {
                "platform": c.platform,
                "enabled": c.enabled,
                "privacy": c.privacy,
                "links": [
                    {"label": link.label, "linked_at": link.linked_at.isoformat(), "notify": link.notify}
                    for link in db.scalars(
                        select(ChannelLink).where(ChannelLink.platform == c.platform, ChannelLink.revoked_at.is_(None))
                    )
                ],
            }
            for c in db.scalars(select(ChannelConfig).order_by(ChannelConfig.platform))
        ],
        # Pilote automatique : ton cadre et son journal (le ticket n'existe qu'en empreinte, jamais exporté).
        "autopilot": _autopilot(db),
        # Déclarations récurrentes : ce qui est suivi, ce qui a été fait et ce que tu as déclaré.
        "obligations": obligations_service.export(db),
        # Crédits suivis et relevé de carrière : ce que tu as saisi (contrat, trimestres) ; le reste se recalcule.
        "loans": [
            {
                "name": loan.name,
                "lender": loan.lender,
                "kind": loan.kind,
                "principal_cents": loan.principal_cents,
                "annual_rate_bp": loan.annual_rate_bp,
                "months": loan.months,
                "first_payment": loan.first_payment.isoformat(),
                "insurance_monthly_cents": loan.insurance_monthly_cents,
                "account_id": loan.account_id,
            }
            for loan in db.scalars(select(Loan).order_by(Loan.id))
        ],
        "retirement_profile": retirement_service.profile(db) or None,
        # Logement : ta description (données déclarées) et la dernière estimation ; les ventes DVF se retéléchargent.
        "home": {"profile": home_value_service.profile(db) or None, "estimate": home_value_service.view_estimate(db)},
        # Crypto : adresses publiques suivies, avoirs, opérations fiscales (aucune clé : Bank n'en a jamais).
        "crypto": {
            "wallets": [
                {"label": w.label, "chain": w.chain, "address": w.address}
                for w in db.scalars(select(CryptoWallet).order_by(CryptoWallet.id))
            ],
            "holdings": [
                {
                    "asset": h.asset,
                    "quantity": h.quantity,
                    "platform": h.platform,
                    "foreign": h.foreign,
                    "watched": h.wallet_id is not None,
                }
                for h in db.scalars(select(CryptoHolding).order_by(CryptoHolding.id))
            ],
            "tax_events": [
                {
                    "day": e.day.isoformat(),
                    "kind": e.kind,
                    "amount_cents": e.amount_cents,
                    "fees_cents": e.fees_cents,
                    "portfolio_value_cents": e.portfolio_value_cents,
                    "asset": e.asset,
                    "note": e.note,
                }
                for e in db.scalars(select(CryptoTaxEvent).order_by(CryptoTaxEvent.day))
            ],
            "exchanges": exchange_keys_service.export(db),  # plateformes reliées : jamais la clé ni le secret
            # Alertes de cours : tes seuils, tes repères (texte libre que tu as saisi) et quand elles ont sonné.
            "price_alerts": [
                {
                    "asset": a.asset,
                    "kind": a.kind,
                    "threshold": a.threshold,
                    "label": a.label,
                    "state": a.state,
                    "created_at": a.created_at.isoformat(),
                    "fires": a.fires,
                    "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
                    "triggered_price": a.triggered_price,
                    "triggered_change": a.triggered_change,
                    "rearmed_at": a.rearmed_at.isoformat() if a.rearmed_at else None,
                }
                for a in db.scalars(select(PriceAlert).order_by(PriceAlert.id))
            ],
        },
        # Assistant Enable Banking : indice et empreinte seulement, jamais l'identifiant entier ni la clé privée.
        "bank_setup": bank_setup_service.export(db),
        # Powens (sur contrat) : domaine, indice et empreinte ; jamais le secret ni le jeton de l'utilisateur Powens.
        "powens_setup": powens_service.export(db),
    }
    audit.record(db, ctx.user.username, "export.done", transactions=len(payload["transactions"]))
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bank-export.json"'},
    )
