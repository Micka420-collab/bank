from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import AmountError, cents_to_decimal, decimal_to_cents, format_eur, parse_amount


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("12,50", 1250),
        ("-12,50", -1250),
        ("1 234,56", 123456),
        ("1 234,56 €", 123456),
        ("1 234,56", 123456),
        ("1.234,56", 123456),
        ("1,234.56", 123456),
        ("-1.234,56", -123456),
        ("12,50-", -1250),
        ("(12.50)", -1250),
        ("+3", 300),
        ("−7,10 €", -710),
        ("1.234", 123400),  # séparateur de milliers
        ("1,234,567", 123456700),
        ("12,5", 1250),
        (".5", 50),
        ("0", 0),
        ("2 150,00 EUR", 215000),
        ("12,345", 1234500),  # 3 chiffres => milliers, jamais 3 décimales en euros
        ("0,005", 500),
        ("1.234.567,89", 123456789),
    ],
)
def test_parse_amount(text: str, cents: int) -> None:
    assert parse_amount(text) == cents


@pytest.mark.parametrize("text", ["", "   ", "abc", "12,50,3", "1.234.56", "--5", "€", "1e5", "12;50", "1,2,3"])
def test_parse_amount_rejects(text: str) -> None:
    with pytest.raises(AmountError):
        parse_amount(text)


@given(st.integers(min_value=-(10**11), max_value=10**11))
def test_roundtrip_french_format(cents: int) -> None:
    assert parse_amount(format_eur(cents)) == cents


@given(st.integers(min_value=-(10**11), max_value=10**11))
def test_roundtrip_dot_decimal(cents: int) -> None:
    sign = "-" if cents < 0 else ""
    e, c = divmod(abs(cents), 100)
    assert parse_amount(f"{sign}{e}.{c:02d}") == cents
    assert parse_amount(f"{sign}{e:,}.{c:02d}") == cents  # milliers à l'anglaise


@given(st.decimals(min_value=Decimal("-1e9"), max_value=Decimal("1e9"), places=2, allow_nan=False))
def test_decimal_roundtrip(value: Decimal) -> None:
    assert cents_to_decimal(decimal_to_cents(value)) == value


def test_decimal_to_cents_rejects() -> None:
    with pytest.raises(AmountError):
        decimal_to_cents("NaN")
    with pytest.raises(AmountError):
        decimal_to_cents("pas un nombre")


def test_bankers_rounding() -> None:
    assert decimal_to_cents("0.125") == 12
    assert decimal_to_cents("0.135") == 14


def test_format_eur() -> None:
    assert format_eur(-123456) == "-1 234,56 €"
    assert format_eur(5) == "0,05 €"
