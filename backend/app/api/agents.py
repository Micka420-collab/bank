"""Côté humain de l'intégration agent : jetons, propositions à valider, onboarding « Ma situation »,
aperçu de ce que l'agent voit, et journal de ce qu'il a fait."""

from __future__ import annotations

import json
import typing
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import AuthDep, DbDep, SettingsDep, TodayDep, client_ip
from app.db import utcnow
from app.domain.onboarding import BY_ID, LIST_ITEMS, QUESTIONS, SECTIONS, AnswerError, completeness
from app.models import AgentProposal, AgentToken, AuditLog, CategorySource, Transaction
from app.security import audit
from app.security.agents import MAX_TTL_DAYS, PRESETS, PRIVACY_MODES, SCOPES, issue_agent_token
from app.security.auth import verify_password
from app.services import advisor, autonomy
from app.services.proposals import ProposalError, decide, describe, expire_old

router = APIRouter(prefix="/api", tags=["agents"])


# ------------------------------------------------------------------ jetons d'agent
class TokenIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)  # confirmation : un cookie volé ne suffit pas
    name: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9ÀÂÄÇÉÈÊËÎÏÔÖÙÛÜàâäçéèêëîïôöùûü _.-]+$")
    preset: Literal["observateur", "conseiller", "gestionnaire"] | None = "conseiller"
    scopes: list[str] | None = None
    privacy: Literal["redacted", "aggregates", "full"] = "redacted"
    allowed_ips: list[str] = Field(default_factory=list, max_length=10)
    ttl_days: int = Field(default=90, ge=1, le=MAX_TTL_DAYS)


def _token_out(t: AgentToken) -> dict[str, Any]:
    now = utcnow()
    state = "revoque" if t.revoked_at else "expire" if t.expires_at <= now else "actif"
    return {
        "id": t.id,
        "name": t.name,
        "prefix": t.prefix,
        "scopes": json.loads(t.scopes),
        "privacy": t.privacy,
        "allowed_ips": json.loads(t.allowed_ips or "[]"),
        "created_at": t.created_at.isoformat(),
        "expires_at": t.expires_at.isoformat(),
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "calls": t.calls,
        "state": state,
    }


def hermes_config(mcp_url: str) -> str:
    return (
        "# ~/.hermes/config.yaml\n"
        "mcp_servers:\n"
        "  bank:\n"
        f'    url: "{mcp_url}"\n'
        "    headers:\n"
        '      Authorization: "Bearer ${BANK_MCP_TOKEN}"\n'
        "    timeout: 60\n"
        "    connect_timeout: 10\n"
        "    sampling:\n"
        "      enabled: false\n"
    )


@router.get("/agents/catalog")
def catalog(_: AuthDep) -> dict[str, Any]:
    return {"scopes": SCOPES, "presets": PRESETS, "privacy": PRIVACY_MODES, "max_ttl_days": MAX_TTL_DAYS}


@router.get("/agents/tokens")
def list_tokens(_: AuthDep, db: DbDep) -> list[dict[str, Any]]:
    return [_token_out(t) for t in db.scalars(select(AgentToken).order_by(AgentToken.id.desc()))]


@router.post("/agents/tokens", status_code=201)
def create_token(body: TokenIn, request: Request, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    # Un jeton d'agent ouvre l'accès à toute la situation financière : mot de passe exigé, comme pour l'export.
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "agent.token.denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    scopes = body.scopes if body.scopes else PRESETS[body.preset or "observateur"]
    active = db.scalar(select(AgentToken).where(AgentToken.name == body.name, AgentToken.revoked_at.is_(None)))
    if active is not None and active.expires_at > utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "Un jeton actif porte déjà ce nom : révoque-le d'abord")
    try:
        token, raw = issue_agent_token(
            db,
            name=body.name,
            scopes=scopes,
            privacy=body.privacy,
            allowed_ips=body.allowed_ips,
            ttl_days=body.ttl_days,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit.record(
        db,
        ctx.user.username,
        "agent.token.create",
        token=token.id,
        name=token.name,
        scopes=scopes,
        privacy=body.privacy,
    )
    mcp_url = settings.public_url.rstrip("/") + "/mcp"
    return {
        **_token_out(token),
        "token": raw,  # affiché UNE fois : seule son empreinte est conservée
        "mcp_url": mcp_url,
        "hermes_config": hermes_config(mcp_url),
        "hermes_env": f"BANK_MCP_TOKEN={raw}",
    }


@router.delete("/agents/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, ctx: AuthDep, db: DbDep) -> None:
    token = db.get(AgentToken, token_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jeton introuvable")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        audit.record(db, ctx.user.username, "agent.token.revoke", token=token_id)
        autonomy.token_revoked(db, token_id, actor=ctx.user.username, now=token.revoked_at)  # pilote coupé avec


@router.get("/agents/activity")
def activity(_: AuthDep, db: DbDep, limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AuditLog).where(AuditLog.actor.like("agent:%")).order_by(AuditLog.id.desc()).limit(limit * 2)
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        entry = {"at": r.ts.isoformat(), "agent": r.actor[6:], "action": r.action, "details": json.loads(r.details)}
        prev = out[-1] if out else None
        # Un appel d'outil inscrit aussi l'effet métier juste avant lui (réponse notée, proposition créée) :
        # on fusionne les deux pour une ligne lisible (« a noté une réponse : employment »).
        if (
            prev is not None
            and prev["action"].startswith("mcp.")
            and not r.action.startswith("mcp.")
            and prev["agent"] == entry["agent"]
            and not prev.get("merged")
        ):
            prev["details"] = {**entry["details"], **prev["details"]}
            prev["merged"] = True
            continue
        out.append(entry)
    for e in out:
        e.pop("merged", None)
    return out[:limit]


@router.get("/agents/summary")
def summary(_: AuthDep, db: DbDep) -> dict[str, int]:
    """Résumé léger pour la page d'accueil : propositions en attente, complétude, agents actifs."""
    expire_old(db)
    now = utcnow()
    return {
        "pending_proposals": db.scalar(select(func.count(AgentProposal.id)).where(AgentProposal.status == "pending"))
        or 0,
        "onboarding_percent": completeness(advisor.plain_answers(db)).percent,
        "active_agents": db.scalar(
            select(func.count(AgentToken.id)).where(AgentToken.revoked_at.is_(None), AgentToken.expires_at > now)
        )
        or 0,
    }


# ------------------------------------------------------------------ propositions
@router.get("/agents/proposals")
def list_proposals(
    _: AuthDep,
    db: DbDep,
    status_: Literal["pending", "applied", "rejected", "failed", "expired", "all"] = Query("pending", alias="status"),
) -> list[dict[str, Any]]:
    expire_old(db)
    stmt = select(AgentProposal).order_by(AgentProposal.id.desc()).limit(100)
    if status_ != "all":
        stmt = stmt.where(AgentProposal.status == status_)
    out = []
    for p in db.scalars(stmt):
        d = describe(p)
        if p.kind == "create_rule" and p.status == "pending":
            d["impact"] = rule_impact(db, d["params"])
        out.append(d)
    return out


def rule_impact(db: DbDep, params: dict[str, Any]) -> int:
    """Nombre d'opérations (non classées à la main) qu'une règle proposée reclasserait : affiché avant ton clic."""
    pattern = str(params.get("pattern", ""))
    target = (
        Transaction.merchant == pattern
        if params.get("match") == "merchant"
        else func.upper(Transaction.raw_label).contains(pattern, autoescape=True)
    )
    return (
        db.scalar(
            select(func.count(Transaction.id)).where(
                target, Transaction.category_source != CategorySource.MANUAL, Transaction.is_transfer.is_(False)
            )
        )
        or 0
    )


def _decide(proposal_id: int, approve: bool, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return describe(decide(db, proposal_id, approve=approve, actor=ctx.user.username))
    except ProposalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/agents/proposals/{proposal_id}/approve")
def approve(proposal_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    return _decide(proposal_id, True, ctx, db)


@router.post("/agents/proposals/{proposal_id}/reject")
def reject(proposal_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    return _decide(proposal_id, False, ctx, db)


# ------------------------------------------------------------------ onboarding « Ma situation »
FIELD_LABELS = {
    "label": "Libellé",
    "kind": "Type",
    "remaining_cents": "Reste dû",
    "monthly_cents": "Mensualité",
    "rate_percent": "Taux (%)",
    "end_date": "Fin",
    "value_cents": "Valeur",
    "monthly_contribution_cents": "Versement mensuel",
    "amount_cents": "Montant",
    "target_date": "Échéance",
    "priority": "Priorité (1 = indispensable)",
    "date": "Date",
    "rule": "Règle",
}


def item_fields(model: type[BaseModel]) -> list[dict[str, Any]]:
    """Décrit les champs d'un élément de liste pour que l'interface génère le formulaire."""
    out = []
    for name, f in model.model_fields.items():
        ann = f.annotation
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        base = args[0] if args and typing.get_origin(ann) is not Literal else ann
        if name.endswith("_cents"):
            kind, options = "money", []
        elif typing.get_origin(ann) is Literal:
            kind, options = "choice", [str(v) for v in typing.get_args(ann)]
        elif base is Decimal:
            kind, options = "decimal", []
        elif base is date:
            kind, options = "date", []
        elif base is int:
            kind, options = "int", []
        else:
            kind, options = "text", []
        out.append(
            {
                "name": name,
                "label": FIELD_LABELS.get(name, name),
                "kind": kind,
                "options": options,
                "required": f.is_required(),
            }
        )
    return out


@router.get("/onboarding")
def onboarding(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    state = advisor.onboarding_state(db, today)
    values = {k: v["value"] for k, v in state["answers"].items()}
    dossier = advisor.build_dossier(db, today, "aggregates")
    return {
        "percent": state["percent"],
        "answered": state["answered"],
        "total": state["total"],
        "sections": [{"id": s, "title": t} for s, t in SECTIONS],
        "questions": [
            {
                "id": q.id,
                "section": q.section,
                "question": q.label,
                "why": q.why,
                "kind": q.kind,
                "weight": q.weight,
                "options": [{"value": k, "label": lbl} for k, lbl in q.options],
                "max_items": q.max_items,
                "minimum": q.minimum,
                "maximum": q.maximum,
                "item_fields": item_fields(LIST_ITEMS[q.id]) if q.id in LIST_ITEMS else None,
            }
            for q in QUESTIONS
        ],
        "answers": state["answers"],
        "suggestions": state["suggestions"],
        "missing": [q["id"] for q in state["next_questions"]],
        "risk_profile": dossier["risk_profile"],
        "values_preview": values,
    }


class AnswerIn(BaseModel):
    value: Any

    @field_validator("value")
    @classmethod
    def _size(cls, v: Any) -> Any:
        if len(json.dumps(v, default=str)) > 20_000:
            raise ValueError("réponse trop volumineuse")
        return v


@router.put("/onboarding/{question_id}")
def put_answer(question_id: str, body: AnswerIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if question_id not in BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question inconnue")
    try:
        stored = advisor.set_answer(db, question_id, body.value, source=f"user:{ctx.user.username}", unit="cents")
    except AnswerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"question_id": question_id, "value": stored}


@router.delete("/onboarding/{question_id}", status_code=204)
def delete_answer(question_id: str, ctx: AuthDep, db: DbDep) -> None:
    if question_id not in BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question inconnue")
    advisor.clear_answer(db, question_id, source=f"user:{ctx.user.username}")


@router.get("/onboarding/{question_id}/history")
def answer_history(question_id: str, _: AuthDep, db: DbDep) -> list[dict[str, Any]]:
    if question_id not in BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question inconnue")
    return advisor.history(db, question_id)


# ------------------------------------------------------------------ ce que l'agent voit
@router.get("/agents/dossier")
def dossier_preview(
    _: AuthDep,
    db: DbDep,
    today: TodayDep,
    privacy: Literal["redacted", "aggregates", "full"] = "redacted",
) -> dict[str, Any]:
    d = advisor.build_dossier(db, today, privacy)
    return {"dossier": d, "markdown": advisor.render_markdown(d)}


@router.get("/alerts")
def get_alerts(_: AuthDep, db: DbDep, today: TodayDep) -> list[dict[str, str]]:
    return advisor.alerts(db, today)
