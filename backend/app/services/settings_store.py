"""Préférences applicatives (clé -> JSON) avec valeurs par défaut typées."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AppSetting

DEFAULTS: dict[str, Any] = {
    "buffer_cents": 10_000,  # coussin de sécurité : le « reste à dépenser » garde 100 € de côté
    "savings_goal_cents": 0,  # épargne mensuelle visée, réservée avant le reste à dépenser
    # Électricité : tarif réglementé Base 6 kVA au 1er août 2026 (modifiable), en centimes pour 100 kWh.
    "elec_cents_per_100kwh": 2001,
    "elec_subscription_cents_per_year": 19035,
    "elec_tariff_label": "Base 6 kVA (tarif réglementé)",
    # Assistant : son nom, la durée de conservation des conversations, ce que chaque modèle sait faire.
    "assistant_name": "Bank",
    "conversation_retention_days": 365,
    "llm_model_caps": {},
    # Opportunités : ce que vaut une heure de ton temps (hypothèse réglable) pour classer par gain net attendu.
    "time_value_cents_per_hour": 1_500,
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
