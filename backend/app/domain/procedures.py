"""Démarches : modèles sourcés, champs typés (chaque valeur garde sa provenance), pièces tirées du coffre,
contrôles de cohérence, revue complète liée à une empreinte, suivi. Module pur : aucune base, aucun réseau.

Principes :
* un modèle dit d'où vient chaque étape (sources officielles datées) et avec quelle confiance ;
* Bank prépare, vérifie et explique ; c'est toi qui transmets — Bank n'envoie jamais rien à un organisme ;
* une valeur qui contredit le justificatif dont elle vient bloque la préparation jusqu'à correction ;
* la validation porte sur une empreinte du dossier complet : un changement après validation l'annule ;
* un dossier n'est marqué « envoyé » qu'une fois (empreinte unique), avec ta référence si tu en as une.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain import vault as dv
from app.domain.money import format_eur

STAGES = ("verify", "documents", "ready", "sent", "waiting", "done", "abandoned")
STAGE_LABELS = {
    "discover": "À découvrir",
    "verify": "À vérifier",
    "documents": "Documents nécessaires",
    "ready": "Prêt à valider",
    "sent": "Envoyé",
    "waiting": "En attente",
    "done": "Terminé",
    "abandoned": "Abandonné",
}
OPEN_STAGES = frozenset({"verify", "documents", "ready"})
FIELD_TYPES = (*dv.FIELD_TYPES, "choice", "bool", "longtext")
MAX_LONGTEXT = 2_000


@dataclass(frozen=True)
class Source:
    title: str
    url: str | None
    verified_on: str | None  # date de dernière vérification (par la source ou par nous), AAAA-MM-JJ
    kind: str = "officiel"  # officiel | secondaire | texte (référence juridique)


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    type: str
    required: bool = True
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    when: tuple[str, str] | None = None  # (champ, valeur) : demandé seulement dans ce cas


@dataclass(frozen=True)
class PieceDef:
    key: str
    label: str
    kinds: tuple[str, ...]
    count: int = 1
    max_age_days: int | None = None
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class Declaration:
    key: str
    text: str
    kind: str  # honneur | engagement | consequence


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    summary: str
    category: str  # aide | banque | abonnement | epargne | autre
    organisme: str
    channel: str  # online_official | letter | counter | libre
    recipient: str
    official_url: str | None
    steps: tuple[str, ...]
    sources: tuple[Source, ...]
    confidence: str  # élevée | moyenne | faible
    fields: tuple[FieldDef, ...]
    pieces: tuple[PieceDef, ...] = ()
    declarations: tuple[Declaration, ...] = ()
    followup_days: int = 30
    followup_label: str = ""
    letter: Callable[[dict[str, Any], date | None], str] | None = None


# ============================================================================ lettres
def _money(cents: Any) -> str:
    return format_eur(int(cents)) if cents not in (None, "") else "…"


def _fr_date(d: date | None) -> str:
    return f"{d.day} {dv.MONTHS[d.month - 1]} {d.year}" if d else "[date d'envoi]"


def _fees_letter(v: dict[str, Any], today: date | None) -> str:
    reasons = {
        "plafond": (
            "Ces frais me paraissent dépasser les plafonds fixés par le Code monétaire et financier : les "
            "commissions d'intervention sont limitées à 8 € par opération et 80 € par mois (article R. 312-4-1)"
            + (
                ", et à 4 € par opération et 20 € par mois pour un client en situation de fragilité financière "
                "(article R. 312-4-2)"
                if v.get("fragile")
                else ""
            )
            + " ; les frais de rejet de prélèvement ne peuvent dépasser 20 € ni le montant du prélèvement rejeté "
            "(article D. 133-6)."
        ),
        "geste": (
            "Compte tenu de ma situation"
            + (" de fragilité financière" if v.get("fragile") else "")
            + ", je vous demande à titre commercial le remboursement de ces frais."
        ),
        "erreur": "Ces frais ne me semblent correspondre à aucune opération ou à aucun service que j'aurais demandé.",
    }
    lines = [
        str(v.get("nom") or "…"),
        "",
        f"À l'attention du service clients — {v.get('banque') or 'ma banque'}",
        "",
        f"Le {_fr_date(today)}",
        "",
        f"Objet : réclamation — frais bancaires ({v.get('periode') or 'période à préciser'})",
        "",
        "Madame, Monsieur,",
        "",
        "Titulaire du compte "
        + (f"{v['compte']} " if v.get("compte") else "")
        + f"dans votre établissement, je constate que des frais d'un montant total de {_money(v.get('montant'))} "
        f"ont été prélevés ({v.get('periode') or 'période à préciser'}).",
    ]
    if v.get("detail"):
        lines += ["", "Détail :", str(v["detail"])]
    lines += [
        "",
        reasons.get(str(v.get("motif")), reasons["geste"]),
        "",
        f"Je vous demande donc le remboursement de {_money(v.get('montant'))} sur mon compte.",
        "",
        "À défaut de réponse satisfaisante dans un délai de deux mois, je saisirai le médiateur de votre "
        "établissement.",
        "",
        "Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.",
        "",
        str(v.get("nom") or "…"),
    ]
    return "\n".join(lines)


def _cancel_letter(v: dict[str, Any], today: date | None) -> str:
    effect = v.get("date_effet")
    when = f"à compter du {_fr_date(date.fromisoformat(str(effect)))}" if effect else "dans les meilleurs délais"
    lines = [
        str(v.get("nom") or "…"),
        "",
        f"À l'attention de {v.get('fournisseur') or 'mon fournisseur'}",
        "",
        f"Le {_fr_date(today)}",
        "",
        "Objet : résiliation de mon contrat"
        + (f" (référence client : {v['reference_client']})" if v.get("reference_client") else ""),
        "",
        "Madame, Monsieur,",
        "",
        f"Je vous informe de ma décision de résilier mon contrat {when}.",
    ]
    if v.get("motif"):
        lines += ["", f"Motif : {v['motif']}"]
    lines += [
        "",
        "Je vous remercie de m'adresser une confirmation écrite de cette résiliation indiquant sa date d'effet, et de "
        "cesser tout prélèvement à compter de cette date.",
        "",
        "Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.",
        "",
        str(v.get("nom") or "…"),
    ]
    return "\n".join(lines)


def _rent_dpe_letter(v: dict[str, Any], today: date | None) -> str:
    """Révision de loyer sur un logement classé F ou G (#34) : la règle citée telle que Service-Public l'écrit."""
    revised = v.get("date_revision")
    when = _fr_date(date.fromisoformat(str(revised))) if revised else "[date de la révision]"
    dpe = f" (DPE n° {v['numero_dpe']})" if v.get("numero_dpe") else ""
    before, rise = v.get("loyer_avant"), v.get("hausse")
    after = _money(int(before) + int(rise)) if before is not None and rise is not None else "…"
    lines = [
        str(v.get("nom") or "…"),
        str(v.get("adresse_logement") or "[adresse du logement]"),
        "",
        f"À l'attention de {v.get('bailleur') or 'mon bailleur'}",
        "",
        f"Le {_fr_date(today)}",
        "",
        f"Objet : révision de loyer non due — logement classé {v.get('classe') or 'F ou G'}",
        "",
        "Madame, Monsieur,",
        "",
        f"Locataire du logement cité en tête, je constate que le montant payé chaque mois a été révisé le {when}, "
        f"passant de {_money(before)} à {after}.",
        "",
        f"Or ce logement est classé {v.get('classe') or 'F ou G'} au diagnostic de performance énergétique{dpe}. "
        "Comme le rappelle Service-Public (fiche F1311), réviser le loyer est interdit pour un logement classé F ou G "
        "lorsque le bail est signé, renouvelé ou tacitement reconduit depuis le 24 août 2022 en métropole (depuis le "
        "1er juillet 2024 en Guadeloupe, Guyane, Martinique, à La Réunion et à Mayotte), ce qui est le cas de mon "
        "bail.",
        "",
        f"Je vous demande donc de ramener le loyer au montant d'avant la révision et de me rembourser les sommes "
        f"versées au titre de cette révision, soit {_money(v.get('montant'))} "
        f"({v.get('periode') or 'période à préciser'}).",
        "",
        "Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.",
        "",
        str(v.get("nom") or "…"),
    ]
    return "\n".join(lines)


# ============================================================================ modèles
_SP_F1311 = Source(
    "Service-Public — Augmentation et révision du loyer en cours de bail d'habitation (fiche F1311, vérifiée le "
    "08/08/2025)",
    "https://www.service-public.gouv.fr/particuliers/vosdroits/F1311",
    "2026-10-24",
)
_ADEME_DPE = Source(
    "ADEME — base des DPE des logements existants (dpe03existant)",
    "https://data.ademe.fr/datasets/dpe03existant",
    "2026-10-24",
)
_CAF_FICHE = Source(
    "Cnaf — fiche pratique « Faire une demande de prime d'activité » (mise à jour 07/2022)",
    "https://www.caf.fr/sites/default/files/medias/cnaf/Aides_et_demarches/Mes-demarches/Fiches-pratiques/"
    "Faire-une-demande-de-Prime-d-activite.pdf",
    "2026-10-21",
)
_CAF_DOCS = Source(
    "Caf du Nord — les documents nécessaires pour la demande de prime d'activité",
    "https://caf.fr/allocataires/caf-du-nord/offre-de-service/thematique-libre/"
    "les-documents-necessaires-pour-la-demande-de-prime-d-activite",
    "2026-10-21",
)
_CAF_NET_SOCIAL = Source(
    "Caf du Nord — RSA, prime d'activité : je déclare le montant net social (05/02/2025)",
    "https://caf.fr/allocataires/caf-du-nord/actualites-departementales/rsa-prime-d-activite-je-declare-le-montant-net-social",
    "2026-10-21",
)
_INC_MEDIATION = Source(
    "INC — médiation bancaire : délais de traitement des réclamations (recommandation ACPR 2022-R-01)",
    "https://www.inc-conso.fr/content/mediation-bancaire-et-assurantielle-amelioration-de-la-procedure-de-traitement",
    "2026-10-21",
    "secondaire",
)
_CMF_FEES = Source(
    "Code monétaire et financier, art. R. 312-4-1, R. 312-4-2 (commissions d'intervention) et D. 133-6 (rejet de "
    "prélèvement) ; Code de la consommation, art. L. 612-2 (médiateur : un an après la réclamation écrite)",
    None,
    None,
    "texte",
)
_COMPARATOR = Source(
    "Comparateur public des tarifs bancaires (ministère de l'Économie et CCSF)",
    "https://www.tarifs-bancaires.gouv.fr/",
    "2026-10-21",
)
_INC_CANCEL = Source(
    "INC — résilier un contrat d'abonnement en quelques clics (loi 2022-1158, décret 2023-417, art. L. 215-1-1 "
    "du Code de la consommation)",
    "https://www.inc-conso.fr/content/vous-pouvez-resilier-votre-contrat-dabonnement-en-quelques-clics",
    "2026-10-21",
    "secondaire",
)
_LEP = Source(
    "economie.gouv.fr — Livret d'épargne populaire : comment ça marche ? (15/07/2026)",
    "https://www.economie.gouv.fr/node/35358",
    "2026-10-21",
)
_AMELI_CSS = Source(
    "Ameli — Complémentaire santé solidaire : comment la demander ? (18/06/2026)",
    "https://www.ameli.fr/assure/droits-demarches/difficultes-acces-droits-soins/complementaire-sante/"
    "complementaire-sante-solidaire-demande",
    "2026-10-21",
)

_YES_NO = (("oui", "Oui"), ("non", "Non"))

TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            key="prime_activite",
            title="Demander la prime d'activité",
            summary="Demande en ligne sur caf.fr avec tes ressources des 3 derniers mois, prêtes à recopier.",
            category="aide",
            organisme="CAF",
            channel="online_official",
            recipient="Ta Caf, par ton espace Mon Compte sur caf.fr",
            official_url="https://www.caf.fr/",
            steps=(
                "Sur caf.fr : Aides et démarches › Mes démarches › Faire une demande de prestation en ligne › Vie "
                "professionnelle › Prime d'activité.",
                "Connecte-toi (ou crée ton compte, ou passe par FranceConnect). Ton numéro de sécurité sociale "
                "t'est demandé : saisis-le directement sur caf.fr, Bank ne le garde pas.",
                "Étapes « Accès » et « Situation » : ta situation familiale et ta situation professionnelle du "
                "trimestre précédent.",
                "Étape « Logement » : ton statut et ton loyer.",
                "Étape « Ressources » : pour chaque mois, recopie le montant net social de ton bulletin (le mois où "
                "le salaire est arrivé sur ton compte).",
                "Étape « Coordonnées bancaires » si tu n'es pas encore allocataire.",
                "Vérifie le récapitulatif et le montant estimé, puis « Valider ». Un accusé de réception arrive par "
                "e-mail ; envoie les pièces demandées depuis Mon Compte › Mes démarches.",
            ),
            sources=(_CAF_FICHE, _CAF_DOCS, _CAF_NET_SOCIAL),
            confidence="moyenne",
            fields=(
                FieldDef(
                    "situation",
                    "Situation familiale",
                    "choice",
                    choices=(("seul", "Seul(e)"), ("couple", "En couple")),
                ),
                FieldDef("enfants", "Enfants à charge", "number", help="0 si aucun"),
                FieldDef(
                    "logement",
                    "Logement",
                    "choice",
                    choices=(("locataire", "Locataire"), ("proprietaire", "Propriétaire"), ("heberge", "Hébergé(e)")),
                ),
                FieldDef("loyer", "Loyer mensuel", "amount", when=("logement", "locataire")),
                FieldDef("salaire_m1", "Montant net social — mois le plus récent", "amount"),
                FieldDef("salaire_m2", "Montant net social — mois précédent", "amount"),
                FieldDef("salaire_m3", "Montant net social — il y a trois mois", "amount"),
                FieldDef(
                    "autres_ressources",
                    "Autres ressources sur les 3 mois (allocations, pensions…)",
                    "amount",
                    required=False,
                ),
                FieldDef("allocataire", "Déjà allocataire de la Caf ?", "choice", choices=_YES_NO),
            ),
            pieces=(
                PieceDef(
                    "bulletins",
                    "Bulletins de paie des 3 derniers mois",
                    ("payslip",),
                    count=3,
                    max_age_days=130,
                ),
                PieceDef(
                    "rib",
                    "RIB",
                    ("rib",),
                    required=False,
                    help="Seulement si tu n'es pas encore allocataire",
                ),
                PieceDef(
                    "identite",
                    "Pièce d'identité",
                    ("identity",),
                    required=False,
                    help="Demandée pour une première demande ; elle reste dans ton coffre, tu la déposes sur caf.fr",
                ),
                PieceDef(
                    "avis",
                    "Avis d'impôt (N-2)",
                    ("tax_notice",),
                    required=False,
                    help="Si tu es indépendant ou as des revenus de placements ou fonciers",
                ),
            ),
            declarations=(
                Declaration(
                    "exactitude",
                    "Ce que tu déclares sur caf.fr engage ta responsabilité : une erreur ou un oubli peut conduire la "
                    "Caf à te réclamer les sommes versées en trop (un « indu »).",
                    "consequence",
                ),
                Declaration(
                    "trimestrielle",
                    "Tu t'engages à déclarer tes ressources tous les trois mois pour continuer à percevoir la prime.",
                    "engagement",
                ),
                Declaration(
                    "changements",
                    "Tu t'engages à signaler tout changement de situation (emploi, logement, couple, enfants) dans "
                    "ton espace Mon Compte.",
                    "engagement",
                ),
            ),
            followup_days=21,
            followup_label="Regarde l'état de ta demande dans Mon Compte",
        ),
        Template(
            key="contester_frais",
            title="Contester des frais bancaires",
            summary="Une réclamation écrite à ta banque, avec les plafonds légaux, puis le médiateur si besoin.",
            category="banque",
            organisme="Ta banque",
            channel="letter",
            recipient="Le service clients de ta banque (messagerie sécurisée ou courrier)",
            official_url="https://www.tarifs-bancaires.gouv.fr/",
            steps=(
                "Envoie la lettre par la messagerie sécurisée de ta banque, ou par courrier recommandé ; garde une "
                "copie et la date d'envoi.",
                "Ta banque doit accuser réception sous 10 jours ouvrables et te répondre sous deux mois au plus.",
                "Sans réponse au bout de deux mois, ou si la réponse ne te convient pas, tu peux saisir gratuitement "
                "le médiateur de ta banque (ses coordonnées figurent sur tes relevés et sa brochure tarifaire), dans "
                "l'année qui suit ta réclamation écrite.",
            ),
            sources=(_INC_MEDIATION, _CMF_FEES, _COMPARATOR),
            confidence="élevée",
            fields=(
                FieldDef("nom", "Ton nom et prénom (signature de la lettre)", "text"),
                FieldDef("banque", "Banque", "text"),
                FieldDef("compte", "Compte (4 derniers chiffres)", "text", required=False),
                FieldDef("periode", "Période des frais", "text", help="Par exemple « septembre 2026 »"),
                FieldDef("montant", "Montant total contesté", "amount"),
                FieldDef("detail", "Détail des frais (date, libellé, montant)", "longtext", required=False),
                FieldDef(
                    "motif",
                    "Motif",
                    "choice",
                    choices=(
                        ("plafond", "Frais au-delà des plafonds légaux"),
                        ("geste", "Demande de geste commercial"),
                        ("erreur", "Frais injustifiés"),
                    ),
                ),
                FieldDef(
                    "fragile",
                    "Situation de fragilité financière (offre client fragile, incidents répétés)",
                    "bool",
                    required=False,
                ),
            ),
            pieces=(
                PieceDef(
                    "releve",
                    "Relevé de compte montrant les frais",
                    ("bank_statement",),
                    required=False,
                    max_age_days=400,
                ),
            ),
            declarations=(
                Declaration(
                    "nom_propre",
                    "Cette lettre part en ton nom : relis les montants, les dates et le motif avant de l'envoyer.",
                    "engagement",
                ),
            ),
            followup_days=60,
            followup_label="Deux mois sans réponse : tu peux saisir le médiateur",
            letter=_fees_letter,
        ),
        Template(
            key="resilier_abonnement",
            title="Résilier un abonnement",
            summary="La fonction « résilier votre contrat » du site du fournisseur, ou une lettre recommandée.",
            category="abonnement",
            organisme="Le fournisseur",
            channel="letter",
            recipient="Le service clients du fournisseur",
            official_url=None,
            steps=(
                "Si tu as souscrit en ligne (ou si le fournisseur le permet) : sur son site, la fonction « résilier "
                "votre contrat » est obligatoire depuis le 1er juin 2023, sans créer de compte ; confirme avec le "
                "bouton de notification et garde l'e-mail de confirmation (il indique la date d'effet).",
                "Sinon : envoie la lettre en recommandé avec accusé de réception.",
                "Un préavis, une durée minimale d'engagement ou des frais prévus au contrat peuvent s'appliquer : "
                "vérifie ton contrat.",
                "Bank surveille ensuite tes opérations : s'il y a un nouveau prélèvement après la date d'effet, tu "
                "seras prévenu.",
            ),
            sources=(_INC_CANCEL,),
            confidence="élevée",
            fields=(
                FieldDef("nom", "Ton nom et prénom (signature de la lettre)", "text"),
                FieldDef("fournisseur", "Fournisseur", "text"),
                FieldDef("reference_client", "Référence client ou numéro de contrat", "text", required=False),
                FieldDef("date_effet", "Date d'effet souhaitée", "date", required=False, help="Vide : au plus tôt"),
                FieldDef("motif", "Motif (facultatif)", "text", required=False),
                FieldDef("montant_mensuel", "Coût mensuel actuel", "amount", required=False),
            ),
            declarations=(
                Declaration(
                    "fin_service",
                    "La résiliation met fin au service à la date d'effet ; un préavis, une durée d'engagement ou des "
                    "frais prévus au contrat peuvent s'appliquer.",
                    "consequence",
                ),
            ),
            followup_days=35,
            followup_label="Vérifie que les prélèvements se sont arrêtés",
            letter=_cancel_letter,
        ),
        Template(
            key="revision_loyer_dpe",
            title="Révision de loyer non due (logement classé F ou G)",
            summary="Une lettre à ton bailleur : le loyer revient au montant d'avant la révision, le surplus versé "
            "est remboursé.",
            category="autre",
            organisme="Ton bailleur",
            channel="letter",
            recipient="Ton bailleur ou son agence (courrier recommandé avec accusé de réception)",
            official_url="https://www.service-public.gouv.fr/particuliers/vosdroits/F1311",
            steps=(
                "Relis les montants : ils viennent de tes prélèvements, et une hausse de la provision pour charges "
                "reste due (l'avis du bailleur sépare loyer et charges).",
                "Envoie la lettre en recommandé avec accusé de réception, avec une copie du DPE ; garde la date "
                "d'envoi.",
                "Si le désaccord persiste, une démarche amiable est obligatoire avant le juge pour un litige "
                "jusqu'à 5 000 € : commission départementale de conciliation ou conciliateur de justice, tous deux "
                "gratuits.",
                "Le juge des contentieux de la protection se saisit dans les 3 ans qui suivent la révision contestée.",
            ),
            sources=(_SP_F1311, _ADEME_DPE),
            confidence="élevée",
            fields=(
                FieldDef("nom", "Ton nom et prénom (signature de la lettre)", "text"),
                FieldDef("adresse_logement", "Adresse du logement loué", "text"),
                FieldDef("bailleur", "Bailleur ou agence", "text"),
                FieldDef("classe", "Classe du DPE", "choice", choices=(("F", "F"), ("G", "G"))),
                FieldDef("numero_dpe", "Numéro du DPE (ADEME)", "text", required=False),
                FieldDef("date_revision", "Date de la révision", "date"),
                FieldDef("loyer_avant", "Montant payé avant la révision", "amount"),
                FieldDef("hausse", "Hausse mensuelle", "amount"),
                FieldDef("montant", "Somme à rembourser", "amount"),
                FieldDef("periode", "Période concernée", "text", help="Par exemple « du 09/2025 au 10/2026 »"),
            ),
            pieces=(
                PieceDef("bail", "Bail", ("lease",), required=False),
                PieceDef("quittances", "Quittances après la révision", ("rent_receipt",), required=False),
            ),
            declarations=(
                Declaration(
                    "nom_propre",
                    "Cette lettre part en ton nom : relis la classe du DPE, les dates et les montants avant de "
                    "l'envoyer.",
                    "engagement",
                ),
            ),
            followup_days=60,
            followup_label="Sans accord : commission départementale de conciliation ou conciliateur de justice",
            letter=_rent_dpe_letter,
        ),
        Template(
            key="ouvrir_lep",
            title="Ouvrir un livret d'épargne populaire (LEP)",
            summary="Le livret réglementé le mieux rémunéré, réservé aux revenus modestes : à demander à ta banque.",
            category="epargne",
            organisme="Ta banque",
            channel="counter",
            recipient="Ta banque (agence ou espace en ligne)",
            official_url="https://www.economie.gouv.fr/node/35358",
            steps=(
                "Demande l'ouverture d'un LEP à ta banque (agence ou espace en ligne). Le premier versement est de "
                "30 € au minimum ; le plafond est de 10 000 €, hors intérêts.",
                "Ta banque vérifie ton revenu fiscal de référence (avis d'impôt) au regard du plafond de l'année : "
                "garde ton avis à portée de main.",
                "Un seul LEP par personne, deux au plus par foyer fiscal.",
            ),
            sources=(_LEP,),
            confidence="moyenne",
            fields=(
                FieldDef("banque", "Banque", "text"),
                FieldDef("rfr", "Revenu fiscal de référence", "amount"),
                FieldDef("parts", "Nombre de parts", "number"),
                FieldDef("versement", "Premier versement prévu", "amount", required=False, help="30 € au minimum"),
            ),
            pieces=(PieceDef("avis", "Avis d'impôt le plus récent", ("tax_notice",)),),
            declarations=(
                Declaration(
                    "un_par_personne",
                    "Tu n'as pas déjà de LEP (un seul par personne, deux au plus par foyer).",
                    "honneur",
                ),
            ),
            followup_days=15,
            followup_label="Vérifie que le livret apparaît dans tes comptes",
        ),
        Template(
            key="demander_css",
            title="Demander la Complémentaire santé solidaire",
            summary="L'aide de l'Assurance maladie pour payer tes dépenses de santé : demande en ligne sur ameli, "
            "réponse de la CPAM sous deux mois.",
            category="aide",
            organisme="Assurance maladie (CPAM)",
            channel="online_official",
            recipient="Ta CPAM, par ton compte ameli (rubrique « Mes démarches »)",
            official_url=_AMELI_CSS.url,
            steps=(
                "Connecte-toi à ton compte ameli (site ou application) et ouvre la rubrique « Mes démarches ».",
                "Choisis la demande de Complémentaire santé solidaire. Si tu es allocataire de la Caf, garde ton "
                "numéro de dossier à portée de main : saisis-le directement sur ameli, Bank ne le garde pas.",
                "Confirme ou corrige la composition de ton foyer.",
                "Dépose les justificatifs numérisés : pour chaque personne du foyer, un avis d'imposition (ou un "
                "avis de situation déclarative à l'impôt, ASDIR). Certaines ressources (salaires, retraites, "
                "allocations chômage) sont récupérées directement auprès des organismes.",
                "Choisis l'organisme qui gérera ta complémentaire, puis valide la demande.",
                "La CPAM a deux mois à compter du dossier complet pour te répondre. Les droits sont ouverts pour un "
                "an, en principe à partir du 1er jour du mois qui suit sa décision.",
            ),
            sources=(_AMELI_CSS,),
            confidence="moyenne",
            fields=(
                FieldDef(
                    "situation",
                    "Situation familiale",
                    "choice",
                    choices=(("seul", "Seul(e)"), ("couple", "En couple")),
                ),
                FieldDef("personnes", "Nombre de personnes dans ton foyer", "number"),
                FieldDef("allocataire", "Allocataire de la Caf ?", "choice", choices=_YES_NO),
                FieldDef(
                    "etranger",
                    "As-tu résidé à l'étranger au cours des 12 derniers mois ?",
                    "choice",
                    choices=_YES_NO,
                ),
                FieldDef(
                    "gestionnaire",
                    "Qui gérera ta complémentaire ?",
                    "choice",
                    choices=(
                        ("assurance_maladie", "Ta caisse d'Assurance maladie"),
                        ("organisme_complementaire", "Un organisme complémentaire"),
                    ),
                ),
                FieldDef(
                    "mutuelle_actuelle",
                    "Coût mensuel de ta mutuelle actuelle",
                    "amount",
                    required=False,
                    help="Pour mesurer l'économie ; si la CSS t'est accordée, pense à résilier ta mutuelle",
                ),
            ),
            pieces=(
                PieceDef(
                    "avis",
                    "Avis d'imposition (ou ASDIR) le plus récent",
                    ("tax_notice",),
                    help="Un par personne du foyer",
                ),
                PieceDef(
                    "etranger",
                    "Justificatifs de ta situation fiscale et sociale à l'étranger",
                    ("other",),
                    required=False,
                    help="Seulement si tu as résidé à l'étranger au cours des 12 derniers mois",
                ),
            ),
            declarations=(
                Declaration(
                    "exactitude",
                    "Ce que tu déclares engage ta responsabilité : relis la composition du foyer et les justificatifs "
                    "avant de valider sur ameli.",
                    "consequence",
                ),
                Declaration(
                    "participation",
                    "S'il y a une participation financière, la CPAM t'en communique le montant : vérifie-le avant "
                    "d'accepter.",
                    "engagement",
                ),
            ),
            followup_days=60,
            followup_label="Deux mois après le dossier complet, la CPAM doit t'avoir répondu",
        ),
        Template(
            key="libre",
            title="Démarche libre",
            summary="Pour suivre n'importe quelle démarche : pièces, échéance, notes.",
            category="autre",
            organisme="—",
            channel="libre",
            recipient="L'organisme concerné",
            official_url=None,
            steps=(),
            sources=(),
            confidence="—",
            fields=(
                FieldDef("organisme", "Organisme", "text"),
                FieldDef("objet", "Objet de la démarche", "text"),
                FieldDef("notes", "Notes", "longtext", required=False),
            ),
            followup_days=30,
            followup_label="Fais le point sur cette démarche",
        ),
    )
}


# ============================================================================ valeurs
def field_def(template: Template, key: str) -> FieldDef | None:
    return next((f for f in template.fields if f.key == key), None)


def active_fields(template: Template, values: dict[str, Any]) -> list[FieldDef]:
    """Les champs demandés compte tenu des réponses (le loyer seulement pour un locataire…)."""
    return [f for f in template.fields if f.when is None or values.get(f.when[0]) == f.when[1]]


def parse(fdef: FieldDef, raw: Any) -> Any:
    """Valeur saisie, contrôlée selon son type ; None pour effacer un champ facultatif."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if fdef.type == "choice":
        value = str(raw)
        if value not in {k for k, _ in fdef.choices}:
            raise ValueError("Choix inconnu")
        return value
    if fdef.type == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in ("true", "oui", "1"):
            return True
        if str(raw).lower() in ("false", "non", "0"):
            return False
        raise ValueError("Oui ou non")
    if fdef.type == "longtext":
        text = str(raw).replace("\r\n", "\n")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
            raise ValueError("Caractères non autorisés")
        return text.strip()[:MAX_LONGTEXT]
    return dv.parse_value(fdef.type, raw)


def coerce(fdef: FieldDef, value: Any) -> Any:
    """Valeur venant de Bank lui-même (coffre, profil, opportunité) : déjà dans sa forme interne — un montant entier
    est en centimes. Un texte est lu comme une saisie (« 96,00 » = 96 €)."""
    if fdef.type == "amount" and isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= abs(value) <= 10_000_000_000:
            raise ValueError("Montant hors limites")
        return value
    return parse(fdef, value)


def display(fdef: FieldDef, value: Any) -> str:
    if value is None or value == "":
        return "—"
    if fdef.type == "choice":
        return dict(fdef.choices).get(str(value), str(value))
    if fdef.type == "bool":
        return "Oui" if value else "Non"
    if fdef.type == "amount":
        return format_eur(int(value))
    if fdef.type == "date":
        return _fr_date(date.fromisoformat(str(value)))
    if fdef.type == "month":
        return dv.month_label(str(value))
    if fdef.type == "percent":
        return f"{str(value).replace('.', ',')} %"
    return str(value)


# ============================================================================ évaluation
@dataclass
class Evaluation:
    stage: str
    missing_fields: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing_pieces: list[str] = field(default_factory=list)
    unverified: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AttachedDoc:
    id: int
    kind: str
    title: str
    status: str
    document_date: date | None
    fingerprint: str


def evaluate(
    template: Template, fields: dict[str, dict[str, Any]], pieces: dict[str, list[AttachedDoc]], today: date
) -> Evaluation:
    """Où en est le dossier : champs manquants, valeurs contredites par leur justificatif, pièces manquantes ou à
    vérifier. Le premier manque décide de l'étape (les valeurs d'abord, puis les pièces)."""
    values = {k: v.get("value") for k, v in fields.items()}
    out = Evaluation(stage="ready")
    for fdef in active_fields(template, values):
        if fdef.required and values.get(fdef.key) in (None, ""):
            out.missing_fields.append(fdef.key)
    for key, entry in fields.items():
        prefilled = entry.get("prefilled")
        if entry.get("source") == "user" and prefilled is not None and prefilled.get("value") != entry.get("value"):
            spec = field_def(template, key)
            if spec is None:
                continue
            out.conflicts.append(
                {
                    "key": key,
                    "label": spec.label,
                    "value": display(spec, entry.get("value")),
                    "expected": display(spec, prefilled.get("value")),
                    "document_id": prefilled.get("document_id"),
                    "evidence": prefilled.get("evidence"),
                }
            )
    for pdef in template.pieces:
        docs = pieces.get(pdef.key, [])
        fresh = [d for d in docs if pdef.max_age_days is None or not d.document_date
                 or (today - d.document_date).days <= pdef.max_age_days]  # fmt: skip
        if pdef.required and len(fresh) < pdef.count:
            out.missing_pieces.append(pdef.key)
        if len(fresh) < len(docs):
            out.warnings.append(f"{pdef.label} : un document joint est plus ancien que ce qui est demandé.")
        out.unverified += [d.id for d in docs if d.status != "verified"]
    if out.missing_fields or out.conflicts:
        out.stage = "verify"
    elif out.missing_pieces or out.unverified:
        out.stage = "documents"
    return out


def canonical(
    template: Template,
    fields: dict[str, dict[str, Any]],
    pieces: dict[str, list[AttachedDoc]],
    letter: str | None,
) -> dict[str, Any]:
    """Le dossier tel qu'il sera transmis : ce que la validation engage, rien de plus, rien de moins."""
    values = {k: v.get("value") for k, v in fields.items()}
    return {
        "template": template.key,
        "recipient": template.recipient,
        "fields": {f.key: values.get(f.key) for f in active_fields(template, values)},
        "pieces": {k: sorted(d.fingerprint for d in docs) for k, docs in sorted(pieces.items()) if docs},
        "letter": letter,
        "declarations": [d.text for d in template.declarations],
    }


def fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def render_letter(template: Template, fields: dict[str, dict[str, Any]], when: date | None) -> str | None:
    """La lettre ; sans date (``when`` vide) pour l'empreinte : la validation porte sur le fond, pas sur le jour."""
    if template.letter is None:
        return None
    return template.letter({k: v.get("value") for k, v in fields.items()}, when)


def next_check(template: Template, sent_on: date) -> date:
    return sent_on + timedelta(days=template.followup_days)
