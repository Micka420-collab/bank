# Modèle de menace et défenses

Bank contient l'historique bancaire complet d'un foyer, ses revenus et sa situation sociale. Chaque menace ci-dessous
a au moins une défense testée (référence du test entre parenthèses).

## Ce qu'on protège

| Donnée | Où | Protection |
|---|---|---|
| IBAN, uid de compte DSP2, session DSP2, secret TOTP, profil du foyer, résultats de simulation | SQLite | AES-256-GCM par champ, contexte lié en AAD (`test_context_binding_blocks_column_swap`) |
| Mots de passe | SQLite | Argon2id 64 MiB, t=3, p=2, re-hash automatique (`test_hash_and_verify`) |
| Jetons de session | SQLite | Seule l'empreinte SHA-256 est stockée (`test_session_token_stored_hashed_and_idle_expiry`) |
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

## Hors périmètre (assumé)

- Compromission de nimbus avec accès root : la clé maîtresse est lisible. Défense : disque chiffré (LUKS), mises à jour.
- Un seul worker uvicorn : le rate-limit en mémoire est exact ; avec plusieurs workers il faudrait un stockage partagé.
- Passkeys (WebAuthn) : prévues, le TOTP couvre le besoin en attendant.

## Signaler un problème

Projet personnel : ouvre une issue privée sur le dépôt.
