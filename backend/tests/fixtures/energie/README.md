Réponses réelles, réduites, utilisées par `tests/test_energy_signals.py` (#33).

- `puissance_max_quotidienne.json` : réponse de Conso API au type `puissance_conso_max_quotidienne` (API Enedis
  « Mesure synchrone V2 », pas P1D, grandeur PMA, en VA), enregistrée par l'auteur de Conso API dans
  github.com/bokub/linky (`test/fixtures/fixtures.cjs`, PRM anonymisé). Le type et ses paramètres ont été vérifiés en
  direct le 24/10/2026 (réponse 401 sans jeton valide, liste des types acceptés).
- `tempo_*.json` : réponses d'api-couleur-tempo.fr du 24/10/2026 (`/api/joursTempo?dateJour[]=…` et
  `/api/jourTempo/<jour>`), telles quelles.
- `openmeteo_archive.json` : réponse de `https://archive-api.open-meteo.com/v1/archive` (5 jours de janvier 2025,
  `daily=temperature_2m_mean`, Paris), relevée le 24/10/2026, telle quelle.
- `courbe_de_charge.json` : réponse de Conso API au type `courbe_de_charge_consommation` (48 demi-heures du
  01/04/2026, W, PT30M), enregistrée par l'auteur de Conso API dans github.com/bokub/linky (`test/fixtures/fixtures.cjs`,
  PRM anonymisé) ; le type a été vérifié en direct le 24/10/2026 (401 sans jeton valide, liste des types acceptés).
- `tempo_saison.json` : 5 jours de la réponse d'api-couleur-tempo.fr à `/api/joursTempo?periode=2025-2026` (365 jours,
  300 bleus, 43 blancs, 22 rouges), relevée le 24/10/2026.
