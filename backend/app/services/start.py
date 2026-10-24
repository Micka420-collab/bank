"""Démarrer avec Bank (DESIGN.md § 3.10) : le parcours de première ouverture et la liste « Bien démarrer ».

Tout « fait » se recalcule à chaque lecture à partir des tables réelles (requêtes `exists` légères, aucune lecture
réseau) : la liste dit la même chose sur le téléphone et sur l'ordinateur. Le seul état gardé est une clé JSON
`start_journey` dans `app_settings`, faite de dates et de clés d'étape — jamais une réponse, un montant, une adresse
ni un secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import (
    Account,
    AgentToken,
    AutonomyMandate,
    BackupRun,
    BankConnection,
    ChannelLink,
    ConnectionStatus,
    CryptoWallet,
    EnergyConnection,
    ExchangeKey,
    LlmProvider,
    MailAccount,
    OfficialFact,
    Passkey,
    SearchProvider,
    Transaction,
    User,
    VaultDocument,
    WorkPlan,
)
from app.security import audit
from app.services import advisor, consents
from app.services.settings_store import get_setting, set_setting

KEY = "start_journey"
COLLAPSE_DAYS = 7  # la liste terminée reste une pilule une semaine, puis s'en va

# Les cinq réponses de l'étape « Ton foyer » (les mêmes que dans Ma situation).
HOUSEHOLD = ("employment", "net_monthly_income", "household", "dependents", "housing_status")


class StartError(ValueError):
    """Demande refusée (étape inconnue, accord qui n'est pas public) : le message dit pourquoi."""


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    minutes: int


STEPS: tuple[Step, ...] = (
    Step("banque", "Ta banque", 5),
    Step("foyer", "Ton foyer", 1),
    Step("accords", "Tes accords", 1),
    Step("papier", "Ton premier papier", 1),
    Step("priorites", "Tes priorités", 1),
    Step("prevenir", "Où te prévenir", 1),
    Step("visite", "La visite", 1),
)
STEP_KEYS = tuple(s.key for s in STEPS)

# Éléments de la liste : (clé, titre, pourquoi, minutes, lien). Les six premiers reprennent les étapes du parcours.
ESSENTIALS: tuple[tuple[str, str, str, int, str], ...] = (
    ("banque", "Relier ta banque", "Plus aucun relevé à pointer à la main.", 5, "#/bienvenue/banque"),
    (
        "foyer",
        "Décrire ton foyer",
        "Bank simule tes droits : prime d'activité, aides au logement.",
        1,
        "#/bienvenue/foyer",
    ),
    (
        "accords",
        "Tes accords pour les données publiques",
        "Barèmes à jour, jours fériés, Tempo : sans rien saisir.",
        1,
        "#/bienvenue/accords",
    ),
    (
        "papier",
        "Ranger un premier papier",
        "Un avis d'impôt préremplit revenu fiscal, parts et échéances.",
        1,
        "#/bienvenue/papier",
    ),
    (
        "priorites",
        "Choisir tes priorités",
        "Les pistes qui comptent pour toi remontent en premier.",
        1,
        "#/bienvenue/priorites",
    ),
    ("prevenir", "Être prévenu", "Un découvert vu trois jours avant ne coûte rien.", 1, "#/bienvenue/prevenir"),
    (
        "securite",
        "Protéger ton accès",
        "Double authentification ou clé d'accès : ton mot de passe ne suffit plus.",
        2,
        "#/reglages?section=sec-title",
    ),
    (
        "sauvegarde",
        "Vérifier ta sauvegarde",
        "Une restauration d'essai prouve que tu peux tout récupérer.",
        1,
        "#/reglages?section=backup-title",
    ),
)
MORE: tuple[tuple[str, str, str, int, str], ...] = (
    (
        "assistant",
        "Brancher l'assistant IA",
        "Pose tes questions d'argent en français, avec ta clé OpenRouter.",
        3,
        "#/connecteurs?section=assistant-title",
    ),
    (
        "linky",
        "Relier ton compteur Linky",
        "Ta facture d'électricité prévue, ton option tarifaire vérifiée.",
        3,
        "#/connecteurs?section=linky-title",
    ),
    (
        "mail",
        "Relier ta boîte mail",
        "Factures et attestations reçues par mail, proposées dans Papiers.",
        3,
        "#/connecteurs?section=mail-title",
    ),
    (
        "planning",
        "Relier ton planning de travail",
        "Ta paie prévue au jour près, heures sup. comprises.",
        3,
        "#/connecteurs?section=planning-title",
    ),
    (
        "powens",
        "Ta banque par Powens",
        "Si ta banque n'est pas chez Enable Banking.",
        5,
        "#/connecteurs?section=banks-title",
    ),
    (
        "pilote",
        "Essayer le pilote automatique",
        "Ton agent agit seul, dans ton cadre ; tu coupes d'un geste.",
        5,
        "#/pilote",
    ),
    ("crypto", "Suivre tes cryptos", "Leur valeur dans ton patrimoine et l'impôt à prévoir.", 2, "#/crypto"),
)

# Les sections de Connecteurs, dans l'ordre de la page : (clé, titre court, id du titre de section).
CONNECTORS: tuple[tuple[str, str, str], ...] = (
    ("banques", "Banques", "banks-title"),
    ("planning", "Planning", "planning-title"),
    ("linky", "Linky", "linky-title"),
    ("etat", "État", "etat-title"),
    ("assistant", "Assistant IA", "assistant-title"),
    ("recherche", "Recherche web", "search-title"),
    ("mail", "Boîte mail", "mail-title"),
    ("agents", "Agents IA", "agents-title"),
    ("messageries", "Messageries", "ch-link-title"),
)


# ------------------------------------------------------------------ état gardé
def _state(db: Session) -> dict[str, Any]:
    raw = get_setting(db, KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save(db: Session, state: dict[str, Any]) -> None:
    allowed = {
        "started_at",
        "current",
        "skipped",
        "finished_at",
        "tour_done_at",
        "checklist_completed_at",
        "dismissed_at",
    }
    set_setting(db, KEY, {k: v for k, v in state.items() if k in allowed})


def _save_on_read(db: Session, state: dict[str, Any]) -> None:
    """Écriture faite pendant une lecture (migration d'un compte existant, liste terminée) : plusieurs lectures
    simultanées peuvent la tenter ; la première gagne, les autres la laissent (même contenu, à la seconde près)."""
    try:
        with db.begin_nested():
            _save(db, state)
    except IntegrityError:
        db.expire_all()


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ------------------------------------------------------------------ « fait », à partir des tables réelles
def _any(db: Session, *criteria: Any) -> bool:
    return bool(db.scalar(select(exists().where(*criteria))))


def has_money_data(db: Session) -> bool:
    """Une banque active, une opération importée ou un compte saisi avec un solde."""
    return (
        _any(db, BankConnection.status == ConnectionStatus.ACTIVE)
        or _any(db, Transaction.id.is_not(None))
        or _any(db, Account.archived.is_(False), Account.balance_cents != 0)
    )


def _household_done(db: Session) -> bool:
    answers = advisor.plain_answers(db)
    return all(answers.get(k) not in (None, "", []) for k in HOUSEHOLD)


def _security_done(db: Session) -> bool:
    return _any(db, User.totp_enabled.is_(True)) or _any(db, Passkey.id.is_not(None))


def _backup_verified(db: Session) -> bool:
    last = db.scalar(select(BackupRun.status).where(BackupRun.kind == "verify").order_by(BackupRun.id.desc()).limit(1))
    return last == "ok"


def _eb_bank(db: Session) -> bool:
    return _any(db, BankConnection.status == ConnectionStatus.ACTIVE, BankConnection.provider.is_(None))


def facts(db: Session, state: dict[str, Any]) -> dict[str, bool]:
    """Chaque clé (étape, élément de la liste, section de Connecteurs) → fait ou non, maintenant."""
    linked = _any(db, ChannelLink.revoked_at.is_(None))
    return {
        "banque": has_money_data(db),
        "foyer": _household_done(db),
        "accords": all(consents.decided(db, p) for p in consents.PUBLIC_PURPOSES),
        "papier": _any(db, VaultDocument.id.is_not(None)),
        "priorites": bool(get_setting(db, "opportunity_priorities")),
        "prevenir": linked,
        "visite": bool(state.get("tour_done_at")),
        "securite": _security_done(db),
        "sauvegarde": _backup_verified(db),
        "planning": _any(db, WorkPlan.id.is_not(None)),
        "assistant": _any(db, LlmProvider.id.is_not(None)),
        "linky": _any(db, EnergyConnection.status == "active"),
        "mail": _any(db, MailAccount.id.is_not(None)),
        "powens": _any(db, BankConnection.status == ConnectionStatus.ACTIVE, BankConnection.provider == "powens"),
        "crypto": _any(db, CryptoWallet.id.is_not(None)) or _any(db, ExchangeKey.id.is_not(None)),
        "pilote": _any(db, AutonomyMandate.enabled.is_(True)),
        # Sections de Connecteurs
        "banques": _any(db, BankConnection.status == ConnectionStatus.ACTIVE),
        "etat": _any(db, OfficialFact.id.is_not(None)),
        "recherche": _any(db, SearchProvider.id.is_not(None)),
        "agents": _any(db, AgentToken.revoked_at.is_(None)),
        "messageries": linked,
    }


# ------------------------------------------------------------------ lecture
def _status(state: dict[str, Any]) -> str:
    if state.get("finished_at"):
        return "finished"
    if state.get("started_at"):
        return "in_progress"
    return "new"


def _migrate(db: Session, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Compte d'avant le parcours : s'il a déjà une banque ou des opérations, on ne lui impose pas l'accueil."""
    if state or not has_money_data(db):
        return state
    state = {"started_at": _iso(now), "finished_at": _iso(now), "skipped": [], "current": None}
    _save_on_read(db, state)
    return state


def _next_step(done: dict[str, bool], skipped: list[str], after: str | None = None) -> str | None:
    start = STEP_KEYS.index(after) + 1 if after in STEP_KEYS else 0
    for key in STEP_KEYS[start:]:
        if not done[key] and key not in skipped:
            return key
    return None


def view(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    state = _migrate(db, _state(db), now)
    done = facts(db, state)
    skipped = [k for k in state.get("skipped", []) if k in STEP_KEYS]
    current = state.get("current") if state.get("current") in STEP_KEYS else None
    if current is None or done[current]:
        current = _next_step(done, skipped, current) or _next_step(done, skipped)
    status = _status(state)

    essentials = [_item(k, t, w, m, h, done[k], "essentiel") for k, t, w, m, h in ESSENTIALS]
    count = sum(1 for i in essentials if i["done"])
    completed_at = _parse(state.get("checklist_completed_at"))
    just_completed = False
    if completed_at is None and count == len(essentials):
        # Huit sur huit pour la première fois : on le note (la fête, elle, se décide côté interface).
        completed_at = now
        state["checklist_completed_at"] = _iso(now)
        _save_on_read(db, state)
        just_completed = True
    dismissed = bool(state.get("dismissed_at"))
    expired = completed_at is not None and now - completed_at >= timedelta(days=COLLAPSE_DAYS)
    return {
        "journey": {
            "status": status,
            "current": current if status != "finished" else None,
            "steps": [
                {
                    "key": s.key,
                    "title": s.title,
                    "done": done[s.key],
                    "skipped": s.key in skipped and not done[s.key],
                    "minutes": s.minutes,
                    "href": f"#/bienvenue/{s.key}",
                }
                for s in STEPS
            ],
        },
        "checklist": {
            "visible": not dismissed and not expired,
            "collapsed": completed_at is not None and not expired and not dismissed,
            "dismissed": dismissed,
            "done": count,
            "total": len(essentials),
            "completed_at": _iso(completed_at) if completed_at else None,
            "just_completed": just_completed,
            "items": essentials + _more(db, done),
        },
        "public_consents": public_consents(db),
        "connectors": [{"key": k, "title": t, "linked": done[k], "section": sid} for k, t, sid in CONNECTORS],
    }


def _item(key: str, title: str, why: str, minutes: int, href: str, done: bool, group: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "why": why,
        "minutes": minutes,
        "done": done,
        "group": group,
        "href": href,
        "value_cents": None,
    }


def _more(db: Session, done: dict[str, bool]) -> list[dict[str, Any]]:
    """« Pour aller plus loin » : non compté. Le planning d'abord si le revenu varie ; Powens seulement sans banque
    Enable Banking ; le pilote seulement quand banque, foyer et priorités sont faits ; la crypto en dernier sauf si
    tu en as déclaré."""
    answers = advisor.plain_answers(db)
    variable = answers.get("income_variability") in ("un_peu", "beaucoup") or answers.get("employment") in (
        "cdd",
        "independant",
    )
    has_crypto = any(isinstance(a, dict) and a.get("kind") == "crypto" for a in answers.get("outside_assets") or [])
    order = [k for k, *_ in MORE]
    if has_crypto:
        order.remove("crypto")
        order.insert(0, "crypto")
    if variable:
        order.remove("planning")
        order.insert(0, "planning")
    keep = []
    for key in order:
        if key == "powens" and _eb_bank(db):
            continue
        if key == "pilote" and not (done["banque"] and done["foyer"] and done["priorites"]):
            continue
        keep.append(key)
    spec = {k: (t, w, m, h) for k, t, w, m, h in MORE}
    return [_item(k, *spec[k], done[k], "plus") for k in keep]


def public_consents(db: Session) -> list[dict[str, Any]]:
    out = []
    for purpose in consents.PUBLIC_PURPOSES:
        spec = consents.PURPOSES[purpose]
        group, sends = consents.SENDS[purpose]
        out.append(
            {
                "purpose": purpose,
                "group": group,
                "title": spec["title"],
                "sends": sends,
                "text": spec["text"],
                "granted": consents.granted(db, purpose),
                "decided": consents.decided(db, purpose),
            }
        )
    return out


def checklist_visible(db: Session, now: datetime | None = None) -> bool:
    """La liste se voit sur Aujourd'hui (et tait les initiatives de mise en route) : ni masquée, ni terminée depuis
    une semaine, et pas encore huit sur huit."""
    state = _state(db)
    if state.get("dismissed_at") or state.get("checklist_completed_at"):
        return False
    done = facts(db, state)
    return not all(done[k] for k, *_ in ESSENTIALS)


# ------------------------------------------------------------------ écritures
def step_action(db: Session, key: str, action: str, *, actor: str, now: datetime | None = None) -> dict[str, Any]:
    if key not in STEP_KEYS:
        raise StartError("Étape inconnue.")
    if action not in ("skip", "resume", "current"):
        raise StartError("Action inconnue.")
    now = now or utcnow()
    state = _state(db)
    state.setdefault("started_at", _iso(now))
    skipped = [k for k in state.get("skipped", []) if k in STEP_KEYS]
    done = facts(db, state)
    if action == "skip":
        if not done[key]:  # passer une étape faite est sans effet
            if key not in skipped:
                skipped.append(key)
            state["current"] = _next_step(done, skipped, key)
    elif action == "resume":
        skipped = [k for k in skipped if k != key]
        state["current"] = key
    else:
        state["current"] = key
    state["skipped"] = skipped
    _save(db, state)
    audit.record(db, actor, f"start.step.{action}", step=key)
    return view(db, now)


def finish(db: Session, *, tour_done: bool, actor: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    state = _state(db)
    state.setdefault("started_at", _iso(now))
    state["finished_at"] = state.get("finished_at") or _iso(now)
    state["current"] = None
    if tour_done:
        state["tour_done_at"] = state.get("tour_done_at") or _iso(now)
    _save(db, state)
    audit.record(db, actor, "start.finish", tour=tour_done)
    return view(db, now)


def checklist_action(db: Session, action: str, *, actor: str, now: datetime | None = None) -> dict[str, Any]:
    if action not in ("dismiss", "restore"):
        raise StartError("Action inconnue.")
    now = now or utcnow()
    state = _migrate(db, _state(db), now)
    if action == "dismiss":
        state["dismissed_at"] = _iso(now)
    else:
        state.pop("dismissed_at", None)
        # Revenir sur une liste terminée depuis longtemps : la pilule repart pour une semaine.
        if state.get("checklist_completed_at"):
            state["checklist_completed_at"] = _iso(now)
    _save(db, state)
    audit.record(db, actor, f"start.checklist.{action}")
    return view(db, now)


def grant_public(db: Session, purposes: list[str], *, actor: str) -> list[dict[str, Any]]:
    """« Tout activer pour les données publiques » : un clic explicite, ces accords-là et eux seuls, chacun
    journalisé comme un accord ordinaire (consent.grant, version du texte)."""
    wanted = list(dict.fromkeys(purposes))
    if not wanted:
        raise StartError("Aucun accord demandé.")
    refused = [p for p in wanted if p not in consents.PUBLIC_PURPOSES]
    if refused:
        raise StartError(f"Ces accords ne concernent pas des données publiques : {', '.join(sorted(refused))}.")
    for purpose in wanted:
        consents.set_consent(db, purpose, True, actor=actor)
    return public_consents(db)
