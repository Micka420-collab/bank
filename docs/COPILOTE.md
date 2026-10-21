# Bank — copilote financier et administratif : audit, architecture cible, feuille de route

> Ce document répond au « prompt master » du 21 octobre 2026. Il dit ce qui existe, ce qui manque, l'architecture
> visée, ce qui borne le produit (droit, sécurité, coûts) et l'ordre de construction. Il est tenu à jour à chaque
> livraison : la colonne « État » ne passe à ✅ que pour ce qui est opérationnel **et** testé.

## 0. En une phrase

Bank sait déjà lire tes comptes, prévoir ton mois, reconnaître tes aides et ton salaire, et poser les bonnes
questions. Il lui manque de **garder tes documents**, de **mener tes démarches** jusqu'au bout, de **chiffrer ce
qu'il peut te faire gagner** et de **surveiller ce qui change** côté administration — c'est l'objet des phases 2 à 7.

## 1. Audit de l'existant (octobre 2026, commit `25c82bc`)

### 1.1 Ce qui fonctionne, testé

| Domaine | Ce qui existe | Garanties |
|---|---|---|
| Accès | Création protégée par jeton d'installation, Argon2id, TOTP + codes de secours, sessions `__Host-` HttpOnly, CSRF double-submit, verrouillage exponentiel | Tests d'attaque (force brute, rejeu TOTP, CSRF, hôte non autorisé) |
| Données | Chiffrement AES-256-GCM par champ avec contexte en AAD, rotation de clé, index aveugles, journal d'audit chaîné HMAC | `test_audit_chain_*`, `test_context_binding_blocks_column_swap` |
| Comptes | Import CSV/OFX toutes banques (sans parseur XML), synchro DSP2 lecture seule (Enable Banking, agréé), rapprochement des doublons | 488 tests backend, couverture 94 % |
| Budget | Reste à dépenser par jour contraint par la trésorerie, prévision du mois, enveloppes, récurrences | Propriétés Hypothesis |
| Aides | Reconnaissance des versements (CAF, France Travail, CPAM, MSA, impôts), simulation OpenFisca **chez toi** (réseau sans Internet), écarts droits / versements | Intégration avec le vrai moteur |
| Patrimoine | Valeur nette reconstruite, matelas, livrets réglementés datés, objectifs, plan « où placer » | — |
| Connecteurs | Banques (DSP2), Linky (Conso API), documents officiels PDF (lus dans un processus confiné, identifiants jetés), planning de travail (iCal) → paie prévue | SSRF, bombe gzip, PDF piégé |
| Proactivité | Cartes « Bank te demande » (faits à confirmer, abonnements, bilan du mois, sécurité…) | Actions autorisées seulement sur une carte visible |
| Agents | Serveur MCP gouverné pour HERMÈS (portées, mandat, propositions validées par toi), assistant OpenRouter (clé chiffrée, plafonds, dossier anonymisé) | Jetons hachés, audit sans contenu |
| Interface | Design system « Encre claire », onboarding, tableau de bord interactif, mobile, sombre, AA | 51 tests front, Playwright 390/820/1340 px |

### 1.2 Ce qui manque par rapport à l'ambition

| Attendu | État avant ce chantier | Écart |
|---|---|---|
| Coffre-fort documentaire | Les PDF officiels sont lus puis **jetés** (seuls les chiffres restent) | Pas de stockage, pas de photo, pas de bulletin de paie, pas de dates d'expiration |
| Démarches | Aucune | Ni modèle de procédure, ni suivi, ni écran de revue |
| Opportunités | Dispersées (cartes, écarts d'aides, plan d'épargne) | Pas de classement par gain net, pas de sources ni de confiance, pas de mesure de la valeur obtenue |
| Veille | Taux des livrets figés dans `rates.json` (1er août 2026) | Aucune source surveillée, aucune alerte de fraîcheur |
| Mémoire de l'assistant | Chaque question repart de zéro | Rien n'est retenu ni amélioré |
| Consentements | Implicites, connecteur par connecteur | Pas de registre par finalité, pas de révocation centrale |
| Notifications | Dans l'application seulement | Pas de canal sortant (push, ntfy, e-mail) |
| Foyer | Un seul utilisateur | Pas de partage ni de permissions par membre |
| Tâches de fond | Boucles asyncio dans le processus de l'API | Pas de file persistante (reprise, relance, budget par agent) |

### 1.3 Risques relevés

- **Sortie de données vers les modèles de langage** : maîtrisée pour l'assistant (dossier anonymisé, plafonds) ; à
  étendre aux documents — une pièce d'identité ne doit **jamais** partir vers un modèle externe.
- **Informations réglementaires périmées** : `rates.json` et les plafonds de frais bancaires sont datés mais rien
  ne signale qu'ils ont vieilli. Une règle non revérifiée doit s'afficher « à vérifier ».
- **Boucles de fond dans le processus web** : un redémarrage perd une synchro en cours (sans dégât, tout est
  idempotent) ; une file persistante devient nécessaire dès que des démarches ont des échéances.
- **Clé maîtresse sur le même hôte** que la base : acceptable pour un serveur domestique, mais une sauvegarde de
  `/data` sans la clé est inutilisable — c'est voulu — et la clé doit être sauvegardée à part (documenté).
- **Injection de consignes** par un libellé bancaire, un document ou une page web : tout contenu externe est une
  donnée ; aucun modèle n'a d'outil d'écriture sans validation humaine.

### 1.4 Coûts d'exploitation

| Poste | Coût | Maîtrise |
|---|---|---|
| Agrégation bancaire | 0 € (Enable Banking, mode restreint gratuit pour usage personnel) | — |
| Simulation des aides | 0 € (OpenFisca local) | — |
| Modèle de langage | ~0,1 à 0,5 centime par question avec un petit modèle (dossier ≈ 2–3 k jetons) | Plafonds mensuel et quotidien, petit modèle par défaut, mémoire bornée |
| Lecture de documents | 0 € en local (texte des PDF, OCR Tesseract si installé) ; vision par modèle externe seulement sur demande explicite | Consentement par document, interdit pour les pièces d'identité |
| Veille | Quelques requêtes HTTP par jour | Cache, empreintes de contenu, fréquence par source |

## 2. Principes non négociables

1. **L'IA prépare, vérifie, explique et propose ; tu décides et tu transmets.** Aucune transmission, aucun
   paiement, aucun engagement sans ta validation explicite, liée à l'empreinte exacte de ce qui part.
2. **Provenance partout** : chaque valeur dit d'où elle vient (relevé, document, toi, simulation, source officielle),
   quand elle a été vérifiée, et si c'est un **fait**, une **estimation** ou une **hypothèse**.
3. **Fraîcheur** : une règle dont la vérification a expiré est affichée « à vérifier », jamais comme en vigueur.
4. **Moindre privilège** : chaque module ne voit que ce dont il a besoin ; le contenu externe (pages, documents,
   libellés, réponses de modèles) est une donnée non fiable.
5. **Pas d'invention** : un champ manquant reste vide et bloque la préparation, il n'est jamais deviné.
6. **Statut honnête** : Bank n'est ni conseiller en investissement financier (CIF), ni prestataire de services de
   paiement ; il donne des repères et des calculs, il n'initie aucun paiement.

## 3. Architecture cible

On garde ce qui marche : un monolithe modulaire FastAPI + SQLite chiffrée par champ, un front React, des
connecteurs. Les « agents » du brief deviennent des **services à privilèges bornés**, orchestrés par une file de
tâches persistante, et tous les appels à un modèle passent par une **passerelle unique**.

```
                ┌──────────────────────────── Interface (React PWA) ────────────────────────────┐
                │ Aujourd'hui · Opportunités · Démarches · Documents · Opérations · Patrimoine … │
                └───────────────┬───────────────────────────────────────────────┬─────────────┘
                                │ API REST (sessions, CSRF)                      │ MCP (HERMÈS, jetons)
   ┌────────────────────────────┴───────────────────────────────────────────────┴──────────────┐
   │ Orchestrateur : file de tâches persistante (idempotence, reprise, relance, budget)         │
   ├──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬────────────────┤
   │ Veille       │ Vérification │ Finances     │ Démarches    │ Documents    │ Connecteurs    │
   │ sources →    │ fraîcheur,   │ opportunités,│ modèles,     │ coffre,      │ catalogue,     │
   │ changements  │ confiance    │ gain net     │ revue, suivi │ extraction   │ connexion      │
   ├──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴────────────────┤
   │ Conformité : consentements par finalité · journal chaîné · règles « jamais » · plafonds   │
   ├────────────────────────────────────────────────────────────────────────────────────────────┤
   │ Passerelle IA (OpenRouter, ta clé) : anonymisation, budget, mémoire bornée, sans contenu   │
   │ dans les journaux · Mémoire de l'assistant (faits, préférences, résumés de conversations) │
   ├────────────────────────────────────────────────────────────────────────────────────────────┤
   │ Stockage : SQLite (champs chiffrés) · coffre de fichiers chiffrés (AES-256-GCM, clé dérivée) │
   └────────────────────────────────────────────────────────────────────────────────────────────┘
```

| « Agent » | Ce qu'il lit | Ce qu'il peut écrire | Accès réseau |
|---|---|---|---|
| Veille | Sources déclarées, étiquettes de profil (jamais tes montants) | Éléments de veille, cartes | Hôtes de la source, HTTPS, sans adresse privée |
| Vérification | Éléments de veille, données de référence datées | Niveau de confiance, alertes de fraîcheur | Aucun |
| Finances | Opérations, prévisions, profil | Opportunités (propositions) | Aucun |
| Démarches | Modèles, profil, champs des documents liés | Dossiers, étapes ; **jamais** « envoyé » sans toi | Aucun |
| Documents | Le document traité | Champs extraits (corrigeables), dates | Aucun (OCR local) ; modèle externe sur consentement par document |
| Connecteurs | Catalogue | État des connexions | Celui du connecteur |
| Assistant | Dossier anonymisé, mémoire | Mémoire (bornée, visible, effaçable), propositions | OpenRouter |
| Navigateur (étude) | Pages autorisées | Brouillons de formulaires | Session autorisée ; lecture / préparation / envoi séparés |

## 4. Ce qui borne le produit (France)

- **RGPD** : minimisation (les identifiants des documents officiels sont jetés), finalités consenties et révocables,
  droit d'accès / rectification / effacement / portabilité (export JSON), données sensibles (santé, pièces
  d'identité) traitées seulement avec ton accord explicite et jamais envoyées à un modèle externe.
- **DSP2 / Open Banking** : Bank lit tes comptes via un prestataire agréé (Enable Banking, service
  d'information sur les comptes). Il n'initie aucun paiement : pas besoin du statut de prestataire de paiement.
- **Conseil financier** : repères généraux et calculs, pas de recommandation personnalisée d'un produit
  financier (qui relèverait du statut CIF). Le plan « où placer » le dit.
- **Organismes publics** : CAF, impôts, ameli et France Travail n'offrent pas d'API aux applications
  personnelles ; FranceConnect est réservé aux fournisseurs habilités. Bank prépare donc le dossier et te guide
  sur le site officiel ; il ne se connecte jamais à ta place et ne contourne aucune protection anti-robot.
- **Démarches avec engagement** : les déclarations sur l'honneur, engagements financiers et conséquences juridiques
  sont signalés en tête de l'écran de revue.

## 5. Feuille de route

| Phase | Objectif mesurable | État |
|---|---|---|
| 1. Fondations | Consentements par finalité révocables ; passerelle IA unique avec plafonds ; design system | ✅ registre versionné, retrait immédiat, plafonds vérifiés avant chaque appel |
| 2. Documents | Une photo ou un PDF devient un document chiffré, typé, daté, avec des champs liés à leur source et corrigeables ; bulletin de paie lu | ✅ voir § 9 |
| 3. Finances | Opportunités classées par gain net attendu (montant × probabilité − coût − effort), avec sources et confiance ; frais bancaires comparés aux plafonds légaux ; valeur obtenue mesurée | 🚧 ce passage |
| 4. Veille | Sources officielles suivies avec date de vérification et empreinte ; changement détecté → carte pour toi ; données de référence périmées signalées | 🚧 ce passage |
| 5. Démarches | Modèles sourcés, pièces liées au coffre, préparation bloquée en cas d'incohérence, revue obligatoire liée à l'empreinte, envoi marqué une seule fois, suivi des échéances | 🚧 ce passage |
| 5 bis. Mémoire | L'assistant retient (faits, préférences, résumés), s'améliore par tes retours, et te montre tout ce qu'il sait | ✅ voir § 8 |
| 6. Connecteurs et navigateur | Catalogue de connecteurs avec état ; parcours guidé quand aucune intégration n'est possible ; agent navigateur à l'étude | ⏳ |
| 7. Proactivité | File de tâches persistante, notifications sortantes configurables (ntfy, e-mail), calendrier administratif | ⏳ |
| 8. Industrialisation | Tests de charge, sauvegarde/restauration testées, supervision, coûts suivis | ⏳ |

## 6. Scénarios de bout en bout

| Scénario | Couvert par | État |
|---|---|---|
| A — Une information officielle change | Veille → empreinte → correspondance avec ton profil → carte « à vérifier » + relance de la simulation | 🚧 |
| B — Photo d'un bulletin de paie | Coffre → type reconnu → champs extraits liés au document → vérification → rangement | ✅ `test_photo_of_a_payslip_end_to_end` |
| C — Demander une aide | Démarche depuis un modèle sourcé → pièces → champs préremplis → revue complète avant transmission | 🚧 |
| D — Plateforme non connectée | Démarche → connecteur disponible ? connexion officielle : parcours guidé sur le site officiel | 🚧 |
| E — Dépense évitable | Opportunité frais bancaires / abonnement → alternatives avec coûts et conditions → démarche | 🚧 |
| F — Valeur contradictoire | Démarche → champ ≠ justificatif → préparation bloquée jusqu'à correction | 🚧 |

## 7. Volontairement écarté (pour l'instant)

- **Connecteurs générés par IA et déployés automatiquement** : un connecteur écrit par un modèle est du code qui
  touche à tes secrets. Il naît dans une branche, avec ses tests, et n'est déployé qu'après ta relecture —
  jamais à chaud dans l'application.
- **Agent navigateur sur les sites publics** : CGU et protections anti-robots des organismes ; à n'envisager que
  site par site, en lecture et préparation, l'envoi restant ton clic.
- **Mode foyer multi-utilisateur** : changement de modèle de données profond (permissions par membre, consentements
  distincts) ; prévu après la phase 7.

## 8. Mémoire de l'assistant (livrée)

Conception reprise d'[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, licence MIT), puis
adaptée à une application financière auto-hébergée.

| Hermes Agent | Bank | Pourquoi l'écart |
|---|---|---|
| `MEMORY.md` / `USER.md`, entrées séparées par `§`, budgets en caractères | Carnets « notes » (2 200) et « user » (1 500), même séparateur, même budget **sur le résultat final** d'un lot | Identique : mémoire courte, injectée à chaque conversation |
| Outil `memory` add / replace / remove | add / **patch** (un passage, une seule occurrence, tolérant à la typographie) / remove, en lot tout-ou-rien, doublons ignorés, vidage complet refusé | `replace` servait à écraser des entrées entières |
| Filtre d'injection à l'écriture et au chargement | Même principe, plus les identifiants financiers et personnels (IBAN, carte vérifiée par Luhn, NIR, téléphone, e-mail, codes, longs numéros) et les caractères bidirectionnels | Une mémoire qui part vers un modèle externe ne doit rien contenir d'identifiant |
| Instantané figé par session | Instantané figé par conversation (chiffré) | Contexte stable du premier au dernier message |
| Relecture en arrière-plan par un agent avec outils | Un appel, sortie JSON validée ; **ajouts seulement** — corrections et suppressions deviennent des propositions « À valider », périmées si l'entrée a changé | Pas d'écriture destructrice sans toi |
| `session_search` (FTS5 + résumé par LLM) | Outil `recall` : recherche par mots (idf, accents ignorés) dans les conversations déchiffrées en mémoire, extraits réels, aucun appel de modèle | Pas d'index en clair sur le disque |
| Retours de l'utilisateur dans la conversation | 👍 / 👎 avec note, injectés dans la relecture comme signal prioritaire | Apprendre de ce qui n'a pas plu |
| — | Provenance de chaque entrée (conversation, relecture, toi), épinglage, accord « mémoire » versionné, durée de conservation, export, oubli total protégé par mot de passe | RGPD, contrôle |

Déclencheurs de la relecture : toutes les 8 questions, à la fermeture d'une conversation (dès 2 questions non
relues), et toutes les heures pour les conversations endormies depuis 6 h (5 relectures au plus par passage). Chaque
relecture compte dans les plafonds. Sans accord : aucun outil de mémoire, aucune relecture, conversation effacée à sa
fermeture.

Persona : tutoiement, chaleureux et direct, humour léger, honnête quand c'est serré, jamais moralisateur ; il dit
ce qu'il ne sait pas et ne se présente pas comme conseiller agréé. Son prénom se règle dans « Ce que Bank retient ».

Tests : `backend/tests/test_memory.py` (45 cas dont propriétés Hypothesis sur le filtre et les lots),
`frontend/src/assistant.test.tsx` (accord, avis « Retenu » + annulation, 👎 avec note, reprise de conversation,
page mémoire, confidentialité).

## 9. Coffre-fort de documents (livré)

| Étape | Ce qui se passe | Où |
|---|---|---|
| Dépôt | 1 à 10 fichiers (12 Mo chacun, 40 Mo l'envoi), appareil photo du téléphone ; format reconnu aux premiers octets ; doublon reconnu par empreinte à clé ; fichier chiffré aussitôt | `services/vault.ingest` |
| Lecture (tâche de fond) | Photo : redressée, réencodée sans métadonnées, vignette, OCR (Tesseract fra+eng, texte et confiance par mot) ; PDF : texte, ou images des pages scannées puis OCR ; tout en processus confinés ; reprise automatique après un redémarrage | `domain/reading`, `image_child`, `pdf_text`, `ocr_child` |
| Compréhension | 13 types (signaux pondérés), valeurs par règles déclaratives avec leur ligne de preuve ; analyseurs éprouvés pour l'avis d'impôt, la CAF et France Travail ; contrôle de cohérence du bulletin ; dates du document, d'expiration et de conservation | `domain/vault` |
| Revue | Valeurs, preuves, « non trouvé », corrections (l'originale reste), type modifiable (relecture), « C'est bon » | page Documents |
| Usage | Chiffres officiels repris dans Bank ; documents liés aux démarches (phase 5) ; échéances surveillées | `apply_official`, `expiring` |
| Garde | Durées minimales Service-Public.fr (F19134, vérifiée le 13/10/2026) ; export zip ; suppression ; journal des accès | — |

Lecture assistée par modèle : seulement avec l'accord « Lecture de documents par un modèle externe », jamais pour une
pièce sensible (ni pour un texte qui en a les signes), texte masqué, et chaque valeur proposée doit être une
citation exacte du document qui la contient — sinon écartée et comptée.

Mise à niveau du schéma : `db.upgrade_schema` ajoute au démarrage les colonnes et index apparus depuis la création de
la base (colonnes facultatives ou à valeur par défaut fixe) et refuse de démarrer sur un changement qui demanderait
une migration écrite.

Tests : `backend/tests/test_vault.py` (45 cas, dont l'OCR réel d'une photo inclinée et d'un PDF scanné),
`frontend/src/documents.test.tsx`.
