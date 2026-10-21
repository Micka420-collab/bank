"""Documents officiels : aperçu d'un PDF (rien n'est enregistré), validation, liste, suppression."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.domain.documents import DocumentError
from app.services import documents

router = APIRouter(prefix="/api/documents", tags=["documents"])

PORTALS = [
    {
        "id": "impots",
        "name": "impots.gouv.fr",
        "what": "Avis d'impôt (revenu fiscal de référence, parts, taux de prélèvement)",
        "url": "https://cfspart.impots.gouv.fr/",
        "how": "Espace particulier › Documents › Avis d'impôt sur le revenu › télécharger le PDF",
    },
    {
        "id": "caf",
        "name": "caf.fr",
        "what": "Attestation de paiement ou de droits (APL, prime d'activité, RSA, AF…)",
        "url": "https://www.caf.fr/allocataires/mon-compte",
        "how": "Mon Compte › Mes attestations › Attestation de paiement (PDF)",
    },
    {
        "id": "france_travail",
        "name": "francetravail.fr",
        "what": "Attestation de situation (allocation journalière, fin de droits)",
        "url": "https://candidat.francetravail.fr/espacepersonnel/",
        "how": "Mon espace › Mes attestations › Attestation de situation",
    },
    {
        "id": "mesdroits",
        "name": "mesdroitssociaux.gouv.fr",
        "what": "Simulateur officiel de tous tes droits (à comparer avec l'estimation de Bank)",
        "url": "https://www.mesdroitssociaux.gouv.fr/",
        "how": "Connexion FranceConnect, puis « Simuler mes droits »",
    },
    {
        "id": "enedis",
        "name": "Mon compte Enedis",
        "what": "Gérer ou révoquer le partage de tes données Linky",
        "url": "https://mon-compte-particulier.enedis.fr/partage-donnees-tiers",
        "how": "Partage de données avec des tiers",
    },
]


@router.get("/portals")
def portals(_: AuthDep) -> list[dict[str, str]]:
    return PORTALS


@router.post("/preview")
async def preview_document(file: Annotated[UploadFile, File()], _: AuthDep) -> dict[str, Any]:
    data = await file.read(documents.MAX_PDF_BYTES + 1)
    try:
        # Lecture dans un thread (elle attend un processus enfant) : la boucle d'événements reste disponible
        # pour /api/health et le serveur MCP pendant ce temps.
        parsed = await run_in_threadpool(documents.preview, data)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return asdict(parsed)


class ApplyIn(BaseModel):
    kind: str = Field(max_length=24)
    fields: dict[str, Any]


@router.post("/apply", status_code=201)
def apply_document(body: ApplyIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        row = documents.apply(db, kind=body.kind, fields=body.fields, actor=f"user:{ctx.user.username}", today=today)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"id": row.id, "kind": row.kind, "period": row.period, "title": row.title}


@router.get("")
def list_facts(_: AuthDep, db: DbDep) -> list[dict[str, Any]]:
    return documents.facts(db)


@router.delete("/{fact_id}", status_code=204)
def delete_fact(fact_id: int, ctx: AuthDep, db: DbDep) -> None:
    if not documents.remove(db, fact_id, f"user:{ctx.user.username}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document introuvable")
