"""Moteur SQLAlchemy, types personnalisés (UTC strict, champs chiffrés) et cycle de session."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Engine, MetaData, Text, create_engine, event, inspect, literal, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.security.crypto import get_keyring

log = logging.getLogger("bank")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Stocke en UTC naïf (SQLite n'a pas de fuseau), relit en UTC *aware*. Refuse les datetimes naïfs
    en écriture : une heure sans fuseau est un bug latent."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime naïf refusé : utilise utcnow() / un datetime aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class EncryptedText(TypeDecorator[str]):
    """Colonne chiffrée AES-256-GCM, liée à son contexte (AAD) : transparente pour le code métier."""

    impl = Text
    cache_ok = True

    def __init__(self, context: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context = context

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_keyring().encrypt(value, self.context)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_keyring().decrypt(value, self.context)


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            # Les pages libérées sont écrasées : une donnée supprimée ne traîne pas dans le fichier.
            cur.execute("PRAGMA secure_delete=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


def write_lock(db: Session) -> None:
    """Prend tout de suite le verrou d'écriture de SQLite (BEGIN IMMEDIATE) pour la transaction en cours.

    Sans lui, le pilote de Python ne commence une transaction qu'à la première écriture : deux requêtes parallèles
    lisent le même état (plafonds, tête du journal d'audit), décident chacune de leur côté, puis écrivent — et
    toutes passent. Avec lui, la seconde attend que la première ait validé, puis relit l'état à jour. Sans effet si
    la transaction a déjà écrit (elle détient alors déjà le verrou), ni hors SQLite."""
    conn = db.connection()
    if conn.dialect.name != "sqlite":
        return
    raw = conn.connection.dbapi_connection
    if raw is not None and not getattr(raw, "in_transaction", True):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def upsert(db: Session, model: type[Base], values: dict[str, Any], keys: list[str]) -> None:
    """INSERT … ON CONFLICT DO UPDATE : deux requêtes simultanées (un écran ouvert deux fois, la boucle de fond) ne
    se marchent plus dessus sur une ligne « une par jour »."""
    dialect = db.get_bind().dialect.name
    stmt = (pg_insert if dialect == "postgresql" else sqlite_insert)(model).values(**values)
    update = {k: v for k, v in values.items() if k not in keys}
    db.execute(stmt.on_conflict_do_update(index_elements=keys, set_=update))


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Une transaction par requête : commit si tout va bien, rollback sinon."""
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class SchemaUpgradeError(RuntimeError):
    """Un changement de schéma qu'on ne sait pas appliquer sans risque : il faut une migration écrite."""


def upgrade_schema(engine: Engine, metadata: MetaData) -> list[str]:
    """Met à niveau une base créée par une version précédente : ajoute les colonnes et index apparus depuis.

    ``create_all`` crée les tables manquantes mais ne touche jamais une table existante. SQLite ne sait qu'ajouter
    des colonnes : on ajoute celles qui sont facultatives ou qui ont une valeur par défaut fixe (rendue en SQL), et
    les index manquants. Tout le reste (colonne obligatoire sans valeur par défaut, colonne unique) arrête le
    démarrage avec un message clair plutôt que de corrompre la base. Idempotent."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    added: list[str] = []
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in tables:
                continue
            present = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in present:
                    continue
                if col.primary_key or col.unique:
                    raise SchemaUpgradeError(f"{table.name}.{col.name} : colonne clé ou unique, migration manuelle")
                default = col.default.arg if col.default is not None and col.default.is_scalar else None  # type: ignore[attr-defined]
                clause = ""
                if default is not None:
                    rendered = literal(default).compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
                    clause = f" DEFAULT {rendered}"
                if not col.nullable:
                    if default is None:
                        raise SchemaUpgradeError(
                            f"{table.name}.{col.name} : colonne obligatoire sans valeur par défaut, migration manuelle"
                        )
                    clause = f" NOT NULL{clause}"
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{clause}'))
                added.append(f"{table.name}.{col.name}")
            index_names = {i["name"] for i in insp.get_indexes(table.name)}
            for index in table.indexes:
                if index.name not in index_names:
                    index.create(conn, checkfirst=True)
                    added.append(f"index {index.name}")
    for item in added:
        log.info("schéma mis à niveau : %s", item)
    return added
