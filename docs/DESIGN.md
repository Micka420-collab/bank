# Bank — design system et direction artistique

## 1. Audit de l'interface précédente (octobre 2026)

Ce qui marchait : une identité (cahier Seyès, marge rouge, dates dans la marge), un chiffre du jour lisible, des
textes en français direct, zéro script inline (CSP stricte), mobile correct.

Défauts relevés, page par page :

| Où | Défaut | Conséquence |
|---|---|---|
| Partout | La marge rouge et les carreaux évoquent l'école, pas la confiance ; aucune élévation : tout est à plat, on ne distingue pas un groupe d'une liste | Hiérarchie faible, impression de prototype |
| Partout | Pas d'icônes, pas d'état visuel de connexion, boutons uniformes | Les actions principales ne ressortent pas |
| Aujourd'hui | Courbe limitée au mois, sans infobulle ni période ; pas de tendance (vs mois précédent), pas de dernières opérations, pas de répartition | Il faut aller chercher ailleurs ce qu'un tableau de bord doit dire en 5 secondes |
| Opérations | 100 lignes identiques, un `<select>` par ligne, aucun total par jour ou par mois, recherche discrète | Bruit visuel, lecture lente |
| Patrimoine | Formulaire d'objectif toujours ouvert, pas d'historique de valeur nette, projection austère | La page sert plus à saisir qu'à comprendre |
| Connecteurs | Tuiles sans couleur de marque, aucun retour pendant la connexion (redirection « à froid »), état de synchro invisible | Connexion bancaire anxiogène |
| Première ouverture | Formulaire de création d'accès brut, puis page vide | Aucun « moment » : on ne sait pas ce que l'application va apporter |
| Formulaires | Champs sans affordance claire, erreurs en texte seul, pas d'état de chargement sur les boutons | Doute après le clic |
| Mobile | Tabbar sans icônes, zones de clic parfois < 44 px, tableaux qui débordent | Fatigue au pouce |
| Accessibilité | Focus visible OK, mais contrastes faibles sur `faint`, infobulles absentes des graphiques, pas de `aria-live` sur les chargements | AA non garanti |

## 2. Direction artistique — « Encre claire »

Une surface calme et lumineuse où **les chiffres sont le héros**. Un seul geste de boldness : le chiffre du jour
qui se compte sous tes yeux et la courbe qui se trace, une fois, à l'ouverture. Tout le reste est discipliné.

- **Typographie** : une seule famille, Bricolage Grotesque Variable (déjà embarquée, axes `wdth` et `opsz`) :
  corps à 100 % de chasse, grands nombres à 92 % de chasse, graisse 750, interlettrage −0,03 em ; chiffres
  tabulaires partout. Échelle : 12 / 14 / 16 / 20 / 24 / 32 / héros `clamp(3rem, 8vw, 5.5rem)`.
- **Couleur** (clair) : papier `#F5F6FA`, surface `#FFFFFF`, surface creuse `#F0F2F8`, trait `#E3E6F0`, encre
  `#141A33` / `#4B5370` / `#8A91AC` ; accent encre `#2F4BDF` (survol `#1F36B8`, lavis `#E8ECFF`) ; gain
  `#1E8A5A`, perte `#D1374F`, attention `#B86E0D`, chacun avec son lavis. Sombre : fond `#0B0F1F`, surface
  `#121831`, surface creuse `#182040`, trait `#262F55`, encre `#E9ECF8` / `#AEB5D1` / `#7B83A6`, accent
  `#8FA3FF`, gain `#5AD39B`, perte `#FF7A8C`, attention `#F2B65B`. Contrastes texte ≥ 4,5:1 (AA) sur leur
  surface, `faint` relevé à `#6E7694` en clair.
- **Surfaces** : cartes à 16 px de rayon, trait 1 px + ombre douce teintée encre (`0 1px 2px` + `0 8px 24px -12px`) ;
  panneaux héros à 24 px ; contrôles à 10 px ; pastilles à 999 px. Pas de dégradés décoratifs.
- **Espace** : grille 4 pt (4 / 8 / 12 / 16 / 24 / 32 / 48 / 64), contenu à 1 200 px maxi, gouttières 32 px
  (16 px sur mobile), 68 ch de ligne maxi.
- **Icônes** : jeu maison en SVG inline, 20 px, trait 1,75, bouts ronds — une icône par destination, jamais
  décorative.
- **Mouvement** : `--ease-out` `cubic-bezier(.2,.8,.2,1)`, 120 ms (retour bouton), 200 ms (état), 320 ms
  (page, graphique). Une seule séquence orchestrée à l'ouverture du tableau de bord (chiffre qui se compte,
  courbe qui se trace, blocs qui apparaissent avec 40 ms d'écart). Ensuite le mouvement ne répond qu'au geste.
  `prefers-reduced-motion` coupe tout.
- **Écriture** : voix active, phrase courte, le bouton dit ce qu'il fait (« Relier l'agenda » → « Agenda
  relié »), les vides invitent à agir, les erreurs disent quoi faire.

### 2 bis. Simple et ludique (octobre 2026)

Demande de Micka : « une app simple et ludique, facilement utilisable, avec un beau design ».

- **Cinq destinations, pas une de plus** : Aujourd'hui, Opérations, **Gagner** (missions + aides),
  **Papiers** (documents + démarches), Épargne ; le reste (Ma situation, Veille, Connecteurs, Réglages) en plus
  petit sous un filet, ou dans « Plus » sur téléphone. Les deux pôles s'ouvrent sur des **onglets** en pastille.
  Sur téléphone : Jour · Opérations · Gagner · Papiers · Plus (les papiers, c'est l'appareil photo).
- **Missions** : chaque piste devient une carte à **récompense** (pastille « pièce » `+13,49 € par mois`), avec
  le temps qu'elle prend, sa chance en trois points, un seul bouton principal, et « Pourquoi ? » replié. La
  première est mise en avant (« La mission du moment »).
- **La tirelire** : un bocal dessiné au trait des icônes, qui se remplit vers le prochain palier (25 €, 50 €,
  100 €…) **uniquement** avec les gains constatés et confirmés ; l'estimé reste « en route », en pointillé, jamais
  ajouté. Paliers atteints en badges. Une pièce tombe dans le bocal quand tu confirmes un gain : c'est le seul
  mouvement de la page, coupé avec `prefers-reduced-motion`.
- **Couleur « pièce »** : `--coin` `#E9A400` (lavis `#FFF3D1`, encre `#7A4D00` ; sombre `#F7C548`, `#3B2F10`,
  `#F9D77E`), réservée à ce qu'on peut gagner. Le vert reste celui de l'argent réellement arrivé.
- **Aujourd'hui allégé** : le chiffre du jour, ce que Bank te demande, tes missions, le mois jour par jour et
  « Je peux m'acheter… ? » ; tout le reste (solde, répartition, échéances, enveloppes, dernières opérations) se
  déplie d'un geste (« Tout le détail », état retenu dans le navigateur).

## 3. Architecture des composants

```
src/
  styles.css            jetons (:root clair / sombre), primitives, composants, pages, mobile
  components/
    ui.tsx              Field, EuroInput, Loading (squelette), ErrorNote, Amount, useToast, useAsyncAction
    Icon.tsx            jeu d'icônes inline (name → path)
    Initiatives.tsx     cartes « Bank te demande »
    Welcome.tsx         trois chemins (après l'onboarding, quand il n'y a pas encore d'opérations)
    BankConnect.tsx     tuiles de marque, recherche instantanée, états (repos, survol, connexion, connecté, erreur)
    Onboarding.tsx      intro animée → accès → banque → synchronisation → confirmation → tableau de bord
    charts/
      TrendChart.tsx    courbe/aire interactive (infobulle, filtres 7 j / 30 j / 3 mois / 1 an / tout)
      Donut.tsx         répartition par catégorie
      Bars.tsx          revenus vs dépenses par période
    CountUp.tsx         chiffre qui se compte (respecte reduced-motion)
    Segmented.tsx       filtres temporels / onglets
  pages/                Today (tableau de bord), Operations, Aids, Wealth, Situation, Connecteurs, Planning,
                        Settings, Auth, Agents
```

Primitives CSS (classes) : `.card`, `.card-hero`, `.card-quiet`, `.btn` (`.quiet`, `.ghost`, `.danger`,
`.link`, `.is-loading`, `.sm`, `.lg`), `.field`, `.input`, `.search`, `.filters`, `.segmented`, `.pill`, `.tag`,
`.stats` / `.stat` (avec `.trend`), `.meter`, `.skeleton`, `.toast`, `.notice`, `.empty`, `.ledger` (mois, jour,
total du jour), `.connected` (liste logo + texte + actions), `.account-list`, `.streams`, `.bank-grid` /
`.bank-tile` / `.bank-mark`, `.table-scroll` (un tableau ne fait jamais déborder la page : il défile dans sa
carte).

Règles de mise en page qui évitent les régressions mobiles : la colonne de contenu est `minmax(0, 1fr)` et la
feuille a `min-width: 0` (un contenu large ne peut pas élargir la page) ; toute grille de contrôles donne
`min-width: 0` à ses enfants ; les tableaux restants sont dans `.table-scroll` ; les listes à colonnes
(comptes, jetons d'agent, versements) sont des `flex-wrap`, pas des tableaux.

Thème : `:root` clair par défaut, sombre via `prefers-color-scheme` **ou** `data-theme="dark"` posé par
`theme.ts` (choix dans Réglages → Apparence, mémorisé dans `localStorage`, appliqué avant le premier rendu).

## 4. Parcours de première ouverture

1. Intro animée (3 écrans, 8 s au total, passables) : « Combien tu peux dépenser aujourd'hui » (le chiffre se
   compte), « Tes aides et ta paie, prévues » (la courbe se trace), « Ton agent, gouverné » (une carte qui se
   retourne).
2. Création de l'accès (jeton d'installation, identifiant, phrase de passe) ou connexion.
3. « Connecte ta banque » : tuiles aux couleurs de chaque banque, recherche instantanée, connexion sécurisée
   DSP2 chez la banque (jamais d'identifiants saisis ici), **« Je connecterai mes comptes plus tard »** toujours
   visible.
4. Retour de la banque : synchronisation avec étapes visibles (connexion établie, comptes récupérés,
   opérations lues, catégories reconnues), puis confirmation des comptes.
5. « Ajouter une autre banque » ou « Voir mon tableau de bord ».

Les logos officiels ne sont pas embarqués (ce sont des marques déposées) : chaque tuile montre une pastille aux
couleurs de la banque ; déposer `frontend/public/banks/<id>.svg` affiche le logo à la place.

## 5. Graphiques

Un seul composant de tendance (`TrendChart`) sert le tableau de bord (solde) et le patrimoine (valeur nette) :
SVG responsive, aire sous la courbe, grille discrète, repère du jour, infobulle au survol/toucher et au clavier
(flèches ; date, valeur, variation), filtres de période, tracé animé au changement de période. Deux échelles :
`baseline="zero"` garde le zéro visible (un solde, dont le découvert est la référence), `baseline="auto"` cadre
les valeurs (une valeur nette, dont les variations comptent plus que la distance à zéro). `Donut` et `Bars` pour
la répartition et la comparaison. Tous compréhensibles sans vocabulaire financier : une phrase sous chaque
graphique dit ce qu'il montre.

Données : `GET /api/history?range=` reconstruit les soldes jour par jour à rebours depuis le solde actuel (exact
sans historique de soldes), agrège par jour / semaine / mois, compare à la période précédente et signale les
catégories qui dérapent (× 1,5 et + 50 €). La valeur nette (`/api/wealth`) est reconstruite de la même façon sur
tous les comptes, virements internes exclus (ils ne changent pas la valeur nette, même quand un seul des deux
comptes est importé) ; les comptes saisis à la main sont supposés stables.

Libellés : les marchands arrivent en capitales de la banque (`CARREFOUR MARKET ORLEANS`) ; `merchantCase` les
écrit comme on les lit (`Carrefour Market Orleans`, `EDF Clients Particuliers`, `CAF du Loiret APL`) et laisse
le libellé brut en dessous.

## 6. Qualité

Chaque composant a ses états (chargement, vide, erreur, déconnecté) ; cibles tactiles ≥ 44 px ; focus visible ;
`aria-live` sur ce qui change ; tests Vitest sur les parcours (`design.test.tsx` : intro → accès → banque →
« plus tard », retour de la banque → synchronisation → confirmation, serveur sans synchro bancaire, tuiles /
recherche / caisses régionales / états, connexion rapide avec code validé tout seul **une seule fois par code**,
graphiques, thème) et captures Playwright à 390 / 820 / 1340 px, clair et sombre, sans erreur de console ni
violation CSP ; un script mesure le débordement horizontal de chaque page à 390 px (aucun).

Ce que l'on a volontairement laissé de côté : les logos officiels (marques déposées : une pastille aux couleurs
de l'enseigne, remplaçable par `public/banks/<id>.svg`), les gestes de balayage (le navigateur et la barre
d'onglets suffisent ; un balayage « retour » entrerait en conflit avec celui du système), et toute animation
qui ne répond pas à une action.
