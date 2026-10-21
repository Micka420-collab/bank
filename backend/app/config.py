"""Configuration typée, chargée depuis l'environnement (préfixe BANK_) ou des fichiers secrets Docker.

Règle : aucun secret n'a de valeur par défaut. Un secret peut être fourni en clair (BANK_X) ou,
de préférence en production, via un fichier (BANK_X_FILE -> /run/secrets/x) : la valeur ne
transite alors jamais par l'environnement du process (lisible via /proc).
"""

from __future__ import annotations

import base64
import binascii
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Configuration invalide : on refuse de démarrer plutôt que de tourner en mode dégradé."""


def _read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"Impossible de lire le secret {path}: {exc.strerror}") from exc


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BANK_", env_file=".env", extra="ignore")

    env: Literal["dev", "prod", "test"] = "prod"
    database_url: str = "sqlite:///./data/bank.db"

    # Clé(s) maîtresse(s) : "k2:<b64>,k1:<b64>" (la première est active) ou une seule clé base64.
    master_key: SecretStr | None = None
    master_key_file: Path | None = None

    # Jeton exigé pour créer l'unique compte au premier lancement (empêche quiconque sur le LAN
    # de "réclamer" l'instance avant toi).
    setup_token: SecretStr | None = None
    setup_token_file: Path | None = None

    public_url: str = "https://localhost"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "testserver"])

    session_idle_minutes: int = Field(default=30, ge=5, le=240)
    session_absolute_hours: int = Field(default=12, ge=1, le=72)
    login_max_attempts: int = Field(default=5, ge=3, le=20)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)

    max_upload_bytes: int = Field(default=5_000_000, ge=10_000, le=50_000_000)

    # Coffre-fort de documents : taille d'un fichier, d'un envoi (plusieurs fichiers), et du coffre entier.
    vault_file_bytes: int = Field(default=12_000_000, ge=100_000, le=50_000_000)
    vault_upload_bytes: int = Field(default=40_000_000, ge=1_000_000, le=200_000_000)
    vault_total_bytes: int = Field(default=2_000_000_000, ge=10_000_000, le=50_000_000_000)

    # Énergie (Linky via Conso API) : synchronisation quotidienne en tâche de fond.
    energy_enabled: bool = True

    # Agents IA (serveur MCP sur /mcp) : désactivable d'un coup, débit borné par jeton.
    agents_enabled: bool = True
    agent_calls_per_minute: int = Field(default=120, ge=10, le=1000)

    enablebanking_app_id: str | None = None
    enablebanking_key_file: Path | None = None
    enablebanking_base_url: str = "https://api.enablebanking.com"
    # DSP2 : 4 accès non-sollicités max par jour et par compte -> au moins 6 h entre deux sync auto.
    sync_interval_hours: int = Field(default=8, ge=0, le=168)

    fiscal_engine_url: str = "http://fiscal:8001"
    fiscal_engine_token: SecretStr | None = None
    fiscal_engine_token_file: Path | None = None

    @field_validator("sync_interval_hours")
    @classmethod
    def _psd2_limit(cls, v: int) -> int:
        if 0 < v < 6:
            raise ValueError("sync_interval_hours doit être 0 (désactivé) ou >= 6 (limite DSP2 de 4/jour)")
        return v

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _prod_requires_https(self) -> Settings:
        if self.env == "prod" and not self.public_url.startswith("https://"):
            raise ValueError("En production, BANK_PUBLIC_URL doit être en https://")
        return self

    # ------------------------------------------------------------------ secrets
    def _secret(self, value: SecretStr | None, file: Path | None) -> str | None:
        if file is not None:
            return _read_secret_file(file)
        return value.get_secret_value() if value is not None else None

    @cached_property
    def master_keys(self) -> dict[str, bytes]:
        """Retourne {kid: clé 32 octets}, ordonné : la première entrée est la clé active."""
        raw = self._secret(self.master_key, self.master_key_file)
        if not raw:
            raise ConfigError(
                "BANK_MASTER_KEY(_FILE) manquant. Génère-le : python -c "
                '"import os,base64;print(base64.b64encode(os.urandom(32)).decode())"'
            )
        keys: dict[str, bytes] = {}
        for i, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
            kid, _, b64 = part.rpartition(":") if ":" in part else (f"k{i + 1}", "", part)
            try:
                key = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ConfigError(f"Clé maîtresse '{kid}' : base64 invalide") from exc
            if len(key) != 32:
                raise ConfigError(f"Clé maîtresse '{kid}' : 32 octets attendus, {len(key)} reçus")
            if kid in keys:
                raise ConfigError(f"Identifiant de clé dupliqué : {kid}")
            keys[kid] = key
        return keys

    @property
    def setup_token_value(self) -> str | None:
        return self._secret(self.setup_token, self.setup_token_file)

    @property
    def fiscal_token_value(self) -> str | None:
        return self._secret(self.fiscal_engine_token, self.fiscal_engine_token_file)

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def enablebanking_enabled(self) -> bool:
        return bool(self.enablebanking_app_id and self.enablebanking_key_file)
