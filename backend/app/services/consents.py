"""Consentements par finalité : chaque traitement qui sort de l'ordinaire (une donnée qui part vers un modèle
externe, une pièce sensible conservée, une source surveillée) dépend d'un accord explicite, daté, révocable.
Retirer un accord a un effet immédiat ; le texte auquel tu as dit oui est versionné."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Consent
from app.security import audit

PURPOSES: dict[str, dict[str, Any]] = {
    "ai_memory": {
        "title": "Mémoire de l'assistant",
        "text": (
            "L'assistant retient ce que tu lui confies (faits, préférences, résumés de vos conversations) pour mieux "
            "t'aider d'une fois sur l'autre. Tout est chiffré ici, visible et effaçable dans « Ce que Bank retient ». "
            "Ce qu'il retient est envoyé à ton modèle OpenRouter au début de chaque conversation, avec le dossier. "
            "Retirer ton accord arrête l'apprentissage ; ce qui a été retenu reste jusqu'à ce que tu l'effaces."
        ),
        "default": False,
        "version": 1,
    },
    "ai_documents": {
        "title": "Lecture de documents par un modèle externe",
        "text": (
            "Quand la lecture locale d'une photo échoue, Bank peut l'envoyer à ton modèle OpenRouter pour en lire les "
            "champs — après ta confirmation, document par document. Jamais pour une pièce d'identité, un titre de "
            "séjour ou un document de santé."
        ),
        "default": False,
        "version": 1,
    },
    "sensitive_documents": {
        "title": "Pièces sensibles dans le coffre",
        "text": (
            "Conserver des pièces d'identité, titres de séjour ou documents de santé dans le coffre (chiffrés, jamais "
            "envoyés à un modèle). Sans ton accord, Bank les refuse."
        ),
        "default": False,
        "version": 1,
    },
    "channels": {
        "title": "Bank dans tes messageries",
        "text": (
            "Tu peux parler à Bank depuis WhatsApp, Telegram, Discord, Slack ou Signal et y recevoir ses alertes. "
            "Ce que Bank t'y écrit (montants, échéances, pistes) passe par les serveurs de la messagerie choisie — "
            "sauf Signal, chiffré de bout en bout. Seuls les comptes que tu relies depuis Bank sont écoutés. Aucune "
            "transmission, aucun paiement, aucune décision ne se fait depuis une messagerie : tu valides dans Bank."
        ),
        "default": False,
        "version": 1,
    },
    "watch": {
        "title": "Veille sur les sources officielles",
        "text": (
            "Bank interroge régulièrement des sources publiques (ministère de l'Économie, moteur OpenFisca, "
            "sources que tu ajoutes) pour repérer ce qui change. Aucune donnée te concernant n'est envoyée : la "
            "comparaison avec ta situation se fait ici."
        ),
        "default": True,
        "version": 1,
    },
}


class ConsentError(Exception):
    """Traitement refusé faute d'accord : le message dit lequel donner, et où."""

    def __init__(self, purpose: str, message: str | None = None) -> None:
        self.purpose = purpose
        super().__init__(
            message or f"Il faut ton accord « {PURPOSES[purpose]['title']} » (Réglages › Confidentialité)."
        )


def granted(db: Session, purpose: str) -> bool:
    if purpose not in PURPOSES:
        raise KeyError(purpose)
    row = db.get(Consent, purpose)
    if row is None:
        return bool(PURPOSES[purpose]["default"])
    # Un texte modifié depuis ton accord n'est pas un texte auquel tu as dit oui : on redemande.
    return row.granted and row.wording_version >= int(PURPOSES[purpose]["version"])


def decided(db: Session, purpose: str) -> bool:
    """Tu as répondu (oui ou non) au texte actuel : on ne te repose pas la question."""
    row = db.get(Consent, purpose)
    return row is not None and row.wording_version >= int(PURPOSES[purpose]["version"])


def require(db: Session, purpose: str, message: str | None = None) -> None:
    if not granted(db, purpose):
        raise ConsentError(purpose, message)


def set_consent(db: Session, purpose: str, value: bool, *, actor: str) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise KeyError(purpose)
    row = db.get(Consent, purpose)
    version = int(PURPOSES[purpose]["version"])
    if row is None:
        row = Consent(purpose=purpose, granted=value, wording_version=version)
        db.add(row)
    else:
        row.granted = value
        row.wording_version = version
    db.flush()
    audit.record(db, actor, "consent.grant" if value else "consent.revoke", purpose=purpose, version=version)
    return _view(db, purpose)


def _view(db: Session, purpose: str) -> dict[str, Any]:
    row = db.get(Consent, purpose)
    spec = PURPOSES[purpose]
    return {
        "purpose": purpose,
        "title": spec["title"],
        "text": spec["text"],
        "granted": granted(db, purpose),
        "explicit": row is not None,
        "decided": decided(db, purpose),
        "updated_at": row.updated_at.isoformat() if row else None,
        "default": spec["default"],
    }


def listing(db: Session) -> list[dict[str, Any]]:
    return [_view(db, p) for p in PURPOSES]
