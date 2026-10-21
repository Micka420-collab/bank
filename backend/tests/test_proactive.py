"""Bank prend des initiatives : cartes du jour (API), planning de travail relié à la paie (API + prévision)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain.recurring import add_months
from app.models import AuditLog, CategoryRule, InitiativeState, WorkShift
from app.services import planning
from tests.scenario import TODAY, seed_household
from tests.test_mcp import make_agent


@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def card_keys(client: TestClient, limit: int = 20) -> list[str]:
    r = client.get(f"/api/initiatives?limit={limit}")
    assert r.status_code == 200
    return [c["key"] for c in r.json()["cards"]]


# ------------------------------------------------------------------ cartes
def test_cards_reflect_what_bank_read_in_the_statements(world: TestClient) -> None:
    r = world.get("/api/initiatives?limit=20")
    data = r.json()
    keys = [c["key"] for c in data["cards"]]
    assert data["total"] >= 6 and len(keys) == min(20, data["total"])
    assert "fact:net_monthly_income" in keys and "fact:housing_cost" in keys
    salary = next(c for c in data["cards"] if c["key"] == "fact:net_monthly_income")
    assert salary["title"] == "Ton salaire net, c'est bien 2 150 € par mois ?" and "BRGM" not in salary["title"]
    assert salary["actions"][0]["effect"]["value"] == 215_000 and salary["input"] == {"kind": "money"}
    assert any(k.startswith("series:") and "NETFLIX" in k for k in keys)
    assert not any("FONCIA" in k or "COFIDIS" in k for k in keys)  # loyer et crédit : couverts par les faits
    assert "planning:connect" in keys and "security:totp" in keys
    # Priorité : les faits à confirmer avant les abonnements, les abonnements avant les questions libres.
    assert keys.index("fact:net_monthly_income") < min(i for i, k in enumerate(keys) if k.startswith("series:"))
    assert all(keys.index(k) > keys.index("fact:housing_cost") for k in keys if k.startswith("ask:"))


def test_one_tap_confirms_a_fact_and_the_agent_sees_it_verified(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = world.post("/api/initiatives/fact:net_monthly_income/act", json={"action": "yes"})
    assert r.status_code == 200 and "agent" in r.json()["result"]
    assert "fact:net_monthly_income" not in [c["key"] for c in r.json()["cards"]]
    ob = world.get("/api/onboarding").json()
    assert ob["answers"]["net_monthly_income"]["value"] == 215_000 and ob["answers"]["net_monthly_income"][
        "source"
    ].startswith("user:")
    # Corriger avec une valeur saisie.
    r = world.post("/api/initiatives/fact:housing_cost/act", json={"action": "fix", "value": 67_000})
    assert r.status_code == 200
    assert world.get("/api/onboarding").json()["answers"]["housing_cost"]["value"] == 67_000
    with app.state.sessionmaker() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.action == "initiative.act"))]
    assert len(actions) == 2


def test_snooze_and_dismiss_are_remembered_and_a_stale_card_is_refused(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    keys = card_keys(world)
    ask = next(k for k in keys if k.startswith("ask:"))
    assert world.post(f"/api/initiatives/{ask}/act", json={"action": "snooze"}).status_code == 200
    assert ask not in card_keys(world)
    with app.state.sessionmaker() as db:
        st = db.get(InitiativeState, ask)
        assert st is not None and st.status == "snoozed" and st.until == TODAY + timedelta(days=3)
    # Après l'échéance, la question revient.
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=3)
    assert ask in card_keys(world)
    app.dependency_overrides[deps.today] = lambda: TODAY
    assert world.post("/api/initiatives/security:totp/act", json={"action": "snooze"}).status_code == 200
    assert "security:totp" not in card_keys(world)
    # Une carte absente ou une action qui n'est pas la sienne : refus explicite, rien n'est appliqué.
    assert world.post("/api/initiatives/fact:net_monthly_income/act", json={"action": "dismiss"}).status_code == 422
    assert world.post("/api/initiatives/nope:x/act", json={"action": "yes"}).status_code == 422
    assert world.post(f"/api/initiatives/{'k' * 200}/act", json={"action": "yes"}).status_code == 422


def test_acknowledging_a_subscription_and_classifying_a_merchant(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    keys = card_keys(world)
    netflix = next(k for k in keys if k.startswith("series:") and "NETFLIX" in k)
    r = world.post(f"/api/initiatives/{netflix}/act", json={"action": "ended"})
    assert r.status_code == 200 and "attendrai" in r.json()["result"]
    with app.state.sessionmaker() as db:
        assert db.get(InitiativeState, netflix).note == "terminé"  # type: ignore[union-attr]
    # Marchand non classé : on crée une opération non classée répétée, la carte apparaît, un geste classe tout.
    from app.importers.base import ParsedTransaction
    from app.models import Account
    from app.services.ingest import ingest

    with app.state.sessionmaker() as db:
        acc = db.scalar(select(Account).where(Account.name == "Compte courant"))
        rows = [ParsedTransaction(TODAY - timedelta(days=i * 9), -2_000 - i, "CB ZZTOP GARAGE") for i in range(4)]
        ingest(db, acc, rows, provider="file")  # type: ignore[arg-type]
        db.commit()
    card = next(
        c for c in world.get("/api/initiatives?limit=20").json()["cards"] if c["key"] == "merchant:ZZTOP GARAGE"
    )
    assert card["input"]["kind"] == "category" and any(o["value"] == "transport" for o in card["input"]["options"])
    assert (
        world.post(
            "/api/initiatives/merchant:ZZTOP GARAGE/act", json={"action": "classify", "value": "nope"}
        ).status_code
        == 422
    )
    r = world.post("/api/initiatives/merchant:ZZTOP GARAGE/act", json={"action": "classify", "value": "transport"})
    assert r.status_code == 200 and "4 opération" in r.json()["result"]
    page = world.get("/api/transactions?q=ZZTOP").json()
    assert all(t["category"] == "transport" for t in page["items"]) and len(page["items"]) == 4
    with app.state.sessionmaker() as db:
        assert db.scalar(select(CategoryRule).where(CategoryRule.pattern == "ZZTOP GARAGE")) is not None


def test_month_review_sets_the_savings_goal(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: date(2026, 10, 5)  # première semaine : bilan de septembre
    cards = world.get("/api/initiatives?limit=20").json()["cards"]
    review = next(c for c in cards if c["key"] == "review:2026-09")
    assert review["kind"] == "question" and review["title"].startswith("Septembre : il t'est resté 539,46 €")
    goal = review["actions"][0]["effect"]["value"]
    assert goal == 26_000  # moitié de ce qu'il reste en moyenne sur 3 mois, arrondi à 10 €
    assert world.post("/api/initiatives/review:2026-09/act", json={"action": "other", "value": -1}).status_code == 422
    r = world.post("/api/initiatives/review:2026-09/act", json={"action": "yes"})
    assert r.status_code == 200 and "260 €" in r.json()["result"]
    assert world.get("/api/budget").json()["settings"]["savings_goal_cents"] == 26_000
    assert "review:2026-09" not in [c["key"] for c in r.json()["cards"]]
    # En novembre, octobre (sans salaire dans ce scénario) est déficitaire : un constat, pas une question.
    app.dependency_overrides[deps.today] = lambda: date(2026, 11, 2)
    neg = next(c for c in world.get("/api/initiatives?limit=20").json()["cards"] if c["key"] == "review:2026-10")
    assert neg["kind"] == "notice" and "de plus que reçu" in neg["title"]


def test_agent_dossier_carries_what_bank_wants_to_ask(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    dossier = agent.ok("get_dossier")
    asks = dossier["bank_asks"]
    assert asks and all(set(a) >= {"key", "kind", "title"} for a in asks) and len(asks) <= 3
    assert not any(a["topic"] == "securite" for a in asks)


# ------------------------------------------------------------------ planning de travail
def ics(*events: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//\nX-WR-CALNAME:Boulot\n" + "\n".join(events) + "\nEND:VCALENDAR\n"
    ).encode()


def all_day(uid: str, start: date, days: int, title: str) -> str:
    end = start + timedelta(days=days)
    return f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;VALUE=DATE:{start:%Y%m%d}\nDTEND;VALUE=DATE:{end:%Y%m%d}\nSUMMARY:{title}\nEND:VEVENT"


def test_planning_file_connect_model_and_prediction_feed_the_month(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    before = world.get("/api/planning").json()
    assert before["connected"] is False and before["employer"] == "BRGM" and before["pay_day"] == 28
    assert before["usual_pay_cents"] == 215_000 and before["next_pay_date"] == "2026-10-28"
    # Deux astreintes en octobre (dont une à cheval sur novembre), une en novembre.
    data = ics(
        all_day("a1", date(2026, 10, 12), 7, "Astreinte réseau"),
        all_day("a2", date(2026, 10, 29), 5, "Astreinte"),
        all_day("a3", date(2026, 11, 16), 7, "Astreinte"),
        all_day("r", date(2026, 10, 20), 1, "Réunion d'équipe"),
    )
    r = world.post("/api/planning/file", files={"file": ("boulot.ics", io.BytesIO(data), "text/calendar")})
    assert r.status_code == 201, r.text
    st = r.json()
    assert (
        st["connected"] and st["plan"]["label"] == "Boulot" and st["imported"] == 20 and st["plan"]["source"] == "file"
    )
    assert st["model"]["base_cents"] == 215_000 and st["model"]["rules"] == []  # part fixe = paie habituelle
    assert st["predictions"] == []  # sans règle, rien à annoncer
    assert "planning:rules" in card_keys(world) and "planning:connect" not in card_keys(world)

    r = world.put(
        "/api/planning/model", json={"rules": [{"pattern": "astreinte", "unit": "per_event", "amount_cents": 18_000}]}
    )
    assert r.status_code == 200
    preds = r.json()["predictions"]
    assert preds[0]["pay_date"] == "2026-10-28" and preds[0]["amount_cents"] == 215_000 + 36_000
    assert preds[0]["extras"][0]["count"] == 2 and preds[0]["worked_days"] == 11  # 7 + 3 (fin octobre) + réunion
    assert preds[1]["pay_date"] == "2026-11-28" and preds[1]["amount_cents"] == 215_000 + 18_000
    # La prévision du mois utilise le montant prévu, pas la médiane.
    upcoming = world.get("/api/budget").json()["forecast"]["upcoming"]
    pay = next(e for e in upcoming if e["kind"] == "income" and "BRGM" in e["label"])
    assert pay["amount_cents"] == 251_000 and pay["date"] == "2026-10-28"
    keys = card_keys(world)
    assert any(k.startswith("planning:pay:20261028:251000") for k in keys) and "planning:rules" not in keys
    card = next(
        c for c in world.get("/api/initiatives?limit=20").json()["cards"] if c["key"].startswith("planning:pay:")
    )
    assert card["title"] == "Paie du 28 octobre : 2 510 € prévus (+360 €)"
    # L'agent voit la corrélation, pas l'agenda.
    agent = make_agent(app, world)
    dossier = agent.ok("get_dossier")
    assert dossier["work"]["next_pay_predicted_cents"] == 251_000 and dossier["work"]["employer"] == "BRGM"
    assert "upcoming" not in dossier["work"] and "Réunion" not in str(dossier)
    # Déconnexion : tout disparaît, la prévision revient à la médiane.
    assert world.delete("/api/planning").status_code == 204
    assert world.get("/api/planning").json()["connected"] is False
    with app.state.sessionmaker() as db:
        assert db.scalar(select(WorkShift)) is None
    pay = next(
        e
        for e in world.get("/api/budget").json()["forecast"]["upcoming"]
        if e["kind"] == "income" and "BRGM" in e["label"]
    )
    assert pay["amount_cents"] == 215_000


def test_planning_learns_rule_amounts_from_past_pays(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    """Historique : chaque mois, un nombre d'astreintes différent et une paie qui suit (base 2 000 € + 175 €/astreinte)."""
    from app.importers.base import ParsedTransaction
    from app.models import Account, Category, Transaction
    from app.services.ingest import ingest

    plan = {-6: 1, -5: 3, -4: 0, -3: 2, -2: 1, -1: 3}
    events = []
    with app.state.sessionmaker() as db:
        acc = db.scalar(select(Account).where(Account.name == "Compte courant"))
        # On remplace les salaires fixes du scénario par des paies qui dépendent des astreintes.
        salaire = db.scalar(select(Category).where(Category.slug == "salaire"))
        for t in db.scalars(select(Transaction).where(Transaction.category_id == salaire.id)):  # type: ignore[union-attr]
            db.delete(t)
        rows = []
        for offset, oncalls in plan.items():
            month_start = add_months(TODAY.replace(day=1), offset)
            for i in range(oncalls):
                events.append(all_day(f"a{offset}{i}", month_start + timedelta(days=2 + 7 * i), 3, "Astreinte"))
            rows.append(
                ParsedTransaction(
                    month_start.replace(day=28),
                    200_000 + 17_500 * oncalls + (13 if oncalls % 2 else -9),
                    "VIR SEPA RECU /DE BRGM /MOTIF SALAIRE",
                )
            )
        ingest(db, acc, rows, provider="file")  # type: ignore[arg-type]
        db.commit()
    events.append(all_day("next", add_months(TODAY.replace(day=1), 1) + timedelta(days=3), 3, "Astreinte"))
    r = world.post("/api/planning/file", files={"file": ("b.ics", io.BytesIO(ics(*events)), "text/calendar")})
    assert r.status_code == 201
    r = world.put(
        "/api/planning/model",
        json={"base_cents": 150_000, "rules": [{"pattern": "astreinte", "unit": "per_event", "amount_cents": 5_000}]},
    )
    st = r.json()
    learned = st["learned"]
    assert learned and learned["periods"] == 6 and learned["residual_cents"] < 100
    assert abs(learned["base_cents"] - 200_000) < 100 and abs(learned["rules"][0]["amount_cents"] - 17_500) < 100
    assert learned["rules"][0]["learned"] is True and "6 paies" in learned["note"]
    # Le modèle appris, fiable, sert à la prévision : octobre (aucune astreinte) puis novembre (une).
    preds = st["predictions"]
    assert abs(preds[0]["amount_cents"] - 200_000) < 300 and preds[0]["extras"][0]["learned"] is True
    assert abs(preds[1]["amount_cents"] - (200_000 + 17_500)) < 300


def test_planning_url_source_is_fetched_safely(app, world: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    data = ics(all_day("a1", date(2026, 10, 12), 2, "Astreinte"))
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("redirect.ics"):
            return httpx.Response(302, headers={"Location": "https://169.254.169.254/latest/meta-data"})
        if req.url.path.endswith("big.ics"):
            return httpx.Response(200, content=b"BEGIN:VCALENDAR\n" + b"X:" + b"a" * 2_100_000)
        if req.url.path.endswith("nope.ics"):
            return httpx.Response(404)
        return httpx.Response(200, content=data, headers={"Content-Type": "text/calendar"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(
        planning, "_resolve", lambda host: ["142.250.74.46"] if host == "calendar.google.com" else ["10.0.0.5"]
    )
    app.state.ics_fetcher = lambda url: planning.fetch_ics(url, http)

    for bad, needle in (
        ("http://calendar.google.com/ical/x/basic.ics", "https://"),
        ("https://localhost/basic.ics", "locale"),
        ("https://127.0.0.1/basic.ics", "locale"),
        ("https://[::1]/basic.ics", "locale"),
        ("https://nas.lan/basic.ics", "locale"),
        ("https://intranet.example.org/basic.ics", "locale"),  # résolu en 10.0.0.5
        ("https://user:pw@calendar.google.com/basic.ics", "identifiants"),
        ("https://calendar.google.com/redirect.ics", "redirige"),
        ("https://calendar.google.com/big.ics", "volumineux"),
        ("https://calendar.google.com/nope.ics", "404"),
    ):
        r = world.post("/api/planning/url", json={"url": bad})
        assert r.status_code == 422 and needle in r.json()["detail"], (bad, r.text)
    assert not any("169.254" in str(req.url) or "10.0.0.5" in str(req.url) for req in seen)

    r = world.post(
        "/api/planning/url", json={"url": "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics"}
    )
    assert r.status_code == 201 and r.json()["plan"]["source"] == "url" and r.json()["imported"] == 2
    assert seen[-1].headers["user-agent"].startswith("Bank/") and "private-abc" not in r.text
    # Synchronisation : relecture par l'adresse enregistrée (chiffrée), jamais renvoyée.
    r = world.post("/api/planning/sync")
    assert r.status_code == 200 and r.json()["imported"] == 2
    export = world.get("/api/planning").json()
    assert "private-abc" not in str(export)


def test_planning_rejects_bad_models_and_files(world: TestClient) -> None:
    assert world.put("/api/planning/model", json={"rules": []}).status_code == 422  # pas d'agenda relié
    r = world.post("/api/planning/file", files={"file": ("x.ics", io.BytesIO(b"garbage"), "text/calendar")})
    assert r.status_code == 422 and "illisible" in r.json()["detail"]
    data = ics(all_day("a1", date(2026, 10, 12), 2, "Astreinte"))
    assert (
        world.post("/api/planning/file", files={"file": ("x.ics", io.BytesIO(data), "text/calendar")}).status_code
        == 201
    )
    for bad in (
        {"rules": [{"pattern": "<b>", "unit": "per_event", "amount_cents": 1}]},
        {"rules": [{"pattern": "a", "unit": "per_minute", "amount_cents": 1}]},
        {"offset_months": 2, "rules": []},
        {"base_cents": -1, "rules": []},
        {"rules": [{"pattern": f"r{i}", "unit": "per_day", "amount_cents": 1} for i in range(9)]},
    ):
        assert world.put("/api/planning/model", json=bad).status_code == 422, bad
    assert world.post("/api/planning/sync").status_code == 200  # source fichier : rien à relire, pas d'erreur


def test_export_includes_planning_shifts_not_the_url(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import PASSWORD

    data = ics(all_day("a1", date(2026, 10, 12), 2, "Astreinte"))
    world.post("/api/planning/file", files={"file": ("x.ics", io.BytesIO(data), "text/calendar")})
    r = world.post("/api/export", json={"password": PASSWORD})
    body: dict[str, Any] = r.json()
    assert len(body["work_shifts"]) == 2 and body["work_plan"]["source"] == "file" and "url" not in body["work_plan"]
    assert body["initiative_states"] == []
