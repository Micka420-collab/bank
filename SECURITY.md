# Modèle de menace et défenses

Bank contient l'historique bancaire complet d'un foyer, ses revenus et sa situation sociale. Chaque menace ci-dessous
a au moins une défense testée (référence du test entre parenthèses).

## Ce qu'on protège

| Donnée | Où | Protection |
|---|---|---|
| IBAN, uid de compte DSP2, session DSP2, secret TOTP, profil du foyer, résultats de simulation | SQLite | AES-256-GCM par champ, contexte lié en AAD (`test_context_binding_blocks_column_swap`) |
| Mots de passe | SQLite | Argon2id 64 MiB, t=3, p=2, re-hash automatique (`test_hash_and_verify`) |
| Jetons de session | SQLite | Seule l'empreinte SHA-256 est stockée (`test_session_token_stored_hashed_and_idle_expiry`) |
| Jetons d'agent IA (`bnk_…`) | SQLite | Empreinte SHA-256 seule, affichés une fois, expiration ≤ 365 j (`test_token_is_stored_hashed_and_authenticates`) |
| Dossier « Ma situation », propositions de l'agent | SQLite | AES-256-GCM par champ, historique des réponses chiffré |
| Libellés et montants | SQLite | En clair (recherche, catégorisation) : volume local + sauvegardes chiffrées avec `age` |
| Clé maîtresse | `./secrets` (640, groupe 10010) | Jamais en base, jamais dans l'environnement du process en prod (`*_FILE`) |

Clés dérivées par HKDF, une par usage (chiffrement, index aveugle, jetons signés, chaîne d'audit).

## Menaces et réponses

| Menace | Défense |
|---|---|
| Quelqu'un sur le LAN « réclame » l'instance avant toi | Création du compte exige `BANK_SETUP_TOKEN` (`test_setup_requires_token_and_strong_password`) |
| Force brute / bourrage d'identifiants | Rate-limit nginx + backend, verrouillage exponentiel jusqu'à 24 h persisté même en cas d'échec (`test_generic_errors_and_lockout`, `test_rate_limit`) |
| Énumération d'identifiants | Message identique, coût Argon2 payé même pour un utilisateur inconnu |
| Vol du mot de passe | TOTP avec anti-rejeu par pas temporel, 10 codes de secours à usage unique (`test_full_mfa_flow_with_replay_protection_and_recovery`) |
| CSRF | Cookies `SameSite=Strict`, jeton double-submit comparé à une empreinte, vérification de l'en-tête Origin (`test_csrf_and_origin_enforced`) |
| XSS | CSP `script-src 'self'` sans inline, React sans `dangerouslySetInnerHTML`, QR code rendu via `<img>` (vérifié sans violation dans Chromium) |
| Vol de cookie | `__Host-` + HttpOnly + Secure, expiration d'inactivité 30 min et absolue 12 h, révocation globale au changement de mot de passe (`test_password_change_revokes_other_sessions`) |
| DNS rebinding | Liste blanche d'hôtes (`test_untrusted_host_rejected`) |
| Fichier d'import malveillant | Taille bornée (nginx + middleware + lecture), aucun parseur XML (pas de XXE, `test_ofx_v2_xml_is_not_vulnerable_to_xxe`), caractères de contrôle supprimés |
| Injection SQL | ORM paramétré partout, échappement des jokers LIKE (`test_search_filters_and_like_escaping`) |
| Fuite d'un code d'autorisation bancaire dans les logs | Journal d'accès maison sans query string, uvicorn sans access log |
| Rejeu du retour bancaire | `state` aléatoire à usage unique, durée de vie 15 min, stocké sous forme d'empreinte (`test_connect_callback_sync_reconcile_revoke`, `test_expired_state_rejected`) |
| Falsification de l'historique d'activité | Chaîne HMAC (clé hors base) : modification, suppression, insertion ou recalcul détectés (`test_audit_chain_*`) ; tête de chaîne ancrée dans les logs du conteneur contre la troncature |
| Fuite de la situation fiscale | Moteur OpenFisca sur un réseau Docker sans Internet, jeton partagé, liste blanche de variables |
| Conteneur compromis | Non-root, FS en lecture seule, toutes capabilities retirées, `no-new-privileges`, limites mémoire/pids, réseaux segmentés |
| Données en cache | `Cache-Control: no-store` sur l'API ; le service worker ne met jamais `/api` en cache |
| Export de toutes les données | Re-saisie du mot de passe, rate-limit, journalisé |
| Fuite d'erreurs internes | Gestionnaire global : message générique, détails seulement dans les logs ; erreurs de validation sans les valeurs reçues (`test_validation_errors_never_echo_input`) |

## Agents IA (serveur MCP `/mcp`)

Un agent est un utilisateur non fiable par construction : il peut se tromper, ou lire un texte piégé (un libellé
d'opération qui lui donne des « consignes »). Bank ne lui fait donc confiance que pour ce qu'il ne peut pas abîmer.

| Menace | Défense |
|---|---|
| Accès sans jeton, jeton volé ou révoqué | Jeton vérifié en base à chaque requête HTTP **et** à chaque appel d'outil ; révocation immédiate (`test_revocation_is_immediate`) ; liste d'IP par jeton (`test_ip_allowlist`) |
| Agent qui déplace de l'argent | Aucun outil ne le permet ; Bank n'a de toute façon qu'un accès DSP2 en lecture (`test_initialize_and_catalogue`) |
| Agent qui s'auto-promeut (élargit son mandat, lève une ligne rouge) | Questions du mandat refusées côté MCP **et** côté service, quel que soit le chemin (`test_agent_records_answers_but_never_its_mandate`, `test_mandate_cannot_be_written_by_an_agent_even_internally`) |
| Injection de consignes via un libellé | Libellés livrés comme champs de données, consignes explicites dans le dossier et la compétence ; aucune action à effet sans validation humaine (`test_transactions_respect_privacy_mode`) |
| Changement de réglage non voulu | Propositions typées et validées strictement, file bornée (20), dédoublonnées, expirées après 14 j, appliquées par le code de Bank après ton clic ; une règle proposée affiche le nombre d'opérations touchées et son motif est validé après normalisation (`test_proposals_are_strictly_validated`, `test_pending_queue_is_bounded`, `test_rule_patterns_are_validated_after_normalisation`) |
| Agent qui réécrit ta situation ou fausse la prévision | Il ne peut ni écraser une réponse que tu as donnée, ni écrire une dépense prévue : il les propose (`test_agent_cannot_overwrite_user_answers_nor_forecast_inputs`) ; ses classements sont marqués « agent », jamais appris, jamais par-dessus les tiens (`test_agent_categorization_is_marked_and_never_overrides_the_user`) |
| Session volée qui crée un jeton | Mot de passe redemandé, essais limités, refus journalisés (`test_token_creation_requires_password`) |
| Simulation en boucle | Résultat de moins de 6 h réutilisé, 4 simulations/h par jeton, une seule à la fois (`test_simulation_is_reused_and_profile_errors_never_echo_values`) |
| Fuite de données personnelles vers un modèle distant | Anonymisation par défaut, qui échoue du côté sûr : la contrepartie d'un virement est un particulier sauf indice d'organisation ; IBAN quels que soient les séparateurs ; masquage jusque dans les preuves des valeurs détectées et les paliers du budget ; mode agrégats sans aucun libellé ; erreurs de validation jamais renvoyées avec les valeurs (`test_redaction_masks_personal_data`, `test_any_two_word_transfer_counterparty_is_masked`, `test_income_from_a_person_never_leaks_in_redacted_dossier`, `test_onboarding_status_respects_privacy`) |
| Abus de débit, boucle d'agent | 120 appels/min par jeton (API) + 5 r/s par IP (nginx) (`test_rate_limit_per_token`) |
| Page web qui pilote l'agent depuis ton navigateur, DNS rebinding | Vérification Host/Origin par le SDK MCP en plus du TrustedHost (`test_security_middlewares_apply_to_mcp`) |
| Portée insuffisante contournée | Portée vérifiée par outil, refus journalisé `mcp.denied` (`test_scopes_are_enforced_per_tool`) |
| Agent qui agit sans trace | Chaque appel d'outil, chaque refus (`mcp.refused`) et les jetons refusés (`mcp.auth_failed`, un par minute et par IP) sont inscrits dans la chaîne d'audit HMAC, visible dans Ma situation (`test_every_tool_call_is_audited_and_chain_holds`, `test_duplicate_authorization_headers_and_failed_auth_are_handled`) |

`BANK_AGENTS_ENABLED=false` coupe le serveur MCP d'un coup (`test_mcp_can_be_disabled`).

## Hors périmètre (assumé)

- Compromission de nimbus avec accès root : la clé maîtresse est lisible. Défense : disque chiffré (LUKS), mises à jour.
- Un seul worker uvicorn : le rate-limit en mémoire est exact ; avec plusieurs workers il faudrait un stockage partagé.
- La liste d'IP d'un jeton repose sur `X-Forwarded-For` posé par ton reverse proxy : nginx ne le croit que depuis
  la passerelle du réseau `edge` (sous-réseau fixe 172.31.250.0/29), par où arrive le port publié sur 127.0.0.1.
  Garde `BANK_BIND=127.0.0.1` : publié sur le LAN, le port laisserait n'importe qui forger cet en-tête. C'est une
  défense en profondeur, le jeton reste la barrière principale.
- Ce que fait HERMÈS de ses propres données (mémoire, journaux) relève de sa configuration, pas de Bank.
- Passkeys (WebAuthn) : prévues, le TOTP couvre le besoin en attendant.

## Signaler un problème

Projet personnel : ouvre une issue privée sur le dépôt.
