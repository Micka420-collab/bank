from datetime import date

import pytest

from app.importers.base import ImportFormatError, ParsedTransaction, external_ref, fingerprints
from app.importers.csv_import import map_header, parse_csv, parse_date
from app.importers.ofx_import import looks_like_ofx, parse_ofx
from tests.conftest import fixture_bytes


def test_credit_agricole_style_export() -> None:
    r = parse_csv(fixture_bytes("ca_export.csv"))
    assert r.detected["encoding"] == "cp1252" and r.detected["delimiter"] == ";"
    assert r.detected["header_line"] == "9"  # 8 lignes de préambule ignorées
    amounts = [t.amount_cents for t in r.transactions]
    assert amounts[:3] == [-65000, 25829, 3188]
    salary = next(t for t in r.transactions if "SALAIRE" in t.label)
    assert salary.amount_cents == 215000 and salary.booking_date == date(2026, 9, 28)
    assert len(r.transactions) == 9
    reasons = {s.reason for s in r.skipped}
    assert reasons == {"date illisible", "montant manquant"}


def test_boursorama_style_export() -> None:
    r = parse_csv(fixture_bytes("boursorama.csv"))
    assert [t.amount_cents for t in r.transactions] == [-2390, -1349, 5000]
    assert r.transactions[0].value_date == date(2026, 10, 1)
    assert "UBER" in r.transactions[0].label


def test_revolut_style_export() -> None:
    r = parse_csv(fixture_bytes("revolut.csv"))
    assert r.detected["delimiter"] == ","
    assert [t.amount_cents for t in r.transactions] == [-8235, 100000, -3100]
    assert r.transactions[0].booking_date == date(2026, 10, 4)  # date de début, heure ignorée
    assert r.transactions[2].label == "Pizzeria Da Mario, Orleans"


def test_headerless_inference() -> None:
    r = parse_csv(fixture_bytes("headerless.csv"))
    assert r.detected["header_line"] == "0"
    assert [t.amount_cents for t in r.transactions] == [-4520, -6200, 1890]
    assert r.transactions[1].label.startswith("PRLV SEPA EDF")


def test_debit_credit_headerless() -> None:
    data = b"01/10/2026;CB X;12,00;\n02/10/2026;VIR Y;;30,00\n03/10/2026;CB Z;5,50;\n"
    r = parse_csv(data)
    assert [t.amount_cents for t in r.transactions] == [-1200, 3000, -550]


@pytest.mark.parametrize("data", [b"", b"   \n\n", b"a;b;c\nd;e;f\n", b"Date;Libelle;Montant\nxx;yy;zz\n"])
def test_csv_errors(data: bytes) -> None:
    with pytest.raises(ImportFormatError):
        parse_csv(data)


def test_map_header_prefers_exact_then_prefix() -> None:
    m = map_header(["Date", "Date de valeur", "Libellé", "Montant (EUR)", "Solde"])
    assert m == {"date": 0, "value_date": 1, "label": 2, "amount": 3, "balance": 4}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15/10/2026", date(2026, 10, 15)),
        ("15/10/26", date(2026, 10, 15)),
        ("2026-10-15T08:00:00", date(2026, 10, 15)),
        ("15.10.2026", date(2026, 10, 15)),
        ("31/02/2026", None),
        ("", None),
        ("01/01/1850", None),
    ],
)
def test_parse_date(raw: str, expected: date | None) -> None:
    assert parse_date(raw) == expected


def test_ofx_v1_sgml() -> None:
    data = fixture_bytes("sample_v1.ofx")
    assert looks_like_ofx(data)
    r = parse_ofx(data)
    assert r.account_hint == "00012345678"
    assert r.balance_cents == 123456 and r.balance_date == date(2026, 10, 20)
    assert [(t.amount_cents, t.external_id) for t in r.transactions] == [
        (-65000, "FIT0001"),
        (25829, "FIT0002"),
        (-1999, "FIT0003"),
    ]
    assert r.transactions[0].label == "PRLV SEPA FONCIA LOYER OCTOBRE"
    assert r.transactions[1].booking_date == date(2026, 10, 5)
    assert r.skipped[0].reason == "DTPOSTED illisible"


def test_ofx_v2_xml_is_not_vulnerable_to_xxe() -> None:
    r = parse_ofx(fixture_bytes("sample_v2.ofx"))
    assert r.detected["version"] == "2.x"
    assert r.transactions[0].label == "&xxe;"  # entité jamais résolue : aucun parseur XML n'est impliqué
    assert "root:" not in r.transactions[0].label
    assert r.transactions[1].label == "VIR AMI REMBOURSEMENT RESTO"
    assert r.balance_cents == 50000


def test_ofx_errors() -> None:
    with pytest.raises(ImportFormatError):
        parse_ofx(b"pas de l'ofx")
    with pytest.raises(ImportFormatError):
        parse_ofx(b"<OFX><BANKTRANLIST></BANKTRANLIST></OFX>")


def test_fingerprints_are_stable_and_rank_duplicates() -> None:
    coffee = ParsedTransaction(date(2026, 10, 1), -250, "CB CAFE")
    other = ParsedTransaction(date(2026, 10, 1), -900, "CB RESTO")
    a = fingerprints(1, [coffee, coffee, other])
    b = fingerprints(1, [coffee, other, coffee])
    assert len(set(a)) == 3  # deux cafés identiques = deux opérations
    assert set(a) == set(b)  # l'ordre ne change pas l'ensemble
    assert fingerprints(2, [coffee])[0] != a[0]  # lié au compte
    assert external_ref("file", "X1") != external_ref("enablebanking", "X1")
