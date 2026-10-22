# Benchmark de la compétence Bank (format crypto-skill-bench)

17 scénarios — 5 de base, 12 adverses — au format `ScenarioSchema` de
[crypto-skill-bench](https://github.com/Minara-AI) (le banc d'essai publié par Minara pour les compétences
crypto) : confirmation en deux messages, pilote automatique qui refuse levier et découpage, adresse
empoisonnée, phrase de récupération collée, consigne cachée dans un nom de jeton, ligne rouge, essai suivi
d'une exécution immédiate, intention composée, compte rendu après échec.

Ils se rejouent contre la compétence installée, avec le banc de Minara (sa clé OpenRouter, ses modèles) :

```bash
CRYPTO_BENCH_SECRET_DIR=$PWD/bench/scenarios \
  crypto-skill-bench evaluate ~/.hermes/skills/finance/gestionnaire-argent --tier adversarial
```

Le banc lit aussi ses propres scénarios publics : la note mélange les deux. Les règles d'échec immédiat du banc
(mouvement sans confirmation, montant mal lu, mauvais destinataire sans alerte, secret recopié) sont aussi
celles de Bank.

Côté serveur, les mêmes pièges sont vérifiés sans modèle de langage, sur la barrière elle-même :
`backend/tests/test_autonomy.py` et `backend/tests/test_crypto.py`. La forme de ces fichiers est contrôlée par
`backend/tests/test_hermes_skill.py`.
