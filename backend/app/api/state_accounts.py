"""Comptes de l'État (connecteur guidé) : l'état de chaque organisme et les documents à récupérer, nés d'un mail
d'avis sans pièce jointe ou d'une attente de saison. Bank ouvre la bonne page ; il ne s'y connecte jamais."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AuthDep, DbDep, TodayDep
from app.db import utcnow
from app.services import state_accounts as svc

router = APIRouter(prefix="/api/state-accounts", tags=["state-accounts"])


class CloseIn(BaseModel):
    status: Literal["done", "dismissed"]


@router.get("")
def overview(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    svc.sync(db, today=today, now=utcnow())  # un PDF déposé à l'instant ferme sa tâche tout de suite
    return svc.view(db, today)


@router.post("/tasks/{task_id}")
def close(task_id: int, body: CloseIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.close(db, task_id, status=body.status, actor=ctx.user.username, now=utcnow())
    except svc.StateAccountsError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return svc.view(db, today)
