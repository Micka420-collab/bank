import os
import time

import pytest
from sqlalchemy import text

from app.security import audit
from app.security.crypto import CryptoError, Keyring, install_keyring


def test_encrypt_roundtrip_and_randomized(keyring: Keyring) -> None:
    a = keyring.encrypt("FR7630004000031234567890143", "account.iban")
    b = keyring.encrypt("FR7630004000031234567890143", "account.iban")
    assert a != b  # nonce aléatoire : deux chiffrés du même IBAN ne se ressemblent pas
    assert a.startswith("enc:k1:")
    assert keyring.decrypt(a, "account.iban") == "FR7630004000031234567890143"


def test_context_binding_blocks_column_swap(keyring: Keyring) -> None:
    token = keyring.encrypt("secret", "user.totp_secret")
    with pytest.raises(CryptoError):
        keyring.decrypt(token, "account.iban")


def test_tampering_detected(keyring: Keyring) -> None:
    token = keyring.encrypt("hello", "ctx")
    prefix, kid, payload = token.split(":")
    flipped = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    with pytest.raises(CryptoError):
        keyring.decrypt(f"{prefix}:{kid}:{flipped}", "ctx")
    with pytest.raises(CryptoError):
        keyring.decrypt("garbage", "ctx")
    with pytest.raises(CryptoError):
        keyring.decrypt("enc:unknown:AAAA", "ctx")


def test_key_rotation() -> None:
    old_key, new_key = os.urandom(32), os.urandom(32)
    old = Keyring({"k1": old_key})
    token = old.encrypt("donnée", "ctx")
    rotated = Keyring({"k2": new_key, "k1": old_key})
    assert rotated.decrypt(token, "ctx") == "donnée"
    assert rotated.needs_reencrypt(token)
    assert rotated.encrypt("x", "ctx").startswith("enc:k2:")


def test_blind_index(keyring: Keyring) -> None:
    a = keyring.blind_index("fr76 3000 4000 0312 3456 7890 143", "account.iban")
    b = keyring.blind_index("FR7630004000031234567890143", "account.iban")
    assert a == b and len(a) == 32
    assert keyring.blind_index("FR7630004000031234567890143", "other") != a
    assert Keyring({"k1": os.urandom(32)}).blind_index("FR7630004000031234567890143", "account.iban") != a


def test_signed_tokens(keyring: Keyring, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = keyring.sign_token({"uid": 1}, 60, "mfa")
    assert keyring.verify_token(tok, "mfa")["uid"] == 1
    with pytest.raises(CryptoError):
        keyring.verify_token(tok, "other-purpose")
    raw, mac = tok.split(".")
    with pytest.raises(CryptoError):
        keyring.verify_token(raw + "x." + mac, "mfa")
    with pytest.raises(CryptoError):
        keyring.verify_token("nope", "mfa")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 120)
    with pytest.raises(CryptoError):
        keyring.verify_token(tok, "mfa")


def test_audit_chain_detects_tampering(db) -> None:  # type: ignore[no-untyped-def]
    for i in range(5):
        audit.record(db, "micka", "test.event", n=i, password="ne-doit-pas-apparaitre")
    db.commit()
    status = audit.verify_chain(db)
    assert status.ok and status.entries >= 5
    assert "ne-doit-pas-apparaitre" not in db.execute(text("select group_concat(details) from audit_log")).scalar()

    db.execute(text("update audit_log set details = :d where id = 3"), {"d": '{"n":999}'})
    db.commit()
    broken = audit.verify_chain(db)
    assert not broken.ok and broken.broken_at == 3 and broken.reason == "contenu modifié"


def test_audit_chain_detects_deletion(db) -> None:  # type: ignore[no-untyped-def]
    for i in range(4):
        audit.record(db, "micka", "test.event", n=i)
    db.commit()
    db.execute(text("delete from audit_log where id = 2"))
    db.commit()
    status = audit.verify_chain(db)
    assert not status.ok and status.broken_at == 3


def test_audit_chain_cannot_be_forged_without_key(db) -> None:  # type: ignore[no-untyped-def]
    audit.record(db, "micka", "a")
    db.commit()
    # Un attaquant qui recalcule la chaîne avec une autre clé ne passe pas la vérification.
    install_keyring(Keyring({"k1": os.urandom(32)}))
    assert not audit.verify_chain(db).ok
