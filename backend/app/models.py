"""Schéma relationnel. Montants : toujours en centimes entiers (BigInteger), jamais en float."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    POWENS = "powens"


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


class Passkey(Base):
    """Une passkey (WebAuthn) : la clé publique et son compteur de signature ; rien de secret ici, le secret
    reste dans l'authentificateur (téléphone, clé, ordinateur)."""

    __tablename__ = "passkeys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(512), unique=True)  # base64url
    public_key: Mapped[str] = mapped_column(Text)  # COSE, base64url
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    aaguid: Mapped[str] = mapped_column(String(40), default="")
    transports: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(EncryptedText("passkey.name"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


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
    # Agrégateur de la connexion : NULL = Enable Banking (connexions d'avant Powens), « powens » sinon. Pour Powens,
    # session_id porte l'identifiant de connexion Powens (chiffré comme le reste).
    provider: Mapped[str | None] = mapped_column(String(24))

    accounts: Mapped[list[Account]] = relationship(back_populates="connection")


class ProviderCredential(Base):
    """Identifiants d'un agrégateur bancaire saisis dans l'assistant de connexion (Enable Banking : identifiant
    d'application et clé privée RSA). Chiffrés ; l'API n'en rend qu'un indice et l'empreinte de la clé publique,
    jamais la clé ; ni l'export ni le journal ne les contiennent. Une configuration du serveur (.env) prime."""

    __tablename__ = "bank_provider_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), unique=True)  # enablebanking | powens
    client_id: Mapped[str] = mapped_column(EncryptedText("bank_provider.client_id"))
    secret: Mapped[str] = mapped_column(EncryptedText("bank_provider.secret"))
    client_hint: Mapped[str] = mapped_column(String(24))  # « …9f3a2c » : reconnaître l'application
    secret_fingerprint: Mapped[str] = mapped_column(String(64))  # empreinte SHA-256 de la clé publique
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Powens seulement : le domaine de l'API (« monappli » pour monappli.biapi.pro), le jeton permanent de
    # l'utilisateur Powens de Bank et son identifiant (pour renouveler le jeton sans recréer l'utilisateur).
    domain: Mapped[str | None] = mapped_column(String(80))
    user_token: Mapped[str | None] = mapped_column(EncryptedText("bank_provider.user_token"))
    user_ref: Mapped[str | None] = mapped_column(EncryptedText("bank_provider.user_ref"))


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


class Loan(Base):
    """Un crédit suivi : ce qu'il faut pour le tableau d'amortissement (capital, taux, durée, assurance, première
    échéance). Nom et prêteur chiffrés ; le capital restant dû entre dans le patrimoine net."""

    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(EncryptedText("loan.name"))
    lender: Mapped[str | None] = mapped_column(EncryptedText("loan.lender"))
    kind: Mapped[str] = mapped_column(String(16), default="consommation")
    principal_cents: Mapped[int] = mapped_column(BigInteger)
    annual_rate_bp: Mapped[int] = mapped_column(Integer)
    months: Mapped[int] = mapped_column(Integer)
    first_payment: Mapped[date] = mapped_column(Date)
    insurance_monthly_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MailAccount(Base):
    """La boîte mail reliée en lecture seule : serveur, identifiants chiffrés (jamais réaffichés), un dossier, la
    liste des expéditeurs retenus, où en est la lecture (dernier UID vu). Une seule ligne (id = 1)."""

    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=993)
    security: Mapped[str] = mapped_column(String(8), default="ssl")  # ssl | starttls
    username: Mapped[str] = mapped_column(EncryptedText("mail.username"))
    username_hint: Mapped[str] = mapped_column(String(40), default="")  # « mi…@example.org » : pour reconnaître
    password: Mapped[str] = mapped_column(EncryptedText("mail.password"))
    folder: Mapped[str] = mapped_column(String(120), default="INBOX")
    senders: Mapped[str] = mapped_column(Text, default="[]")  # JSON : adresses ou domaines retenus (vide : tous)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    uid_validity: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MailProposal(Base):
    """Un PDF repéré dans la boîte mail, proposé à l'import dans Papiers : qui l'envoie, quel objet, quel fichier,
    quel genre probable. L'expéditeur et l'objet sont chiffrés ; l'empreinte sert à écarter les doublons."""

    __tablename__ = "mail_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[int] = mapped_column(Integer, index=True)
    part_sha256: Mapped[str] = mapped_column(String(64), index=True)
    sender: Mapped[str] = mapped_column(EncryptedText("mail.sender"))
    sender_domain: Mapped[str] = mapped_column(String(120), default="")
    subject: Mapped[str] = mapped_column(EncryptedText("mail.subject"))
    filename: Mapped[str] = mapped_column(String(160))
    size: Mapped[int] = mapped_column(Integer, default=0)
    kind_guess: Mapped[str] = mapped_column(String(24), default="other")
    confidence: Mapped[float] = mapped_column(Float, default=0.3)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(10), default="proposed", index=True)  # proposed | imported | dismissed
    document_id: Mapped[int | None] = mapped_column(ForeignKey("vault_documents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RetirementProfile(Base):
    """Ton relevé de carrière tel que tu l'as saisi (trimestres acquis, salaire annuel moyen, points Agirc-Arrco,
    début de carrière) : chiffré, une seule ligne (id = 1)."""

    __tablename__ = "retirement_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(EncryptedText("retirement.profile"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class BackupRun(Base):
    """Une sauvegarde ou une restauration d'essai : ce qui s'est passé, visible dans Bank. Jamais de secret :
    noms de fichiers, tailles, nombre de lignes par table, message d'erreur."""

    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)  # backup | verify
    status: Mapped[str] = mapped_column(String(8), index=True)  # ok | failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    details: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    trigger: Mapped[str] = mapped_column(String(10), default="auto")  # auto | manual


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


# ---------------------------------------------------------------------------- planning de travail
class WorkPlan(Base):
    """Agenda de travail (iCal) relié à la paie : l'adresse privée est chiffrée, le modèle de paie aussi."""

    __tablename__ = "work_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(8), default="file")  # url | file
    url: Mapped[str | None] = mapped_column(EncryptedText("planning.url"))
    label: Mapped[str] = mapped_column(String(80), default="Planning")
    tz: Mapped[str] = mapped_column(String(40), default="Europe/Paris")
    model: Mapped[str | None] = mapped_column(EncryptedText("planning.model"))  # JSON PayModel
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WorkShift(Base):
    """Une journée d'un événement de l'agenda (titre chiffré : il peut être personnel)."""

    __tablename__ = "work_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("work_plan.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start_day: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Float, default=0.0)
    all_day: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(EncryptedText("planning.title"))
    uid_hash: Mapped[str] = mapped_column(String(32), default="")


# ---------------------------------------------------------------------------- initiatives (« Bank te demande »)
class InitiativeState(Base):
    """Ce que tu as répondu à une carte : faite, remise à plus tard (jusqu'à une date) ou écartée."""

    __tablename__ = "initiative_states"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(10))  # done | snoozed | dismissed
    until: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(40))  # réponse courte (« voulu », « inconnu », « terminé »)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- assistant (OpenRouter)
class LlmProvider(Base):
    """Ta clé OpenRouter (chiffrée, jamais réaffichée), le modèle choisi et les garde-fous. Une seule ligne (id = 1)."""

    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), default="openrouter")
    api_key: Mapped[str] = mapped_column(EncryptedText("llm.api_key"))
    key_prefix: Mapped[str] = mapped_column(String(16))  # « sk-or-v1-ab12… » : pour reconnaître la clé, pas l'utiliser
    model: Mapped[str] = mapped_column(String(120))
    privacy: Mapped[str] = mapped_column(String(12), default="redacted")  # full | redacted | aggregates
    monthly_cap_usd_cents: Mapped[int] = mapped_column(Integer, default=500)  # 5 $ de crédits par mois
    daily_calls_cap: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))


class LlmUsage(Base):
    """Un appel au modèle : quoi, combien de jetons, combien de crédits — jamais le contenu."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    feature: Mapped[str] = mapped_column(String(16))  # ask | hints | test
    model: Mapped[str] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0)


class SearchProvider(Base):
    """Le moteur de recherche web de l'assistant : SearXNG chez toi (adresse) ou Brave (clé chiffrée, jamais
    réaffichée). Une seule ligne (id = 1). Sans ligne, l'assistant ne cherche pas sur le web."""

    __tablename__ = "search_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))  # searxng | brave
    base_url: Mapped[str | None] = mapped_column(String(300))  # SearXNG : https://recherche.chez-toi.lan
    api_key: Mapped[str | None] = mapped_column(EncryptedText("search.api_key"))  # Brave
    key_prefix: Mapped[str | None] = mapped_column(String(16))
    daily_cap: Mapped[int] = mapped_column(Integer, default=40)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))


class MerchantHint(Base):
    """Catégorie proposée par le modèle pour un marchand non classé ; appliquée seulement quand tu confirmes."""

    __tablename__ = "merchant_hints"

    merchant: Mapped[str] = mapped_column(String(120), primary_key=True)
    category_slug: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- consentements (par finalité)
class Consent(Base):
    """Ton accord pour une finalité précise (mémoire de l'assistant, lecture de documents par un modèle externe,
    conservation de pièces sensibles, veille). Révocable à tout moment : l'effet est immédiat."""

    __tablename__ = "consents"

    purpose: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    wording_version: Mapped[int] = mapped_column(Integer, default=1)  # texte auquel tu as dit oui
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------- coffre-fort de documents
class VaultDocument(Base):
    """Un document que tu as confié à Bank : métadonnées et texte chiffrés, fichier dans VaultBlob."""

    __tablename__ = "vault_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # payslip | tax_notice | identity | invoice…
    title: Mapped[str] = mapped_column(EncryptedText("vault.title"))
    filename: Mapped[str] = mapped_column(EncryptedText("vault.filename"))
    mime: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)  # même fichier déposé deux fois : un seul document
    pages: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(12), default="processing"
    )  # processing | to_review | verified | failed | refused
    error: Mapped[str | None] = mapped_column(String(200))  # pourquoi la lecture a échoué ou a été refusée
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(12), default="upload")  # upload | camera
    method: Mapped[str] = mapped_column(String(12), default="none")  # pdf_text | ocr | none
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # confiance de la lecture (0 à 1)
    text: Mapped[str | None] = mapped_column(EncryptedText("vault.text"))
    issuer: Mapped[str | None] = mapped_column(EncryptedText("vault.issuer"))
    document_date: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date, index=True)
    retention_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    fields: Mapped[list[VaultField]] = relationship(back_populates="document", cascade="all, delete-orphan")


class VaultBlob(Base):
    """Le fichier lui-même (et sa vignette), chiffrés AES-256-GCM avec l'identifiant du document en AAD."""

    __tablename__ = "vault_blobs"

    document_id: Mapped[int] = mapped_column(ForeignKey("vault_documents.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    thumb: Mapped[bytes | None] = mapped_column(LargeBinary)


class VaultField(Base):
    """Une valeur lue dans un document, liée à sa source ; tu peux la corriger (l'originale est gardée)."""

    __tablename__ = "vault_fields"
    __table_args__ = (UniqueConstraint("document_id", "key", name="uq_vault_field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("vault_documents.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(EncryptedText("vault.field"))  # JSON
    original: Mapped[str | None] = mapped_column(EncryptedText("vault.field_original"))  # JSON, avant correction
    evidence: Mapped[str | None] = mapped_column(EncryptedText("vault.field_evidence"))  # extrait du texte
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(12), default="extraction")  # extraction | assist | user
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    document: Mapped[VaultDocument] = relationship(back_populates="fields")


class VaultShare(Base):
    """Un papier transmis par lien : jeton connu par son empreinte seulement, destinataire chiffré, copie filigranée
    chiffrée (effacée dès le téléchargement, l'expiration ou la révocation). La ligne reste : c'est le journal."""

    __tablename__ = "vault_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("vault_documents.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    recipient: Mapped[str] = mapped_column(EncryptedText("vault.share_recipient"))
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# ---------------------------------------------------------------------------- démarches
class Procedure(Base):
    """Une démarche suivie de bout en bout. Le contenu (champs, pièces, revue) est chiffré ; l'étape est en clair
    pour le tableau de bord. « Envoyée » n'est possible qu'avec une validation liée à l'empreinte du dossier."""

    __tablename__ = "procedures"

    id: Mapped[int] = mapped_column(primary_key=True)
    template: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(EncryptedText("procedure.title"))
    stage: Mapped[str] = mapped_column(String(12), default="discover", index=True)
    data: Mapped[str] = mapped_column(EncryptedText("procedure.data"))  # JSON : champs, pièces, notes
    approval: Mapped[str | None] = mapped_column(EncryptedText("procedure.approval"))  # JSON : empreinte, date
    reference: Mapped[str | None] = mapped_column(EncryptedText("procedure.reference"))  # n° de dossier, accusé
    submission_key: Mapped[str | None] = mapped_column(String(64), unique=True)  # anti double envoi
    opportunity_key: Mapped[str | None] = mapped_column(String(160), index=True)
    deadline: Mapped[date | None] = mapped_column(Date, index=True)
    next_check: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    events: Mapped[list[ProcedureEvent]] = relationship(
        back_populates="procedure", cascade="all, delete-orphan", order_by="ProcedureEvent.id"
    )


class ProcedureEvent(Base):
    """Fil de la démarche : chaque étape, correction, validation, envoi — traçable."""

    __tablename__ = "procedure_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    procedure_id: Mapped[int] = mapped_column(ForeignKey("procedures.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    kind: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(EncryptedText("procedure.event"))  # JSON

    procedure: Mapped[Procedure] = relationship(back_populates="events")


# ---------------------------------------------------------------------------- opportunités et valeur obtenue
class OpportunityState(Base):
    """Ce que tu as décidé d'une opportunité : prise (avec la démarche liée), écartée, ou à revoir plus tard."""

    __tablename__ = "opportunity_states"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(10))  # started | dismissed | snoozed | done
    until: Mapped[date | None] = mapped_column(Date)
    procedure_id: Mapped[int | None] = mapped_column(ForeignKey("procedures.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ValueEvent(Base):
    """Valeur produite par Bank : une estimation (au départ d'une démarche) ou un gain confirmé (argent reçu,
    dépense arrêtée). Les deux ne se mélangent jamais dans les totaux."""

    __tablename__ = "value_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_key: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # estimated | confirmed
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    period: Mapped[str] = mapped_column(String(8))  # once | month | year
    on_date: Mapped[date] = mapped_column(Date)
    label: Mapped[str] = mapped_column(EncryptedText("value.label"))
    evidence: Mapped[str | None] = mapped_column(EncryptedText("value.evidence"))
    source: Mapped[str] = mapped_column(String(8), default="user")  # user | auto
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- veille
class WatchSource(Base):
    """Une source surveillée : officielle (préréglée) ou ajoutée par toi. Jamais de donnée personnelle envoyée."""

    __tablename__ = "watch_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(10))  # rss | pypi
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    trust: Mapped[str] = mapped_column(String(12))  # officiel | secondaire | perso
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    etag: Mapped[str | None] = mapped_column(String(200))
    last_modified: Mapped[str | None] = mapped_column(String(80))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))


class WatchItem(Base):
    """Une information trouvée : provenance, date de publication, première vue, dernière vérification, empreinte
    du contenu (pour voir ce qui change), sujets reconnus. Jamais présentée comme une règle en vigueur."""

    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_watch_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("watch_sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))  # empreinte du guid / lien
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, default=1)
    topics: Mapped[str] = mapped_column(String(300), default="[]")  # JSON
    status: Mapped[str] = mapped_column(String(10), default="new")  # new | seen | dismissed


# ---------------------------------------------------------------------------- mémoire de l'assistant
class MemoryEntry(Base):
    """Une entrée d'un des deux carnets (« user » : qui tu es ; « notes » : ton environnement financier).
    Chiffrée ; épinglée = seul toi peux la modifier ou la retirer."""

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(8), index=True)
    content: Mapped[str] = mapped_column(EncryptedText("memory.entry"))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(8), default="chat")  # chat | review | user
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class MemoryProposal(Base):
    """Ce que la relecture automatique voudrait modifier ou retirer (ou ajouter quand le carnet est plein) :
    rien ne change sans ta validation. L'entrée visée est figée pour détecter une modification entre-temps."""

    __tablename__ = "memory_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))  # add | patch | remove
    old_text: Mapped[str | None] = mapped_column(EncryptedText("memory.proposal_old"))
    new_text: Mapped[str | None] = mapped_column(EncryptedText("memory.proposal_new"))
    target_snapshot: Mapped[str | None] = mapped_column(EncryptedText("memory.proposal_target"))
    reason: Mapped[str | None] = mapped_column(EncryptedText("memory.proposal_reason"))
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Conversation(Base):
    """Une conversation avec l'assistant. La mémoire injectée est figée au début (même contexte du premier au
    dernier message) ; titre et résumé viennent de la relecture."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    last_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    title: Mapped[str | None] = mapped_column(EncryptedText("conversation.title"))
    summary: Mapped[str | None] = mapped_column(EncryptedText("conversation.summary"))
    model: Mapped[str] = mapped_column(String(120), default="")
    memory_on: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_snapshot: Mapped[str | None] = mapped_column(EncryptedText("conversation.memory"))
    user_turns: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_turns: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.id"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(EncryptedText("conversation.message"))
    meta: Mapped[str] = mapped_column(Text, default="{}")  # modèle, coût, jetons, opérations de mémoire (sans contenu)
    feedback: Mapped[str | None] = mapped_column(String(6))  # up | down
    feedback_note: Mapped[str | None] = mapped_column(EncryptedText("conversation.feedback"))
    # Blocs interactifs d'une réponse (boutons, graphique, schéma, sources) : des chiffres et des libellés à toi.
    blocks: Mapped[str | None] = mapped_column(EncryptedText("conversation.blocks"))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------- canaux de messagerie
class ChannelConfig(Base):
    """Une messagerie (Telegram, Discord, Slack, WhatsApp, Signal, ntfy) : jetons chiffrés, jamais réaffichés
    (seul un préfixe sert à les reconnaître), réglages non secrets, confidentialité et état de la connexion."""

    __tablename__ = "channel_configs"

    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    secrets: Mapped[str] = mapped_column(EncryptedText("channel.secrets"))  # JSON
    options: Mapped[str] = mapped_column(Text, default="{}")  # JSON non secret (identifiant de numéro, URL…)
    hints: Mapped[str] = mapped_column(Text, default="{}")  # JSON : préfixes des secrets, pour les reconnaître
    privacy: Mapped[str] = mapped_column(String(12), default="redacted")  # full | redacted | aggregates
    status: Mapped[str] = mapped_column(String(12), default="off")  # off | connecting | online | error
    bot_name: Mapped[str | None] = mapped_column(String(120))
    cursor: Mapped[str | None] = mapped_column(String(64))  # Telegram : prochain update_id à lire
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ignored: Mapped[int] = mapped_column(Integer, default=0)  # messages d'expéditeurs inconnus, ignorés
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ChannelLink(Base):
    """Un compte de messagerie relié par code d'appairage : le seul qu'on écoute et à qui on écrit. L'identifiant
    externe (un numéro de téléphone pour WhatsApp et Signal) est chiffré ; un index aveugle sert à le retrouver."""

    __tablename__ = "channel_links"
    __table_args__ = (UniqueConstraint("platform", "user_index", name="uq_channel_link_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    user_index: Mapped[str] = mapped_column(String(32))
    user_ref: Mapped[str] = mapped_column(EncryptedText("channel.user"))
    chat_ref: Mapped[str] = mapped_column(EncryptedText("channel.chat"))
    label: Mapped[str] = mapped_column(EncryptedText("channel.label"))
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ChannelSeen(Base):
    """Messages entrants déjà traités : les plateformes réessaient, on ne répond qu'une fois."""

    __tablename__ = "channel_seen"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ChannelNotice(Base):
    """Alerte déjà envoyée à un compte relié (clé stable par événement) : jamais deux fois la même."""

    __tablename__ = "channel_notices"
    __table_args__ = (UniqueConstraint("link_id", "key", name="uq_channel_notice"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("channel_links.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


# ---------------------------------------------------------------------------- pilote automatique
class AutonomyMandate(Base):
    """Ton accord pour que ton agent bouge de l'argent sans te demander — désactivé par défaut, borné de toutes
    parts (plafonds, actifs, adresses, consignes, durée, pertes), lié à UN jeton d'agent précis, et coupé d'un
    geste. Une seule ligne (id = 1)."""

    __tablename__ = "autonomy_mandate"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trial: Mapped[bool] = mapped_column(Boolean, default=True)  # mode essai : l'agent dit ce qu'il ferait, sans agir
    agent_token_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tokens.id", ondelete="SET NULL"))
    agent_name: Mapped[str | None] = mapped_column(String(64))  # pour l'affichage ; l'autorisation suit le jeton
    mode: Mapped[str] = mapped_column(String(8), default="rules")  # rules | free
    scopes: Mapped[str] = mapped_column(Text, default='["crypto_spot"]')  # JSON
    assets: Mapped[str] = mapped_column(Text, default='["BTC", "ETH", "USDC", "EURC"]')  # JSON
    rules: Mapped[str] = mapped_column(EncryptedText("autonomy.rules"), default="[]")  # JSON : tes consignes
    destinations: Mapped[str] = mapped_column(EncryptedText("autonomy.destinations"), default="[]")  # JSON
    per_op_cents: Mapped[int] = mapped_column(BigInteger, default=2_500)
    per_day_cents: Mapped[int] = mapped_column(BigInteger, default=5_000)
    per_month_cents: Mapped[int] = mapped_column(BigInteger, default=20_000)
    max_moves_per_day: Mapped[int] = mapped_column(Integer, default=5)
    max_loss_cents: Mapped[int] = mapped_column(BigInteger, default=10_000)
    enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disabled_reason: Mapped[str | None] = mapped_column(String(200))
    disabled_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AutonomyMove(Base):
    """Un mouvement demandé par l'agent : autorisé (réservé sur tes plafonds), puis rapporté exécuté ou échoué —
    ou refusé, avec la raison. Le ticket n'est gardé qu'en empreinte."""

    __tablename__ = "autonomy_moves"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    agent_token_id: Mapped[int | None] = mapped_column(Integer, index=True)
    agent_name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))  # buy | sell | swap | transfer
    asset: Mapped[str] = mapped_column(String(16))
    to_asset: Mapped[str | None] = mapped_column(String(16))
    chain: Mapped[str | None] = mapped_column(String(24))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    destination_label: Mapped[str | None] = mapped_column(String(60))
    rule_id: Mapped[str | None] = mapped_column(String(16), index=True)
    period: Mapped[str | None] = mapped_column(String(12))  # 2026-10-21, 2026-W43 ou 2026-10 : cadence d'une consigne
    rule: Mapped[str | None] = mapped_column(EncryptedText("autonomy.rule"))
    reason: Mapped[str] = mapped_column(EncryptedText("autonomy.reason"))
    # reserved | executed | failed | unreported | revoked | simulated | refused
    status: Mapped[str] = mapped_column(String(10), index=True)
    refusal: Mapped[str | None] = mapped_column(String(200))
    tx_ref: Mapped[str | None] = mapped_column(EncryptedText("autonomy.tx"))
    executed_cents: Mapped[int | None] = mapped_column(BigInteger)
    pnl_cents: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    reported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# ---------------------------------------------------------------------------- crypto
class CryptoWallet(Base):
    """Une adresse PUBLIQUE suivie en lecture seule. Jamais de clé ni de phrase de récupération : Bank ne
    pourrait de toute façon rien signer. L'adresse est chiffrée (elle relie ton identité à tout ton historique)."""

    __tablename__ = "crypto_wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(60))
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(EncryptedText("crypto.address"))
    address_index: Mapped[str] = mapped_column(String(64), unique=True)  # empreinte : pas deux fois la même
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_error: Mapped[str | None] = mapped_column(String(200))


class CryptoHolding(Base):
    """Une quantité d'un actif : relevée sur une adresse suivie, ou saisie (plateforme, portefeuille matériel…).
    Quantité en texte décimal exact (jamais de flottant)."""

    __tablename__ = "crypto_holdings"
    __table_args__ = (UniqueConstraint("wallet_id", "asset", name="uq_crypto_wallet_asset"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int | None] = mapped_column(ForeignKey("crypto_wallets.id", ondelete="CASCADE"), index=True)
    exchange_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("crypto_exchange_keys.id", ondelete="CASCADE"), index=True
    )  # relevé par une clé d'API de plateforme (lecture seule) : jamais modifié à la main
    platform: Mapped[str | None] = mapped_column(String(60))  # Coinbase, Kraken, Ledger… (saisie manuelle)
    foreign: Mapped[bool] = mapped_column(Boolean, default=False)  # compte hors de France : 3916-bis
    asset: Mapped[str] = mapped_column(String(16))
    coingecko_id: Mapped[str | None] = mapped_column(String(64))  # pour un actif hors catalogue
    quantity: Mapped[str] = mapped_column(String(48), default="0")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CryptoPrice(Base):
    """Dernier cours connu en euros (CoinGecko), avec sa date : un cours périmé se voit."""

    __tablename__ = "crypto_prices"

    asset: Mapped[str] = mapped_column(String(16), primary_key=True)
    eur: Mapped[str] = mapped_column(String(48))
    change_24h: Mapped[str | None] = mapped_column(String(24))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CryptoSnapshot(Base):
    """Valeur du portefeuille crypto, une fois par jour : la courbe se dessine sans rien demander à personne."""

    __tablename__ = "crypto_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    value_cents: Mapped[int] = mapped_column(BigInteger)


class CryptoTaxEvent(Base):
    """Un achat payé en euros (acquisition) ou une vente contre des euros ou un bien (cession) : de quoi remplir
    le 2086. Les échanges crypto contre crypto ne sont pas imposables et ne s'y notent pas."""

    __tablename__ = "crypto_tax_events"
    __table_args__ = (
        Index(
            "uq_crypto_tax_import",
            "import_key",
            unique=True,
            sqlite_where=text("import_key IS NOT NULL"),
            postgresql_where=text("import_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(12))  # acquisition | cession
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    fees_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    portfolio_value_cents: Mapped[int | None] = mapped_column(BigInteger)
    asset: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(EncryptedText("crypto.tax_note"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Empreinte d'une ligne d'export de plateforme (jamais son identifiant) : réimporter ne crée rien.
    import_key: Mapped[str | None] = mapped_column(String(64))


class CryptoImportExcluded(Base):
    """Une opération importée que tu as supprimée : le prochain import (un export qui chevauche) ne la recrée pas,
    sauf si tu le demandes ligne par ligne. Seule l'empreinte est gardée."""

    __tablename__ = "crypto_import_excluded"

    import_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- déclarations récurrentes
class Obligation(Base):
    """Une déclaration récurrente dont dépend une aide (actualisation France Travail, déclaration trimestrielle
    CAF, renouvellement de la Complémentaire santé solidaire, déclaration de revenus, ou une obligation à toi).
    Le libellé et les réglages sont chiffrés : ils disent de quelles aides tu vis."""

    __tablename__ = "obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str | None] = mapped_column(EncryptedText("obligation.label"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    anchor: Mapped[date | None] = mapped_column(Date)  # mois de déclaration, fin de droits, prochaine échéance
    cadence_months: Mapped[int] = mapped_column(Integer, default=12)
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    settings: Mapped[str | None] = mapped_column(EncryptedText("obligation.settings"))  # JSON
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    done: Mapped[list[ObligationDone]] = relationship(back_populates="obligation", cascade="all, delete-orphan")


class ObligationDone(Base):
    """Une période réglée : par toi, ou constatée par le versement qui a suivi. Ce que tu as déclaré (montants par
    type de ressource) est gardé, chiffré, pour le comparer au versement qui suit."""

    __tablename__ = "obligation_done"
    __table_args__ = (UniqueConstraint("obligation_id", "period", name="uq_obligation_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(12))
    done_on: Mapped[date] = mapped_column(Date)
    how: Mapped[str] = mapped_column(String(40), default="user")  # user | payment:<id> | auto
    declared: Mapped[str | None] = mapped_column(EncryptedText("obligation.declared"))  # JSON
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    obligation: Mapped[Obligation] = relationship(back_populates="done")


# ---------------------------------------------------------------------------- clés d'API de plateforme (crypto)
class ExchangeKey(Base):
    """Une clé d'API de plateforme d'échange, en lecture seule — droits vérifiés à la connexion et gardés tels que
    la plateforme (ou les sondes) les ont dits. Clé et secret chiffrés ; l'API n'en rend qu'un indice (fin de
    l'identifiant), jamais le secret ; ni l'export ni le journal ne les contiennent."""

    __tablename__ = "crypto_exchange_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)  # kraken | binance | coinbase
    label: Mapped[str] = mapped_column(String(60))
    api_key: Mapped[str] = mapped_column(EncryptedText("crypto.exchange_key"))
    api_secret: Mapped[str] = mapped_column(EncryptedText("crypto.exchange_secret"))
    key_hint: Mapped[str] = mapped_column(String(16))  # « …a9F2 » : reconnaître la clé sans la montrer
    foreign: Mapped[bool] = mapped_column(Boolean, default=True)  # compte tenu hors de France : 3916-bis
    rights: Mapped[str] = mapped_column(String(200), default="{}")  # JSON : read, trade, withdraw, source, ip
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------- alertes de cours, suivi du pilote
class PriceAlert(Base):
    """Une alerte de cours que tu as fixée : seuil haut, seuil bas ou variation sur 24 h. Armée, elle se déclenche
    une fois au franchissement (mise à jour conditionnelle : deux relèves simultanées n'envoient rien deux fois) ;
    elle se réarme quand le cours revient de l'autre côté avec une marge, ou quand tu la réarmes."""

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # above | below | change
    threshold: Mapped[str] = mapped_column(String(32))  # euros (above, below) ou points de % (change), en décimal
    label: Mapped[str | None] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(10), default="armed")  # armed | triggered
    fires: Mapped[int] = mapped_column(Integer, default=0)  # nombre de déclenchements : la clé de l'envoi
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    triggered_price: Mapped[str | None] = mapped_column(String(48))
    triggered_change: Mapped[str | None] = mapped_column(String(24))
    rearmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AutonomyEpoch(Base):
    """Une période où le pilote était allumé, avec les consignes de ce moment-là : le suivi « pilote vs consigne »
    sait quand une consigne était due, et quand elle ne l'était pas (pilote en pause, arrêté, échu)."""

    __tablename__ = "autonomy_epochs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    trial: Mapped[bool] = mapped_column(Boolean, default=True)
    rules: Mapped[str] = mapped_column(EncryptedText("autonomy.epoch.rules"), default="[]")  # JSON


# ---------------------------------------------------------------------------- valeur du logement (DVF)
class HomeProfile(Base):
    """Ton logement tel que tu l'as décrit (type, surface, commune, section et pièces facultatives) : chiffré, une
    seule ligne (id = 1). Jamais d'adresse : la commune suffit, la section affine sur place."""

    __tablename__ = "home_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(EncryptedText("home.profile"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DvfCache(Base):
    """Les ventes de ta commune, réduites (date, prix, type, surface, pièces, section) et chiffrées — elles disent
    où tu habites —, avec la date de l'extraction et l'estimation qui en découle. Une seule ligne (id = 1),
    rafraîchie au plus une fois par trimestre ; un téléchargement raté garde la précédente."""

    __tablename__ = "dvf_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str | None] = mapped_column(EncryptedText("dvf.sales"))  # JSON : commune, ventes, années lues
    result: Mapped[str | None] = mapped_column(EncryptedText("dvf.estimate"))  # JSON : dernière estimation
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    quarter: Mapped[str | None] = mapped_column(String(8))  # 2026-T4
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(300))
    attempts_day: Mapped[str | None] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
