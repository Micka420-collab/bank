import pytest

from app.domain.categorize import (
    BUILTIN_CATEGORIES,
    BUILTIN_RULES,
    LEGACY_COLORS,
    TONES,
    Categorizer,
    Rule,
    TokenBayes,
    builtin_category,
    category_tone,
)
from app.domain.normalize import merchant_key, searchable


@pytest.mark.parametrize(
    ("raw", "merchant"),
    [
        ("PAIEMENT PAR CARTE X1234 LECLERC ORLEANS 15/10", "LECLERC ORLEANS"),
        ("CB LECLERC 0510 ORLEANS", "LECLERC ORLEANS"),
        ("FACTURE CARTE DU 151024 LECLERC ORLEANS CARTE 4974XXXXXXXX1234", "LECLERC ORLEANS"),
        ("CARTE 15/10/24 LECLERC CB*1234", "LECLERC"),
        ("PRLV SEPA FREE MOBILE ECH/151024 ID EMETTEUR/FR25ZZZ123456 MDT/XYZ", "FREE MOBILE"),
        ("VIR SEPA RECU /DE CAF DU LOIRET /MOTIF PRIME ACTIVITE", "CAF LOIRET"),
        ("VIR CAF DU LOIRET PRIME ACTIVITE 092026", "CAF LOIRET PRIME"),
        ("PAYPAL EUROPE S.A.R.L. ET CIE S.C.A *NETFLIX", "NETFLIX"),
        ("CB SUPER U 12/10 SARAN", "SUPER U SARAN"),
        ("Leclerc", "LECLERC"),
        ("Café de la Gare", "CAFE GARE"),
        ("", ""),
        ("CHEQUE 1234567", ""),
    ],
)
def test_merchant_key(raw: str, merchant: str) -> None:
    assert merchant_key(raw) == merchant


def test_searchable_strips_accents_and_spaces() -> None:
    assert searchable("  Prélèvement   Échéance ") == "PRELEVEMENT ECHEANCE"


@pytest.mark.parametrize(
    ("label", "amount", "expected"),
    [
        ("CB CARREFOUR MARKET 07/09 ORLEANS", -4520, "courses"),
        ("PRLV SEPA FREE MOBILE", -1999, "telecom"),
        ("PRLV SEPA EDF CLIENTS PARTICULIERS", -6200, "energie"),
        ("PRLV SEPA FONCIA LOYER", -65000, "logement"),
        ("VIR CAF DU LOIRET", 25829, "aides"),
        ("VIR SEPA RECU /DE BRGM /MOTIF SALAIRE SEPTEMBRE", 215000, "salaire"),
        ("CARTE UBER *EATS", -2390, "restaurants"),
        ("UBER TRIP", -1200, "transport"),
        ("PRLV SEPA NETFLIX.COM", -1349, "abonnements"),
        ("CB CAFE DE LA GARE", -380, "restaurants"),  # CAFE ne doit pas être pris pour la CAF
        ("CB BPCE FRAIS", -500, "frais_bancaires"),  # BP ne matche pas BPCE
        ("RETRAIT DAB 12/10 ORLEANS", -4000, "retraits"),
        ("VIR VERS LIVRET A", -30000, "epargne"),
        ("CB PIZZERIA DA MARIO", -3100, "restaurants"),
        ("VIR CPAM DU LOIRET", 1890, "remboursements"),
        ("BUTAGAZ LIVRAISON", -9000, None),  # BUT (magasin) ne matche pas BUTAGAZ
        ("OPERATION MYSTERE", -100, None),
    ],
)
def test_builtin_category(label: str, amount: int, expected: str | None) -> None:
    assert builtin_category(label, amount) == expected


def test_direction_matters() -> None:
    assert builtin_category("VIR SALAIRE", 100000) == "salaire"
    assert builtin_category("VIR SALAIRE", -100000) != "salaire"


def test_rules_reference_existing_categories() -> None:
    slugs = {c.slug for c in BUILTIN_CATEGORIES}
    assert all(r.category in slugs for r in BUILTIN_RULES)
    assert len(slugs) == len(BUILTIN_CATEGORIES)


def _bayes() -> TokenBayes:
    samples = [("PRLV SEPA PAYPAL SPOTIFY", "abonnements")] * 15 + [("CB BOULANGERIE MARIE", "courses")] * 15
    samples += [("VIR PERSO TONTINE AMIS", "cadeaux_dons")] * 5
    return TokenBayes(min_samples=20).fit(samples)


def test_bayes_learns_user_habits() -> None:
    model = _bayes()
    assert model.ready
    cat, conf = model.predict("PRLV PAYPAL SPOTIFY P2B") or ("", 0.0)
    assert cat == "abonnements" and conf > 0.9
    assert model.predict("TOTALLY UNKNOWN WORDS") is None
    assert TokenBayes().predict("x") is None  # pas assez d'exemples


def test_categorizer_precedence() -> None:
    rules = [
        Rule("AMAZON", "contains", "out", "maison", "user"),
        Rule("LECLERC ORLEANS", "merchant", "out", "enfants", "learned"),
    ]
    c = Categorizer(rules, _bayes())
    assert c.decide("CB AMAZON MKTP", "AMAZON MKTP", -1000).source == "user_rule"
    assert c.decide("CB AMAZON MKTP", "AMAZON MKTP", -1000).category == "maison"
    d = c.decide("CB LECLERC 0510 ORLEANS", "LECLERC ORLEANS", -5000)
    assert (d.category, d.source) == ("enfants", "learned")
    d = c.decide("CB CARREFOUR", "CARREFOUR", -100)
    assert (d.category, d.source) == ("courses", "builtin")
    d = c.decide("PRLV PAYPAL SPOTIFY", "SPOTIFY", -999)
    assert d.category == "abonnements"
    assert c.decide("RIEN DE CONNU", "RIEN CONNU", -100).category == "a_categoriser"
    # Une règle « entrées » ne s'applique pas à une sortie
    c2 = Categorizer([Rule("REMBOURSEMENT", "contains", "in", "remboursements", "user")])
    assert c2.decide("REMBOURSEMENT X", "X", -100).source != "user_rule"


def test_builtin_colors_are_lagon_tones_and_only_defaults_follow_the_theme() -> None:
    old_violets = {"#7c3aed", "#9333ea", "#a855f7", "#6d28d9", "#c026d3", "#be185d", "#9f1239", "#d946ef"}
    assert all(c.tone in TONES and c.color == TONES[c.tone] for c in BUILTIN_CATEGORIES)
    assert not {c.color for c in BUILTIN_CATEGORIES} & old_violets
    assert set(LEGACY_COLORS) == {c.slug for c in BUILTIN_CATEGORIES}
    assert category_tone("logement", TONES["c1"].upper()) == "c1"
    assert category_tone("logement", "#123456") is None  # couleur choisie : elle fait foi
    assert category_tone("ma_categorie", TONES["c1"]) is None
