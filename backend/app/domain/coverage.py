"""Ce que Bank sait de toi, et ce qu'il ne sait pas encore — avec ce que chaque connexion débloquerait.

C'est la matière des initiatives de l'assistant : plutôt que d'attendre une question, il propose de relier ce qui
manque, dans l'ordre de ce que ça rapporte (la banque d'abord, puis les papiers qui nourrissent les corrélations,
puis le reste). Tout est calculé ici, sans modèle : un bouton « Connecter ma banque » n'est jamais inventé, il
correspond à un écran de Bank qui existe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

STALE_AFTER_DAYS = 10  # une banque connectée sans opération depuis dix jours : la connexion a peut-être expiré
OWNER = frozenset({"proprietaire", "proprietaire_credit"})


@dataclass(frozen=True)
class CoverageInput:
    today: date
    bank_connected: int = 0  # comptes alimentés par la banque (DSP2)
    file_accounts: int = 0  # comptes alimentés par import de relevés
    manual_accounts: int = 0
    transactions: int = 0
    months_of_data: float = 0.0
    last_transaction: date | None = None
    aids_seen: Sequence[str] = ()  # natures d'aides reconnues sur douze mois (caf_apl, france_travail…)
    obligations_active: Sequence[str] = ()
    documents: Mapping[str, int] | None = None  # nature de papier -> nombre (lease, insurance, payslip…)
    energy: str = "none"  # none | active | error | revoked
    crypto_wallets: int = 0
    crypto_holdings: int = 0
    profile_percent: int = 0
    housing_status: str | None = None
    channels: Sequence[str] = ()
    assistant_configured: bool = False
    memory_decided: bool = False
    search_configured: bool = False
    totp: bool = False
    passkeys: int = 0
    goals: int = 0
    push_devices: int = 0  # appareils qui reçoivent les notifications du navigateur
    backup: str = "none"  # none | unverified | ok | failed | stale : la sauvegarde chiffrée et sa restauration d'essai


@dataclass(frozen=True)
class Source:
    """Une connexion possible : ce qu'elle apporte, où elle se fait, où on en est."""

    key: str
    title: str
    state: str  # connected | partial | missing
    detail: str  # où on en est, en une ligne
    unlocks: str  # ce que la connexion débloque, concret
    page: str  # écran de Bank (fragment de route)
    label: str  # libellé du bouton
    weight: int  # plus petit = plus utile


def _docs(inp: CoverageInput, kind: str) -> int:
    return int((inp.documents or {}).get(kind, 0))


def _bank(inp: CoverageInput) -> Source:
    if inp.bank_connected:
        stale = inp.last_transaction is not None and (inp.today - inp.last_transaction).days > STALE_AFTER_DAYS
        if stale and inp.last_transaction is not None:
            days = (inp.today - inp.last_transaction).days
            return Source(
                "bank",
                "Banque",
                "partial",
                f"connectée, mais aucune opération depuis {days} jours",
                "des prévisions à jour : le consentement bancaire expire au bout de 180 jours, reconnecte-la",
                "connecteurs",
                "Reconnecter ma banque",
                1,
            )
        return Source(
            "bank",
            "Banque",
            "connected",
            f"{inp.bank_connected} compte(s) mis à jour automatiquement",
            "",
            "connecteurs",
            "Voir mes connexions",
            90,
        )
    if inp.file_accounts:
        return Source(
            "bank",
            "Banque",
            "partial",
            f"{inp.file_accounts} compte(s) alimenté(s) par import de relevés",
            "la mise à jour automatique (DSP2, lecture seule) : plus rien à importer à la main, les échéances et "
            "les aides suivies au jour le jour",
            "connecteurs",
            "Connecter ma banque",
            2,
        )
    return Source(
        "bank",
        "Banque",
        "missing",
        "aucun compte connecté",
        "tout le reste : reste à dépenser, échéances, aides reconnues, année qui vient, corrélations",
        "connecteurs",
        "Connecter ma banque",
        0,
    )


def sources(inp: CoverageInput) -> list[Source]:
    """Toutes les connexions, connues ou non, triées : ce qui manque et rapporte le plus d'abord."""
    out: list[Source] = [_bank(inp)]
    if not inp.transactions and not inp.bank_connected:
        out.append(
            Source(
                "statements",
                "Relevés",
                "missing",
                "aucune opération",
                "lire ton salaire, ton loyer, tes abonnements dès maintenant, sans attendre la banque (CSV ou OFX)",
                "operations?import=1",
                "Importer un relevé",
                1,
            )
        )
    if inp.housing_status not in OWNER:  # locataire, hébergé, ou pas encore dit : le bail compte
        if _docs(inp, "lease"):
            out.append(Source("lease", "Bail", "connected", "bail lu", "", "documents", "Voir mes papiers", 91))
        else:
            out.append(
                Source(
                    "lease",
                    "Bail",
                    "missing",
                    "pas de bail dans Papiers",
                    "vérifier ton loyer face au bail et à la révision IRL, le dépôt de garantie, et repérer un "
                    "doublon d'assurance habitation (celle du bail et celle que ta banque prélève)",
                    "documents",
                    "Déposer mon bail",
                    3,
                )
            )
    if _docs(inp, "insurance"):
        out.append(Source("insurance", "Assurances", "connected", "attestation(s) lue(s)", "", "documents", "Voir", 92))
    else:
        out.append(
            Source(
                "insurance",
                "Assurances",
                "missing",
                "aucune attestation d'assurance",
                "repérer deux assurances pour la même chose, une prime qui monte, et préparer une résiliation",
                "documents",
                "Déposer une attestation d'assurance",
                4,
            )
        )
    if inp.transactions and not _docs(inp, "payslip"):
        out.append(
            Source(
                "payslip",
                "Bulletins de paie",
                "missing",
                "aucun bulletin",
                "le net social prérempli pour la déclaration trimestrielle, et l'écart entre le bulletin et le "
                "virement reçu",
                "documents",
                "Déposer un bulletin de paie",
                5,
            )
        )
    if not _docs(inp, "tax_notice"):
        out.append(
            Source(
                "tax_notice",
                "Avis d'impôt",
                "missing",
                "pas d'avis d'impôt",
                "ta tranche exacte et ton revenu N-2 : des aides mieux calculées",
                "documents",
                "Déposer mon avis d'impôt",
                6,
            )
        )
    declared_aids = {k for k in inp.aids_seen if k.startswith(("ft_", "caf_", "msa_"))}
    if declared_aids and not inp.obligations_active:
        out.append(
            Source(
                "obligations",
                "Déclarations",
                "missing",
                "des aides reçues, aucune déclaration suivie",
                "ne plus rater une actualisation ou une déclaration trimestrielle : l'échéance rappelée, les "
                "chiffres préparés, le versement constaté",
                "aides",
                "Suivre mes déclarations",
                2,
            )
        )
    if inp.energy == "active":
        out.append(Source("energy", "Linky", "connected", "consommation relevée", "", "connecteurs", "Voir", 93))
    elif inp.energy in ("error", "revoked"):
        out.append(
            Source(
                "energy",
                "Linky",
                "partial",
                "connexion en erreur ou révoquée",
                "retrouver tes relevés de consommation et anticiper la régularisation",
                "connecteurs",
                "Vérifier Linky",
                7,
            )
        )
    else:
        out.append(
            Source(
                "energy",
                "Linky",
                "missing",
                "pas de compteur relié",
                "expliquer ta facture d'électricité et anticiper la régularisation annuelle",
                "connecteurs",
                "Relier mon compteur Linky",
                8,
            )
        )
    if inp.crypto_wallets or inp.crypto_holdings:
        out.append(Source("crypto", "Crypto", "connected", "suivie en lecture seule", "", "crypto", "Voir", 94))
    else:
        out.append(
            Source(
                "crypto",
                "Crypto",
                "missing",
                "aucune adresse ni quantité",
                "si tu en as : suivi en lecture seule (jamais de clé), valeur dans ton patrimoine, formulaire 2086",
                "crypto",
                "Suivre mes crypto",
                12,
            )
        )
    if inp.profile_percent < 70:
        out.append(
            Source(
                "profile",
                "Ma situation",
                "partial" if inp.profile_percent else "missing",
                f"renseignée à {inp.profile_percent} %",
                "simuler tes droits (OpenFisca) et un plan B si un revenu s'arrête",
                "situation",
                "Compléter ma situation",
                6,
            )
        )
    if not inp.goals:
        out.append(
            Source(
                "goals",
                "Objectifs d'épargne",
                "missing",
                "aucun objectif",
                "un cap pour l'épargne : « je peux m'acheter… ? » répond avec ton objectif en tête",
                "patrimoine",
                "Fixer un objectif",
                10,
            )
        )
    if not inp.search_configured:
        out.append(
            Source(
                "search",
                "Recherche web",
                "missing",
                "aucun moteur de recherche branché",
                "des réponses à jour avec leurs sources (SearXNG chez toi, ou Brave)",
                "connecteurs",
                "Brancher la recherche web",
                11,
            )
        )
    if not inp.channels:
        out.append(
            Source(
                "channels",
                "Messageries",
                "missing",
                "aucune messagerie reliée",
                "les alertes et les questions depuis Telegram, Signal, Discord, Slack ou WhatsApp",
                "messageries",
                "Relier une messagerie",
                13,
            )
        )
    if not inp.push_devices:
        out.append(
            Source(
                "push",
                "Notifications",
                "missing",
                "aucun appareil prévenu",
                "être prévenu sur ton téléphone (découvert qui approche, déclaration à faire, gain à confirmer) "
                "sans messagerie, message chiffré pour ton appareil",
                "reglages",
                "Activer les notifications",
                9,
            )
        )
    if inp.backup == "ok":
        out.append(
            Source("backup", "Sauvegarde", "connected", "copie chiffrée relue avec succès", "", "reglages", "Voir", 95)
        )
    elif inp.backup in ("failed", "stale"):
        out.append(
            Source(
                "backup",
                "Sauvegarde",
                "partial",
                "la dernière sauvegarde a échoué" if inp.backup == "failed" else "aucune sauvegarde depuis deux jours",
                "dormir tranquille : une copie chiffrée chaque nuit, relue chaque semaine",
                "reglages",
                "Voir mes sauvegardes",
                3,
            )
        )
    elif inp.backup in ("none", "unverified"):
        out.append(
            Source(
                "backup",
                "Sauvegarde",
                "partial" if inp.backup == "unverified" else "missing",
                "sauvegardée, restauration pas encore vérifiée" if inp.backup == "unverified" else "aucune sauvegarde",
                "dormir tranquille : une copie chiffrée chaque nuit, relue chaque semaine, visible ici",
                "reglages",
                "Vérifier ma sauvegarde" if inp.backup == "unverified" else "Lancer une sauvegarde",
                12,
            )
        )
    if not inp.totp:
        out.append(
            Source(
                "totp",
                "Double authentification",
                "missing",
                "mot de passe seul",
                "un code de ton téléphone en plus : indispensable pour des données bancaires",
                "reglages",
                "Activer la double authentification",
                9,
            )
        )
    elif not inp.passkeys:
        out.append(
            Source(
                "passkeys",
                "Passkeys",
                "missing",
                "aucune passkey",
                "te connecter avec Face ID, une empreinte ou une clé, sans mot de passe ni risque de phishing",
                "reglages",
                "Ajouter une passkey",
                14,
            )
        )
    out.sort(key=lambda s: ({"missing": 0, "partial": 0, "connected": 1}[s.state], s.weight, s.key))
    return out


def next_steps(srcs: Sequence[Source], limit: int = 3) -> list[Source]:
    return [s for s in srcs if s.state != "connected"][:limit]


def score(srcs: Sequence[Source]) -> int:
    """Part des connexions faites, en pourcentage : un repère, pas une note."""
    if not srcs:
        return 0
    done = sum(1 for s in srcs if s.state == "connected")
    return round(100 * done / len(srcs))


def prompt_lines(srcs: Sequence[Source]) -> list[str]:
    """Pour le modèle : ce qui est connu, ce qui manque et ce que ça débloquerait (sans libellés privés)."""
    known = [f"{s.title} ({s.detail})" for s in srcs if s.state == "connected"]
    missing = [
        f"{s.title} — {s.detail} ; relier débloquerait : {s.unlocks} [bouton {s.key}]"
        for s in srcs
        if s.state != "connected"
    ]
    out = []
    if known:
        out.append("Connu : " + " ; ".join(known) + ".")
    if missing:
        out.append("Pas encore relié :")
        out.extend(f"- {m}" for m in missing[:8])
    return out


def source_to_dict(s: Source) -> dict[str, Any]:
    return {
        "key": s.key,
        "title": s.title,
        "state": s.state,
        "detail": s.detail,
        "unlocks": s.unlocks,
        "page": s.page,
        "label": s.label,
    }
