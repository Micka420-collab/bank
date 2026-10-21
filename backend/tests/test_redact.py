import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.redact import PERSON_TOKEN, is_person_transfer, redact_label, redact_merchant


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "must_contain"),
    [
        ("VIR INST A M JEAN DUPONT", ["JEAN", "DUPONT"], [PERSON_TOKEN]),
        ("VIR SEPA DE MME CLAIRE MARTIN-DURAND LOYER", ["CLAIRE", "MARTIN"], [PERSON_TOKEN, "LOYER"]),
        ("VIR M ET MME LEROY CADEAU", ["LEROY"], [PERSON_TOKEN, "CADEAU"]),
        ("PAYLIB DE MARIE MARTIN", ["MARIE", "MARTIN"], ["PAYLIB DE", PERSON_TOKEN]),
        ("LYDIA PAUL", ["PAUL"], ["LYDIA", PERSON_TOKEN]),
        ("VIR FR76 3000 6000 0112 3456 7890 189 LOYER", ["3000 6000"], ["[IBAN]", "LOYER"]),
        ("VIR FR7630006000011234567890189", ["30006000"], ["[IBAN]"]),
        ("REMB jean.dupont@exemple.fr", ["DUPONT@"], ["[EMAIL]"]),
        ("RAPPEL 06 12 34 56 78", ["12 34"], ["[TEL]"]),
        ("RAPPEL +33 6 12 34 56 78", ["12 34 56"], ["[TEL]"]),
        ("CB CARREFOUR 21/10 CARTE X1234 REF 99887766", ["99887766"], ["CARREFOUR", "[NUM]"]),
        # Virement sans civilité : la contrepartie est un particulier jusqu'à preuve du contraire.
        ("VIR SEPA RECU /DE JEAN DUPONT /MOTIF LOYER", ["JEAN", "DUPONT"], [PERSON_TOKEN, "LOYER"]),
        ("VIR INST LUCAS BERNARD", ["LUCAS", "BERNARD"], [PERSON_TOKEN]),
        ("VIREMENT DE CLAIRE MARTIN PENSION", ["CLAIRE", "MARTIN"], [PERSON_TOKEN, "PENSION"]),
        ("VIR FR76-3000-6000-0112-3456-7890-189", ["3000-6000"], ["[IBAN]"]),
        ("VIR FR76.3000.6000.0112.3456.7890.189", ["3000.6000"], ["[IBAN]"]),
    ],
)
def test_redaction_masks_personal_data(raw: str, must_not_contain: list[str], must_contain: list[str]) -> None:
    out = redact_label(raw)
    for s in must_not_contain:
        assert s not in out, out
    for s in must_contain:
        assert s in out, out


@pytest.mark.parametrize(
    "label",
    [
        "VIR SEPA RECU /DE BRGM /MOTIF SALAIRE",
        "VIR CAF DU LOIRET PRIME ACTIVITE",
        "VIR VERS LIVRET A",
        "VIR DGFIP FINANCES PUBLIQUES REMBOURSEMENT",
        "VIR SEPA RECU /DE FRANCE TRAVAIL /MOTIF ARE",
        "VIR DE MUTUELLE GENERALE REMBT SOINS",
        "VIR SEPA EMIS VERS ORANGE SA FACTURE",
    ],
)
def test_organisations_in_transfers_are_kept(label: str) -> None:
    assert PERSON_TOKEN not in redact_label(label)
    assert not is_person_transfer(label)


def test_merchants_and_decision_data_survive() -> None:
    assert redact_label("PRLV SEPA EDF CLIENTS PARTICULIERS") == "PRLV SEPA EDF CLIENTS PARTICULIERS"
    assert redact_label("CB UBER *EATS 12/10") == "CB UBER *EATS 12/10"
    assert "NETFLIX" in redact_label("PRLV SEPA NETFLIX.COM")


def test_person_transfer_detection_drives_merchant_masking() -> None:
    assert is_person_transfer("VIR INST A M JEAN DUPONT")
    assert is_person_transfer("WERO VERS LUCAS")
    assert not is_person_transfer("PRLV SEPA FONCIA LOYER")
    assert is_person_transfer("VIR SEPA RECU /DE JEAN DUPONT /MOTIF LOYER")
    assert redact_merchant("JEAN DUPONT", "VIR SEPA RECU /DE JEAN DUPONT /MOTIF LOYER") == PERSON_TOKEN
    assert redact_merchant("JEAN DUPONT", "VIR INST A M JEAN DUPONT") == PERSON_TOKEN
    assert redact_merchant("FONCIA", "PRLV SEPA FONCIA LOYER") == "FONCIA"


@given(st.text(alphabet="0123456789", min_size=6, max_size=30))
def test_no_long_number_survives(digits: str) -> None:
    out = redact_label(f"REF {digits} FIN")
    assert digits not in out


@given(st.text(max_size=200))
def test_redaction_is_total_and_idempotent(raw: str) -> None:
    once = redact_label(raw)
    assert redact_label(once) == once


@settings(max_examples=400)
@given(st.lists(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=2, max_size=10), min_size=2, max_size=3))
def test_any_two_word_transfer_counterparty_is_masked(words: list[str]) -> None:
    """Échec du côté sûr : quel que soit le nom, un virement vers « PRÉNOM NOM » ne le laisse pas passer."""
    from app.domain.normalize import _STOP
    from app.domain.redact import _NOT_A_NAME, _ORG_HINT

    # Mots qui ont un sens bancaire (préfixes, marqueurs de fin) : ce ne sont pas des noms.
    banking = {
        "VIR",
        "SEPA",
        "RECU",
        "DE",
        "MOTIF",
        "CADEAU",
        "ID",
        "REF",
        "RUM",
        "ICS",
        "LIB",
        "NUM",
        "DATE",
        "MDT",
        "ECH",
        "REFERENCE",
        "EMETTEUR",
        "CARTE",
        "CB",
        "PRLV",
        "ACHAT",
        "RETRAIT",
        "CHEQUE",
        "CHQ",
        "VIREMENT",
        "PRELEVEMENT",
        "PAIEMENT",
        "FACTURE",
        "AVOIR",
        "REMISE",
        "DAB",
        "GAB",
        "EMIS",
        "PERMANENT",
        "INST",
        "INSTANTANE",
        "DU",
        "POUR",
        "LYDIA",
        "PAYLIB",
        "WERO",
        "PAYPAL",
        "SUMUP",
        "ZETTLE",
        "IZETTLE",
        "STRIPE",
        "SQ",
    }
    name = " ".join(words)
    if _ORG_HINT.search(name) or any(w in _NOT_A_NAME or w in _STOP or w in banking for w in words):
        return
    out = redact_label(f"VIR SEPA RECU /DE {name} /MOTIF CADEAU")
    assert all(f" {w} " not in f" {out} " for w in words), out
