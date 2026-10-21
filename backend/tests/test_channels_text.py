"""Texte des messageries : découpe sûre (propriétés), dialectes, identifiants retirés, secrets repérés."""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.channels.text import (
    LIMITS,
    scrub,
    secret_kind,
    split_message,
    to_discord,
    to_plain,
    to_slack,
    to_telegram_html,
    to_whatsapp,
    utf16_len,
)

_SUFFIX = re.compile(r"\s?\(\d+/\d+\)$")


def _unsuffix(chunk: str) -> str:
    return _SUFFIX.sub("", chunk)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=300),
            st.just("```python"),
            st.just("```"),
            st.just(""),
        ),
        max_size=60,
    ),
    st.integers(min_value=60, max_value=600),
)
def test_split_never_exceeds_the_limit_and_keeps_every_word(lines: list[str], limit: int) -> None:
    text = "\n".join(lines)
    chunks = split_message(text, limit)
    assert all(len(c) <= limit for c in chunks)
    original = [w for w in text.split() if not w.startswith("```")]
    rebuilt = [w for c in chunks for w in _unsuffix(c).split() if not w.startswith("```")]
    # Une ligne trop longue est coupée en morceaux : on retrouve les mêmes caractères, dans l'ordre.
    assert "".join(rebuilt) == "".join(original)


def test_split_closes_and_reopens_code_blocks_and_numbers_parts() -> None:
    code = "\n".join(f"ligne {i} " + "x" * 50 for i in range(60))
    chunks = split_message(f"Voici :\n```python\n{code}\n```\nfin", 600)
    assert len(chunks) > 2
    for i, chunk in enumerate(chunks, 1):
        assert chunk.endswith(f"({i}/{len(chunks)})")
        assert chunk.count("```") % 2 == 0, "chaque morceau a des blocs de code équilibrés"
    assert chunks[1].startswith("```python")
    assert chunks[0].endswith(f"```\n(1/{len(chunks)})"), "le numéro ne colle pas à la fermeture du bloc"
    assert split_message("court", 100) == ["court"]
    assert split_message("   ", 100) == []


def test_split_counts_utf16_units_for_telegram() -> None:
    text = "💶" * 3000  # 6000 unités UTF-16, 3000 caractères
    chunks = split_message(text, LIMITS["telegram"], utf16_len)
    assert len(chunks) >= 2
    assert all(utf16_len(c) <= LIMITS["telegram"] for c in chunks)


def test_dialects() -> None:
    md = "# Ton budget\n**49 €** par jour <ok> & *bien*\n- un\n[Bank](https://bank.example/#/x) `a<b`"
    html = to_telegram_html(md)
    assert "<b>Ton budget</b>" in html and "<b>49 €</b>" in html and "<i>bien</i>" in html
    assert "&lt;ok&gt; &amp;" in html and "<code>a&lt;b</code>" in html
    assert '<a href="https://bank.example/#/x">Bank</a>' in html and "• un" in html
    assert to_telegram_html("[x](javascript:alert(1))") == "[x](javascript:alert(1))", "seuls les liens http(s)"
    slack = to_slack(md)
    assert "*Ton budget*" in slack and "*49 €*" in slack and "_bien_" in slack and "&lt;ok&gt; &amp;" in slack
    assert "<https://bank.example/#/x|Bank>" in slack and "`a<b`" in slack, "le code n'est pas échappé"
    wa = to_whatsapp(md)
    assert "*Ton budget*" in wa and "*49 €*" in wa and "Bank (https://bank.example/#/x)" in wa
    assert to_plain(md).startswith("Ton budget\n49 € par jour") and "`" not in to_plain(md)
    assert "**Ton budget**" in to_discord(md) and "(<https://bank.example/#/x>)" in to_discord(md)
    assert to_plain("```\nbloc\n```") == "```\nbloc\n```"


def test_scrub_removes_identifiers_but_keeps_amounts_and_dates() -> None:
    text = (
        "IBAN FR76 3000 6000 0112 3456 7890 189, carte 4111 1111 1111 1111, allocataire 1234567, "
        "NIR 1 85 05 78 006 084 36, 1 234,56 € le 21/10/2026, 150000 €, tel 09 69 32 52 52, a@b.fr"
    )
    out = scrub(text, privacy="redacted")
    for secret in ("FR76", "4111", "1234567", "006 084", "09 69", "a@b.fr"):
        assert secret not in out
    for kept in ("1 234,56 €", "21/10/2026", "150000 €"):
        assert kept in out
    assert "[IBAN]" in out and "[CARTE]" in out and "[NUMÉRO]" in out and "[N° SÉCU]" in out
    full = scrub("Appelle le 09 69 32 52 52 ou écris à contact@caf.fr — carte 4111 1111 1111 1111", privacy="full")
    assert "09 69 32 52 52" in full and "contact@caf.fr" in full, "en mode complet : contacts d'organismes gardés"
    assert "[CARTE]" in full, "une carte n'a jamais sa place dans une messagerie"
    assert "4970 1012 3456 7890" in scrub("ref 4970 1012 3456 7890", privacy="redacted"), "pas un numéro de carte"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mon mot de passe est Soleil2024!", "un mot de passe ou un code"),
        ("mdp: azerty123", "un mot de passe ou un code"),
        ("pin : 1234", "un mot de passe ou un code"),
        ("ma carte 4111 1111 1111 1111 exp 12/27", "un numéro de carte"),
        ("mon iban fr76 3000 6000 0112 3456 7890 189", "un IBAN"),
        ("j'ai oublié mon mot de passe de la CAF, que faire ?", None),
        ("le pass navigo: 86,40 € par mois", None),
        ("mon mot de passe est compliqué", None),
        ("combien pour 1 234,56 € ?", None),
    ],
)
def test_secret_kind(text: str, kind: str | None) -> None:
    assert secret_kind(text) == kind
