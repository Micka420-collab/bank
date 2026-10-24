"""Powens, prêt à brancher : identifiants du contrat saisis dans Bank, connexions par la webview, synchronisation.

Identifiants (domaine, client_id, client_secret) chiffrés en base avec le jeton permanent de l'utilisateur Powens de
Bank ; l'API n'en rend que le domaine, un indice du client_id et une empreinte du secret (HMAC sous la clé du serveur :
reconnaître le secret sans permettre de le retrouver). Un secret que Powens refuse n'est jamais gardé.

Correspondance avec le modèle de Bank : chaque compte activé dans la webview devient un compte (courant, livret,
assurance-vie, PEA, PER, crédit…) avec son solde ; les opérations passent par ``ingest`` (dédoublonnées par identifiant
Powens puis par empreinte). Les placements ne sont pas détaillés ligne à ligne : Bank n'a pas de modèle de titres hors
crypto, seule la valorisation totale sert de solde quand Powens n'en donne pas. Les crédits deviennent des comptes
« crédit » (solde négatif), à relier à un prêt dans Crédits ; l'échéancier Powens n'est pas importé."""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import utcnow, write_lock
from app.models import Account, AccountKind, BankConnection, ConnectionStatus, Provider, ProviderCredential
from app.providers.powens import REAUTH_STATES, STATE_MESSAGES, PowensAccount, PowensClient, PowensError
from app.security import audit, netfetch
from app.security.crypto import get_keyring, sha256_hex
from app.services.bank_setup import SetupError
from app.services.bank_sync import SyncReport, held_elsewhere
from app.services.ingest import ingest
from app.services.settings_store import get_setting, set_setting

log = logging.getLogger("bank.powens")
PROVIDER = "powens"
CALLBACK_PATH = "/api/banks/powens/callback"
CONSOLE_URL = "https://console.powens.com"
STATE_TTL = timedelta(minutes=30)  # le code temporaire de la webview vit 30 minutes
FIRST_SYNC_DAYS = 365
# Recouvrement large : min_date filtre sur la date d'application ; une opération « à venir » plus d'une semaine
# (carte à débit différé, correction tardive) ne passe en comptabilisée qu'après. Le dédoublonnage rend ce recouvrement
# sans effet sur les opérations déjà là.
OVERLAP_DAYS = 45
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
SECRET_RE = re.compile(r"^[\x21-\x7e]{16,256}$")  # imprimable, sans espace
BLOCKING = frozenset({"bad_key", "unreachable"})
PASSING = "Powens signale un incident sur cette banque : la prochaine synchronisation réessaiera."

# Types de comptes Powens (GET /account_types) vers les nôtres. « card » (carte à débit différé) est ignoré : ses
# opérations se retrouvent sur le compte courant au prélèvement mensuel, les compter deux fois fausserait le budget.
KINDS: dict[str, AccountKind] = {
    "checking": AccountKind.CHECKING,
    "joint": AccountKind.CHECKING,
    "savings": AccountKind.SAVINGS,
    "deposit": AccountKind.SAVINGS,
    "csl": AccountKind.SAVINGS,
    "cat": AccountKind.SAVINGS,
    "livret_b": AccountKind.SAVINGS,
    "livret_a": AccountKind.LIVRET_A,
    "ldds": AccountKind.LDDS,
    "cel": AccountKind.CEL,
    "pel": AccountKind.PEL,
    "pea": AccountKind.PEA,
    "lifeinsurance": AccountKind.ASSURANCE_VIE,
    "capitalisation": AccountKind.ASSURANCE_VIE,
    "per": AccountKind.PER,
    "perp": AccountKind.PER,
    "perco": AccountKind.PER,
    "madelin": AccountKind.PER,
    "article83": AccountKind.PER,
    "loan": AccountKind.CREDIT,
    "consumercredit": AccountKind.CREDIT,
    "revolvingcredit": AccountKind.CREDIT,
}
IGNORED_TYPES = frozenset({"card"})
INVESTMENT_KINDS = frozenset({AccountKind.PEA, AccountKind.ASSURANCE_VIE, AccountKind.PER, AccountKind.OTHER})


class PowensFlowError(Exception):
    pass


# ------------------------------------------------------------------ saisie
def redirect_url(public_url: str) -> str:
    return public_url.rstrip("/") + CALLBACK_PATH


def clean_domain(raw: str) -> str:
    """Le domaine de l'API : « monappli » ou « monappli-sandbox ». L'adresse complète collée telle quelle
    (https://monappli.biapi.pro/2.0) est acceptée."""
    value = (raw or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/", 1)[0]
    value = value.removesuffix(".biapi.pro")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise SetupError(
            "Ce domaine ne ressemble pas à celui d'une application Powens : recopie-le depuis la console "
            "(« monappli » ou « monappli-sandbox », sans le .biapi.pro)."
        )
    return value


def clean_client_id(raw: str) -> str:
    value = (raw or "").strip()
    if not CLIENT_ID_RE.fullmatch(value):
        raise SetupError("Ce client_id ne ressemble pas à celui de la console Powens : recopie-le en entier.")
    return value


def clean_secret(raw: str) -> str:
    value = (raw or "").strip()
    if not SECRET_RE.fullmatch(value):
        raise SetupError("Ce secret ne ressemble pas à un client_secret Powens : recopie-le en entier, sans espace.")
    return value


def hint(client_id: str) -> str:
    return "…" + client_id[-4:] if len(client_id) > 4 else "…"


def secret_fingerprint(secret: str) -> str:
    """Empreinte HMAC du secret sous la clé du serveur : on reconnaît le même secret, on ne peut pas le retrouver."""
    return get_keyring().blind_index(secret, "bank_provider.powens_secret")


def short_fingerprint(full: str | None) -> str | None:
    return " ".join(full[i : i + 4] for i in range(0, 16, 4)) if full else None


# ------------------------------------------------------------------ client
def stored(db: Session) -> ProviderCredential | None:
    return db.scalar(select(ProviderCredential).where(ProviderCredential.provider == PROVIDER))


def make_client(
    domain: str,
    client_id: str,
    secret: str,
    user_token: str | None = None,
    *,
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
) -> PowensClient:
    return PowensClient(domain, client_id, secret, user_token, http=http, resolver=resolver)


def client_from_db(
    db: Session, *, http: httpx.Client | None = None, resolver: netfetch.Resolver | None = None
) -> PowensClient | None:
    row = stored(db)
    if row is None or not row.domain or not row.user_token:
        return None
    return make_client(row.domain, row.client_id, row.secret, row.user_token, http=http, resolver=resolver)


def renews(row: ProviderCredential | None, domain: str, client_id: str) -> bool:
    """Même domaine, même client et utilisateur Powens connu : on renouvelle son jeton, ses connexions restent."""
    return row is not None and bool(row.user_ref) and row.domain == domain and row.client_id == client_id


def enroll(client: PowensClient, row: ProviderCredential | None, *, may_replace: bool) -> tuple[str | None, bool]:
    """Obtient le jeton permanent : jeton renouvelé pour l'utilisateur existant quand c'est possible, sinon un nouvel
    utilisateur Powens. Un utilisateur effacé chez Powens (404 au renouvellement) est remplacé si aucune banque n'y est
    reliée. Rend l'identifiant de l'utilisateur et si l'ancien est remplacé (à effacer chez Powens)."""
    if row is not None and row.user_ref and renews(row, client.domain, client.client_id):
        try:
            client.renew_token(row.user_ref)
            return row.user_ref, False
        except PowensError as exc:
            if not (may_replace and exc.status == 404):
                raise
    _, user_ref = client.init_user()
    return user_ref, row is not None


def retire_user(db: Session, old: PowensClient | None) -> None:
    """L'ancien utilisateur Powens est remplacé : effacé chez Powens au mieux (RGPD : identifiants bancaires et
    données), et ses connexions, qui ne reviendront pas, coupées dans Bank."""
    if old is not None:
        with contextlib.suppress(PowensError):
            old.delete_user()
    alive = select(BankConnection).where(
        BankConnection.provider == PROVIDER, BankConnection.status != ConnectionStatus.REVOKED
    )
    for conn in db.scalars(alive):
        conn.status = ConnectionStatus.REVOKED
        conn.session_id = None
        conn.state_hash = None
        conn.state_expires_at = None


def refusal(exc: PowensError) -> bool:
    """Un refus des identifiants (4xx hors 429) : à ne jamais garder. Le reste est une panne passagère."""
    return exc.status is not None and 400 <= exc.status < 500 and exc.status != 429


# ------------------------------------------------------------------ vérification
def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


BAD_KEY = (
    "Powens refuse ces identifiants. Vérifie dans la console Powens le domaine, le client_id et le client_secret de "
    "ton application (onglet « Client applications »), puis recommence."
)
UNREACHABLE = (
    "Powens ne répond pas pour l'instant. Tes réglages sont gardés : réessaie la vérification dans quelques minutes."
)
SANDBOX = "Bac à sable : banque de démonstration seulement. Pour tes vrais comptes, il faut le domaine de production."


def check(client: PowensClient, now: datetime, *, failure: PowensError | None = None) -> dict[str, Any]:
    """Une requête avec le jeton (GET /users/me) : messages fixes, rien du secret ni de la réponse brute."""
    issues: list[dict[str, str]] = []
    exc = failure
    if exc is None:
        try:
            client.get_user()
        except PowensError as err:
            exc = err
    if exc is not None:
        issues.append(_issue("bad_key", BAD_KEY) if refusal(exc) else _issue("unreachable", UNREACHABLE))
    if client.sandbox:
        issues.append(_issue("sandbox", SANDBOX))
    return {
        "ok": not any(i["code"] in BLOCKING for i in issues),
        "issues": issues,
        "environment": "SANDBOX" if client.sandbox else "PRODUCTION",
        "checked_at": now.isoformat(),
    }


def last_check(db: Session) -> dict[str, Any] | None:
    found = (get_setting(db, "bank_setup_check") or {}).get(PROVIDER)
    return found if isinstance(found, dict) else None


def remember_check(db: Session, result: dict[str, Any] | None) -> None:
    current = dict(get_setting(db, "bank_setup_check") or {})
    if result is None:
        current.pop(PROVIDER, None)
    else:
        current[PROVIDER] = result
    set_setting(db, "bank_setup_check", current)


# ------------------------------------------------------------------ état, enregistrement
def connections(db: Session) -> list[BankConnection]:
    return list(
        db.scalars(
            select(BankConnection)
            .where(BankConnection.provider == PROVIDER, BankConnection.status != ConnectionStatus.PENDING)
            .order_by(BankConnection.id)
        )
    )


def status(db: Session, public_url: str, *, enabled: bool) -> dict[str, Any]:
    row = stored(db)
    return {
        "configured": row is not None and enabled,
        "domain": row.domain if row is not None else None,
        "client_id_hint": row.client_hint if row is not None else None,
        "secret_fingerprint": short_fingerprint(row.secret_fingerprint) if row is not None else None,
        "sandbox": bool(row is not None and (row.domain or "").endswith("-sandbox")),
        "updated_at": row.updated_at.isoformat() if row is not None else None,
        "redirect_url": redirect_url(public_url),
        "console_url": CONSOLE_URL,
        "check": last_check(db) if row is not None else None,
        "connections": [
            {
                "id": c.id,
                "aspsp_name": c.aspsp_name,
                "status": c.status,
                "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
                "last_error": c.last_error,
                "accounts": [a.name for a in c.accounts],
            }
            for c in connections(db)
        ],
    }


def has_live_connections(db: Session) -> bool:
    return any(c.status in (ConnectionStatus.ACTIVE, ConnectionStatus.EXPIRED) for c in connections(db))


def save(db: Session, client: PowensClient, *, secret: str, user_ref: str | None, now: datetime) -> ProviderCredential:
    write_lock(db)  # lire puis écrire : une seule ligne par agrégateur
    row = stored(db)
    if row is None:
        row = ProviderCredential(provider=PROVIDER, created_at=now)
        db.add(row)
    row.domain = client.domain
    row.client_id = client.client_id
    row.secret = secret
    row.user_token = client.user_token
    row.user_ref = user_ref
    row.client_hint = hint(client.client_id)
    row.secret_fingerprint = secret_fingerprint(secret)
    row.updated_at = now
    db.flush()
    return row


def remove(db: Session) -> bool:
    write_lock(db)
    row = stored(db)
    if row is None:
        return False
    for conn in connections(db):  # l'utilisateur Powens est effacé : ces connexions ne reviendront pas
        conn.status = ConnectionStatus.REVOKED
        conn.session_id = None
    db.delete(row)
    remember_check(db, None)
    db.flush()
    return True


def export(db: Session) -> dict[str, Any] | None:
    """Export RGPD : domaine, indice et empreinte ; jamais le client_id entier, le secret ni le jeton."""
    row = stored(db)
    if row is None:
        return None
    return {
        "provider": row.provider,
        "domain": row.domain,
        "client_id_hint": row.client_hint,
        "secret_fingerprint": short_fingerprint(row.secret_fingerprint),
        "updated_at": row.updated_at.isoformat(),
    }


# ------------------------------------------------------------------ connexions (webview)
def start_connection(db: Session, client: PowensClient, public_url: str, conn: BankConnection | None = None) -> str:
    """Ouvre la webview : ajout d'une banque, ou reconnexion d'une connexion existante (authentification forte)."""
    state = secrets.token_urlsafe(32)
    if conn is None:
        conn = BankConnection(
            aspsp_name="Powens", aspsp_country="FR", status=ConnectionStatus.PENDING, provider=PROVIDER
        )
        db.add(conn)
    conn.state_hash = sha256_hex(state)
    conn.state_expires_at = utcnow() + STATE_TTL
    db.flush()
    code = client.temp_code()
    remote = conn.session_id if conn.status != ConnectionStatus.PENDING else None
    url = client.webview_url(code, redirect_url(public_url), state, connection_id=remote)
    audit.record(db, "user", "bank.connect.start", connection=conn.id, provider=PROVIDER, reconnect=remote is not None)
    return url


def complete_connection(db: Session, client: PowensClient, state: str, connection_id: str | None) -> BankConnection:
    conn = db.scalar(
        select(BankConnection).where(
            BankConnection.state_hash == sha256_hex(state), BankConnection.provider == PROVIDER
        )
    )
    if conn is None:
        raise PowensFlowError("Autorisation inconnue ou déjà utilisée")
    expired = conn.state_expires_at is None or conn.state_expires_at < utcnow()
    conn.state_hash = None  # usage unique
    conn.state_expires_at = None
    if expired:
        _drop_attempt(db, conn, "expired")
        raise PowensFlowError("Autorisation expirée, recommence la connexion")
    fresh = conn.status == ConnectionStatus.PENDING
    if fresh:
        if not connection_id or not connection_id.isdigit() or len(connection_id) > 18:
            _drop_attempt(db, conn, "no_connection")
            raise PowensFlowError("Retour de Powens sans connexion")
        same = next((c for c in connections(db) if c.session_id == connection_id), None)
        if same is not None:  # la même banque ajoutée deux fois : on garde la ligne d'origine et ses comptes
            db.delete(conn)
            conn, fresh = same, False
        conn.session_id = connection_id
    try:
        remote = client.get_connection(conn.session_id or "")
        conn.aspsp_name = (remote.bank_name or conn.aspsp_name)[:120]
        accounts = client.list_accounts(conn.session_id or "")
    except PowensError:
        if fresh:
            # La connexion existe chez Powens (avec ton accès bancaire) : supprimée au mieux, sinon gardée en erreur,
            # visible et coupable dans Bank.
            try:
                client.delete_connection(connection_id or "")
            except PowensError:
                conn.status = ConnectionStatus.ERROR
                conn.last_error = "Connexion inachevée chez Powens : coupe-la puis recommence."
            else:
                _drop_attempt(db, conn, "unreadable")
        raise
    conn.status = ConnectionStatus.ACTIVE
    conn.last_error = None
    skipped = [name for name in (_link_or_skip(db, conn, acc) for acc in accounts) if name]
    if skipped:
        conn.last_error = _elsewhere(skipped)
    audit.record(db, "user", "bank.connect.done", connection=conn.id, provider=PROVIDER, accounts=len(conn.accounts))
    return conn


def _drop_attempt(db: Session, conn: BankConnection, reason: str) -> None:
    """Une tentative de connexion sans suite (annulée, expirée, illisible) : rien ne reste chez Powens, la ligne en
    attente disparaît (seule la trace du journal reste). Une connexion déjà établie garde sa ligne."""
    if conn.status != ConnectionStatus.PENDING:
        return
    audit.record(db, "user", "bank.connect.failed", connection=conn.id, provider=PROVIDER, reason=reason)
    db.delete(conn)


def fail_connection(db: Session, state: str, error: str) -> None:
    """Retour de la webview en erreur (accès refusé, conditions déclinées…) : la demande en attente est close."""
    conn = db.scalar(
        select(BankConnection).where(
            BankConnection.state_hash == sha256_hex(state), BankConnection.provider == PROVIDER
        )
    )
    if conn is None:
        return
    conn.state_hash = None
    conn.state_expires_at = None
    _drop_attempt(db, conn, "cancelled" if error in ("access_denied", "tos_declined") else "failed")


def _elsewhere(names: list[str]) -> str:
    return f"Déjà suivi via Enable Banking, laissé de côté ici : {', '.join(names)}."[:300]


def _link_or_skip(db: Session, conn: BankConnection, remote: PowensAccount) -> str | None:
    """Rattache le compte ; rend son nom s'il est laissé de côté parce qu'Enable Banking le suit déjà."""
    if remote.type in IGNORED_TYPES:
        return None
    return (remote.name or "compte") if link_account(db, conn, remote) is None else None


def link_account(db: Session, conn: BankConnection, remote: PowensAccount) -> Account | None:
    """Rattache un compte Powens : retrouvé par son identifiant Powens, sinon par l'IBAN (un compte importé ou relié
    par Enable Banking garde son historique), sinon créé avec le type qui correspond. Un compte qu'une connexion Enable
    Banking vivante suit déjà n'est pas repris : deux agrégateurs sur un même compte doubleraient ses opérations."""
    if remote.type in IGNORED_TYPES:
        return None
    kr = get_keyring()
    uid = f"powens:{remote.id}"
    uid_idx = kr.blind_index(uid, "account.provider_uid")
    iban_idx = kr.blind_index(remote.iban, "account.iban") if remote.iban else None
    acc = db.scalar(select(Account).where(Account.provider_uid_bidx == uid_idx))
    if acc is None and iban_idx:
        acc = db.scalar(select(Account).where(Account.iban_bidx == iban_idx))
        if acc is not None and held_elsewhere(acc, conn, Provider.POWENS):
            return None
    if acc is None:
        suffix = f" ••{remote.iban[-4:]}" if remote.iban else ""
        acc = Account(
            name=(remote.name or conn.aspsp_name)[:100] + suffix,
            kind=KINDS.get(remote.type, AccountKind.OTHER),
            institution=conn.aspsp_name,
            currency=remote.currency,
            # un crédit ou un placement n'entre pas dans le budget du mois
            include_in_budget=KINDS.get(remote.type) == AccountKind.CHECKING,
        )
        db.add(acc)
    acc.provider = Provider.POWENS
    acc.provider_uid = uid
    acc.provider_uid_bidx = uid_idx
    if remote.iban:
        acc.iban, acc.iban_bidx = remote.iban, iban_idx
    acc.connection = conn
    db.flush()
    return acc


# ------------------------------------------------------------------ synchronisation
def _balance(client: PowensClient, acc: Account, remote: PowensAccount) -> int | None:
    cents = remote.balance_cents
    if cents is None and acc.kind in INVESTMENT_KINDS:
        cents = client.investments_valuation(remote.id)
    if cents is not None and acc.kind == AccountKind.CREDIT:
        cents = -abs(cents)  # capital restant dû : une dette
    return cents


def sync_connection(db: Session, client: PowensClient, conn: BankConnection, today: date) -> SyncReport:
    report = SyncReport()
    if conn.provider != PROVIDER or conn.status != ConnectionStatus.ACTIVE or not conn.session_id:
        return report
    since = (
        (conn.last_sync_at.date() - timedelta(days=OVERLAP_DAYS))
        if conn.last_sync_at
        else today - timedelta(days=FIRST_SYNC_DAYS)
    )
    try:
        remote = client.get_connection(conn.session_id)
        if remote.state in REAUTH_STATES:
            # L'authentification forte ne s'automatise pas (DSP2) : la carte « reconnecte ta banque » prend le relais.
            conn.status = ConnectionStatus.EXPIRED
            conn.last_error = STATE_MESSAGES[remote.state]
            report.errors.append(conn.last_error)
            audit.record(db, "system", "bank.sync", connection=conn.id, provider=PROVIDER, state=remote.state)
            return report
        warning = STATE_MESSAGES.get(remote.state, PASSING) if remote.state else None  # passager : on lit quand même
        skipped: list[str] = []
        for ra in client.list_accounts(conn.session_id):
            if ra.deleted:
                continue
            acc = link_account(db, conn, ra)
            if acc is None and ra.type not in IGNORED_TYPES:
                skipped.append(ra.name or "compte")
            if acc is None or acc.archived:
                continue
            cents = _balance(client, acc, ra)
            if cents is not None:
                acc.balance_cents, acc.balance_at = cents, utcnow()
            booked = [t for t in client.iter_transactions(ra.id, since, acc.currency) if t.status == "booked"]
            r = ingest(db, acc, booked, provider=PROVIDER)
            report.accounts += 1
            report.inserted += r.inserted
            report.duplicates += r.duplicates
            report.reconciled += r.reconciled
        conn.last_sync_at = utcnow()
        if skipped:
            warning = " ".join(filter(None, [warning, _elsewhere(skipped)]))[:300]
        conn.last_error = warning
        if warning:
            report.errors.append(warning)
    except PowensError as exc:
        conn.last_error = str(exc)[:300]
        if exc.status in (401, 403):
            conn.last_error = "Powens n'accepte plus le jeton de Bank : vérifie tes identifiants dans l'assistant."
        report.errors.append(conn.last_error)
    audit.record(
        db,
        "system",
        "bank.sync",
        connection=conn.id,
        provider=PROVIDER,
        inserted=report.inserted,
        errors=len(report.errors),
    )
    return report


def sync_all(factory: sessionmaker[Session], client: PowensClient, today: date) -> SyncReport:
    total = SyncReport()
    with factory() as db:
        conns = list(
            db.scalars(
                select(BankConnection).where(
                    BankConnection.provider == PROVIDER, BankConnection.status == ConnectionStatus.ACTIVE
                )
            )
        )
        for conn in conns:
            try:
                r = sync_connection(db, client, conn, today)
                db.commit()
            except Exception:  # une banque en panne ne bloque pas les autres
                db.rollback()
                log.exception("sync powens connection %s failed", conn.id)
                continue
            total.accounts += r.accounts
            total.inserted += r.inserted
            total.errors.extend(r.errors)
    return total
