"""Copilote : ce qui mérite ton attention maintenant (pistes, démarches à relancer, nouveautés), en un appel."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import AuthDep, DbDep, TodayDep
from app.domain.procedures import OPEN_STAGES, STAGE_LABELS
from app.models import Procedure
from app.services import opportunities, procedures, watch

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.get("/summary")
def summary(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    value = opportunities.value_summary(db)
    ready = db.scalar(select(func.count()).select_from(Procedure).where(Procedure.stage == "ready")) or 0
    open_count = db.scalar(select(func.count()).select_from(Procedure).where(Procedure.stage.in_(OPEN_STAGES))) or 0
    waiting = db.scalar(select(func.count()).select_from(Procedure).where(Procedure.stage == "waiting")) or 0
    return {
        "opportunities": opportunities.top(db, today, limit=3),
        "value": {
            "confirmed_yearly_cents": value["confirmed_yearly_cents"],
            "estimated_yearly_cents": value["estimated_yearly_cents"],
        },
        "confirmations": len(opportunities.confirmations(db, today)),
        "procedures": {
            "open": open_count,
            "ready": ready,
            "waiting": waiting,
            "due": [
                {
                    "id": p.id,
                    "title": p.title,
                    "next_check": p.next_check.isoformat() if p.next_check else None,
                    "stage_label": STAGE_LABELS.get(p.stage, p.stage),
                }
                for p in procedures.due(db, today)[:5]
            ],
        },
        "watch": watch.summary(db, today),
    }


@router.get("/badges")
def badges(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, int]:
    """Pastilles de la navigation : sans lancer les détecteurs d'opportunités (léger, appelé à chaque page)."""
    return {
        "procedures_due": len(procedures.due(db, today)),
        "procedures_ready": db.scalar(select(func.count()).select_from(Procedure).where(Procedure.stage == "ready"))
        or 0,
        "confirmations": len(opportunities.confirmations(db, today)),
        "watch_new_relevant": int(watch.summary(db, today)["new_relevant"]),
    }
