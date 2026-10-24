# Deux sessions sur Bank en même temps

Une session Claude dans le cloud et une autre sur nimbus peuvent travailler en parallèle sur ce dépôt. Elles ne se
voient pas : **GitHub est le seul point de rencontre**. Règles simples pour ne pas se marcher dessus.

## Réserver, puis coder

1. Avant de toucher au code d'une issue de la [feuille de route](ROADMAP.md), **commente l'issue** : « 🔧 En cours —
   session cloud » ou « 🔧 En cours — session nimbus », avec une ligne sur ce que tu comptes faire. Une issue = une
   session. Si l'issue porte déjà ce commentaire, prends-en une autre.
2. Une fois livrée, le commit la ferme (`Closes #N`) et la ligne « Livré le … » de `docs/ROADMAP.md` la cite.

## Pousser souvent, rebaser toujours

- `git fetch origin && git pull --rebase origin main` **avant chaque commit et chaque push**. Un push refusé se
  résout par un rebase, jamais par un `--force`.
- Des petits commits, un sujet chacun, message en français qui dit quoi et pourquoi (voir l'historique). Signature :
  `git -c user.name="Micka" -c user.email="micka.delcato.rp@gmail.com" commit`, et les lignes d'attribution de la
  session en pied de message.
- Une branche + PR si le chantier dure plus d'une session : l'autre session relit. **La fusion attend le retour du
  relecteur** : ses points « à corriger » sont réglés dans la PR, ou reportés explicitement dans une issue qui les
  cite — jamais perdus. Une CI verte ne remplace pas la relecture (le 24 octobre, #20, #21, #23 et #24 ont été
  fusionnées avant les corrections ; elles vivent dans #25 à #28).

## Rien de rouge ne part

- `make check` (ruff, mypy strict, bandit, pytest ≥ 85 % de couverture, build front) doit passer avant le push ; la
  CI le rejoue (pip-audit, npm audit, Trivy, SBOM).
- Les chiffres du paragraphe « Qualité » du README (tests backend / front, couverture) se recomptent au moment du
  commit : `pytest --co -q | awk -F': ' '/^tests\//{s+=$2} END{print s}'` et `npx vitest run`.
- Les règles datées (barèmes, plafonds, taux, textes) portent leur date de vérification dans le code et dans `docs/`.

## Zones pour éviter les collisions

| Session | Zones en cours | Issues |
|---|---|---|
| cloud (Claude Fable 5.1) | plan B, crédits, sauvegardes, boîte mail IMAP, retraite, clés de plateforme, accessibilité téléphone, journal en français, sommaire collant, CI ; #7 en attente de la décision de Micka (agrément PISP) ; relit les PR de nimbus | #4 ✔, #6 ✔, #16 ✔, #15 ✔, #9 ✔, #11 ✔ |
| nimbus (Opus 5.5) | partage d'un papier (`services/shares`, `pages/Received`), import CSV crypto (`importers/crypto_csv`, `services/crypto_import`), alertes de cours et pilote vs consigne (`services/price_alerts`, `domain/pilot_track`), valeur du logement DVF (`domain/dvf`, `services/home_value`, `components/HomeValue`) ; suites de relecture #25 à #28 | #14 ✔, #10 ✔, #12 ✔, #8 ✔ |

Opus a maintenant son accès GitHub : chaque session pose ses commentaires d'issue, pousse ses branches et ouvre ses
PR ; l'autre relit.

Les fichiers partagés par tout le monde (`README.md`, `SECURITY.md`, `docs/ROADMAP.md`, `app/main.py`,
`frontend/src/api.ts`, `frontend/src/styles.css`, `tests/test_mcp.py`, la compétence HERMÈS) se touchent par petites
additions : un conflit s'y résout en gardant les deux ajouts.

## Déploiement sur nimbus

Après un `git pull` sur nimbus : `docker compose build && docker compose up -d` (les images embarquent le code),
puis `make backup` si un changement de schéma arrive (le schéma se met à niveau seul au démarrage, colonnes et tables
ajoutées ; jamais une suppression). Les clés et jetons ne bougent pas.
