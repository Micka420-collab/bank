"""Valeur du portefeuille crypto à partir des avoirs et des derniers cours connus — sans réseau, sans dépendre
du reste des services (le patrimoine l'additionne sans boucle d'imports)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.crypto import MAX_VALUE_CENTS, qty_text, value_cents
from app.models import CryptoHolding, CryptoPrice, CryptoWallet


def latest_prices(db: Session) -> dict[str, CryptoPrice]:
    return {p.asset: p for p in db.scalars(select(CryptoPrice))}


def holdings_value(db: Session) -> dict[str, dict[str, Any]]:
    """{actif: {quantity, value_cents, sources}} — un actif sans cours compte pour 0 (et se voit comme tel)."""
    prices = latest_prices(db)
    labels = {w.id: w.label for w in db.scalars(select(CryptoWallet))}
    out: dict[str, dict[str, Any]] = {}
    for h in db.scalars(select(CryptoHolding)):
        try:
            qty = Decimal(h.quantity)
        except InvalidOperation:
            continue
        if not qty.is_finite() or qty <= 0:
            continue
        price = prices.get(h.asset)
        try:
            eur = Decimal(price.eur) if price else None
        except InvalidOperation:
            eur = None
        if eur is not None and not eur.is_finite():
            eur = None
        entry = out.setdefault(h.asset, {"qty": Decimal(0), "value_cents": 0, "sources": []})
        entry["qty"] += qty
        entry["value_cents"] = min(
            entry["value_cents"] + (value_cents(qty, eur) if eur is not None else 0), MAX_VALUE_CENTS
        )
        where = labels.get(h.wallet_id, "?") if h.wallet_id is not None else (h.platform or "Saisie")
        entry["sources"].append({"where": where, "quantity": qty_text(qty), "watched": h.wallet_id is not None})
    for entry in out.values():
        entry["quantity"] = qty_text(entry.pop("qty"))
    return out


def portfolio_value_cents(db: Session) -> int:
    return sum(int(v["value_cents"]) for v in holdings_value(db).values())
