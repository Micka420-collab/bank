"""Radar prospectif : prévisions du moteur Bank et actualités *séparées*, sans causalité inventée.

Le mois estimé et les montants proviennent de yearahead ; les articles, uniquement de flux déjà
collectés avec consentement et horodatage. Aucune donnée bancaire n'est envoyée aux flux publics.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import yearahead as yd
from app.domain.forecast import MonthForecast, UpcomingEvent
from app.domain.money import format_eur
from app.models import WatchItem, WatchSource
from app.services import yearahead

WINDOW_DAYS = 45
MIN_AMOUNT_CENTS = 10_000
MAX_SIGNALS = 6
MAX_CONTEXT = 5
MACRO_TOPICS = frozenset({"marches", "geopolitique", "transparence"})
OFFICIAL_TRUST = frozenset({"officiel"})


BASIS = {
    "planned": "Dépense saisie par toi",
    "forecast": "Prévision Bank",
    "detected": "Récurrence détectée",
    "custom": "Échéance ajoutée",
}


def _level(reference_cents: int, buffer_cents: int) -> str:
    """Comme yearahead : « risk » sous zéro, « tight » sous le matelas, « watch » sinon."""
    if reference_cents < 0:
        return "risk"
    if reference_cents < buffer_cents:
        return "tight"
    return "watch"


def _signal(
    *,
    key: str,
    label: str,
    day: date,
    days: int,
    amount_cents: int,
    reference_cents: int,
    source: str,
    buffer_cents: int,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label[:60],
        "on": day.isoformat(),
        "days": days,
        "amount_cents": amount_cents,
        "projected_month_end_cents": reference_cents,
        "level": _level(reference_cents, buffer_cents),
        "basis": BASIS.get(source, "Estimation"),
        "source": source,
        "confidence": "declared" if source in ("planned", "custom") else "estimated",
    }


def month_signals(
    forecast: MonthForecast, planned: Sequence[UpcomingEvent], *, today: date, buffer_cents: int = 0
) -> list[dict[str, Any]]:
    """Les jours qui restent au mois en cours : yearahead commence au mois suivant, la prévision du mois les
    connaît (grosses sorties prévues, dépenses saisies). Référence : le point bas du mois, pas sa fin."""
    declared = {(e.date, e.label, e.amount_cents) for e in planned}
    out = []
    for event in forecast.upcoming:
        amount = -event.amount_cents
        days = (event.date - today).days
        if event.kind != "fixed" or amount < MIN_AMOUNT_CENTS or not 0 <= days <= WINDOW_DAYS:
            continue
        source = "planned" if (event.date, event.label, event.amount_cents) in declared else "forecast"
        out.append(
            _signal(
                key=f"radar:month:{event.date.isoformat()}:{event.label[:40]}:{amount}",
                label=event.label,
                day=event.date,
                days=days,
                amount_cents=amount,
                reference_cents=forecast.lowest_balance_cents,
                source=source,
                buffer_cents=buffer_cents,
            )
        )
    return out


def build_signals(
    y: yd.YearAhead,
    *,
    today: date,
    buffer_cents: int = 0,
    forecast: MonthForecast | None = None,
    planned: Sequence[UpcomingEvent] = (),
) -> list[dict[str, Any]]:
    """Une échéance n'est un risque estimé que si le mois associé finit sous zéro (« tight » : sous le matelas)."""
    months = {p.month: p for p in y.points}
    out = month_signals(forecast, planned, today=today, buffer_cents=buffer_cents) if forecast else []
    for spike in y.spikes:
        days = (spike.day - today).days
        if not 0 <= days <= WINDOW_DAYS or spike.amount_cents < MIN_AMOUNT_CENTS:
            continue
        # Les pics estimés sans date précise (impôts) ne doivent jamais devenir une alerte datée.
        if spike.source == "estimated":
            continue
        point = months.get(spike.day.strftime("%Y-%m"))
        if point is None:
            continue
        out.append(
            _signal(
                key=f"radar:{spike.key}:{spike.day.isoformat()}",
                label=spike.label,
                day=spike.day,
                days=days,
                amount_cents=spike.amount_cents,
                reference_cents=point.end_balance_cents,
                source=spike.source,
                buffer_cents=buffer_cents,
            )
        )
    rank = {"risk": 0, "tight": 1, "watch": 2}
    out.sort(key=lambda row: (rank[cast(str, row["level"])], row["on"], -cast(int, row["amount_cents"])))
    return out[:MAX_SIGNALS]


def macro_context(db: Session, today: date) -> list[dict[str, Any]]:
    """Titres publics récents, jamais interprétés comme rendement ou variation future."""
    cutoff = today - timedelta(days=14)
    rows = db.scalars(
        select(WatchItem)
        .where(WatchItem.status != "dismissed", WatchItem.published_at.is_not(None))
        .order_by(WatchItem.published_at.desc())
        .limit(70)
    )
    out = []
    for item in rows:
        if item.published_at is None or not cutoff <= item.published_at.date() <= today:
            continue
        try:
            topics = set(json.loads(item.topics))
        except (ValueError, TypeError):
            continue
        related = topics & MACRO_TOPICS
        if not related:
            continue
        source = db.get(WatchSource, item.source_id)
        if source is None or source.trust not in OFFICIAL_TRUST or not source.enabled:
            continue
        out.append(
            {
                "id": item.id,
                "title": item.title[:200],
                "published_at": item.published_at.date().isoformat(),
                "url": item.url if item.url.startswith("https://") else None,
                "source": source.name,
                "topics": sorted(related),
            }
        )
        if len(out) >= MAX_CONTEXT:
            break
    return out


def crypto_stress(db: Session, today: date) -> dict[str, Any]:
    """Test de résistance HYPOTHÉTIQUE : ni rendement futur ni recommandation de transaction."""
    from app.db import utcnow
    from app.services import crypto_value
    from app.services.crypto import STALE_PRICE

    holdings = crypto_value.holdings_value(db)
    prices = crypto_value.latest_prices(db)
    assets: list[dict[str, Any]] = []
    total_fresh_cents = 0
    stale_count = 0
    now = utcnow()
    for asset, position in sorted(holdings.items()):
        value = int(position["value_cents"])
        quote = prices.get(asset)
        # Même fraîcheur que la page Crypto et l'inventaire des placements (STALE_PRICE, 24 h).
        recent = bool(quote and timedelta(0) <= now - quote.fetched_at <= STALE_PRICE)
        if not recent:
            stale_count += 1
        if value <= 0 or not recent or quote is None:
            continue
        assets.append({"asset": asset[:16], "value_cents": value, "as_of": quote.fetched_at.date().isoformat()})
        total_fresh_cents += value
    return {
        "tracked_count": len(holdings),
        "priced_count": len(assets),
        "stale_count": stale_count,
        "assets": sorted(assets, key=lambda x: -x["value_cents"])[:8],
        "valued_cents": total_fresh_cents,
        "shock_10_cents": total_fresh_cents // 10,
        "shock_25_cents": total_fresh_cents // 4,
        "note": "Chocs fictifs de -10 % / -25 % sur la valeur des cryptoactifs avec cours récent (24 h au plus, "
        "comme la page Crypto). Ils ne prédisent pas une baisse et n'incluent pas les actions, livrets ou placements "
        "sans inventaire/cotation disponible. Aucun achat ni vente automatique.",
    }


def signals(db: Session, today: date, *, y: yd.YearAhead | None = None) -> list[dict[str, Any]]:
    """Les signaux du radar ; `y` évite de recalculer yearahead quand l'appelant l'a déjà (passage des alertes)."""
    from app.services import analytics
    from app.services.settings_store import get_setting

    if not analytics.budget_accounts(db):
        return []
    inputs = analytics.forecast_inputs(db, today)
    return build_signals(
        y or yearahead.compute(db, today),
        today=today,
        buffer_cents=int(get_setting(db, "buffer_cents")),
        forecast=inputs.run(),
        planned=inputs.planned,
    )


def view(db: Session, today: date) -> dict[str, Any]:
    # Pas de faux signal de trésorerie si aucun compte courant n'alimente le modèle.
    from app.services import analytics, consents

    connected = bool(analytics.budget_accounts(db))
    return {
        "horizon_days": WINDOW_DAYS,
        "crypto_stress": crypto_stress(db, today),
        "available": connected,
        "signals": signals(db, today) if connected else [],
        "news_consent": consents.granted(db, "watch"),
        "macro_context": macro_context(db, today) if consents.granted(db, "watch") else [],
        "disclaimer": "Scénarios construits à partir des opérations connues. Une actualité mondiale ne prouve "
        "ni une baisse future de tes investissements ni une relation de cause à effet. "
        "Les alertes doivent être confirmées en consultant les chiffres et les sources.",
    }


def alerts(
    db: Session, *, today: date, privacy: str, public_url: str, y: yd.YearAhead | None = None
) -> list[tuple[str, str, bool]]:
    """Seules les dépenses déclarées non déjà alertées par yearahead, quand le mois passe sous zéro ou sous le
    matelas ; le texte dit lequel."""
    out = []
    for signal in signals(db, today, y=y):
        if signal["source"] != "planned" or signal["level"] == "watch":
            continue
        days = int(signal["days"])
        when = "Aujourd'hui" if days == 0 else "Demain" if days == 1 else f"Dans {days} jours"
        what = (
            "ton compte passerait sous zéro" if signal["level"] == "risk" else "ton matelas de sécurité serait entamé"
        )
        text = (
            f"⚠️ {when}, une dépense prévue"
            + (f" ({format_eur(signal['amount_cents'])})" if privacy != "aggregates" else "")
            + f" : {what}. Solde estimé : {format_eur(signal['projected_month_end_cents'])}. "
            f"Vérifie le radar : {public_url}/#/veille"
        )
        out.append((signal["key"], text, signal["level"] == "risk"))
    return out[:3]
