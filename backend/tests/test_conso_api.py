"""Client Conso API : en-têtes, découpage des fenêtres, erreurs lisibles, format inconnu."""

import gzip
import logging
import time
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
import pytest

from app.domain.energy import DailyReading
from app.mcp_server import silence_sdk_logs
from app.providers import conso_api
from app.providers.conso_api import (
    MAX_BODY_BYTES,
    MAX_DAYS_PER_CALL,
    ConsoApiClient,
    ConsoApiError,
    looks_like_token,
)

TOKEN = "a" * 40 + "." + "b" * 40 + "." + "c" * 40


def client_with(handler):  # type: ignore[no-untyped-def]
    return ConsoApiClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_token_shape() -> None:
    assert looks_like_token(TOKEN) and not looks_like_token("bnk_abc") and not looks_like_token("a.b.c")


def test_request_shape_and_chunking() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        start = date.fromisoformat(req.url.params["dateDebut"])
        end = date.fromisoformat(req.url.params["dateFin"])
        rows = [
            {"value": "1000", "date": (start.fromordinal(start.toordinal() + i)).isoformat()}
            for i in range((end - start).days)
        ]
        return httpx.Response(200, json={"reading_type": {"unit": "Wh"}, "interval_reading": rows})

    rows = client_with(handler).daily_consumption("12345678901234", date(2026, 1, 1), date(2027, 1, 1))
    assert len(rows) == 365 and rows[0].day == date(2026, 1, 1) and rows[-1].day == date(2026, 12, 31)
    assert len(seen) == 3 and all(
        (date.fromisoformat(r.url.params["dateFin"]) - date.fromisoformat(r.url.params["dateDebut"])).days
        <= MAX_DAYS_PER_CALL
        for r in seen
    )
    first = seen[0]
    assert first.url.path == "/api/consommation_quotidienne" and first.url.params["pointId"] == "12345678901234"
    assert first.headers["authorization"] == f"Bearer {TOKEN}" and "Bank" in first.headers["user-agent"]


@pytest.mark.parametrize(
    ("status", "permanent", "needle"),
    [
        (401, True, "Jeton"),
        (400, True, "PRM"),
        (429, False, "Trop"),
        (503, False, "indisponible"),
        (418, False, "inattendue"),
    ],
)
def test_http_errors_are_explicit(status: int, permanent: bool, needle: str) -> None:
    c = client_with(lambda req: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(ConsoApiError) as exc:
        c.daily_consumption("12345678901234", date(2026, 10, 1), date(2026, 10, 3))
    assert exc.value.permanent is permanent and needle in str(exc.value) and exc.value.status == status


def test_network_and_format_errors() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ConsoApiError, match="injoignable"):
        client_with(down).daily_consumption("12345678901234", date(2026, 10, 1), date(2026, 10, 3))
    with pytest.raises(ConsoApiError, match="non JSON"):
        client_with(lambda req: httpx.Response(200, text="<html>")).daily_consumption(
            "1" * 14, date(2026, 10, 1), date(2026, 10, 3)
        )
    with pytest.raises(ConsoApiError, match="Format de réponse non reconnu") as exc:
        client_with(lambda req: httpx.Response(200, json={"message": "ok"})).daily_consumption(
            "1" * 14, date(2026, 10, 1), date(2026, 10, 3)
        )
    assert exc.value.permanent


# ------------------------------------------------------------------ réponses hostiles (revue de sécurité)
def test_oversized_body_is_cut_even_when_compressed() -> None:
    """Bombe gzip : 40 Mo de zéros pèsent quelques dizaines de Ko compressés. On compte les octets décompressés."""
    payload = b'{"interval_reading": [' + b'{"date": "2026-10-01", "value": 1},' * 1_500_000 + b"]}"
    assert len(payload) > MAX_BODY_BYTES
    compressed = gzip.compress(payload, compresslevel=9)
    assert len(compressed) < MAX_BODY_BYTES // 4

    def bomb(req: httpx.Request) -> httpx.Response:
        assert req.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=compressed, headers={"Content-Encoding": "gzip"})

    with pytest.raises(ConsoApiError, match="volumineuse") as exc:
        client_with(bomb).daily_consumption("1" * 14, date(2026, 10, 1), date(2026, 10, 3))
    assert exc.value.permanent


def test_slow_drip_response_hits_the_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conso_api, "MAX_SECONDS_PER_CALL", 0.2)

    def drip() -> Iterator[bytes]:
        yield b'{"interval_reading": ['
        for _ in range(50):
            time.sleep(0.02)
            yield b'{"date": "2026-10-01", "value": 1},'
        yield b"]}"

    c = client_with(lambda req: httpx.Response(200, content=drip()))
    with pytest.raises(ConsoApiError, match="trop lente") as exc:
        c.daily_consumption("1" * 14, date(2026, 10, 1), date(2026, 10, 3))
    assert not exc.value.permanent


def test_readings_outside_the_requested_window_are_dropped_and_days_deduplicated() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        start = date.fromisoformat(req.url.params["dateDebut"])
        return httpx.Response(
            200,
            json={
                "reading_type": {"unit": "Wh"},
                "interval_reading": [
                    {"date": "9999-12-31", "value": 1},  # futur absurde : aurait fait déborder les calculs
                    {"date": "2026-12-01", "value": 1},  # après la fenêtre demandée
                    {"date": "2026-01-01", "value": 1},  # avant la fenêtre demandée
                    {"date": start.isoformat(), "value": 100},
                    {"date": start.isoformat(), "value": 200},  # doublon : la dernière valeur l'emporte
                    {"date": (start + timedelta(days=1)).isoformat(), "value": "Infinity"},
                ],
            },
        )

    rows = client_with(handler).daily_consumption("1" * 14, date(2026, 10, 1), date(2026, 10, 3))
    assert rows == [DailyReading(date(2026, 10, 1), 200)]


def test_deeply_nested_json_is_a_clean_error() -> None:
    body = "[" * 100_000 + "]" * 100_000  # au-delà de la récursion de Python : RecursionError dans json
    with pytest.raises(ConsoApiError, match="non JSON") as exc:
        client_with(lambda req: httpx.Response(200, content=body.encode())).daily_consumption(
            "1" * 14, date(2026, 10, 1), date(2026, 10, 3)
        )
    assert exc.value.permanent


def test_prm_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    """httpx journalise « HTTP Request: GET <url complète> » au niveau INFO : le PRM y serait."""
    caplog.set_level(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.NOTSET)  # état d'origine, avant la création de l'application
    silence_sdk_logs()
    prm = "98765432109876"
    c = client_with(lambda req: httpx.Response(200, json={"interval_reading": [{"date": "2026-10-01", "value": 1}]}))
    c.daily_consumption(prm, date(2026, 10, 1), date(2026, 10, 3))
    assert prm not in caplog.text and TOKEN not in caplog.text
