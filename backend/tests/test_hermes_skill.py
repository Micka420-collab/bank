"""La compétence HERMÈS et son banc d'essai restent cohérents avec le serveur : chaque outil cité existe, chaque
référence est livrée, chaque scénario a la forme attendue par crypto-skill-bench, et l'installateur écrit sa
règle de routage une seule fois."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from app.security.agents import PRESETS, issue_agent_token
from tests.test_mcp import Agent

HERMES = Path(__file__).resolve().parents[2] / "integrations" / "hermes"
SKILL = HERMES / "skills" / "finance" / "gestionnaire-argent"
DIMENSIONS = {"safety", "routing", "robustness", "coverage", "ux"}
EXPECTED_BOOLS = {
    "confirms_before_execution",
    "shows_summary",
    "warns_about_unknown_token",
    "does_not_execute_blindly",
    "suggests_alternatives",
}


def frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "frontmatter YAML manquant"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict)
    return data


def test_skill_frontmatter_and_references() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = frontmatter(text)
    assert meta["name"] == "gestionnaire-argent" and meta["version"] == "2.0.0"
    assert "crypto" in meta["metadata"]["hermes"]["tags"]
    for ref in set(re.findall(r"references/([\w-]+\.md)", text)):
        assert (SKILL / "references" / ref).is_file(), ref
    # Les règles qui comptent sont écrites noir sur blanc.
    for must in ("Porte d'entrée", "Jamais de confirmation inventée", "Anti-boucle", "request_autonomous_move",
                 "Phrase de récupération, clé privée : jamais", "adresse en entier"):  # fmt: skip
        assert must in text, must


def test_every_tool_named_in_the_skill_exists(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        _, raw = issue_agent_token(s, name="hermes", scopes=PRESETS["gestionnaire"])
        s.commit()
    tools = {t["name"] for t in Agent(auth_client, raw).rpc("tools/list")["tools"]}
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in SKILL.rglob("*.md"))
    cited = set(re.findall(r"mcp_bank_(\w+)", corpus))
    cited |= set(re.findall(r"`(\w+)\(", corpus))
    unknown = sorted(cited - tools)
    assert not unknown, f"outils cités mais absents du serveur : {unknown}"
    for core in ("get_crypto", "crypto_check", "autonomy_status", "request_autonomous_move", "report_autonomous_move"):
        assert core in corpus and core in tools


def test_bench_scenarios_follow_the_crypto_skill_bench_schema() -> None:
    files = sorted((HERMES / "bench" / "scenarios").rglob("*.yaml"))
    assert len(files) >= 15
    names = set()
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert set(data) <= {
            "name",
            "category",
            "tier",
            "intent",
            "turns",
            "context",
            "expected",
            "dimensions_tested",
        }, f
        assert data["name"] and data["intent"], f
        assert data["category"] in ("core", "adversarial") and data["tier"] in ("basic", "intermediate", "adversarial")
        assert f.parent.name == data["category"], f
        assert data.get("turns") is None or all(isinstance(t, str) for t in data["turns"])
        ctx = data.get("context") or {}
        assert set(ctx) <= {"chain", "balance", "note"}
        assert all(isinstance(v, int | float) for v in (ctx.get("balance") or {}).values())
        exp = data["expected"]
        assert set(exp) <= EXPECTED_BOOLS | {"correct_command", "correct_args"}
        assert all(isinstance(exp[k], bool) for k in EXPECTED_BOOLS & set(exp))
        assert all(isinstance(v, str) for v in (exp.get("correct_args") or {}).values())
        assert data["dimensions_tested"] and set(data["dimensions_tested"]) <= DIMENSIONS
        assert data["name"] not in names
        names.add(data["name"])
    adversarial = [f for f in files if f.parent.name == "adversarial"]
    assert len(adversarial) >= 10


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_installer_writes_the_routing_rule_once(tmp_path: Path) -> None:
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text("BANK_MCP_TOKEN=bnk_" + "a" * 40 + "\n")
    env = {**os.environ, "HERMES_HOME": str(home), "PATH": os.environ.get("PATH", "")}
    args = ["bash", str(HERMES / "install.sh"), "--url", "http://127.0.0.1:9/mcp", "--hermes-home", str(home)]
    for _ in range(2):
        subprocess.run(args, env=env, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    memory = (home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert memory.count("## Bank — argent de Micka") == 1
    assert "request_autonomous_move" in memory
    assert (home / "skills" / "finance" / "gestionnaire-argent" / "references" / "autonomie.md").is_file()
    assert list((home / "memories").glob("MEMORY.md.bak-*")), "sauvegarde avant la première écriture"
