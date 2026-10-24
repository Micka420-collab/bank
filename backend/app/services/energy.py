"""Énergie : connexion Linky, synchronisation quotidienne, analyse coût / mensualité / pics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import rates
from app.domain.energy import (
    MAX_WH_PER_DAY,
    PRM_RE,
    DailyReading,
    EnergyInsights,
    Tariff,
    insights,
    is_electricity_supplier,
    typical_monthly_payment,
)
from app.models import AppSetting, Category, EnergyConnection, EnergyReading, Transaction
from app.providers.conso_api import ConsoApiClient, ConsoApiError, looks_like_token
from app.security import audit
from app.services.settings_store import get_setting, set_setting

BACKFILL_DAYS = 400  # un peu plus d'un an : permet la comparaison avec le même mois de l'an dernier
ClientFactory = Callable[[str], ConsoApiClient]


class EnergyError(ValueError):
    pass


def default_client(token: str) -> ConsoApiClient:
    return ConsoApiClient(token)


# ------------------------------------------------------------------ tarif
def trv_options() -> list[dict[str, Any]]:
    raw = rates.current()  # grille du fichier, recouverte de celle relevée à la CRE si elle est au moins aussi récente
    trv = raw["electricity_trv"]
    return [{**o, "as_of": trv["as_of"], "source": trv["source"]} for o in trv["options"]]


def current_tariff(db: Session) -> Tariff:
    if all(db.get(AppSetting, k) is None for k in ("elec_cents_per_100kwh", "elec_subscription_cents_per_year")):
        # Jamais réglé : le tarif par défaut (Base 6 kVA) suit la grille en vigueur, mise à jour par Bank s'il le faut.
        base6 = next((o for o in trv_options() if o["id"] == "base_6"), None)
        if base6 is not None:
            return Tariff(
                int(base6["cents_per_100kwh"]),
                int(base6["yearly_subscription_cents"]),
                str(get_setting(db, "elec_tariff_label")),
            )
    return Tariff(
        int(get_setting(db, "elec_cents_per_100kwh")),
        int(get_setting(db, "elec_subscription_cents_per_year")),
        str(get_setting(db, "elec_tariff_label")),
    )


def set_tariff(db: Session, *, cents_per_100kwh: int, subscription_cents_per_year: int, label: str) -> None:
    if not 1 <= cents_per_100kwh <= 100_000 or not 0 <= subscription_cents_per_year <= 1_000_000:
        raise EnergyError("Tarif hors limites")
    set_setting(db, "elec_cents_per_100kwh", cents_per_100kwh)
    set_setting(db, "elec_subscription_cents_per_year", subscription_cents_per_year)
    set_setting(db, "elec_tariff_label", label[:60])


# ------------------------------------------------------------------ connexion
def active_connection(db: Session) -> EnergyConnection | None:
    return db.scalar(select(EnergyConnection).where(EnergyConnection.status != "revoked").limit(1))


def connect_linky(
    db: Session, *, prm: str, token: str, actor: str, today: date, make_client: ClientFactory = default_client
) -> EnergyConnection:
    prm = prm.replace(" ", "")
    if not PRM_RE.match(prm):
        raise EnergyError("Le PRM (point de livraison) fait 14 chiffres : il est sur ta facture ou ton compteur")
    if not looks_like_token(token):
        raise EnergyError("Jeton Conso API invalide : copie-le entièrement depuis conso.boris.sh")
    if active_connection(db) is not None:
        raise EnergyError("Un compteur est déjà connecté : déconnecte-le d'abord")
    client = make_client(token)
    try:  # test immédiat sur une semaine : un mauvais jeton ou PRM est refusé tout de suite
        client.daily_consumption(prm, today - timedelta(days=8), today - timedelta(days=1))
    except ConsoApiError as exc:
        raise EnergyError(f"Connexion refusée : {exc}") from exc
    conn = EnergyConnection(prm=prm, token=token.strip(), status="active")
    db.add(conn)
    db.flush()
    audit.record(db, actor, "energy.connect", connection=conn.id)
    return conn


def disconnect(db: Session, actor: str) -> None:
    conn = active_connection(db)
    if conn is None:
        return
    db.execute(delete(EnergyReading).where(EnergyReading.connection_id == conn.id))
    db.delete(conn)
    audit.record(db, actor, "energy.disconnect", connection=conn.id)


# ------------------------------------------------------------------ synchronisation
def sync(db: Session, today: date, *, make_client: ClientFactory = default_client, full: bool = False) -> int:
    """Rapatrie les jours manquants jusqu'à hier (les données d'un jour arrivent le lendemain matin).
    Retourne le nombre de relevés ajoutés ou mis à jour."""
    conn = active_connection(db)
    if conn is None:
        return 0
    last = db.scalar(
        select(EnergyReading.day).where(EnergyReading.connection_id == conn.id).order_by(EnergyReading.day.desc())
    )
    start = today - timedelta(days=BACKFILL_DAYS) if full or last is None else last - timedelta(days=2)
    end_exclusive = today  # hier inclus
    if start >= end_exclusive:
        return 0
    try:
        rows = make_client(conn.token).daily_consumption(conn.prm, start, end_exclusive)
    except ConsoApiError as exc:
        conn.status = "error" if exc.permanent else "active"
        conn.last_error = str(exc)[:300]
        audit.record(db, "system", "energy.sync_failed", connection=conn.id, permanent=exc.permanent)
        return 0
    existing = {
        r.day: r
        for r in db.scalars(
            select(EnergyReading).where(EnergyReading.connection_id == conn.id, EnergyReading.day >= start)
        )
    }
    changed = 0
    for r in rows:
        if not (start <= r.day < end_exclusive) or not 0 <= r.wh <= MAX_WH_PER_DAY:
            continue  # le client filtre déjà ; une implémentation de remplacement ne doit pas pouvoir l'oublier
        row = existing.get(r.day)
        if row is None:
            db.add(EnergyReading(connection_id=conn.id, day=r.day, wh=r.wh))
            changed += 1
        elif row.wh != r.wh:
            row.wh = r.wh
            changed += 1
    conn.status, conn.last_error, conn.last_sync_at = "active", None, utcnow()
    if changed:  # une passe quotidienne sans nouveauté n'encombre pas le journal ; last_sync_at suffit
        audit.record(db, "system", "energy.sync", connection=conn.id, changed=changed)
    return changed


def readings(db: Session, since: date | None = None) -> list[DailyReading]:
    conn = active_connection(db)
    if conn is None:
        return []
    stmt = select(EnergyReading).where(EnergyReading.connection_id == conn.id).order_by(EnergyReading.day)
    if since is not None:
        stmt = stmt.where(EnergyReading.day >= since)
    return [DailyReading(r.day, r.wh) for r in db.scalars(stmt)]


# ------------------------------------------------------------------ mensualités du fournisseur
def electricity_payments(db: Session, today: date, months: int = 4) -> list[int]:
    """Prélèvements du fournisseur d'électricité sur les derniers mois (catégorie Énergie + marchand connu)."""
    since = today - timedelta(days=31 * months)
    rows = db.execute(
        select(Transaction.amount_cents, Transaction.merchant)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.slug == "energie",
            Transaction.booking_date >= since,
            Transaction.amount_cents < 0,
            Transaction.is_transfer.is_(False),
        )
    )
    return [a for a, m in rows if is_electricity_supplier(m or "")]


def analysis(db: Session, today: date) -> EnergyInsights:
    return insights(
        readings(db, today - timedelta(days=430)),
        today=today,
        tariff=current_tariff(db),
        monthly_payment_cents=typical_monthly_payment(electricity_payments(db, today)),
    )


def status(db: Session, today: date) -> dict[str, Any]:
    conn = active_connection(db)
    tariff = current_tariff(db)
    out: dict[str, Any] = {
        "connected": conn is not None,
        "tariff": {**asdict(tariff), "options": trv_options()},
        "connection": None,
        "insights": None,
    }
    if conn is None:
        return out
    ins = analysis(db, today)
    out["connection"] = {
        "id": conn.id,
        "label": conn.label,
        "prm_masked": "•••• •••• " + conn.prm[-4:],
        "status": conn.status,
        "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
        "last_error": conn.last_error,
        "created_at": conn.created_at.isoformat(),
    }
    out["insights"] = asdict(ins)
    out["daily"] = [{"day": r.day.isoformat(), "wh": r.wh} for r in readings(db, today - timedelta(days=60))]
    return out


def summary_for_agent(db: Session, today: date) -> dict[str, Any] | None:
    """Vue compacte pour le dossier de l'agent (pas de relevé quotidien, seulement les agrégats)."""
    conn = active_connection(db)
    if conn is None:
        return None
    ins = analysis(db, today)
    return {
        "source": "detected",
        "data_through": ins.data_through.isoformat() if ins.data_through else None,
        "stale": ins.stale,
        "last_30_days_kwh": round(ins.last_30_days_wh / 1000),
        "last_30_days_cost_cents": ins.last_30_days_cost_cents,
        "yearly_estimate_cents": ins.yearly_estimate_cents,
        "yearly_is_extrapolated": ins.yearly_is_extrapolated,
        "recommended_monthly_cents": ins.recommended_monthly_cents,
        "monthly_payment_cents": ins.monthly_payment_cents,
        "regularisation_cents": ins.regularisation_cents,
        "change_vs_last_year_percent": ins.change_vs_last_year_percent,
        "spike": ins.spike,
        "months": [{"month": m.month, "kwh": round(m.wh / 1000), "cost_cents": m.cost_cents} for m in ins.months[-6:]],
        "tariff": current_tariff(db).label,
    }
