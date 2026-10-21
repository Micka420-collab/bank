"""Coffre-fort de documents : lecture confinée (images nettoyées, PDF, scans, OCR), types et valeurs avec preuves,
pièces sensibles, corrections, chiffres officiels, lecture assistée sans invention, export, effacement."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from PIL import Image
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, select
from starlette.testclient import TestClient

from app.db import SchemaUpgradeError, upgrade_schema, utcnow
from app.domain import reading
from app.domain import vault as dv
from app.models import AuditLog, OfficialFact, VaultBlob, VaultDocument
from app.services import vault
from tests.conftest import PASSWORD
from tests.llm import FakeLlm, answer, configure
from tests.pdfgen import make_pdf
from tests.test_documents import AVIS, CAF, FT
from tests.vaultgen import PAYSLIP, gps_exif, scanned_pdf, text_image

needs_ocr = pytest.mark.skipif(not reading.ocr_available(), reason="Tesseract absent : OCR non testable ici")
PAYSLIP_TEXT = "\n".join(PAYSLIP)
INVOICE = [
    "EDF - Électricité de France",
    "FACTURE du 12/10/2026",
    "Consommation : 1 234 kWh",
    "Abonnement 12,44 €",
    "Montant TTC à payer : 145,90 €",
    "À payer avant le 02/11/2026",
    "Contact : client@edf.example 09 69 32 15 15",
]
RIB = [
    "RELEVÉ D'IDENTITÉ BANCAIRE",
    "Banque : Crédit Mutuel",
    "Titulaire du compte : M. MICKA DUPONT",
    "IBAN : FR76 3000 6000 0112 3456 7890 189",
    "BIC : CMCIFR2A",
]
IDENTITY = [
    "RÉPUBLIQUE FRANÇAISE",
    "CARTE NATIONALE D'IDENTITÉ",
    "Nom : DUPONT",
    "N° : X4RTBPFW4",
    "Date d'expiration : 14/02/2031",
    "IDFRADUPONT<<<<<<<<<<<<<<<<<<<<<<<<<<<<<",
]


# ============================================================================ domaine : types et valeurs
@pytest.mark.parametrize(
    ("lines", "kind"),
    [
        (PAYSLIP, "payslip"),
        (AVIS, "tax_notice"),
        (CAF, "caf"),
        (FT, "france_travail"),
        (INVOICE, "invoice"),
        (
            ["QUITTANCE DE LOYER", "Loyer de septembre 2026", "Total : 650,00 €", "Bailleur : SCI Les Tilleuls"],
            "rent_receipt",
        ),
        (RIB, "rib"),
        (
            ["CRÉDIT AGRICOLE", "RELEVÉ DE COMPTE", "du 01/09/2026 au 30/09/2026", "Nouveau solde créditeur 640,12"],
            "bank_statement",
        ),
        (["MAIF", "ATTESTATION D'ASSURANCE", "Assurance habitation", "Valable jusqu'au 31/12/2026"], "insurance"),
        (IDENTITY, "identity"),
        (["ORDONNANCE", "Dr Martin, médecin généraliste", "Le 03/10/2026", "Patient : M. X"], "health"),
        (["Liste de courses", "lait, oeufs, pain"], "other"),
    ],
)
def test_classification(lines: list[str], kind: str) -> None:
    got, confidence = dv.classify("\n".join(lines))
    assert got == kind
    assert 0 < confidence <= 0.97


def test_payslip_values_come_with_their_proof() -> None:
    reading_ = dv.read(PAYSLIP_TEXT, "IMG_2041.jpg")
    by = {f.key: f for f in reading_.fields}
    assert reading_.kind == "payslip" and reading_.title == "Bulletin de paie — septembre 2026"
    assert by["net_avant_impot"].value == 206_460 and "AVANT IMPOT" in by["net_avant_impot"].evidence.upper()
    assert by["net_paye"].value == 196_919 and by["pas_montant"].value == 9_541 and by["pas_taux"].value == "4.5"
    assert by["brut"].value == 265_000 and by["net_social"].value == 207_115 and by["net_imposable"].value == 212_030
    assert by["periode"].value == "2026-09" and by["date_paiement"].value == "2026-09-30"
    assert by["employeur"].value == "ACME SERVICES SAS"
    assert reading_.document_date == date(2026, 9, 30) and reading_.warnings == []
    assert dv.retention_until("payslip", reading_.document_date) is None  # jusqu'à la retraite


def test_inconsistent_payslip_is_flagged_not_trusted() -> None:
    text = PAYSLIP_TEXT.replace("1 969,19", "1 869,19")
    reading_ = dv.read(text)
    assert reading_.warnings and "net payé" in reading_.warnings[0]
    by = {f.key: f for f in reading_.fields}
    assert by["net_paye"].confidence == 0.5 and by["brut"].confidence == 0.9


def test_missing_values_stay_missing() -> None:
    reading_ = dv.read("BULLETIN DE PAIE\nNET A PAYER AVANT IMPOT SUR LE REVENU 2 064,60\nSalaire de base")
    keys = {f.key for f in reading_.fields}
    assert keys == {"net_avant_impot"}  # rien n'est déduit ni deviné


def test_invoice_rib_identity_extraction() -> None:
    inv = {f.key: f.value for f in dv.read("\n".join(INVOICE)).fields}
    assert inv["montant_ttc"] == 14_590 and inv["date_facture"] == "2026-10-12" and inv["echeance"] == "2026-11-02"
    assert inv["consommation_kwh"] == 1234 and inv["fournisseur"] == "EDF"
    rib = dv.read("\n".join(RIB))
    values = {f.key: f.value for f in rib.fields}
    assert (
        values["iban_masque"] == "FR76 •••• 189" and values["bic"] == "CMCIFR2A" and values["banque"] == "Crédit Mutuel"
    )
    assert "3000 6000" not in json.dumps(values)  # jamais l'IBAN entier
    ident = dv.read("\n".join(IDENTITY))
    assert {f.key: f.value for f in ident.fields} == {"type_piece": "Carte d'identité", "date_expiration": "2031-02-14"}
    assert ident.expires_on == date(2031, 2, 14) and ident.title == "Carte d'identité"
    assert "X4RTBPFW4" not in json.dumps([f.value for f in ident.fields])  # aucun numéro de pièce extrait


def test_official_documents_reuse_the_proven_parsers() -> None:
    tax = dv.read("\n".join(AVIS))
    by = {f.key: f for f in tax.fields}
    assert tax.kind == "tax_notice" and tax.title == "Avis d'impôt — revenus 2025"
    assert by["rfr"].value == 2_345_600 and by["rfr"].evidence == "Revenu fiscal de référence : 23 456 €"
    assert by["parts"].value == "1.00" and by["taux_pas"].value == "5.6" and by["annee_revenus"].value == 2025
    assert dv.retention_until("tax_notice", date(2026, 8, 1), {"annee_revenus": 2025}) == date(2028, 12, 31)
    caf = {f.key: f.value for f in dv.read("\n".join(CAF)).fields}
    assert caf["caf_aide_logement"] == 21_200 and caf["caf_prime_activite"] == 25_829 and caf["mois"] == "2026-09"
    ft = dv.read("\n".join(FT))
    assert ft.expires_on == date(2027, 3, 15)  # fin des droits : une échéance à surveiller


def test_retention_rules() -> None:
    assert dv.retention_until("invoice", date(2026, 10, 12)) == date(2031, 10, 12)
    assert dv.retention_until("health", date(2024, 2, 29)) == date(2026, 2, 28)
    assert dv.retention_until("rent_receipt", date(2026, 9, 1)) is None  # durée du bail + 3 ans : pas de date fixe
    assert dv.retention_until("invoice", None) is None
    assert dv.RETENTION_SOURCE["url"].startswith("https://www.service-public.gouv.fr/")


def test_user_values_are_checked() -> None:
    assert dv.parse_value("amount", "2 064,60") == 206_460
    assert dv.parse_value("amount", "1 969,19 €") == 196_919 and dv.parse_value("amount", 12.5) == 1_250
    assert dv.parse_value("date", "30/09/2026") == "2026-09-30" and dv.parse_value("date", "2026-09-30") == "2026-09-30"
    assert dv.parse_value("month", "09/2026") == "2026-09" and dv.parse_value("month", "septembre 2026") == "2026-09"
    assert dv.parse_value("percent", "4,5 %") == "4.5" and dv.parse_value("number", "145") == 145
    assert dv.parse_value("text", "  ACME   SAS ") == "ACME SAS"
    for type_, bad in (
        ("amount", "beaucoup"),
        ("amount", "-999999999"),
        ("date", "31/02/2026"),
        ("month", "13/2026"),
        ("percent", "150"),
        ("number", "-3"),
        ("text", "a\x00b"),
        ("text", " "),
        ("nope", "x"),
    ):
        with pytest.raises(ValueError):
            dv.parse_value(type_, bad)


def test_amount_reading_never_confuses_years_or_ids() -> None:
    assert list(dv.amounts("ANNEE 2026 SIRET 123 456 789 00012")) == []
    assert [c for c, _ in dv.amounts("BASE 151,67 H 2 450,00 -95,41")] == [15_167, 245_000, -9_541]
    assert dv.month_of("PERIODE DU 01/09/2026 AU 30/09/2026") == "2026-09" and dv.month_of("RIEN") is None


@given(st.text(max_size=600))
@hsettings(max_examples=200, deadline=None)
def test_reading_any_text_never_crashes(blob: str) -> None:
    result = dv.read(blob, "x.pdf")
    assert result.kind in dv.KINDS
    for f in result.fields:
        assert dv.field_spec(result.kind, f.key) is not None and 0 < f.confidence <= 1


def test_identifiers_are_masked_before_any_model_sees_them() -> None:
    masked = vault.mask_identifiers("\n".join([*RIB, *INVOICE, "NIR 1 85 05 75 123 456 78", *PAYSLIP]))
    for secret in ("3000 6000", "client@edf.example", "09 69 32 15 15", "1 85 05 75 123 456"):
        assert secret not in masked
    assert "2 064,60" in masked and "30/09/2026" in masked and "145,90" in masked  # montants et dates intacts


# ============================================================================ lecture confinée
def test_sniffing_by_content_not_by_name() -> None:
    assert reading.sniff(b"%PDF-1.7 ...") == reading.PDF
    assert reading.sniff(text_image(["x"])) == reading.JPEG
    assert reading.sniff(text_image(["x"], fmt="PNG")) == reading.PNG
    assert reading.sniff(text_image(["x"], fmt="WEBP")) == reading.WEBP
    with pytest.raises(reading.ReadingError, match="HEIC"):
        reading.sniff(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00")
    with pytest.raises(reading.ReadingError, match="non pris en charge"):
        reading.sniff(b"<html><script>alert(1)</script>")


def test_photos_lose_their_metadata_and_stand_upright() -> None:
    raw = text_image(["BULLETIN DE PAIE"], exif=gps_exif(orientation=6))
    assert Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8825)  # la photo d'origine porte une position GPS
    clean = reading.clean_image(raw)
    img = Image.open(io.BytesIO(clean.data))
    assert img.format == "JPEG" and not img.getexif() and "exif" not in img.info
    original = Image.open(io.BytesIO(raw)).size
    assert (clean.width, clean.height) == (original[1], original[0])  # orientation EXIF appliquée
    assert max(Image.open(io.BytesIO(clean.thumb)).size) <= 360


def test_transparent_png_and_hostile_images() -> None:
    clean = reading.clean_image(text_image(["Facture"], fmt="PNG", mode="RGBA"))
    assert Image.open(io.BytesIO(clean.data)).getpixel((5, 5)) == (255, 255, 255)  # fond blanc, pas noir
    with pytest.raises(reading.ReadingError):
        reading.clean_image(b"\xff\xd8\xff" + b"pas une image" * 100)
    bomb = io.BytesIO()
    Image.new("1", (12_000, 12_000)).save(bomb, "PNG")  # 144 Mpx compressés en quelques Ko
    with pytest.raises(reading.ReadingError):
        reading.clean_image(bomb.getvalue())


def test_tesseract_output_is_rebuilt_line_by_line() -> None:
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t10\t10\t-1\t",
            "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96\tNET",
            "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t90\tPAYE",
            "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t80\t1 969,19",
            "5\t1\t2\t1\t1\t1\t0\t0\t1\t1\tx\tbad",
            "5\t1\t3\t1\t1\t1\t0\t0\t1\t1\t70\tFIN",
            "junk",
        ]
    )
    out = reading.parse_tsv(tsv)
    assert out.text == "NET PAYE\n1 969,19\n\nFIN" and out.words == 4 and out.confidence == 0.84


@needs_ocr
def test_ocr_reads_a_tilted_photo() -> None:
    clean = reading.clean_image(text_image(PAYSLIP, rotate=1.5))
    out = reading.ocr(clean.data)
    assert out.confidence > 0.7 and "NET A PAYER AVANT IMPOT" in dv.fold(out.text)


@needs_ocr
def test_scanned_pdf_pages_are_read_by_ocr() -> None:
    pdf = scanned_pdf(PAYSLIP)
    assert reading.pdf_text_content(pdf).strip() == ""  # aucun texte : c'est un scan
    images = reading.pdf_images(pdf)
    assert len(images) == 1
    text = reading.ocr(reading.clean_image(images[0]).data).text
    assert dv.classify(text)[0] == "payslip"


def test_encrypted_or_broken_pdfs_are_refused() -> None:
    with pytest.raises(reading.ReadingError):
        reading.pdf_text_content(b"%PDF-1.4\n%garbage")
    assert reading.pdf_images(make_pdf(["texte seul"])) == []


# ============================================================================ API : dépôt, lecture, revue
def _upload(world: TestClient, *files: tuple[str, bytes], source: str = "upload") -> Any:
    return world.post(
        "/api/vault/upload",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        data={"source": source},
    )


def _consent(world: TestClient, purpose: str, value: bool = True) -> None:
    assert world.put(f"/api/consents/{purpose}", json={"granted": value}).status_code == 200


@needs_ocr
def test_photo_of_a_payslip_end_to_end(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = _upload(world, ("IMG_2041.jpg", text_image(PAYSLIP, exif=gps_exif())), source="camera")
    assert r.status_code == 201, r.text
    doc = r.json()["documents"][0]
    assert doc["status"] == "processing" and r.json()["skipped"] == []
    detail = world.get(f"/api/vault/{doc['id']}").json()  # lu en tâche de fond, juste après la réponse
    assert detail["status"] == "to_review" and detail["kind"] == "payslip" and detail["method"] == "ocr"
    assert (
        detail["title"] == "Bulletin de paie — septembre 2026" and detail["has_thumb"] and detail["source"] == "camera"
    )
    by = {f["key"]: f for f in detail["fields"]}
    assert by["net_paye"]["value"] == 196_919 and "NET PAYE" in by["net_paye"]["evidence"].upper()
    assert detail["missing"] == [] and detail["warnings"] == []
    assert detail["retention"]["label"].startswith("Jusqu'à la liquidation")
    # Le fichier servi est l'image nettoyée : plus de position GPS ; affichée dans un bac à sable
    f = world.get(f"/api/vault/{doc['id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    assert "sandbox" in f.headers["content-security-policy"] and f.headers["x-content-type-options"] == "nosniff"
    assert not Image.open(io.BytesIO(f.content)).getexif()
    assert world.get(f"/api/vault/{doc['id']}/thumb").headers["content-type"] == "image/jpeg"
    with app.state.sessionmaker() as db:
        raw = db.get(VaultBlob, doc["id"])
        assert raw is not None and raw.data.startswith(b"BKV1") and b"JFIF" not in raw.data  # chiffré
        actions = [a.action for a in db.scalars(select(AuditLog))]
        assert "vault.upload" in actions and "vault.file_read" in actions
        details = " ".join(a.details for a in db.scalars(select(AuditLog).where(AuditLog.action.like("vault.%"))))
        assert "1 969" not in details and "ACME" not in details  # journal sans contenu
    listing = world.get("/api/vault").json()
    assert listing["counts"] == {"payslip": 1} and listing["documents"][0]["fields_count"] == 10


def test_uploads_are_filtered_and_deduplicated(world: TestClient) -> None:
    pdf = make_pdf(INVOICE)
    r = _upload(world, ("facture.pdf", pdf), ("notes.txt", b"juste du texte"), ("../../etc/passwd", b"%PDF-1.4 x"))
    body = r.json()
    assert r.status_code == 201 and len(body["documents"]) == 2
    assert body["skipped"] == [
        {"filename": "notes.txt", "reason": "Format non pris en charge : PDF, JPEG, PNG ou WebP"}
    ]
    assert {d["title"] for d in body["documents"]} == {"facture.pdf", "passwd"}  # chemin retiré du nom
    listed = {d["title"]: d for d in world.get("/api/vault").json()["documents"]}  # après la lecture en tâche de fond
    assert listed["Facture EDF — 12 octobre 2026"]["status"] == "to_review"
    again = _upload(world, ("copie.pdf", pdf)).json()
    assert again["documents"] == [] and again["skipped"][0]["reason"] == "Déjà dans le coffre"
    assert _upload(world, *[(f"{i}.pdf", make_pdf([f"doc {i}"])) for i in range(11)]).status_code == 422
    assert _upload(world, ("x.pdf", pdf), source="robot").status_code == 422
    broken = next(d for d in body["documents"] if d["title"] == "passwd")
    assert world.get(f"/api/vault/{broken['id']}").json()["status"] == "failed"


def test_official_pdf_feeds_bank_after_review(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc = _upload(world, ("avis.pdf", make_pdf(AVIS))).json()["documents"][0]
    detail = world.get(f"/api/vault/{doc['id']}").json()
    assert detail["kind"] == "tax_notice" and detail["method"] == "pdf_text" and detail["official"]
    assert detail["retention_until"] == "2028-12-31"
    # Une correction est prise en compte, l'original reste visible
    r = world.put(f"/api/vault/{doc['id']}/fields/rfr", json={"value": "23 500"})
    rfr = next(f for f in r.json()["fields"] if f["key"] == "rfr")
    assert rfr["value"] == 2_350_000 and rfr["original"] == 2_345_600 and rfr["source"] == "user"
    assert world.post(f"/api/vault/{doc['id']}/apply").status_code == 200
    with app.state.sessionmaker() as db:
        fact = db.scalars(select(OfficialFact)).one()
        assert fact.kind == "avis_impot" and json.loads(fact.data)["rfr_cents"] == 2_350_000
    payslip = _upload(world, ("p.pdf", make_pdf(PAYSLIP))).json()["documents"][0]
    assert world.post(f"/api/vault/{payslip['id']}/apply").status_code == 422


def test_corrections_kinds_and_review(world: TestClient) -> None:
    doc = _upload(world, ("bulletin.pdf", make_pdf(PAYSLIP))).json()["documents"][0]
    did = doc["id"]
    assert world.put(f"/api/vault/{did}/fields/net_paye", json={"value": "beaucoup"}).status_code == 422
    assert world.put(f"/api/vault/{did}/fields/inconnu", json={"value": "1"}).status_code == 404
    out = world.put(f"/api/vault/{did}/fields/net_paye", json={"value": "1 869,19"}).json()
    assert out["warnings"]  # ta correction contredit les autres montants : c'est signalé, pas masqué
    out = world.delete(f"/api/vault/{did}/fields/employeur").json()
    assert {"key": "employeur", "label": "Employeur", "type": "text"} in out["missing"]
    assert world.delete(f"/api/vault/{did}/fields/employeur").status_code == 404
    # Changer de type relit le texte et garde tes valeurs
    out = world.patch(f"/api/vault/{did}", json={"kind": "invoice", "title": "Bulletin à reclasser"}).json()
    assert out["kind"] == "invoice" and out["title"] == "Bulletin à reclasser"
    back = world.patch(f"/api/vault/{did}", json={"kind": "payslip"}).json()
    assert next(f for f in back["fields"] if f["key"] == "net_paye")["value"] == 186_919  # ta valeur, gardée
    assert world.patch(f"/api/vault/{did}", json={"kind": "identity"}).status_code == 409  # sans ton accord
    assert world.patch(f"/api/vault/{did}", json={"kind": "inconnu"}).status_code == 422
    assert world.post(f"/api/vault/{did}/verify").json()["status"] == "verified"
    assert world.patch(f"/api/vault/{did}", json={"sensitive": True}).json()["sensitive"] is True


def test_sensitive_documents_need_consent(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    refused = _upload(world, ("cni.pdf", make_pdf(IDENTITY))).json()["documents"][0]
    detail = world.get(f"/api/vault/{refused['id']}").json()
    assert detail["status"] == "refused" and detail["sensitive"] and "accord" in detail["error"]
    assert world.get(f"/api/vault/{refused['id']}/file").status_code == 404  # le fichier n'a pas été gardé
    with app.state.sessionmaker() as db:
        assert db.get(VaultBlob, refused["id"]) is None
        doc = db.get(VaultDocument, refused["id"])
        assert doc is not None and doc.text is None
    world.delete(f"/api/vault/{refused['id']}")
    _consent(world, "sensitive_documents")
    kept = _upload(world, ("cni.pdf", make_pdf(IDENTITY))).json()["documents"][0]
    detail = world.get(f"/api/vault/{kept['id']}").json()
    assert detail["status"] == "to_review" and detail["sensitive"] and detail["expires_on"] == "2031-02-14"
    assert world.patch(f"/api/vault/{kept['id']}", json={"sensitive": False}).status_code == 422
    configure(world)
    _consent(world, "ai_documents")
    r = world.post(f"/api/vault/{kept['id']}/assist")
    assert r.status_code == 403 and FakeLlm.calls == []  # jamais envoyée à un modèle


def test_assisted_reading_keeps_only_quoted_values(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    lines = ["Garage du Centre", "Réparation du véhicule", "Total à régler 389,90 € le 14/10/2026", *RIB[3:4]]
    doc = _upload(world, ("devis.pdf", make_pdf(lines))).json()["documents"][0]
    assert world.post(f"/api/vault/{doc['id']}/assist").status_code == 403  # pas d'accord
    configure(world)
    _consent(world, "ai_documents")
    FakeLlm.script = [
        answer(
            json.dumps(
                {
                    "kind": "invoice",
                    "fields": [
                        {"key": "montant_ttc", "value": "389,90", "evidence": "Total à régler 389,90 € le 14/10/2026"},
                        {
                            "key": "date_facture",
                            "value": "14/10/2026",
                            "evidence": "Total à régler 389,90 € le 14/10/2026",
                        },
                        {"key": "echeance", "value": "01/11/2026", "evidence": "à payer avant le 01/11/2026"},
                        {"key": "fournisseur", "value": "Garage Dupont", "evidence": "Garage du Centre"},
                        {"key": "inconnu", "value": "x", "evidence": "Garage du Centre"},
                    ],
                }
            )
        )
    ]
    r = world.post(f"/api/vault/{doc['id']}/assist")
    assert r.status_code == 200, r.text
    out = r.json()
    by = {f["key"]: f for f in out["fields"]}
    assert out["kind"] == "invoice" and out["assist"]["added"] == ["montant_ttc", "date_facture"]
    assert out["assist"]["rejected"] == 3  # citation inventée, valeur absente de la citation, champ inconnu
    assert by["montant_ttc"]["source"] == "assist" and by["montant_ttc"]["value"] == 38_990
    sent = FakeLlm.calls[-1]["messages"][1]["content"]
    assert "3000 6000" not in sent and "[IBAN masqué]" in sent and FakeLlm.calls[-1]["temperature"] == 0
    with app.state.sessionmaker() as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "vault.assist")).one()
        assert '"rejected":3' in entry.details and "389" not in entry.details


def test_export_and_delete(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc = _upload(world, ("facture.pdf", make_pdf(INVOICE))).json()["documents"][0]
    assert world.post("/api/vault/export", json={"password": "faux"}).status_code == 403
    r = world.post("/api/vault/export", json={"password": PASSWORD})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.read(manifest["documents"][0]["file"]).startswith(b"%PDF")
    assert "manifest.json" in names and manifest["documents"][0]["kind"] == "invoice"
    assert world.delete(f"/api/vault/{doc['id']}").status_code == 204
    assert world.get(f"/api/vault/{doc['id']}").status_code == 404
    assert world.delete(f"/api/vault/{doc['id']}").status_code == 404
    with app.state.sessionmaker() as db:
        assert db.get(VaultBlob, doc["id"]) is None
        assert {a.action for a in db.scalars(select(AuditLog))} >= {
            "vault.export",
            "vault.export_denied",
            "vault.delete",
        }


def test_stuck_reads_resume_and_failures_are_explained(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        created, _ = vault.ingest(
            db, [vault.Incoming("f.pdf", make_pdf(INVOICE))], source="upload", actor="t", file_limit=10**7,
            vault_limit=10**9,
        )  # fmt: skip
        created[0].created_at = utcnow() - timedelta(minutes=30)  # serveur arrêté pendant la lecture
        db.commit()
        assert vault.housekeeping(db) == {"vault_resumed": 1}
        db.commit()
        doc = db.get(VaultDocument, created[0].id)
        assert doc is not None and doc.status == "to_review" and doc.kind == "invoice"
        assert vault.housekeeping(db) == {"vault_resumed": 0}
    did = created[0].id
    assert world.post(f"/api/vault/{did}/reprocess").status_code == 202
    assert world.get(f"/api/vault/{did}").json()["status"] == "to_review"


def test_expiring_documents_are_listed(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _consent(world, "sensitive_documents")
    soon = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
    lines = [*IDENTITY[:4], f"Date d'expiration : {soon}"]
    _upload(world, ("cni.pdf", make_pdf(lines)))
    with app.state.sessionmaker() as db:
        expiring = vault.expiring(db, date.today())
        assert [d.kind for d in expiring] == ["identity"]


# ============================================================================ schéma : colonnes ajoutées sans migration
def test_schema_upgrade_adds_new_columns_and_indexes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    old = MetaData()
    Table("things", old, Column("id", Integer, primary_key=True), Column("name", String(20)))
    old.create_all(engine)
    new = MetaData()
    Table(
        "things",
        new,
        Column("id", Integer, primary_key=True),
        Column("name", String(20), index=True),
        Column("note", String(40)),
        Column("flag", Integer, nullable=False, default=0),
    )
    added = upgrade_schema(engine, new)
    assert "things.note" in added and "things.flag" in added and any(a.startswith("index") for a in added)
    columns = {c["name"] for c in inspect(engine).get_columns("things")}
    assert {"note", "flag"} <= columns and upgrade_schema(engine, new) == []  # idempotent
    broken = MetaData()
    Table("things", broken, Column("id", Integer, primary_key=True), Column("must", String(5), nullable=False))
    with pytest.raises(SchemaUpgradeError):
        upgrade_schema(engine, broken)
    engine.dispose()


# ============================================================================ chemins plus rares
@needs_ocr
def test_scanned_pdf_upload_is_read_with_a_thumbnail(world: TestClient) -> None:
    doc = _upload(world, ("scan.pdf", scanned_pdf(PAYSLIP))).json()["documents"][0]
    detail = world.get(f"/api/vault/{doc['id']}").json()
    assert detail["kind"] == "payslip" and detail["method"] == "ocr" and detail["has_thumb"] and detail["pages"] == 1
    assert world.get(f"/api/vault/{doc['id']}/file").headers["content-disposition"].startswith("attachment")


def test_without_tesseract_photos_are_kept_unread(world: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reading, "ocr_available", lambda: False)
    doc = _upload(world, ("photo.jpg", text_image(["BULLETIN DE PAIE"], exif=gps_exif()))).json()["documents"][0]
    detail = world.get(f"/api/vault/{doc['id']}").json()
    assert detail["status"] == "to_review" and detail["kind"] == "other" and detail["method"] == "none"
    assert "indisponible" in detail["error"] and detail["has_thumb"]
    assert not Image.open(
        io.BytesIO(world.get(f"/api/vault/{doc['id']}/file").content)
    ).getexif()  # nettoyée quand même
    assert world.get("/api/vault").json()["ocr_available"] is False


def test_unexpected_crash_marks_the_document_failed(world: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(vault, "_read_content", boom)
    doc = _upload(world, ("f.pdf", make_pdf(INVOICE))).json()["documents"][0]
    detail = world.get(f"/api/vault/{doc['id']}").json()
    assert detail["status"] == "failed" and detail["error"] == "Lecture interrompue : réessaie"
    assert world.patch(f"/api/vault/{doc['id']}", json={"title": "x"}).status_code == 200  # échec : renommable
    assert world.post(f"/api/vault/{doc['id']}/verify").status_code == 409


def test_caf_and_france_travail_feed_bank(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    caf = _upload(world, ("caf.pdf", make_pdf(CAF))).json()["documents"][0]
    world.put(f"/api/vault/{caf['id']}/fields/caf_prime_activite", json={"value": "260,00"})
    assert world.post(f"/api/vault/{caf['id']}/apply").json() == {"kind": "caf", "period": "2026-09"}
    ft = _upload(world, ("ft.pdf", make_pdf(FT))).json()["documents"][0]
    assert world.post(f"/api/vault/{ft['id']}/apply").status_code == 200
    with app.state.sessionmaker() as db:
        facts = {f.kind: json.loads(f.data) for f in db.scalars(select(OfficialFact))}
    prestations = {p["code"]: p["monthly_cents"] for p in facts["caf"]["prestations"]}
    assert prestations == {"aide_logement": 21_200, "prime_activite": 26_000}  # ta correction fait foi
    assert facts["france_travail"]["daily_allowance_cents"] == 3_845 and facts["france_travail"]["days_left"] == 145


def test_assisted_reading_refusals(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc = _upload(world, ("note.pdf", make_pdf(["Note de frais", "Repas 23,40 € le 03/10/2026"]))).json()["documents"][
        0
    ]
    _consent(world, "ai_documents")
    r = world.post(f"/api/vault/{doc['id']}/assist")
    assert r.status_code == 409 and "Connecteurs" in r.json()["detail"]  # pas de clé
    configure(world, privacy="aggregates")
    assert "chiffres seuls" in world.post(f"/api/vault/{doc['id']}/assist").json()["detail"]
    world.patch("/api/assistant", json={"privacy": "redacted"})
    FakeLlm.script = [answer("pas du JSON du tout")]
    out = world.post(f"/api/vault/{doc['id']}/assist").json()
    assert out["assist"]["added"] == [] and out["kind"] == "other"
    FakeLlm.script = [answer(json.dumps({"kind": "identity", "fields": []}))]
    r = world.post(f"/api/vault/{doc['id']}/assist")
    assert r.status_code == 409 and world.get(f"/api/vault/{doc['id']}").json()["sensitive"] is True
    assert world.post(f"/api/vault/{doc['id']}/assist").status_code == 403  # désormais sensible : plus jamais envoyé
    blank = _upload(world, ("vide.pdf", make_pdf([" "]))).json()["documents"][0]
    unread = world.get(f"/api/vault/{blank['id']}").json()
    assert unread["status"] == "to_review" and unread["error"].startswith("Aucun texte lu")  # gardé, à compléter
    assert world.post(f"/api/vault/{blank['id']}/assist").status_code == 409  # rien à envoyer


def test_limits_and_refusals(app, world: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.vault_file_bytes = 200_000
    big = _upload(world, ("gros.pdf", b"%PDF-1.4\n" + b"0" * 300_000)).json()
    assert big["documents"] == [] and "trop lourd" in big["skipped"][0]["reason"]
    app.state.settings.vault_total_bytes = 10
    full = _upload(world, ("f.pdf", make_pdf(INVOICE))).json()
    assert full["skipped"][0]["reason"].startswith("Coffre plein")
    app.state.settings.vault_total_bytes = 10**9
    doc = _upload(world, ("f.pdf", make_pdf(INVOICE))).json()["documents"][0]
    monkeypatch.setattr(vault, "EXPORT_MAX_BYTES", 10)
    assert world.post("/api/vault/export", json={"password": PASSWORD}).status_code == 413
    assert world.get("/api/vault/999").status_code == 404 and world.get("/api/vault/999/thumb").status_code == 404
    assert world.patch(f"/api/vault/{doc['id']}", json={"title": "\x00\x01"}).status_code == 422
    refused = _upload(world, ("cni.pdf", make_pdf(IDENTITY))).json()["documents"][0]
    assert world.post(f"/api/vault/{refused['id']}/reprocess").status_code == 409
    assert world.patch(f"/api/vault/{refused['id']}", json={"title": "x"}).status_code == 409
