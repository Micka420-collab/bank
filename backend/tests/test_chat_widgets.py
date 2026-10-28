"""Widgets du chat : structures bornées, sans HTML, données libres ni action automatique."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.domain import chatblocks as b
from app.domain.decisions import PurchasePlan, PurchaseVerdict, SavingPath, Snapshot
from app.services import assistant


def test_poll_is_clean_bounded_and_roundtrips() -> None:
    raw = {
        "kind": "poll",
        "title": "Quelle priorité ?",
        "description": "Choisis avant d'agir.",
        "submit_label": "Choisir",
        "fields": [
            {
                "id": "goal",
                "kind": "single",
                "label": "Que préfères-tu ?",
                "required": True,
                "options": ["Épargne", "Dépenser moins", "Épargne", "https://evil.test/", "Libre"],
            }
        ],
        "script": "<script>attack</script>",
    }
    item = b.interactive_block(raw)
    assert item["type"] == "interactive" and item["kind"] == "poll"
    assert item["fields"][0] == {
        "id": "goal",
        "kind": "single",
        "label": "Que préfères-tu ?",
        "required": True,
        "options": ["Épargne", "Dépenser moins", "Libre"],
    }
    assert "script" not in item
    assert b.validate_blocks([item]) == [item]


def test_checklist_must_be_multiple_options_and_caps_options() -> None:
    options = [f"Étape {n}" for n in range(20)]
    item = b.interactive_block(
        {
            "kind": "checklist",
            "title": "Démarches",
            "fields": [{"id": "todo", "kind": "multiple", "label": "À faire", "options": options}],
        }
    )
    assert len(item["fields"][0]["options"]) == b.MAX_INTERACTIVE_OPTIONS
    with pytest.raises(b.BlockError):
        b.interactive_block(
            {
                "kind": "checklist",
                "title": "Démarches",
                "fields": [{"id": "x", "kind": "text", "label": "Mauvais champ"}],
            }
        )


def test_form_ignores_junk_caps_fields_and_scale_bounds() -> None:
    fields = [{"id": f"q{i}", "kind": "text", "label": "Question " + str(i), "required": i == 0} for i in range(10)]
    item = b.interactive_block({"kind": "form", "title": "Formulaire", "fields": fields})
    assert len(item["fields"]) == b.MAX_INTERACTIVE_FIELDS
    assert item["fields"][0]["required"] is True
    assert item["fields"][1]["required"] is False
    scales = b.interactive_block(
        {
            "kind": "form",
            "title": "Évaluer",
            "fields": [
                {"id": "a", "kind": "scale", "label": "Confort", "min": 5, "max": 8},
                {"id": "b", "kind": "scale", "label": "Urgence", "min": -999, "max": 2000},
                {"id": "a", "kind": "text", "label": "Doublon"},
                {"id": "bad id", "kind": "text", "label": "Ignoré"},
            ],
        }
    )
    assert [(f["id"], f["min"], f["max"]) for f in scales["fields"]] == [
        ("a", 5, 8),
        ("b", 0, 10),
    ]
    assert b.validate_blocks([{"type": "interactive", "kind": "script", "title": "X", "fields": []}]) == []
    assert (
        b.validate_blocks(
            [
                {
                    "type": "interactive",
                    "kind": "form",
                    "title": "X",
                    "fields": [{"id": "x", "kind": "iframe", "label": "Danger"}],
                }
            ]
        )
        == []
    )


def test_widget_is_not_a_payment_or_html_tool() -> None:
    ctx = assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False)  # type: ignore[arg-type]
    args = {
        "kind": "poll",
        "title": "Budget ?",
        "fields": [
            {"id": "answer", "kind": "single", "label": "On commence par ?", "options": ["Épargne", "Loyer"]},
        ],
    }
    result = json.loads(assistant._execute_tool(ctx, "ask_interactive", json.dumps(args)))
    assert result["done"] is True and ctx.interactive is not None
    assert ctx.blocks()[0]["type"] == "interactive"
    again = json.loads(assistant._execute_tool(ctx, "ask_interactive", json.dumps(args)))
    assert "error" in again
    assert ctx.blocks() == [b.interactive_block(args)]
    assert "questionnaire" in b.text_fallback(ctx.blocks())[0]
    assert "error" in json.loads(assistant._execute_tool(ctx, "ask_interactive", '{"kind":"form"}'))


def test_widget_prompt_and_tool_schema_are_registered() -> None:
    tool = assistant.TOOL_INTERACTIVE["function"]
    assert tool["name"] == "ask_interactive"
    assert tool["parameters"]["additionalProperties"] is False
    assert {"poll", "checklist", "form"} == set(tool["parameters"]["properties"]["kind"]["enum"])


def test_scenario_block_only_accepts_consecutive_bank_balances() -> None:
    raw = {
        "type": "scenario",
        "title": "Et si",
        "source": "Données Bank https://evil.test",
        "daily_savings_cap_cents": 2_500,
        "points": [
            {"date": "2026-10-28", "balance_cents": 50_000},
            {"date": "2026-10-29", "balance_cents": 49_000},
            {"date": "2026-10-30", "balance_cents": 48_000},
        ],
        "arbitrary": "<iframe>",
    }
    b1 = b.scenario_block(raw)
    assert b1["points"] == raw["points"]
    assert b1["daily_savings_cap_cents"] == 2_500
    assert b1["expense_cap_cents"] == 200_000
    assert b1["source"] == "Données Bank"
    assert "arbitrary" not in b1
    assert b.validate_blocks([b1]) == [b1]
    assert "simulateur" in b.text_fallback([b1])[0]

    for values in (
        [{"date": "2026-10-28", "balance_cents": True}, raw["points"][1]],
        [{"date": "2026-10-28", "balance_cents": 50_000}, {"date": "2026-11-01", "balance_cents": 40_000}],
        [{"date": "2026-10-28", "balance_cents": 50_000}, {"date": "2026-10-28", "balance_cents": 49_000}],
        [{"date": "2026-10-28", "balance_cents": 50_000}, {"date": "2026-10-30", "balance_cents": 49_000}],
    ):
        with pytest.raises(b.BlockError):
            b.scenario_block({**raw, "points": values})

    assert b.validate_blocks([{**raw, "daily_savings_cap_cents": "infinite"}]) == []
    assert b.validate_blocks([{**raw, "daily_savings_cap_cents": 100_000_000}])[0]["daily_savings_cap_cents"] == 100_000


def test_budget_simulator_tool_uses_bank_data_not_the_models_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    # La projection est simulée mais la seule source permise est le résultat du moteur Bank.
    points = [
        SimpleNamespace(date=date(2026, 10, 21), balance_cents=50_000),
        SimpleNamespace(date=date(2026, 10, 22), balance_cents=48_000),
        SimpleNamespace(date=date(2026, 10, 23), balance_cents=46_000),
    ]
    forecast = SimpleNamespace(projection=points, daily_variable_rate_cents=900, projected_end_balance_cents=46_000)
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [object()])
    monkeypatch.setattr(assistant.analytics, "forecast_inputs", lambda db, today: SimpleNamespace(run=lambda: forecast))
    ctx = assistant._ToolContext(None, "user", date(2026, 10, 21), 7, False)  # type: ignore[arg-type]
    result = json.loads(
        assistant._execute_tool(
            ctx,
            "show_budget_simulator",
            json.dumps({"points": [{"date": "2026-10-21", "balance_cents": 900_000_000}]}),
        )
    )
    assert result["done"] is True and ctx.scenario is not None
    assert ctx.scenario["points"] == [{"date": p.date.isoformat(), "balance_cents": p.balance_cents} for p in points]
    assert ctx.scenario["daily_savings_cap_cents"] == 900
    assert ctx.blocks()[0]["type"] == "scenario"
    assert "error" in json.loads(assistant._execute_tool(ctx, "show_budget_simulator", "{}"))
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [])
    other = assistant._ToolContext(None, "user", date(2026, 10, 21), 7, False)  # type: ignore[arg-type]
    no_account = json.loads(assistant._execute_tool(other, "show_budget_simulator", "{}"))
    assert "error" in no_account and other.scenario is None


def test_decision_board_validates_same_grounded_forecast_schema() -> None:
    raw = {
        "type": "decision_board",
        "title": "Mon plan",
        "source": "Bank",
        "daily_savings_cap_cents": 1_400,
        "points": [
            {"date": "2026-10-28", "balance_cents": 70_000},
            {"date": "2026-10-29", "balance_cents": 65_000},
        ],
        "model_defined_payout": 900_000_000,
    }
    result = b.decision_board_block(raw)
    assert result["type"] == "decision_board"
    assert result["points"] == raw["points"]
    assert result["daily_savings_cap_cents"] == 1_400
    assert "model_defined_payout" not in result
    assert b.validate_blocks([result]) == [result]
    assert "tableau de décision" in b.text_fallback([result])[0]
    assert b.validate_blocks([{**raw, "points": [{"date": "x", "balance_cents": 90}]}]) == []


def test_decision_board_and_scenario_are_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [
        SimpleNamespace(date=date(2026, 10, 28), balance_cents=70_000),
        SimpleNamespace(date=date(2026, 10, 29), balance_cents=65_000),
    ]
    forecast = SimpleNamespace(projection=points, daily_variable_rate_cents=1_400, projected_end_balance_cents=65_000)
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [object()])
    monkeypatch.setattr(assistant.analytics, "forecast_inputs", lambda db, today: SimpleNamespace(run=lambda: forecast))
    ctx = assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False)  # type: ignore[arg-type]
    response = json.loads(assistant._execute_tool(ctx, "show_decision_board", '{"daily_savings_cap_cents":999999999}'))
    assert response["done"] is True
    assert ctx.decision_board is not None and ctx.scenario is None
    assert ctx.decision_board["daily_savings_cap_cents"] == 1_400
    assert ctx.blocks()[0]["type"] == "decision_board"
    assert "error" in json.loads(assistant._execute_tool(ctx, "show_decision_board", "{}"))
    assert "error" in json.loads(assistant._execute_tool(ctx, "show_budget_simulator", "{}"))
    assert assistant.TOOL_BOARD["function"]["parameters"]["additionalProperties"] is False
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [])
    empty = assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False)  # type: ignore[arg-type]
    assert "error" in json.loads(assistant._execute_tool(empty, "show_decision_board", "{}"))
    assert empty.decision_board is None


def test_purchase_coach_validates_bank_verdict_and_is_idempotent() -> None:
    snapshot = {
        "per_day_cents": 3_000,
        "lowest_cents": 20_000,
        "lowest_date": "2026-10-28",
        "end_balance_cents": 45_000,
        "overdraft_date": None,
        "unsafe_html": "<script>",
    }
    data = {
        "answer": "oui_mais",
        "label": "Un vélo",
        "headline": "Oui mais attention",
        "amount_cents": 35_000,
        "this_month": {
            "before": snapshot,
            "after": {**snapshot, "lowest_cents": 8_000},
            "best_date": "2026-10-30",
        },
        "paths": [{"label": "Attendre", "months": 1, "note": "Une option"}],
        "explanation": "Reste une petite marge.",
        "extra": "<iframe>",
    }
    block = b.purchase_coach_block(data)
    assert block["answer"] == "oui_mais"
    assert block["amount_cents"] == 35_000
    assert block["before"]["lowest_cents"] == 20_000
    assert block["after"]["lowest_cents"] == 8_000
    assert block["best_date"] == "2026-10-30"
    assert "unsafe_html" not in block["before"]
    assert "extra" not in block
    assert b.validate_blocks([block]) == [block]
    assert "coach d'achat" in b.text_fallback([block])[0]
    assert b.validate_blocks([{**data, "answer": "inventé"}]) == []
    assert b.validate_blocks([{**data, "amount_cents": True}]) == []
    assert b.validate_blocks([{**data, "this_month": {"before": snapshot, "after": {}}}]) == []


def test_purchase_coach_tool_is_sourced_from_bank_not_model_data(monkeypatch: pytest.MonkeyPatch) -> None:
    before = Snapshot(3_000, 20_000, date(2026, 10, 28), None, 45_000)
    after = Snapshot(1_500, 8_000, date(2026, 10, 29), None, 10_000)
    verdict = PurchaseVerdict("serre", 35_000, date(2026, 10, 28), before, after, None, None, 2.0, "Marge réduite")
    purchase = PurchasePlan(
        "oui_mais",
        "Oui, mais la marge est serrée",
        35_000,
        verdict,
        0,
        [SavingPath("Épargne", 2, date(2026, 12, 1), 17_500, "Attendre")],
        "Marge réduite",
    )
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [object()])
    called: list[tuple[int, str]] = []

    def purchase_stub(db: object, today: date, *, amount_cents: int, label: str, category: str | None) -> PurchasePlan:
        called.append((amount_cents, label))
        return purchase

    monkeypatch.setattr(assistant.decisions, "purchase", purchase_stub)
    ctx = assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False)  # type: ignore[arg-type]
    result = json.loads(assistant._execute_tool(ctx, "show_purchase_coach", '{"amount_eur":350,"label":"Vélo"}'))
    assert result["done"] is True
    assert called == [(35_000, "Vélo")]
    assert ctx.purchase_coach is not None
    assert ctx.purchase_coach["amount_cents"] == 35_000
    assert ctx.purchase_coach["answer"] == "oui_mais"
    assert ctx.blocks() == [ctx.purchase_coach]
    assert "error" in json.loads(assistant._execute_tool(ctx, "show_purchase_coach", '{"amount_eur":200}'))
    assert "error" in json.loads(
        assistant._execute_tool(
            assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False),  # type: ignore[arg-type]
            "show_purchase_coach",
            '{"amount_eur":-20}',
        )
    )
    monkeypatch.setattr(assistant.analytics, "budget_accounts", lambda db: [])
    empty = assistant._ToolContext(None, "user", date(2026, 10, 28), 7, False)  # type: ignore[arg-type]
    assert "error" in json.loads(assistant._execute_tool(empty, "show_purchase_coach", '{"amount_eur":350}'))
    assert empty.purchase_coach is None
    assert assistant.TOOL_PURCHASE_COACH["function"]["parameters"]["additionalProperties"] is False
