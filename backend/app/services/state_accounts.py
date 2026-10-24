"""Comptes de l'État, connecteur guidé (#30, #32) : les tâches « document à récupérer » nées d'un mail d'organisme sans
pièce jointe ou d'une attente de saison, fermées seules quand le PDF arrive dans Papiers (ou est lu dans Comptes de
l'État), et l'état de chaque organisme (dernier document lu, sa date, « à renouveler »).

Rien ne part : la boîte mail en lecture seule donne l'expéditeur et l'objet (jamais le texte), Papiers donne le genre
et la date des documents déposés. Bank ne garde aucun identifiant d'un compte de l'État."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import aids as aids_domain
from app.domain import mail as mail_domain
from app.domain import state_accounts as dom
from app.domain import vault as vault_domain
from app.models import DocumentTask, OfficialFact, VaultDocument
from app.security import audit
from app.services import analytics

# Ce que la lecture d'un PDF dans Comptes de l'État (services.documents) range sous chaque organisme.
FACT_KINDS = {"impots": "avis_impot", "caf": "caf", "france_travail": "france_travail"}
READABLE = ("verified", "to_review")
log = logging.getLogger(__name__)


class StateAccountsError(ValueError):
    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.status = status


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d is not None and d.tzinfo is None else d


# ------------------------------------------------------------------ le crochet de la boîte mail
def on_mail(db: Session, headers: mail_domain.Headers, *, has_pdf: bool, now: datetime) -> bool:
    """Appelé par la lecture IMAP pour chaque message retenu : un avis d'organisme sans PDF devient une tâche.
    Rien d'autre que l'expéditeur, l'objet, la date et l'identifiant du message n'est utilisé.

    Le message vient de n'importe qui (l'en-tête Date se forge) : une date future est ramenée à maintenant, et une
    erreur en lisant ce qu'il annonce n'interrompt jamais la lecture de la boîte (le message est ignoré ici)."""
    try:
        received = min(_aware(headers.received_at) or now, now)
        notice = dom.notice_from_mail(headers.domain, headers.subject, received.date(), headers.message_id, has_pdf)
    except Exception:  # une tâche en moins, jamais une boîte mail bloquée
        log.warning("state_accounts.on_mail : message ignoré", exc_info=True)
        return False
    if notice is None or db.scalar(select(DocumentTask.id).where(DocumentTask.key == notice.key)) is not None:
        return False
    db.add(
        DocumentTask(
            key=notice.key,
            body=notice.body,
            origin="mail",
            expected=notice.expected,
            subject=(headers.subject or "(sans objet)")[:300],
            received_at=received,
            due=notice.due,
            status="open",
        )
    )
    db.flush()
    return True


# ------------------------------------------------------------------ attentes de saison et fermeture
def _paid_months(db: Session, today: date) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in analytics.aid_payments(db, today - timedelta(days=75)):
        try:
            org = aids_domain.organism_of(p.kind)
        except KeyError:
            continue
        out.setdefault(org, set()).add(f"{p.date.year:04d}-{p.date.month:02d}")
    return out


def _filed(db: Session, body: str, since: datetime) -> tuple[str, int | None] | None:
    """Le premier papier de cet organisme déposé depuis `since` : (« doc » | « fact », identifiant)."""
    spec = dom.BY_SLUG[body]
    doc = db.scalar(
        select(VaultDocument)
        .where(
            VaultDocument.kind == spec.doc_kind, VaultDocument.status.in_(READABLE), VaultDocument.created_at >= since
        )
        .order_by(VaultDocument.created_at)
        .limit(1)
    )
    if doc is not None:
        return "doc", doc.id
    fact_kind = FACT_KINDS.get(body)
    if fact_kind and db.scalar(
        select(OfficialFact.id).where(OfficialFact.kind == fact_kind, OfficialFact.imported_at >= since).limit(1)
    ):
        return "fact", None
    return None


def sync(db: Session, *, today: date, now: datetime) -> dict[str, int]:
    """Crée les attentes de saison du moment, ferme les tâches dont le PDF est arrivé. Local, sans réseau."""
    write_lock(db)
    created = closed = expired = 0
    for e in dom.seasonal(today, _paid_months(db, today)):
        if today < e.opens or db.scalar(select(DocumentTask.id).where(DocumentTask.key == e.key)) is not None:
            continue
        # L'attestation du mois remplace celle du mois d'avant, encore ouverte : une seule à récupérer.
        for old in db.scalars(
            select(DocumentTask).where(
                DocumentTask.origin == "season", DocumentTask.body == e.body, DocumentTask.status == "open"
            )
        ):
            if dom.same_expectation(old.key, e.key):
                old.status, old.closed_at = "expired", now
                expired += 1
        db.add(
            DocumentTask(
                key=e.key,
                body=e.body,
                origin="season",
                expected=e.expected,
                subject=None,
                received_at=datetime.combine(e.opens, time(0), tzinfo=UTC),
                due=e.due,
                status="open",
            )
        )
        created += 1
    db.flush()
    for t in db.scalars(select(DocumentTask).where(DocumentTask.status == "open")):
        if t.origin == "season" and today > dom.season_expires(t.key, t.due):
            t.status, t.closed_at = "expired", now  # la saison est passée : elle ne reste pas « en retard » à vie
            expired += 1
            continue
        spec = dom.BY_SLUG.get(t.body)
        if spec is None or t.expected in dom.NO_PDF:
            continue
        since = (_aware(t.received_at) or _aware(t.created_at) or now) - timedelta(days=1)
        found = _filed(db, t.body, since)
        if found is None:
            continue
        t.status, t.document_id, t.closed_at = "done", found[1], now
        closed += 1
    db.flush()
    if closed:
        audit.record(db, "system", "papers.task_done", count=closed)
    return {"doc_tasks_created": created, "doc_tasks_closed": closed, "doc_tasks_expired": expired}


def close(db: Session, task_id: int, *, status: str, actor: str, now: datetime) -> None:
    """Tu l'as récupéré (« done ») ou ce n'est pas pour toi (« dismissed »)."""
    if status not in ("done", "dismissed"):
        raise StateAccountsError("Décision inconnue", 422)
    write_lock(db)
    task = db.get(DocumentTask, task_id)
    if task is None or task.status != "open":
        raise StateAccountsError("Tâche introuvable ou déjà fermée")
    task.status, task.closed_at = status, now
    db.flush()
    audit.record(db, actor, "papers.task_close", body=task.body, status=status)


# ------------------------------------------------------------------ vue (Connecteurs › Comptes de l'État)
def _task_view(t: DocumentTask, today: date) -> dict[str, Any]:
    spec = dom.BY_SLUG.get(t.body)
    return {
        "id": t.id,
        "body": t.body,
        "body_name": spec.name if spec else t.body,
        "origin": t.origin,
        "expected": t.expected,
        "subject": t.subject,
        "received_at": _iso(t.received_at),
        "due": t.due.isoformat(),
        "late": t.status == "open" and t.due < today,
        "status": t.status,
        "entry_url": spec.entry_url if spec else None,
        "path": spec.path if spec else None,
        "document_id": t.document_id,
        "closed_at": _iso(t.closed_at),
        "self_closing": t.expected not in dom.NO_PDF,
    }


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _last_document(db: Session, body: dom.Body) -> dict[str, Any] | None:
    doc = db.scalar(
        select(VaultDocument)
        .where(VaultDocument.kind == body.doc_kind, VaultDocument.status.in_(READABLE))
        .order_by(VaultDocument.created_at.desc())
        .limit(1)
    )
    fact_kind = FACT_KINDS.get(body.slug)
    fact = (
        db.scalar(
            select(OfficialFact)
            .where(OfficialFact.kind == fact_kind)
            .order_by(OfficialFact.imported_at.desc())
            .limit(1)
        )
        if fact_kind
        else None
    )
    candidates: list[dict[str, Any]] = []
    if doc is not None:
        spec = vault_domain.KINDS.get(doc.kind)
        candidates.append(
            {
                "title": doc.title if not doc.sensitive else (spec.label if spec else "Document"),
                "day": (doc.document_date or doc.created_at.date()).isoformat(),
                "read_at": doc.created_at.isoformat(),
                "document_id": doc.id,
            }
        )
    if fact is not None:
        candidates.append(
            {"title": fact.title, "day": fact.imported_at.date().isoformat(), "read_at": fact.imported_at.isoformat()}
        )
    return max(candidates, key=lambda c: c["read_at"], default=None)


def view(db: Session, today: date) -> dict[str, Any]:
    tasks = list(db.scalars(select(DocumentTask).order_by(DocumentTask.due, DocumentTask.id)))
    open_tasks = [t for t in tasks if t.status == "open"]
    bodies = []
    for b in dom.BODIES:
        last = _last_document(db, b)
        mine = [t for t in open_tasks if t.body == b.slug]
        stale = last is not None and (today - date.fromisoformat(last["day"])).days > dom.RENEW_DAYS
        status = "to_fetch" if mine else "renew" if stale else "ok" if last else "none"
        bodies.append(
            {
                "slug": b.slug,
                "name": b.name,
                "entry_url": b.entry_url,
                "path": b.path,
                "expected": b.default_expected,
                "last_document": last,
                "open_tasks": len(mine),
                "status": status,
            }
        )
    recent = [t for t in tasks if t.status != "open"]
    recent.sort(key=lambda t: t.closed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return {
        "bodies": bodies,
        "tasks": [_task_view(t, today) for t in open_tasks],
        "recent": [_task_view(t, today) for t in recent[:8]],
        "checked_on": dom.CHECKED_ON,
        "note": (
            "Aucun organisme n'envoie ses PDF ni n'ouvre d'accès aux applications personnelles : Bank t'ouvre la bonne "
            "page et te dit quoi y chercher. Un mail d'avis sans pièce jointe (boîte mail reliée) devient une tâche ; "
            "elle se ferme seule quand tu déposes le PDF dans Papiers. Bank ne garde aucun identifiant de ces comptes."
        ),
    }
