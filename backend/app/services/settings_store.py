"""Préférences applicatives (clé -> JSON) avec valeurs par défaut typées."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AppSetting

DEFAULTS: dict[str, Any] = {
    "buffer_cents": 10_000,  # coussin de sécurité : le « reste à dépenser » garde 100 € de côté
    "savings_goal_cents": 0,  # épargne mensuelle visée, réservée avant le reste à dépenser
}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(AppSetting, key)
    return json.loads(row.value) if row else DEFAULTS.get(key)


def set_setting(db: Session, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(AppSetting, key)
    payload = json.dumps(value)
    if row is None:
        db.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload
