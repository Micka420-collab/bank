Réponses réelles, réduites, utilisées par `tests/test_energy_signals.py` (#33).

- `puissance_max_quotidienne.json` : réponse de Conso API au type `puissance_conso_max_quotidienne` (API Enedis
  « Mesure synchrone V2 », pas P1D, grandeur PMA, en VA), enregistrée par l'auteur de Conso API dans
  github.com/bokub/linky (`test/fixtures/fixtures.cjs`, PRM anonymisé). Le type et ses paramètres ont été vérifiés en
  direct le 24/10/2026 (réponse 401 sans jeton valide, liste des types acceptés).
- `tempo_*.json` : réponses d'api-couleur-tempo.fr du 24/10/2026 (`/api/joursTempo?dateJour[]=…` et
  `/api/jourTempo/<jour>`), telles quelles.
