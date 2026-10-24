"""Pilote vs consigne : le suivi de chaque consigne du pilote automatique, lu dans ce que Bank a déjà (périodes
d'activité, journal des mouvements). Rien n'est écrit ici ; le calcul est dans app.domain.pilot_track."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import pilot_track as dom
from app.models import AutonomyEpoch, AutonomyMove
from app.services import autonomy

HISTORY = timedelta(days=92)


def _rules(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [r for r in value if isinstance(r, dict) and r.get("id") and r.get("every") in autonomy.EVERY]


def windows(db: Session, now: datetime, since: datetime) -> tuple[list[dom.Window], list[dict[str, Any]]]:
    """Les périodes où le pilote était allumé, et toutes les consignes qu'il a eues (les actuelles d'abord)."""
    row = autonomy.mandate(db)
    rules = {str(r["id"]): r for r in _rules(row.rules)}
    out: list[dom.Window] = []
    epochs = list(db.scalars(select(AutonomyEpoch).order_by(AutonomyEpoch.started_at)))
    for e in epochs:
        end = min(e.stopped_at or e.expires_at, e.expires_at)
        if end <= since:
            continue
        found = _rules(e.rules)
        for r in found:
            rules.setdefault(str(r["id"]), r)
        out.append(dom.Window(e.started_at, end, frozenset(str(r["id"]) for r in found), e.trial))
    if not epochs and row.enabled_at is not None and row.expires_at is not None:
        # Pilote allumé avant que Bank ne note ses périodes : une seule, de l'activation à l'arrêt (ou l'échéance).
        end = min(row.disabled_at or row.expires_at, row.expires_at)
        out.append(dom.Window(row.enabled_at, end, frozenset(rules), row.trial))
    return out, list(rules.values())


def view(db: Session, *, now: datetime, tz: tzinfo) -> dict[str, Any]:
    since = now - HISTORY
    found, rules = windows(db, now, since)
    moves = [
        dom.Move(
            id=m.id,
            rule_id=m.rule_id,
            period=m.period,
            status=m.status,
            amount_cents=int(m.amount_cents),
            executed_cents=int(m.executed_cents) if m.executed_cents is not None else None,
            created_at=m.created_at,
            text=autonomy.describe(m),
            refusal=m.refusal,
        )
        for m in db.scalars(select(AutonomyMove).where(AutonomyMove.created_at >= since).order_by(AutonomyMove.id))
    ]
    out = dom.track(rules, found, moves, now=now, tz=tz, since=since)
    out["since"] = since.astimezone(tz).date().isoformat()
    return out
