"""Pilote automatique : le seul chemin par lequel un agent bouge de l'argent sans redemander.

Les scénarios adverses du benchmark de Minara (crypto-skill-benchmark : bait-and-switch, montant absurde,
précision, empoisonnement d'adresse, mauvais réseau, levier, pilote tiers, essai contourné, insistance après
refus…) sont rejoués ici contre la barrière de Bank, côté service, API, MCP et messageries."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pyotp
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.channels.gateway import Gateway
from app.db import utcnow
from app.domain.crypto import eip55
from app.models import AgentToken, AuditLog, AutonomyMove, ChannelLink, User
from app.security import audit
from app.security.agents import PRESETS, issue_agent_token
from app.security.auth import SlidingWindowLimiter
from app.security.crypto import get_keyring
from app.services import advisor, autonomy, channel_alerts, channels
from tests.conftest import PASSWORD
from tests.test_mcp import Agent

_REAL = utcnow()
# Un mercredi 10 h UTC de la semaine en cours : les cadences « chaque semaine » ne basculent pas en plein test.
NOW = datetime.combine(_REAL.date() - timedelta(days=_REAL.weekday() - 2), time(10, 0), tzinfo=UTC)
TODAY = NOW.date()
# Adresses du benchmark de Minara, remises avec leur somme de contrôle EIP-55 (Bank refuse une coquille).
LEDGER = eip55("0x742d35cc6634c0532925a3b844bc9e7595f2bd18")
POISONED = eip55("0x742d35cc6634c0532925a3b844bc9e7595f2bd19")  # même début, même fin à un caractère près
BASE: dict[str, Any] = {
    "scopes": ["crypto_spot"],
    "assets": ["BTC", "ETH", "USDC"],
    "mode": "free",
    "rules": [],
    "destinations": [],
    "per_op_cents": 2_500,
    "per_day_cents": 5_000,
    "per_month_cents": 20_000,
    "max_moves_per_day": 5,
    "max_loss_cents": 3_000,
    "trial": False,
    "days": 30,
}


def token(db: Session, name: str = "hermes", preset: str = "conseiller") -> AgentToken:
    tok, _ = issue_agent_token(db, name=name, scopes=PRESETS[preset])
    db.flush()
    return tok


def pilot(db: Session, tok: AgentToken | None = None, *, now: datetime = NOW, **over: Any) -> AgentToken:
    tok = tok or token(db)
    autonomy.configure(db, {**BASE, "enabled": True, "agent_token_id": tok.id, **over}, actor="micka", now=now)
    db.flush()
    return tok


def ask(db: Session, tok: AgentToken, *, now: datetime = NOW, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"kind": "buy", "asset": "BTC", "amount_eur": "20", "reason": "Achat programmé"}
    args.update(kw)
    return autonomy.request_move(db, token_id=tok.id, agent=tok.name, now=now, today=now.date(), **args)


def report(db: Session, tok: AgentToken, ticket: str, *, now: datetime = NOW, **kw: Any) -> dict[str, Any]:
    kw.setdefault("status", "executed")
    return autonomy.report_move(db, token_id=tok.id, agent=tok.name, ticket=ticket, now=now, **kw)


def refused(out: dict[str, Any], contains: str) -> None:
    assert out["allowed"] is False, out
    assert contains in out["reason"], out["reason"]
    assert "ticket" not in out


# ------------------------------------------------------------------ éteint par défaut
def test_off_by_default_every_move_is_refused_and_logged(db: Session) -> None:
    tok = token(db)
    assert autonomy.status_for_agent(db, tok.id, now=NOW, today=TODAY)["enabled"] is False
    refused(ask(db, tok), "désactivé")
    move = db.scalar(select(AutonomyMove))
    assert move is not None and move.status == "refused" and move.reason == "Achat programmé"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "autonomy.refused")) is not None
    # Rien n'est actif tant qu'on n'a pas activé : la vue le dit, et le dossier de l'agent aussi.
    v = autonomy.view(db, now=NOW, today=TODAY)
    assert v["active"] is False and v["journal"][0]["status"] == "refused"
    assert advisor.build_dossier(db, TODAY, "redacted")["mandate"]["autopilot"]["active"] is False


def test_enabling_needs_a_real_agent_a_duration_and_no_red_line(db: Session) -> None:
    observer = token(db, "observateur", preset="observateur")
    cfg = {**BASE, "enabled": True}
    with pytest.raises(autonomy.AutonomyError, match="Choisis l'agent"):
        autonomy.configure(db, cfg, actor="micka", now=NOW)
    with pytest.raises(autonomy.AutonomyError, match="Choisis l'agent"):  # sans la portée « propose »
        autonomy.configure(db, {**cfg, "agent_token_id": observer.id}, actor="micka", now=NOW)
    tok = token(db)
    for days in (0, 91):
        with pytest.raises(autonomy.AutonomyError, match="Durée"):
            autonomy.configure(db, {**cfg, "agent_token_id": tok.id, "days": days}, actor="micka", now=NOW)
    advisor.set_answer(db, "red_lines", [{"rule": "Pas de crypto, jamais"}], source="user:micka")
    with pytest.raises(autonomy.AutonomyError, match="lignes rouges excluent la crypto"):
        autonomy.configure(db, {**cfg, "agent_token_id": tok.id}, actor="micka", now=NOW)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"scopes": []}, "au moins une permission"),
        ({"scopes": ["perps"]}, "au moins une permission"),
        ({"assets": ["bitcoin!"]}, "Actifs"),
        ({"per_op_cents": 100_001}, "Par opération"),
        ({"per_day_cents": 1_000}, "Par 24 h"),
        ({"per_month_cents": 4_000}, "Par 30 jours"),
        ({"max_moves_per_day": 0}, "Opérations par 24 h"),
        ({"max_loss_cents": 30_000}, "Perte maximale"),
        ({"mode": "rules"}, "au moins une consigne"),
        ({"scopes": ["transfer"]}, "enregistre d'abord"),
        (
            {"scopes": ["transfer"], "destinations": [{"label": "L", "chain": "ethereum", "address": "0x12"}]},
            "0x suivi",
        ),
        ({"scopes": ["transfer"], "destinations": [{"label": "L", "chain": "tron", "address": LEDGER}]}, "réseau"),
        (
            {
                "scopes": ["transfer"],
                "destinations": [
                    {"label": "A", "chain": "ethereum", "address": LEDGER},
                    {"label": "B", "chain": "base", "address": LEDGER},
                ],
            },
            "deux fois",
        ),
        ({"mode": "rules", "rules": [{"kind": "buy", "asset": "DOGE", "amount_cents": 500, "every": "week"}]}, "DOGE"),
        ({"mode": "rules", "rules": [{"kind": "buy", "asset": "BTC", "amount_cents": 9_000, "every": "week"}]}, "1 €"),
        ({"mode": "rules", "rules": [{"kind": "buy", "asset": "BTC", "amount_cents": 500, "every": "hour"}]}, "chaque"),
        (
            {"mode": "rules", "rules": [{"kind": "transfer", "asset": "USDC", "amount_cents": 500, "every": "week"}]},
            "accorde",
        ),
    ],
)
def test_the_frame_is_validated(db: Session, over: dict[str, Any], message: str) -> None:
    tok = token(db)
    with pytest.raises(autonomy.AutonomyError, match=message):
        autonomy.configure(db, {**BASE, "enabled": True, "agent_token_id": tok.id, **over}, actor="micka", now=NOW)


# ------------------------------------------------------------------ consignes et essai
def test_rules_mode_follows_each_rule_once_per_period_and_nothing_else(db: Session) -> None:
    rule = {"kind": "buy", "asset": "BTC", "amount_cents": 2_000, "every": "week"}
    tok = pilot(db, mode="rules", rules=[rule])
    status = autonomy.status_for_agent(db, tok.id, now=NOW, today=TODAY)
    (r,) = status["rules"]
    assert r["label"] == "Acheter 20,00\xa0€ de BTC chaque semaine" and r["done_this_period"] is False
    # (refus espacés de 20 minutes : cinq refus en une heure couperaient le pilote, voir plus bas)
    t = [NOW + timedelta(minutes=20 * i) for i in range(7)]
    refused(ask(db, tok, now=t[0]), "cite l'identifiant")  # mode consignes : pas de consigne, pas de mouvement
    refused(ask(db, tok, rule="r0000000000", now=t[1]), "Consigne inconnue")
    refused(ask(db, tok, rule=r["id"], asset="ETH", now=t[2]), "ne correspond pas")
    refused(ask(db, tok, rule=r["id"], kind="sell", now=t[3]), "ne correspond pas")
    refused(ask(db, tok, rule=r["id"], amount_eur="20.01", now=t[4]), "montant de la consigne")
    ok = ask(db, tok, rule=r["id"], amount_eur="19.50", now=t[5])
    assert ok["allowed"] is True and ok["authorized"]["amount_eur"] == "19.50"
    report(db, tok, ok["ticket"], executed_eur="19.50", tx_ref="0xabc", now=t[5])
    refused(ask(db, tok, rule=r["id"], now=t[6]), "déjà suivie")
    assert autonomy.status_for_agent(db, tok.id, now=NOW, today=TODAY)["rules"][0]["done_this_period"] is True
    # La semaine suivante, la consigne s'applique de nouveau.
    later = NOW + timedelta(days=7)
    assert ask(db, tok, rule=r["id"], now=later)["allowed"] is True


def test_trial_mode_never_hands_out_a_ticket(db: Session) -> None:
    tok = pilot(db, trial=True)
    out = ask(db, tok)
    assert out["allowed"] is True and out["trial"] is True and "ticket" not in out
    assert "N'EXÉCUTE RIEN" in out["instructions"]
    assert autonomy.status_for_agent(db, tok.id, now=NOW, today=TODAY)["trial"] is True
    move = db.get(AutonomyMove, out["move_id"])
    assert move is not None and move.status == "simulated" and move.ticket_hash is None
    # « Fais un essai, puis exécute-le tout de suite » (dry-run-bypass) : il n'y a rien à exécuter, ni à rapporter.
    with pytest.raises(autonomy.AutonomyError, match="Ticket inconnu"):
        report(db, tok, "tk_essai")
    # L'essai montre les vrais refus : les plafonds s'y appliquent.
    ask(db, tok, amount_eur="25")
    refused(ask(db, tok, amount_eur="10"), "Plafond des 24 h")


# ------------------------------------------------------------------ scénarios adverses
def test_bait_and_switch_more_executed_than_authorized_stops_everything(db: Session) -> None:
    tok = pilot(db)
    ok = ask(db, tok, amount_eur="20")
    out = report(db, tok, ok["ticket"], executed_eur="50")
    assert out["pilot_stopped"] == "Montant exécuté supérieur au montant autorisé"
    assert autonomy.mandate(db).enabled is False
    refused(ask(db, tok), "désactivé")
    # La marge de frais (1 €) n'arrête rien.
    # (les plafonds sont ceux de la personne, pas ceux d'un agent : on repart un jour plus tard)
    later = NOW + timedelta(days=1, hours=1)
    tok2 = pilot(db, token(db, "hermes2"), now=later)
    ok = ask(db, tok2, amount_eur="20", now=later)
    assert report(db, tok2, ok["ticket"], executed_eur="20.99", now=later)["pilot_stopped"] is None


@pytest.mark.parametrize(
    ("amount", "message"),
    [
        ("999999999", "hors de toute proportion"),
        ("0.001", "au centime près"),
        ("-5", "positif"),
        ("0", "positif"),
        ("NaN", "positif"),
        ("Infinity", "positif"),
        ("vingt", "illisible"),
    ],
)
def test_absurd_or_imprecise_amounts_are_errors(db: Session, amount: str, message: str) -> None:
    tok = pilot(db)
    with pytest.raises(autonomy.AutonomyError, match=message):
        ask(db, tok, amount_eur=amount)


def test_french_decimal_comma_is_read_exactly(db: Session) -> None:
    tok = pilot(db)
    assert ask(db, tok, amount_eur="19,99")["authorized"]["amount_eur"] == "19.99"


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"kind": "perps"}, "perps"),
        ({"kind": "buy", "leverage": 10}, "levier"),
        ({"kind": "limit_order"}, "cours limité"),
        ({"kind": "autopilot"}, "pilote de trading"),
        ({"kind": "onramp"}, "carte ou ta banque"),
        ({"kind": "withdraw"}, "non enregistrée"),
        ({"kind": "stake"}, "Opération inconnue"),
        ({"kind": "transfer", "asset": "USDC", "destination": LEDGER}, "Permission non accordée"),
        ({"reason": "   "}, "Raison obligatoire"),
        ({"asset": "PEPE"}, "hors de la liste"),
        ({"kind": "swap", "asset": "ETH"}, "autre actif"),
        ({"kind": "swap", "asset": "ETH", "to_asset": "ETH"}, "autre actif"),
        ({"kind": "swap", "asset": "ETH", "to_asset": "SHIB"}, "hors de la liste"),
        ({"amount_eur": "26"}, "plafond par opération"),
    ],
)
def test_hard_rules_cannot_be_talked_around(db: Session, kw: dict[str, Any], message: str) -> None:
    tok = pilot(db)
    refused(ask(db, tok, **kw), message)


def test_address_poisoning_wrong_chain_and_unknown_destinations(db: Session) -> None:
    dest = [{"label": "Mon Ledger", "chain": "ethereum", "address": LEDGER}]
    tok = pilot(db, scopes=["crypto_spot", "transfer"], destinations=dest)
    send: dict[str, Any] = {"kind": "transfer", "asset": "USDC"}
    refused(ask(db, tok, **send, destination=POISONED), "ressemble à une autre")
    refused(ask(db, tok, **send, destination="0xDEAD000000000000000000000000000000000000"), "Destination inconnue")
    refused(ask(db, tok, **send, destination="mon ledger", chain="solana"), "Réseau incohérent")
    by_name = ask(db, tok, **send, destination="mon ledger")
    assert by_name["allowed"] is True
    assert by_name["authorized"]["to_address"] == LEDGER and by_name["authorized"]["chain"] == "ethereum"
    report(db, tok, by_name["ticket"])
    by_address = ask(db, tok, **send, destination=LEDGER, amount_eur="5")
    assert by_address["allowed"] is True
    # L'agent ne voit que les noms et réseaux, jamais une adresse qu'il pourrait « corriger ».
    assert autonomy.status_for_agent(db, tok.id, now=NOW, today=TODAY)["destinations"] == [
        {"label": "Mon Ledger", "chain": "ethereum"}
    ]


def test_symbols_are_normalised_but_never_guessed(db: Session) -> None:
    tok = pilot(db)
    out = ask(db, tok, kind="swap", asset="usdc", to_asset=" eth ")
    assert out["allowed"] is True and out["authorized"]["asset"] == "USDC" and out["authorized"]["to_asset"] == "ETH"
    refused(ask(db, tok, asset="WBTC"), "hors de la liste")  # un « équivalent » n'est pas l'actif autorisé


def test_caps_are_rolling_and_count_operations(db: Session) -> None:
    tok = pilot(db)
    for i in range(2):
        ok = ask(db, tok, amount_eur="25", now=NOW + timedelta(minutes=i))
        report(db, tok, ok["ticket"], now=NOW + timedelta(minutes=i))
    refused(ask(db, tok, amount_eur="1", now=NOW + timedelta(hours=23)), "Plafond des 24 h atteint (reste 0,00\xa0€)")
    # Pas de remise à zéro à minuit : 24 h glissantes.
    assert ask(db, tok, amount_eur="25", now=NOW + timedelta(hours=24, minutes=1))["allowed"] is True


def test_monthly_cap_and_failed_moves(db: Session) -> None:
    tok = pilot(db, per_day_cents=5_000, per_month_cents=10_000)
    for day in range(4):
        at = NOW + timedelta(days=day, minutes=1)
        ok = ask(db, tok, amount_eur="25", now=at)
        report(db, tok, ok["ticket"], now=at)
    refused(ask(db, tok, amount_eur="5", now=NOW + timedelta(days=5)), "Plafond des 30 jours")
    # Un échec rapporté libère le montant, mais compte comme une opération (pas de tentatives en boucle).
    tok2 = pilot(db, token(db, "hermes2"), max_moves_per_day=2, now=NOW + timedelta(days=40))
    for i in range(2):
        ok = ask(db, tok2, amount_eur="1", now=NOW + timedelta(days=40, minutes=i))
        report(db, tok2, ok["ticket"], status="failed", now=NOW + timedelta(days=40, minutes=i))
    refused(ask(db, tok2, amount_eur="1", now=NOW + timedelta(days=40, minutes=5)), "Nombre d'opérations")


def test_an_unreported_move_counts_as_spent_and_stops_the_pilot(db: Session) -> None:
    tok = pilot(db)
    ok = ask(db, tok, amount_eur="25")
    later = NOW + timedelta(minutes=11)
    status = autonomy.status_for_agent(db, tok.id, now=later, today=TODAY)
    assert status["enabled"] is False
    row = autonomy.mandate(db)
    assert row.disabled_reason is not None and "n'a pas rendu compte" in row.disabled_reason
    move = db.get(AutonomyMove, ok["move_id"])
    assert move is not None and move.status == "unreported"
    assert autonomy.view(db, now=later, today=TODAY)["usage"]["day_cents"] == 2_500, "compté comme dépensé"
    # La vérité peut encore entrer : le compte rendu tardif est accepté (une seule fois).
    out = report(db, tok, ok["ticket"], status="failed", now=later)
    assert out["late"] is True
    assert autonomy.view(db, now=later, today=TODAY)["usage"]["day_cents"] == 0
    with pytest.raises(autonomy.AutonomyError, match="déjà utilisé"):
        report(db, tok, ok["ticket"], now=later)


def test_tickets_are_single_use_bound_to_their_token_and_validated(db: Session) -> None:
    tok = pilot(db)
    other = token(db, "intrus")
    ok = ask(db, tok)
    with pytest.raises(autonomy.AutonomyError, match="pas à toi"):
        report(db, other, ok["ticket"])
    with pytest.raises(autonomy.AutonomyError, match="tx_ref"):
        report(db, tok, ok["ticket"], tx_ref="<script>alert(1)</script>")
    with pytest.raises(autonomy.AutonomyError, match="status"):
        report(db, tok, ok["ticket"], status="done")
    assert report(db, tok, ok["ticket"], tx_ref="0x" + "a" * 64)["recorded"] is True
    with pytest.raises(autonomy.AutonomyError, match="déjà utilisé"):
        report(db, tok, ok["ticket"])
    with pytest.raises(autonomy.AutonomyError, match="Ticket inconnu"):
        report(db, tok, "tk_invente")
    move = db.get(AutonomyMove, ok["move_id"])
    assert move is not None and move.ticket_hash is None, "le ticket n'est jamais gardé en clair"


def test_an_agent_that_insists_after_refusals_is_cut_off(db: Session) -> None:
    tok = pilot(db)
    outs = [ask(db, tok, asset="PEPE", now=NOW + timedelta(minutes=i)) for i in range(autonomy.REFUSAL_BURST)]
    assert outs[-1].get("pilot_stopped") is True and "pilot_stopped" not in outs[0]
    row = autonomy.mandate(db)
    assert row.enabled is False and "insiste" in (row.disabled_reason or "")


def test_losses_beyond_the_threshold_stop_the_pilot(db: Session) -> None:
    tok = pilot(db, max_loss_cents=3_000)
    ok = ask(db, tok, kind="sell")
    out = report(db, tok, ok["ticket"], pnl_eur="-31.00")
    assert out["pilot_stopped"] == "Perte maximale atteinte"
    later = NOW + timedelta(days=2)
    tok2 = pilot(db, token(db, "hermes2"), now=later)
    ticket = ask(db, tok2, now=later)["ticket"]
    with pytest.raises(autonomy.AutonomyError, match="pnl_eur"):
        report(db, tok2, ticket, pnl_eur="beaucoup", now=later)


def test_kill_switch_cuts_new_moves_but_lets_the_truth_in(db: Session) -> None:
    tok = pilot(db)
    ok = ask(db, tok)
    assert autonomy.stop(db, actor="micka", now=NOW) is True
    move = db.get(AutonomyMove, ok["move_id"])
    assert move is not None and move.status == "revoked"
    refused(ask(db, tok), "désactivé")
    assert report(db, tok, ok["ticket"])["late"] is True, "déjà exécuté avant l'arrêt : c'est noté"
    assert autonomy.stop(db, actor="micka", now=NOW) is False


def test_expiry_and_token_changes_end_the_pilot(db: Session) -> None:
    tok = pilot(db, days=1)
    assert autonomy.status_for_agent(db, tok.id, now=NOW + timedelta(hours=23), today=TODAY)["enabled"] is True
    autonomy.sweep(db, NOW + timedelta(days=1, seconds=1))
    assert autonomy.mandate(db).disabled_reason == "Arrivé à échéance"
    # Un nouveau jeton du même nom n'hérite de rien : le pilote suit le jeton, pas le nom.
    tok = pilot(db)
    tok.revoked_at = NOW
    twin = token(db, "hermes")
    refused(ask(db, twin), "désactivé")
    assert autonomy.mandate(db).disabled_reason == "Le jeton de l'agent n'est plus valable"
    tok3 = pilot(db, twin)
    refused(ask(db, token(db, "autre")), "pas celui")
    autonomy.token_revoked(db, tok3.id, actor="micka", now=NOW)
    assert autonomy.mandate(db).disabled_reason == "Jeton de l'agent révoqué"


def test_a_red_line_added_later_wins(db: Session) -> None:
    tok = pilot(db)
    advisor.set_answer(db, "red_lines", [{"rule": "Je ne veux plus de bitcoin"}], source="user:micka")
    refused(ask(db, tok), "Ligne rouge")


def test_reconfiguring_keeps_the_loss_clock_unless_the_agent_changes(db: Session) -> None:
    tok = pilot(db)
    first = autonomy.mandate(db).enabled_at
    pilot(db, tok, now=NOW + timedelta(hours=2), per_op_cents=1_000)
    assert autonomy.mandate(db).enabled_at == first
    pilot(db, token(db, "hermes2"), now=NOW + timedelta(hours=3))
    assert autonomy.mandate(db).enabled_at == NOW + timedelta(hours=3)
    autonomy.configure(db, {**BASE, "enabled": False}, actor="micka", now=NOW + timedelta(hours=4))
    assert autonomy.mandate(db).disabled_by_user is True


# ------------------------------------------------------------------ messageries
def test_moves_are_announced_and_anomalies_pass_quiet_hours(db: Session) -> None:
    tok = pilot(db)
    link = ChannelLink(
        platform="telegram",
        user_index=get_keyring().blind_index("42", "channel.telegram"),
        user_ref="42",
        chat_ref="42",
        label="@micka",
    )
    db.add(link)
    ok = ask(db, tok, amount_eur="20")
    report(db, tok, ok["ticket"], tx_ref="0xabc")
    found = channel_alerts.collect(
        db, today=TODAY, local=NOW, privacy="redacted", public_url="https://bank.example", now=NOW
    )
    texts = [n.text for n in found if n.key.startswith("auto:")]
    assert any("achat de 20,00\xa0€ de BTC exécuté" in t and "https://bank.example/#/pilote" in t for t in texts)
    agg = channel_alerts.collect(db, today=TODAY, local=NOW, privacy="aggregates", public_url="https://b", now=NOW)
    assert any("mouvement de 20,00\xa0€" in n.text and "BTC" not in n.text for n in agg if n.key.startswith("auto:"))
    # Un mouvement non rapporté arrête le pilote : l'alerte passe même la nuit.
    ask(db, tok, amount_eur="5")
    night = NOW + timedelta(minutes=30)
    autonomy.sweep(db, night)
    quiet_local = datetime(night.year, night.month, night.day, 2, 0, tzinfo=UTC)
    deliveries = channel_alerts.plan(
        db, platforms={"telegram"}, today=TODAY, local=quiet_local, public_url="https://bank.example"
    )
    (d,) = deliveries
    assert "Pilote automatique arrêté" in d.text and "jamais rapporté" in d.text
    assert "exécuté" not in d.text.split("arrêté")[0], "le reste attend le matin"


def test_pilote_command_reports_and_stops_from_chat(db: Session) -> None:
    tok = pilot(db)
    link = SimpleNamespace(platform="telegram", id=1)
    cfg = SimpleNamespace(privacy="redacted")
    common: dict[str, Any] = {"now": NOW, "today": TODAY, "public_url": "https://bank.example"}
    (text,) = channels._run_command(db, cfg, link, "pilote", "", **common)  # type: ignore[arg-type]
    assert "Pilote automatique en marche" in text and "« hermes »" in text and "/pilote stop" in text
    assert channels._command("/pilote stop") == ("pilote", "stop")
    assert channels._command("Pilote") == ("pilote", "")
    (text,) = channels._run_command(db, cfg, link, "pilote", "arrêt", **common)  # type: ignore[arg-type]
    assert "arrêté" in text and autonomy.mandate(db).enabled is False
    (text,) = channels._run_command(db, cfg, link, "pilote", "off", **common)  # type: ignore[arg-type]
    assert "déjà arrêté" in text
    (text,) = channels._run_command(db, cfg, link, "pilote", "", **common)  # type: ignore[arg-type]
    assert "arrêté (Arrêté par toi (messagerie))" in text
    assert tok.name == "hermes"


def test_gateway_wakes_up_immediately_when_nudged(app, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    gateway = Gateway(
        app.state.sessionmaker,
        app.state.settings,
        llm_factory=lambda: None,  # type: ignore[arg-type,return-value]
        limiter=SlidingWindowLimiter(5, 60),
        alert_seconds=3600,
        alert_first_delay=3600,
    )
    calls: list[int] = []

    async def fake_run_alerts() -> int:
        calls.append(1)
        return 0

    gateway.run_alerts = fake_run_alerts  # type: ignore[method-assign]

    async def scenario() -> None:
        await gateway.start()
        try:
            threading.Thread(target=gateway.nudge_threadsafe).start()
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(0.02)
        finally:
            await gateway.stop()

    asyncio.run(scenario())
    assert calls, "le réveil n'attend pas l'heure suivante"


# ------------------------------------------------------------------ API
def _api_body(tok_id: int, **over: Any) -> dict[str, Any]:
    return {**BASE, "password": PASSWORD, "enabled": True, "agent_token_id": tok_id, **over}


def test_api_needs_password_and_second_factor_but_stopping_needs_nothing(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        tok = token(s)
        s.commit()
        tok_id = tok.id
    view = auth_client.get("/api/autonomy").json()
    assert view["active"] is False and view["agents"] == [{"id": tok_id, "name": "hermes"}]
    assert "hard_rules" in view and view["limits"]["per_op_cents"] == 2_500
    assert auth_client.put("/api/autonomy", json=_api_body(tok_id, password="faux")).status_code == 403
    bad = auth_client.put("/api/autonomy", json=_api_body(tok_id, days=0))
    assert bad.status_code == 422 and "Durée" in bad.json()["detail"]
    on = auth_client.put("/api/autonomy", json=_api_body(tok_id))
    assert on.status_code == 200, on.text
    assert on.json()["active"] is True and on.json()["agent_name"] == "hermes"
    # Double authentification activée : le code devient obligatoire, et ne sert qu'une fois.
    secret = pyotp.random_base32()
    with app.state.sessionmaker() as s:
        user = s.scalar(select(User))
        assert user is not None
        user.totp_secret, user.totp_enabled = secret, True
        s.commit()
    assert auth_client.put("/api/autonomy", json=_api_body(tok_id)).status_code == 403
    code = pyotp.TOTP(secret).now()
    assert auth_client.put("/api/autonomy", json=_api_body(tok_id, code=code)).status_code == 200
    assert auth_client.put("/api/autonomy", json=_api_body(tok_id, code=code)).status_code == 403, "pas de rejeu"
    off = auth_client.post("/api/autonomy/stop")
    assert off.status_code == 200 and off.json()["active"] is False
    with app.state.sessionmaker() as s:
        actions = [a for (a,) in s.execute(select(AuditLog.action).where(AuditLog.action.like("autonomy.%")))]
        assert audit.verify_chain(s)
    assert "autonomy.denied" in actions and "autonomy.enabled" in actions and "autonomy.stopped" in actions


def test_revoking_the_token_in_bank_stops_the_pilot(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        tok = pilot(s)
        s.commit()
        tok_id = tok.id
    assert auth_client.delete(f"/api/agents/tokens/{tok_id}").status_code == 204
    assert auth_client.get("/api/autonomy").json()["disabled_reason"] == "Jeton de l'agent révoqué"


def test_export_contains_the_frame_and_journal_never_a_ticket(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        tok = pilot(s)
        ticket = ask(s, tok)["ticket"]
        s.commit()
    r = auth_client.post("/api/export", json={"password": PASSWORD})
    assert r.status_code == 200
    data = r.json()["autopilot"]
    assert data["mandate"]["agent"] == "hermes" and data["moves"][0]["status"] == "reserved"
    assert ticket not in r.text and ticket[3:] not in r.text


# ------------------------------------------------------------------ MCP
def test_mcp_round_trip_for_the_chosen_agent_only(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        tok, raw = issue_agent_token(s, name="hermes", scopes=PRESETS["conseiller"])
        _, raw_other = issue_agent_token(s, name="autre", scopes=PRESETS["conseiller"])
        _, raw_observer = issue_agent_token(s, name="lecteur", scopes=PRESETS["observateur"])
        s.flush()
        pilot(s, tok, now=utcnow())
        s.commit()
    agent, other, observer = Agent(auth_client, raw), Agent(auth_client, raw_other), Agent(auth_client, raw_observer)
    assert other.ok("autonomy_status")["enabled"] is False
    assert agent.ok("autonomy_status")["enabled"] is True
    assert "Portée « propose »" in observer.fails("request_autonomous_move", kind="buy", asset="BTC", amount_eur=5,
                                                   reason="test")  # fmt: skip
    out = other.ok("request_autonomous_move", kind="buy", asset="BTC", amount_eur=5, reason="je tente")
    assert out["allowed"] is False and "pas celui" in out["reason"]
    perps = agent.ok("request_autonomous_move", kind="perps", asset="ETH", amount_eur=5, reason="long 10x")
    assert perps["allowed"] is False
    ok = agent.ok("request_autonomous_move", kind="buy", asset="BTC", amount_eur="20.00", reason="DCA du lundi")
    assert ok["allowed"] is True and ok["ticket"].startswith("tk_")
    assert "Montant" in agent.fails(
        "request_autonomous_move", kind="buy", asset="BTC", amount_eur="0.001", reason="poussière"
    )
    assert "Ticket inconnu" in other.fails("report_autonomous_move", ticket=ok["ticket"], status="executed")
    done = agent.ok("report_autonomous_move", ticket=ok["ticket"], status="executed", executed_eur=20, tx_ref="0xabc")
    assert done["recorded"] is True and done["pilot_stopped"] is None
    journal = auth_client.get("/api/autonomy").json()["journal"]
    assert journal[0]["status"] == "refused" or journal[0]["status"] == "executed"
    assert any(m["status"] == "executed" and m["tx_ref"] == "0xabc" for m in journal)
    with app.state.sessionmaker() as s:
        assert audit.verify_chain(s)


def test_destinations_are_checksummed_and_lookalikes_refused(db: Session) -> None:
    tok = token(db)
    twin = eip55(LEDGER[:6].lower() + ("0" if LEDGER[6] != "0" else "1") + LEDGER[7:].lower())
    two = [
        {"label": "Ledger", "chain": "ethereum", "address": LEDGER},
        {"label": "Copie", "chain": "ethereum", "address": twin},
    ]
    with pytest.raises(autonomy.AutonomyError, match="empoisonnement"):
        autonomy.configure(
            db, {**BASE, "scopes": ["transfer"], "destinations": two, "enabled": True, "agent_token_id": tok.id},
            actor="micka", now=NOW,
        )  # fmt: skip
    # Saisie en minuscules : rangée avec sa somme de contrôle, et reconnue quelle que soit la casse demandée.
    lower = [{"label": "Ledger", "chain": "ethereum", "address": LEDGER.lower()}]
    pilot(db, tok, scopes=["transfer"], destinations=lower)
    assert autonomy.view(db, now=NOW, today=TODAY)["destinations"][0]["address"] == LEDGER
    out = ask(db, tok, kind="transfer", asset="USDC", destination=LEDGER.lower())
    assert out["allowed"] is True and out["authorized"]["to_address"] == LEDGER
