"""Énergie : connexion Linky, synchronisation quotidienne, analyse coût / mensualité / pics, puissance maximale
atteinte chaque jour face aux kVA de ton contrat, courbe de charge, consommation corrigée du climat, simulation
Base / Heures creuses / Tempo et prix repère du gaz (#33)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import utcnow, write_lock
from app.domain import energy_analysis as ana
from app.domain import energy_signals as sig
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
from app.domain.money import format_eur
from app.models import (
    AppSetting,
    Category,
    EnergyConnection,
    EnergyLoadCurve,
    EnergyMaxPower,
    EnergyReading,
    RateObservation,
    Transaction,
)
from app.providers.conso_api import ConsoApiClient, ConsoApiError, looks_like_token
from app.security import audit
from app.services import consents
from app.services.settings_store import get_setting, set_setting

BACKFILL_DAYS = 400  # un peu plus d'un an : permet la comparaison avec le même mois de l'an dernier
CURVE_CONSENT = "linky_curve"
CURVE_CALLS_PER_RUN = 8  # 8 semaines de courbe par passe : l'année se rattrape en une semaine, sans insister
CURVE_HISTORY_DAYS = 366
CURVE_RETRY = timedelta(days=7)  # courbe jamais obtenue (enregistrement non activé chez Enedis) : un essai par semaine
GAS_RE = re.compile(r"\b(GAZ|GAS)\b")  # « TOTALENERGIES GAZ », « ENI GAS », « GAZ DE BORDEAUX »
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
    picked = _contract_of_option(label)
    if picked is not None:  # une ligne de la grille choisie dit aussi l'option et la puissance
        set_setting(db, "elec_contract", picked)


def _contract_of_option(label: str) -> dict[str, Any] | None:
    for o in trv_options():
        oid = str(o.get("id") or "")
        kind, _, kva = oid.partition("_")
        if o.get("label") == label and kind in sig.OPTIONS and kva.isdigit() and int(kva) in sig.STANDARD_KVA:
            return {"option": kind, "kva": int(kva)}
    return None


def contract(db: Session) -> dict[str, Any] | None:
    """Ton option et ta puissance souscrite : réglées par toi, ou lues sur la ligne de grille choisie comme tarif ;
    et tes heures creuses (« 22:00-06:00 ») quand tu les as indiquées."""
    raw = get_setting(db, "elec_contract")
    if isinstance(raw, dict) and raw.get("option") in sig.OPTIONS and raw.get("kva") in sig.STANDARD_KVA:
        out: dict[str, Any] = {"option": str(raw["option"]), "kva": int(raw["kva"])}
        if isinstance(raw.get("hc"), str) and raw["hc"]:
            out["hc"] = raw["hc"]
        return out
    return _contract_of_option(str(get_setting(db, "elec_tariff_label")))


def set_contract(db: Session, *, option: str, kva: int, hc: str | None = None) -> None:
    if option not in sig.OPTIONS:
        raise EnergyError("Option inconnue : Base, Heures creuses ou Tempo")
    if kva not in sig.STANDARD_KVA:
        raise EnergyError(f"Puissance non normalisée : {', '.join(str(k) for k in sig.STANDARD_KVA)} kVA")
    value: dict[str, Any] = {"option": option, "kva": kva}
    if hc and hc.strip():
        try:
            ana.parse_hc_ranges(hc)
        except ValueError as exc:
            raise EnergyError(str(exc)) from exc
        value["hc"] = hc.strip()[:40]
    set_setting(db, "elec_contract", value)


def _hc_ranges(c: dict[str, Any] | None) -> list[tuple[int, int]] | None:
    try:
        return ana.parse_hc_ranges(c["hc"]) if c and c.get("hc") else None
    except ValueError:
        return None


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
    db.execute(delete(EnergyMaxPower).where(EnergyMaxPower.connection_id == conn.id))
    db.execute(delete(EnergyLoadCurve).where(EnergyLoadCurve.connection_id == conn.id))
    set_setting(db, "energy_power_state", {})
    set_setting(db, "energy_curve_state", {})
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


# ------------------------------------------------------------------ puissance maximale (#33)
def sync_max_power(db: Session, today: date, *, make_client: ClientFactory = default_client, full: bool = False) -> int:
    """Rapatrie la puissance maximale des jours manquants jusqu'à hier, par le même jeton que la consommation.
    Le réseau est interrogé hors transaction (rien n'est verrouillé pendant l'appel) ; un échec est noté à part et
    ne touche ni la connexion ni la consommation. Retourne le nombre de jours ajoutés ou mis à jour."""
    conn = active_connection(db)
    if conn is None:
        return 0
    conn_id, prm, token = conn.id, conn.prm, conn.token
    last = db.scalar(
        select(EnergyMaxPower.day).where(EnergyMaxPower.connection_id == conn_id).order_by(EnergyMaxPower.day.desc())
    )
    start = today - timedelta(days=BACKFILL_DAYS) if full or last is None else last - timedelta(days=2)
    if start >= today:
        return 0
    db.commit()  # rien ne reste verrouillé pendant l'appel à Enedis
    try:
        rows = make_client(token).daily_max_power(prm, start, today)
        error = None
    except ConsoApiError as exc:
        rows, error = [], str(exc)[:300]
    write_lock(db)
    db.expire_all()
    if db.get(EnergyConnection, conn_id) is None:
        return 0  # déconnecté pendant l'appel : rien à garder
    state = dict(get_setting(db, "energy_power_state") or {})
    state["last_attempt_at"] = utcnow().isoformat()
    if error is not None:
        state["last_error"] = error
        set_setting(db, "energy_power_state", state)
        audit.record(db, "system", "energy.max_power_failed", connection=conn_id)
        return 0
    existing = {
        r.day: r
        for r in db.scalars(
            select(EnergyMaxPower).where(EnergyMaxPower.connection_id == conn_id, EnergyMaxPower.day >= start)
        )
    }
    changed = 0
    for r in rows:
        if not (start <= r.day < today) or not 0 <= r.va <= sig.MAX_VA:
            continue  # le client filtre déjà ; une implémentation de remplacement ne doit pas pouvoir l'oublier
        row = existing.get(r.day)
        if row is None:
            db.add(EnergyMaxPower(connection_id=conn_id, day=r.day, va=r.va, at=r.at))
            changed += 1
        elif (row.va, row.at) != (r.va, r.at):
            row.va, row.at = r.va, r.at
            changed += 1
    state["last_error"] = None
    state["last_sync_at"] = state["last_attempt_at"]
    set_setting(db, "energy_power_state", state)
    if changed:
        audit.record(db, "system", "energy.max_power", connection=conn_id, changed=changed)
    db.flush()
    return changed


def max_power(db: Session, since: date | None = None) -> list[sig.MaxPower]:
    conn = active_connection(db)
    if conn is None:
        return []
    stmt = select(EnergyMaxPower).where(EnergyMaxPower.connection_id == conn.id).order_by(EnergyMaxPower.day)
    if since is not None:
        stmt = stmt.where(EnergyMaxPower.day >= since)
    return [sig.MaxPower(r.day, r.va, r.at) for r in db.scalars(stmt)]


def latest_cre(db: Session, prefix: str) -> dict[str, tuple[str, str, str]]:
    """Dernière valeur relevée de chaque série CRE commençant par `prefix` : série → (période, valeur, adresse)."""
    out: dict[str, tuple[str, str, str]] = {}
    for r in db.scalars(select(RateObservation).where(RateObservation.series.like(f"{prefix}%"))):
        if r.series not in out or r.period > out[r.series][0]:  # périodes AAAA-MM-JJ : l'ordre du texte suffit
            out[r.series] = (r.period, r.value, r.source_url)
    return out


def grid(db: Session, option: str) -> dict[int, sig.GridPower]:
    """La grille de l'option : relevée à la CRE (Barèmes tenus à jour) puissance par puissance, sinon rates.json
    pour l'option Base (6 et 9 kVA). Pour chaque puissance, la grille la plus récente l'emporte."""
    latest = latest_cre(db, f"cre.{option}.")
    when = db.scalar(select(func.max(RateObservation.fetched_at)).where(RateObservation.series.like(f"cre.{option}.%")))
    source = "CRE, tarifs réglementés de vente de l'électricité (données ouvertes)" + (
        f", relus le {when:%d/%m/%Y}" if when else ""
    )
    out = sig.grid_from_rates(option, rates.current())
    for kva, g in sig.grid_from_observations(option, latest, source).items():
        if kva not in out or g.as_of >= out[kva].as_of:
            out[kva] = g
    return out


def _yearly_wh(db: Session, today: date) -> int | None:
    rows = readings(db, today - timedelta(days=sig.YEAR_DAYS))
    year = [r for r in rows if r.day < today]
    if sig.YEAR_DAYS - len(year) > sig.MAX_MISSING_DAYS:
        return None  # moins de onze mois de consommation : Bank ne compte pas l'écart de prix du kWh
    return sum(r.wh for r in year)


def power(db: Session, today: date) -> sig.PowerCheck:
    c = contract(db)
    option = c["option"] if c else None
    return sig.power_check(
        max_power(db, today - timedelta(days=sig.YEAR_DAYS + 1)),
        today=today,
        subscribed_kva=c["kva"] if c else None,
        option=option,
        grid=grid(db, option) if option else {},
        yearly_wh=_yearly_wh(db, today),
    )


def power_view(db: Session, today: date) -> dict[str, Any]:
    check = power(db, today)
    state = get_setting(db, "energy_power_state") or {}
    return {
        "status": check.status,
        "message": check.message,
        "subscribed_kva": check.subscribed_kva,
        "option": check.option,
        "max_kva": None if check.max_va is None else round(check.max_va / 1000, 2),
        "max_day": check.max_day.isoformat() if check.max_day else None,
        "max_at": check.max_at,
        "days": check.days,
        "months_available": check.months_available,
        "since": check.since.isoformat() if check.since else None,
        "target_kva": check.target_kva,
        "yearly_saving_cents": check.yearly_saving_cents,
        "subscription_saving_cents": check.subscription_saving_cents,
        "grid_as_of": check.grid_as_of,
        "grid_source": check.grid_source,
        "grid_url": check.grid_url,
        "source": "Enedis, puissance maximale quotidienne (Linky), via Conso API",
        "last_sync_at": state.get("last_sync_at"),
        "last_error": state.get("last_error"),
    }


# ------------------------------------------------------------------ courbe de charge (#33)
def _curve_ranges(
    db: Session, conn_id: int, today: date, state: dict[str, Any]
) -> tuple[tuple[date, date] | None, tuple[date, date] | None]:
    """Ce qu'il reste à lire : d'abord les jours récents, puis l'historique à reculons, 8 semaines par passe."""
    days = list(db.scalars(select(EnergyLoadCurve.day).where(EnergyLoadCurve.connection_id == conn_id)))
    budget = CURVE_CALLS_PER_RUN * 7
    if not days:
        return (today - timedelta(days=7), today), None
    newest, oldest = max(days), min(days)
    forward: tuple[date, date] | None = None
    start = max(newest - timedelta(days=1), today - timedelta(days=budget))
    if start < today:
        forward = (start, today)
        budget -= (today - start).days
    floor = today - timedelta(days=CURVE_HISTORY_DAYS)
    backward: tuple[date, date] | None = None
    if not state.get("history_done") and oldest > floor and budget >= 7:
        backward = (max(floor, oldest - timedelta(days=budget)), oldest)
    return forward, backward


def sync_load_curve(db: Session, today: date, *, now: Any = None, make_client: ClientFactory = default_client) -> int:
    """Rapatrie la courbe de charge (avec ton accord « Courbe de charge Linky »), par le même jeton. Réseau hors
    transaction ; un échec est noté à part, sans toucher la connexion ni la consommation. Retourne le nombre de
    jours ajoutés ou complétés."""
    conn = active_connection(db)
    if conn is None or not consents.granted(db, CURVE_CONSENT):
        return 0
    conn_id, prm, token = conn.id, conn.prm, conn.token
    state = dict(get_setting(db, "energy_curve_state") or {})
    moment = now or utcnow()
    retry_at = state.get("retry_after")
    if retry_at and str(retry_at) > moment.isoformat():
        return 0
    forward, backward = _curve_ranges(db, conn_id, today, state)
    db.commit()  # rien ne reste verrouillé pendant les appels à Enedis
    got: dict[date, list[int | None]] = {}
    error: str | None = None
    history_done = False
    client = make_client(token)
    try:
        if forward is not None:
            got.update(client.load_curve(prm, *forward))
        if backward is not None:
            older = client.load_curve(prm, *backward)
            got.update(older)
            history_done = not older or backward[0] <= today - timedelta(days=CURVE_HISTORY_DAYS)
    except ConsoApiError as exc:
        error = (
            f"Courbe de charge indisponible ({exc}). Son enregistrement s'active dans ton espace client Enedis ; "
            "l'historique commence à cette date."
        )
    write_lock(db)
    db.expire_all()
    if db.get(EnergyConnection, conn_id) is None or not consents.granted(db, CURVE_CONSENT):
        return 0
    state = dict(get_setting(db, "energy_curve_state") or {}) | {"last_attempt_at": moment.isoformat()}
    existing = {r.day: r for r in db.scalars(select(EnergyLoadCurve).where(EnergyLoadCurve.connection_id == conn_id))}
    if error is None and not got and not existing:
        error = (
            "Enedis n'a pas de courbe de charge pour la dernière semaine : son enregistrement s'active dans ton "
            "espace client Enedis, et l'historique commence à cette date."
        )
    changed = 0
    for day, slots in got.items():
        if not today - timedelta(days=CURVE_HISTORY_DAYS) <= day < today:
            continue
        row = existing.get(day)
        if row is None:
            db.add(EnergyLoadCurve(connection_id=conn_id, day=day, slots=json.dumps(slots)))
            changed += 1
            continue
        old = _slots(row.slots)
        merged = [n if n is not None else o for n, o in zip(slots, old, strict=True)]
        if merged != old:
            row.slots = json.dumps(merged)
            changed += 1
    state["last_error"] = error
    if history_done:
        state["history_done"] = True
    if error is not None and not existing and not got:
        state["retry_after"] = (moment + CURVE_RETRY).isoformat()  # rien chez Enedis : nouvel essai dans 7 jours
    else:
        state.pop("retry_after", None)
    if error is None:
        state["last_sync_at"] = moment.isoformat()
    set_setting(db, "energy_curve_state", state)
    if changed or error:
        audit.record(db, "system", "energy.load_curve", connection=conn_id, changed=changed, failed=error is not None)
    db.flush()
    return changed


def _slots(raw: str) -> list[int | None]:
    try:
        data = json.loads(raw)
    except ValueError:
        return [None] * ana.SLOTS
    if not isinstance(data, list) or len(data) != ana.SLOTS:
        return [None] * ana.SLOTS
    return [v if isinstance(v, int) and not isinstance(v, bool) else None for v in data]


def load_curve(db: Session, since: date) -> dict[date, list[int | None]]:
    """La courbe gardée, seulement avec ton accord en cours (le retirer la rend muette aussitôt)."""
    conn = active_connection(db)
    if conn is None or not consents.granted(db, CURVE_CONSENT):
        return {}
    rows = db.scalars(
        select(EnergyLoadCurve).where(EnergyLoadCurve.connection_id == conn.id, EnergyLoadCurve.day >= since)
    )
    return {r.day: _slots(r.slots) for r in rows}


def curve_view(db: Session, today: date) -> dict[str, Any]:
    state = get_setting(db, "energy_curve_state") or {}
    curve = load_curve(db, today - timedelta(days=CURVE_HISTORY_DAYS))
    full = [d for d, s in curve.items() if ana.complete(s)]
    return {
        "consent": consents.granted(db, CURVE_CONSENT),
        "days": len(full),
        "since": min(full).isoformat() if full else None,
        "last_sync_at": state.get("last_sync_at"),
        "last_error": state.get("last_error"),
    }


# ------------------------------------------------------------------ simulation des options (#33)
def simulation(db: Session, today: date, *, tempo_colors: dict[date, str], tempo_known: bool) -> ana.Simulation:
    c = contract(db)
    since = today - timedelta(days=367)
    return ana.simulate(
        readings(db, since),
        load_curve(db, since),
        tempo_colors,
        today=today,
        kva=c["kva"] if c else None,
        grids={o: grid(db, o) for o in sig.OPTIONS},
        hc_ranges=_hc_ranges(c),
        tempo_colors_known=tempo_known,
    )


def simulation_view(sim: ana.Simulation, current_option: str | None) -> dict[str, Any]:
    return {
        "status": sim.status,
        "message": sim.message,
        "kva": sim.kva,
        "mode": sim.mode,
        "days": sim.days,
        "exact_days": sim.exact_days,
        "kwh": sim.kwh,
        "options": [asdict(o) for o in sim.options],
        "current_option": current_option,
        "assumptions": list(sim.assumptions),
        "notes": list(sim.notes),
        "hc_breakeven_pct": sim.hc_breakeven_pct,
        "red_days": sim.red_days,
        "grid_as_of": sim.grid_as_of,
        "grid_url": sim.grid_url,
        "source": "Relevés Linky (Enedis, via Conso API) et grilles des tarifs réglementés de la CRE",
        "tempo_hours_url": ana.TEMPO_HOURS_URL,
    }


# ------------------------------------------------------------------ consommation corrigée du climat (#33)
def climate(db: Session, today: date, temps: dict[date, float]) -> ana.ClimateCheck:
    return ana.climate_check(readings(db, today - timedelta(days=800)), temps, today=today)


def climate_view(check: ana.ClimateCheck) -> dict[str, Any]:
    return {
        "status": check.status,
        "message": check.message,
        "raw_pct": check.raw_pct,
        "cold_pct": check.cold_pct,
        "corrected_pct": check.corrected_pct,
        "extra_kwh": check.extra_kwh,
        "period": check.period,
        "wh_per_hdd": check.wh_per_hdd,
        "r2": check.r2,
        "fit_days": check.fit_days,
        "threshold_pct": ana.DRIFT_PCT,
        "base_c": ana.HDD_BASE_C,
        "months": [asdict(m) | {"hdd": round(m.hdd), "prev_hdd": round(m.prev_hdd)} for m in check.months],
    }


# ------------------------------------------------------------------ gaz : prix repère de la CRE (#33)
def is_gas_supplier(merchant: str) -> bool:
    return bool(GAS_RE.search(merchant.upper()))


def gas_view(db: Session, today: date) -> dict[str, Any] | None:
    """Une ligne de comparaison, seulement quand Bank voit des prélèvements de gaz : ce que tu as payé sur douze mois
    (tes opérations) à côté du prix repère du mois (rates.json, daté et sourcé). Bank ne connaît pas tes MWh : il
    ne divise pas l'un par l'autre."""
    ref = ana.gas_reference(rates.current(), today)
    since = today - timedelta(days=365)
    rows = db.execute(
        select(Transaction.amount_cents, Transaction.merchant)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.slug == "energie",
            Transaction.booking_date >= since,
            Transaction.booking_date < today,
            Transaction.is_transfer.is_(False),
        )
    )
    paid = [(a, m or "") for a, m in rows if is_gas_supplier(m or "")]
    if not paid or ref is None:
        return None
    spent = -sum(a for a, _ in paid)
    merchants = sorted({m.strip() for _, m in paid})[:3]
    message = (
        f"Gaz : {format_eur(spent)} prélevés sur douze mois ({', '.join(merchants)}). Prix repère de la CRE en "
        f"{ana.month_year(ref.since)} : {ana.euro_mwh(ref.eur_per_mwh)} TTC, soit "
        f"{sig.euro_kwh(ref.eur_per_mwh / 1000)} le kWh, à comparer avec le prix du kWh de ta facture de gaz."
    )
    if ref.next_price is not None and ref.next_since is not None:
        message += f" Au {sig.fr_date(ref.next_since)} : {ana.euro_mwh(ref.next_price)} TTC."
    return {
        "message": message,
        "spent_cents": spent,
        "payments": len(paid),
        "eur_per_mwh": str(ref.eur_per_mwh),
        "since": ref.since.isoformat(),
        "next_eur_per_mwh": None if ref.next_price is None else str(ref.next_price),
        "next_since": ref.next_since.isoformat() if ref.next_since else None,
        "source": ref.source,
        "source_url": ref.source_url,
        "published": ref.published,
    }


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
    # Un prélèvement « TOTALENERGIES GAZ » n'est pas une mensualité d'électricité (#33).
    return [a for a, m in rows if is_electricity_supplier(m or "") and not is_gas_supplier(m or "")]


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
        "contract": contract(db),
        "standard_kva": list(sig.STANDARD_KVA),
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
    out["power"] = power_view(db, today)
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
        "power": {k: v for k, v in power_view(db, today).items() if k not in ("last_error", "last_sync_at")},
    }
