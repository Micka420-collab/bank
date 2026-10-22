"""Calendrier des obligations : fenêtres datées et sourcées, propositions tirées des versements, constat par le
versement qui suit, alerte urgente en retard, chiffres de la déclaration trimestrielle — et jamais rien d'envoyé."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.api import deps
from app.domain import obligations as dom
from app.domain.aids import classify_aid, income_category_for
from app.importers.base import ParsedTransaction
from app.models import Account, AccountKind, AuditLog, ObligationDone, Provider, VaultDocument, VaultField
from app.services import channel_alerts, channels
from app.services import obligations as svc
from app.services.ingest import ingest
from tests.scenario import TODAY, seed_household
from tests.test_mcp import make_agent


# ------------------------------------------------------------------ domaine : les fenêtres
def test_france_travail_window_opens_on_the_28th_and_closes_on_the_15th() -> None:
    p = dom.ft_period(date(2026, 10, 1))
    assert (p.key, p.opens, p.deadline) == ("2026-10", date(2026, 10, 28), date(2026, 11, 15))
    assert p.proof_to == date(2026, 11, 22), "virement sous 5 jours ouvrés : preuve attendue jusqu'au 22"
    feb = dom.ft_period(date(2026, 2, 1))
    assert feb.opens == date(2026, 2, 26) and feb.deadline == date(2026, 3, 15), (
        "février court : le 26 (calendrier 2026)"
    )
    assert dom.ft_period(date(2028, 2, 1)).opens == date(2028, 2, 27)


def test_quarterly_declaration_covers_the_three_months_before_last() -> None:
    p = dom.dtr_period(date(2026, 6, 1))
    assert p.reference_months == ("2026-02", "2026-03", "2026-04")
    assert (p.opens, p.deadline) == (date(2026, 6, 1), date(2026, 6, 30))
    assert (p.proof_from, p.proof_to) == (date(2026, 7, 1), date(2026, 7, 15)), "le versement de juillet prouve"
    assert "février 2026" in p.label and "avril 2026" in p.label


def test_css_renewal_window_runs_from_four_to_two_months_before_the_end() -> None:
    p = dom.css_period(date(2027, 3, 31))
    assert (p.opens, p.deadline) == (date(2026, 11, 30), date(2027, 1, 31))
    assert p.note and "31 mars 2027" in p.note


def test_tax_zones_and_calendar() -> None:
    assert dom.tax_zone("45234") == 2 and dom.tax_zone("75056") == 3 and dom.tax_zone("01053") == 1
    assert dom.tax_zone("2A004") == 2 and dom.tax_zone("97411") == 3 and dom.tax_zone("") is None
    p = dom.tax_period(2026, 2)
    assert (p.opens, p.deadline, p.estimated) == (date(2026, 4, 9), date(2026, 5, 28), False)
    assert dom.tax_period(2026, 1).deadline == date(2026, 5, 21) and dom.tax_period(2026, 3).deadline == date(
        2026, 6, 4
    )
    nxt = dom.tax_period(2027, None)
    assert nxt.estimated and nxt.note and "estimées" in nxt.note and "Département inconnu" in nxt.note
    assert nxt.deadline == date(2027, 5, 22), "sans département : la date la plus proche, pour prévenir à temps"


def test_schedule_keeps_a_late_period_two_months_then_drops_it() -> None:
    today = date(2026, 10, 22)
    keys = [p.key for p in dom.schedule("ft_actualisation", today)]
    assert keys[:4] == ["2026-08", "2026-09", "2026-10", "2026-11"], (
        "août (limite 15/09) encore rattrapable, puis la suite"
    )
    assert "2026-07" not in keys, "limite du 15 août : plus de 60 jours, période manquée"
    dtr = dom.schedule("caf_dtr", today, anchor=date(2026, 11, 1))
    assert [p.key for p in dtr][:3] == ["2026-08", "2026-11", "2027-02"]
    assert dom.schedule("caf_dtr", today) == [], "sans mois de déclaration, rien à calculer"
    css = dom.schedule("css_renewal", today, anchor=date(2027, 3, 31))
    assert css[0].key == "2027-03"
    custom = dom.schedule("custom", today, anchor=date(2026, 9, 1), cadence_months=12, window_days=30)
    assert [p.key for p in custom] == ["2026-09-01", "2027-09-01"]
    tax = dom.schedule("tax_return", today, insee_code="45234")
    assert [p.key for p in tax] == ["2027"], "la campagne 2026 est passée depuis plus de 60 jours"


def test_states_follow_the_calendar() -> None:
    p = dom.ft_period(date(2026, 10, 1))  # ouvre le 28/10, limite le 15/11
    assert dom.state_of(p, date(2026, 10, 20), False) == "upcoming"
    assert dom.state_of(p, date(2026, 10, 28), False) == "open"
    assert dom.state_of(p, date(2026, 11, 13), False) == "soon"
    assert dom.state_of(p, date(2026, 11, 16), False) == "late"
    assert dom.state_of(p, date(2027, 1, 20), False) == "missed"
    assert dom.state_of(p, date(2027, 1, 20), True) == "done"


def test_the_payment_that_follows_is_the_proof() -> None:
    spec = dom.SPECS["ft_actualisation"]
    p = dom.ft_period(date(2026, 9, 1))  # fenêtre 28/09 → 15/10, preuve jusqu'au 22/10
    paid = [dom.PaymentHint(7, date(2026, 10, 2), 98_000, "ft_are")]
    assert dom.proof_for(p, spec, paid) is not None and dom.proof_for(p, spec, paid).id == 7  # type: ignore[union-attr]
    other = [
        dom.PaymentHint(8, date(2026, 10, 2), 25_000, "caf_ppa"),
        dom.PaymentHint(9, date(2026, 9, 2), 98_000, "ft_are"),
    ]
    assert dom.proof_for(p, spec, other) is None, "un versement CAF, ou celui du mois d'avant, ne prouve rien"
    assert dom.awaiting_payment(p, spec, date(2026, 10, 23), False, None) is True, "rien vu après le 22 : alerte"
    assert dom.awaiting_payment(p, spec, date(2026, 10, 10), False, None) is False
    assert dom.awaiting_payment(dom.tax_period(2026, 1), dom.SPECS["tax_return"], date(2026, 7, 1), True, None) is False


def test_suggestions_come_from_the_payments() -> None:
    today = date(2026, 10, 22)
    pays = [
        dom.PaymentHint(1, date(2026, 10, 2), 98_000, "ft_are"),
        dom.PaymentHint(2, date(2026, 10, 5), 25_000, "caf_ppa"),
        dom.PaymentHint(3, date(2026, 3, 5), 25_000, "msa_prestation"),  # trop ancien
    ]
    assert dom.suggest(pays, today, existing=(), dismissed=(), has_data=True) == [
        "ft_actualisation",
        "caf_dtr",
        "tax_return",
    ]
    assert dom.suggest(pays, today, existing=("caf_dtr",), dismissed=("tax_return",), has_data=True) == [
        "ft_actualisation"
    ]
    assert dom.suggest([], today, existing=(), dismissed=(), has_data=False) == []


def test_daily_allowances_are_income_not_refunds() -> None:
    assert classify_aid("VIR CPAM DU LOIRET IJ MALADIE", 54_000) == "cpam_ij"
    assert classify_aid("CPAM PENSION INVALIDITE", 60_000) == "cpam_invalidite"
    assert classify_aid("CPAM REMBOURSEMENT SOINS", 2_300) == "cpam_rembt"
    assert income_category_for("cpam_ij") == "aides" and income_category_for("cpam_rembt") == "remboursements"


def test_quarterly_prefill_classifies_resources_and_prefers_the_net_social_amount() -> None:
    months = ("2026-07", "2026-08", "2026-09")
    rows = [
        dom.IncomeRow(1, date(2026, 7, 28), 215_000, "BRGM SALAIRE", "salaire", None, False),
        dom.IncomeRow(2, date(2026, 8, 28), 215_000, "BRGM SALAIRE", "salaire", None, False),
        dom.IncomeRow(3, date(2026, 9, 28), 230_000, "BRGM SALAIRE", "salaire", None, False),
        dom.IncomeRow(4, date(2026, 8, 5), 25_829, "CAF PRIME ACTIVITE", "aides", "caf_ppa", False),
        dom.IncomeRow(5, date(2026, 9, 2), 98_000, "FRANCE TRAVAIL ARE", "aides", "ft_are", False),
        dom.IncomeRow(6, date(2026, 9, 10), 54_000, "CPAM IJ", "aides", "cpam_ij", False),
        dom.IncomeRow(7, date(2026, 9, 12), 2_300, "CPAM REMBOURSEMENT", "remboursements", "cpam_rembt", False),
        dom.IncomeRow(8, date(2026, 9, 15), 50_000, "VIR DE MOI LIVRET", "virements_internes", None, True),
        dom.IncomeRow(9, date(2026, 9, 20), 2_500, "PAYLIB DE MARIE", "divers", None, False),
        dom.IncomeRow(10, date(2026, 6, 28), 215_000, "BRGM SALAIRE", "salaire", None, False),  # hors période
        dom.IncomeRow(11, date(2026, 8, 20), 30_000, "VIR PENSION ALIMENTAIRE", "autres_revenus", None, False),
    ]
    slips = [dom.Payslip(42, "2026-09", 241_500, 230_000, "BRGM"), dom.Payslip(43, "2026-05", 241_500, 230_000, "BRGM")]
    out = dom.dtr_prefill(months, rows, slips)
    assert out.totals == {
        "activite": 215_000 + 215_000 + 241_500,
        "chomage": 98_000,
        "ij": 54_000,
        "pension": 0,
        "autres": 30_000,
    }
    sept = out.cells["activite"]["2026-09"]
    assert sept["source"] == "bulletin" and sept["amount_cents"] == 241_500 and sept["net_paid_cents"] == 230_000
    assert sept["documents"] == [42] and out.cells["activite"]["2026-07"]["source"] == "releve"
    assert [e["id"] for e in out.excluded] == [4, 7] and [c["id"] for c in out.to_check] == [9]
    assert any("montant net social" in n for n in out.notes), "le net versé n'est pas ce que la CAF attend"
    assert "chômage 980 €" in dom.summary_line(out) and "pension" not in dom.summary_line(out)
    assert dom.prefill_to_dict(out)["month_labels"] == ["juillet 2026", "août 2026", "septembre 2026"]


# ------------------------------------------------------------------ service et API
@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def cards(client: TestClient) -> dict[str, Any]:
    r = client.get("/api/initiatives?limit=20")
    assert r.status_code == 200
    return {c["key"]: c for c in r.json()["cards"]}


def test_the_day_screen_proposes_what_the_payments_reveal_and_one_tap_activates(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    found = cards(world)
    assert "obligation:suggest:caf_dtr" in found and "obligation:suggest:tax_return" in found
    assert "obligation:suggest:ft_actualisation" not in found, "pas de versement France Travail dans ce foyer"
    card = found["obligation:suggest:caf_dtr"]
    assert "prime d'activité" in card["title"] and card["actions"][0]["effect"] == {
        "type": "obligation",
        "kind": "caf_dtr",
    }
    r = world.post("/api/initiatives/obligation:suggest:caf_dtr/act", json={"action": "obligation_on"})
    assert r.status_code == 200 and "préviens" in r.json()["result"]
    listing = world.get("/api/obligations").json()
    row = next(o for o in listing["obligations"] if o["kind"] == "caf_dtr")
    assert row["needs_anchor"] is True and row["current"] is None and row["anchor_label"]
    assert f"obligation:{row['id']}:anchor" in cards(world), "il manque le mois de déclaration : Bank le demande"
    # On écarte la déclaration de revenus : plus proposée, mais toujours dans le catalogue.
    r = world.post("/api/initiatives/obligation:suggest:tax_return/act", json={"action": "obligation_off"})
    assert r.status_code == 200
    listing = world.get("/api/obligations").json()
    assert [s["kind"] for s in listing["suggestions"]] == [] and any(
        c["kind"] == "tax_return" for c in listing["catalog"]
    )
    with app.state.sessionmaker() as s:
        actions = [a for (a,) in s.execute(select(AuditLog.action).where(AuditLog.action.like("obligation.%")))]
    assert "obligation.add" in actions and "obligation.dismiss" in actions


def test_quarterly_declaration_gets_its_figures_and_keeps_what_was_declared(world: TestClient) -> None:
    r = world.post("/api/obligations", json={"kind": "caf_dtr", "anchor": "2026-10-01"})
    assert r.status_code == 201
    row = next(o for o in r.json()["obligations"] if o["kind"] == "caf_dtr")
    cur = row["current"]
    assert (
        cur["key"] == "2026-10"
        and cur["state"] == "open"
        and cur["reference_months"] == ["2026-06", "2026-07", "2026-08"]
    )
    pre = world.get(f"/api/obligations/{row['id']}/prefill?period=2026-10").json()
    assert pre["months"] == ["2026-06", "2026-07", "2026-08"]
    assert pre["totals"]["activite"] == 3 * 215_000, "le salaire des trois mois de référence"
    assert pre["totals"]["chomage"] == 0 and all(e["label"].startswith("CAF") for e in pre["excluded"])
    assert any("net social" in n for n in pre["notes"])
    assert pre["cells"]["activite"]["2026-06"]["items"][0]["amount_cents"] == 215_000
    assert "salaires 6450 €" in pre["summary"]
    # La carte du jour porte les chiffres, et « c'est fait » note la période avec ce qui a été déclaré.
    key = f"obligation:{row['id']}:2026-10:open"
    card = cards(world)[key]
    assert "Tes chiffres sont prêts" in card["body"] and card["actions"][0]["label"] == "C'est fait"
    r = world.post(f"/api/obligations/{row['id']}/done", json={"period": "2026-10", "declared": pre["totals"]})
    assert r.status_code == 200
    done = next(o for o in r.json()["obligations"] if o["kind"] == "caf_dtr")["periods"]
    oct = next(p for p in done if p["key"] == "2026-10")
    assert oct["state"] == "done" and oct["how"] == "user" and oct["declared"]["activite"] == 645_000
    assert key not in cards(world)
    assert world.get(f"/api/obligations/{row['id']}/prefill?period=2025-01").status_code == 422
    export = world.post("/api/export", json={"password": "une phrase de passe tres longue 42"}).json()["obligations"]
    assert export[0]["kind"] == "caf_dtr" and export[0]["done"][0]["period"] == "2026-10"
    r = world.delete(f"/api/obligations/{row['id']}/done/2026-10")
    assert r.status_code == 200 and f"obligation:{row['id']}:2026-10:open" in cards(world)


def _ft_account(db: Session, payments: list[tuple[date, int]]) -> None:
    account = Account(
        name="Compte FT",
        kind=AccountKind.CHECKING,
        institution="Banque",
        provider=Provider.FILE,
        balance_cents=100_000,
        balance_at=None,
    )
    db.add(account)
    db.flush()
    ingest(db, account, [ParsedTransaction(d, c, "VIR FRANCE TRAVAIL ARE") for d, c in payments], provider="file")


def test_the_payment_proves_the_actualisation_and_silence_raises_the_alarm(app, auth_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        _ft_account(s, [(date(2026, 9, 2), 98_000), (date(2026, 10, 2), 98_000)])
        svc.activate(s, "ft_actualisation", actor="micka")
        s.commit()
    with app.state.sessionmaker() as s:
        view = svc.view(s, date(2026, 10, 22))
        row = view["obligations"][0]
        by_key = {p["key"]: p for p in row["periods"]}
        sept = by_key["2026-09"]  # fenêtre 28/09 → 15/10 : le virement du 2/10 prouve l'actualisation
        assert (
            sept["state"] == "done" and sept["how"].startswith("payment:") and sept["proof"]["amount_cents"] == 98_000
        )
        assert by_key["2026-10"]["state"] == "upcoming" and row["current"]["key"] == "2026-10"
        assert svc.auto_detect(s, date(2026, 10, 22)) == 2 and svc.auto_detect(s, date(2026, 10, 22)) == 0
        assert s.scalar(select(ObligationDone).where(ObligationDone.period == "2026-09")) is not None
        # Novembre : la fenêtre d'octobre (28/10 → 15/11) s'est refermée sans virement → urgence.
        late = svc.view(s, date(2026, 11, 23))["obligations"][0]["current"]
        assert late["key"] == "2026-10" and late["state"] == "late" and late["awaiting_payment"] is True
        notices = svc.alerts(s, today=date(2026, 11, 23), privacy="redacted", public_url="https://bank.example")
        assert notices and notices[0][2] is True and "aucun versement" in notices[0][1]
        soon = svc.alerts(s, today=date(2026, 11, 13), privacy="redacted", public_url="https://bank.example")
        assert any(k.endswith(":soon") and not urgent for k, _, urgent in soon)
        opened = svc.alerts(s, today=date(2026, 10, 28), privacy="redacted", public_url="https://bank.example")
        assert any(k.endswith(":open") and "jusqu'au 15 novembre" in t for k, t, _ in opened)
        # Les alertes de messagerie les reprennent, et l'urgence passe les heures calmes.
        from datetime import UTC, datetime

        all_notices = channel_alerts.collect(
            s, today=date(2026, 11, 23), local=datetime(2026, 11, 23, 23, 0, tzinfo=UTC), privacy="redacted",
            public_url="https://bank.example",
        )  # fmt: skip
        mine = [n for n in all_notices if n.key.startswith("obl:")]
        assert mine and mine[0].urgent is True
        lines = svc.chat_lines(s, date(2026, 11, 23), "redacted", "https://bank.example")
        assert lines[0].startswith("🚨") and lines[-1].endswith("/#/aides")


def test_agent_reads_the_calendar_and_chat_answers_declarations(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    assert world.post("/api/obligations", json={"kind": "caf_dtr", "anchor": "2026-10-01"}).status_code == 201
    agent = make_agent(app, world)
    out = agent.ok("get_obligations")
    row = out["obligations"][0]
    assert (
        row["kind"] == "caf_dtr"
        and row["current"]["state"] == "open"
        and row["prefill_totals_cents"]["activite"] == 645_000
    )
    assert "ne déclare jamais" in out["rule"]
    discreet = make_agent(app, world, preset="observateur", privacy="aggregates", name="discret")
    assert discreet.ok("get_obligations")["obligations"][0]["prefill_totals_cents"] is None
    with app.state.sessionmaker() as s:
        from types import SimpleNamespace

        link = SimpleNamespace(platform="telegram", id=1, notify=True)
        cfg = SimpleNamespace(privacy="redacted")
        (text,) = channels._run_command(db=s, cfg=cfg, link=link, name="declarations", args="", now=None, today=TODAY,  # type: ignore[arg-type]
                                       public_url="https://bank.example")  # fmt: skip
        assert "Déclaration trimestrielle" in text and "31 octobre" in text
        assert channels._command("actualisation") == ("declarations", "")


def test_custom_obligation_rolls_forward_and_css_follows_the_end_of_rights(world: TestClient) -> None:
    r = world.post(
        "/api/obligations",
        json={"kind": "custom", "label": "Attestation d'assurance au bailleur", "anchor": "2026-11-05",
              "cadence_months": 12, "window_days": 20, "url": "https://example.org"},
    )  # fmt: skip
    assert r.status_code == 201
    row = next(o for o in r.json()["obligations"] if o["kind"] == "custom")
    assert row["label"] == "Attestation d'assurance au bailleur" and row["current"]["key"] == "2026-11-05"
    assert row["current"]["state"] == "open" and row["url"] == "https://example.org"
    r = world.post(f"/api/obligations/{row['id']}/done", json={"period": "2026-11-05"})
    nxt = next(o for o in r.json()["obligations"] if o["id"] == row["id"])
    assert (
        nxt["anchor"] == "2027-11-05"
        and nxt["current"]["key"] == "2027-11-05"
        and nxt["current"]["state"] == "upcoming"
    )
    bad = world.post(
        "/api/obligations", json={"kind": "custom", "label": "x", "anchor": "2026-12-01", "url": "http://x"}
    )
    assert bad.status_code == 422 and "https" in bad.json()["detail"]
    assert world.post("/api/obligations", json={"kind": "custom", "anchor": "2026-12-01"}).status_code == 422
    r = world.post("/api/obligations", json={"kind": "css_renewal", "anchor": "2027-02-15", "automatic": True})
    css = next(o for o in r.json()["obligations"] if o["kind"] == "css_renewal")
    assert css["current"]["key"] == "2027-02" and css["current"]["state"] == "open" and css["automatic"] is True
    r = world.post(f"/api/obligations/{css['id']}/done", json={"period": "2027-02"})
    css = next(o for o in r.json()["obligations"] if o["kind"] == "css_renewal")
    assert css["anchor"] == "2028-02-15"
    r = world.patch(f"/api/obligations/{css['id']}", json={"active": False})
    assert next(o for o in r.json()["obligations"] if o["kind"] == "css_renewal")["active"] is False
    assert world.delete(f"/api/obligations/{css['id']}").status_code == 204
    assert world.delete(f"/api/obligations/{css['id']}").status_code == 404


def test_a_read_payslip_feeds_the_quarterly_figures(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        doc = VaultDocument(
            kind="payslip", title="Bulletin", filename="bulletin.pdf", mime="application/pdf", size=10, sha256="a" * 64,
            status="verified", source="upload", method="pdf_text", confidence=0.9,
        )  # fmt: skip
        s.add(doc)
        s.flush()
        s.add_all(
            [
                VaultField(document_id=doc.id, key="periode", value=json.dumps("2026-08"), confidence=0.9),
                VaultField(document_id=doc.id, key="net_social", value=json.dumps(241_500), confidence=0.9),
                VaultField(document_id=doc.id, key="net_paye", value=json.dumps(215_000), confidence=0.9),
            ]
        )
        s.commit()
    r = world.post("/api/obligations", json={"kind": "caf_dtr", "anchor": "2026-10-01"})
    row = next(o for o in r.json()["obligations"] if o["kind"] == "caf_dtr")
    pre = world.get(f"/api/obligations/{row['id']}/prefill?period=2026-10").json()
    aug = pre["cells"]["activite"]["2026-08"]
    assert aug["source"] == "bulletin" and aug["amount_cents"] == 241_500 and aug["net_paid_cents"] == 215_000
    assert pre["totals"]["activite"] == 215_000 + 241_500 + 215_000


def test_tax_return_window_depends_on_the_department(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: date(2026, 5, 10)
    r = world.post("/api/obligations", json={"kind": "tax_return"})
    row = next(o for o in r.json()["obligations"] if o["kind"] == "tax_return")
    cur = row["current"]
    assert cur["key"] == "2026" and cur["state"] == "open"
    assert cur["deadline"] == "2026-05-28", "Loiret (45) : zone 2"
    assert row["source"].startswith("impots.gouv.fr")
    app.dependency_overrides[deps.today] = lambda: date(2026, 5, 27)
    assert world.get("/api/obligations").json()["obligations"][0]["current"]["state"] == "soon"
    assert any(k.startswith("obligation:") and k.endswith(":soon") for k in cards(world))


def test_nothing_in_the_calendar_posts_anything_anywhere() -> None:
    """Garde-fou de principe : le service ne connaît aucun client réseau et aucun outil MCP n'écrit."""
    import inspect

    source = inspect.getsource(svc)
    assert "netfetch" not in source and "httpx" not in source and "requests" not in source
    assert TODAY + timedelta(days=0) == TODAY
