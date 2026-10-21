"""Démarches de bout en bout (scénarios C, D, F) : modèle sourcé, valeurs préremplies avec leur provenance,
pièces du coffre, contradiction bloquante, revue liée à l'empreinte, validation, envoi déclaré une seule fois,
suivi et résultat — et jamais aucun envoi par Bank lui-même."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import procedures as dp
from app.models import AuditLog, OpportunityState, ProcedureEvent, ValueEvent
from tests.pdfgen import make_pdf
from tests.scenario import TODAY
from tests.vaultgen import PAYSLIP


def _payslip(month: int, net_social: str, paid: str) -> list[str]:
    lines = [ln.replace("09/2026", f"{month:02d}/2026") for ln in PAYSLIP]
    lines = [ln.replace("2 071,15", net_social) for ln in lines]
    return [ln.replace("Date de paiement : 30/09/2026", f"Date de paiement : {paid}") for ln in lines]


def _deposit(world: TestClient, name: str, lines: list[str], verify: bool = True) -> int:
    r = world.post("/api/vault/upload", files=[("files", (name, make_pdf(lines), "application/pdf"))])
    doc_id = int(r.json()["documents"][0]["id"])
    if verify:
        assert world.post(f"/api/vault/{doc_id}/verify").status_code == 200
    return doc_id


@pytest.fixture
def payslips(world: TestClient) -> dict[str, int]:
    return {
        "2026-09": _deposit(world, "sept.pdf", _payslip(9, "2 071,15", "30/09/2026")),
        "2026-08": _deposit(world, "aout.pdf", _payslip(8, "2 012,40", "31/08/2026")),
        "2026-07": _deposit(world, "juil.pdf", _payslip(7, "1 998,05", "31/07/2026")),
    }


def _plain(text: str) -> str:
    """Montant affiché avec des espaces ordinaires (le formatage français utilise des espaces insécables)."""
    return " ".join(text.replace("\u202f", " ").replace("\xa0", " ").split())


def _fields(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in view["fields"]}


def _review_and_approve(world: TestClient, proc: dict[str, Any]) -> dict[str, Any]:
    keys = [d["key"] for d in proc["template"]["declarations"]]
    r = world.post(f"/api/procedures/{proc['id']}/approve", json={"hash": proc["hash"], "declarations": keys})
    assert r.status_code == 200, r.text
    return dict(r.json())


# ============================================================================ scénario C — demander une aide
def test_asking_for_the_prime_d_activite_end_to_end(app, world: TestClient, payslips: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    catalogue = world.get("/api/procedures").json()
    tpl = next(t for t in catalogue["templates"] if t["key"] == "prime_activite")
    assert tpl["channel"] == "online_official" and tpl["sources"] and tpl["confidence"] == "moyenne"
    assert all(s["verified_on"] for s in tpl["sources"] if s["kind"] != "texte")
    proc = world.post("/api/procedures", json={"template": "prime_activite"}).json()
    f = _fields(proc)
    # Préremplissage : profil du foyer, puis les trois derniers bulletins, chacun avec sa preuve
    assert f["situation"]["value"] == "seul" and f["enfants"]["value"] == 0
    assert f["logement"]["value"] == "locataire" and f["loyer"]["value"] == 65_000
    assert f["loyer"]["provenance"]["label"] == "Ton profil"
    assert [f[k]["value"] for k in ("salaire_m1", "salaire_m2", "salaire_m3")] == [207_115, 201_240, 199_805]
    m1 = f["salaire_m1"]["provenance"]
    assert m1["source"] == "vault" and m1["document_id"] == payslips["2026-09"] and m1["period"] == "septembre 2026"
    assert "net social" in m1["evidence"].lower()
    # Pièces obligatoires jointes d'office (les 3 bulletins), les facultatives laissées à ton choix
    pieces = {p["key"]: p for p in proc["pieces"]}
    assert sorted(d["id"] for d in pieces["bulletins"]["attached"]) == sorted(payslips.values())
    assert pieces["rib"]["attached"] == [] and not pieces["rib"]["missing"]
    # Des versements de la Caf arrivent sur le compte : déjà allocataire, et Bank dit d'où il le sait
    assert f["allocataire"]["value"] == "oui" and f["allocataire"]["provenance"]["label"] == "Tes comptes"
    assert proc["stage"] == "ready" and proc["evaluation"]["missing_fields"] == []
    proc = world.patch(f"/api/procedures/{proc['id']}/fields", json={"values": {"allocataire": "non"}}).json()
    assert _fields(proc)["allocataire"]["provenance"]["label"] == "Saisi par toi"  # ta réponse l'emporte
    assert proc["stage"] == "ready" and proc["evaluation"]["conflicts"] == []
    # Scénario D : pas de connecteur officiel, parcours guidé sur caf.fr
    assert proc["template"]["official_url"] == "https://www.caf.fr/" and len(proc["template"]["steps"]) >= 5
    assert "Bank ne le garde pas" in " ".join(proc["template"]["steps"])
    # Revue : sans tous les engagements cochés, pas de validation ; avec, validation liée à l'empreinte
    r = world.post(f"/api/procedures/{proc['id']}/approve", json={"hash": proc["hash"], "declarations": ["exactitude"]})
    assert r.status_code == 422
    assert world.post(f"/api/procedures/{proc['id']}/sent", json={}).status_code == 409  # pas encore validé
    proc = _review_and_approve(world, proc)
    assert proc["approved"] is True
    sent = world.post(f"/api/procedures/{proc['id']}/sent", json={"reference": " CAF-2026-1234 "}).json()
    assert sent["stage"] == "waiting" and sent["reference"] == "CAF-2026-1234"
    assert sent["next_check"] == (TODAY + timedelta(days=21)).isoformat() and sent["sent_on"] == TODAY.isoformat()
    again = world.post(f"/api/procedures/{proc['id']}/sent", json={"reference": "autre"}).json()
    assert again["reference"] == "CAF-2026-1234"  # deuxième clic : rien ne se répète
    assert world.patch(f"/api/procedures/{proc['id']}/fields", json={"values": {"enfants": 1}}).status_code == 409
    done = world.post(
        f"/api/procedures/{proc['id']}/close", json={"outcome": "obtenu", "amount_cents": 18_000, "period": "month"}
    ).json()
    assert done["stage"] == "done" and done["outcome"]["result"] == "obtenu"
    with app.state.sessionmaker() as db:
        value = db.scalars(select(ValueEvent)).one()
        assert value.kind == "confirmed" and value.amount_cents == 18_000 and value.period == "month"
        kinds = [e.kind for e in db.scalars(select(ProcedureEvent).order_by(ProcedureEvent.id))]
        assert kinds == ["created", "fields", "approved", "sent", "closed"]
        details = " ".join(e.detail for e in db.scalars(select(ProcedureEvent)))
        assert "2071" not in details and "207115" not in details  # le fil ne contient aucune valeur
        audit = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.action.like("procedure.%")))]
        assert audit == [
            "procedure.create",
            "procedure.fields",
            "procedure.approve",
            "procedure.sent",
            "procedure.close",
        ]


# ============================================================================ scénario F — valeur contradictoire
def test_a_value_contradicting_its_proof_blocks_until_corrected(world: TestClient, payslips: dict[str, int]) -> None:
    proc = world.post("/api/procedures", json={"template": "prime_activite"}).json()
    pid = proc["id"]
    world.patch(f"/api/procedures/{pid}/fields", json={"values": {"allocataire": "oui"}})
    proc = world.patch(f"/api/procedures/{pid}/fields", json={"values": {"salaire_m1": "1 500,00"}}).json()
    conflict = _fields(proc)["salaire_m1"]["conflict"]
    assert proc["stage"] == "verify" and _plain(conflict["expected"]) == "2 071,15 €"
    assert conflict["document_id"] == payslips["2026-09"] and "net social" in conflict["evidence"].lower()
    r = world.post(f"/api/procedures/{pid}/approve", json={"hash": proc["hash"], "declarations": []})
    assert r.status_code == 409 and "pas prêt" in r.json()["detail"]
    # Voie 1 : revenir à la valeur du justificatif
    proc = world.patch(f"/api/procedures/{pid}/fields", json={"values": {"salaire_m1": "2 071,15"}}).json()
    assert proc["stage"] == "ready" and _fields(proc)["salaire_m1"]["provenance"]["source"] == "vault"
    # Voie 2 : le justificatif était mal lu → corrigé dans le coffre, la démarche suit
    world.put(f"/api/vault/{payslips['2026-09']}/fields/net_social", json={"value": "2 081,15"})
    proc = world.get(f"/api/procedures/{pid}").json()
    assert _fields(proc)["salaire_m1"]["value"] == 208_115 and proc["stage"] == "ready"
    world.patch(f"/api/procedures/{pid}/fields", json={"values": {"salaire_m2": "9 999,00"}})
    world.put(f"/api/vault/{payslips['2026-08']}/fields/net_social", json={"value": "9 999,00"})
    assert world.get(f"/api/procedures/{pid}").json()["evaluation"]["conflicts"] == []
    # Voie 3 : « reprendre la valeur du document » en un geste (sans retaper le montant)
    proc = world.patch(f"/api/procedures/{pid}/fields", json={"values": {"salaire_m3": "1,00"}}).json()
    assert proc["stage"] == "verify"
    proc = world.post(f"/api/procedures/{pid}/fields/salaire_m3/restore").json()
    assert proc["stage"] == "ready" and _fields(proc)["salaire_m3"]["value"] == 199_805
    assert _fields(proc)["salaire_m3"]["provenance"]["source"] == "vault"
    assert world.post(f"/api/procedures/{pid}/fields/allocataire/restore").status_code == 409  # saisi, sans document


def test_approval_is_bound_to_the_exact_dossier(world: TestClient, payslips: dict[str, int]) -> None:
    proc = world.post("/api/procedures", json={"template": "prime_activite"}).json()
    proc = world.patch(f"/api/procedures/{proc['id']}/fields", json={"values": {"allocataire": "non"}}).json()
    stale_hash = proc["hash"]
    proc = _review_and_approve(world, proc)
    proc = world.patch(f"/api/procedures/{proc['id']}/fields", json={"values": {"autres_ressources": "120"}}).json()
    assert proc["approved"] is False and proc["approved_stale"] is True and proc["hash"] != stale_hash
    assert world.post(f"/api/procedures/{proc['id']}/sent", json={}).status_code == 409
    r = world.post(
        f"/api/procedures/{proc['id']}/approve",
        json={"hash": stale_hash, "declarations": [d["key"] for d in proc["template"]["declarations"]]},
    )
    assert r.status_code == 409 and "changé" in r.json()["detail"]
    # Une pièce non vérifiée dans le coffre bloque aussi : Bank ne s'appuie que sur ce que tu as relu
    fresh = _deposit(world, "oct.pdf", _payslip(10, "2 100,00", "31/10/2026"), verify=False)
    proc = world.put(f"/api/procedures/{proc['id']}/pieces/bulletins", json={"document_ids": [fresh]}).json()
    assert proc["stage"] == "documents" and proc["evaluation"]["unverified"] == [fresh]
    assert "bulletins" in proc["evaluation"]["missing_pieces"]  # un seul bulletin sur trois


def test_identical_dossiers_are_sent_only_once(world: TestClient, payslips: dict[str, int]) -> None:
    first = world.post("/api/procedures", json={"template": "prime_activite"}).json()
    second = world.post("/api/procedures", json={"template": "prime_activite"}).json()
    ids = []
    for proc in (first, second):
        proc = world.patch(f"/api/procedures/{proc['id']}/fields", json={"values": {"allocataire": "non"}}).json()
        ids.append(_review_and_approve(world, proc)["id"])
    assert world.post(f"/api/procedures/{ids[0]}/sent", json={}).status_code == 200
    r = world.post(f"/api/procedures/{ids[1]}/sent", json={})
    assert r.status_code == 409 and f"n° {ids[0]}" in r.json()["detail"]


# ============================================================================ lettres, suivi, garde-fous
def test_fee_complaint_letter_and_follow_up(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    proc = world.post(
        "/api/procedures",
        json={
            "template": "contester_frais",
            "opportunity_key": "fees:2026-09",
            "initial": {
                "montant": "96,00",
                "periode": "septembre 2026",
                "detail": "12/09 Commission d'intervention 8,00 €",
            },
        },
    ).json()
    f = _fields(proc)
    assert f["banque"]["value"] == "Crédit Agricole" and f["montant"]["value"] == 9_600
    assert f["montant"]["provenance"]["label"] == "Calculé par Bank"
    assert (
        world.post("/api/procedures", json={"template": "contester_frais", "opportunity_key": "fees:2026-09"}).json()[
            "id"
        ]
        == proc["id"]
    )
    proc = world.patch(
        f"/api/procedures/{proc['id']}/fields",
        json={"values": {"nom": "Micka Exemple", "motif": "plafond", "fragile": True}},
    ).json()
    letter = proc["letter"]
    assert "R. 312-4-1" in letter and "R. 312-4-2" in letter and "D. 133-6" in letter
    assert "96,00" in letter and "Micka Exemple" in letter and "médiateur" in letter
    assert f"Le {TODAY.day} octobre 2026" in letter
    download = world.get(f"/api/procedures/{proc['id']}/letter")
    assert (
        download.headers["content-type"].startswith("text/plain")
        and "attachment" in download.headers["content-disposition"]
    )
    proc = _review_and_approve(world, proc)
    # Envoyé le lendemain de la validation : l'empreinte ne dépend pas du jour, seulement du fond
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=1)
    sent = world.post(
        f"/api/procedures/{proc['id']}/sent", json={"sent_on": (TODAY + timedelta(days=1)).isoformat()}
    ).json()
    assert sent["stage"] == "waiting" and sent["escalation"]["label"].startswith("Saisir le médiateur")
    assert sent["escalation"]["until"] == (TODAY + timedelta(days=366)).isoformat()
    assert f"Le {TODAY.day + 1} octobre 2026" in sent["letter"]  # la lettre porte la date d'envoi
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=70)
    listing = world.get("/api/procedures").json()["procedures"]
    assert listing[0]["due"] is True  # deux mois passés : à relancer
    snoozed = world.post(f"/api/procedures/{proc['id']}/snooze", json={"days": 10}).json()
    assert snoozed["next_check"] == (TODAY + timedelta(days=80)).isoformat() and snoozed["due"] is False
    with app.state.sessionmaker() as db:
        state = db.get(OpportunityState, "fees:2026-09")
        assert state is not None and state.status == "started" and state.procedure_id == proc["id"]


def test_cancellation_letter_and_guardrails(world: TestClient) -> None:
    proc = world.post(
        "/api/procedures",
        json={"template": "resilier_abonnement", "initial": {"fournisseur": "SalleFit", "montant_mensuel": "29,90"}},
    ).json()
    pid = proc["id"]
    assert proc["stage"] == "verify" and proc["evaluation"]["missing_fields"] == ["nom"]
    for values, status in (
        ({"inconnu": "x"}, 404),
        ({"date_effet": "31/02/2027"}, 422),
        ({"nom": "a\x00b"}, 422),
    ):
        assert world.patch(f"/api/procedures/{pid}/fields", json={"values": values}).status_code == status
    proc = world.patch(
        f"/api/procedures/{pid}/fields",
        json={"values": {"nom": "Micka", "date_effet": "30/11/2026", "reference_client": "C-778"}},
    ).json()
    assert "à compter du 30 novembre 2026" in proc["letter"] and "C-778" in proc["letter"]
    assert proc["stage"] == "ready" and proc["template"]["declarations"][0]["kind"] == "consequence"
    assert world.put(f"/api/procedures/{pid}/pieces/rib", json={"document_ids": []}).status_code == 404
    assert world.post(f"/api/procedures/{pid}/snooze", json={"days": 5}).status_code == 409
    assert world.post(f"/api/procedures/{pid}/close", json={"outcome": "abandon"}).json()["stage"] == "abandoned"
    assert world.post(f"/api/procedures/{pid}/close", json={"outcome": "abandon"}).status_code == 409
    assert world.delete(f"/api/procedures/{pid}").status_code == 204
    assert world.get(f"/api/procedures/{pid}").status_code == 404
    assert world.post("/api/procedures", json={"template": "inconnu"}).status_code == 404


def test_pieces_must_match_their_kind(world: TestClient) -> None:
    invoice = _deposit(world, "f.pdf", ["EDF", "FACTURE du 12/10/2026", "Montant TTC à payer : 45,90 €"])
    tax = _deposit(
        world,
        "avis.pdf",
        [
            "AVIS D'IMPÔT 2026",
            "Impôt sur les revenus 2025",
            "Revenu fiscal de référence : 18 400 €",
            "Nombre de parts : 1,00",
        ],
    )
    proc = world.post("/api/procedures", json={"template": "ouvrir_lep"}).json()
    f = _fields(proc)
    assert f["rfr"]["value"] == 1_840_000 and f["parts"]["value"] == 1 and f["banque"]["value"] == "Crédit Agricole"
    assert {p["key"]: [d["id"] for d in p["attached"]] for p in proc["pieces"]} == {"avis": [tax]}
    r = world.put(f"/api/procedures/{proc['id']}/pieces/avis", json={"document_ids": [invoice]})
    assert r.status_code == 422 and "avis" in r.json()["detail"].lower()
    assert world.put(f"/api/procedures/{proc['id']}/pieces/avis", json={"document_ids": [9999]}).status_code == 404
    proc = world.put(f"/api/procedures/{proc['id']}/pieces/avis", json={"document_ids": []}).json()
    assert proc["stage"] == "documents" and proc["pieces"][0]["candidates"][0]["id"] == tax


def test_templates_are_sourced_and_never_claim_to_send() -> None:
    for tpl in dp.TEMPLATES.values():
        if tpl.key == "libre":
            continue
        assert tpl.sources and tpl.confidence in ("élevée", "moyenne", "faible")
        assert all(s.url is None or s.url.startswith("https://") for s in tpl.sources)
        text = " ".join([*tpl.steps, tpl.summary, *(d.text for d in tpl.declarations)]).lower()
        assert "bank envoie" not in text and "bank transmet" not in text
    tpl = dp.TEMPLATES["prime_activite"]
    assert dp.fingerprint({"a": 1, "b": [2]}) == dp.fingerprint({"b": [2], "a": 1})  # ordre sans effet
    assert [f.key for f in dp.active_fields(tpl, {"logement": "proprietaire"})].count("loyer") == 0
    assert dp.display(dp.field_def(tpl, "logement"), "heberge") == "Hébergé(e)"  # type: ignore[arg-type]
    letter = dp.render_letter(dp.TEMPLATES["contester_frais"], {"motif": {"value": "geste"}}, None)
    assert letter is not None and "[date d'envoi]" in letter and "à titre commercial" in letter
    assert dp.next_check(tpl, date(2026, 1, 31)) == date(2026, 2, 21)


def test_free_procedure_notes_and_titles(world: TestClient) -> None:
    proc = world.post("/api/procedures", json={"template": "libre", "title": "Dossier MDPH"}).json()
    assert proc["title"] == "Dossier MDPH" and proc["pieces"] == []
    proc = world.patch(
        f"/api/procedures/{proc['id']}", json={"notes": "Appeler lundi", "title": "  Dossier  MDPH 2026 "}
    ).json()
    assert proc["notes"] == "Appeler lundi" and proc["title"] == "Dossier MDPH 2026"
    assert world.patch(f"/api/procedures/{proc['id']}", json={"title": "   "}).status_code == 422
    assert json.dumps(proc).count("Appeler") == 1
