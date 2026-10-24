"""L'année qui vient : assemble la prévision du mois (son solde de fin), les séries récurrentes, tes pics ajoutés ou
écartés, et laisse le domaine projeter douze mois. Alertes deux mois avant un pic, provision proposée en carte."""

from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.domain import yearahead as dom
from app.domain.money import format_eur
from app.security import audit
from app.services import analytics, housing, income_watch, territory
from app.services.settings_store import get_setting, set_setting

ALERT_DAYS = 60
ALERT_MIN_CENTS = 10_000
MAX_CUSTOM = 30


class YearAheadError(Exception):
    pass


def _settings(db: Session) -> dict[str, Any]:
    raw = get_setting(db, "year_spikes") or {}
    return {"custom": list(raw.get("custom") or []), "ignored": [str(k) for k in (raw.get("ignored") or [])]}


def _custom(db: Session) -> list[dom.CustomSpike]:
    out: list[dom.CustomSpike] = []
    for item in _settings(db)["custom"]:
        try:
            out.append(
                dom.CustomSpike(
                    str(item["key"]),
                    str(item["label"])[:60],
                    int(item["amount_cents"]),
                    date.fromisoformat(str(item["next_date"])),
                    int(item.get("every_months") or 12),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _planned(db: Session) -> list[tuple[date, str, int]]:
    profile = analytics.load_financial_profile(db)
    items = (profile.get("planned_expenses") or {}).get("value") or []
    out: list[tuple[date, str, int]] = []
    for item in items:
        try:
            out.append(
                (
                    date.fromisoformat(str(item["date"])),
                    str(item.get("label") or "Dépense prévue")[:60],
                    int(item["amount_cents"]),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def compute(db: Session, today: date) -> dom.YearAhead:
    inputs = analytics.forecast_inputs(db, today)
    month = inputs.run()
    cal = territory.calendar(db)  # jours fériés et vacances gardés (accord « calendrier »), sans réseau
    return dom.build_year_ahead(
        today=today,
        start_balance_cents=month.projected_end_balance_cents,
        series=inputs.series,
        categories=inputs.categories,
        txns=inputs.txns,
        custom=_custom(db),
        planned=_planned(db),
        estimated=income_watch.september_balance(db, today),  # solde d'impôt estimé (taux de prélèvement)
        ignored=_settings(db)["ignored"],
        buffer_cents=int(get_setting(db, "buffer_cents")),
        holidays=cal.holidays if cal is not None and cal.holidays else None,
        school_breaks=cal.breaks if cal is not None else (),
        forecasts=[f] if (f := housing.tax_provision(db, today)) is not None else [],
    )


def view(db: Session, today: date) -> dict[str, Any]:
    y = compute(db, today)
    data = dom.to_dict(y)
    data["savings_goal_cents"] = int(get_setting(db, "savings_goal_cents"))
    data["buffer_cents"] = int(get_setting(db, "buffer_cents"))
    data["custom"] = [
        {
            "key": c.key,
            "label": c.label,
            "amount_cents": c.amount_cents,
            "next_date": c.next_date.isoformat(),
            "every_months": c.every_months,
        }
        for c in _custom(db)
    ]
    cal = territory.calendar(db)
    data["calendar"] = (
        None
        if cal is None
        else {
            "zone": cal.zone,
            "academy": cal.academy,
            "school_zone": cal.school_zone,
            "note": "Jours fériés et vacances scolaires relus aux sources publiques ; un prélèvement qui tombe un "
            "dimanche ou un jour férié est placé au jour ouvré suivant.",
        }
    )
    data["disclaimer"] = (
        "Projection à partir de tes prélèvements réguliers, de tes revenus reconnus et de ton rythme de dépenses : "
        "un ordre de grandeur, pas une promesse. Un pic détecté demande deux occurrences ; tu peux en ajouter ou en "
        "écarter."
    )
    return data


# ------------------------------------------------------------------ réglages
def add_custom(db: Session, *, label: str, amount_cents: int, next_date: date, every_months: int, actor: str) -> str:
    label = " ".join(label.split())[:60]
    if not label:
        raise YearAheadError("Donne un nom à cette dépense")
    if not 100 <= amount_cents <= 100_000_000:
        raise YearAheadError("Montant entre 1 € et 1 000 000 €")
    if not 1 <= every_months <= 60:
        raise YearAheadError("Cadence entre 1 et 60 mois")
    settings = _settings(db)
    if len(settings["custom"]) >= MAX_CUSTOM:
        raise YearAheadError(f"{MAX_CUSTOM} dépenses ajoutées au plus")
    key = "custom:" + secrets.token_urlsafe(6)
    settings["custom"].append(
        {
            "key": key,
            "label": label,
            "amount_cents": amount_cents,
            "next_date": next_date.isoformat(),
            "every_months": every_months,
        }
    )
    set_setting(db, "year_spikes", settings)
    audit.record(db, actor, "yearahead.custom.add", key=key, amount_cents=amount_cents)
    return key


def remove_custom(db: Session, key: str, *, actor: str) -> None:
    settings = _settings(db)
    before = len(settings["custom"])
    settings["custom"] = [c for c in settings["custom"] if c.get("key") != key]
    if len(settings["custom"]) == before:
        raise YearAheadError("Dépense introuvable")
    set_setting(db, "year_spikes", settings)
    audit.record(db, actor, "yearahead.custom.remove", key=key)


def set_ignored(db: Session, key: str, ignored: bool, *, actor: str) -> None:
    settings = _settings(db)
    keys = [k for k in settings["ignored"] if k != key]
    if ignored:
        keys.append(key[:120])
    settings["ignored"] = keys[-100:]
    set_setting(db, "year_spikes", settings)
    audit.record(db, actor, "yearahead.ignore", key=key[:60], ignored=ignored)


# ------------------------------------------------------------------ alertes, cartes, agent
def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    out: list[tuple[str, str, bool]] = []
    y = compute(db, today)
    for s in y.spikes:
        days = (s.day - today).days
        if s.amount_cents < ALERT_MIN_CENTS or not 0 <= days <= ALERT_DAYS or s.source == "planned":
            continue
        label = s.label if privacy == "full" else "Un pic de dépense"
        out.append(
            (
                f"spike:{s.key}:{s.day.isoformat()}",
                f"📅 Dans {days} jour{'s' if days > 1 else ''} : {label}, {format_eur(s.amount_cents)}. "
                f"Provision conseillée pour lisser l'année : {format_eur(y.provision_monthly_cents)} par mois. "
                f"{public_url}/#/aujourdhui",
                False,
            )
        )
    if y.status == "risk":
        out.append(
            (
                f"year_risk:{y.lowest_month}",
                f"⚠️ Sur l'année qui vient, ton solde passerait sous zéro vers {dom.month_label(y.lowest_month)} "
                f"({format_eur(y.lowest_cents)}). Les pics sont connus : {public_url}/#/aujourdhui",
                False,
            )
        )
    return out


def provision_card(db: Session, today: date) -> dict[str, Any] | None:
    """Proposer de réserver chaque mois la provision des pics, si elle n'est pas déjà dans l'épargne visée."""
    y = compute(db, today)
    goal = int(get_setting(db, "savings_goal_cents"))
    if y.provision_monthly_cents < 2_000 or not y.spikes:
        return None
    target = goal + y.provision_monthly_cents
    biggest = max(y.spikes, key=lambda s: s.amount_cents)
    return {
        "key": f"yearahead:provision:{y.provision_monthly_cents}",
        "provision_cents": y.provision_monthly_cents,
        "target_cents": target,
        "yearly_cents": y.yearly_spikes_cents,
        "biggest": f"{biggest.label} ({format_eur(biggest.amount_cents)}, {biggest.day:%d/%m})",
        "count": len({s.key for s in y.spikes}),
        "lowest": f"{dom.month_label(y.lowest_month)} ({format_eur(y.lowest_cents)})",
        "status": y.status,
    }


def agent_view(db: Session, today: date, privacy: str) -> dict[str, Any]:
    y = compute(db, today)
    data = dom.to_dict(y)
    if privacy == "aggregates":
        data["spikes"] = [{**s, "label": "Pic"} for s in data["spikes"]]
        data["ignored"] = []
        for p in data["points"]:
            p["spikes"] = []
    data.pop("ignored", None)
    data["rule"] = (
        "Un ordre de grandeur calculé par Bank : explique le point bas et les pics, propose la provision mensuelle, "
        "ne recalcule rien toi-même."
    )
    return data


def within(today: date, days: int) -> date:
    return today + timedelta(days=days)
