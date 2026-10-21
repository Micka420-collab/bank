"""Schéma relationnel. Montants : toujours en centimes entiers (BigInteger), jamais en float."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, EncryptedText, UTCDateTime, utcnow


class AccountKind(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    LIVRET_A = "livret_a"
    LDDS = "ldds"
    LEP = "lep"
    CEL = "cel"
    PEL = "pel"
    PEA = "pea"
    ASSURANCE_VIE = "assurance_vie"
    PER = "per"
    CRYPTO = "crypto"
    CREDIT = "credit"
    OTHER = "other"


class Provider(StrEnum):
    MANUAL = "manual"
    FILE = "file"
    ENABLEBANKING = "enablebanking"


class CategoryKind(StrEnum):
    INCOME = "income"
    FIXED = "fixed"
    VARIABLE = "variable"
    SAVINGS = "savings"
    TRANSFER = "transfer"
    UNCATEGORIZED = "uncategorized"


class CategorySource(StrEnum):
    MANUAL = "manual"  # choisi à la main sur cette transaction
    USER_RULE = "user_rule"  # règle explicite créée par l'utilisateur
    LEARNED = "learned"  # appris d'une correction précédente sur le même marchand
    BAYES = "bayes"  # classifieur probabiliste entraîné sur ton historique
    BUILTIN = "builtin"  # dictionnaire intégré (marchands FR)
    AGENT = "agent"  # classé par un agent IA (mandat « gestionnaire ») : jamais appris, jamais prioritaire
    NONE = "none"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


# ---------------------------------------------------------------------------- sécurité
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret: Mapped[str | None] = mapped_column(EncryptedText("user.totp_secret"))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    recovery_codes: Mapped[str] = mapped_column(Text, default="[]")  # JSON : empreintes SHA-256
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    # On ne stocke que l'empreinte du jeton : une fuite de la base ne permet pas de détourner une session.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[str] = mapped_column(Text, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------------------- comptes
class BankConnection(Base):
    __tablename__ = "bank_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    aspsp_name: Mapped[str] = mapped_column(String(120))
    aspsp_country: Mapped[str] = mapped_column(String(2))
    status: Mapped[ConnectionStatus] = mapped_column(String(16), default=ConnectionStatus.PENDING)
    session_id: Mapped[str | None] = mapped_column(EncryptedText("bank_connection.session_id"))
    state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    state_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    accounts: Mapped[list[Account]] = relationship(back_populates="connection")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[AccountKind] = mapped_column(String(20), default=AccountKind.CHECKING)
    institution: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    iban: Mapped[str | None] = mapped_column(EncryptedText("account.iban"))
    iban_bidx: Mapped[str | None] = mapped_column(String(32), index=True)
    provider: Mapped[Provider] = mapped_column(String(20), default=Provider.MANUAL)
    provider_uid: Mapped[str | None] = mapped_column(EncryptedText("account.provider_uid"))
    provider_uid_bidx: Mapped[str | None] = mapped_column(String(32), index=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("bank_connections.id", ondelete="SET NULL"))
    balance_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    balance_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    include_in_budget: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    connection: Mapped[BankConnection | None] = relationship(back_populates="accounts")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[CategoryKind] = mapped_column(String(16))
    color: Mapped[str] = mapped_column(String(9), default="#64748b")
    monthly_budget_cents: Mapped[int | None] = mapped_column(BigInteger)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)


class CategoryRule(Base):
    __tablename__ = "category_rules"
    __table_args__ = (UniqueConstraint("pattern", "match", "direction", name="uq_rule"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(120))
    match: Mapped[str] = mapped_column(String(10), default="merchant")  # merchant | contains
    direction: Mapped[str] = mapped_column(String(3), default="any")  # in | out | any
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10), default="learned")  # user | learned
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    category: Mapped[Category] = relationship()


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(200))
    file_sha256: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(10))
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    reconciled: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "fingerprint", name="uq_txn_fingerprint"),
        Index("ix_txn_account_date", "account_id", "booking_date"),
        Index(
            "uq_txn_external_ref",
            "account_id",
            "external_ref",
            unique=True,
            sqlite_where=text("external_ref IS NOT NULL"),
            postgresql_where=text("external_ref IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    booking_date: Mapped[date] = mapped_column(Date, index=True)
    value_date: Mapped[date | None] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    raw_label: Mapped[str] = mapped_column(Text)
    merchant: Mapped[str] = mapped_column(String(120), index=True, default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    category_source: Mapped[CategorySource] = mapped_column(String(10), default=CategorySource.NONE)
    status: Mapped[str] = mapped_column(String(8), default="booked")
    fingerprint: Mapped[str] = mapped_column(String(64))
    external_ref: Mapped[str | None] = mapped_column(String(64))
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_peer_id: Mapped[int | None] = mapped_column(Integer)
    aid_kind: Mapped[str | None] = mapped_column(String(40), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    account: Mapped[Account] = relationship()
    category: Mapped[Category | None] = relationship()


# ---------------------------------------------------------------------------- profil & patrimoine
class HouseholdProfile(Base):
    """Profil du foyer (pour la simulation des aides) — stocké entièrement chiffré."""

    __tablename__ = "household_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(EncryptedText("household.profile"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class FiscalSimulation(Base):
    __tablename__ = "fiscal_simulations"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7))
    result: Mapped[str] = mapped_column(EncryptedText("fiscal.result"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SavingsGoal(Base):
    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    target_cents: Mapped[int] = mapped_column(BigInteger)
    target_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    monthly_contribution_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WealthSnapshot(Base):
    __tablename__ = "wealth_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    assets_cents: Mapped[int] = mapped_column(BigInteger)
    liabilities_cents: Mapped[int] = mapped_column(BigInteger)
    net_cents: Mapped[int] = mapped_column(BigInteger)


# ---------------------------------------------------------------------------- agents IA (HERMÈS…)
class AgentToken(Base):
    """Identité machine d'un agent : jeton porteur à forte entropie dont seule l'empreinte est stockée,
    limité par portées (scopes), mode de confidentialité, adresses IP et date d'expiration."""

    __tablename__ = "agent_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    privacy: Mapped[str] = mapped_column(String(12), default="redacted")  # full | redacted | aggregates
    allowed_ips: Mapped[str] = mapped_column(Text, default="[]")  # JSON, vide = toutes
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    calls: Mapped[int] = mapped_column(Integer, default=0)


class AgentProposal(Base):
    """Changement proposé par un agent, appliqué seulement après ta validation dans Bank."""

    __tablename__ = "agent_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(EncryptedText("agent_proposal.payload"))
    rationale: Mapped[str] = mapped_column(EncryptedText("agent_proposal.rationale"))
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    result: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FinancialProfile(Base):
    """Réponses à l'onboarding (ta situation, tes objectifs, ton mandat pour l'agent) — chiffrées."""

    __tablename__ = "financial_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(EncryptedText("financial.profile"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProfileRevision(Base):
    """Historique de chaque réponse : qui l'a donnée (toi ou un agent), quand — et retour arrière possible."""

    __tablename__ = "profile_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(EncryptedText("financial.revision"))
    source: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- énergie (Linky)
class EnergyConnection(Base):
    """Accès Linky par Conso API : le PRM et le jeton sont chiffrés ; révocable d'un clic ici ou chez Enedis."""

    __tablename__ = "energy_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), default="linky")
    label: Mapped[str] = mapped_column(String(80), default="Linky")
    prm: Mapped[str] = mapped_column(EncryptedText("energy.prm"))
    token: Mapped[str] = mapped_column(EncryptedText("energy.token"))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error | revoked
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class EnergyReading(Base):
    """Consommation électrique d'un jour, en Wh (entier), une ligne par jour et par connexion."""

    __tablename__ = "energy_readings"
    __table_args__ = (UniqueConstraint("connection_id", "day", name="uq_energy_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("energy_connections.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    wh: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(10), default="api")  # api | manual


# ---------------------------------------------------------------------------- documents officiels
class OfficialFact(Base):
    """Faits tirés d'un document officiel (avis d'impôt, attestation CAF ou France Travail) : chiffres seulement,
    jamais d'identifiant. Un seul fait par (type, année/période)."""

    __tablename__ = "official_facts"
    __table_args__ = (UniqueConstraint("kind", "period", name="uq_official_fact"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # avis_impot | caf | france_travail
    period: Mapped[str] = mapped_column(String(10))  # 2025 | 2026-09 | …
    title: Mapped[str] = mapped_column(String(120))
    data: Mapped[str] = mapped_column(EncryptedText("official.fact"))
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
