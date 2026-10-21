from datetime import timedelta

import pyotp
import pytest

from app.config import Settings
from app.db import utcnow
from app.models import User
from app.security.auth import (
    SlidingWindowLimiter,
    consume_recovery_code,
    hash_password,
    is_locked,
    new_recovery_codes,
    password_problems,
    register_failure,
    register_success,
    verify_password,
    verify_totp,
)


def test_password_policy() -> None:
    assert password_problems("court", "micka")
    assert password_problems("micka-est-super-long", "micka")  # contient l'identifiant
    assert password_problems("aaaaaaaaaaaaaa", "x")  # trop prévisible
    assert password_problems("motdepasse123", "x")
    assert password_problems("a" * 300, "x")
    assert password_problems("cheval correct pile agrafe", "micka") == []


def test_hash_and_verify() -> None:
    h = hash_password("une phrase de passe")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "une phrase de passe") == (True, False)
    assert verify_password(h, "mauvais")[0] is False
    assert verify_password("pas-un-hash", "x") == (False, False)


def test_totp_window_and_replay() -> None:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    now = 1_800_000_000.0
    code = totp.at(now)
    step = verify_totp(secret, code, last_step=0, now=now)
    assert step == int(now // 30)
    assert verify_totp(secret, code, last_step=step, now=now) is None  # rejeu refusé
    assert verify_totp(secret, totp.at(now - 30), last_step=0, now=now) is not None  # dérive d'horloge tolérée
    assert verify_totp(secret, totp.at(now - 90), last_step=0, now=now) is None
    assert verify_totp(secret, "12345", last_step=0, now=now) is None
    assert verify_totp(secret, f"{code[:3]} {code[3:]}", last_step=0, now=now) is not None  # espaces tolérés


def test_recovery_codes_single_use() -> None:
    codes, stored = new_recovery_codes(3)
    assert len(codes) == 3 and all(len(c) == 14 for c in codes)
    ok, stored2 = consume_recovery_code(stored, codes[0].upper())
    assert ok
    ok_again, _ = consume_recovery_code(stored2, codes[0])
    assert not ok_again
    assert consume_recovery_code(stored2, "zzzz-zzzz-zzzz") == (False, stored2)


def test_lockout_backoff() -> None:
    s = Settings(env="test", master_key="x", login_max_attempts=3, lockout_minutes=10)
    u = User(username="u", password_hash="h", failed_attempts=0)
    now = utcnow()
    for _ in range(2):
        register_failure(u, s, now)
    assert not is_locked(u, now)
    register_failure(u, s, now)
    assert is_locked(u, now) and u.locked_until == now + timedelta(minutes=10)
    register_failure(u, s, now)
    assert u.locked_until == now + timedelta(minutes=20)  # exponentiel
    for _ in range(20):
        register_failure(u, s, now)
    assert u.locked_until == now + timedelta(hours=24)  # plafonné
    register_success(u)
    assert not is_locked(u, now) and u.failed_attempts == 0


def test_sliding_window_limiter() -> None:
    lim = SlidingWindowLimiter(max_events=3, window_seconds=10)
    assert all(lim.hit("ip", now=t) for t in (0, 1, 2))
    assert not lim.hit("ip", now=3)
    assert lim.hit("other", now=3)
    assert lim.hit("ip", now=10.5)  # la fenêtre a glissé
    lim.reset()
    assert lim.hit("ip", now=11)


@pytest.mark.parametrize("hours", [1, 5])
def test_settings_rejects_aggressive_sync(hours: int) -> None:
    with pytest.raises(ValueError, match="DSP2"):
        Settings(env="test", sync_interval_hours=hours)


def test_settings_prod_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(env="prod", public_url="http://bank.lan")


def test_master_key_parsing() -> None:
    import base64
    import os

    from app.config import ConfigError

    k1, k2 = (base64.b64encode(os.urandom(32)).decode() for _ in range(2))
    s = Settings(env="test", master_key=f"k2:{k2},k1:{k1}")
    assert list(s.master_keys) == ["k2", "k1"]
    with pytest.raises(ConfigError):
        _ = Settings(env="test", master_key="pas-du-base64!").master_keys
    with pytest.raises(ConfigError):
        _ = Settings(env="test", master_key=base64.b64encode(b"court").decode()).master_keys
    with pytest.raises(ConfigError):
        _ = Settings(env="test").master_keys
    with pytest.raises(ConfigError):
        _ = Settings(env="test", master_key=f"k1:{k1},k1:{k2}").master_keys
