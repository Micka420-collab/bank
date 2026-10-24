"""Partage d'un papier par lien : mot de passe exigé, un seul téléchargement, filigrane au nom du destinataire, lien
expiré, consommé ou révoqué indiscernable, débit limité, journal et audit, copie effacée."""

from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from typing import Any

import pypdf
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.db import utcnow
from app.domain import pdf_text, reading
from app.models import AuditLog, VaultDocument, VaultShare
from app.services import shares
from app.services.vault import VaultError
from tests.conftest import PASSWORD
from tests.pdfgen import encrypt_pdf, make_pdf
from tests.vaultgen import text_image

DAY = date(2026, 10, 15)
LEASE = ["QUITTANCE DE LOYER", "Loyer de septembre 2026", "Total : 650,00 €", "Bailleur : SCI Les Tilleuls"]


@pytest.fixture
def owner(app, auth_client: TestClient) -> Any:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: DAY
    yield auth_client
    app.dependency_overrides.clear()


def _upload(client: TestClient, name: str, data: bytes) -> int:
    r = client.post("/api/vault/upload", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 201, r.text
    return int(r.json()["documents"][0]["id"])


def _share(client: TestClient, doc_id: int, recipient: str = "Agence Dupont", hours: int = 48) -> Any:
    return client.post(
        f"/api/vault/{doc_id}/shares", json={"recipient": recipient, "hours": hours, "password": PASSWORD}
    )


def _token(link: str) -> str:
    return link.split("#/recu/", 1)[1]


def _open(client: TestClient, token: str) -> Any:
    return client.post("/api/share/open", json={"token": token})


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)


def test_a_link_is_created_only_with_the_password(app, owner: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "quittance.pdf", make_pdf(LEASE))
    body = {"recipient": "Agence Dupont", "hours": 24}
    assert owner.post(f"/api/vault/{doc_id}/shares", json={**body, "password": "faux"}).status_code == 403
    assert owner.post(f"/api/vault/{doc_id}/shares", json=body).status_code == 422
    assert owner.post(f"/api/vault/{doc_id}/shares", json={**body, "hours": 5, "password": PASSWORD}).status_code == 422
    assert _share(owner, doc_id, recipient="​ \x07").status_code == 422  # rien d'imprimable
    assert _share(owner, 9999).status_code == 404
    assert owner.get("/api/vault/9999/shares").status_code == 404
    anonymous = TestClient(app, base_url="https://testserver")  # sans session : rien d'autre que l'ouverture
    assert anonymous.get(f"/api/vault/{doc_id}/shares").status_code == 401
    assert anonymous.post(f"/api/vault/{doc_id}/shares", json={**body, "password": PASSWORD}).status_code == 401
    with app.state.sessionmaker() as db:
        assert db.scalars(select(VaultShare)).all() == []
        assert "vault.share_denied" in [a.action for a in db.scalars(select(AuditLog))]


def test_one_download_with_the_watermark_then_nothing(app, owner: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "quittance-loyer-septembre.pdf", make_pdf(LEASE, pages=2))
    r = _share(owner, doc_id, recipient="  Agence\tDupont  ")
    assert r.status_code == 201, r.text
    link, share = r.json()["link"], r.json()["share"]
    token = _token(link)
    assert link.startswith("https://testserver/#/recu/") and len(token) >= 40  # 256 bits, dans le fragment
    assert "quittance" not in link and f"/{doc_id}" not in link  # le lien ne dit rien du document
    assert share["recipient"] == "Agence Dupont" and share["state"] == "active"
    with app.state.sessionmaker() as db:
        row = db.get(VaultShare, share["id"])
        assert row is not None and row.token_hash != token and token not in row.token_hash
        assert row.payload is not None and row.payload.startswith(b"BKV1")  # copie chiffrée
        assert row.expires_at - row.created_at == timedelta(hours=48)

    anonymous = TestClient(app, base_url="https://testserver")  # le destinataire n'a pas de session
    got = _open(anonymous, token)
    assert got.status_code == 200 and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"] == 'attachment; filename="document.pdf"'
    assert got.headers["cache-control"] == "no-store" and got.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in got.headers["x-robots-tag"]
    text = _text(got.content)
    assert "Transmis à Agence Dupont le 15/10/2026 par Bank — usage unique" in text
    assert "QUITTANCE DE LOYER" in text and len(pypdf.PdfReader(io.BytesIO(got.content)).pages) == 2

    again = _open(anonymous, token)
    assert again.status_code == 404
    journal = owner.get(f"/api/vault/{doc_id}/shares").json()["shares"]
    assert [s["state"] for s in journal] == ["used"] and journal[0]["used_at"]
    with app.state.sessionmaker() as db:
        row = db.get(VaultShare, share["id"])
        assert row is not None and row.payload is None  # copie effacée dès le téléchargement
        actions = [a.action for a in db.scalars(select(AuditLog))]
        assert {"vault.share_create", "vault.share_download", "vault.share_refused"} <= set(actions)
        details = " ".join(a.details for a in db.scalars(select(AuditLog).where(AuditLog.action.like("vault.share%"))))
        assert token not in details and "Dupont" not in details  # ni jeton ni destinataire au journal


def test_expired_revoked_used_and_unknown_look_the_same(
    app, owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "quittance.pdf", make_pdf(LEASE))
    used, revoked, expired = (_token(_share(owner, doc_id, hours=h).json()["link"]) for h in (48, 24, 1))
    anonymous = TestClient(app, base_url="https://testserver")
    assert _open(anonymous, used).status_code == 200
    used_id, revoked_id, _ = sorted(s["id"] for s in owner.get(f"/api/vault/{doc_id}/shares").json()["shares"])
    r = owner.post(f"/api/vault/{doc_id}/shares/{revoked_id}/revoke")
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    assert owner.post(f"/api/vault/{doc_id}/shares/{revoked_id}/revoke").json()["state"] == "revoked"  # idempotent
    assert owner.post(f"/api/vault/{doc_id + 1}/shares/{revoked_id}/revoke").status_code == 404
    assert owner.post(f"/api/vault/{doc_id}/shares/{used_id}/revoke").json()["state"] == "used"

    later = utcnow() + timedelta(hours=2)
    monkeypatch.setattr("app.api.shares.utcnow", lambda: later)
    responses = [_open(anonymous, t) for t in (used, revoked, expired, "inconnu", "x" * 500, "")]
    assert {r.status_code for r in responses} == {404}
    assert len({r.content for r in responses}) == 1  # un seul corps, quel que soit le refus
    states = {s["id"]: s["state"] for s in owner.get(f"/api/vault/{doc_id}/shares").json()["shares"]}
    assert sorted(states.values()) == ["expired", "revoked", "used"]
    with app.state.sessionmaker() as db:
        assert shares.housekeeping(db, now=later) == {"shares_purged": 1}  # la copie du lien expiré
        db.commit()
        assert all(s.payload is None for s in db.scalars(select(VaultShare)))
        assert "vault.share_revoke" in [a.action for a in db.scalars(select(AuditLog))]


def test_two_simultaneous_downloads_cannot_both_succeed(
    app, owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "quittance.pdf", make_pdf(LEASE))
    token = _token(_share(owner, doc_id).json()["link"])
    with app.state.sessionmaker() as db:  # l'autre ouverture a déjà consommé le lien…
        row = db.scalars(select(VaultShare)).one()
        row.used_at = utcnow()
        db.commit()
    monkeypatch.setattr(shares, "state", lambda *_: "active")  # …mais celle-ci l'a lu juste avant
    with app.state.sessionmaker() as db:
        assert shares.open_link(db, token, now=utcnow(), ip="1.2.3.4") is None  # la mise à jour conditionnelle tranche


def test_opening_links_is_rate_limited(app, owner: TestClient) -> None:  # type: ignore[no-untyped-def]
    anonymous = TestClient(app, base_url="https://testserver")
    codes = [_open(anonymous, f"essai-{i}").status_code for i in range(12)]
    assert codes[:10] == [404] * 10 and codes[10:] == [429, 429]


def test_a_photo_is_shared_as_a_watermarked_pdf(app, owner: TestClient) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "IMG_1.png", text_image(["Attestation"], fmt="PNG"))
    token = _token(_share(owner, doc_id, recipient="Mme Lefèvre (CAF)").json()["link"])
    got = _open(TestClient(app, base_url="https://testserver"), token)
    assert got.status_code == 200 and got.content.startswith(b"%PDF-")
    assert "Transmis à Mme Lefèvre (CAF) le 15/10/2026" in _text(got.content)


def test_what_cannot_be_shared(app, owner: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    doc_id = _upload(owner, "quittance.pdf", make_pdf(LEASE))

    def broken(*_: Any) -> bytes:
        raise reading.ReadingError("Filigrane impossible")

    monkeypatch.setattr(reading, "stamp_pdf", broken)
    r = _share(owner, doc_id)
    assert r.status_code == 422 and r.json()["detail"] == "Filigrane impossible"
    monkeypatch.undo()
    with app.state.sessionmaker() as db:
        doc = db.get(VaultDocument, doc_id)
        assert doc is not None
        doc.status = "processing"
        db.commit()
    assert _share(owner, doc_id).status_code == 409
    with app.state.sessionmaker() as db:
        doc = db.get(VaultDocument, doc_id)
        assert doc is not None
        doc.status = "to_review"
        db.commit()
    token = _token(_share(owner, doc_id).json()["link"])
    assert owner.delete(f"/api/vault/{doc_id}").status_code == 204  # le document effacé emporte ses liens
    assert _open(TestClient(app, base_url="https://testserver"), token).status_code == 404


def test_recipient_names_are_cleaned_for_the_watermark() -> None:
    assert shares.clean_recipient("  Jean​  Dupont\n\x1b[31m ") == "Jean Dupont [31m"
    assert shares.clean_recipient("Ｍｍｅ Ｌｅｆèｖｒｅ 🏠") == "Mme Lefèvre"  # NFKC, emoji hors police
    assert len(shares.clean_recipient("A" * 500)) == shares.RECIPIENT_MAX
    with pytest.raises(VaultError):
        shares.clean_recipient("🏠")
    assert shares.watermark("X", date(2026, 1, 2)) == "Transmis à X le 02/01/2026 par Bank — usage unique"


def test_the_watermark_is_written_in_the_confined_child(monkeypatch: pytest.MonkeyPatch) -> None:
    stamped = reading.stamp_pdf(make_pdf(LEASE), "Transmis à (Paul) \\ le 01/01/2026")
    assert "Transmis à (Paul) \\ le 01/01/2026" in _text(stamped)
    with pytest.raises(reading.ReadingError):
        reading.stamp_pdf(b"%PDF-1.4 garbage", "x")
    with pytest.raises(reading.ReadingError):
        reading.stamp_pdf(make_pdf(["page"], pages=pdf_text.MAX_STAMP_PAGES + 1), "x")
    with pytest.raises(ValueError, match="invalide"):
        pdf_text.unpack_stamp(b"nope")
    with pytest.raises(ValueError, match="trop long"):
        pdf_text.unpack_stamp(pdf_text.STAMP_MAGIC + (10_000).to_bytes(4, "big"))
    rotated = make_pdf(["tournée"]).replace(b"/MediaBox", b"/Rotate 90 /MediaBox")
    assert "Transmis" in _text(pdf_text.stamp(rotated, "Transmis à Z"))

    monkeypatch.setattr(pdf_text, "_confine", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["pdf_text.py", "stamp"])

    def run(data: bytes) -> tuple[int, bytes]:
        out = io.BytesIO()
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out))
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        code = pdf_text.main()
        sys.stdout.flush()
        return code, out.getvalue()

    code, out = run(pdf_text.pack_stamp("Transmis à Y", make_pdf(LEASE)))
    assert code == pdf_text.EXIT_OK and out.startswith(b"%PDF-")
    code, _ = run(pdf_text.pack_stamp("x", encrypt_pdf(make_pdf(LEASE), "secret")))
    assert code == pdf_text.EXIT_ENCRYPTED
    code, out = run(pdf_text.pack_stamp("x", b"GIF89a"))
    assert code == pdf_text.EXIT_UNREADABLE and out == b""
