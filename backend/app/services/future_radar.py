"""Radar prospectif : prévisions du moteur Bank et actualités *séparées*, sans causalité inventée.

Le mois estimé et les montants proviennent de yearahead ; les articles, uniquement de flux déjà
collectés avec consentement et horodatage. Aucune donnée bancaire n'est envoyée aux flux publics.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import yearahead as yd
from app.domain.money import format_eur
from app.models import WatchItem, WatchSource
from app.services import yearahead

WINDOW_DAYS = 45
MIN_AMOUNT_CENTS = 10_000
MAX_SIGNALS = 6
MAX_CONTEXT = 5
MACRO_TOPICS = frozenset({"marches", "geopolitique", "transparence"})
OFFICIAL_TRUST = frozenset({"officiel"})


def build_signals(y: yd.YearAhead, *, today: date, buffer_cents: int = 0) -> list[dict[str, Any]]:
    """Une échéance n'est un risque estimé que si le mois associé finit sous le seuil."""
    months = {p.month: p for p in y.points}
    out = []
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
        at_risk = point.end_balance_cents < buffer_cents
        out.append(
            {
                "key": f"radar:{spike.key}:{spike.day.isoformat()}",
                "label": spike.label[:60],
                "on": spike.day.isoformat(),
                "days": days,
                "amount_cents": spike.amount_cents,
                "projected_month_end_cents": point.end_balance_cents,
                "level": "risk" if at_risk else "watch",
                "basis": (
                    "Dépense saisie par toi"
                    if spike.source == "planned"
                    else "Prévision Bank"
                    if spike.source == "forecast"
                    else "Récurrence détectée"
                    if spike.source == "detected"
                    else "Échéance ajoutée"
                    if spike.source == "custom"
                    else "Estimation"
                ),
                "source": spike.source,
                "confidence": "declared" if spike.source in ("planned", "custom") else "estimated",
            }
        )
    out.sort(key=lambda row: (row["level"] != "risk", row["on"], -cast(int, row["amount_cents"])))
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
    from app.services import crypto_value

    holdings = crypto_value.holdings_value(db)
    prices = crypto_value.latest_prices(db)
    assets: list[dict[str, Any]] = []
    total_fresh_cents = 0
    stale_count = 0
    for asset, position in sorted(holdings.items()):
        value = int(position["value_cents"])
        quote = prices.get(asset)
        recent = bool(quote and 0 <= (today - quote.fetched_at.date()).days <= 2)
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
        "note": "Chocs fictifs de -10 % / -25 % sur la valeur des cryptoactifs avec cours récent "
        "(48 h max). Ils ne prédisent pas une baisse et n'incluent pas les actions, livrets ou "
        "placements sans inventaire/cotation disponible. Aucun achat ni vente automatique.",
    }


def view(db: Session, today: date) -> dict[str, Any]:
    # Pas de faux signal de trésorerie si aucun compte courant n'alimente le modèle.
    from app.services import analytics, consents
    from app.services.settings_store import get_setting

    connected = bool(analytics.budget_accounts(db))
    forecast = yearahead.compute(db, today) if connected else None
    return {
        "horizon_days": WINDOW_DAYS,
        "crypto_stress": crypto_stress(db, today),
        "available": connected,
        "signals": build_signals(forecast, today=today, buffer_cents=int(get_setting(db, "buffer_cents")))
        if forecast
        else [],
        "news_consent": consents.granted(db, "watch"),
        "macro_context": macro_context(db, today) if consents.granted(db, "watch") else [],
        "disclaimer": "Scénarios construits à partir des opérations connues. Une actualité mondiale ne prouve "
        "ni une baisse future de tes investissements ni une relation de cause à effet. "
        "Les alertes doivent être confirmées en consultant les chiffres et les sources.",
    }


def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    """Seules les dépenses déclarées non déjà alertées par yearahead, avec vraie marge menacée."""
    from app.services import analytics
    from app.services.settings_store import get_setting

    if not analytics.budget_accounts(db):
        return []
    y = yearahead.compute(db, today)
    signals = build_signals(y, today=today, buffer_cents=int(get_setting(db, "buffer_cents")))
    return [
        (
            signal["key"],
            f"⚠️ Dans {signal['days']} jours, une dépense prévue peut fragiliser ton budget"
            + (f" ({format_eur(signal['amount_cents'])})" if privacy != "aggregates" else "")
            + f". Solde estimé fin de mois : {format_eur(signal['projected_month_end_cents'])}. "
            f"Vérifie le radar : {public_url}/#/veille",
            False,
        )
        for signal in signals
        if signal["source"] == "planned" and signal["level"] == "risk"
    ][:3]
