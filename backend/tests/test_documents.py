"""Documents officiels : on garde les chiffres utiles, on jette les identifiants."""

from pathlib import Path

import pytest

from app.domain.documents import MAX_PAGES, DocumentError, detect_kind, extract_text, parse_document
from tests.pdfgen import make_pdf

AVIS = [
    "DIRECTION GÉNÉRALE DES FINANCES PUBLIQUES",
    "AVIS D'IMPÔT 2026",
    "Impôt sur les revenus 2025",
    "Numéro fiscal : 12 34 567 891 234",
    "M. MICKA DUPONT",
    "12 RUE DES LILAS",
    "45000 ORLEANS",
    "Déclarant 1",
    "Revenu brut global : 25 800 €",
    "Revenu net imposable : 23 220 €",
    "Revenu fiscal de référence : 23 456 €",
    "Nombre de parts : 1,00",
    "Montant de l'impôt net : 1 234 €",
    "Taux de prélèvement à la source personnalisé : 5,6 %",
]

CAF = [
    "Caisse d'allocations familiales du Loiret",
    "ATTESTATION DE PAIEMENT",
    "Allocataire n° 1234567",
    "Droits au 01/09/2026",
    "Aide personnalisée au logement : 212,00 €",
    "Prime d'activité : 258,29 €",
    "Allocations familiales : 0,00 €",
    "Quotient familial : 1 045 €",
    "1 enfant à charge",
]

FT = [
    "FRANCE TRAVAIL",
    "ATTESTATION DE SITUATION",
    "Identifiant France Travail : 1234567A",
    "Allocation d'aide au retour à l'emploi (ARE)",
    "Montant journalier net : 38,45 €",
    "Fin de droits : 15/03/2027",
    "Jours restants : 145 jours",
]


def test_extracts_text_and_rejects_empty_or_broken_pdfs() -> None:
    assert "FINANCES PUBLIQUES" in extract_text(make_pdf(AVIS))
    with pytest.raises(DocumentError, match="illisible"):
        extract_text(b"%PDF-1.4 garbage")
    with pytest.raises(DocumentError, match="Aucun texte"):
        extract_text(make_pdf(["x"]))
    # Bornes : seules les premières pages sont lues.
    big = make_pdf(["Revenu fiscal de référence : 1 €"] * 2, pages=MAX_PAGES + 5)
    assert extract_text(big).count("FISCAL") <= 2 * MAX_PAGES


def test_detect_kind() -> None:
    assert detect_kind("\n".join(AVIS)) == "avis_impot"
    assert detect_kind("\n".join(CAF)) == "caf"
    assert detect_kind("\n".join(FT)) == "france_travail"
    assert detect_kind("Relevé de compte Crédit Agricole octobre 2026 solde 640 €") == "inconnu"


def test_avis_impot_keeps_figures_and_discards_identifiers() -> None:
    d = parse_document(make_pdf(AVIS))
    assert d.kind == "avis_impot" and d.title == "Avis d'impôt sur les revenus 2025"
    f = d.fields
    assert f["incomes_year"] == 2025 and f["rfr_cents"] == 2_345_600 and f["taxable_income_cents"] == 2_322_000
    assert f["parts"] == "1.00" and f["income_tax_cents"] == 123_400 and f["withholding_rate_percent"] == "5.6"
    assert f["declarants"] == 1 and d.warnings == []
    assert set(d.discarded) == {"numéro fiscal", "adresse postale"}
    blob = repr(d.fields)
    assert "12 34 567" not in blob and "LILAS" not in blob and "DUPONT" not in blob


def test_avis_with_missing_pieces_warns_instead_of_guessing() -> None:
    d = parse_document(make_pdf(["AVIS D'IMPÔT 2026", "Revenu fiscal de référence : 9 999 €", "Montant net : 0 €"]))
    assert d.fields["incomes_year"] == 2025 and d.fields["parts"] is None
    assert any("parts" in w for w in d.warnings) and any("déduite" in w for w in d.warnings)
    d2 = parse_document(make_pdf(["Impôt sur le revenu", "Avis", "rien de chiffré ici du tout vraiment"]))
    assert d2.fields["rfr_cents"] is None and any("introuvable" in w for w in d2.warnings)


def test_caf_attestation() -> None:
    d = parse_document(make_pdf(CAF))
    assert d.kind == "caf"
    by = {p["code"]: p["monthly_cents"] for p in d.fields["prestations"]}
    assert by == {"aide_logement": 21_200, "prime_activite": 25_829}  # les 0,00 € ne comptent pas
    assert d.fields["month"] == "2026-09" and d.fields["quotient_familial_cents"] == 104_500
    assert d.fields["children"] == 1 and "numéro d'allocataire" in d.discarded
    empty = parse_document(
        make_pdf(["Caisse d'allocations familiales", "Attestation de droits", "sans montant lisible"])
    )
    assert empty.fields["prestations"] == [] and empty.warnings


def test_france_travail_attestation() -> None:
    d = parse_document(make_pdf(FT))
    assert d.kind == "france_travail"
    f = d.fields
    assert f["daily_allowance_cents"] == 3_845 and f["monthly_estimate_cents"] == 116_888
    assert f["end_of_rights"] == "2027-03-15" and f["days_left"] == 145 and f["allowance"] == "ARE"
    assert "identifiant France Travail" in d.discarded


def test_unknown_document() -> None:
    d = parse_document(
        make_pdf(
            [
                "Relevé de compte Crédit Agricole",
                "Solde au 30/09/2026 : 640,00 €",
                "IBAN FR76 3000 6000 0112 3456 7890 189",
            ]
        )
    )
    assert d.kind == "inconnu" and d.fields == {} and "IBAN" in d.discarded


# ------------------------------------------------------------------ précision du lecteur (revue de sécurité)
def test_amounts_without_thousands_separator_are_not_truncated() -> None:
    d = parse_document(
        make_pdf(["AVIS D'IMPÔT 2026", "Impôt sur les revenus 2025", "Revenu fiscal de référence : 23456 €"])
    )
    assert d.fields["rfr_cents"] == 2_345_600
    d2 = parse_document(
        make_pdf(["AVIS D'IMPÔT 2026", "Impôt sur les revenus 2025", "Revenu fiscal de référence : 1234,56"])
    )
    assert d2.fields["rfr_cents"] == 123_456


def test_allowance_detection_needs_a_whole_word() -> None:
    base = ["FRANCE TRAVAIL", "ATTESTATION DE SITUATION", "Montant journalier net : 38,45 €"]
    assert parse_document(make_pdf([*base, "Allocation versée : (ARE)"])).fields["allowance"] == "ARE"
    assert parse_document(make_pdf([*base, "Je déclare que la carte est à jour"])).fields["allowance"] is None
    assert parse_document(make_pdf([*base, "Allocation de solidarité spécifique"])).fields["allowance"] == "ASS"


def test_sources_contain_no_control_characters() -> None:
    """Un « \\b » devenu un vrai caractère d'effacement rend une expression régulière silencieusement fausse."""
    root = Path(__file__).resolve().parent.parent / "app"
    for path in root.rglob("*.py"):
        raw = path.read_bytes()
        bad = {b for b in raw if b < 32 and b not in (9, 10, 13)}
        assert not bad, f"{path}: caractères de contrôle {sorted(bad)}"
