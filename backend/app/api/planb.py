"""Plan B « perte de revenu » : trois scénarios en un écran — mois d'autonomie, prestations recalculées par
OpenFisca, démarches de la première semaine. Lecture seule : Bank n'envoie rien, chaque chiffre dit d'où il vient."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.api.deps import AuthDep, DbDep, TodayDep
from app.domain import planb as dom
from app.services import planb as svc

router = APIRouter(prefix="/api/planb", tags=["planb"])


@router.get("")
def plan_b(
    request: Request,
    _: AuthDep,
    db: DbDep,
    today: TodayDep,
    drop: int = Query(default=dom.DEFAULT_DROP, ge=dom.MIN_DROP, le=dom.MAX_DROP),
) -> dict[str, Any]:
    return svc.view(db, today, drop_percent=drop, fiscal=getattr(request.app.state, "fiscal_client", None))
