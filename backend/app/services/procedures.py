"""Démarches suivies de bout en bout : création depuis un modèle sourcé (ou une opportunité), préremplissage avec la
provenance de chaque valeur, pièces tirées du coffre, évaluation (manques, contradictions), revue complète,
validation liée à l'empreinte du dossier, marquage « envoyé » unique, relances et résultat.

Bank ne transmet rien lui-même : il prépare le dossier, et c'est toi qui l'envoies (site officiel, messagerie de ta
banque, courrier). « Envoyé » est une déclaration de ta part, possible seulement après ta validation du dossier exact.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import procedures as dp
from app.domain import vault as dv
from app.models import (
    Account,
    OfficialFact,
    OpportunityState,
    Procedure,
    ProcedureEvent,
    ValueEvent,
    VaultDocument,
)
from app.security import audit
from app.services import advisor, analytics

OUTCOMES = {"obtenu": "Obtenu", "refuse": "Refusé", "termine": "Terminé", "abandon": "Abandonné"}


class ProcedureError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _template(key: str) -> dp.Template:
    tpl = dp.TEMPLATES.get(key)
    if tpl is None:
        raise ProcedureError("Modèle de démarche inconnu", 404)
    return tpl


def _get(db: Session, proc_id: int) -> Procedure:
    proc = db.get(Procedure, proc_id)
    if proc is None:
        raise ProcedureError("Démarche introuvable", 404)
    return proc


def _data(proc: Procedure) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(proc.data)
    data.setdefault("fields", {})
    data.setdefault("pieces", {})
    data.setdefault("notes", "")
    return data


def _event(proc: Procedure, kind: str, **detail: Any) -> None:
    proc.events.append(ProcedureEvent(kind=kind, detail=json.dumps(detail, ensure_ascii=False)))


# ------------------------------------------------------------------ préremplissage
def _doc_month(doc: VaultDocument) -> str | None:
    for f in doc.fields:
        if f.key == "periode":
            return str(json.loads(f.value))
    return doc.document_date.strftime("%Y-%m") if doc.document_date else None


def _vault_docs(db: Session, kinds: tuple[str, ...]) -> list[VaultDocument]:
    rows = db.scalars(
        select(VaultDocument).where(VaultDocument.kind.in_(kinds), VaultDocument.status.in_(("to_review", "verified")))
    )
    return sorted(rows, key=lambda d: (d.document_date or date.min, d.id), reverse=True)


def _field_value(doc: VaultDocument, *keys: str) -> tuple[Any, str | None] | None:
    by = {f.key: f for f in doc.fields}
    for key in keys:
        if key in by:
            return json.loads(by[key].value), by[key].evidence
    return None


def _main_account(db: Session) -> Account | None:
    accounts = list(db.scalars(select(Account).where(Account.archived.is_(False)).order_by(Account.id)))
    return next((a for a in accounts if a.kind == "checking"), accounts[0] if accounts else None)


def prefill(
    db: Session, tpl: dp.Template, data: dict[str, Any], initial: dict[str, Any] | None = None, *, today: date
) -> None:
    """Remplit ce que Bank sait déjà, en disant d'où vient chaque valeur ; ne touche jamais une valeur saisie."""
    fields: dict[str, Any] = data["fields"]

    def put(key: str, value: Any, source: str, **provenance: Any) -> None:
        fdef = dp.field_def(tpl, key)
        if fdef is None or value in (None, "") or key in fields:
            return
        try:
            value = dp.coerce(fdef, value)
        except ValueError:
            return
        entry: dict[str, Any] = {"value": value, "source": source, **provenance}
        if source == "vault":  # le justificatif d'origine : sert à repérer une contradiction ensuite
            entry["prefilled"] = {
                k: v for k, v in entry.items() if k in ("value", "document_id", "evidence", "vault_key", "period")
            }
        fields[key] = entry

    for key, value in (initial or {}).items():
        put(key, value, "opportunite")
    answers = advisor.plain_answers(db)
    household = analytics.load_profile(db) or {}
    account = _main_account(db)
    if tpl.key == "prime_activite":
        couple = household.get("couple")
        if couple is None and answers.get("household"):
            couple = str(answers["household"]).startswith("couple")
        if couple is not None:
            put("situation", "couple" if couple else "seul", "profil")
        kids = household.get("children_birth_dates")
        put("enfants", len(kids) if kids is not None else answers.get("dependents"), "profil")
        housing = str(answers.get("housing_status") or household.get("housing") or "")
        logement = (
            "locataire"
            if housing.startswith("locataire")
            else "proprietaire"
            if housing.startswith(("proprietaire", "accedant"))
            else "heberge"
            if housing.startswith("heberge")
            else None
        )
        put("logement", logement, "profil")
        if logement == "locataire":
            put("loyer", household.get("rent_cents") or answers.get("housing_cost"), "profil")
        months: dict[str, VaultDocument] = {}
        for doc in _vault_docs(db, ("payslip",)):
            month = _doc_month(doc)
            if month and month not in months:
                months[month] = doc
        for key, month in zip(("salaire_m1", "salaire_m2", "salaire_m3"), sorted(months, reverse=True), strict=False):
            for vault_key in ("net_social", "net_avant_impot"):  # la Caf demande le net social (sinon net avant impôt)
                found = _field_value(months[month], vault_key)
                if found is not None:
                    put(key, found[0], "vault", document_id=months[month].id, evidence=found[1], period=month,
                        vault_key=vault_key)  # fmt: skip
                    break
    if tpl.key == "demander_css":
        couple = household.get("couple")
        if couple is not None:
            put("situation", "couple" if couple else "seul", "profil")
            kids = household.get("children_birth_dates")
            if kids is not None:
                put("personnes", 1 + int(bool(couple)) + len(kids), "profil")
    if tpl.key in ("prime_activite", "demander_css"):
        if _vault_docs(db, ("caf",)):
            put("allocataire", "oui", "coffre")
        elif any(s.organism == "caf" for s in analytics.aids_summary(db, today).streams):
            put("allocataire", "oui", "comptes")  # des versements de la Caf arrivent sur tes comptes
    if tpl.key in ("contester_frais", "ouvrir_lep") and account is not None:
        if account.institution:
            put("banque", account.institution, "comptes")
        if tpl.key == "contester_frais" and account.iban:
            put("compte", "•••• " + account.iban.replace(" ", "")[-4:], "comptes")
    if tpl.key == "ouvrir_lep":
        for doc in _vault_docs(db, ("tax_notice",)):
            for key, src in (("rfr", "rfr"), ("parts", "parts")):
                found = _field_value(doc, src)
                if found is not None:
                    put(key, found[0], "vault", document_id=doc.id, evidence=found[1], vault_key=src)
            break
        fact = db.scalars(
            select(OfficialFact).where(OfficialFact.kind == "avis_impot").order_by(OfficialFact.period.desc())
        ).first()
        if fact is not None:
            values = json.loads(fact.data)
            put("rfr", values.get("rfr_cents"), "officiel")
            put("parts", values.get("parts"), "officiel")


def _auto_attach(db: Session, tpl: dp.Template, data: dict[str, Any], today: date) -> None:
    """Joint d'office les pièces obligatoires que le coffre contient (les plus récentes) ; les facultatives restent
    à ton choix — un dossier ne contient que le nécessaire."""
    used = {v.get("document_id") for v in data["fields"].values() if v.get("document_id")}
    for pdef in tpl.pieces:
        if not pdef.required or data["pieces"].get(pdef.key):
            continue
        candidates = [
            d
            for d in _vault_docs(db, pdef.kinds)
            if pdef.max_age_days is None or not d.document_date or (today - d.document_date).days <= pdef.max_age_days
        ]
        candidates.sort(key=lambda d: (d.id not in used,))  # ceux qui ont servi aux valeurs d'abord
        data["pieces"][pdef.key] = [d.id for d in candidates[: pdef.count]]


# ------------------------------------------------------------------ cycle de vie
def create(
    db: Session,
    template_key: str,
    *,
    actor: str,
    today: date,
    opportunity_key: str | None = None,
    initial: dict[str, Any] | None = None,
    title: str | None = None,
) -> Procedure:
    tpl = _template(template_key)
    if opportunity_key:
        existing = db.scalar(
            select(Procedure).where(
                Procedure.opportunity_key == opportunity_key, Procedure.stage.not_in(("done", "abandoned"))
            )
        )
        if existing is not None:
            return existing  # une opportunité = une démarche en cours (pas de doublon)
    data: dict[str, Any] = {"fields": {}, "pieces": {}, "notes": ""}
    prefill(db, tpl, data, initial, today=today)
    _auto_attach(db, tpl, data, today)
    proc = Procedure(
        template=tpl.key,
        title=(title or tpl.title)[:120],
        stage="verify",
        data=json.dumps(data, ensure_ascii=False),
        opportunity_key=opportunity_key,
    )
    db.add(proc)
    db.flush()
    _event(proc, "created", template=tpl.key, prefilled=sorted(data["fields"]))
    _restage(db, proc, today)
    if opportunity_key:
        state = db.get(OpportunityState, opportunity_key) or OpportunityState(key=opportunity_key, status="started")
        state.status, state.procedure_id = "started", proc.id
        db.merge(state)
    db.flush()
    audit.record(db, actor, "procedure.create", procedure=proc.id, template=tpl.key)
    return proc


def _attached(db: Session, data: dict[str, Any]) -> dict[str, list[dp.AttachedDoc]]:
    out: dict[str, list[dp.AttachedDoc]] = {}
    for key, ids in data["pieces"].items():
        docs = []
        for doc_id in ids:
            doc = db.get(VaultDocument, doc_id)
            if doc is None or doc.status in ("refused", "processing", "failed"):
                continue
            docs.append(dp.AttachedDoc(doc.id, doc.kind, doc.title, doc.status, doc.document_date, doc.sha256))
        out[key] = docs
    return out


def _follow_documents(db: Session, tpl: dp.Template, data: dict[str, Any]) -> None:
    """Une valeur tirée du coffre suit son justificatif : corrigé dans le coffre, il l'est ici ; une valeur que tu
    as changée reste la tienne, mais elle est comparée au justificatif tel qu'il est maintenant."""
    for key, entry in data["fields"].items():
        ref = entry.get("prefilled")
        if not ref or not ref.get("document_id") or not ref.get("vault_key"):
            continue
        doc = db.get(VaultDocument, ref["document_id"])
        found = _field_value(doc, ref["vault_key"]) if doc is not None else None
        fdef = dp.field_def(tpl, key)
        if found is None or fdef is None:
            continue
        try:
            live = dp.coerce(fdef, found[0])
        except ValueError:
            continue
        ref["value"], ref["evidence"] = live, found[1]
        if entry.get("source") == "vault":
            entry["value"], entry["evidence"] = live, found[1]


def _state(db: Session, proc: Procedure, today: date) -> tuple[dp.Template, dict[str, Any], dp.Evaluation, str]:
    tpl = _template(proc.template)
    data = _data(proc)
    _follow_documents(db, tpl, data)
    pieces = _attached(db, data)
    evaluation = dp.evaluate(tpl, data["fields"], pieces, today)
    current = dp.fingerprint(dp.canonical(tpl, data["fields"], pieces, dp.render_letter(tpl, data["fields"], None)))
    return tpl, data, evaluation, current


def _restage(db: Session, proc: Procedure, today: date) -> None:
    if proc.stage in dp.OPEN_STAGES:
        proc.stage = _state(db, proc, today)[2].stage


def _ensure_open(proc: Procedure) -> None:
    if proc.stage not in dp.OPEN_STAGES:
        raise ProcedureError("Démarche déjà envoyée ou close : elle ne se modifie plus", 409)


def set_fields(db: Session, proc_id: int, values: dict[str, Any], *, actor: str, today: date) -> Procedure:
    proc = _get(db, proc_id)
    _ensure_open(proc)
    tpl = _template(proc.template)
    data = _data(proc)
    _follow_documents(db, tpl, data)
    changed = []
    for key, raw in values.items():
        fdef = dp.field_def(tpl, key)
        if fdef is None:
            raise ProcedureError(f"Champ inconnu : {key}", 404)
        try:
            value = dp.parse(fdef, raw)
        except ValueError as exc:
            raise ProcedureError(f"{fdef.label} : {exc}") from exc
        entry = data["fields"].get(key, {})
        prefilled = entry.get("prefilled")
        if value is None:
            data["fields"].pop(key, None)
        elif prefilled is not None and prefilled.get("value") == value:
            data["fields"][key] = {**prefilled, "source": "vault", "prefilled": prefilled}  # retour au justificatif
        else:
            data["fields"][key] = {"value": value, "source": "user", **({"prefilled": prefilled} if prefilled else {})}
        changed.append(key)
    proc.data = json.dumps(data, ensure_ascii=False)
    _event(proc, "fields", keys=changed)  # jamais les valeurs dans le fil
    _restage(db, proc, today)
    db.flush()
    audit.record(db, actor, "procedure.fields", procedure=proc.id, fields=len(changed))
    return proc


def restore_field(db: Session, proc_id: int, key: str, *, actor: str, today: date) -> Procedure:
    """« Reprendre la valeur du justificatif » : la valeur redevient celle lue dans le document, provenance comprise."""
    proc = _get(db, proc_id)
    _ensure_open(proc)
    tpl = _template(proc.template)
    data = _data(proc)
    _follow_documents(db, tpl, data)
    entry = data["fields"].get(key)
    prefilled = (entry or {}).get("prefilled")
    if not prefilled:
        raise ProcedureError("Cette valeur ne vient d'aucun document", 409)
    data["fields"][key] = {**prefilled, "source": "vault", "prefilled": prefilled}
    proc.data = json.dumps(data, ensure_ascii=False)
    _event(proc, "fields", keys=[key], restored=True)
    _restage(db, proc, today)
    db.flush()
    audit.record(db, actor, "procedure.restore", procedure=proc.id)
    return proc


def set_pieces(
    db: Session, proc_id: int, piece_key: str, document_ids: list[int], *, actor: str, today: date
) -> Procedure:
    proc = _get(db, proc_id)
    _ensure_open(proc)
    tpl = _template(proc.template)
    pdef = next((p for p in tpl.pieces if p.key == piece_key), None)
    if pdef is None:
        raise ProcedureError("Pièce inconnue pour cette démarche", 404)
    ids = list(dict.fromkeys(document_ids))
    if len(ids) > max(pdef.count, 1) * 2:
        raise ProcedureError("Trop de documents pour cette pièce")
    for doc_id in ids:
        doc = db.get(VaultDocument, doc_id)
        if doc is None or doc.status in ("refused", "processing", "failed"):
            raise ProcedureError("Document introuvable ou illisible dans le coffre", 404)
        if doc.kind not in pdef.kinds:
            raise ProcedureError(f"Ce document n'est pas un(e) {pdef.label.lower()}")
    data = _data(proc)
    data["pieces"][piece_key] = ids
    proc.data = json.dumps(data, ensure_ascii=False)
    _event(proc, "pieces", piece=piece_key, count=len(ids))
    _restage(db, proc, today)
    db.flush()
    audit.record(db, actor, "procedure.pieces", procedure=proc.id, piece=piece_key)
    return proc


def update(db: Session, proc_id: int, *, actor: str, notes: str | None = None, title: str | None = None) -> Procedure:
    proc = _get(db, proc_id)
    data = _data(proc)
    if notes is not None:
        data["notes"] = dp.parse(dp.FieldDef("notes", "Notes", "longtext"), notes) or ""
    if title is not None:
        clean = " ".join(title.split())[:120]
        if not clean:
            raise ProcedureError("Titre vide")
        proc.title = clean
    proc.data = json.dumps(data, ensure_ascii=False)
    db.flush()
    audit.record(db, actor, "procedure.update", procedure=proc.id)
    return proc


def approve(
    db: Session, proc_id: int, *, payload_hash: str, declarations: list[str], actor: str, today: date
) -> Procedure:
    """Ta validation, liée à l'empreinte du dossier que tu as relu : tout changement ensuite l'annule."""
    proc = _get(db, proc_id)
    _ensure_open(proc)
    tpl, _, evaluation, current = _state(db, proc, today)
    if evaluation.stage != "ready":
        raise ProcedureError("Le dossier n'est pas prêt : complète ou corrige d'abord ce qui est signalé", 409)
    if payload_hash != current:
        raise ProcedureError("Le dossier a changé depuis ta relecture : relis-le avant de valider", 409)
    missing = [d.key for d in tpl.declarations if d.key not in declarations]
    if missing:
        raise ProcedureError("Coche chaque engagement pour valider", 422)
    proc.approval = json.dumps({"hash": current, "at": utcnow().isoformat(), "declarations": sorted(declarations)})
    _event(proc, "approved", hash=current[:12])
    db.flush()
    audit.record(db, actor, "procedure.approve", procedure=proc.id, hash=current[:12])
    return proc


def _approved_hash(proc: Procedure) -> str | None:
    return str(json.loads(proc.approval)["hash"]) if proc.approval else None


def mark_sent(
    db: Session,
    proc_id: int,
    *,
    actor: str,
    today: date,
    reference: str | None = None,
    sent_on: date | None = None,
) -> Procedure:
    """« Je l'ai envoyé » : seulement le dossier validé, une seule fois (même deux clics ou deux onglets)."""
    proc = _get(db, proc_id)
    if proc.stage in ("sent", "waiting", "done") and proc.submission_key:
        return proc  # déjà marqué : rien ne se répète
    _ensure_open(proc)
    tpl, data, _evaluation, current = _state(db, proc, today)
    if _approved_hash(proc) != current:
        raise ProcedureError("Valide d'abord le dossier tel qu'il sera envoyé (revue)", 409)
    twin = db.scalar(select(Procedure).where(Procedure.submission_key == current, Procedure.id != proc.id))
    if twin is not None:
        raise ProcedureError(f"Ce même dossier a déjà été envoyé (démarche n° {twin.id})", 409)
    when = sent_on or today
    if when > today or when < today - timedelta(days=365):
        raise ProcedureError("Date d'envoi invalide")
    clean_ref = " ".join((reference or "").split())[:80] or None
    proc.submission_key, proc.reference, proc.submitted_at = current, clean_ref, utcnow()
    proc.stage = "waiting"
    proc.next_check = dp.next_check(tpl, when)
    if tpl.key == "contester_frais":  # médiateur : après deux mois sans réponse, et dans l'année
        data["escalation"] = {
            "label": "Saisir le médiateur de ta banque",
            "from": (when + timedelta(days=60)).isoformat(),
            "until": (when + timedelta(days=365)).isoformat(),
        }
        proc.deadline = when + timedelta(days=365)
    data["sent_on"] = when.isoformat()
    proc.data = json.dumps(data, ensure_ascii=False)
    _event(proc, "sent", on=when.isoformat(), reference=bool(clean_ref))
    db.flush()
    audit.record(db, actor, "procedure.sent", procedure=proc.id, hash=current[:12])
    return proc


def snooze(db: Session, proc_id: int, *, days: int, actor: str, today: date) -> Procedure:
    proc = _get(db, proc_id)
    if proc.stage != "waiting":
        raise ProcedureError("Seule une démarche en attente se relance plus tard", 409)
    if not 1 <= days <= 120:
        raise ProcedureError("Entre 1 et 120 jours")
    proc.next_check = today + timedelta(days=days)
    _event(proc, "snoozed", days=days)
    db.flush()
    return proc


def close(
    db: Session,
    proc_id: int,
    *,
    outcome: str,
    actor: str,
    today: date,
    amount_cents: int | None = None,
    period: str = "once",
    note: str | None = None,
) -> Procedure:
    """Résultat : obtenu (avec le montant, compté comme gain confirmé), refusé, terminé ou abandonné."""
    proc = _get(db, proc_id)
    if proc.stage in ("done", "abandoned"):
        raise ProcedureError("Démarche déjà close", 409)
    if outcome not in OUTCOMES:
        raise ProcedureError("Résultat inconnu")
    if period not in ("once", "month", "year"):
        raise ProcedureError("Périodicité inconnue")
    if amount_cents is not None and not 0 < amount_cents <= 100_000_000:
        raise ProcedureError("Montant hors limites")
    data = _data(proc)
    data["outcome"] = {"result": outcome, "amount_cents": amount_cents, "period": period, "note": (note or "")[:300]}
    proc.data = json.dumps(data, ensure_ascii=False)
    proc.stage = "abandoned" if outcome == "abandon" else "done"
    proc.closed_at, proc.next_check = utcnow(), None
    key = proc.opportunity_key or f"procedure:{proc.id}"
    already = db.scalar(select(ValueEvent.id).where(ValueEvent.opportunity_key == key, ValueEvent.kind == "confirmed"))
    if outcome == "obtenu" and amount_cents and already is None:  # un gain ne se compte qu'une fois
        db.add(
            ValueEvent(
                opportunity_key=key,
                kind="confirmed",
                amount_cents=amount_cents,
                period=period,
                on_date=today,
                label=proc.title,
                evidence=f"Démarche n° {proc.id} : résultat déclaré par toi",
                source="user",
            )
        )
    if proc.opportunity_key:
        state = db.get(OpportunityState, proc.opportunity_key)
        if state is not None:
            state.status = "done" if outcome != "abandon" else "dismissed"
    _event(proc, "closed", outcome=outcome)
    db.flush()
    audit.record(db, actor, "procedure.close", procedure=proc.id, outcome=outcome)
    return proc


def delete(db: Session, proc_id: int, *, actor: str) -> None:
    proc = _get(db, proc_id)
    if proc.opportunity_key:
        state = db.get(OpportunityState, proc.opportunity_key)
        if state is not None and state.procedure_id == proc.id:
            db.delete(state)
    db.delete(proc)
    db.flush()
    audit.record(db, actor, "procedure.delete", procedure=proc_id)


# ------------------------------------------------------------------ vues
def _provenance(db: Session, entry: dict[str, Any]) -> dict[str, Any] | None:
    source = entry.get("source")
    labels = {
        "vault": "Lu dans le coffre",
        "profil": "Ton profil",
        "comptes": "Tes comptes",
        "officiel": "Avis d'impôt repris dans Bank",
        "opportunite": "Calculé par Bank",
        "coffre": "Tes documents",
        "user": "Saisi par toi",
    }
    out: dict[str, Any] = {"source": source, "label": labels.get(str(source), str(source))}
    doc_id = entry.get("document_id") or (entry.get("prefilled") or {}).get("document_id")
    if doc_id:
        doc = db.get(VaultDocument, doc_id)
        out.update(document_id=doc_id, document_title=doc.title if doc else "document supprimé")
    if entry.get("evidence"):
        out["evidence"] = entry["evidence"]
    if entry.get("period"):
        out["period"] = dv.month_label(str(entry["period"]))
    return out


def view(db: Session, proc_id: int, today: date) -> dict[str, Any]:
    proc = _get(db, proc_id)
    tpl, data, evaluation, current = _state(db, proc, today)
    values = {k: v.get("value") for k, v in data["fields"].items()}
    active = {f.key for f in dp.active_fields(tpl, values)}
    pieces = _attached(db, data)
    approved_hash = _approved_hash(proc)
    fields = []
    for fdef in tpl.fields:
        entry = data["fields"].get(fdef.key, {})
        fields.append(
            {
                "key": fdef.key,
                "label": fdef.label,
                "type": fdef.type,
                "required": fdef.required,
                "active": fdef.key in active,
                "help": fdef.help,
                "choices": [{"value": k, "label": v} for k, v in fdef.choices],
                "value": entry.get("value"),
                "display": dp.display(fdef, entry.get("value")),
                "provenance": _provenance(db, entry) if entry else None,
                "missing": fdef.key in evaluation.missing_fields,
                "conflict": next((c for c in evaluation.conflicts if c["key"] == fdef.key), None),
            }
        )
    piece_views = []
    for pdef in tpl.pieces:
        attached_ids = {d.id for d in pieces.get(pdef.key, [])}
        piece_views.append(
            {
                "key": pdef.key,
                "label": pdef.label,
                "required": pdef.required,
                "count": pdef.count,
                "help": pdef.help,
                "missing": pdef.key in evaluation.missing_pieces,
                "attached": [_doc_view(d) for d in pieces.get(pdef.key, [])],
                "candidates": [_doc_view_row(d) for d in _vault_docs(db, pdef.kinds) if d.id not in attached_ids][:12],
            }
        )
    sent_on = date.fromisoformat(data["sent_on"]) if data.get("sent_on") else None
    letter = dp.render_letter(tpl, data["fields"], sent_on or today)
    due = proc.stage == "waiting" and proc.next_check is not None and proc.next_check <= today
    return {
        "id": proc.id,
        "title": proc.title,
        "template": _template_view(tpl),
        "stage": proc.stage,
        "stage_label": dp.STAGE_LABELS[proc.stage],
        "fields": fields,
        "pieces": piece_views,
        "evaluation": {
            "missing_fields": evaluation.missing_fields,
            "conflicts": evaluation.conflicts,
            "missing_pieces": evaluation.missing_pieces,
            "unverified": evaluation.unverified,
            "warnings": evaluation.warnings,
        },
        "letter": letter,
        "hash": current,
        "approved": approved_hash == current,
        "approved_stale": approved_hash is not None and approved_hash != current and proc.stage in dp.OPEN_STAGES,
        "reference": proc.reference,
        "notes": data.get("notes") or "",
        "sent_on": data.get("sent_on"),
        "next_check": proc.next_check.isoformat() if proc.next_check else None,
        "due": due,
        "deadline": proc.deadline.isoformat() if proc.deadline else None,
        "escalation": data.get("escalation"),
        "outcome": data.get("outcome"),
        "opportunity_key": proc.opportunity_key,
        "created_at": proc.created_at.isoformat(),
        "updated_at": proc.updated_at.isoformat(),
        "events": [
            {"ts": e.ts.isoformat(), "kind": e.kind, "detail": json.loads(e.detail)} for e in reversed(proc.events)
        ],
    }


def _doc_view(d: dp.AttachedDoc) -> dict[str, Any]:
    return {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "document_date": d.document_date.isoformat() if d.document_date else None,
    }


def _doc_view_row(d: VaultDocument) -> dict[str, Any]:
    return {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "document_date": d.document_date.isoformat() if d.document_date else None,
    }


def _template_view(tpl: dp.Template) -> dict[str, Any]:
    return {
        "key": tpl.key,
        "title": tpl.title,
        "summary": tpl.summary,
        "category": tpl.category,
        "organisme": tpl.organisme,
        "channel": tpl.channel,
        "recipient": tpl.recipient,
        "official_url": tpl.official_url,
        "steps": list(tpl.steps),
        "sources": [
            {"title": s.title, "url": s.url, "verified_on": s.verified_on, "kind": s.kind} for s in tpl.sources
        ],
        "confidence": tpl.confidence,
        "declarations": [{"key": d.key, "text": d.text, "kind": d.kind} for d in tpl.declarations],
        "followup_label": tpl.followup_label,
        "has_letter": tpl.letter is not None,
    }


def listing(db: Session, today: date) -> dict[str, Any]:
    procs = list(db.scalars(select(Procedure).order_by(Procedure.updated_at.desc())))
    rows = []
    for p in procs:
        rows.append(
            {
                "id": p.id,
                "title": p.title,
                "template": p.template,
                "stage": p.stage,
                "stage_label": dp.STAGE_LABELS.get(p.stage, p.stage),
                "next_check": p.next_check.isoformat() if p.next_check else None,
                "due": p.stage == "waiting" and p.next_check is not None and p.next_check <= today,
                "deadline": p.deadline.isoformat() if p.deadline else None,
                "updated_at": p.updated_at.isoformat(),
                "opportunity_key": p.opportunity_key,
            }
        )
    return {
        "procedures": rows,
        "stages": [{"key": k, "label": dp.STAGE_LABELS[k]} for k in dp.STAGES],
        "templates": [_template_view(t) for t in dp.TEMPLATES.values()],
    }


def due(db: Session, today: date) -> list[Procedure]:
    return list(
        db.scalars(
            select(Procedure)
            .where(Procedure.stage == "waiting", Procedure.next_check.is_not(None), Procedure.next_check <= today)
            .order_by(Procedure.next_check)
        )
    )
