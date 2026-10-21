"""Coffre-fort de documents : dépôt (plusieurs fichiers, appareil photo), bibliothèque, détail avec preuves,
corrections, vérification, chiffres officiels, lecture assistée, export, suppression."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.domain import reading
from app.models import VaultDocument
from app.security import audit
from app.security.auth import verify_password
from app.services import vault

router = APIRouter(prefix="/api/vault", tags=["vault"])
log = logging.getLogger("bank")

# Le contenu servi vient de toi mais a pu être forgé : jamais interprété comme une page de l'application.
FILE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class UpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = Field(default=None, max_length=24)
    sensitive: bool | None = None


class FieldIn(BaseModel):
    value: str | int | float = Field(union_mode="left_to_right")


class ExportIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _http(exc: vault.VaultError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def process_in_background(app: FastAPI, doc_ids: list[int]) -> None:
    """Lecture hors requête (OCR compris), une transaction par document ; une erreur ne bloque pas les suivants."""
    for doc_id in doc_ids:
        try:
            with app.state.sessionmaker() as db:
                vault.process(db, doc_id)
                db.commit()
        except Exception:
            log.exception("lecture d'un document du coffre en échec")
            with app.state.sessionmaker() as db:
                doc = db.get(VaultDocument, doc_id)
                if doc is not None and doc.status == "processing":
                    doc.status, doc.error = "failed", "Lecture interrompue : réessaie"
                    db.commit()


@router.get("")
def listing(_: AuthDep, db: DbDep, kind: str | None = Query(default=None, max_length=24)) -> dict[str, Any]:
    return vault.listing(db, kind)


@router.post("/upload", status_code=201)
async def upload(
    request: Request,
    background: BackgroundTasks,
    ctx: AuthDep,
    db: DbDep,
    files: Annotated[list[UploadFile], File()],
    source: Annotated[str, Form(max_length=12)] = "upload",
) -> dict[str, Any]:
    settings = request.app.state.settings
    items: list[vault.Incoming] = []
    total = 0
    for f in files[: vault.MAX_FILES_PER_UPLOAD + 1]:
        data = await f.read(settings.vault_file_bytes + 1)
        total += len(data)
        if total > settings.vault_upload_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Envoi trop lourd : dépose moins de fichiers")
        items.append(vault.Incoming(f.filename or "document", data))
    try:
        created, skipped = vault.ingest(
            db,
            items,
            source=source,
            actor=ctx.user.username,
            file_limit=settings.vault_file_bytes,
            vault_limit=settings.vault_total_bytes,
        )
    except vault.VaultError as exc:
        raise _http(exc) from exc
    db.commit()  # la lecture se fait dans sa propre transaction : le dépôt doit être écrit
    if created:
        background.add_task(process_in_background, request.app, [d.id for d in created])
    return {"documents": [vault.summary(d) for d in created], "skipped": skipped}


@router.get("/{doc_id}")
def detail(doc_id: int, _: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return vault.view(db, doc_id)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.get("/{doc_id}/file")
def file(doc_id: int, ctx: AuthDep, db: DbDep, download: bool = False) -> Response:
    try:
        data, mime, name = vault.file_content(db, doc_id, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc
    # Un PDF est toujours téléchargé (jamais ouvert dans l'onglet de l'application) ; une image nettoyée s'affiche.
    disposition = "attachment" if download or mime == reading.PDF else "inline"
    headers = {**FILE_HEADERS, "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}"}
    return Response(data, media_type=mime, headers=headers)


@router.get("/{doc_id}/thumb")
def thumb(doc_id: int, _: AuthDep, db: DbDep) -> Response:
    try:
        data = vault.thumb_content(db, doc_id)
    except vault.VaultError as exc:
        raise _http(exc) from exc
    return Response(data, media_type="image/jpeg", headers={**FILE_HEADERS, "Content-Disposition": "inline"})


@router.patch("/{doc_id}")
def update(doc_id: int, body: UpdateIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return vault.update(
            db, doc_id, actor=ctx.user.username, title=body.title, kind=body.kind, sensitive=body.sensitive
        )
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.put("/{doc_id}/fields/{key}")
def set_field(doc_id: int, key: str, body: FieldIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return vault.set_field(db, doc_id, key[:40], body.value, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.delete("/{doc_id}/fields/{key}")
def remove_field(doc_id: int, key: str, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return vault.remove_field(db, doc_id, key[:40], actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.post("/{doc_id}/verify")
def verify(doc_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return vault.verify(db, doc_id, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.post("/{doc_id}/reprocess", status_code=202)
def reprocess(doc_id: int, request: Request, background: BackgroundTasks, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        doc = vault.reprocess(db, doc_id, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc
    db.commit()
    background.add_task(process_in_background, request.app, [doc.id])
    return vault.summary(doc)


@router.post("/{doc_id}/apply")
def apply_official(doc_id: int, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        return vault.apply_official(db, doc_id, actor=ctx.user.username, today=today)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.post("/{doc_id}/assist")
async def assist(doc_id: int, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["assistant"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de demandes : attends une minute")
    try:
        result: dict[str, Any] = await run_in_threadpool(
            vault.assist,
            db,
            doc_id,
            actor=ctx.user.username,
            today=today,
            make_client=request.app.state.llm_client_factory,
        )
        return result
    except vault.VaultError as exc:
        db.commit()  # coût et erreurs du fournisseur restent comptés
        raise _http(exc) from exc


@router.delete("/{doc_id}", status_code=204)
def delete(doc_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        vault.delete(db, doc_id, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc


@router.post("/export")
def export(body: ExportIn, request: Request, ctx: AuthDep, db: DbDep) -> Response:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "vault.export_denied")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        data = vault.export_zip(db, actor=ctx.user.username)
    except vault.VaultError as exc:
        raise _http(exc) from exc
    name = f"bank-coffre-{date.today().isoformat()}.zip"  # noqa: DTZ011
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
