"""Crypto : adresses vérifiées pour de vrai, secrets jamais acceptés, jetons inconnus ignorés, fiscalité exacte,
arnaques repérées — et rien sur le réseau sans accord."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.channels.text import secret_kind as chat_secret_kind
from app.db import utcnow
from app.domain import crypto as dc
from app.models import Account, AccountKind, AuditLog, CryptoHolding, CryptoWallet
from app.security.agents import PRESETS, issue_agent_token
from app.services import analytics, consents
from app.services import crypto as svc
from app.services.settings_store import set_setting
from tests.llm import configure as configure_assistant
from tests.test_mcp import Agent

TODAY = date(2026, 10, 21)
NOW = utcnow()
ETH_ADDR = dc.eip55("0x00000000219ab540356cbb839cbe05303d7705fa")
BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC_SEGWIT = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
BTC_TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
SOL_ADDR = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
SEED_EN = "abandon ability able about above absent absorb abstract absurd abuse access accident"
SEED_FR = "1. abaisser 2. abandon 3. abdiquer 4. abeille 5. abolir 6. aborder 7. aboutir 8. aboyer 9. abrasif 10. abreuver 11. abriter 12. abroger"
WIF = "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
WIF_COMPRESSED = "KwdMAjGmerYanjeui5SHS7JkmpZvVipYvB2LJGU1ZxJwYvP98617"


# ------------------------------------------------------------------ primitives
def test_keccak_and_eip55_reference_vectors() -> None:
    assert dc.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert dc.keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    # Bords de bloc (136 octets) : vérifiés contre une implémentation de référence.
    assert dc.keccak256(b"a" * 135).hex() == "34367dc248bbd832f4e3e69dfaac2f92638bd0bbd18f2912ba4ef454919cf446"
    assert dc.keccak256(b"a" * 136).hex() == "a6c4d403279fe3e0af03729caada8374b5ca54d8065329a3ebcaeb4b60aa386e"
    assert dc.keccak256(b"a" * 200).hex() == "96ea54061def936c4be90b518992fdc6f12f535068a256229aca54267b4d084d"
    for vector in (  # exemples de l'EIP-55
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ):
        assert dc.eip55(vector.lower()) == vector
        assert dc.validate_address("ethereum", vector.lower()) == vector


@settings(max_examples=60, deadline=None)
@given(st.binary(min_size=20, max_size=20), st.data())
def test_one_miscopied_letter_never_passes_the_checksum(raw: bytes, data: st.DataObject) -> None:
    good = dc.eip55("0x" + raw.hex())
    assert dc.validate_address("base", good) == good
    letters = [i for i, ch in enumerate(good) if i > 1 and ch.isalpha()]
    if not letters or good[2:] == good[2:].lower() or good[2:] == good[2:].upper():
        return
    i = data.draw(st.sampled_from(letters))
    bad = good[:i] + good[i].swapcase() + good[i + 1 :]
    with pytest.raises(dc.CryptoError, match="EIP-55"):
        dc.validate_address("ethereum", bad)


def test_bitcoin_and_solana_addresses_are_checksummed() -> None:
    assert dc.validate_address("bitcoin", BTC_LEGACY) == BTC_LEGACY
    assert dc.validate_address("bitcoin", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy").startswith("3J98")
    assert dc.validate_address("bitcoin", BTC_SEGWIT.upper()) == BTC_SEGWIT
    assert dc.validate_address("bitcoin", BTC_TAPROOT) == BTC_TAPROOT
    for bad in (
        "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb",  # dernier caractère changé
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh",  # somme bech32m sur une adresse v0 (BIP350)
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",  # somme bech32 sur une adresse v1
        "bc1qW508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # casse mélangée
    ):
        with pytest.raises(dc.CryptoError):
            dc.validate_address("bitcoin", bad)
    assert dc.validate_address("solana", SOL_ADDR) == SOL_ADDR
    with pytest.raises(dc.CryptoError):
        dc.validate_address("solana", "0OIl" + SOL_ADDR[4:])
    with pytest.raises(dc.CryptoError, match="Réseau inconnu"):
        dc.validate_address("tron", ETH_ADDR)


@pytest.mark.parametrize("secret", [SEED_EN, SEED_FR, WIF, WIF_COMPRESSED, "ma clé privée : " + "ab" * 32, "f" * 64])
def test_secrets_are_recognised_everywhere(secret: str) -> None:
    assert dc.secret_kind(secret) is not None
    assert chat_secret_kind(f"Salut, voilà : {secret} merci") is not None or secret == "f" * 64
    with pytest.raises(dc.CryptoError, match="SECRET"):
        dc.validate_address("ethereum", secret)


def test_an_xprv_or_a_solana_keypair_is_a_secret() -> None:
    xprv = "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi"
    assert dc.secret_kind(xprv) == "une clé privée étendue (xprv)"
    keypair = json.dumps([random.Random(1).randrange(256) for _ in range(64)])
    assert dc.secret_kind(keypair) is not None


@settings(max_examples=40, deadline=None)
@given(st.lists(st.sampled_from(sorted(dc.bip39_words())), min_size=12, max_size=24))
def test_any_twelve_list_words_in_a_row_are_a_seed(words: list[str]) -> None:
    assert dc.is_mnemonic(" ".join(words))


@pytest.mark.parametrize(
    "prose",
    [
        "Bonjour, je voudrais savoir combien je peux dépenser cette semaine pour les courses et le loyer du mois "
        "prochain sans toucher à mon livret A, et si la prime d'activité tombe bien le cinq.",
        "Can you tell me about the balance of my account and whether I can afford a new phone this month?",
        "Mon adresse Ethereum est " + ETH_ADDR + " et la transaction 0x" + "ab" * 32 + " est passée.",
    ],
)
def test_ordinary_messages_are_not_secrets(prose: str) -> None:
    assert dc.secret_kind(prose) is None


def test_lookalike_addresses_are_caught() -> None:
    known = [("Mon Ledger", ETH_ADDR)]
    twin = ETH_ADDR[:6] + ("0" if ETH_ADDR[6] != "0" else "1") + ETH_ADDR[7:]
    assert dc.lookalike(twin, known) == "Mon Ledger"
    assert dc.lookalike(ETH_ADDR.lower(), known) is None, "la même adresse, autre casse"
    assert dc.lookalike(dc.eip55("0x" + "12" * 20), known) is None
    # bc1q… : le préfixe commun ne compte pas pour la ressemblance.
    assert dc.lookalike("bc1qzzzz" + BTC_SEGWIT[8:], [("Cold", BTC_SEGWIT)]) is None


def test_quantities_are_exact() -> None:
    assert dc.qty_text(dc.quantity("0,00012345")) == "0.00012345"
    assert dc.qty_text(dc.quantity("100")) == "100"
    assert dc.qty_text(dc.quantity("1 000,5")) == "1000.5"
    for bad in ("-1", "abc", "1e30", "0." + "1" * 19, "NaN"):
        with pytest.raises(dc.CryptoError):
            dc.quantity(bad)
    assert dc.value_cents(Decimal("0.5"), Decimal("61234.56")) == 3_061_728


# ------------------------------------------------------------------ fiscalité
def test_tax_follows_article_150_vh_bis() -> None:
    events = [
        dc.TaxEvent(1, date(2026, 1, 10), "acquisition", 100_000),  # 1 000 € investis
        dc.TaxEvent(2, date(2026, 3, 1), "cession", 60_000, 0, 300_000),  # vend 600 € quand le tout vaut 3 000 €
        dc.TaxEvent(3, date(2026, 6, 1), "cession", 40_000, 1_000, 160_000),  # 400 € (10 € de frais), le tout 1 600 €
    ]
    years, basis = dc.tax_years(events)
    y = years[2026]
    first, second = y.cessions
    assert (first.acquisition_share_cents, first.gain_cents) == (20_000, 40_000)  # 1 000 × 600 / 3 000
    assert (second.acquisition_share_cents, second.gain_cents) == (20_000, 19_000)  # 800 × 400 / 1 600, moins frais
    assert basis == 60_000
    assert y.total_price_cents == 100_000 and not y.exempt
    assert y.taxable_cents == 59_000 and y.estimated_tax_cents == 18_526  # 31,4 %
    assert y.rate and y.rate["total_percent"] == "31.4"


def test_small_cessions_are_exempt_and_bad_entries_are_errors() -> None:
    years, _ = dc.tax_years(
        [
            dc.TaxEvent(1, date(2026, 1, 1), "acquisition", 10_000),
            dc.TaxEvent(2, date(2026, 2, 1), "cession", 30_500, 0, 50_000),
        ]
    )
    assert years[2026].exempt and years[2026].taxable_cents == 0 and years[2026].estimated_tax_cents == 0
    years, _ = dc.tax_years([dc.TaxEvent(1, date(2025, 2, 1), "cession", 50_000, 0, 50_000)])
    assert years[2025].estimated_tax_cents is None, "taux 2025 à vérifier : pas d'estimation inventée"
    with pytest.raises(dc.CryptoError, match="valeur globale"):
        dc.tax_years([dc.TaxEvent(1, date(2026, 2, 1), "cession", 50_000, 0, 10_000)])
    reminders = dc.tax_reminders(2026, foreign_accounts=2, has_cessions=True)
    assert any("2086" in r for r in reminders) and any("3916-bis" in r and "750" in r for r in reminders)


# ------------------------------------------------------------------ arnaques
def test_scam_check_reads_the_classics() -> None:
    fake_support = (
        "Bonjour, ici le support Ledger. Pour sécuriser vos fonds, envoyez votre phrase de récupération de 24 mots "
        "dans les 24 h, sinon votre compte sera bloqué."
    )
    out = dc.scam_check(fake_support)
    assert out["level"] == "danger" and {"seed", "urgency", "support"} <= {s["key"] for s in out["signals"]}
    assert any("0 805 805 817" in r["label"] for r in out["resources"])
    assert dc.scam_check("Rendement garanti de 20 % par mois, sans risque !")["level"] == "danger"
    assert dc.scam_check("Airdrop : réclamez vos jetons gratuits")["level"] == "warning"
    assert dc.scam_check("La gendarmerie : réglez votre amende en bitcoin")["level"] == "danger"
    assert dc.scam_check("Tu as pensé à payer le loyer ?")["level"] == "ok"
    twin = ETH_ADDR[:6] + ("0" if ETH_ADDR[6] != "0" else "1") + ETH_ADDR[7:]
    poisoned = dc.scam_check(f"Envoie les USDC à {twin}", known=[("Mon Ledger", ETH_ADDR)])
    assert poisoned["level"] == "danger" and "imite « Mon Ledger »" in poisoned["signals"][0]["label"]


# ------------------------------------------------------------------ service
class FakeNet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.broken: set[str] = set()

    def get_json(self, url: str) -> Any:
        self.calls.append(("GET", url, None))
        if "mempool" in url:
            return {"chain_stats": {"funded_txo_sum": 150_000_000, "spent_txo_sum": 50_000_000},
                    "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0}}  # fmt: skip
        if "coingecko" in url:
            return {
                "bitcoin": {"eur": 60000, "eur_24h_change": 2.5},
                "ethereum": {"eur": 3000.5, "eur_24h_change": -1.25},
                "usd-coin": {"eur": 0.92},
                "solana": {"eur": 150},
                "euro-coin": {"eur": 1.0},
            }
        if "alternative.me" in url:
            return {"data": [{"value": "72", "value_classification": "Greed"}]}
        raise AssertionError(url)

    def post_json(self, url: str, payload: Any) -> Any:
        self.calls.append(("POST", url, payload))
        if url in self.broken:
            return {"oops": True}
        if "solana" in url:
            return [
                {"jsonrpc": "2.0", "id": 0, "result": {"value": 2_500_000_000}},
                {"jsonrpc": "2.0", "id": 1, "result": {"value": [_spl("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "12500000")]}},
                {"jsonrpc": "2.0", "id": 2, "result": {"value": []}},
                {"jsonrpc": "2.0", "id": 3, "result": {"value": [_spl("ScamMint11111111111111111111111111111111111", "9" * 12)]}},
            ]  # fmt: skip
        out = [{"jsonrpc": "2.0", "id": 0, "result": hex(2 * 10**18)}]
        for call in payload[1:]:
            to = call["params"][0]["to"]
            amount = 250 * 10**6 if to == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" else 0
            out.append({"jsonrpc": "2.0", "id": call["id"], "result": hex(amount)})
        return list(reversed(out))  # un nœud peut répondre dans le désordre


def _spl(mint: str, amount: str) -> dict[str, Any]:
    return {"account": {"data": {"parsed": {"info": {"mint": mint, "tokenAmount": {"amount": amount, "decimals": 6}}}}}}


def _consent(db: Session) -> None:
    consents.set_consent(db, svc.CONSENT, True, actor="micka")


def test_a_seed_pasted_as_an_address_is_refused_and_leaves_no_trace(db: Session) -> None:
    with pytest.raises(dc.CryptoError, match="SECRET"):
        svc.add_wallet(db, label="Ledger", chain="ethereum", address=SEED_EN, actor="micka")
    db.flush()
    assert db.scalar(select(CryptoWallet)) is None
    logged = " ".join(a.details for a in db.scalars(select(AuditLog)))
    assert "abandon" not in logged
    with pytest.raises(dc.CryptoError, match="secret"):
        svc.add_wallet(db, label=SEED_EN, chain="ethereum", address=ETH_ADDR, actor="micka")


def test_wallets_are_unique_checksummed_and_lookalikes_need_confirmation(db: Session) -> None:
    w = svc.add_wallet(db, label="Ledger", chain="ethereum", address=ETH_ADDR.lower(), actor="micka")
    assert w.address == ETH_ADDR, "rangée avec sa somme de contrôle"
    with pytest.raises(dc.CryptoError, match="déjà suivie"):
        svc.add_wallet(db, label="Encore", chain="base", address=ETH_ADDR, actor="micka")  # même adresse EVM
    twin = ETH_ADDR[:6] + ("0" if ETH_ADDR[6] != "0" else "1") + ETH_ADDR[7:]
    twin = dc.eip55(twin.lower())
    with pytest.raises(dc.CryptoError, match="empoisonnement"):
        svc.add_wallet(db, label="Copie", chain="ethereum", address=twin, actor="micka")
    assert svc.add_wallet(db, label="Copie", chain="ethereum", address=twin, actor="micka", confirm_lookalike=True)
    raw = db.execute(select(CryptoWallet.address)).scalars().all()
    assert ETH_ADDR in raw  # déchiffrée par l'ORM…
    from sqlalchemy import text

    stored = db.execute(text("SELECT address FROM crypto_wallets")).scalars().all()
    assert all(ETH_ADDR.lower() not in s.lower() for s in stored), "…mais chiffrée en base"


def test_nothing_goes_on_the_network_without_consent(db: Session) -> None:
    svc.add_wallet(db, label="Ledger", chain="ethereum", address=ETH_ADDR, actor="micka")
    net = FakeNet()
    with pytest.raises(consents.ConsentError):
        svc.sync(db, net=net, now=NOW, actor="micka")
    assert net.calls == []


def test_sync_reads_balances_ignores_unknown_tokens_and_values_the_portfolio(db: Session) -> None:
    _consent(db)
    svc.add_wallet(db, label="Ledger", chain="ethereum", address=ETH_ADDR, actor="micka")
    svc.add_wallet(db, label="Cold", chain="bitcoin", address=BTC_SEGWIT, actor="micka")
    svc.add_wallet(db, label="Phantom", chain="solana", address=SOL_ADDR, actor="micka")
    net = FakeNet()
    out = svc.sync(db, net=net, now=NOW, actor="micka")
    assert out["wallets_synced"] == 3 and out["wallets_failed"] == 0 and out["sentiment"]["label"] == "Avidité"
    rpc = next(c for c in net.calls if c[0] == "POST" and "ethereum" in c[1])
    assert rpc[2][1]["params"][0]["data"] == "0x70a08231" + ETH_ADDR.lower()[2:].rjust(64, "0")
    v = svc.view(db, today=TODAY, now=NOW)
    by = {a["asset"]: a for a in v["assets"]}
    assert by["BTC"]["quantity"] == "1" and by["BTC"]["value_cents"] == 6_000_000
    assert by["ETH"]["quantity"] == "2" and by["ETH"]["value_cents"] == 600_100
    assert by["USDC"]["quantity"] == "262.5"  # 250 sur Ethereum + 12,5 sur Solana
    assert by["SOL"]["quantity"] == "2.5"
    assert all("Scam" not in json.dumps(a) for a in v["assets"]), "un jeton inconnu n'apparaît jamais"
    assert v["total_cents"] == sum(a["value_cents"] for a in v["assets"])
    assert v["history"][-1]["value_cents"] == v["total_cents"]
    assert v["change_24h_cents"] != 0
    # Le patrimoine compte la crypto, à part, jamais comme argent disponible.
    line = [b for b in analytics.account_balances(db) if b.kind == "crypto"]
    assert line and line[0].balance_cents == v["total_cents"] and line[0].id == 0
    set_setting(db, "crypto_in_wealth", False)
    assert not [b for b in analytics.account_balances(db) if b.kind == "crypto"]


def test_a_broken_node_keeps_the_last_balances_and_says_so(db: Session) -> None:
    _consent(db)
    w = svc.add_wallet(db, label="Ledger", chain="ethereum", address=ETH_ADDR, actor="micka")
    net = FakeNet()
    svc.sync(db, net=net, now=NOW, actor="micka")
    net.broken.add("https://ethereum-rpc.publicnode.com")
    out = svc.sync(db, net=net, now=NOW + timedelta(hours=1), actor="micka")
    assert out["wallets_failed"] == 1
    assert w.sync_error == "Réponse inattendue du nœud"
    eth = db.scalar(select(CryptoHolding).where(CryptoHolding.wallet_id == w.id, CryptoHolding.asset == "ETH"))
    assert eth is not None and eth.quantity == "2"


def test_manual_holdings_foreign_accounts_and_guards(db: Session) -> None:
    db.add(Account(name="Compte courant", kind=AccountKind.CHECKING, balance_cents=100_000))
    db.flush()
    _consent(db)
    svc.set_holding(db, actor="micka", asset="btc", quantity="0,1", platform="Kraken", foreign=True)
    svc.set_holding(db, actor="micka", asset="BTC", quantity="0,2", platform="Kraken", foreign=True)  # mise à jour
    svc.set_holding(db, actor="micka", asset="PEPE", quantity="1000000", platform="Binance", coingecko="pepe")
    svc.refresh_prices(db, net=FakeNet(), now=NOW, force=True)
    v = svc.view(db, today=TODAY, now=NOW)
    assert [m["quantity"] for m in v["manual"] if m["asset"] == "BTC"] == ["0.2"]
    pepe = next(a for a in v["assets"] if a["asset"] == "PEPE")
    assert pepe["value_cents"] == 0 and pepe["stale"] is True, "sans cours : vu comme tel, jamais inventé"
    assert v["tax"]["foreign_accounts"] == ["Kraken"]
    assert any("3916-bis" in r for r in v["tax"]["reminders"])
    assert any("pèse" in g["text"] for g in v["guards"]) and any("matelas" in g["text"] for g in v["guards"])
    with pytest.raises(dc.CryptoError):
        svc.set_holding(db, actor="micka", asset="BTC", quantity="-1")


# ------------------------------------------------------------------ API
def test_api_wallets_holdings_tax_and_settings(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    r = c.post("/api/crypto/wallets", json={"label": "Ledger", "chain": "ethereum", "address": SEED_FR})
    assert r.status_code == 422 and "SECRET" in r.json()["detail"]
    assert (
        c.post("/api/crypto/wallets", json={"label": "Ledger", "chain": "ethereum", "address": ETH_ADDR}).status_code
        == 201
    )
    assert c.post("/api/crypto/sync").status_code == 409, "accord requis"
    with app.state.sessionmaker() as s:
        _consent(s)
        s.commit()
    app.state.crypto_net = FakeNet
    r = c.post("/api/crypto/sync")
    assert r.status_code == 200 and r.json()["view"]["total_cents"] > 0
    assert c.post("/api/crypto/holdings", json={"asset": "ETH", "quantity": "abc"}).status_code == 422
    h = c.post("/api/crypto/holdings", json={"asset": "ETH", "quantity": "0.5", "platform": "Coinbase"}).json()["id"]
    assert c.put(f"/api/crypto/holdings/{h}", json={"asset": "ETH", "quantity": "0.6"}).status_code == 200
    assert c.delete(f"/api/crypto/holdings/{h}").status_code == 204
    bad = c.post("/api/crypto/tax", json={"day": "2026-03-01", "kind": "cession", "amount_cents": 60_000})
    assert bad.status_code == 422 and "valeur globale" in bad.json()["detail"]
    c.post("/api/crypto/tax", json={"day": "2026-01-10", "kind": "acquisition", "amount_cents": 100_000})
    r = c.post("/api/crypto/tax", json={"day": "2026-03-01", "kind": "cession", "amount_cents": 60_000,
                                        "portfolio_value_cents": 300_000})  # fmt: skip
    assert r.status_code == 201 and r.json()["tax"]["net_gain_cents"] == 40_000
    assert c.get("/api/crypto/tax?year=2026").json()["estimated_tax_cents"] == 12_560
    future = (date.today() + timedelta(days=2)).isoformat()
    assert c.post("/api/crypto/tax", json={"day": future, "kind": "acquisition", "amount_cents": 1}).status_code == 422
    assert c.put("/api/crypto/settings", json={"endpoints": {"ethereum": "http://192.168.1.2:8545"}}).status_code == 422
    ok = c.put("/api/crypto/settings", json={"endpoints": {"ethereum": "https://node.example.org"}, "in_wealth": False})
    assert ok.status_code == 200 and ok.json()["endpoints"]["ethereum"] == "https://node.example.org"
    assert ok.json()["in_wealth"] is False
    check = c.post("/api/crypto/check", json={"text": "Ici le support Binance, donnez votre phrase de récupération"})
    assert check.json()["level"] == "danger"
    wallets = c.get("/api/crypto").json()["wallets"]
    assert c.delete(f"/api/crypto/wallets/{wallets[0]['id']}").status_code == 204
    export = c.post("/api/export", json={"password": "une phrase de passe tres longue 42"}).json()["crypto"]
    assert export["tax_events"] and "wallets" in export


# ------------------------------------------------------------------ agent et messageries
def test_agent_sees_the_portfolio_never_an_address(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        _consent(s)
        svc.add_wallet(s, label="Ledger", chain="ethereum", address=ETH_ADDR, actor="micka")
        svc.sync(s, net=FakeNet(), now=utcnow(), actor="micka")
        _, raw = issue_agent_token(s, name="hermes", scopes=PRESETS["conseiller"])
        _, raw_agg = issue_agent_token(s, name="discret", scopes=PRESETS["observateur"], privacy="aggregates")
        s.commit()
    agent = Agent(auth_client, raw)
    out = agent.ok("get_crypto")
    assert out["total_cents"] > 0 and out["assets"] and out["wallets"] == [{"label": "Ledger", "chain": "ethereum"}]
    assert ETH_ADDR.lower() not in json.dumps(out).lower()
    agg = Agent(auth_client, raw_agg).ok("get_crypto")
    assert "assets" not in agg and "wallets" not in agg and agg["total_cents"] == out["total_cents"]
    checked = agent.ok("crypto_check", text="Connectez votre wallet pour réclamer vos jetons gratuits (airdrop)")
    assert checked["level"] == "danger"


def test_a_seed_never_reaches_the_model(world: TestClient) -> None:
    assert configure_assistant(world).status_code == 200
    r = world.post("/api/assistant/ask", json={"question": f"Garde ça pour moi : {SEED_EN}"})
    assert r.status_code == 422 and "phrase de récupération" in r.json()["detail"]
    from tests.llm import FakeLlm

    assert FakeLlm.calls == []
    assert "phrase de récupération" in (chat_secret_kind(SEED_FR) or "")
