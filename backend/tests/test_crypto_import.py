"""Import des exports Kraken, Binance et Coinbase : trois formats reconnus à leurs en-têtes, un aperçu avant tout,
les échanges crypto contre crypto écartés, aucun doublon d'un import à l'autre, la valeur du portefeuille reprise
du suivi (ou demandée, jamais inventée) — et rien d'identifiant gardé, rien sur le réseau."""

from __future__ import annotations

import json
import socket
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.api import deps
from app.importers import crypto_csv as cc
from app.importers.base import ImportFormatError
from app.models import AuditLog, CryptoImportExcluded, CryptoSnapshot, CryptoTaxEvent
from app.services import crypto_import

TODAY = date(2026, 10, 24)
FIXTURES = Path(__file__).parent / "fixtures" / "crypto"


def export(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def c(app, auth_client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    return auth_client


def send(c: TestClient, name: str, data: bytes, *, confirm: bool = False, values: dict[str, int] | None = None) -> Any:
    form = {"values": json.dumps(values)} if values else {}
    return c.post(
        "/api/crypto/import" if confirm else "/api/crypto/import/preview",
        files={"file": (name, data, "text/csv")},
        data=form,
    )


def snapshot(db: Session, day: date, cents: int) -> None:
    db.add(CryptoSnapshot(day=day, value_cents=cents))
    db.commit()


# ------------------------------------------------------------------ lecture des trois formats
def test_kraken_ledgers_pairs_legs_by_refid_and_normalises_assets() -> None:
    r = cc.parse(export("kraken_ledgers.csv"))
    assert (r.platform, r.format, r.rows) == ("kraken", "kraken_ledgers", 14)
    moves = [(m.day.isoformat(), m.kind, m.amount_cents, m.fees_cents, m.asset) for m in r.movements]
    assert moves == [
        ("2026-01-12", "acquisition", 50_000, 130, "BTC"),  # XXBT contre ZEUR
        ("2026-02-01", "acquisition", 20_000, 300, "ETH"),  # « Acheter des cryptos » : spend + receive
        ("2026-07-01", "cession", 30_000, 78, "BTC"),  # 22 h 30 UTC le 30 juin = 1er juillet à Paris
    ]
    reasons = {i.line: i.reason for i in r.ignored}
    assert reasons[2] == cc.PENDING and reasons[3] == cc.TRANSFER and reasons[8] == cc.SWAP  # ETH → USDC
    assert "USD" in reasons[12] and "inventé" in reasons[12]
    assert reasons[14] == cc.REWARD and reasons[15] == cc.SEND


def test_kraken_trades_shares_the_ledger_fingerprint_and_skips_margin() -> None:
    trades, ledgers = cc.parse(export("kraken_trades.csv")), cc.parse(export("kraken_ledgers.csv"))
    assert trades.format == "kraken_trades"
    assert trades.movements[0].key == ledgers.movements[0].key, "txid de l'échange = refid du grand livre"
    assert [(m.kind, m.asset, m.amount_cents, m.fees_cents) for m in trades.movements] == [
        ("acquisition", "BTC", 50_000, 130),
        ("cession", "ETH", 10_000, 26),
    ]
    assert [i.reason for i in trades.ignored] == [cc.SWAP, cc.DERIVATIVES]


def test_binance_groups_legs_by_second_and_never_reads_user_id() -> None:
    r = cc.parse(export("binance.csv"))
    assert r.platform == "binance"
    assert [(m.kind, m.asset, m.amount_cents, m.fees_cents, m.quantity) for m in r.movements] == [
        ("acquisition", "BTC", 30_000, 0, Decimal("0.00599400")),  # frais pris en BTC : déjà déduits
        ("cession", "BTC", 14_000, 14, Decimal("0.00200000")),
    ]
    reasons = {i.line: i.reason for i in r.ignored}
    assert reasons[9] == cc.SWAP and reasons[13] == cc.NO_EUR  # achat par carte : pas d'euros dans l'export
    assert reasons[14].startswith("Opération Binance non reconnue (« cmd"), "la formule n'est qu'un texte, sans « = »"
    assert "123456789" not in repr(r) and "bc1q" not in repr(r)


def test_coinbase_skips_its_preamble_and_reads_the_pair_from_notes() -> None:
    r = cc.parse(export("coinbase.csv"))
    assert (r.platform, r.rows) == ("coinbase", 7)
    assert [(m.kind, m.asset, m.amount_cents, m.fees_cents) for m in r.movements] == [
        ("acquisition", "BTC", 11_000, 199),
        ("cession", "BTC", 6_000, 36),  # « €60,000.00 » : séparateur de milliers à l'américaine
    ]
    reasons = [i.reason for i in r.ignored]
    assert reasons[:2] == [cc.SWAP, cc.SWAP], "ETH payé en BTC (paire ETH-BTC), puis une conversion"
    assert reasons[2] == cc.SEND and reasons[3] == cc.REWARD and "USD" in reasons[4]
    assert "Jeanne" not in repr(r) and "5f0c0000" not in repr(r) and "6650a1b2" not in repr(r)


def test_coinbase_old_format_without_id_still_deduplicates() -> None:
    data = (
        b"You can use this transaction report to inform your likely tax obligations.\n"
        b"Transactions\nUser,x@example.org\n\n"
        b"Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,Spot Price at Transaction,Subtotal,"
        b"Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
        b"2026-04-01T10:00:00Z,Buy,SOL,1,EUR,150,,151.50,1.50,\n"
        b"2026-04-01T10:00:00Z,Buy,SOL,1,EUR,150,,151.50,1.50,\n"
        b"2026-04-02T10:00:00Z,Recurring Buy,BTC,0.0001,EUR,60000,6,6.10,0.10,\n"
        b"2026-04-03T10:00:00Z,Sell,BTC,0.0001,EUR,60000,,5.90,0.10,\n"
    )
    r = cc.parse(data)
    assert [(m.kind, m.amount_cents, m.fees_cents) for m in r.movements] == [
        ("acquisition", 15_000, 150),  # sans sous-total : total − frais
        ("acquisition", 15_000, 150),
        ("acquisition", 600, 10),
        ("cession", 600, 10),  # vente : total + frais
    ]
    assert len({m.key for m in r.movements}) == 4, "deux achats identiques à la même seconde restent deux achats"
    assert [m.key for m in cc.parse(data).movements] == [m.key for m in r.movements]


def test_cells_numbers_dates_and_limits() -> None:
    assert cc.number("-€1,234.56") == Decimal("-1234.56") and cc.number("1E-8") == Decimal("1E-8")
    assert cc.number("(5.00)") == Decimal("-5.00") and cc.number("12 EUR") == Decimal(12)
    for bad in ("=1+1", "@SUM(A1)", "NaN", "Infinity", "1e400", "", "abc"):
        assert cc.number(bad) is None, bad
    assert cc.text('=HYPERLINK("x")') == 'HYPERLINK("x")' and cc.text("+a\x00b") == "a b"
    assert cc.asset("XXBT", kraken=True) == "BTC" and cc.asset("EUR.HOLD", kraken=True) == "EUR"
    assert cc.asset("XTZ", kraken=True) == "XTZ" and cc.asset("ETH2.S", kraken=True) == "ETH"
    assert cc.when("26-03-01 10:00:00") == cc.when("2026-03-01T10:00:00Z") == cc.when("2026-03-01 10:00:00 UTC")
    assert cc.when("01/03/2026") is None
    assert cc.paris_day(cc.when("2025-12-31 23:30:00")) == date(2026, 1, 1)  # type: ignore[arg-type]
    with pytest.raises(ImportFormatError, match="non reconnu"):
        cc.parse(b"date,libelle,montant\n2026-01-01,Cafe,-2\n")
    with pytest.raises(ImportFormatError, match="vide"):
        cc.parse(b"\n\n")
    with pytest.raises(ImportFormatError, match="volumineux"):
        cc.parse(b"x" * (cc.MAX_BYTES + 1))
    many = "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n" + "1,2026-01-01 00:00:00,Spot,Deposit,EUR,1,\n" * (
        cc.MAX_ROWS + 1
    )
    with pytest.raises(ImportFormatError, match="trop long"):
        cc.parse(many.encode())
    latin = "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n1,2026-01-01 00:00:00,Spot,Dépôt,EUR,1,é\n"
    assert cc.parse(latin.encode("cp1252")).ignored[0].reason.startswith("Opération Binance non reconnue (« dépôt")


def test_mixed_and_odd_groups_are_flagged_not_guessed() -> None:
    head = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"
    rows = [
        "L1,T1,2026-02-01 10:00:00,trade,,currency,ZEUR,-100,0,0",  # euros contre deux cryptos à la fois
        "L2,T1,2026-02-01 10:00:00,trade,,currency,XXBT,0.001,0,0",
        "L3,T1,2026-02-01 10:00:00,trade,,currency,XETH,-0.01,0,0",
        "L4,T2,2026-02-02 10:00:00,trade,,currency,ZEUR,-100,0,0",  # change EUR → USD
        "L5,T2,2026-02-02 10:00:00,trade,,currency,ZUSD,108,0,0",
        "L6,T3,2026-02-03 10:00:00,trade,,currency,XXBT,0.001,0,0",  # une jambe seule
        "L7,T4,2026-02-04 10:00:00,trade,,currency,ZEUR,-0.001,0,0",  # poussière
        "L8,T4,2026-02-04 10:00:00,trade,,currency,XXBT,0.00000001,0,0",
        "L9,T5,2026-02-05 10:00:00,trade,,currency,ZEUR,-1,5,0",  # frais > montant
        "L10,T5,2026-02-05 10:00:00,trade,,currency,XXBT,0.00001,0,0",
        "L11,T6,pas une date,trade,,currency,ZEUR,-1,0,0",
        "L12,T7,2026-02-06 10:00:00,margin,,currency,XXBT,0.1,0,0",
        "L13,T8,2026-02-06 10:00:00,adjustment,,currency,XXBT,0.1,0,0",
        "L14,T9,2026-02-06 10:00:00,mystere,,currency,XXBT,0.1,0,0",
    ]
    r = cc.parse((head + "\n".join(rows)).encode())
    assert r.movements == []
    reasons = [i.reason for i in sorted(r.ignored, key=lambda x: x.line)]
    assert "plusieurs cryptos" in reasons[0] and reasons[1] == cc.FIAT_ONLY and reasons[2] == cc.NO_EUR
    assert "poussière" in reasons[3] and "Frais" in reasons[4] and "illisible" in reasons[5]
    assert reasons[6] == cc.DERIVATIVES and reasons[7] == cc.INTERNAL and "mystere" in reasons[8]


# ------------------------------------------------------------------ aperçu, import, doublons
def test_preview_then_import_then_reimport_creates_nothing(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 7, 1), 2_000_000)  # le suivi connaît le portefeuille le jour de la vente
    p = send(c, "ledgers.csv", export("kraken_ledgers.csv"))
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["platform_label"] == "Kraken" and body["years"] == [2026]
    assert body["counts"] == {
        "acquisitions": 2,
        "cessions": 1,
        "to_complete": 0,
        "duplicates": 0,
        "manual_matches": 0,
        "excluded": 0,
        "ignored": 6,
    }
    sale = next(e for e in body["events"] if e["kind"] == "cession")
    assert sale["portfolio_value_cents"] == 2_000_000 and sale["value_source"] == "suivi"
    assert sale["value_day"] == "2026-07-01", "pas de relevé la veille : celui du jour, à vérifier"
    assert {r["reason"] for r in body["reasons"]} >= {cc.SWAP, cc.REWARD}
    assert db.scalars(select(CryptoTaxEvent)).first() is None, "l'aperçu n'écrit rien"

    r = send(c, "ledgers.csv", export("kraken_ledgers.csv"), confirm=True)
    assert r.status_code == 201, r.text
    assert r.json()["result"]["created"] == 3
    tax = r.json()["view"]["tax"]
    assert len(tax["events"]) == 3 and tax["events"][0]["note"] == "Import Kraken : 0.005 BTC"
    # 30 000 − 78 − (50 130 + 20 300) × 30 000 / 2 000 000 = 28 866 (arrondi au centime)
    assert tax["cessions"][0]["gain_cents"] == 30_000 - 78 - round(70_430 * 30_000 / 2_000_000)

    again = send(c, "ledgers.csv", export("kraken_ledgers.csv"), confirm=True).json()["result"]
    assert again["created"] == 0 and again["counts"]["duplicates"] == 3
    trades = send(c, "trades.csv", export("kraken_trades.csv")).json()
    assert trades["counts"]["duplicates"] == 1, "le même achat lu dans trades.csv n'est pas recompté"
    db.expire_all()
    assert len(list(db.scalars(select(CryptoTaxEvent)))) == 3
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "crypto.tax.import")).all()
    assert len(audit) == 2 and '"created":3' in audit[0].details


def test_sale_without_tracked_value_waits_for_yours(c: TestClient, db: Session) -> None:
    data = export("binance.csv")
    body = send(c, "binance.csv", data).json()
    assert body["counts"]["cessions"] == 0 and body["counts"]["to_complete"] == 1
    sale = next(e for e in body["events"] if e["status"] == "to_complete")
    assert "Pas de relevé" in sale["issue"] and sale["portfolio_value_cents"] is None

    first = send(c, "binance.csv", data, confirm=True).json()["result"]
    assert first["created"] == 1, "l'achat passe, la vente attend sa valeur"
    low = send(c, "binance.csv", data, values={sale["key"]: 10_000}).json()
    assert low["counts"]["to_complete"] == 1 and "inférieure" in low["events"][-1]["issue"]
    done = send(c, "binance.csv", data, confirm=True, values={sale["key"]: 500_000}).json()["result"]
    assert done["created"] == 1 and done["counts"]["duplicates"] == 1
    db.expire_all()
    row = db.scalars(select(CryptoTaxEvent).where(CryptoTaxEvent.kind == "cession")).one()
    assert row.portfolio_value_cents == 500_000 and row.day == date(2026, 5, 20)


def test_tracked_value_below_the_sale_price_is_not_trusted(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 3, 11), 1_000)
    body = send(c, "coinbase.csv", export("coinbase.csv")).json()
    sale = next(e for e in body["events"] if e["kind"] == "cession")
    assert sale["status"] == "to_complete" and "moins que le prix de vente" in sale["issue"]


def test_nothing_identifying_is_kept_anywhere(c: TestClient, db: Session) -> None:
    for name in ("binance.csv", "coinbase.csv"):
        out = send(c, name, export(name), confirm=True, values=None)
        assert out.status_code == 201
        text = out.text
        for secret in ("123456789", "Jeanne", "5f0c0000", "6650a1b2", "bc1qw508", "Withdraw fee"):
            assert secret not in text, secret
    db.expire_all()
    dump = repr([(e.note, e.import_key, e.asset) for e in db.scalars(select(CryptoTaxEvent))])
    dump += repr([a.details for a in db.scalars(select(AuditLog))])
    for secret in ("123456789", "Jeanne", "6650a1b2", "bc1qw508"):
        assert secret not in dump, secret


def test_import_never_touches_the_network(c: TestClient, app, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("appel réseau pendant un import")

    app.state.crypto_net = refuse
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    for name in ("kraken_ledgers.csv", "kraken_trades.csv", "binance.csv", "coinbase.csv"):
        assert send(c, name, export(name), confirm=True).status_code == 201


def test_api_refuses_what_it_cannot_read(c: TestClient) -> None:
    assert send(c, "x.csv", b"date,libelle,montant\n").status_code == 422
    big = send(c, "x.csv", b"x" * (cc.MAX_BYTES + 1))
    assert big.status_code == 413
    bad = c.post(
        "/api/crypto/import/preview",
        files={"file": ("b.csv", export("binance.csv"), "text/csv")},
        data={"values": "{pas du json"},
    )
    assert bad.status_code == 422
    for values in ({"court": 1}, {"a" * 64: -5}, {"a" * 64: True}, {"a" * 64: "12"}):
        assert send(c, "b.csv", export("binance.csv"), values=values).status_code == 422, values
    listed = c.post(
        "/api/crypto/import/preview",
        files={"file": ("b.csv", export("binance.csv"), "text/csv")},
        data={"values": "[1, 2]"},
    )
    assert listed.status_code == 422
    future = b"User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n1,2027-01-01 00:00:00,Spot,Buy,EUR,-5,\n"
    future += b"1,2027-01-01 00:00:00,Spot,Buy,BTC,0.0001,\n"
    out = send(c, "f.csv", future).json()
    assert out["counts"]["acquisitions"] == 0 and out["ignored"][0]["reason"].startswith("Date hors")


def test_import_requires_a_session(client: TestClient) -> None:
    r = client.post("/api/crypto/import/preview", files={"file": ("b.csv", export("binance.csv"), "text/csv")})
    assert r.status_code in (401, 403)


def test_kraken_trades_new_pair_spelling_and_other_fiat() -> None:
    data = (
        b"txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
        b"TA,OA,BTC/EUR,2026-04-01 10:00:00.1234,buy,market,60000,60,0.15,0.001,0,,\n"
        b"TB,OB,XXBTZUSD,2026-04-02 10:00:00,sell,market,65000,65,0.17,0.001,0,,\n"
        b"TC,OC,???,2026-04-03 10:00:00,sell,market,1,1,0,1,0,,\n"
    )
    r = cc.parse(data)
    assert [(m.kind, m.asset, m.amount_cents) for m in r.movements] == [("acquisition", "BTC", 6_000)]
    assert "USD" in r.ignored[0].reason and "illisible" in r.ignored[1].reason
    with pytest.raises(ImportFormatError, match="CSV illisible"):
        cc.parse(b'User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n1,"' + b"x" * 200_000 + b'"\n')


# ------------------------------------------------------------------ retours de relecture
BINANCE_HEAD = "User_ID,UTC_Time,Account,Operation,Coin,Change,Remark\n"


def test_french_spreadsheet_reads_its_comma_as_decimal() -> None:
    assert cc.number("0,006", comma=True) == Decimal("0.006") and cc.number("-1\u202f234,56", comma=True) == Decimal(
        "-1234.56"
    )
    assert cc.number("1.234,56") == cc.number("1,234.56", comma=True) == Decimal("1234.56")
    assert cc.number("0,006") is None and cc.number("12,34") is None, "sans contexte, une virgule n'est pas devinée"
    assert cc.number("1,234") == Decimal(1234) and cc.number("1,2,3") is None
    data = (
        "User_ID;UTC_Time;Account;Operation;Coin;Change;Remark\n"
        "1;2026-01-16 11:00:00;Spot;Transaction Spend;EUR;-300,00;\n"
        "1;2026-01-16 11:00:00;Spot;Transaction Buy;BTC;0,006;\n"
    )
    r = cc.parse(data.encode())
    assert [(m.kind, m.amount_cents, m.quantity) for m in r.movements] == [("acquisition", 30_000, Decimal("0.006"))]


def test_binance_fee_in_bnb_leaves_the_trade_readable() -> None:
    data = BINANCE_HEAD + (
        "1,2026-01-16 11:00:00,Spot,Transaction Spend,EUR,-300,\n"
        "1,2026-01-16 11:00:00,Spot,Transaction Buy,BTC,0.006,\n"
        "1,2026-01-16 11:00:00,Spot,Transaction Fee,BNB,-0.0005,\n"
        "1,2026-05-20 09:00:00,Spot,Transaction Sold,BTC,-0.006,\n"
        "1,2026-05-20 09:00:00,Spot,Transaction Revenue,EUR,300,\n"
        "1,2026-05-20 09:00:00,Spot,Transaction Fee,BNB,-0.0005,\n"
    )
    r = cc.parse(data.encode())
    assert [(m.kind, m.asset, m.amount_cents, m.quantity) for m in r.movements] == [
        ("acquisition", "BTC", 30_000, Decimal("0.006")),
        ("cession", "BTC", 30_000, Decimal("0.006")),
    ]
    assert [(i.line, i.reason) for i in r.ignored] == [(4, cc.CRYPTO_FEE), (7, cc.CRYPTO_FEE)]


def test_kraken_pair_ending_in_z_keeps_its_asset() -> None:
    assert cc._split_pair("XTZEUR") == ("XTZ", "EUR") and cc._split_pair("XTZUSD") == ("XTZ", "USD")
    assert cc._split_pair("XXBTZEUR") == ("BTC", "EUR") and cc._split_pair("XETHXXBT") == ("ETH", "BTC")
    assert cc._split_pair("USDTZUSD") == ("USDT", "USD") and cc._split_pair("BLZEUR") == ("BLZ", "EUR")
    data = (
        b"txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
        b"TZ,OZ,XTZEUR,2026-04-01 10:00:00,buy,market,1,10,0.02,10,0,,\n"
    )
    assert [m.asset for m in cc.parse(data).movements] == ["XTZ"]


def test_coinbase_older_report_still_reads_its_fees() -> None:
    data = (
        b"Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,Spot Price at Transaction,Subtotal,"
        b"Total (inclusive of fees),Fees,Notes\n"
        b"2026-04-03T10:00:00Z,Sell,BTC,0.001,EUR,60000,60,59.64,0.36,\n"
        b"2026-04-04T10:00:00Z,Buy,BTC,0.001,EUR,60000,,60.36,0.36,\n"
    )
    r = cc.parse(data)
    assert [(m.kind, m.amount_cents, m.fees_cents) for m in r.movements] == [
        ("cession", 6_000, 36),
        ("acquisition", 6_000, 36),  # sans sous-total : total − frais
    ]


def test_amounts_out_of_bounds_are_set_aside(c: TestClient, db: Session) -> None:
    huge = BINANCE_HEAD + "1,2026-01-16 11:00:00,Spot,Transaction Spend,EUR,-999999999999999,\n" * 100
    huge += "1,2026-01-16 11:00:00,Spot,Transaction Buy,BTC,1,\n"
    one = BINANCE_HEAD + "1,2026-01-17 11:00:00,Spot,Transaction Spend,EUR,-999999999999,\n"
    one += "1,2026-01-17 11:00:00,Spot,Transaction Buy,BTC,1,\n"
    for data in (huge, one):
        r = send(c, "b.csv", data.encode(), confirm=True)
        assert r.status_code == 201, r.text
        assert r.json()["result"]["created"] == 0
        assert r.json()["result"]["ignored"][-1]["reason"].startswith("Montant hors bornes")
    assert db.scalars(select(CryptoTaxEvent)).all() == []


def test_values_nested_without_end_are_refused(c: TestClient) -> None:
    for raw in ("[" * 100_000, "{" + '"a":[' * 100_000, "null"):
        r = c.post(
            "/api/crypto/import/preview",
            files={"file": ("b.csv", export("binance.csv"), "text/csv")},
            data={"values": raw},
        )
        assert r.status_code == 422 and "illisibles" in r.json()["detail"]


def test_same_export_imported_twice_at_once_answers_cleanly(
    c: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = export("kraken_ledgers.csv")
    assert send(c, "l.csv", data, confirm=True).status_code == 201
    created = len(db.scalars(select(CryptoTaxEvent)).all())
    monkeypatch.setattr(crypto_import, "_known", lambda *_: set())  # l'autre requête n'avait encore rien écrit
    r = send(c, "l.csv", data, confirm=True)
    assert r.status_code == 422 and "vient d'être importé" in r.json()["detail"]
    db.expire_all()
    assert len(db.scalars(select(CryptoTaxEvent)).all()) == created


# ------------------------------------------------------------------ suite de relecture (#26)
def manual_sale(c: TestClient, day: str = "2026-07-01", amount: int = 30_000) -> Any:
    r = c.post(
        "/api/crypto/tax",
        json={"day": day, "kind": "cession", "amount_cents": amount, "portfolio_value_cents": 2_000_000},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_sale_already_noted_by_hand_is_not_counted_twice(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 7, 1), 2_000_000)
    assert manual_sale(c)["tax"]["total_price_cents"] == 30_000
    data = export("kraken_ledgers.csv")
    body = send(c, "l.csv", data).json()
    sale = next(e for e in body["events"] if e["kind"] == "cession")
    assert sale["status"] == "manual_match" and "à la main" in sale["issue"]
    assert body["counts"]["manual_matches"] == 1 and body["counts"]["cessions"] == 0

    r = send(c, "l.csv", data, confirm=True).json()
    assert r["result"]["created"] == 2, "les deux achats, pas la vente"
    assert r["view"]["tax"]["total_price_cents"] == 30_000, "60 000 si la vente était recréée"
    db.expire_all()
    assert len(db.scalars(select(CryptoTaxEvent).where(CryptoTaxEvent.kind == "cession")).all()) == 1

    # Une autre vente, vraiment : elle n'entre que cochée.
    forced = c.post(
        "/api/crypto/import",
        files={"file": ("l.csv", data, "text/csv")},
        data={"include": json.dumps([sale["key"]])},
    ).json()
    assert forced["result"]["created"] == 1 and forced["view"]["tax"]["total_price_cents"] == 60_000


def test_two_identical_sales_match_one_manual_entry_only(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 5, 19), 1_000_000)
    manual_sale(c, "2026-05-20", 14_000)
    data = BINANCE_HEAD + (
        "1,2026-05-20 09:00:00,Spot,Transaction Sold,BTC,-0.002,\n"
        "1,2026-05-20 09:00:00,Spot,Transaction Revenue,EUR,140,\n"
        "1,2026-05-20 15:00:00,Spot,Transaction Sold,BTC,-0.002,\n"
        "1,2026-05-20 15:00:00,Spot,Transaction Revenue,EUR,140,\n"
    )
    body = send(c, "b.csv", data.encode()).json()
    assert [e["status"] for e in body["events"]] == ["manual_match", "new"]


def test_tracked_value_prefers_the_day_before_and_yours_wins(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 6, 30), 1_500_000)  # la veille : avant la vente
    snapshot(db, date(2026, 7, 1), 1_470_000)  # le soir de la vente : après
    data = export("kraken_ledgers.csv")
    sale = next(e for e in send(c, "l.csv", data).json()["events"] if e["kind"] == "cession")
    assert (sale["portfolio_value_cents"], sale["value_source"], sale["value_day"]) == (
        1_500_000,
        "suivi",
        "2026-06-30",
    )
    mine = send(c, "l.csv", data, confirm=True, values={sale["key"]: 4_000_000}).json()
    assert mine["result"]["created"] == 3
    db.expire_all()
    row = db.scalars(select(CryptoTaxEvent).where(CryptoTaxEvent.kind == "cession")).one()
    assert row.portfolio_value_cents == 4_000_000


def test_value_below_the_price_is_refused_not_replaced_by_the_tracked_one(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 6, 30), 1_500_000)
    data = export("kraken_ledgers.csv")
    key = next(e for e in send(c, "l.csv", data).json()["events"] if e["kind"] == "cession")["key"]
    sale = next(e for e in send(c, "l.csv", data, values={key: 100}).json()["events"] if e["kind"] == "cession")
    assert sale["status"] == "to_complete" and "au moins 300,00" in sale["issue"].replace("\u202f", " ")


def test_deleted_import_stays_deleted_until_you_take_it_back(c: TestClient, db: Session) -> None:
    snapshot(db, date(2026, 7, 1), 2_000_000)
    data = export("kraken_ledgers.csv")
    tax = send(c, "l.csv", data, confirm=True).json()["view"]["tax"]
    sale = next(e for e in tax["events"] if e["kind"] == "cession")
    assert c.delete(f"/api/crypto/tax/{sale['id']}").status_code == 204
    body = send(c, "l.csv", data).json()
    gone = next(e for e in body["events"] if e["kind"] == "cession")
    assert gone["status"] == "excluded" and body["counts"]["excluded"] == 1
    assert send(c, "l.csv", data, confirm=True).json()["result"]["created"] == 0

    back = c.post(
        "/api/crypto/import", files={"file": ("l.csv", data, "text/csv")}, data={"include": json.dumps([gone["key"]])}
    ).json()
    assert back["result"]["created"] == 1
    db.expire_all()
    assert db.scalars(select(CryptoImportExcluded)).all() == [], "reprise exprès : plus écartée"
    for bad in ("[1]", '["court"]', "{}", "[" * 1000):
        r = c.post("/api/crypto/import/preview", files={"file": ("l.csv", data, "text/csv")}, data={"include": bad})
        assert r.status_code == 422, bad


def test_binance_old_export_reads_transaction_related() -> None:
    r = cc.parse(export("binance_2021.csv"))
    assert [(m.day.isoformat(), m.kind, m.asset, m.amount_cents, m.fees_cents) for m in r.movements] == [
        ("2021-03-02", "acquisition", "BTC", 40_000, 0),
        ("2021-11-09", "cession", "BTC", 29_000, 29),
    ]
    reasons = [i.reason for i in r.ignored]
    assert reasons == [cc.TRANSFER, cc.CRYPTO_FEE] and cc.SWAP not in reasons
    assert "supprime" in cc.CRYPTO_FEE and "ajoute-les à la main au 2086" not in cc.CRYPTO_FEE


def test_offsets_currency_suffix_fiat_pairs_and_earn_allocations() -> None:
    assert cc.when("2026-12-31 23:30:00+02:00") == cc.when("2026-12-31 21:30:00")
    assert cc.when("2026-03-01 10:00:00-05:00") == cc.when("2026-03-01 15:00:00")
    assert cc.when("2026-03-01 10:00:00.1234") is not None and cc.when("2026-13-01 10:00:00") is None
    assert cc.number("110,00€", comma=True) == Decimal("110.00") and cc.number("-1,234.56 €") == Decimal("-1234.56")
    trades = (
        b"txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
        b"TU,OU,ZUSDZEUR,2026-04-01 10:00:00,buy,market,0.92,92,0.2,100,0,,\n"
    )
    assert cc.parse(trades).movements == [] and cc.parse(trades).ignored[0].reason == cc.FIAT_ONLY
    ledgers = (
        b"txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"
        b"L1,R1,2026-02-01 10:00:00,earn,allocation,currency,DOT,10,0,10\n"
        b"L2,R2,2026-02-02 10:00:00,earn,reward,currency,DOT,0.01,0,10.01\n"
    )
    assert [i.reason for i in cc.parse(ledgers).ignored] == [cc.INTERNAL, cc.REWARD]


def test_coinbase_2019_report_reads_its_currency_columns() -> None:
    data = (
        b"Timestamp,Transaction Type,Asset,Quantity Transacted,EUR Spot Price at Transaction,EUR Subtotal,"
        b"EUR Total (inclusive of fees),EUR Fees,Notes\n"
        b"2019-04-03T10:00:00Z,Buy,BTC,0.01,4500,45,46.99,1.99,Bought 0.01 BTC for 46.99 EUR\n"
        b"2019-06-03T10:00:00Z,Sell,BTC,0.005,7000,35,34.48,0.52,Sold 0.005 BTC for 34.48 EUR\n"
    )
    r = cc.parse(data)
    assert [(m.kind, m.amount_cents, m.fees_cents) for m in r.movements] == [
        ("acquisition", 4_500, 199),
        ("cession", 3_500, 52),
    ]
