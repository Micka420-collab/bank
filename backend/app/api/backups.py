"""Sauvegardes : état (dernière sauvegarde, dernière restauration d'essai, fichiers), lancer une sauvegarde ou une
vérification maintenant. Aucune donnée ne sort par ici : les fichiers restent sur le serveur, chiffrés."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AuthDep, DbDep
from app.services import backup as svc

router = APIRouter(prefix="/api/system/backups", tags=["system"])


def _limited(request: Request) -> None:
    """Six gestes par heure : une sauvegarde prend quelques secondes, une restauration d'essai aussi."""
    if not request.app.state.limiters["backups"].hit("backups"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une heure")


@router.get("")
def backups_status(request: Request, _: AuthDep, db: DbDep) -> dict[str, Any]:
    return svc.status(db, request.app.state.settings)


@router.post("/run")
def run_now(request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limited(request)
    settings = request.app.state.settings
    if not settings.backup_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sauvegardes désactivées (BANK_BACKUP_ENABLED)")
    row = svc.backup_and_record(db, settings, actor=ctx.user.username, trigger="manual")
    out = svc.status(db, settings)
    out["run"] = svc.run_view(row)
    return out


@router.post("/verify")
def verify_now(request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    _limited(request)
    settings = request.app.state.settings
    row = svc.verify_and_record(db, settings, actor=ctx.user.username, trigger="manual")
    out = svc.status(db, settings)
    out["run"] = svc.run_view(row)
    return out
