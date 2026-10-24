"""Boîte mail en lecture seule, contre un faux serveur IMAP local qui journalise chaque commande : relier exige le mot
de passe de Bank et un essai réussi (rien n'est gardé sinon) ; la lecture n'ouvre le dossier qu'en lecture seule
(EXAMINE, BODY.PEEK), ne reprend que les messages nouveaux, propose les PDF joints inconnus avec un genre deviné,
écarte les doublons et les expéditeurs hors liste ; l'import relit la pièce, vérifie son empreinte et la dépose au
coffre ; identifiants jamais renvoyés, jamais dans une erreur ; aucune commande qui écrit."""

from __future__ import annotations

import imaplib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.domain import mail as dom
from app.models import AuditLog, MailAccount, MailProposal, VaultDocument
from app.services import mail as svc
from tests.conftest import PASSWORD, do_login, do_setup
from tests.fake_imap import FakeImapServer, make_mail
from tests.pdfgen import make_pdf

INVOICE_PDF = make_pdf(["Free Mobile", "FACTURE du 03/10/2026", "Montant TTC : 19,99 €", "À payer avant le 20/10/2026"])
CAF_PDF = make_pdf(["Caf du Loiret", "ATTESTATION DE PAIEMENT", "Prime d'activité : 258,29 €", "Octobre 2026"])


@pytest.fixture
def imap() -> Iterator[FakeImapServer]:
    server = FakeImapServer()
    yield server
    server.close()


@pytest.fixture
def world(app, client: TestClient, imap: FakeImapServer) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    app.state.mail_client_factory = lambda host, port, security: imaplib.IMAP4("127.0.0.1", imap.port)
    return client


def _link(world: TestClient, imap: FakeImapServer, **over: object) -> object:
    body = {
        "password": PASSWORD,
        "host": "imap.example.org",
        "port": imap.port,
        "security": "ssl",
        "username": "micka@example.org",
        "mail_password": "app-password-1234",
        "folder": "INBOX",
        "senders": [],
    }
    body.update(over)
    return world.put("/api/mail", json=body)


def test_domain_reads_headers_attachments_and_guesses_the_kind() -> None:
    msg = make_mail(
        sender="Caf du Loiret <ne-pas-repondre@caf.fr>",
        subject="=?utf-8?q?Votre_attestation_de_paiement_d=C3=A9cembre?=",
        pdf=CAF_PDF,
        filename="attestation paiement.pdf",
    )
    raw = msg.as_bytes()
    h = dom.parse_headers(raw)
    assert h.sender == "ne-pas-repondre@caf.fr" and h.domain == "caf.fr" and h.sender_name == "Caf du Loiret"
    assert h.subject == "Votre attestation de paiement décembre" and h.content_type == "multipart/mixed"
    assert h.received_at is not None and h.received_at.tzinfo is not None
    atts, skipped = dom.attachments(raw)
    assert [a.filename for a in atts] == ["attestation paiement.pdf"] and skipped == []
    assert atts[0].data.startswith(b"%PDF") and len(atts[0].sha256) == 64
    assert dom.guess_kind(h, atts[0].filename) == ("caf", 0.9)
    free = dom.parse_headers(
        make_mail(sender="Free <facture@free-mobile.fr>", subject="Votre facture", pdf=None).as_bytes()
    )
    assert dom.guess_kind(free, "Facture_2026_10.pdf") == ("invoice", 0.7)
    unknown = dom.parse_headers(make_mail(sender="x@y.example", subject="Hello", pdf=None).as_bytes())
    assert dom.guess_kind(unknown, "piece.pdf")[0] == "other"
    # Un « PDF » qui n'en est pas un, une pièce trop lourde, plus de cinq pièces : écartés et dits.
    fake = make_mail(sender="a@b.example", subject="x", pdf=b"not a pdf", filename="faux.pdf")
    assert dom.attachments(fake.as_bytes()) == ([], ["faux.pdf : pas un PDF"])
    heavy = make_mail(sender="a@b.example", subject="x", pdf=b"%PDF-1.4" + b"0" * 2_000, filename="gros.pdf")
    assert dom.attachments(heavy.as_bytes(), max_bytes=1_000)[1] == ["gros.pdf : trop lourd (0 Mo)"]
    many = make_mail(sender="a@b.example", subject="x", pdf=None)
    for i in range(7):
        many.add_attachment(b"%PDF-1.4 " + bytes([48 + i]), maintype="application", subtype="pdf", filename=f"p{i}.pdf")
    kept, notes = dom.attachments(many.as_bytes())
    assert len(kept) == 5 and notes == ["Plus de cinq pièces jointes : les suivantes sont ignorées"]
    assert dom.sender_allowed(h, ("caf.fr",)) and not dom.sender_allowed(h, ("impots.gouv.fr",))
    assert dom.sender_allowed(h, ("ne-pas-repondre@caf.fr",)) and dom.sender_allowed(h, ())
    assert dom.scrub("LOGIN micka@example.org password=secret42 failed", "micka@example.org", "secret42") == (
        "LOGIN ••• password=••• failed"
    )
    assert dom.valid_host("IMAP.Free.fr") == "imap.free.fr"
    for bad in ("http://imap.free.fr/", "imap free", "", "a" * 300):
        with pytest.raises(ValueError, match="Serveur IMAP"):
            dom.valid_host(bad)
    with pytest.raises(ValueError, match="dossier"):
        dom.valid_folder('IN"BOX')


def test_linking_needs_bank_password_and_a_working_login_and_never_returns_secrets(
    world: TestClient, imap: FakeImapServer, app
) -> None:  # type: ignore[no-untyped-def]
    st = world.get("/api/mail").json()
    assert st["configured"] is False and st["proposals"] == []
    assert _link(world, imap, password="pas le bon").status_code == 403
    r = _link(world, imap, mail_password="mauvais")
    assert r.status_code == 401 and "Identification refusée" in r.json()["detail"]
    assert "mauvais" not in r.text and "micka@example.org" not in r.text
    assert world.get("/api/mail").json()["configured"] is False, "un essai raté ne garde rien"
    assert _link(world, imap, folder="Factures").status_code == 422
    assert _link(world, imap, host="http://imap").status_code == 422
    r = _link(world, imap, senders=["caf.fr", " francetravail.fr "])
    assert r.status_code == 200, r.text
    acc = r.json()["account"]
    assert acc["username_hint"] == "mi…@example.org" and acc["senders"] == ["caf.fr", "francetravail.fr"]
    assert "password" not in json.dumps(r.json()) and "app-password" not in r.text
    assert imap.commands[-2].endswith("EXAMINE INBOX") or "EXAMINE INBOX" in " ".join(imap.commands)
    with app.state.sessionmaker() as db:
        row = db.get(MailAccount, 1)
        assert row is not None and row.username == "micka@example.org" and row.password == "app-password-1234"
        raw = db.execute(text("SELECT password, username FROM mail_accounts")).one()
        assert "app-password" not in raw[0] and "micka@" not in raw[1], "chiffrés en base"
        for entry in db.scalars(select(AuditLog).where(AuditLog.action.like("mail.%"))):
            assert "app-password" not in entry.details and "micka@example.org" not in entry.details
    assert world.post("/api/mail/test").json() == {"ok": True, "messages": 0}
    # Sans accord « mail », rien n'est lu ; l'accord donné, la lecture tourne.
    rep = world.post("/api/mail/scan").json()["report"]
    assert rep["scanned"] == 0 and any("accord" in n for n in rep["notes"])


def test_scan_proposes_new_pdfs_once_import_puts_them_in_the_vault_and_nothing_is_written(
    world: TestClient, imap: FakeImapServer, app
) -> None:  # type: ignore[no-untyped-def]
    assert _link(world, imap, senders=["caf.fr", "free-mobile.fr"]).status_code == 200
    assert world.put("/api/consents/mail", json={"granted": True}).status_code == 200
    imap.add(make_mail(sender="Caf du Loiret <ne-pas-repondre@caf.fr>", subject="Votre attestation de paiement", pdf=CAF_PDF, filename="attestation.pdf"))  # fmt: skip
    imap.add(make_mail(sender="Free Mobile <facture@free-mobile.fr>", subject="Votre facture Free Mobile", pdf=INVOICE_PDF, filename="Facture_Free.pdf"))  # fmt: skip
    imap.add(make_mail(sender="Promo <pub@shop.example>", subject="Soldes", pdf=INVOICE_PDF, filename="catalogue.pdf"))
    imap.add(make_mail(sender="Caf du Loiret <ne-pas-repondre@caf.fr>", subject="Bonjour", pdf=None))
    out = world.post("/api/mail/scan").json()
    rep = out["report"]
    assert rep["scanned"] == 4 and rep["proposed"] == 2 and rep["skipped"] == 1, rep
    props = out["proposals"]
    assert [p["filename"] for p in props] == ["Facture_Free.pdf", "attestation.pdf"] or {p["filename"] for p in props} == {"Facture_Free.pdf", "attestation.pdf"}  # fmt: skip
    by_file = {p["filename"]: p for p in props}
    assert (
        by_file["attestation.pdf"]["kind_guess"] == "caf"
        and by_file["attestation.pdf"]["kind_label"] == "Attestation CAF"
    )
    assert (
        by_file["Facture_Free.pdf"]["kind_guess"] == "invoice"
        and by_file["Facture_Free.pdf"]["sender_domain"] == "free-mobile.fr"
    )
    assert by_file["attestation.pdf"]["sender"] == "Caf du Loiret <ne-pas-repondre@caf.fr>"
    assert out["account"]["last_uid"] == 4 and out["account"]["status"] == "active"
    # Relire ne repropose rien ; un nouveau message avec la même pièce non plus (empreinte connue).
    again = world.post("/api/mail/scan").json()["report"]
    assert again["scanned"] == 0 and again["proposed"] == 0
    imap.add(make_mail(sender="Free Mobile <facture@free-mobile.fr>", subject="Rappel", pdf=INVOICE_PDF, filename="Facture_Free.pdf"))  # fmt: skip
    rep = world.post("/api/mail/scan").json()["report"]
    assert rep["scanned"] == 1 and rep["proposed"] == 0 and rep["skipped"] == 1
    # Import : la pièce est relue, vérifiée par son empreinte, déposée au coffre, lue en tâche de fond.
    att = by_file["attestation.pdf"]
    r = world.post(f"/api/mail/proposals/{att['id']}/import")
    assert r.status_code == 201, r.text
    doc_ids = r.json()["document_ids"]
    assert len(doc_ids) == 1 and all(p["id"] != att["id"] for p in r.json()["proposals"])
    doc = world.get(f"/api/vault/{doc_ids[0]}").json()
    assert doc["source"] == "mail" and doc["kind"] == "caf" and doc["filename"] == "attestation.pdf"
    with app.state.sessionmaker() as db:
        prop = db.get(MailProposal, att["id"])
        assert prop is not None and prop.status == "imported" and prop.document_id == doc_ids[0]
        assert db.get(VaultDocument, doc_ids[0]) is not None
    assert world.post(f"/api/mail/proposals/{att['id']}/import").status_code == 404, "pas deux fois"
    # Écarter ; les écartés et importés apparaissent dans « récents », plus dans les propositions.
    free = by_file["Facture_Free.pdf"]
    st = world.post(f"/api/mail/proposals/{free['id']}/dismiss").json()
    assert st["proposals"] == [] and {p["status"] for p in st["recent"]} == {"imported", "dismissed"}
    # Le message disparu de la boîte : l'import le dit, sans trace.
    imap.add(make_mail(sender="Caf du Loiret <ne-pas-repondre@caf.fr>", subject="Attestation", pdf=make_pdf(["Caf", "ATTESTATION", "x"]), filename="a2.pdf"))  # fmt: skip
    new = world.post("/api/mail/scan").json()["proposals"][0]
    del imap.messages[new["id"] and max(imap.messages)]
    gone = world.post(f"/api/mail/proposals/{new['id']}/import")
    assert gone.status_code == 410 and "plus dans la boîte" in gone.json()["detail"]
    # Aucune commande qui écrit, jamais de SELECT en écriture ; BODY.PEEK partout.
    joined = " ".join(imap.commands).upper()
    assert (
        "STORE" not in joined and "EXPUNGE" not in joined and "COPY" not in joined and " SELECT " not in f" {joined} "
    )
    assert "EXAMINE INBOX" in joined and "BODY.PEEK[" in joined and "BODY[" not in joined.replace("BODY.PEEK[", "")
    # Retirer la boîte : plus de compte, propositions en attente effacées, le document importé reste.
    assert world.delete("/api/mail").json()["configured"] is False
    with app.state.sessionmaker() as db:
        assert db.get(MailAccount, 1) is None
        assert db.scalar(select(MailProposal).where(MailProposal.status == "proposed")) is None
        assert db.get(VaultDocument, doc_ids[0]) is not None


def test_scan_handles_a_broken_login_or_server_without_leaking_and_runs_on_schedule(
    world: TestClient, imap: FakeImapServer, app
) -> None:  # type: ignore[no-untyped-def]
    assert _link(world, imap).status_code == 200
    world.put("/api/consents/mail", json={"granted": True})
    imap.password = "changed-on-the-server"
    out = world.post("/api/mail/scan").json()
    assert out["account"]["status"] == "error" and "Identification refusée" in out["account"]["last_error"]
    assert "changed-on-the-server" not in json.dumps(out) and "app-password" not in json.dumps(out)
    imap.password = "app-password-1234"
    with app.state.sessionmaker() as db:
        row = db.get(MailAccount, 1)
        assert row is not None
        now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        assert svc.due(row, now) is True
        report = svc.tick(db, now=now, factory=lambda h, p, s: imaplib.IMAP4("127.0.0.1", imap.port))
        assert report == {"mail_scanned": 0, "mail_proposed": 0}
        db.commit()
        row = db.get(MailAccount, 1)
        assert row is not None and row.status == "active" and row.last_sync_at == now
        assert svc.due(row, now + timedelta(minutes=10)) is False and svc.due(row, now + timedelta(hours=1)) is True
    # Serveur injoignable : l'erreur est dite, sans identifiant, et la prochaine lecture reprendra.
    app.state.mail_client_factory = lambda host, port, security: imaplib.IMAP4("127.0.0.1", 1)
    r = world.post("/api/mail/scan")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["account"]["status"] == "error" and "injoignable" in out["account"]["last_error"]
    assert world.post("/api/mail/test").status_code == 502
    for _ in range(4):
        world.post("/api/mail/scan")
    assert world.post("/api/mail/scan").status_code == 429, "six lectures à la demande par heure"
