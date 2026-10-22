"""Couverture : lit dans la base ce qui est relié (banque, papiers, compteur, crypto, messageries, sécurité) et
laisse le domaine dire ce qui manque et ce que ça débloquerait."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import coverage as dom
from app.domain.onboarding import completeness
from app.domain.recurring import add_months
from app.models import (
    Account,
    ChannelConfig,
    CryptoHolding,
    CryptoWallet,
    EnergyConnection,
    LlmProvider,
    Obligation,
    Passkey,
    Provider,
    SavingsGoal,
    SearchProvider,
    Transaction,
    User,
    VaultDocument,
)
from app.services import advisor, consents


def build_input(db: Session, today: date) -> dom.CoverageInput:
    accounts = list(db.scalars(select(Account).where(Account.archived.is_(False))))
    by_provider = {p: sum(1 for a in accounts if a.provider == p) for p in Provider}
    count, first, last = db.execute(
        select(func.count(Transaction.id), func.min(Transaction.booking_date), func.max(Transaction.booking_date))
    ).one()
    months = 0.0
    if first and last:
        months = max(0.0, (last - first).days / 30.44)
    aids = [
        k
        for (k,) in db.execute(
            select(Transaction.aid_kind)
            .where(Transaction.aid_kind.is_not(None), Transaction.booking_date >= add_months(today, -12))
            .distinct()
        )
        if k
    ]
    obligations = [r.kind for r in db.scalars(select(Obligation).where(Obligation.active.is_(True)))]
    docs = {
        kind: int(n)
        for kind, n in db.execute(
            select(VaultDocument.kind, func.count(VaultDocument.id))
            .where(VaultDocument.status != "refused")
            .group_by(VaultDocument.kind)
        )
    }
    energy_rows = list(db.scalars(select(EnergyConnection)))
    if any(e.status == "active" for e in energy_rows):
        energy = "active"
    elif energy_rows:
        energy = energy_rows[-1].status
    else:
        energy = "none"
    values = advisor.plain_answers(db)
    user = db.scalar(select(User))
    return dom.CoverageInput(
        today=today,
        bank_connected=by_provider.get(Provider.ENABLEBANKING, 0),
        file_accounts=by_provider.get(Provider.FILE, 0),
        manual_accounts=by_provider.get(Provider.MANUAL, 0),
        transactions=int(count or 0),
        months_of_data=months,
        last_transaction=last,
        aids_seen=tuple(aids),
        obligations_active=tuple(obligations),
        documents=docs,
        energy=energy,
        crypto_wallets=int(db.scalar(select(func.count(CryptoWallet.id))) or 0),
        crypto_holdings=int(db.scalar(select(func.count(CryptoHolding.id))) or 0),
        profile_percent=completeness(values).percent,
        housing_status=str(values.get("housing_status")) if values.get("housing_status") else None,
        channels=tuple(c.platform for c in db.scalars(select(ChannelConfig).where(ChannelConfig.enabled.is_(True)))),
        assistant_configured=db.get(LlmProvider, 1) is not None,
        memory_decided=consents.decided(db, "ai_memory"),
        search_configured=db.get(SearchProvider, 1) is not None,
        totp=bool(user and user.totp_enabled),
        passkeys=int(db.scalar(select(func.count(Passkey.id))) or 0),
        goals=int(db.scalar(select(func.count(SavingsGoal.id))) or 0),
    )


def sources(db: Session, today: date) -> list[dom.Source]:
    return dom.sources(build_input(db, today))


def view(db: Session, today: date) -> dict[str, Any]:
    srcs = sources(db, today)
    return {
        "score": dom.score(srcs),
        "next": [dom.source_to_dict(s) for s in dom.next_steps(srcs)],
        "sources": [dom.source_to_dict(s) for s in srcs],
    }


def agent_view(db: Session, today: date) -> dict[str, Any]:
    """Pour un agent (MCP) : ce qui manque et ce que ça débloquerait — jamais une valeur privée."""
    srcs = sources(db, today)
    return {
        "score": dom.score(srcs),
        "connected": [s.title for s in srcs if s.state == "connected"],
        "missing": [
            {"key": s.key, "title": s.title, "detail": s.detail, "unlocks": s.unlocks, "where": s.page}
            for s in srcs
            if s.state != "connected"
        ],
        "rule": (
            "Propose de relier ce qui manque quand la question le touche ; chaque connexion se fait dans Bank, "
            "par la personne. Ne demande jamais un identifiant, un mot de passe ni un code."
        ),
    }
