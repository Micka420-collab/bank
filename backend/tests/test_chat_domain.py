"""Le chat interactif, côté domaine : ce que Bank sait ou pas (couverture), les blocs d'une réponse (boutons,
graphique, schéma, sources) toujours bornés et validés, la requête web nettoyée, les résultats normalisés, une
page réduite à son texte."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain import chatblocks as blocks
from app.domain import coverage, websearch

TODAY = date(2026, 10, 22)


# ------------------------------------------------------------------ couverture
def test_nothing_connected_puts_the_bank_first_then_what_unlocks_the_most() -> None:
    srcs = coverage.sources(coverage.CoverageInput(today=TODAY))
    keys = [s.key for s in srcs]
    assert keys[:2] == ["bank", "statements"], "sans opération, la banque et les relevés passent avant tout"
    assert all(s.state != "connected" for s in srcs)
    nxt = coverage.next_steps(srcs)
    assert [s.key for s in nxt] == ["bank", "statements", "obligations"] or nxt[0].key == "bank"
    assert all(blocks.valid_page(s.page) for s in srcs), "chaque bouton mène à un écran qui existe"
    assert coverage.score(srcs) == 0


def test_tenant_with_bank_connected_is_asked_for_the_lease_then_insurance() -> None:
    inp = coverage.CoverageInput(
        today=TODAY,
        bank_connected=2,
        transactions=400,
        months_of_data=9,
        last_transaction=TODAY,
        aids_seen=("ft_are", "caf_logement"),
        housing_status="locataire",
        totp=True,
        passkeys=1,
        profile_percent=80,
        goals=1,
    )
    srcs = coverage.sources(inp)
    by = {s.key: s for s in srcs}
    assert by["bank"].state == "connected" and "2 compte(s)" in by["bank"].detail
    missing = [s.key for s in coverage.next_steps(srcs, limit=4)]
    assert missing[0] == "obligations", "des aides reçues sans déclaration suivie : la priorité"
    assert "lease" in missing and "insurance" in missing
    assert "doublon d'assurance" in by["lease"].unlocks
    assert "totp" not in by and "passkeys" not in by and "profile" not in by and "goals" not in by
    assert 0 < coverage.score(srcs) < 100
    lines = coverage.prompt_lines(srcs)
    assert lines[0].startswith("Connu : Banque") and any("[bouton lease]" in line for line in lines)


def test_owner_is_not_asked_for_a_lease_and_a_stale_bank_asks_to_reconnect() -> None:
    inp = coverage.CoverageInput(
        today=TODAY,
        bank_connected=1,
        transactions=50,
        last_transaction=date(2026, 9, 20),
        housing_status="proprietaire",
    )
    srcs = coverage.sources(inp)
    by = {s.key: s for s in srcs}
    assert "lease" not in by
    assert by["bank"].state == "partial" and by["bank"].label == "Reconnecter ma banque"
    assert coverage.next_steps(srcs)[0].key == "bank"


def test_retirement_asks_for_the_career_statement_once_the_birth_date_is_known() -> None:
    nothing = {s.key for s in coverage.sources(coverage.CoverageInput(today=TODAY))}
    assert "retirement" not in nothing, "sans date de naissance, c'est le profil du foyer qui manque, pas le relevé"
    partial = {s.key: s for s in coverage.sources(coverage.CoverageInput(today=TODAY, birth_known=True))}
    assert partial["retirement"].state == "partial" and partial["retirement"].page == "patrimoine?retraite=1"
    assert "trimestres" in partial["retirement"].unlocks and blocks.valid_page(partial["retirement"].page)
    done = {s.key: s for s in coverage.sources(coverage.CoverageInput(today=TODAY, birth_known=True, retirement=True))}
    assert done["retirement"].state == "connected"


def test_energy_states_and_totp_before_passkeys() -> None:
    err = coverage.sources(coverage.CoverageInput(today=TODAY, energy="error"))
    assert next(s for s in err if s.key == "energy").state == "partial"
    with_totp = coverage.sources(coverage.CoverageInput(today=TODAY, totp=True))
    keys = {s.key for s in with_totp}
    assert "passkeys" in keys and "totp" not in keys
    without = {s.key for s in coverage.sources(coverage.CoverageInput(today=TODAY, totp=False))}
    assert "totp" in without and "passkeys" not in without


# ------------------------------------------------------------------ blocs
def test_clean_strips_control_characters_bidi_and_links() -> None:
    assert blocks.clean("Voir‮ ici http://evil.example/x  maintenant", 60) == "Voir ici maintenant"
    assert blocks.clean("a" * 100, 10) == "a" * 10
    assert blocks.clean(None, 10) == ""
    assert blocks.clean("Lien https://ok.example", 40, links=True) == "Lien https://ok.example"


def test_catalogue_resolves_only_known_keys_and_caps_the_count() -> None:
    cat = blocks.Catalogue()
    cat.add(blocks.navigate("connect:bank", "Connecter ma banque", "connecteurs", hint="banque"))
    cat.add(blocks.ask("ask:spend", "Où est parti mon argent ?", "Où est parti mon argent ce mois-ci ?"))
    cat.add(blocks.act("card:x:follow", "Suivre", "obligation:suggest:caf_dtr", "follow"))
    with pytest.raises(blocks.BlockError):
        blocks.navigate("bad", "x", "admin")  # un écran qui n'existe pas
    with pytest.raises(blocks.BlockError):
        blocks.navigate("bad", "x", "operations?import=../x")
    resolved = cat.resolve(["connect:bank", "connect:bank", "ask:spend", "nope", 42, "card:x:follow"])
    assert [a.key for a in resolved] == ["connect:bank", "ask:spend", "card:x:follow"]
    block = blocks.actions_block(resolved)
    assert block is not None and [it["kind"] for it in block["items"]] == ["navigate", "ask", "act"]
    assert block["items"][0]["page"] == "connecteurs" and block["items"][2]["card"] == "obligation:suggest:caf_dtr"
    assert any("connect:bank" in line and "banque" in line for line in cat.prompt_lines())


def test_chart_block_keeps_integers_colors_and_bounds() -> None:
    items = [
        {"label": "Courses", "value": "12345.6", "color": "#AABBCC"},
        {"label": "", "value": 1},
        {"label": "X", "value": "abc"},
    ]
    block = blocks.chart_block("donut", "Dépenses", items=items, source="Bank", dataset="spend_by_category")
    assert block["items"] == [{"label": "Courses", "value": 12346, "color": "#AABBCC"}, {"label": "X", "value": 0}]
    toned = blocks.chart_block(
        "donut",
        "x",
        items=[{"label": "A", "value": 1, "color": "#0b7480", "tone": "c1"}, {"label": "B", "tone": "var(--x)"}],
    )
    assert toned["items"] == [{"label": "A", "value": 1, "color": "#0b7480", "tone": "c1"}, {"label": "B", "value": 0}]
    pairs = blocks.chart_block("pairs", "Reçu/dépensé", items=[{"label": "oct.", "a": 100, "b": 50}])
    assert pairs["items"][0] == {"label": "oct.", "a": 100, "b": 50}
    with pytest.raises(blocks.BlockError):
        blocks.chart_block("pie", "x")
    big = blocks.chart_block("bars", "x", items=[{"label": str(i), "value": i} for i in range(60)])
    assert len(big["items"]) == blocks.MAX_CHART_ITEMS


def test_diagram_block_is_bounded_and_ignores_broken_edges() -> None:
    raw = {
        "kind": "flow",
        "title": "Comment l'APL se calcule",
        "nodes": [
            {"id": "Loyer", "label": "Loyer et charges"},
            {"id": "ress", "label": "Ressources du foyer (N-2 puis en continu)", "note": "salaires, chômage…"},
            {"id": "apl", "label": "Montant de l'APL"},
            {"id": "loyer", "label": "doublon ignoré"},
            {"id": "bad id!", "label": "ignoré"},
            {"id": "lien", "label": "voir https://caf.fr"},
        ],
        "edges": [
            {"from": "loyer", "to": "apl", "label": "plafonné par zone"},
            {"from": "ress", "to": "apl"},
            {"from": "ress", "to": "apl"},
            {"from": "apl", "to": "apl"},
            {"from": "nope", "to": "apl"},
        ],
    }
    d = blocks.diagram_block(raw)
    assert [n["id"] for n in d["nodes"]] == ["loyer", "ress", "apl", "lien"]
    assert d["nodes"][3]["label"] == "voir" and d["nodes"][1]["note"].startswith("salaires")
    assert d["edges"] == [{"from": "loyer", "to": "apl", "label": "plafonné par zone"}, {"from": "ress", "to": "apl"}]
    steps = blocks.diagram_block({"kind": "steps", "nodes": [{"id": "a", "label": "Un"}, {"id": "b", "label": "Deux"}]})
    assert steps["edges"] == [{"from": "a", "to": "b"}]
    with pytest.raises(blocks.BlockError):
        blocks.diagram_block({"nodes": [{"id": "a", "label": "seul"}]})


def test_sources_block_keeps_https_only_and_validate_blocks_drops_junk() -> None:
    src = blocks.sources_block(
        [
            {"title": "CAF", "url": "https://www.caf.fr/x", "host": "www.caf.fr", "snippet": "APL…"},
            {"title": "pas https", "url": "http://x.example", "host": "x.example", "snippet": ""},
        ],
        "apl 2026",
    )
    assert src is not None and [it["n"] for it in src["items"]] == [1] and src["query"] == "apl 2026"
    out = blocks.validate_blocks(
        [
            {
                "type": "actions",
                "items": [
                    {"kind": "navigate", "label": "x", "page": "evil"},
                    {"kind": "ask", "label": "Q ?", "prompt": "q"},
                ],
            },
            {"type": "chart", "kind": "bars", "title": "t", "items": [{"label": "a", "value": 1}]},
            {"type": "diagram", "nodes": [{"id": "a", "label": "x"}]},
            {"type": "weird"},
            {"type": "note", "text": "ok", "tone": "shout"},
            src,
            "nope",
        ]
    )
    assert [b["type"] for b in out] == ["actions", "chart", "note", "sources"]
    assert out[0]["items"] == [{"kind": "ask", "label": "Q ?", "prompt": "q"}]
    assert out[2]["tone"] == "info"
    assert blocks.validate_blocks("rien") == []
    text = blocks.text_fallback(out)
    assert text[0] == "Dans Bank : " or "Sources :" in text, "les questions suivantes n'ont pas de sens par texto"
    assert any(line.startswith("[1] CAF — https://www.caf.fr/x") for line in text)


# ------------------------------------------------------------------ recherche web
def test_query_is_scrubbed_and_secrets_stop_it() -> None:
    q = websearch.scrub_query(
        "APL 2026 plafond loyer zone 2 pour M. Dupont, dossier 1234567890, tel 06 12 34 56 78, micka@example.org"
    )
    assert "Dupont" not in q and "1234567890" not in q and "06 12" not in q and "@" not in q
    assert q.startswith("APL 2026 plafond loyer zone 2")
    with pytest.raises(websearch.SearchError, match="IBAN"):
        websearch.scrub_query("virement vers FR76 3000 6000 0112 3456 7890 189 frais")  # un IBAN : rien ne part
    with pytest.raises(websearch.SearchError):
        websearch.scrub_query("mon mot de passe est hunter2 ")
    with pytest.raises(websearch.SearchError):
        websearch.scrub_query("   ")
    assert len(websearch.scrub_query("x" * 500)) == websearch.MAX_QUERY


def test_results_are_normalised_https_only_deduplicated_and_capped() -> None:
    payload = {
        "results": [
            {
                "url": "https://www.service-public.fr/a",
                "title": "<b>APL</b>",
                "content": "Aide au logement…",
                "publishedDate": "2026-05-01T00:00:00",
            },
            {"url": "https://www.service-public.fr/a", "title": "doublon"},
            {"url": "http://insecure.example", "title": "non"},
            {"url": "https://user:pw@evil.example/x", "title": "identifiants"},
            *[{"url": f"https://x{i}.example/", "title": f"r{i}", "content": ""} for i in range(10)],
        ]
    }
    out = websearch.parse_searxng(payload)
    assert len(out) == websearch.MAX_RESULTS and out[0].title == "APL" and out[0].host == "www.service-public.fr"
    assert out[0].date == "2026-05-01" and out[0].snippet == "Aide au logement…"
    brave = websearch.parse_brave(
        {
            "web": {
                "results": [{"title": "Brave", "url": "https://b.example/p", "description": "d", "age": "2 days ago"}]
            }
        }
    )
    assert brave[0].url == "https://b.example/p" and brave[0].snippet == "d"
    assert websearch.parse_brave({"web": "x"}) == [] and websearch.parse_searxng(None) == []
    shown = websearch.results_for_model(out[:2])
    assert shown[0]["n"] == 1 and "url" not in shown[0] and shown[0]["site"] == "www.service-public.fr"


def test_html_to_text_drops_scripts_and_keeps_readable_lines() -> None:
    html = b"""<html><head><title>T</title><script>alert(1)</script><style>p{}</style></head>
    <body><nav>menu</nav><h1>Titre</h1><p>Un   paragraphe\n avec   des espaces.</p><script>evil()</script>
    <ul><li>un</li><li>deux</li></ul><p>&eacute;t&eacute; &amp; co</p></body></html>"""
    text = websearch.html_to_text(html)
    assert "alert" not in text and "evil" not in text and "p{}" not in text
    assert "Titre\nUn paragraphe avec des espaces." in text and "un\ndeux" in text and "été & co" in text
    assert websearch.html_to_text("<p>" + "x" * 20_000 + "</p>", limit=100) == "x" * 100
