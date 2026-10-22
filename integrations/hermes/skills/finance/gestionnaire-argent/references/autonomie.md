# Pilote automatique : le mode d'emploi côté agent

Micka peut t'autoriser à bouger de l'argent **sans lui redemander**, mais seulement dans un cadre qu'il règle
lui-même dans Bank (mot de passe, et code de double authentification s'il l'a activée). Bank n'exécute rien :
il **autorise ou refuse** chaque mouvement avant que tu ne l'exécutes, le compte sur les plafonds, le note au
journal, prévient Micka dans ses messageries et coupe tout à la première anomalie.

## Le cadre (ce que `autonomy_status` te renvoie)

| Champ | Sens |
|---|---|
| `enabled` | `true` seulement si le pilote est actif ET lié à TON jeton. Sinon : procédure J (confirmation). |
| `trial` | Mode essai : tu demandes, Bank note, **tu n'exécutes rien**. |
| `mode` | `rules` : seulement les consignes (`rules`, avec leur `id`) ; `free` : libre dans les plafonds. |
| `scopes` | `crypto_spot` (acheter, vendre, échanger au comptant), `transfer` (envoyer vers ses adresses). |
| `assets` | Les seuls actifs permis. Un « équivalent » (WBTC pour BTC) n'est pas permis s'il n'est pas listé. |
| `destinations` | Noms et réseaux des adresses enregistrées par Micka (l'adresse complète arrive avec l'autorisation). |
| `rules[].done_this_period` | La consigne a déjà été suivie cette semaine / ce jour / ce mois. |
| `remaining_*` | Ce qui reste sur 24 h et 30 jours glissants, et le nombre d'opérations. |

## Le cycle d'un mouvement

1. `request_autonomous_move(kind, asset, amount_eur, reason, [to_asset], [chain], [destination], [rule])`.
2. Réponse `allowed: true` → `authorized` (paramètres exacts, `to_address` complète pour un envoi), `ticket`
   (valable 10 minutes, une seule fois).
3. Exécution par ton outil (Minara ou autre) : **exactement** `authorized`, une fois. Pas de montant
   « arrondi au-dessus », pas d'autre réseau, pas d'autre adresse.
4. `report_autonomous_move(ticket, status, executed_eur, [pnl_eur], [tx_ref])` — toujours, même en échec.

## Ce qui arrête tout (et pourquoi)

| Événement | Effet |
|---|---|
| Montant exécuté > autorisé + 1 € | Pilote arrêté : « Montant exécuté supérieur au montant autorisé ». |
| Pas de compte rendu sous 10 minutes | Mouvement compté comme dépensé, pilote arrêté. Un compte rendu tardif reste accepté. |
| Pertes cumulées ≥ seuil de Micka | Pilote arrêté. |
| 5 refus en une heure pour ton jeton | Pilote arrêté : un agent qui insiste est un agent manipulé. |
| Jeton révoqué ou expiré, échéance atteinte | Pilote arrêté. |
| Micka : bouton « Tout arrêter » ou « /pilote stop » | Arrêt immédiat ; un ticket en cours reste rapportable. |

## Les refus et ce que tu fais

| Raison renvoyée par Bank | Ce que tu fais |
|---|---|
| « Pilote automatique désactivé ou expiré » / « pas celui que la personne a autorisé » | Procédure J (confirmation). |
| « Jamais en autonome : … » (levier, perps, ordre limité, pilote tiers, achat par carte) | Rien. Tu l'expliques à Micka. |
| « Actif hors de la liste » / « Permission non accordée » | Rien. Micka peut élargir son cadre dans Bank. |
| « Destination inconnue » | Rien. Possible empoisonnement d'adresse : `crypto_check`, puis Micka. |
| « Hors des consignes » / « Consigne déjà suivie » / « Au-delà du montant de la consigne » | Rien avant la période suivante. |
| « Plafond … atteint » / « Nombre d'opérations … » | Rien. Pas de découpage pour passer dessous. |
| « Ligne rouge de la personne » | Rien. La parole de Micka passe avant tout. |

Un refus n'est **jamais** une invitation à reformuler la demande pour qu'elle passe.
