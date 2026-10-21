"""Jetons d'agent et propositions : ce que l'IA peut faire, et surtout ce qu'elle ne peut pas faire."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AgentProposal, AgentToken, AuditLog, Category, CategoryRule, SavingsGoal
from app.security.agents import (
    PRESETS,
    TOKEN_PREFIX,
    AgentAuthError,
    authenticate_agent,
    issue_agent_token,
    touch,
)
from app.services import proposals as props
from app.services.settings_store import get_setting


# ------------------------------------------------------------------ jetons
def test_token_is_stored_hashed_and_authenticates(db: Session) -> None:
    token, raw = issue_agent_token(db, name="hermes", scopes=PRESETS["conseiller"], allowed_ips=["192.168.1.112"])
    db.commit()
    assert raw.startswith(TOKEN_PREFIX) and len(raw) > 40
    assert raw not in token.token_hash and token.prefix == raw[:10]
    assert json.loads(token.allowed_ips) == ["192.168.1.112/32"]
    p = authenticate_agent(db, f"Bearer {raw}", "192.168.1.112")
    assert p.name == "hermes" and p.actor == "agent:hermes" and p.privacy == "redacted"
    assert p.can("propose") and not p.can("write:categorize")
    touch(db, p.token_id)
    assert db.get(AgentToken, p.token_id).calls == 1  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic abc",
        "Bearer ",
        "Bearer xyz",
        f"Bearer {TOKEN_PREFIX}nope",
        "Bearer " + TOKEN_PREFIX + "a" * 300,
    ],
)
def test_bad_credentials_rejected(db: Session, header: str | None) -> None:
    issue_agent_token(db, name="hermes", scopes=["read"])
    with pytest.raises(AgentAuthError):
        authenticate_agent(db, header, "127.0.0.1")


def test_revoked_expired_and_wrong_ip_rejected(db: Session) -> None:
    t1, raw1 = issue_agent_token(db, name="a", scopes=["read"])
    t1.revoked_at = utcnow()
    _, raw2 = issue_agent_token(db, name="b", scopes=["read"], ttl_days=1)
    _, raw3 = issue_agent_token(db, name="c", scopes=["read"], allowed_ips=["10.0.0.0/8"])
    db.commit()
    with pytest.raises(AgentAuthError):
        authenticate_agent(db, f"Bearer {raw1}", "127.0.0.1")
    with pytest.raises(AgentAuthError):
        authenticate_agent(db, f"Bearer {raw2}", "127.0.0.1", now=utcnow() + timedelta(days=2))
    with pytest.raises(AgentAuthError):
        authenticate_agent(db, f"Bearer {raw3}", "192.168.1.5")
    with pytest.raises(AgentAuthError):
        authenticate_agent(db, f"Bearer {raw3}", "pas-une-ip")
    assert authenticate_agent(db, f"bearer {raw3}", "10.1.2.3").name == "c"


@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"scopes": []}, "Portées"),
        ({"scopes": ["admin"]}, "Portées"),
        ({"scopes": ["read"], "privacy": "tout"}, "confidentialité"),
        ({"scopes": ["read"], "ttl_days": 0}, "validité"),
        ({"scopes": ["read"], "ttl_days": 366}, "validité"),
    ],
)
def test_token_issuance_validates(db: Session, kwargs: dict, msg: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError, match=msg):
        issue_agent_token(db, name="x", **kwargs)
    with pytest.raises(ValueError):
        issue_agent_token(db, name="x", scopes=["read"], allowed_ips=["pas un réseau"])


# ------------------------------------------------------------------ propositions
def test_proposal_lifecycle_envelope(db: Session) -> None:
    prop = props.create_proposal(
        db,
        agent="hermes",
        kind="set_envelope",
        params={"category": "courses", "monthly_eur": "380.50"},
        rationale="Moyenne des 3 derniers mois : 372 €.",
    )
    assert prop.status == "pending" and prop.expires_at > utcnow()
    again = props.create_proposal(
        db, agent="hermes", kind="set_envelope", params={"category": "courses", "monthly_eur": 380.5}, rationale="bis"
    )
    assert again.id == prop.id  # idempotent : la même proposition n'est pas dupliquée
    done = props.decide(db, prop.id, approve=True, actor="micka")
    assert done.status == "applied" and "380.5" in (done.result or "")
    cat = db.scalar(select(Category).where(Category.slug == "courses"))
    assert cat is not None and cat.monthly_budget_cents == 38_050
    with pytest.raises(props.ProposalError, match="déjà"):
        props.decide(db, prop.id, approve=True, actor="micka")
    actions = [a.action for a in db.scalars(select(AuditLog))]
    assert "proposal.create" in actions and "proposal.approve" in actions
    actors = {a.actor for a in db.scalars(select(AuditLog))}
    assert {"agent:hermes", "micka"} <= actors


def test_proposal_kinds_apply(db: Session) -> None:
    specs = [
        ("set_savings_goal", {"monthly_eur": 150}),
        ("set_buffer", {"amount_eur": 200}),
        ("create_goal", {"name": "Vacances", "target_eur": 1200, "target_date": "2027-07-01", "monthly_eur": 100}),
        ("create_rule", {"pattern": "  basic  fit ", "category": "loisirs", "match": "contains"}),
    ]
    for kind, params in specs:
        p = props.create_proposal(db, agent="hermes", kind=kind, params=params, rationale="test")
        assert props.decide(db, p.id, approve=True, actor="micka").status == "applied", kind
    assert get_setting(db, "savings_goal_cents") == 15_000
    assert get_setting(db, "buffer_cents") == 20_000
    goal = db.scalar(select(SavingsGoal))
    assert goal is not None and goal.target_cents == 120_000 and goal.monthly_contribution_cents == 10_000
    rule = db.scalar(select(CategoryRule).where(CategoryRule.pattern == "BASIC FIT"))
    assert rule is not None and rule.source == "user"


def test_rejected_and_failed_and_expired(db: Session) -> None:
    p = props.create_proposal(db, agent="h", kind="set_buffer", params={"amount_eur": 50}, rationale="r")
    assert props.decide(db, p.id, approve=False, actor="micka").status == "rejected"
    assert get_setting(db, "buffer_cents") != 5_000

    q = props.create_proposal(
        db, agent="h", kind="set_envelope", params={"category": "loisirs", "monthly_eur": 60}, rationale="r"
    )
    cat = db.scalar(select(Category).where(Category.slug == "loisirs"))
    db.delete(cat)
    db.flush()
    failed = props.decide(db, q.id, approve=True, actor="micka")
    assert failed.status == "failed" and "disparue" in (failed.result or "")

    old = props.create_proposal(db, agent="h", kind="set_buffer", params={"amount_eur": 70}, rationale="r")
    old.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(props.ProposalError, match="expirée"):
        props.decide(db, old.id, approve=True, actor="micka")
    with pytest.raises(props.ProposalError, match="introuvable"):
        props.decide(db, 9999, approve=True, actor="micka")


@pytest.mark.parametrize(
    ("kind", "params", "rationale", "msg"),
    [
        ("transfer_money", {"amount_eur": 10}, "r", "Type inconnu"),
        ("set_buffer", {"amount_eur": 10}, "", "justification"),
        ("set_buffer", {"amount_eur": 10}, "x" * 2001, "justification"),
        ("set_buffer", {"amount_eur": -1}, "r", "amount_eur"),
        ("set_buffer", {"amount_eur": 10, "extra": 1}, "r", None),
        ("set_envelope", {"category": "inexistante", "monthly_eur": 10}, "r", "Catégorie inconnue"),
        ("create_rule", {"pattern": "X", "category": "courses"}, "r", "pattern"),
        ("create_goal", {"name": "", "target_eur": 10}, "r", "name"),
    ],
)
def test_proposals_are_strictly_validated(
    db: Session, kind: str, params: dict, rationale: str, msg: str | None
) -> None:  # type: ignore[type-arg]
    if msg is None:  # champ inconnu ignoré, pas d'effet de bord
        p = props.create_proposal(db, agent="h", kind=kind, params=params, rationale=rationale)
        assert "extra" not in json.loads(p.payload)
        return
    with pytest.raises(props.ProposalError, match=msg):
        props.create_proposal(db, agent="h", kind=kind, params=params, rationale=rationale)


def test_pending_queue_is_bounded(db: Session) -> None:
    for i in range(props.MAX_PENDING):
        props.create_proposal(db, agent="h", kind="set_buffer", params={"amount_eur": i + 1}, rationale="r")
    with pytest.raises(props.ProposalError, match="Trop"):
        props.create_proposal(db, agent="h", kind="set_buffer", params={"amount_eur": 999}, rationale="r")
    assert db.query(AgentProposal).count() == props.MAX_PENDING
