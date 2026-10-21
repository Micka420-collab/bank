# Entretien de découverte financière

But : que Bank (et donc toi) connaisse la situation de Micka assez bien pour décider juste. Chaque réponse
enregistrée est visible et modifiable par lui dans Bank › Ma situation, avec sa provenance (« dit à hermes »).

## Déroulé

1. `mcp_bank_onboarding_status` : pourcentage, réponses connues, `suggestions` (valeurs lues dans ses relevés)
   et `next_questions` (triées par importance, avec `why` et `options`).
2. **Confirmer d'abord ce que Bank a détecté** — c'est rapide et ça rassure :
   « Bank voit un salaire d'environ 2 150 € par mois et un loyer de 650 € : c'est bien ça ? »
   Si oui → `record_answer` avec la valeur (en euros). Si non → demande la bonne valeur.
3. Puis les questions restantes, **trois au plus par message**, en reformulant simplement et en donnant la
   raison en une demi-phrase (`why`). Propose les options quand il y en a.
4. Enregistre chaque réponse dès qu'elle est claire. En cas de doute, reformule et fais valider avant d'enregistrer.
5. Fin : `get_dossier` (markdown) → résumé en 5 lignes (forces, fragilités, priorité n° 1), puis :
   « Dernière étape, à faire toi-même dans Bank › Ma situation › Le mandat : mon rôle, la fréquence de mes
   points, ton seuil d'alerte et tes lignes rouges. »

Ne fais pas tout d'un coup si Micka n'a pas le temps : l'onboarding peut s'étaler sur plusieurs conversations.
`onboarding_status` dit toujours où on en est.

## Format des réponses (`mcp_bank_record_answer`)

| Question | Type | Exemple de `value` |
|---|---|---|
| `employment` | choix | `"public"` (cdi_prive, public, cdd, independant, chomage, etudiant, retraite, autre) |
| `net_monthly_income`, `yearly_extras`, `side_income`, `housing_cost`, `savings_capacity` | euros | `2150` ou `"2 150,50"` |
| `income_variability` | choix | `"un_peu"` (stable, un_peu, beaucoup) |
| `tax_bracket` | choix | `"11"` (0, 11, 30, 41, 45, inconnue) |
| `household` | choix | `"seul"` (seul, couple, couple_enfants, parent_solo, colocation) |
| `dependents` | entier 0–12 | `0` |
| `housing_status` | choix | `"locataire"` (locataire, proprietaire_credit, proprietaire, heberge) |
| `housing_project` | choix | `"achat_5ans"` (aucun, achat_5ans, achat_plus, demenager) |
| `emergency_target_months` | entier 1–24 | `4` |
| `risk_horizon` | choix | `"5_10"` (moins_2, 2_5, 5_10, plus_10) |
| `risk_drop` | choix | `"attends"` (vends_tout, vends_partie, attends, renforces) |
| `risk_experience` | choix | `"livrets"` (aucune, livrets, quelques, reguliere) |
| `risk_priority` | choix | `"equilibre"` (securite, plutot_securite, equilibre, plutot_rendement) |
| `priorities` | 1 à 3 choix | `["securite", "projets"]` (securite, liberte, projets, plaisir, famille, retraite, independance, generosite) |
| `money_stress` | entier 1–5 | `3` |
| `notes` | texte libre | `"Je change de poste en janvier"` |

Listes (montants en euros, champs suffixés `_eur`) :

```json
"debts": [{"label": "Prêt auto", "kind": "auto", "remaining_eur": 8400, "monthly_eur": 180,
           "rate_percent": 4.9, "end_date": "2029-03-01"}]
```
`kind` : immobilier, conso, auto, etudiant, familial, decouvert, autre. Une liste vide `[]` = « aucun crédit ».

```json
"outside_assets": [{"label": "PEA Boursorama", "kind": "pea", "value_eur": 2500, "monthly_contribution_eur": 50}]
```
`kind` : pea, assurance_vie, per, epargne_salariale, crypto, immobilier, autre.

```json
"life_goals": [{"label": "Apport appartement", "kind": "immobilier", "amount_eur": 15000,
                "target_date": "2029-06-30", "priority": 1}]
```
`kind` : securite, achat, immobilier, voyage, retraite, enfants, liberte, autre. `priority` : 1 indispensable,
2 important, 3 si possible.

```json
"planned_expenses": [{"label": "Pneus", "amount_eur": 320, "date": "2026-11-15"}]
```
Une dépense prévue change la prévision du reste à dépenser : tu ne l'enregistres pas, tu la **proposes**
(`propose_action`, `kind: "set_answer"`, `params: {"question_id": "planned_expenses", "value": [...]}`) et Micka
la valide dans Bank.

Une liste **remplace** la précédente : relis la valeur actuelle (`onboarding_status.answers`) et renvoie la liste
complète, pas seulement l'élément ajouté.

Une réponse que Micka a donnée **lui-même** dans Bank (`source: "user"`) ne peut pas être remplacée par
`record_answer` : si elle a changé, propose la nouvelle valeur avec `kind: "set_answer"`.

## Ce que tu ne demandes jamais

- Identifiants, mots de passe, codes, numéros de carte, IBAN complets : Bank n'en a pas besoin.
- Les questions du **mandat** (`autonomy`, `report_frequency`, `alert_threshold`, `red_lines`, `tone`) :
  `record_answer` les refuse, c'est à Micka de les régler dans Bank.
- Des détails de santé, de vie privée ou de tiers qui n'ont pas d'effet sur une décision d'argent.
