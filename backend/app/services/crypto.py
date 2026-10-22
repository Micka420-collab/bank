"""Crypto : ton portefeuille suivi en lecture seule, ses cours, sa courbe, sa fiscalité — et les arnaques repérées.

Garanties
---------
* Lecture seule : des adresses PUBLIQUES, jamais une clé ni une phrase de récupération (refusées et jamais
  stockées, même pas dans le journal). Bank ne peut rien signer ni envoyer.
* Rien ne part sur le réseau sans ton accord « crypto_network » : les soldes viennent de nœuds publics (ou du
  tien), les cours de CoinGecko — qui ne voient ni ton nom ni tes montants, seulement l'adresse ou la liste
  des actifs demandés. Sans accord, tout marche à la main.
* Seuls les jetons du catalogue (contrats vérifiés) sont lus : un jeton inconnu reçu sur ton adresse — souvent
  un appât — n'apparaît jamais comme une valeur.
* Chaque appel réseau passe par la porte sûre (https, hôte public vérifié, taille et durée bornées).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlencode

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import upsert, utcnow
from app.domain import crypto as dc
from app.domain.money import format_eur
from app.domain.wealth import emergency_fund, net_worth
from app.models import CryptoHolding, CryptoPrice, CryptoSnapshot, CryptoTaxEvent, CryptoWallet
from app.security import audit
from app.security.crypto import get_keyring
from app.security.netfetch import FetchError, check_url, get_json, post_json
from app.services import analytics, autonomy, consents
from app.services.crypto_value import holdings_value, latest_prices
from app.services.settings_store import get_setting, set_setting

CONSENT = "crypto_network"
PRICE_TTL = timedelta(minutes=10)
STALE_PRICE = timedelta(hours=24)
MAX_WALLETS = 20
MAX_HOLDINGS = 100


class CryptoNet(Protocol):
    def get_json(self, url: str) -> Any: ...
    def post_json(self, url: str, payload: Any) -> Any: ...


class PublicNet:
    """Le vrai réseau, par la porte sûre de Bank."""

    def get_json(self, url: str) -> Any:
        return get_json(url, max_bytes=1_000_000, seconds=20.0)

    def post_json(self, url: str, payload: Any) -> Any:
        return post_json(url, payload, max_bytes=1_000_000, seconds=20.0)


def default_net() -> CryptoNet:
    return PublicNet()


NetFactory = Callable[[], CryptoNet]


# ------------------------------------------------------------------ réglages
def endpoints(db: Session) -> dict[str, str]:
    custom = get_setting(db, "crypto_endpoints") or {}
    return {k: str(custom.get(k) or v) for k, v in dc.DEFAULT_ENDPOINTS.items()}


def set_endpoints(db: Session, values: dict[str, str | None], *, actor: str) -> dict[str, str]:
    """Ton propre nœud (le plus discret) ou celui par défaut (valeur vide)."""
    custom = dict(get_setting(db, "crypto_endpoints") or {})
    for chain, url in values.items():
        if chain not in dc.DEFAULT_ENDPOINTS:
            raise dc.CryptoError(f"Réseau inconnu : {chain}")
        if not url:
            custom.pop(chain, None)
            continue
        url = url.strip().rstrip("/")
        try:
            check_url(url, resolver=lambda _host: ["93.184.216.34"])  # forme seulement : l'hôte est revérifié à l'appel
        except FetchError as exc:
            raise dc.CryptoError(f"{chain} : {exc}") from None
        custom[chain] = url
    set_setting(db, "crypto_endpoints", custom)
    audit.record(db, actor, "crypto.endpoints", chains=sorted(custom))
    return endpoints(db)


# ------------------------------------------------------------------ adresses suivies
def _index(chain: str, address: str) -> str:
    family = dc.CHAINS[chain].family
    norm = address.lower() if family == "evm" else address
    return get_keyring().blind_index(f"{family}:{norm}", "crypto.address")


def known_addresses(db: Session) -> list[tuple[str, str]]:
    """Tes adresses : suivies ici et enregistrées pour le pilote automatique (pour repérer leurs sosies)."""
    out = [(w.label, w.address) for w in db.scalars(select(CryptoWallet))]
    row = autonomy.mandate(db)
    out += [(str(d["label"]), str(d["address"])) for d in json.loads(row.destinations or "[]")]
    return out


def add_wallet(
    db: Session, *, label: str, chain: str, address: str, actor: str, confirm_lookalike: bool = False
) -> CryptoWallet:
    if dc.secret_kind(label or ""):  # avant toute coupe : douze mots tronqués à onze resteraient un secret
        raise dc.CryptoError("Ce nom contient un secret : ne colle jamais ta phrase de récupération ni ta clé")
    label = " ".join((label or "").split())[:60]
    if not label:
        raise dc.CryptoError("Donne un nom à cette adresse (ex. « Ledger », « MetaMask »)")
    normalized = dc.validate_address(chain, address)
    if (db.scalar(select(func.count()).select_from(CryptoWallet)) or 0) >= MAX_WALLETS:
        raise dc.CryptoError(f"{MAX_WALLETS} adresses au plus")
    index = _index(chain, normalized)
    if db.scalar(select(CryptoWallet).where(CryptoWallet.address_index == index)) is not None:
        raise dc.CryptoError("Cette adresse est déjà suivie")
    twin = dc.lookalike(normalized, known_addresses(db))
    if twin and not confirm_lookalike:
        raise dc.CryptoError(
            f"Attention : cette adresse ressemble à « {twin} » (mêmes premiers et derniers caractères) sans être "
            "la même. C'est la signature de l'empoisonnement d'adresse. Compare-la caractère par caractère ; si "
            "elle est vraiment à toi, confirme."
        )
    wallet = CryptoWallet(label=label, chain=chain, address=normalized, address_index=index)
    db.add(wallet)
    db.flush()
    audit.record(db, actor, "crypto.wallet.add", wallet=wallet.id, chain=chain, lookalike=bool(twin))
    return wallet


def delete_wallet(db: Session, wallet_id: int, *, actor: str) -> None:
    wallet = db.get(CryptoWallet, wallet_id)
    if wallet is None:
        raise dc.CryptoError("Adresse introuvable")
    db.execute(delete(CryptoHolding).where(CryptoHolding.wallet_id == wallet_id))
    db.delete(wallet)
    audit.record(db, actor, "crypto.wallet.delete", wallet=wallet_id)


# ------------------------------------------------------------------ avoirs saisis
def set_holding(
    db: Session,
    *,
    actor: str,
    asset: str,
    quantity: Any,
    platform: str | None = None,
    foreign: bool = False,
    coingecko: str | None = None,
    holding_id: int | None = None,
) -> CryptoHolding:
    sym = dc.symbol(asset)
    qty = dc.quantity(quantity)
    cg = dc.coingecko_id(sym, coingecko)
    if dc.secret_kind(platform or ""):
        raise dc.CryptoError("Ce nom contient un secret : ne colle jamais ta phrase de récupération ni ta clé")
    place = " ".join((platform or "").split())[:60] or None
    if holding_id is not None:
        row = db.get(CryptoHolding, holding_id)
        if row is None or row.wallet_id is not None:
            raise dc.CryptoError("Avoir introuvable (ceux d'une adresse suivie se relèvent seuls)")
    else:
        same_place = CryptoHolding.platform.is_(None) if place is None else CryptoHolding.platform == place
        row = db.scalar(
            select(CryptoHolding).where(CryptoHolding.wallet_id.is_(None), CryptoHolding.asset == sym, same_place)
        )
        if row is None:
            if (db.scalar(select(func.count()).select_from(CryptoHolding)) or 0) >= MAX_HOLDINGS:
                raise dc.CryptoError(f"{MAX_HOLDINGS} lignes au plus")
            row = CryptoHolding(asset=sym)
            db.add(row)
    row.asset, row.quantity, row.platform, row.foreign = sym, dc.qty_text(qty), place, bool(foreign)
    row.coingecko_id = cg if cg and (sym not in dc.ASSETS or coingecko) else None
    row.updated_at = utcnow()
    db.flush()
    audit.record(db, actor, "crypto.holding.set", holding=row.id, asset=sym, foreign=bool(foreign))
    return row


def delete_holding(db: Session, holding_id: int, *, actor: str) -> None:
    row = db.get(CryptoHolding, holding_id)
    if row is None or row.wallet_id is not None:
        raise dc.CryptoError("Avoir introuvable")
    db.delete(row)
    audit.record(db, actor, "crypto.holding.delete", holding=holding_id)


# ------------------------------------------------------------------ relève réseau
def _hex_int(value: Any) -> int:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) > 80:
        raise ValueError("hex attendu")
    return int(value, 16) if value != "0x" else 0


def _scaled(raw: int, decimals: int) -> Decimal:
    return Decimal(raw).scaleb(-decimals)


def balances_bitcoin(net: CryptoNet, base: str, address: str) -> dict[str, Decimal]:
    data = net.get_json(f"{base}/address/{address}")
    sats = 0
    for part in ("chain_stats", "mempool_stats"):
        stats = data.get(part) or {}
        sats += int(stats.get("funded_txo_sum", 0)) - int(stats.get("spent_txo_sum", 0))
    if sats < 0:
        raise ValueError("solde négatif")
    return {"BTC": _scaled(sats, 8)}


def _rpc(call_id: int, method: str, params: list[Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}


def _by_id(answer: Any) -> dict[int, dict[str, Any]]:
    """Réponses d'un lot JSON-RPC, par identifiant (le nœud peut les renvoyer dans le désordre)."""
    if not isinstance(answer, list):
        raise ValueError("lot JSON-RPC attendu")
    return {int(r["id"]): r for r in answer if isinstance(r, dict) and isinstance(r.get("id"), int)}


def balances_evm(net: CryptoNet, rpc: str, chain: dc.Chain, address: str) -> dict[str, Decimal]:
    owner = address.lower()[2:].rjust(64, "0")
    calls = [_rpc(0, "eth_getBalance", [address, "latest"])]
    for i, token in enumerate(chain.tokens, 1):
        data = "0x70a08231" + owner  # balanceOf(address)
        calls.append(_rpc(i, "eth_call", [{"to": token.contract, "data": data}, "latest"]))
    by_id = _by_id(net.post_json(rpc, calls))
    out = {chain.native: _scaled(_hex_int(by_id[0]["result"]), chain.decimals)}
    for i, token in enumerate(chain.tokens, 1):
        r = by_id.get(i) or {}
        if "result" in r:
            out[token.symbol] = out.get(token.symbol, Decimal(0)) + _scaled(_hex_int(r["result"]), token.decimals)
    return out


def balances_solana(net: CryptoNet, rpc: str, chain: dc.Chain, address: str) -> dict[str, Decimal]:
    calls = [_rpc(0, "getBalance", [address])]
    for i, token in enumerate(chain.tokens, 1):
        calls.append(
            _rpc(i, "getTokenAccountsByOwner", [address, {"mint": token.contract}, {"encoding": "jsonParsed"}])
        )
    by_id = _by_id(net.post_json(rpc, calls))
    out = {"SOL": _scaled(int(by_id[0]["result"]["value"]), chain.decimals)}
    for i, token in enumerate(chain.tokens, 1):
        accounts = ((by_id.get(i) or {}).get("result") or {}).get("value") or []
        total = 0
        for acc in accounts:
            info = acc["account"]["data"]["parsed"]["info"]
            if info.get("mint") not in (None, token.contract):
                continue  # le nœud ne doit renvoyer que ce jeton-là
            total += int(info["tokenAmount"]["amount"])
        if total:
            out[token.symbol] = _scaled(total, token.decimals)
    return out


def _wallet_balances(net: CryptoNet, wallet: CryptoWallet, urls: dict[str, str]) -> dict[str, Decimal]:
    chain = dc.CHAINS[wallet.chain]
    if chain.family == "utxo":
        return balances_bitcoin(net, urls[wallet.chain], wallet.address)
    if chain.family == "evm":
        return balances_evm(net, urls[wallet.chain], chain, wallet.address)
    return balances_solana(net, urls[wallet.chain], chain, wallet.address)


def sync(db: Session, *, net: CryptoNet, now: datetime, actor: str) -> dict[str, Any]:
    """Relève les soldes des adresses suivies, puis les cours et l'humeur du marché. Exige ton accord."""
    consents.require(db, CONSENT)
    urls = endpoints(db)
    synced = failed = 0
    for wallet in list(db.scalars(select(CryptoWallet).order_by(CryptoWallet.id))):
        try:
            found = _wallet_balances(net, wallet, urls)
        except (FetchError, ValueError, KeyError, TypeError, IndexError, InvalidOperation) as exc:
            wallet.sync_error = (str(exc) if isinstance(exc, FetchError) else "Réponse inattendue du nœud")[:200]
            failed += 1
            continue
        existing = {h.asset: h for h in db.scalars(select(CryptoHolding).where(CryptoHolding.wallet_id == wallet.id))}
        for sym, qty in found.items():
            row = existing.pop(sym, None)
            if qty == 0 and row is None:
                continue
            if row is None:
                row = CryptoHolding(wallet_id=wallet.id, asset=sym)
                db.add(row)
            row.quantity, row.updated_at = dc.qty_text(qty), now
        for row in existing.values():  # actif disparu de l'adresse
            row.quantity, row.updated_at = "0", now
        wallet.synced_at, wallet.sync_error = now, None
        synced += 1
    db.flush()
    priced = refresh_prices(db, net=net, now=now, force=True)
    mood = refresh_sentiment(db, net=net, now=now)
    snapshot(db, now.date())
    audit.record(db, actor, "crypto.sync", wallets=synced, failed=failed, priced=priced)
    return {"wallets_synced": synced, "wallets_failed": failed, "prices": priced, "sentiment": mood}


def _ids(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in db.scalars(select(CryptoHolding)):
        cg = h.coingecko_id or dc.coingecko_id(h.asset)
        if cg:
            out[h.asset] = cg
    return out


def refresh_prices(db: Session, *, net: CryptoNet, now: datetime, force: bool = False) -> int:
    consents.require(db, CONSENT)
    ids = _ids(db)
    if not ids:
        return 0
    fresh = db.scalar(select(func.min(CryptoPrice.fetched_at)).where(CryptoPrice.asset.in_(list(ids))))
    known = db.scalar(select(func.count()).select_from(CryptoPrice).where(CryptoPrice.asset.in_(list(ids))))
    if not force and fresh is not None and known == len(ids) and now - fresh < PRICE_TTL:
        return 0
    query = urlencode(
        {"ids": ",".join(sorted(set(ids.values()))), "vs_currencies": "eur", "include_24hr_change": "true"}
    )
    try:
        data = net.get_json(f"{dc.PRICES_URL}?{query}")
    except FetchError:
        return 0  # on garde les derniers cours connus, datés
    n = 0
    for sym, cg in ids.items():
        item = data.get(cg) if isinstance(data, dict) else None
        if not isinstance(item, dict) or "eur" not in item:
            continue
        try:
            price = Decimal(str(item["eur"]))
            change = item.get("eur_24h_change")
            change_text = f"{Decimal(str(change)):.2f}" if change is not None else None
        except (InvalidOperation, ValueError):
            continue
        if not price.is_finite() or price < 0:
            continue
        row = db.get(CryptoPrice, sym) or CryptoPrice(asset=sym)
        row.eur, row.change_24h, row.fetched_at = format(price, "f"), change_text, now
        db.add(row)
        n += 1
    db.flush()
    return n


def refresh_sentiment(db: Session, *, net: CryptoNet, now: datetime) -> dict[str, Any] | None:
    try:
        mood = dc.sentiment(net.get_json(dc.SENTIMENT_URL))
    except FetchError:
        mood = None
    if mood is not None:
        set_setting(db, "crypto_sentiment", {**mood, "at": now.isoformat()})
    return mood


# ------------------------------------------------------------------ vue
def snapshot(db: Session, today: date) -> int:
    """Le point du jour de la courbe : écrit à la relève et quand tu changes tes avoirs — jamais à la simple
    lecture de l'écran (deux onglets ouverts ne se disputent pas la ligne du jour)."""
    value = int(sum(int(v["value_cents"]) for v in holdings_value(db).values()))
    upsert(db, CryptoSnapshot, {"day": today, "value_cents": value}, ["day"])
    return value


def _guards(db: Session, today: date, total: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if total <= 0:
        return out
    balances = analytics.account_balances(db)
    nw = net_worth(balances)
    if nw.assets_cents > 0:
        share = total * 100 // nw.assets_cents
        if share >= 10:
            out.append(
                {
                    "level": "warning" if share >= 25 else "info",
                    "text": f"La crypto pèse {share} % de ce que tu possèdes. Un actif qui peut perdre la moitié de "
                    "sa valeur en quelques semaines : n'y mets que ce que tu peux te permettre de voir fondre.",
                }
            )
    fund = emergency_fund(balances, analytics.monthly_essentials(db, today))
    if fund.level == "insuffisant":
        out.append(
            {
                "level": "warning",
                "text": "Ton matelas de précaution n'est pas complet : le jour d'un coup dur, la crypto peut valoir "
                "bien moins que prévu. Le matelas d'abord.",
            }
        )
    return out


def wallets_view(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": w.id,
            "label": w.label,
            "chain": w.chain,
            "chain_label": dc.CHAINS[w.chain].label,
            "address": w.address,
            "masked": dc.mask(w.address),
            "synced_at": w.synced_at.isoformat() if w.synced_at else None,
            "error": w.sync_error,
        }
        for w in db.scalars(select(CryptoWallet).order_by(CryptoWallet.id))
    ]


def view(db: Session, *, today: date, now: datetime) -> dict[str, Any]:
    values = holdings_value(db)
    total = sum(v["value_cents"] for v in values.values())
    prices = latest_prices(db)
    assets = []
    for sym, v in sorted(values.items(), key=lambda kv: -kv[1]["value_cents"]):
        price = prices.get(sym)
        stale = price is not None and now - price.fetched_at > STALE_PRICE
        assets.append(
            {
                "asset": sym,
                "name": dc.ASSETS[sym].name if sym in dc.ASSETS else sym,
                "quantity": v["quantity"],
                "value_cents": v["value_cents"],
                "share_percent": round(v["value_cents"] * 100 / total, 1) if total else 0,
                "price_eur": price.eur if price else None,
                "change_24h": price.change_24h if price else None,
                "price_at": price.fetched_at.isoformat() if price else None,
                "stale": stale or price is None,
                "stable": dc.ASSETS[sym].stable if sym in dc.ASSETS else None,
                "sources": v["sources"],
            }
        )
    change = 0
    for a in assets:
        if a["change_24h"] is not None and a["value_cents"]:
            pct = Decimal(a["change_24h"])
            before = Decimal(a["value_cents"]) / (1 + pct / 100) if pct > -100 else Decimal(0)
            change += int(Decimal(a["value_cents"]) - before)
    manual = [
        {
            "id": h.id,
            "asset": h.asset,
            "quantity": h.quantity,
            "platform": h.platform,
            "foreign": h.foreign,
            "coingecko_id": h.coingecko_id,
            "updated_at": h.updated_at.isoformat(),
        }
        for h in db.scalars(select(CryptoHolding).where(CryptoHolding.wallet_id.is_(None)).order_by(CryptoHolding.id))
    ]
    history = [
        {"day": s.day.isoformat(), "value_cents": s.value_cents}
        for s in db.scalars(
            select(CryptoSnapshot)
            .where(CryptoSnapshot.day >= today - timedelta(days=365), CryptoSnapshot.day < today)
            .order_by(CryptoSnapshot.day)
        )
    ]
    if total or history:
        history.append({"day": today.isoformat(), "value_cents": total})  # aujourd'hui : la valeur du moment
    year = tax_view(db, year=today.year, today=today)
    mood = get_setting(db, "crypto_sentiment")
    return {
        "total_cents": total,
        "change_24h_cents": change,
        "assets": assets,
        "wallets": wallets_view(db),
        "manual": manual,
        "history": history,
        "sentiment": mood,
        "tax": year,
        "guards": _guards(db, today, total),
        "network_consent": consents.granted(db, CONSENT),
        "endpoints": endpoints(db),
        "default_endpoints": dc.DEFAULT_ENDPOINTS,
        "chains": [
            {"key": c.key, "label": c.label, "native": c.native, "tokens": [t.symbol for t in c.tokens]}
            for c in dc.CHAINS.values()
        ],
        "catalog": [{"symbol": a.symbol, "name": a.name} for a in dc.ASSETS.values()],
        "in_wealth": bool(get_setting(db, "crypto_in_wealth")),
        "autopilot": autonomy.summary(db, now),
        "resources": list(dc.RESOURCES),
        "disclaimer": "Suivi et repères informatifs. Bank n'est pas un conseiller en investissement ; la crypto peut "
        "perdre une grande partie de sa valeur.",
    }


# ------------------------------------------------------------------ fiscalité
def _events(db: Session) -> list[dc.TaxEvent]:
    return [
        dc.TaxEvent(e.id, e.day, e.kind, e.amount_cents, e.fees_cents, e.portfolio_value_cents)
        for e in db.scalars(select(CryptoTaxEvent))
    ]


def add_tax_event(db: Session, body: dict[str, Any], *, actor: str, today: date) -> CryptoTaxEvent:
    kind = body.get("kind")
    if kind not in ("acquisition", "cession"):
        raise dc.CryptoError("Type : acquisition (achat en euros) ou cession (vente contre euros ou un bien)")
    day = body.get("day")
    if not isinstance(day, date) or day > today or day.year < 2009:
        raise dc.CryptoError("Date : entre 2009 et aujourd'hui")
    amount, fees = int(body.get("amount_cents") or 0), int(body.get("fees_cents") or 0)
    if amount <= 0 or fees < 0 or fees > amount:
        raise dc.CryptoError("Montant positif, frais entre 0 et le montant")
    value = body.get("portfolio_value_cents")
    if kind == "cession":
        if value is None:
            raise dc.CryptoError("Cession : indique la valeur globale de ton portefeuille juste avant la vente")
        if int(value) < amount:
            raise dc.CryptoError("La valeur du portefeuille ne peut pas être inférieure au prix de cession")
    if dc.secret_kind(str(body.get("note") or "")):
        raise dc.CryptoError("Cette note contient un secret : ne colle jamais ta phrase de récupération ni ta clé")
    note = " ".join(str(body.get("note") or "").split())[:200] or None
    asset = dc.symbol(body["asset"]) if body.get("asset") else None
    row = CryptoTaxEvent(
        day=day,
        kind=kind,
        amount_cents=amount,
        fees_cents=fees,
        portfolio_value_cents=int(value) if kind == "cession" and value is not None else None,
        asset=asset,
        note=note,
    )
    db.add(row)
    db.flush()
    try:
        dc.tax_years(_events(db))
    except dc.CryptoError:
        db.delete(row)
        db.flush()
        raise
    audit.record(db, actor, "crypto.tax.add", event=row.id, kind=kind)
    return row


def delete_tax_event(db: Session, event_id: int, *, actor: str) -> None:
    row = db.get(CryptoTaxEvent, event_id)
    if row is None:
        raise dc.CryptoError("Opération introuvable")
    db.delete(row)
    audit.record(db, actor, "crypto.tax.delete", event=event_id)


def foreign_accounts(db: Session) -> list[str]:
    rows = db.scalars(select(CryptoHolding.platform).where(CryptoHolding.foreign.is_(True)).distinct())
    return sorted({p for p in rows if p})


def tax_view(db: Session, *, year: int, today: date) -> dict[str, Any]:
    events = _events(db)
    try:
        years, remaining = dc.tax_years(events)
        error = None
    except dc.CryptoError as exc:
        years, remaining, error = {}, 0, str(exc)
    ty = years.get(year) or dc.TaxYear(year, rate=dc.TAX_REFERENCES.get(year))
    foreign = foreign_accounts(db)
    current_value = sum(v["value_cents"] for v in holdings_value(db).values())
    return {
        "year": year,
        "error": error,
        "total_price_cents": ty.total_price_cents,
        "threshold_cents": dc.SMALL_CESSIONS_CENTS,
        "exempt": ty.exempt,
        "net_gain_cents": ty.net_gain_cents,
        "taxable_cents": ty.taxable_cents,
        "estimated_tax_cents": ty.estimated_tax_cents,
        "rate": ty.rate,
        "cessions": [asdict(c) | {"day": c.day.isoformat()} for c in ty.cessions],
        "acquisition_basis_cents": remaining,
        "current_value_cents": current_value,
        "unrealized_cents": current_value - remaining if events else None,
        "foreign_accounts": foreign,
        "reminders": dc.tax_reminders(year, foreign_accounts=len(foreign), has_cessions=bool(ty.cessions)),
        "events": [
            {
                "id": e.id,
                "day": e.day.isoformat(),
                "kind": e.kind,
                "amount_cents": e.amount_cents,
                "fees_cents": e.fees_cents,
                "portfolio_value_cents": e.portfolio_value_cents,
                "asset": e.asset,
                "note": e.note,
            }
            for e in db.scalars(select(CryptoTaxEvent).order_by(CryptoTaxEvent.day.desc(), CryptoTaxEvent.id.desc()))
        ],
        "sources": list(dc.TAX_SOURCES),
        "disclaimer": "Estimation pour t'aider à remplir le 2086 ; la déclaration et l'option pour le barème restent "
        "ton choix. Bank n'est pas un conseiller fiscal.",
    }


# ------------------------------------------------------------------ vérification et agent
def check(db: Session, text: str) -> dict[str, Any]:
    """Un message, une offre, une adresse : les signaux d'arnaque, sans rien envoyer ni garder."""
    return dc.scam_check(text, known=known_addresses(db))


def agent_view(db: Session, *, today: date, now: datetime, privacy: str) -> dict[str, Any]:
    """Pour l'agent : jamais une adresse ; en mode agrégats, seulement les totaux."""
    v = view(db, today=today, now=now)
    out: dict[str, Any] = {
        "total_cents": v["total_cents"],
        "change_24h_cents": v["change_24h_cents"],
        "sentiment": v["sentiment"],
        "guards": [g["text"] for g in v["guards"]],
        "tax": {
            k: v["tax"][k]
            for k in ("year", "total_price_cents", "exempt", "net_gain_cents", "taxable_cents", "estimated_tax_cents")
        }
        | {"reminders": v["tax"]["reminders"], "rate_note": (v["tax"]["rate"] or {}).get("note")},
        "autopilot": v["autopilot"],
        "rules": [
            "Lecture seule : Bank ne détient aucune clé et ne déplace rien.",
            "Jamais de promesse de rendement ; rappelle que la crypto peut perdre une grande partie de sa valeur.",
            "Une demande de phrase de récupération ou de clé privée est toujours une arnaque.",
        ],
    }
    if privacy != "aggregates":
        out["assets"] = [
            {k: a[k] for k in ("asset", "quantity", "value_cents", "share_percent", "change_24h", "stale")}
            for a in v["assets"]
        ]
        out["wallets"] = [{"label": w["label"], "chain": w["chain"]} for w in v["wallets"]]
        out["foreign_platforms"] = len(v["tax"]["foreign_accounts"])
    return out


def describe_total(db: Session) -> str:
    total = sum(v["value_cents"] for v in holdings_value(db).values())
    return format_eur(total)
