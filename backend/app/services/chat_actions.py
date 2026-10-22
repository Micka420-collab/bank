"""Catalogue des boutons proposables dans le chat, calculé à chaque question : relier ce qui manque (couverture),
ouvrir un écran, agir sur une carte « Bank te demande » (par l'API des initiatives, avec ses contrôles), poser la
question suivante. Le modèle ne connaît que les clés ; il ne peut rien proposer d'autre."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.domain import chatblocks as blocks
from app.domain.coverage import Source
from app.services import coverage, initiatives

STATIC: tuple[tuple[str, str, str, str], ...] = (
    ("go:aujourdhui", "Voir mon tableau de bord", "aujourdhui", "reste à dépenser, fin de mois, année qui vient"),
    ("go:operations", "Voir mes opérations", "operations", "une opération précise, un marchand, une catégorie"),
    ("go:opportunites", "Ce que je peux gagner", "opportunites", "missions, aides à demander, argent qui dort"),
    ("go:aides", "Mes aides et déclarations", "aides", "une aide reçue, une actualisation, une déclaration"),
    ("go:documents", "Ouvrir Papiers", "documents", "déposer ou retrouver un document"),
    ("go:demarches", "Mes démarches", "demarches", "une démarche en cours ou à préparer"),
    ("go:patrimoine", "Mon épargne et mon patrimoine", "patrimoine", "épargne, objectifs, valeur nette"),
    ("go:situation", "Ma situation", "situation", "foyer, logement, droits simulés, corrélations"),
    ("go:crypto", "Mes crypto", "crypto", "actifs crypto, arnaques, impôts crypto"),
    ("go:connecteurs", "Connecteurs", "connecteurs", "banque, Linky, modèle, recherche web"),
)  # fmt: skip

FOLLOW_UPS: tuple[tuple[str, str, str], ...] = (
    ("ask:spend", "Où est parti mon argent ?", "Où est parti mon argent ce mois-ci ? Montre-moi la répartition."),
    ("ask:year", "Et l'année qui vient ?", "À quoi ressemble l'année qui vient pour mon solde, avec les pics ?"),
    ("ask:aids", "Quelles aides je pourrais demander ?", "Quelles aides je pourrais demander, d'après ma situation ?"),
    ("ask:save", "Comment épargner plus ?", "Qu'est-ce que je devrais faire en priorité pour épargner plus ?"),
)


def _connect(source: Source) -> blocks.ChatAction:
    return blocks.navigate(
        f"connect:{source.key}",
        source.label,
        source.page,
        hint=(
            f"si la question touche « {source.title.lower()} » ({source.detail}) : relier débloquerait {source.unlocks}"
        ),
    )


def build(db: Session, today: date, *, sources: list[Source] | None = None) -> blocks.Catalogue:
    cat = blocks.Catalogue()
    for s in sources if sources is not None else coverage.sources(db, today):
        if s.state != "connected":
            cat.add(_connect(s))
    for c in initiatives.cards(db, today)[:12]:
        if c.input is not None:
            continue  # une saisie en ligne : ça se fait sur la carte, pas depuis un bouton du chat
        for a in c.actions:
            effect = dict(a.effect)
            if a.style == "link" or effect.get("type") in ("snooze", "dismiss"):
                continue
            key = f"card:{c.key}:{a.id}"
            if effect.get("type") == "navigate" and blocks.valid_page(str(effect.get("page", ""))):
                cat.add(blocks.navigate(key, a.label, str(effect["page"]), hint=f"carte « {c.title} »"))
            elif effect.get("type") != "navigate":
                cat.add(blocks.act(key, a.label, c.key, a.id, hint=f"carte « {c.title} » : {c.body[:90]}"))
    for key, label, page, hint in STATIC:
        cat.add(blocks.navigate(key, label, page, hint=hint))
    for key, label, prompt in FOLLOW_UPS:
        cat.add(blocks.ask(key, label, prompt, hint="question suivante toute prête"))
    return cat
