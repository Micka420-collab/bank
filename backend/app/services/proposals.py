"""Propositions d'agent : l'agent suggère, tu valides, Bank applique.

Chaque type de proposition a un schéma strict. L'application est faite par le code de Bank (jamais
par l'agent), après validation humaine, et le résultat est journalisé.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.money import decimal_to_cents
from app.domain.onboarding import BY_ID, AnswerError, validate_answer
from app.models import AgentProposal, Category, CategoryRule, SavingsGoal
from app.security import audit
from app.services.advisor import set_answer
from app.services.categories import category_ids, load_categorizer, reapply_rules
from app.services.settings_store import set_setting

PROPOSAL_TTL = timedelta(days=14)
MAX_PENDING = 20


# Montant en euros arrondi au centime : 380.5 et "380.50" sont la même proposition (dédoublonnage fiable).
Euros = Annotated[Decimal, AfterValidator(lambda v: v.quantize(Decimal("0.01")))]


class _Euros(BaseModel):
    def cents(self, value: Decimal) -> int:
        return decimal_to_cents(value)


class SetEnvelope(_Euros):
    category: str = Field(max_length=40)
    monthly_eur: Euros = Field(ge=0, le=100_000)


class SetSavingsGoal(_Euros):
    monthly_eur: Euros = Field(ge=0, le=100_000)


class SetBuffer(_Euros):
    amount_eur: Euros = Field(ge=0, le=100_000)


class CreateGoal(_Euros):
    name: str = Field(min_length=1, max_length=120)
    target_eur: Euros = Field(gt=0, le=100_000_000)
    target_date: date | None = None
    monthly_eur: Euros = Field(default=Decimal(0), ge=0, le=100_000)


class CreateRule(BaseModel):
    pattern: str = Field(max_length=120)
    category: str = Field(max_length=40)
    match: Literal["contains", "merchant"] = "contains"

    @field_validator("pattern")
    @classmethod
    def _normalize(cls, v: str) -> str:
        # Validé APRÈS normalisation : « "  " » ou « "E " » ne doit jamais devenir une règle qui attrape tout.
        clean = " ".join(v.upper().split())
        if len(clean) < 3:
            raise ValueError("motif de 3 caractères au moins")
        return clean


class SetAnswer(BaseModel):
    """Réponse « Ma situation » que l'agent ne peut pas écrire seul (mandat exclu) : il la propose."""

    question_id: str = Field(max_length=40)
    value: Any

    @field_validator("question_id")
    @classmethod
    def _known(cls, v: str) -> str:
        q = BY_ID.get(v)
        if q is None:
            raise ValueError("question inconnue")
        if q.section == "mandat":
            raise ValueError("le mandat se règle uniquement par l'utilisateur, dans Bank")
        return v


KINDS: dict[str, tuple[type[BaseModel], str]] = {
    "set_envelope": (SetEnvelope, "Fixer une enveloppe mensuelle"),
    "set_savings_goal": (SetSavingsGoal, "Fixer l'épargne mensuelle visée"),
    "set_buffer": (SetBuffer, "Fixer le coussin de sécurité"),
    "create_goal": (CreateGoal, "Créer un objectif d'épargne"),
    "create_rule": (CreateRule, "Créer une règle de classement"),
    "set_answer": (SetAnswer, "Modifier une réponse de « Ma situation »"),
}


STATUS_FR = {"applied": "appliquée", "rejected": "refusée", "failed": "en échec", "expired": "expirée"}


class ProposalError(ValueError):
    pass


def create_proposal(db: Session, *, agent: str, kind: str, params: dict[str, Any], rationale: str) -> AgentProposal:
    if kind not in KINDS:
        raise ProposalError(f"Type inconnu. Types possibles : {', '.join(KINDS)}")
    if not rationale or len(rationale) > 2000:
        raise ProposalError("Une justification (2000 caractères au plus) est obligatoire")
    model, _ = KINDS[kind]
    try:
        payload = model.model_validate(params).model_dump(mode="json")
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ProposalError(f"Paramètre invalide ({'.'.join(map(str, first['loc']))}) : {first['msg']}") from exc
    if kind == "set_answer":
        # Montants en euros côté agent, stockés normalisés (centimes) : l'utilisateur valide la valeur exacte.
        try:
            payload["value"] = validate_answer(payload["question_id"], payload["value"], "euros")
        except AnswerError as exc:
            raise ProposalError(f"Réponse invalide : {exc}") from exc
    if kind in ("set_envelope", "create_rule") and not db.scalar(
        select(Category.id).where(Category.slug == payload["category"])
    ):
        raise ProposalError("Catégorie inconnue (voir spending_summary pour les identifiants)")
    pending = list(db.scalars(select(AgentProposal).where(AgentProposal.status == "pending")))
    if len(pending) >= MAX_PENDING:
        raise ProposalError("Trop de propositions en attente : attends la décision de l'utilisateur")
    canonical = json.dumps(payload, sort_keys=True)
    for p in pending:
        if p.kind == kind and json.dumps(json.loads(p.payload), sort_keys=True) == canonical:
            return p  # idempotent : pas de doublon
    prop = AgentProposal(
        agent_name=agent[:64],
        kind=kind,
        payload=canonical,
        rationale=" ".join(rationale.split()),
        expires_at=utcnow() + PROPOSAL_TTL,
    )
    db.add(prop)
    db.flush()
    audit.record(db, f"agent:{agent}", "proposal.create", proposal=prop.id, kind=kind)
    return prop


def describe(p: AgentProposal) -> dict[str, Any]:
    params = json.loads(p.payload)
    extra = (
        {"question": BY_ID[params["question_id"]].label}
        if p.kind == "set_answer" and params.get("question_id") in BY_ID
        else {}
    )
    return {
        **extra,
        "id": p.id,
        "agent": p.agent_name,
        "kind": p.kind,
        "title": KINDS.get(p.kind, (None, p.kind))[1],
        "params": params,
        "rationale": p.rationale,
        "status": p.status,
        "result": p.result,
        "created_at": p.created_at.isoformat(),
        "expires_at": p.expires_at.isoformat(),
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
    }


def expire_old(db: Session) -> None:
    now = utcnow()
    for p in db.scalars(select(AgentProposal).where(AgentProposal.status == "pending", AgentProposal.expires_at < now)):
        p.status = "expired"


def decide(db: Session, proposal_id: int, *, approve: bool, actor: str) -> AgentProposal:
    p = db.get(AgentProposal, proposal_id)
    if p is None:
        raise ProposalError("Proposition introuvable")
    expire_old(db)
    if p.status == "pending" and p.expires_at <= utcnow():
        p.status = "expired"
    if p.status != "pending":
        raise ProposalError(f"Proposition déjà {STATUS_FR.get(p.status, p.status)}")
    p.decided_at = utcnow()
    if not approve:
        p.status = "rejected"
        audit.record(db, actor, "proposal.reject", proposal=p.id)
        return p
    try:
        p.result = _apply(db, p.kind, json.loads(p.payload), actor)
        p.status = "applied"
    except (ProposalError, ValidationError) as exc:
        p.status, p.result = "failed", str(exc)[:300]
    audit.record(db, actor, "proposal.approve", proposal=p.id, status=p.status)
    return p


def _apply(db: Session, kind: str, params: dict[str, Any], actor: str) -> str:
    if kind == "set_envelope":
        e = SetEnvelope.model_validate(params)
        cat = db.scalar(select(Category).where(Category.slug == e.category))
        if cat is None:
            raise ProposalError("Catégorie disparue")
        cat.monthly_budget_cents = e.cents(e.monthly_eur) or None
        return f"Enveloppe {cat.name} : {e.monthly_eur} €"
    if kind == "set_savings_goal":
        g = SetSavingsGoal.model_validate(params)
        set_setting(db, "savings_goal_cents", g.cents(g.monthly_eur))
        return f"Épargne visée : {g.monthly_eur} €/mois"
    if kind == "set_buffer":
        b = SetBuffer.model_validate(params)
        set_setting(db, "buffer_cents", b.cents(b.amount_eur))
        return f"Coussin : {b.amount_eur} €"
    if kind == "create_goal":
        c = CreateGoal.model_validate(params)
        goal = SavingsGoal(
            name=c.name,
            target_cents=c.cents(c.target_eur),
            target_date=c.target_date,
            monthly_contribution_cents=c.cents(c.monthly_eur),
        )
        db.add(goal)
        db.flush()
        return f"Objectif « {c.name} » créé"
    if kind == "set_answer":
        a = SetAnswer.model_validate(params)
        try:
            set_answer(db, a.question_id, a.value, source=f"user:{actor}", unit="cents")
        except AnswerError as exc:
            raise ProposalError(str(exc)) from exc
        return f"Réponse « {BY_ID[a.question_id].label} » mise à jour"
    if kind == "create_rule":
        r = CreateRule.model_validate(params)
        cat = db.scalar(select(Category).where(Category.slug == r.category))
        if cat is None:
            raise ProposalError("Catégorie disparue")
        pattern = r.pattern
        existing = db.scalar(
            select(CategoryRule).where(
                CategoryRule.pattern == pattern, CategoryRule.match == r.match, CategoryRule.direction == "any"
            )
        )
        if existing:
            existing.category_id, existing.source = cat.id, "user"
        else:
            db.add(CategoryRule(pattern=pattern, match=r.match, direction="any", category_id=cat.id, source="user"))
        db.flush()
        n = reapply_rules(db, load_categorizer(db), category_ids(db))
        return f"Règle « {pattern} » -> {cat.name} ({n} opérations reclassées)"
    raise ProposalError("Type inconnu")
