"""Comptes de l'État (#30, #32) : un mail d'organisme sans pièce jointe (avis disponible) vu par la boîte mail devient
une tâche « document à récupérer » avec la page d'entrée officielle, le document attendu et une échéance ; les
attentes de saison (avis d'impôt fin août, attestation mensuelle si la CAF verse) relancent ; la tâche se ferme seule
quand le PDF arrive dans Papiers ; chaque organisme dit son dernier document et s'il est à renouveler."""

from __future__ import annotations

import hashlib
import imaplib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from app.api import deps
from app.domain import state_accounts as dom
from app.models import DocumentTask, Transaction, VaultDocument
from tests.conftest import PASSWORD, do_login, do_setup
from tests.fake_imap import FakeImapServer, make_mail
from tests.pdfgen import make_pdf


# ------------------------------------------------------------------ domaine
def test_a_notice_without_attachment_becomes_a_task() -> None:
    day = date(2026, 8, 3)
    n = dom.notice_from_mail("ne-pas-repondre.impots.gouv.fr", "Votre avis d'impôt est disponible", day, "<a@x>", False)
    assert n is not None and (n.body, n.expected, n.due) == ("impots", "avis d'impôt sur le revenu", date(2026, 8, 17))
    assert n.key.startswith("mail:") and len(n.key) == 45
    tf = dom.notice_from_mail("dgfip.finances.gouv.fr", "Votre avis de taxe foncière", day, "", False)
    assert tf is not None and tf.expected == "avis de taxe foncière"
    caf = dom.notice_from_mail("caf.fr", "Votre Caf vous informe : changement de vos droits", day, "<b@x>", False)
    assert caf is not None and caf.expected == "attestation de paiement (tes droits ont changé)"
    ft = dom.notice_from_mail("francetravail.fr", "Un nouveau document est disponible", day, "<c@x>", False)
    assert ft is not None and (ft.body, ft.expected) == ("france_travail", "attestation France Travail")
    assert dom.notice_from_mail("caf.fr", "Votre attestation", day, "<d@x>", True) is None, "PDF joint : déjà proposé"
    assert dom.notice_from_mail("caf.fr", "Bonjour", day, "<e@x>", False) is None, "rien n'est annoncé"
    assert dom.notice_from_mail("impots.gouv.fr.evil.example", "Avis disponible", day, "", False) is None
    assert dom.body_of("assure.ameli.fr") == dom.BY_SLUG["ameli"] and dom.body_of("example.org") is None


def test_seasonal_expectations() -> None:
    assert dom.seasonal(date(2026, 7, 1), {}) == []
    (avis,) = dom.seasonal(date(2026, 7, 20), {})
    assert (avis.key, avis.body, avis.due) == ("season:avis_ir:2026", "impots", date(2026, 8, 31))
    found = dom.seasonal(date(2026, 9, 2), {"caf": {"2026-08"}, "france_travail": {"2026-06"}})
    assert [e.key for e in found] == ["season:avis_ir:2026", "season:caf:2026-08"]
    caf = found[1]
    assert caf.expected == "attestation de paiement CAF de 08/2026" and caf.due == date(2026, 9, 30)
    assert dom.closes("avis d'impôt sur le revenu", "impots", "tax_notice")
    assert not dom.closes("réponse dans ta messagerie sécurisée", "impots", "tax_notice"), "pas de PDF attendu"
    assert not dom.closes("attestation de paiement", "caf", "tax_notice")


# ------------------------------------------------------------------ bout en bout
@pytest.fixture
def imap() -> Iterator[FakeImapServer]:
    server = FakeImapServer()
    yield server
    server.close()


@pytest.fixture
def world(app, client: TestClient, imap: FakeImapServer) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    app.state.mail_client_factory = lambda host, port, security: imaplib.IMAP4("127.0.0.1", imap.port)
    yield client
    app.dependency_overrides.clear()


def _link(world: TestClient, imap: FakeImapServer) -> None:
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
    assert world.put("/api/mail", json=body).status_code == 200
    assert world.put("/api/consents/mail", json={"granted": True}).status_code == 200


def _file(app, kind: str, title: str, when: datetime) -> int:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        doc = VaultDocument(
            kind=kind,
            title=title,
            filename=f"{kind}.pdf",
            mime="application/pdf",
            size=1000,
            sha256=hashlib.sha256(title.encode()).hexdigest(),
            status="verified",
            created_at=when,
        )
        db.add(doc)
        db.commit()
        return doc.id


def test_mail_notice_becomes_a_task_that_closes_when_the_pdf_is_filed(
    world: TestClient, imap: FakeImapServer, app
) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: date(2026, 6, 15)  # hors saison de l'avis : seul le mail compte
    _link(world, imap)
    imap.add(make_mail(sender="DGFiP <ne-pas-repondre@dgfip.finances.gouv.fr>", subject="Votre avis d'impot sur le revenu est disponible", pdf=None))  # fmt: skip
    imap.add(make_mail(sender="Caf <ne-pas-repondre@caf.fr>", subject="Votre attestation de paiement", pdf=make_pdf(["Caf", "ATTESTATION DE PAIEMENT"]), filename="att.pdf"))  # fmt: skip
    imap.add(make_mail(sender="Caf <ne-pas-repondre@caf.fr>", subject="La lettre de la Caf", pdf=None))
    imap.add(make_mail(sender="Boutique <promo@shop.example>", subject="Votre avis compte", pdf=None))
    rep = world.post("/api/mail/scan").json()["report"]
    assert rep["scanned"] == 4 and rep["proposed"] == 1, "le PDF joint reste une proposition dans Papiers"
    view = world.get("/api/state-accounts").json()
    (task,) = view["tasks"]
    assert (task["body"], task["expected"], task["origin"]) == ("impots", "avis d'impôt sur le revenu", "mail")
    assert task["entry_url"] == "https://cfspart.impots.gouv.fr/" and "Documents" in task["path"]
    assert task["subject"] == "Votre avis d'impot sur le revenu est disponible" and task["self_closing"] is True
    due = date.fromisoformat(task["due"])
    assert due - date.fromisoformat(task["received_at"][:10]) == timedelta(days=dom.DUE_DAYS)
    bodies = {b["slug"]: b for b in view["bodies"]}
    assert list(bodies) == ["impots", "caf", "france_travail", "ameli", "retraite"]
    assert bodies["impots"]["status"] == "to_fetch" and bodies["impots"]["open_tasks"] == 1
    assert bodies["caf"]["status"] == "none" and bodies["caf"]["last_document"] is None
    # Relire la boîte ne recrée rien.
    imap.add(make_mail(sender="x <y@shop.example>", subject="z", pdf=None))
    world.post("/api/mail/scan")
    assert len(world.get("/api/state-accounts").json()["tasks"]) == 1
    # Le PDF déposé dans Papiers ferme la tâche ; l'organisme montre ce dernier document.
    doc_id = _file(app, "tax_notice", "Avis d'impôt 2026", datetime.now(UTC))
    view = world.get("/api/state-accounts").json()
    assert view["tasks"] == [] and view["recent"][0]["status"] == "done"
    assert view["recent"][0]["document_id"] == doc_id
    impots = next(b for b in view["bodies"] if b["slug"] == "impots")
    assert impots["status"] == "ok" and impots["last_document"]["title"] == "Avis d'impôt 2026"


def test_seasonal_reminders_and_renewal(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: date(2026, 9, 2)
    with app.state.sessionmaker() as db:
        from app.models import Account

        acc = Account(name="Compte courant", kind="checking", balance_cents=0)
        db.add(acc)
        db.flush()
        db.add(
            Transaction(
                account_id=acc.id,
                booking_date=date(2026, 8, 5),
                amount_cents=25_829,
                raw_label="CAF DU LOIRET PRIME ACTIVITE",
                aid_kind="caf_ppa",
                fingerprint="t-caf-1",
            )
        )
        db.commit()
    _file(app, "caf", "Attestation CAF 2025", datetime(2025, 6, 1, tzinfo=UTC))
    view = world.get("/api/state-accounts").json()
    tasks = {t["expected"]: t for t in view["tasks"]}
    assert set(tasks) == {"avis d'impôt sur le revenu 2026", "attestation de paiement CAF de 08/2026"}
    avis = tasks["avis d'impôt sur le revenu 2026"]
    assert avis["late"] is True and avis["due"] == "2026-08-31" and avis["origin"] == "season"
    caf = next(b for b in view["bodies"] if b["slug"] == "caf")
    assert caf["status"] == "to_fetch" and caf["last_document"]["day"] == "2025-06-01"
    # « Je l'ai » ou « pas pour moi » : la tâche se ferme ; ce n'est pas recréé.
    r = world.post(f"/api/state-accounts/tasks/{avis['id']}", json={"status": "dismissed"})
    assert r.status_code == 200 and len(r.json()["tasks"]) == 1
    assert world.post(f"/api/state-accounts/tasks/{avis['id']}", json={"status": "done"}).status_code == 404
    assert len(world.get("/api/state-accounts").json()["tasks"]) == 1
    # Sans tâche ouverte, une attestation de plus d'un an est « à renouveler ».
    with app.state.sessionmaker() as db:
        for t in db.query(DocumentTask).all():
            t.status = "done"
        db.commit()
    caf = next(b for b in world.get("/api/state-accounts").json()["bodies"] if b["slug"] == "caf")
    assert caf["status"] == "renew"
