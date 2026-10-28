"""Inventaire des avoirs existants, sans second portefeuille ni enrichissement externe non consenti.

Les comptes ont des soldes de comptes, pas des positions sous-jacentes par ISIN. Les lignes
déclarées ne sont jamais additionnées automatiquement aux comptes : elles peuvent les recouper.
Une crypto sans prix ou avec cotation périmée ne vaut jamais artificiellement zéro euro.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.wealth import LONG_TERM, REGULATED, load_regulated_products
from app.models import Account, CryptoHolding, Provider
from app.services import advisor
from app.services.crypto import STALE_PRICE
from app.services.crypto_value import holdings_value, latest_prices

TRACKED_KINDS = LONG_TERM | REGULATED | {"crypto"}
DECLARED_KINDS = {"pea", "assurance_vie", "per", "epargne_salariale", "crypto", "autre"}
REQUIRED_FACTS = (
    "Identifiant du produit (ISIN pour un titre ou fonds)",
    "DIC ou document officiel correspondant au produit exact",
    "Frais récurrents, d'entrée et de sortie",
    "Conditions de retrait et de blocage",
    "Risque de perte et garantie éventuelle",
    "Fiscalité applicable à la situation et à la durée",
)
REGULATED_FACTS = (
    "Taux applicable à la date de l'opération",
    "Conditions d'éligibilité et plafond propre au détenteur",
    "Date de mise à jour du solde bancaire",
)


def account_row(
    *,
    item_id: int,
    name: str,
    kind: str,
    currency: str,
    balance_cents: int,
    balance_at: datetime | None,
    provider: str,
    today: date,
    rates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Un solde en devise étrangère reste non converti : pas de FX implicite."""
    days = (today - balance_at.date()).days if balance_at else None
    valuation_status = (
        "devise_non_convertie"
        if currency != "EUR"
        else "date_inconnue"
        if days is None
        else "solde_ancien"
        if days > 30
        else "solde_date"
    )
    ref = rates.get(kind)
    return {
        "key": f"account:{item_id}",
        "label": name,
        "kind": kind,
        "origin": "compte",
        "source_label": "Saisie manuelle" if provider == Provider.MANUAL else "Compte suivi",
        "currency": currency,
        "quantity": None,
        "value_cents": balance_cents if currency == "EUR" else None,
        "valuation_status": valuation_status,
        "as_of": balance_at.date().isoformat() if balance_at else None,
        "rate_pct": ref["rate_pct"] if ref else None,
        "rate_as_of": ref["as_of"] if ref else None,
        "rate_source": ref["source"] if ref else None,
        "document_verified": False,
        "required_facts": list(REGULATED_FACTS if ref else REQUIRED_FACTS),
        "potential_overlap": False,
        "note": "Solde d'enveloppe ou de compte, pas une composition par titres. "
        "Aucun document officiel spécifique n'est lié à cette ligne.",
    }


def view(db: Session, today: date) -> dict[str, Any]:
    """Lecture seule de la base locale : aucun appel vers un courtier, une blockchain ou un fournisseur de prix."""
    rate_rows = load_regulated_products()
    rates = {
        r.kind: {"rate_pct": float(r.rate_percent), "as_of": r.as_of, "source": r.source}
        for r in rate_rows
    }
    positions: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    for account in db.scalars(select(Account).where(Account.archived.is_(False)).order_by(Account.id)):
        if account.kind not in TRACKED_KINDS:
            continue
        seen_kinds.add(account.kind)
        positions.append(
            account_row(
                item_id=account.id,
                name=account.name,
                kind=account.kind,
                currency=account.currency,
                balance_cents=account.balance_cents,
                balance_at=account.balance_at,
                provider=account.provider,
                today=today,
                rates=rates,
            )
        )

    declared = advisor.plain_answers(db).get("outside_assets") or []
    for i, asset in enumerate(declared):
        kind = str(asset.get("kind") or "autre")
        if kind not in DECLARED_KINDS:
            continue
        overlaps = kind in seen_kinds
        positions.append(
            {
                "key": f"declared:{i}",
                "label": str(asset.get("label") or "Actif déclaré"),
                "kind": kind,
                "origin": "declaration",
                "source_label": "Déclaré dans Mon profil",
                "currency": "EUR",
                "quantity": None,
                "value_cents": int(asset["value_cents"]),
                "valuation_status": "valeur_declaree_non_verifiee",
                "as_of": None,
                "rate_pct": None,
                "rate_as_of": None,
                "rate_source": None,
                "document_verified": False,
                "required_facts": list(REQUIRED_FACTS),
                "potential_overlap": overlaps,
                "note": "Valeur déclarée, non confirmée par un relevé de titres. "
                "Vérifier qu'elle n'est pas déjà incluse dans un compte suivi.",
            }
        )
        seen_kinds.add(kind)

    # Le moteur crypto agrège les avoirs manuels et synchronisés : ne pas additionner ses lignes à
    # une éventuelle enveloppe bancaire « crypto » ou à une déclaration « crypto » de l'onboarding.
    if db.scalar(select(CryptoHolding.id).limit(1)) is not None:
        now = utcnow()
        prices = latest_prices(db)
        values = holdings_value(db)
        for symbol, payload in sorted(values.items()):
            price = prices.get(symbol)
            fresh = price is not None and -STALE_PRICE <= now - price.fetched_at <= STALE_PRICE
            positions.append(
                {
                    "key": f"crypto:{symbol}",
                    "label": symbol,
                    "kind": "crypto",
                    "origin": "crypto",
                    "source_label": "Portefeuille crypto suivi ou déclaré",
                    "currency": "EUR",
                    "quantity": payload["quantity"],
                    "value_cents": int(payload["value_cents"]) if fresh else None,
                    "valuation_status": "cours_date" if fresh else "cours_absent_ou_perime",
                    "as_of": price.fetched_at.date().isoformat() if price else None,
                    "rate_pct": None,
                    "rate_as_of": None,
                    "rate_source": None,
                    "document_verified": False,
                    "required_facts": [
                        "Justificatif de détention et quantité",
                        "Cotation datée et source vérifiable",
                        "Risque de perte et liquidité de la plateforme",
                        "Frais et fiscalité de cession",
                    ],
                    "potential_overlap": "crypto" in seen_kinds,
                    "note": "Cours historique connu, non rafraîchi pour cette page. "
                    "Une cotation absente ou ancienne ne signifie pas que l'actif ne vaut rien.",
                }
            )

    return {
        "as_of": today.isoformat(),
        "positions": positions,
        "counts": {
            "total": len(positions),
            "unpriced_or_unverified": sum(
                p["valuation_status"] != "solde_date" and p["valuation_status"] != "cours_date"
                for p in positions
            ),
            "potential_overlaps": sum(bool(p["potential_overlap"]) for p in positions),
        },
        "note": "Inventaire sans total consolidé : certains comptes et actifs déclarés peuvent se recouper. "
        "Le solde d'un PEA ou d'une assurance vie ne donne ni les lignes, ni les frais, ni le DIC. "
        "Aucun document du coffre n'est attribué automatiquement à un produit sans identifiant vérifié.",
    }
