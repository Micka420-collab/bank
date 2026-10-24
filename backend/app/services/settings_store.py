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
    # Ton contrat : option (base | hphc | tempo) et puissance souscrite en kVA ; None tant que Bank ne les connaît pas.
    "elec_contract": None,
    # Puissance maximale Linky et couleur Tempo (#33) : dernière relève et dernière erreur, dites en clair.
    "energy_power_state": {},
    "tempo_state": {},
    # Assistant : son nom, la durée de conservation des conversations, ce que chaque modèle sait faire.
    "assistant_name": "Bank",
    "conversation_retention_days": 365,
    "llm_model_caps": {},
    # Opportunités : ce que vaut une heure de ton temps (hypothèse réglable) pour classer par gain net attendu.
    "time_value_cents_per_hour": 1_500,
    # Tes priorités (choisies à l'accueil ou dans Opportunités) : les catégories montrées d'abord.
    "opportunity_priorities": [],
    # Messageries : code d'appairage en cours (empreinte salée seulement), heures calmes [début, fin[ et point de
    # la semaine (dimanche soir) envoyé aux comptes reliés qui reçoivent les alertes.
    "channel_pairing": {},
    "channel_quiet_hours": [21, 8],
    "channel_weekly": True,
    "whatsapp_qr_window": {},  # WhatsApp par QR code : fenêtre ouverte avec ton mot de passe (échéance seule)
    # Crypto : nœuds personnalisés par réseau (vide = nœuds publics par défaut), dernière humeur du marché,
    # et le portefeuille compté dans le patrimoine.
    "crypto_endpoints": {},
    "crypto_sentiment": None,
    "crypto_in_wealth": True,
    # Déclarations récurrentes : les propositions que tu as écartées (« ne plus demander »).
    "obligations_dismissed": [],
    # L'année qui vient : pics ajoutés à la main et pics détectés que tu as écartés.
    "year_spikes": {"custom": [], "ignored": []},
    "web_search_counter": {"day": "", "count": 0},  # recherches web du jour (plafond réglable)
    # Assistant de connexion bancaire : dernier résultat de vérification par agrégateur (jamais un secret).
    "bank_setup_check": {},
    # Barèmes relus aux sources publiques : ce que la dernière relève n'a pas pu changer, dit en clair.
    "baremes_notes": [],
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
    db.flush()  # la session ne vide pas d'elle-même : une relecture juste après doit voir la nouvelle valeur
