"""Boîte mail en lecture seule : relier (mot de passe de Bank exigé, essai avant d'enregistrer), état, lecture à la
demande, propositions de PDF à importer dans Papiers (import ou écart). Les identifiants ne ressortent jamais."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, client_ip
from app.api.vault import process_in_background
from app.security.auth import verify_password
from app.services import mail as svc

router = APIRouter(prefix="/api/mail", tags=["mail"])


class MailIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)  # le mot de passe de Bank, pas celui de la boîte
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=993, ge=1, le=65535)
    security: str = Field(default="ssl", pattern="^(ssl|starttls)$")
    username: str = Field(min_length=1, max_length=200)
    mail_password: str = Field(min_length=1, max_length=500)
    folder: str = Field(default="INBOX", min_length=1, max_length=120)
    senders: list[str] = Field(default_factory=list, max_length=50)


def _raise(exc: svc.MailError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _factory(request: Request) -> svc.ClientFactory:
    factory: svc.ClientFactory | None = getattr(request.app.state, "mail_client_factory", None)
    return factory or svc.default_client


@router.get("")
def mail_status(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return svc.status(db)


@router.put("")
def configure(body: MailIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives : attends quelques minutes")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    try:
        svc.configure(
            db,
            actor=ctx.user.username,
            host=body.host,
            port=body.port,
            security=body.security,
            username=body.username,
            password=body.mail_password,
            folder=body.folder,
            senders=body.senders,
            factory=_factory(request),
        )
    except svc.MailError as exc:
        raise _raise(exc) from exc
    return svc.status(db)


@router.delete("")
def remove(ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        svc.remove(db, actor=ctx.user.username)
    except svc.MailError as exc:
        raise _raise(exc) from exc
    return svc.status(db)


@router.post("/test")
def probe(request: Request, _: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return svc.probe(db, factory=_factory(request))
    except svc.MailError as exc:
        raise _raise(exc) from exc


@router.post("/scan")
def scan(request: Request, _: AuthDep, db: DbDep) -> dict[str, Any]:
    if not request.app.state.limiters["mail_scan"].hit("mail-scan"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de lectures : attends une heure")
    report = svc.scan(db, factory=_factory(request))
    out = svc.status(db)
    out["report"] = {
        "scanned": report.scanned,
        "proposed": report.proposed,
        "skipped": report.skipped,
        "notes": report.notes[:10],
    }
    return out


@router.post("/proposals/{proposal_id}/import", status_code=201)
def import_proposal(
    proposal_id: int, request: Request, background: BackgroundTasks, ctx: AuthDep, db: DbDep
) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        doc_ids = svc.import_proposal(
            db,
            proposal_id,
            actor=ctx.user.username,
            file_limit=settings.vault_file_bytes,
            vault_limit=settings.vault_total_bytes,
            factory=_factory(request),
        )
    except svc.MailError as exc:
        raise _raise(exc) from exc
    if doc_ids:
        background.add_task(process_in_background, request.app, doc_ids)
    out = svc.status(db)
    out["document_ids"] = doc_ids
    return out


@router.post("/proposals/{proposal_id}/dismiss")
def dismiss(proposal_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        svc.dismiss(db, proposal_id, actor=ctx.user.username)
    except svc.MailError as exc:
        raise _raise(exc) from exc
    return svc.status(db)
