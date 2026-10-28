"""Widgets du chat : structures bornées, sans HTML, données libres ni action automatique."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain import chatblocks as b
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


def test_budget_simulator_tool_uses_bank_data_not_the_models_arguments(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        ctx = assistant._ToolContext(db, "user", date(2026, 10, 21), 7, False)
        answer = json.loads(
            assistant._execute_tool(
                ctx,
                "show_budget_simulator",
                json.dumps({"points": [{"date": "2026-10-21", "balance_cents": 900_000_000}]}),
            )
        )
        if ctx.scenario:
            assert answer["done"] is True
            assert ctx.scenario["points"][0]["date"] == "2026-10-21"
            assert ctx.scenario["points"][0]["balance_cents"] != 900_000_000
            assert ctx.blocks()[0]["type"] == "scenario"
            assert "error" in json.loads(assistant._execute_tool(ctx, "show_budget_simulator", "{}"))
        else:
            assert "error" in answer  # Aucun compte budgétaire dans le scénario de test.
