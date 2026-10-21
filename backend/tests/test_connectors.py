"""Connecteurs : Linky (client simulé), documents officiels, décisions d'achat et plan de placement — API et MCP."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain.energy import DailyReading
from app.importers.base import ParsedTransaction
from app.models import AuditLog, EnergyConnection, EnergyReading, OfficialFact
from app.providers.conso_api import ConsoApiError
from app.services.ingest import ingest
from tests.pdfgen import make_pdf
from tests.scenario import TODAY, seed_household
from tests.test_documents import AVIS, CAF, FT
from tests.test_mcp import make_agent

TOKEN = "a" * 40 + "." + "b" * 40 + "." + "c" * 40
PRM = "12345678901234"


class FakeConso:
    """Client Conso API simulé : 10 kWh/jour, journalise les fenêtres demandées, peut échouer."""

    calls: ClassVar[list[tuple[date, date]]] = []
    fail: ClassVar[ConsoApiError | None] = None
    wh_per_day: ClassVar[int] = 10_000

    def __init__(self, token: str) -> None:
        assert token == TOKEN

    def daily_consumption(self, prm: str, start: date, end_exclusive: date) -> list[DailyReading]:
        assert prm == PRM
        FakeConso.calls.append((start, end_exclusive))
        if FakeConso.fail is not None:
            raise FakeConso.fail
        return [
            DailyReading(start + timedelta(days=i), FakeConso.wh_per_day) for i in range((end_exclusive - start).days)
        ]


@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    app.state.energy_client_factory = FakeConso
    FakeConso.calls, FakeConso.fail = [], None
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ énergie
def test_linky_connect_backfill_sync_and_insights(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    assert world.get("/api/energy").json()["connected"] is False
    r = world.post("/api/energy/linky", json={"prm": "1234 5678 9012 34", "token": TOKEN})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["connected"] and body["imported"] == 400 and body["connection"]["prm_masked"].endswith("1234")
    assert body["connection"]["status"] == "active"
    ins = body["insights"]
    assert ins["days_of_data"] == 400 and ins["last_30_days_wh"] == 300_000 and not ins["stale"]
    assert ins["yearly_estimate_wh"] == 3_650_000 and ins["monthly_payment_cents"] is None
    # Le test de connexion (une semaine) puis la reprise de 400 jours ; le découpage par tranches est testé
    # avec le vrai client (test_conso_api).
    assert [(e - s).days for s, e in FakeConso.calls] == [7, 400]
    with app.state.sessionmaker() as db:
        conn = db.scalar(select(EnergyConnection))
        assert conn is not None and conn.prm == PRM and conn.token == TOKEN  # chiffrés en base, lisibles ici
        assert db.scalar(select(EnergyReading).where(EnergyReading.day == TODAY - timedelta(days=1))).wh == 10_000  # type: ignore[union-attr]
    # Nouvelle synchro : seulement les jours manquants (depuis l'avant-veille du dernier relevé).
    FakeConso.calls.clear()
    FakeConso.wh_per_day = 12_000
    r = world.post("/api/energy/sync")
    assert r.status_code == 200 and r.json()["imported"] == 3 and len(FakeConso.calls) == 1  # 3 derniers jours relus
    assert world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN}).status_code == 422  # déjà connecté
    # Journal : tout est tracé, jeton jamais écrit.
    with app.state.sessionmaker() as db:
        actions = [a.action for a in db.scalars(select(AuditLog))]
        assert "energy.connect" in actions and "energy.sync_manual" in actions
        assert TOKEN not in " ".join(a.details for a in db.scalars(select(AuditLog)))
    assert world.delete("/api/energy/linky").status_code == 204
    assert world.get("/api/energy").json()["connected"] is False
    with app.state.sessionmaker() as db:
        assert db.scalar(select(EnergyReading)) is None


def test_linky_rejects_bad_inputs_and_reports_remote_errors(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    assert world.post("/api/energy/linky", json={"prm": "123", "token": TOKEN}).status_code == 422
    r = world.post("/api/energy/linky", json={"prm": PRM, "token": "x" * 50})
    assert r.status_code == 422 and "Jeton" in r.text
    FakeConso.fail = ConsoApiError("Jeton refusé ou PRM non autorisé (401)", 401, permanent=True)
    r = world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    assert r.status_code == 422 and "401" in r.text
    assert world.post("/api/energy/sync").status_code == 409  # rien de connecté
    FakeConso.fail = None
    world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    FakeConso.fail = ConsoApiError("Enedis indisponible (500)", 500)
    st = world.post("/api/energy/sync").json()
    assert st["imported"] == 0 and st["connection"]["status"] == "active" and "500" in st["connection"]["last_error"]
    FakeConso.fail = ConsoApiError("Jeton refusé (401)", 401, permanent=True)
    assert world.post("/api/energy/sync").json()["connection"]["status"] == "error"


def test_energy_regularisation_alert_and_agent_view(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    FakeConso.wh_per_day = 15_000  # ≈ 5 475 kWh/an -> ~1 286 €/an au tarif Base 6 kVA
    world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    with app.state.sessionmaker() as db:
        courant = db.scalar(select(EnergyConnection))  # noqa: F841 - la base est prête
        from app.models import Account

        acc = db.scalar(select(Account).where(Account.name == "Compte courant"))
        assert acc is not None
        ingest(
            db,
            acc,
            [
                ParsedTransaction(TODAY - timedelta(days=30 * i + 5), -6_200, "PRLV SEPA EDF CLIENTS PARTICULIERS")
                for i in range(1, 4)
            ],
            provider="file",
        )
        db.commit()
    st = world.get("/api/energy").json()
    ins = st["insights"]
    assert ins["monthly_payment_cents"] == 6_200 and ins["regularisation_cents"] > 40_000
    alerts = world.get("/api/alerts").json()
    reg = next(a for a in alerts if a["code"] == "energie:regularisation")
    assert "rattrapage" in reg["title"] and str(ins["recommended_monthly_cents"] // 100) in reg["suggested_action"]
    agent = make_agent(app, world)
    view = agent.ok("get_energy")
    assert (
        view["connected"]
        and view["last_30_days_kwh"] == 450
        and view["regularisation_cents"] == ins["regularisation_cents"]
    )
    assert "daily" not in view  # l'agent ne reçoit que des agrégats
    dossier = agent.ok("get_dossier")
    assert dossier["energy"]["recommended_monthly_cents"] == ins["recommended_monthly_cents"]
    assert any(a["code"] == "energie:regularisation" for a in dossier["alerts"])
    assert make_agent(app, world, name="obs", preset="observateur").ok("get_energy")["connected"]


def test_tariff_update(world: TestClient) -> None:
    r = world.put(
        "/api/energy/tariff",
        json={"cents_per_100kwh": 1948, "subscription_cents_per_year": 23860, "label": "HP/HC 9 kVA"},
    )
    assert r.status_code == 200
    t = world.get("/api/energy").json()["tariff"]
    assert t["cents_per_100kwh"] == 1948 and t["label"] == "HP/HC 9 kVA" and len(t["options"]) == 4
    assert (
        world.put("/api/energy/tariff", json={"cents_per_100kwh": 0, "subscription_cents_per_year": 1}).status_code
        == 422
    )


# ------------------------------------------------------------------ documents officiels
def upload(client: TestClient, lines: list[str]) -> Any:
    return client.post(
        "/api/documents/preview", files={"file": ("doc.pdf", io.BytesIO(make_pdf(lines)), "application/pdf")}
    )


def test_documents_preview_apply_and_effects(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    assert world.get("/api/documents/portals").json()[0]["id"] == "impots"
    r = upload(world, AVIS)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["kind"] == "avis_impot" and preview["fields"]["rfr_cents"] == 2_345_600
    assert "numéro fiscal" in preview["discarded"]
    with app.state.sessionmaker() as db:
        assert db.scalar(select(OfficialFact)) is None  # l'aperçu n'enregistre rien
    # Validation : l'utilisateur peut corriger un champ avant d'enregistrer ; les champs inconnus sont ignorés.
    fields = {**preview["fields"], "parts": "1.5", "numero_fiscal": "1234567890123"}
    r = world.post("/api/documents/apply", json={"kind": "avis_impot", "fields": fields})
    assert r.status_code == 201 and r.json()["period"] == "2025"
    facts = world.get("/api/documents").json()
    assert len(facts) == 1 and facts[0]["fields"]["parts"] == "1.5" and "numero_fiscal" not in facts[0]["fields"]
    ob = world.get("/api/onboarding").json()
    assert ob["answers"]["tax_bracket"]["value"] == "11" and ob["answers"]["tax_bracket"]["source"].startswith("user:")
    # Même période : remplace, pas de doublon.
    world.post("/api/documents/apply", json={"kind": "avis_impot", "fields": preview["fields"]})
    assert len(world.get("/api/documents").json()) == 1
    assert (
        world.post("/api/documents/apply", json={"kind": "avis_impot", "fields": {"rfr_cents": 1}}).status_code == 422
    )
    assert world.post("/api/documents/apply", json={"kind": "passeport", "fields": {}}).status_code == 422
    assert (
        world.post(
            "/api/documents/apply", json={"kind": "avis_impot", "fields": {"incomes_year": 2025, "rfr_cents": -5}}
        ).status_code
        == 422
    )

    caf = upload(world, CAF).json()
    assert caf["kind"] == "caf"
    world.post("/api/documents/apply", json={"kind": "caf", "fields": caf["fields"]})
    ft = upload(world, FT).json()
    world.post(
        "/api/documents/apply",
        json={
            "kind": "france_travail",
            "fields": {**ft["fields"], "end_of_rights": (TODAY + timedelta(days=20)).isoformat()},
        },
    )
    alerts = world.get("/api/alerts").json()
    fin = next(a for a in alerts if a["code"] == "droits:fin")
    assert fin["severity"] == "critique" and "20 jours" in fin["title"]
    agent = make_agent(app, world)
    docs = agent.ok("get_dossier")["official_documents"]
    assert docs["tax"]["rfr_cents"] == 2_345_600 and docs["caf_rights"]["prestations"][0]["code"] == "aide_logement"
    assert docs["france_travail"]["daily_allowance_cents"] == 3_845
    fact_id = world.get("/api/documents").json()[0]["id"]
    assert world.delete(f"/api/documents/{fact_id}").status_code == 204
    assert world.delete(f"/api/documents/{fact_id}").status_code == 404


def test_documents_reject_non_pdf_and_unknown(world: TestClient) -> None:
    r = world.post(
        "/api/documents/preview", files={"file": ("x.pdf", io.BytesIO(b"pas un pdf du tout"), "application/pdf")}
    )
    assert r.status_code == 422 and "PDF" in r.text
    r = upload(world, ["Relevé de compte", "Solde 640,00 €", "rien d'officiel ici vraiment"])
    assert r.status_code == 200 and r.json()["kind"] == "inconnu"


def test_tax_document_feeds_the_simulation_profile(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from app.fiscal.situation import HouseholdProfileIn
    from app.services import documents

    fields = {"incomes_year": TODAY.year - 2, "rfr_cents": 2_000_000, "parts": "1"}
    world.post("/api/documents/apply", json={"kind": "avis_impot", "fields": fields})
    with app.state.sessionmaker() as db:
        profile = HouseholdProfileIn.model_validate(world.get("/api/profile").json())
        assert profile.annual_taxable_income_n2_cents == 2_000_000  # rempli dans le profil du foyer
        assert (
            documents.with_taxable_income(
                db, profile.model_copy(update={"annual_taxable_income_n2_cents": None}), TODAY
            ).annual_taxable_income_n2_cents
            == 2_000_000
        )


# ------------------------------------------------------------------ décisions
def test_purchase_decision_api_and_mcp(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    small = world.post(
        "/api/decisions/purchase", json={"amount_cents": 4_000, "label": "Livre", "category": "loisirs"}
    ).json()
    assert small["answer"] == "oui" and small["this_month"]["verdict"] == "ok"
    big = world.post("/api/decisions/purchase", json={"amount_cents": 900_000, "label": "Voiture"}).json()
    assert big["answer"] in ("plus_tard", "hors_de_portee", "oui_mais")
    if big["answer"] == "plus_tard":
        assert big["paths"] and big["paths"][0]["months"] and "mois" in big["headline"]
    assert world.post("/api/decisions/purchase", json={"amount_cents": 10, "category": "nimporte"}).status_code == 422
    assert world.post("/api/decisions/purchase", json={"amount_cents": 0}).status_code == 422
    agent = make_agent(app, world)
    plan = agent.ok("can_i_buy", amount_eur=40, label="Livre", category="loisirs")
    assert plan["answer"] == "oui" and plan["amount_cents"] == 4_000
    assert "Catégorie inconnue" in agent.fails("can_i_buy", amount_eur=40, category="nimporte")
    with app.state.sessionmaker() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "decision.purchase")) is not None


def test_investment_plan_api_and_mcp(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    world.put("/api/onboarding/savings_capacity", json={"value": 50_000})
    world.put("/api/onboarding/risk_horizon", json={"value": "plus_10"})
    world.put("/api/onboarding/risk_drop", json={"value": "attends"})
    world.put("/api/onboarding/red_lines", json={"value": [{"rule": "Jamais de crypto"}]})
    plan = world.get("/api/decisions/investment-plan").json()
    assert plan["monthly_capacity_cents"] == 50_000 and plan["steps"]
    # Le foyer de test n'a que 4 800 € de livrets pour un matelas de 4 mois : le matelas passe avant le long terme.
    assert plan["steps"][0]["kind"] == "matelas" and any(s["kind"] == "attente" for s in plan["steps"])
    assert plan["tax"]["source"] in ("estimated", "declared") and plan["excluded"] == ["crypto"]
    assert "pas un conseil" in plan["cautions"][-1]
    agent = make_agent(app, world)
    assert agent.ok("investment_plan")["summary"] == plan["summary"]
    # Un avis d'impôt importé fixe la tranche : « document » prime sur l'estimation.
    world.post(
        "/api/documents/apply",
        json={"kind": "avis_impot", "fields": {"incomes_year": 2025, "rfr_cents": 3_500_000, "parts": "1"}},
    )
    world.delete("/api/onboarding/tax_bracket")
    assert world.get("/api/decisions/investment-plan").json()["tax"]["source"] == "document"


def test_export_includes_energy_and_documents(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import PASSWORD

    world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    world.post(
        "/api/documents/apply", json={"kind": "avis_impot", "fields": {"incomes_year": 2025, "rfr_cents": 1_000_000}}
    )
    r = world.post("/api/export", json={"password": PASSWORD})
    assert r.status_code == 200
    data = json.loads(r.content)
    assert len(data["energy_readings"]) == 400 and data["official_facts"][0]["kind"] == "avis_impot"
    assert TOKEN not in r.text  # jamais le jeton


def test_document_upload_size_is_bounded(world: TestClient) -> None:
    from app.services.documents import MAX_PDF_BYTES

    big = b"%PDF-1.4\n" + b"0" * (MAX_PDF_BYTES + 10)
    r = world.post("/api/documents/preview", files={"file": ("big.pdf", io.BytesIO(big), "application/pdf")})
    assert r.status_code in (413, 422)  # middleware (taille du corps) ou service (5 Mo au plus)


# ------------------------------------------------------------------ validation à l'enregistrement (revue de sécurité)
@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": "NaN"}),  # comparaison impossible
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": "Infinity"}),
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": "0"}),  # division par zéro évitée
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": [1]}),
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "declarants": 3}),
        ("avis_impot", {"incomes_year": 2025, "rfr_cents": 1_000_000, "withholding_rate_percent": "101"}),
        ("caf", {"prestations": [{"code": "<script>", "label": "x", "monthly_cents": 100}]}),
        ("caf", {"prestations": [{"code": "rsa", "monthly_cents": 600_000}]}),
        ("caf", {"prestations": "rsa"}),
        ("caf", {"prestations": [{"code": "rsa", "monthly_cents": True}]}),
        ("france_travail", {"daily_allowance_cents": 3_000, "allowance": "Allocation de mon choix"}),
        ("france_travail", {"daily_allowance_cents": 3_000, "days_left": 10_000}),
    ],
)
def test_apply_rejects_hostile_or_absurd_fields(world: TestClient, kind: str, fields: dict[str, Any]) -> None:
    r = world.post("/api/documents/apply", json={"kind": kind, "fields": fields})
    assert r.status_code == 422, r.text
    assert world.get("/api/documents").json() == []


def test_apply_derives_labels_from_the_known_table_and_respects_privacy(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = world.post(
        "/api/documents/apply",
        json={
            "kind": "caf",
            "fields": {
                "month": "2026-09",
                "prestations": [
                    {"code": "rsa", "label": "Texte libre à faire lire à l'agent", "monthly_cents": 60_000},
                    {"code": "rsa", "monthly_cents": 1},  # doublon ignoré
                    {"code": "af", "monthly_cents": 14_000},
                ],
            },
        },
    )
    assert r.status_code == 201
    stored = world.get("/api/documents").json()[0]["fields"]["prestations"]
    assert [p["label"] for p in stored] == ["RSA", "Allocations familiales"]
    assert (
        world.post(
            "/api/documents/apply",
            json={"kind": "avis_impot", "fields": {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": "2,5"}},
        ).status_code
        == 201
    )
    assert world.get("/api/documents").json()[0]["fields"]["parts"] == "2.5"

    from app.services.advisor import build_dossier

    with app.state.sessionmaker() as db:
        full = build_dossier(db, TODAY, "full")["official_documents"]
        redacted = build_dossier(db, TODAY, "redacted")["official_documents"]
        aggregates = build_dossier(db, TODAY, "aggregates")["official_documents"]
    assert [p["label"] for p in full["caf_rights"]["prestations"]] == ["RSA", "Allocations familiales"]
    assert redacted["caf_rights"] == full["caf_rights"]
    assert "prestations" not in aggregates["caf_rights"]
    assert aggregates["caf_rights"]["monthly_total_cents"] == 74_000 and aggregates["caf_rights"]["count"] == 2
    assert "Texte libre" not in json.dumps(full)


def test_sync_ignores_rows_outside_the_window_even_from_a_broken_client(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    class Broken(FakeConso):
        def daily_consumption(self, prm: str, start: date, end_exclusive: date) -> list[DailyReading]:
            rows = super().daily_consumption(prm, start, end_exclusive)
            return [*rows, DailyReading(date(9999, 12, 31), 5), DailyReading(TODAY, 5), DailyReading(start, -1)]

    app.state.energy_client_factory = Broken
    r = world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    assert r.status_code == 201
    with app.state.sessionmaker() as db:
        days = sorted(d for d in db.scalars(select(EnergyReading.day)))
        total = sum(db.scalars(select(EnergyReading.wh)))
    assert days[-1] == TODAY - timedelta(days=1) and days[0] == TODAY - timedelta(days=400) and len(days) == 400
    assert total == 400 * FakeConso.wh_per_day  # le « -1 » n'a pas écrasé la valeur du premier jour
    assert (
        world.get("/api/alerts").status_code == 200
        and world.get("/api/energy").json()["insights"]["days_of_data"] == 400
    )


def test_apply_treats_cleared_fields_as_missing(world: TestClient) -> None:
    r = world.post(
        "/api/documents/apply",
        json={
            "kind": "avis_impot",
            "fields": {"incomes_year": 2025, "rfr_cents": 1_000_000, "parts": "", "withholding_rate_percent": ""},
        },
    )
    assert r.status_code == 201
    assert "parts" not in world.get("/api/documents").json()[0]["fields"]
