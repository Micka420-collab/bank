from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.crypto import Keyring, install_keyring

FIXTURES = Path(__file__).parent / "fixtures"
SETUP_TOKEN = "setup-token-for-tests-123"
USERNAME = "micka"
PASSWORD = "une phrase de passe tres longue 42"


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def keyring() -> Keyring:
    kr = Keyring({"k1": os.urandom(32)})
    install_keyring(kr)
    return kr


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path / 'bank.db'}",
        master_key=_key(),
        setup_token=SETUP_TOKEN,
        public_url="https://testserver",
        allowed_hosts=["testserver"],
        sync_interval_hours=0,
        fiscal_engine_url="http://fiscal.invalid",
    )


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def db(app, client) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        yield s


def do_setup(client: TestClient) -> None:
    r = client.post("/api/setup", json={"setup_token": SETUP_TOKEN, "username": USERNAME, "password": PASSWORD})
    assert r.status_code == 201, r.text


def do_login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["mfa_required"] is False
    client.headers["x-csrf-token"] = client.cookies["bank_csrf"]


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    do_setup(client)
    do_login(client)
    return client


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
