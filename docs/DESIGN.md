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

## 3. Verre lumineux (octobre 2026)

Demande de Micka : « un vrai design moderne pensé expérience utilisateur, plus amusant pour une personne qui
n'aime pas faire ses comptes et ses papiers ; friendly, amusant, stylé, beau, moderne ; boutons arrondis, effets
de lumière, un peu de transparence ». Les sections 1 et 2 restent comme historique : « Verre lumineux » garde la
typographie, l'espace, les icônes, l'écriture et la tirelire d'« Encre claire » et de « Simple et ludique », et
remplace la couleur, les surfaces, les boutons et le mouvement.

### 3.1 Audit UX : ce qui pèse à quelqu'un qui n'aime pas ses comptes

Audit fait à la lecture du code (`App.tsx`, `styles.css`, pages) : les captures « avant » n'ont pas pu être
prises, le Chromium sans tête de Playwright n'a pas ses bibliothèques système (`libatk-1.0.so.0` manquante) —
à refaire dès qu'elles seront installées.

| Où | Ce qui ressemble à une corvée, ou qui inquiète | Ce qui manque |
|---|---|---|
| Partout | Cartes blanches à plat les unes sous les autres, une seule couleur d'accent, boutons à 10 px de rayon : l'écran a l'air d'un formulaire administratif | Un fond vivant, de la profondeur, une couleur qui change avec l'humeur du mois |
| Partout | Les avertissements (`.notice.warn`) s'empilent en bandeaux jaunes : « à relancer », « en retard », « dérape » — on lit des reproches | Une seule alerte à la fois, formulée comme une prochaine étape, et la récompense à côté |
| Partout | Un succès n'est qu'un texte dans un toast de 4,5 s, identique à une erreur (« Résultat enregistré. ») | Un retour à la hauteur : coche qui se trace, pièce qui tombe, halo qui s'allume |
| Aujourd'hui | Le chiffre du jour est bien le héros, mais « Découvert prévu le… » s'affiche dans la même carte, en rouge, sans issue ; ensuite Initiatives, Copilote, mois, année, « Je peux m'acheter » : six blocs avant la fin du premier écran de téléphone | Le chiffre d'abord, une phrase rassurante, puis **une** chose à faire ; la mauvaise nouvelle avec son « que faire » |
| Opérations | Jusqu'à 100 lignes identiques, un `<select>` par ligne ; « à classer » est une corvée sans fin visible (aucun compteur qui descend) | Un compteur « encore 7 à classer » qui fond, un mode tri rapide, un « Tout est rangé » qui fait plaisir |
| Gagner | La tirelire est le seul moment joyeux de l'app ; les missions sont une longue liste de textes, « chance » en trois points peu lisible | Missions comme des cartes à collectionner, récompense en pièce lumineuse, gain confirmé célébré |
| Papiers · Documents | Paragraphe d'en-tête de trois lignes sur téléphone ; le dépôt de fichier ne se voit pas comme l'action principale | Une grande zone de dépôt « Prends en photo » lumineuse ; « 12 papiers rangés » comme une collection |
| Papiers · Démarches | Tableau en colonnes (à préparer, prêt, envoyé, terminé) empilées sur téléphone ; les démarches « à relancer » en bandeaux jaunes | Une barre de progression par dossier, la prochaine étape en gros, une fête quand le dossier est validé ou obtenu |
| Aides, Épargne, Crypto, Pilote | Pages denses en chiffres et en tableaux, vocabulaire financier | Une phrase en tête qui dit « ce que ça veut dire pour toi », les détails repliés |
| Réglages, Connecteurs | Longs formulaires plats | Groupes en cartes, états « relié » qui brillent en vert |
| Téléphone | La barre d'onglets n'a **pas** les pastilles de compte que le rail montre (gains à confirmer, démarches à traiter) ; Épargne est une destination principale au bureau mais cachée dans « Plus » au téléphone ; onglets de pôle (`.hub-tabs`) en simples liens ; le bouton flottant recouvre la fin des listes | Pastilles sur les onglets, onglet actif allumé, onglets de pôle en segments, marge basse pour le bouton flottant |
| Accessibilité | `.faint` (`--ink-3`) posé sur le fond nu et dans les bandeaux ; aucun repli pour `prefers-reduced-transparency` (déjà du `backdrop-filter` sur la barre d'onglets) | Règles de contraste sur verre, replis opaques |

### 3.2 Principes

1. **Amusant mais calme.** La lumière fait sourire, elle ne clignote pas. Le fond respire, les surfaces sont
   posées dessus comme du verre dépoli ; le mouvement ne répond qu'à un geste (exception : l'ouverture du jour).
2. **L'argent comme une progression, jamais comme une faute.** On montre ce qui avance (tirelire, papiers
   rangés, opérations classées, dossiers prêts) avant ce qui manque. Une dépense n'est jamais rouge par
   principe : le rouge est réservé à ce qui demande une action (découvert prévu, échéance ratée), et il vient
   toujours avec la phrase « ce que tu peux faire ».
3. **Une chose à la fois.** Chaque écran a une action principale (le bouton lumineux) ; les autres sont
   discrètes. Une seule alerte en tête de page, les suivantes repliées (« 2 autres points »).
4. **Fêter ce qui est fait.** Chaque fin de corvée a son petit moment (§ 3.6), court, une fois, coupé par
   `prefers-reduced-motion`. On ne fête jamais une estimation, seulement ce qui est constaté ou validé par toi.
5. **Le verre sert la lecture.** Plus il y a de texte, plus la surface est opaque. Le flou est réservé à
   quelques grandes surfaces ; une liste de cent lignes n'en porte jamais.

### 3.3 Jetons

Les noms existants (`--bg`, `--surface`, `--ink`, `--accent`, `--positive`…) et leurs alias historiques sont
gardés : les composants actuels changent d'apparence sans changer de classe. Les nouveaux jetons s'ajoutent.
Les trois blocs habituels sont tenus à jour ensemble : `:root` (clair), `@media (prefers-color-scheme: dark)
:root:not([data-theme="light"])`, et `:root[data-theme="dark"]` (choix de `theme.ts`, qui peint aussi
`meta[name="theme-color"]` : `#f4f3fb` / `#0c0b1d`).

#### Fond et halos

| Jeton | Clair | Sombre | Rôle |
|---|---|---|---|
| `--bg` | `#F4F3FB` | `#0C0B1D` | Fond de page (lavande très pâle / nuit violette) |
| `--halo-1` (violet) | `#8B7CFF` à 40 % | `#6D5CFF` à 35 % | Haut gauche, derrière le rail et le chiffre du jour |
| `--halo-2` (rose) | `#FF8FB8` à 40 % | `#FF5FA2` à 30 % | Haut droit |
| `--halo-3` (menthe) | `#5EE0C0` à 40 % | `#22C9A0` à 28 % | Bas gauche ; teinte « tout va bien » |
| `--halo-4` (soleil) | `#FFC65C` à 40 % | `#F7B73B` à 24 % | Bas droit ; teinte « pièce », allumée par les gains |

Les halos sont quatre `radial-gradient` (rayon 38–48 vmax, transparent au bord) sur un calque
`body::before` en `position: fixed; inset: 0; z-index: -1; pointer-events: none` : aucune image, aucun script,
rien à recalculer au défilement. Pas d'animation continue. Sur téléphone, deux halos seulement (violet en haut,
menthe en bas) pour garder le fond calme sous le pouce.

#### Verre

| Jeton | Clair | Sombre | Usage |
|---|---|---|---|
| `--glass` | `rgba(255,255,255,.74)` | `rgba(26,24,56,.70)` | Grandes surfaces : héros (posé sur son halo, sans flou), rail, toasts, feuille de l'assistant |
| `--glass-strong` | `rgba(255,255,255,.88)` | `rgba(26,24,56,.86)` | Cartes ordinaires, listes, formulaires, tableaux (beaucoup de texte) |
| `--glass-blur` | `blur(20px) saturate(160%)` | `blur(24px) saturate(140%)` | **Seulement** ce qui est fixe ou collant : rail, barre d'onglets (flou 28 px, verre à 90 %), toasts, assistant, modales. Pas le héros : il défile, le flou s'y recalculerait à chaque image pour un effet invisible |
| `--glass-edge` | `rgba(255,255,255,.70)` | `rgba(255,255,255,.10)` | Bordure 1 px |
| `--glass-shine` | `inset 0 1px 0 rgba(255,255,255,.90)` | `inset 0 1px 0 rgba(255,255,255,.08)` | Reflet du haut, comme une arête de verre |
| `--glass-shadow` | `0 12px 32px -14px rgba(52,40,120,.28), 0 2px 6px -2px rgba(52,40,120,.10)` | `0 20px 40px -20px rgba(0,0,0,.70)` | Élévation |
| `--surface` (opaque) | `#FFFFFF` | `#17152F` | Repli sans transparence, champs de saisie, menus |
| `--surface-2` | `#F0EEFA` | `#201D40` | Creux (pistes de jauge, segments) |
| `--line` / `--line-strong` | `#E4E1F3` / `#8C89B0` | `#2C2858` / `#6F6B9E` | Traits ; `--line-strong` borde les champs (3,3:1 / 3,5:1, ≥ 3:1 exigé) |

Pourquoi les cartes ordinaires n'ont pas de flou : les halos sont déjà des dégradés très doux, un `backdrop-filter`
derrière une carte n'y changerait presque rien à l'œil mais coûterait une couche de composition par carte. Elles
prennent `--glass-strong` sans flou ; l'effet « verre » vient de la bordure claire, du reflet et de la teinte qui
transparaît.

#### Accent, lumière, formes

| Jeton | Clair | Sombre |
|---|---|---|
| `--accent` (liens, anneau de focus, icônes actives) | `#4B3BEA` | `#A99DFF` |
| `--accent-grad` (bouton principal, onglet actif, anneau de progression) | `linear-gradient(135deg, #5B4BFF, #9B3FD8)` | `linear-gradient(135deg, #8C7CFF, #D07BFF)` |
| `--on-accent` | `#FFFFFF` | `#0C0B1D` |
| `--accent-soft` / texte dessus `--accent-ink` | `#EBE8FF` / `#3F32C8` | `#2A2560` / `#C9C1FF` |
| `--glow` (survol, focus des boutons principaux) | `0 0 0 4px rgba(91,75,255,.18), 0 10px 28px -8px rgba(123,69,236,.60)` | `0 0 0 4px rgba(169,157,255,.22), 0 10px 32px -6px rgba(208,123,255,.55)` |
| `--glow-coin` (gain) | `0 0 0 4px rgba(233,164,0,.22), 0 10px 28px -8px rgba(233,164,0,.65)` | `0 0 0 4px rgba(247,197,72,.22), 0 10px 32px -6px rgba(247,197,72,.50)` |
| `--r-pill` | `999px` | |
| `--r-card` / `--r-hero` / `--r-control` | `22px` / `32px` / `14px` | |

Le rayon passe de 16 à 22 px pour les cartes, 32 px pour le héros ; boutons, pastilles, segments, recherche
et barre d'onglets en pilule (`--r-pill`) ; champs de saisie à 14 px (un champ en pilule se lit mal sur
plusieurs lignes). `--r-s`, `--r-m`, `--r-l` deviennent des alias de `--r-control`, `--r-card`, `--r-hero`.

#### Couleurs de sens

| Jeton | Clair | Sombre | Pastille (`-soft`) clair / sombre |
|---|---|---|---|
| `--positive` (argent arrivé, fait, relié) | `#127A4C` | `#5AD39B` | `#DDF5EA` / `#163A2C` |
| `--negative` (action requise : découvert, échéance ratée) | `#C42B4E` | `#FF8597` | `#FDE6EC` / `#3F1A28` |
| `--warning` (à surveiller) | `#8F5407` | `#F2B65B` | `#FDF0D9` / `#3A2C14` |
| `--coin` (ce qu'on peut gagner ; remplissage seulement) | `#E9A400` | `#F7C548` | `#FFF1C7` / `#3B2F10` |
| `--coin-ink` (texte « pièce ») | `#7A4D00` | `#F9D77E` | |
| `--ink` / `--ink-2` / `--ink-3` | `#17163A` / `#4A4870` / `#5B5980` | `#F1EFFF` / `#BDB9E0` / `#A29ECB` | |

#### Contrastes vérifiés (WCAG 2.2, texte ≥ 4,5:1, éléments d'interface ≥ 3:1)

Calculés sur le fond **réel** : verre composé au-dessus du pic du halo le plus défavorable (violet en clair,
menthe en sombre), avec les opacités ci-dessus. Script de calcul : luminance relative sRGB, composition alpha.

| Texte | sur `--glass` clair (pire : `#F1F0FE`) | sur `--glass-strong` clair (`#F9F8FF`) | sur `--glass` sombre (pire : `#18243B`) | sur `--glass-strong` sombre (`#191E39`) |
|---|---|---|---|---|
| `--ink` | 15,3 | 16,4 | 13,7 | 14,5 |
| `--ink-2` | 7,6 | 8,1 | 8,3 | 8,7 |
| `--ink-3` | 5,8 | 6,2 | 6,1 | 6,5 |
| `--accent` | 6,0 | 6,4 | 6,6 | 7,0 |
| `--positive` | 4,8 | 5,1 | 8,3 | 8,8 |
| `--negative` | 4,9 | 5,2 | 6,7 | 7,1 |
| `--warning` | 5,4 | 5,8 | 8,6 | 9,1 |
| `--coin-ink` | 6,4 | 6,9 | 11,1 | 11,7 |

| Paire | Ratio |
|---|---|
| `#FFFFFF` sur `--accent-grad` clair (début `#5B4BFF` / milieu `#7B45EC` / fin `#9B3FD8`) | 5,4 / 5,4 / 5,1 |
| `#0C0B1D` sur `--accent-grad` sombre (début `#8C7CFF` / milieu / fin `#D07BFF`) | 6,0 / 6,6 / 7,4 |
| `#FFFFFF` sur `--negative` clair (bouton danger plein) · `#0C0B1D` sur `#FF8597` sombre | 5,5 · 8,4 |
| Pastilles : positive 4,7 · negative 4,7 · warning 5,4 · coin 6,5 · accent 7,0 (clair) ; 6,7 · 6,5 · 7,5 · 9,4 · 8,2 (sombre) | ✓ |
| Toast `#FFFFFF` sur `#17163A` (clair) · `#17163A` sur `#F1EFFF` (sombre) | 17,3 · 15,3 |
| Repli opaque : `--ink-3` sur `#FFFFFF` · sur `#17152F` | 6,6 · 7,0 |
| Anneau de focus `--accent` sur `--bg` | 6,1 (clair) · 8,3 (sombre) |
| Bordure de champ `--line-strong` sur `--surface` | 3,3 (clair) · 3,5 (sombre) |
| Arc de progression `#5B4BFF` sur verre clair · `#8C7CFF` sur verre sombre | 4,8 · 4,8 |

**Héros** : son halo est peint **sous** le verre (`linear-gradient(var(--glass))` par-dessus un radial
`--hero-halo` par-dessus `--bg`), donc voilé lui aussi. Pire coin (en haut à droite, halo à pleine force) :
clair `--ink-3` 5,5 · `--accent` 5,6 · `--negative` 4,6 · `--positive` 4,47 ; sombre `--ink-2` 7,3 · `--ink-3` 5,4
· `--accent` 5,8. `--positive` clair frôle 4,5 : **tout texte coloré d'un héros va sur une pastille `-soft`
opaque** (verdict, delta), jamais à nu. Le halo du héros suit les replis : 50 % en transparence réduite ou sans
`backdrop-filter`, retiré en contraste renforcé (`--hero-halo-mix`).

**Règle du fond nu** : directement sur `--bg` + halos (titres de page, en-têtes de section hors carte), seuls
`--ink` (≥ 10) et `--ink-2` (≥ 5,2) sont permis. `--ink-3` y tombe à 4,0 en clair, les couleurs de sens à
3,3–3,7 : tout texte `.faint`, `.muted` coloré, montant signé ou `.notice` va **dans** une surface de verre.
`--coin` (`#E9A400`, 1,9:1 sur verre) ne porte jamais de sens seul : il remplit, et le chiffre ou le contour en
`--coin-ink` porte l'information.

### 3.4 Composants

**Boutons** (tous `min-height: 44px`, `border-radius: var(--r-pill)`, `padding: .6rem 1.25rem`, graisse 650) :

| Variante | Classe (inchangée) | Repos | Survol / focus clavier | Appui |
|---|---|---|---|---|
| Principal | `.btn` | `--accent-grad`, texte `--on-accent`, `--glass-shine` | `--glow` + soulèvement de 1 px ; le dégradé glisse (`background-size: 200%`, `background-position` 0 → 100 %) | `scale(.97)`, lueur retirée |
| Secondaire | `.btn.quiet` | `--glass-strong`, bord `--glass-edge`, texte `--ink` | bord `--accent`, halo doux `0 0 0 4px var(--accent-soft)` | `scale(.97)` |
| Discret | `.btn.ghost` | transparent, texte `--accent` | fond `--accent-soft` | `scale(.97)` |
| Danger | `.btn.danger` | transparent, bord et texte `--negative` | fond `--negative-soft` ; jamais de lueur | `scale(.97)` |
| Gain | `.btn.coin` (nouveau) | fond `--coin-soft`, texte `--coin-ink`, pièce à gauche | `--glow-coin` | `scale(.97)` |
| Lien | `.btn.link` | inchangé, souligné | couleur `--accent` | — |

`.sm` garde 44 px au téléphone (règle existante) ; `.lg` 52 px. `.is-loading` : le texte reste, une petite
orbite tourne à gauche (coupée en mouvement réduit, remplacée par « … »). Un seul `.btn` principal visible par
carte.

**Pastilles et puces** : `.chip`, `.pill`, `.tag`, `.chip-count` en pilule. `.chip` au repos en verre fort
(bord `--glass-edge`) ; `[aria-pressed="true"]` en `--accent-grad` avec `--on-accent`. `.tag` reste petit (texte
12 px graisse 650) sur la pastille `-soft` de sa couleur de sens, avec un point de couleur devant ; la
pastille « pièce » (`.coin-chip`, `+13,49 € par mois`) brille d'un `--glow-coin` réduit de moitié.

**Cartes** : `.card`, `.panel`, `.chart-wrap` → `--glass-strong`, bord `--glass-edge`, `--glass-shine` +
`--glass-shadow`, `--r-card`. `.card-quiet` → `--surface-2` à 70 % sans ombre. `.card-hero` → `--glass` posé
sur son propre halo (radial, couleur selon l'état, voir chiffre du jour), sans flou, `--r-hero`. Deux cartes de
verre consécutives gardent 1rem d'écart. Le ton d'une carte arrondie (bandeau, demande, mission, veille) est une
barre de 4 px en pilule **posée à l'intérieur**, retirée de l'arrondi (`--stripe`), jamais une bordure gauche
épaisse qui se courberait en croissant.
Les listes à l'intérieur (`.ledger`, `.opp-list`, `.proc-card`…) n'ont **ni** flou **ni** ombre par ligne : un
séparateur 1 px `--line` et, au survol, `--accent-soft` à 50 %.

**Chiffre du jour** (`.today-figure`) : 92 % de chasse, graisse 750 (inchangé), couleur `--ink` ; derrière, le
halo du héros prend la couleur de l'état du mois : menthe (`ok`), soleil (`tight`), rose (`risk`) — jamais de
rouge vif. Le verdict passe en pastille sous le chiffre (`.verdict[data-status]`, pastille `-soft`) et, en cas de
découvert prévu, porte un lien « Que faire ? » vers « Je peux m'acheter… ? » / l'assistant.

**Rail** (bureau) : `--glass` + `--glass-blur`, détaché du bord (`margin: 12px`, `--r-hero`, hauteur
`calc(100dvh - 24px)`). `.nav-link` en pilule ; actif : fond `--accent-grad` à 14 % d'opacité + texte
`--accent-ink` + icône `--accent`, et un point lumineux de 6 px à gauche. `.nav-badge` en `--accent-grad`.

**Barre d'onglets** (téléphone, `.tabbar`) : île flottante en pilule (`inset: auto 12px calc(10px +
env(safe-area-inset-bottom)) 12px`), `--glass` + `--glass-blur`, `--glass-shadow`. Cinq cibles ≥ 48 × 52 px.
Onglet actif : une bulle `--accent-grad` derrière l'icône (40 × 32 px, texte `--on-accent` pour l'icône,
libellé dessous en `--accent-ink`) qui glisse d'un onglet à l'autre (`transform`, 280 ms). **Pastilles de
compte** sur Gagner (gains à confirmer) et Papiers (démarches à traiter), les mêmes que le rail. La page garde
une marge basse de `calc(9.5rem + env(safe-area-inset-bottom))` pour que ni l'île ni le bouton flottant ne
cachent la dernière ligne.

**Onglets de pôle** (`.hub-tabs`) et **segments** (`.segmented`) : piste en pilule, segment actif en
`--accent-grad` avec texte `--on-accent` (comme pastilles, sommaire et onglets : ce qui est choisi s'allume),
44 px. Un segment « de verre enfoncé » ne se distinguait de sa piste qu'à 1,1:1 (WCAG 1.4.11). Les liens
`aria-current="page"` gardent leur sémantique.

**Champs** (`.field input`, `.input`, `select`, `textarea`) : `--surface` opaque (le texte saisi ne se lit
jamais sur du flou), bord `--line-strong`, `--r-control`, 44 px ; focus : bord `--accent` + `0 0 0 4px
var(--accent-soft)`. Recherche (`.search`) en pilule avec loupe. Erreur : bord `--negative` + message sous le
champ avec icône, jamais la couleur seule.

**Focus visible** : double anneau lisible sur verre comme sur halo — `outline: 3px solid var(--accent);
outline-offset: 2px` + `box-shadow: 0 0 0 2px var(--bg)` côté intérieur pour les boutons en dégradé.

**Toasts** (`.toast`) : pilule en bas au centre (au-dessus de l'île sur téléphone), `--glass` + flou, icône à
gauche. Variantes par classe : neutre, `.is-success` (coche verte qui se trace), `.is-coin` (pièce +
`--glow-coin`), `.is-error` (reste 8 s et garde un bouton « Réessayer » quand il y en a un). `useToast(msg,
kind?)` garde sa signature actuelle (le second argument est facultatif). Région `aria-live="polite"` inchangée.

**Bandeaux** (`.notice`) : verre fort, une barre de 4 px à gauche dans la couleur de sens, icône dans une
pastille ronde `-soft`. Une page n'affiche qu'**un** bandeau d'alerte déplié ; les suivants se regroupent
(« 2 autres points à voir »).

**États vides** (`.empty`, `.empty-inline`) : petite illustration au trait des icônes (SVG inline, 96 px :
bocal, dossier, soleil, coche), une phrase qui félicite ou invite, un bouton. Exemples (le texte existant est
gardé quand il existe) : plus rien « à classer » → « Tout est rangé. » ; plus de mission → texte actuel + soleil ;
aucun document → « Prends un papier en photo : Bank le lit pour toi. »

**Progression** :
- **Anneau** (`.ring`, nouveau, SVG inline 48–96 px) : piste `--surface-2`, arc en `--accent-grad` (dégradé SVG
  `<linearGradient>` dans le JSX, pas d'URL externe), bout rond, valeur en texte au centre et `role="img"` +
  `aria-label` (« 5 papiers sur 7 à jour »). Sert à : opérations classées sur le mois, papiers à jour, dossier
  de démarche.
- **Barre** (`.meter`, `.piggy-bar`) : 10 px de haut, pilule, remplissage `--accent-grad` (ou `--coin` pour la
  tirelire, `--negative` seulement au-delà de 100 %), repère « rythme attendu » gardé.
- **Étapes** (`.proc-phases`) : pastilles reliées par un trait qui se remplit ; étape faite = coche sur
  `--positive-soft`, étape en cours = `--accent-grad` avec `--glow`.

### 3.5 Pages : ce qui change pour l'expérience

- **Aujourd'hui** : salutation selon l'heure au-dessus du chiffre (« Bonjour Micka » / « Bonsoir Micka »), le
  chiffre, le verdict en pastille, puis **une** carte « La prochaine chose à faire » (la première initiative ou
  mission) ; le reste comme aujourd'hui, replié derrière « Tout le détail » (pilule).
- **Opérations** : en tête, l'anneau « classé ce mois » et un bouton « Classer les 7 restantes » qui ouvre un
  mode tri rapide (une opération à la fois, catégories en grosses pastilles, « Passer ») ; la fin du tri déclenche
  le moment « Tout est rangé ». La liste garde son `<select>` (accessibilité, tests).
- **Gagner** : la tirelire en carte héros (verre + halo soleil) ; missions en cartes avec la pièce lumineuse en
  coin, chance en jauge à trois crans colorés plutôt qu'en points.
- **Papiers** : Documents s'ouvre sur une grande zone de dépôt lumineuse (bord en pointillé `--accent`, icône
  appareil photo, « Prends en photo ou dépose un PDF ») et la collection « N papiers rangés » ; Démarches montre
  chaque dossier comme une carte avec sa barre d'étapes et « Prochaine étape : … » en gros, les colonnes
  deviennent des filtres en pastilles au téléphone.
- **Épargne, Aides, Crypto, Pilote** : une phrase « ce que ça veut dire pour toi » en tête de page, chiffres clés
  en pastilles, tableaux repliés.
- **Plus** (téléphone) : tuiles en verre fort avec l'icône dans une pastille de couleur (violet, rose, menthe,
  soleil en alternance), Épargne en premier.
- **Réglages, Connecteurs** : groupes en cartes ; un service relié porte une pastille `--positive` qui
  s'allume (`--glow` vert) au moment où la liaison réussit.

### 3.6 Moments de fête : exactement quand

Un moment ne se déclenche **que** sur une action de l'utilisateur qui vient de réussir (réponse 2xx du
serveur), **une fois** par événement, jamais au chargement d'une page, jamais pour une estimation, jamais pour
une dépense. Il dure moins d'une seconde, n'empêche aucun clic et annonce son texte par le toast (`aria-live`).

| Événement (déclencheur dans le code) | Moment | Toast |
|---|---|---|
| Gain confirmé (`ConfirmGain` → confirmation acceptée) | La pièce tombe dans le bocal (existant) + 8 petites pièces jaillissent du bouton (CSS, 700 ms) + halo soleil du fond s'allume 1,2 s | `.is-coin` « +X dans ta tirelire ! » (texte existant) |
| Palier de tirelire franchi par ce gain (25, 50, 100 €…) | Le badge du palier apparaît en « pop » (échelle .6 → 1, ressort) | `.is-coin` « Palier de X atteint » |
| Résultat de démarche « obtenu » avec montant | Comme un gain confirmé | Texte existant, en `.is-coin` |
| Dossier validé (`Review` → « Dossier validé : tu peux le transmettre. ») | L'étape « Valider » se coche (coche qui se trace, 400 ms) et la barre d'étapes avance | `.is-success`, texte existant |
| Document déposé et lu | La vignette arrive dans la collection, coche verte qui se trace, le compteur « N papiers rangés » monte de 1 | `.is-success` |
| Dernière opération « à classer » de la page classée | L'anneau se ferme, une étincelle tourne une fois autour, l'état vide « Tout est rangé. » remplace la bannière | `.is-success` |
| Banque, messagerie ou agenda relié avec succès | La pastille de l'élément passe au vert avec `--glow` vert 1 s | `.is-success`, textes existants |

Rien d'autre ne célèbre. Les confettis plein écran, les sons et les vibrations sont exclus.

### 3.7 Mouvement

| Jeton | Valeur | Usage |
|---|---|---|
| `--ease-out` | `cubic-bezier(.2,.8,.2,1)` (inchangé) | Entrées, états |
| `--ease-spring` | `cubic-bezier(.34,1.3,.64,1)` (inchangé) | Bulle de l'onglet actif, « pop » d'un badge |
| `--d-fast` / `--d-base` / `--d-slow` | 120 / 200 / 320 ms (inchangés) | Retour bouton / état / page |
| `--d-glow` | 240 ms | Allumage de la lueur au survol et au focus |
| `--d-party` | 700–1 200 ms | Moments de fête, jamais plus |

On n'anime que `transform`, `opacity`, `background-position` et `box-shadow` d'un seul élément à la fois. Pas
d'animation infinie (les halos sont fixes ; seule l'orbite de chargement tourne tant que le bouton attend).
L'ouverture du jour (chiffre qui se compte, cartes qui montent avec 45 ms d'écart) reste la seule séquence non
demandée. `prefers-reduced-motion: reduce` coupe tout (règle globale existante) : les moments de fête se
réduisent à l'état final (badge visible, coche pleine) et au toast.

### 3.8 Replis et accessibilité

- `@media (prefers-reduced-transparency: reduce)` **et** `@supports not ((backdrop-filter: blur(1px)) or
  (-webkit-backdrop-filter: blur(1px)))` : `--glass` et `--glass-strong` deviennent `--surface` (opaque),
  `backdrop-filter: none` ; les halos restent mais à 50 % de leur opacité. Écrire `-webkit-backdrop-filter`
  à côté de `backdrop-filter` (Safari).
- `@media (prefers-contrast: more)` : halos retirés (fond **et** héros), bordures en `--line-strong`, `--ink-3` remplacé par
  `--ink-2`, pas de lueur.
- `@media (forced-colors: active)` : fonds et dégradés ignorés par le navigateur ; on garde des bordures
  `1px solid CanvasText` sur cartes et boutons, l'onglet actif en `Highlight`, le focus en `outline` (jamais
  seulement `box-shadow`).
- Cibles ≥ 44 px au téléphone (onglets ≥ 48) : pastilles, `.btn.sm`, sommaire, cases et segments compris ; au
  bureau (pointeur fin) les petites variantes gardent 36 px (WCAG 2.5.8 : ≥ 24). Le focus suit l'arrondi de
  l'élément (pas de `border-radius` imposé par `:focus-visible`), montants jamais distingués par la couleur
  seule (signe + / −, icône de tendance), `aria-live` existant conservé.
- 390 px : aucun débordement horizontal — île d'onglets en `grid-template-columns: repeat(5, minmax(0, 1fr))`,
  halos en `position: fixed` dans un calque qui ne peut pas élargir la page (`inset: 0`, `overflow: hidden`).
- CSP stricte : aucun `<style>` ni `style=""` statique ajouté (les seules variables posées en `style` restent
  celles qui portent une donnée : `--i`, `--level`, largeurs de jauge) ; pas de police ni d'image externe :
  halos en dégradés CSS, illustrations et anneaux en SVG inline.
- Performance : `backdrop-filter` seulement sur ce qui est fixe ou collant (rail ou île, toast, assistant,
  modale) ; jamais sur une ligne de liste, une pastille ou une carte ordinaire ; `will-change` interdit en
  permanence.

### 3.9 Ordre de réalisation

**Fondations** (jetons et surfaces globales — sans toucher au JSX sauf mention)

1. Jetons clair et sombre (trois blocs), alias `--r-s/m/l`, `--ink-3` relevé, `theme-color` dans `theme.ts`.
2. Fond : calque `body::before` à quatre halos (deux au téléphone).
3. Surfaces : `.card` / `.panel` / `.chart-wrap` en `--glass-strong`, `.card-hero` en `--glass` + flou,
   `.card-quiet`, listes sans flou.
4. Boutons en pilule : `.btn`, `.quiet`, `.ghost`, `.danger`, `.link`, nouveau `.coin`, `.is-loading` ; focus
   double anneau.
5. Champs, recherche, `.segmented`, `.hub-tabs`, `.chip` / `.pill` / `.tag` / `.chip-count`, `.notice`.
6. Rail en verre détaché ; barre d'onglets en île flottante avec bulle active et pastilles de compte (JSX
   `App.tsx` : réutiliser `badges` du rail).
7. Toasts en pilule de verre + variantes (`useToast(msg, kind?)` dans `ui.tsx`, compatible).
8. Replis : transparence réduite, sans `backdrop-filter`, contraste renforcé, couleurs forcées, mouvement
   réduit ; vérification 390 px clair/sombre et suite Vitest.

*Fondations réalisées (octobre 2026).* Écarts assumés par rapport au § 3.4 :
- `.hub-tabs` : piste en verre fort (une piste `--surface-2` disparaît sur le fond lavande) et onglet actif en
  `--accent-grad`, comme la bulle de l'île ; `.segmented` aussi depuis la relecture (le verre enfoncé était
  illisible en sombre).
- La bulle de l'île glisse par `left` (un seul petit élément) : `transform` en pourcentage se rapporte à la bulle,
  pas à la barre. L'index vient de `data-active` posé par `App.tsx`.
- Le toast neutre porte une petite étincelle ; `useToast(msg, kind?)` accepte `"success" | "coin" | "error"`.
- Les pastilles de compte du rail et de l'île viennent d'un seul appel (`useNavBadges`).
- Les cartes à liseré gauche coloré (missions, bandeaux) gardent leur liseré, courbé par le rayon de 22 px.

**Expérience** (pages, plaisir et clarté)

1. Aujourd'hui : salutation, halo du héros selon l'état, verdict en pastille avec « Que faire ? », carte « La
   prochaine chose à faire ».
2. Système de fête : utilitaire `celebrate(kind)` + classes CSS (pièces, coche, étincelle, pop), branché sur les
   sept déclencheurs du § 3.6 et rien d'autre.
3. Gagner : tirelire en héros soleil, missions en cartes avec pièce lumineuse, jauge de chance à trois crans.
4. Opérations : anneau « classé ce mois », mode tri rapide, état « Tout est rangé ».
5. Papiers : zone de dépôt lumineuse et collection dans Documents ; cartes de démarche avec barre d'étapes et
   « Prochaine étape », colonnes → filtres au téléphone.
6. Une seule alerte dépliée par page (regroupement des `.notice.warn`), formulée en prochaine étape.
7. États vides illustrés (SVG inline) et encourageants.
8. Plus, Réglages, Connecteurs : tuiles colorées, groupes en cartes, pastille « relié » qui s'allume ;
   Épargne, Aides, Crypto, Pilote : phrase « ce que ça veut dire pour toi » et détails repliés.

*Expérience, premier passage (octobre 2026).* Fait : salutation et halo d'humeur du héros, verdict en pastille
avec « Que faire ? » (vers « Je peux m'acheter… ? ») quand le mois se resserre, titre visible « La prochaine chose
à faire » ; `useCelebrate()` (ui.tsx) + `Celebration.tsx` (gerbe CSS de 10 pièces ou étincelles derrière le toast,
halo du fond 1,2 s, `aria-hidden`, le toast `aria-live` dit le texte ; retirée en mouvement réduit et en couleurs
forcées), branchée sur : gain confirmé, résultat « obtenu » avec montant, dossier validé, document vérifié,
dernière opération « à classer » de la page rangée, banque / messagerie / agenda / boîte mail reliés. Palier de
tirelire : badge en « pop » (sans second toast, pour ne pas couvrir le gain) et « Palier de X atteint » annoncé
par sa propre région `role="status"`. « Tout est rangé » ne se fête que sans filtre et quand le serveur ne compte
plus rien à classer dans le mois de l'anneau ; « Banque connectée » une seule fois (le paramètre de retour est
retiré de l'adresse). La couche de fête est démontée 1,3 s après, sans attendre la fin du toast. Anneau `Ring.tsx` (« classé ce mois »
dans Opérations, « N papiers rangés » dans Documents), barre d'étapes sur les cartes de démarche, tirelire en
héros soleil, pièce lumineuse sur les récompenses, chance en trois crans, zone de dépôt lumineuse, états vides
illustrés (`EmptyState.tsx`), action principale pleine largeur au téléphone. Pas d'objectif d'épargne « atteint »
côté serveur : rien n'est fêté là. Reste : mode tri rapide, filtres en pastilles des colonnes de démarches, une seule
alerte dépliée par page, phrases « ce que ça veut dire pour toi », tuiles colorées de Plus.

### 3.10 Démarrer avec Bank

Retour de Micka (#44) : « l'interface est compliquée, il n'y a pas d'onboarding qui te guide sur l'utilisation et la
configuration de tout, avec un beau design ». Réponse retenue : un **parcours de première ouverture plein écran**
(on le quitte et on le reprend où on l'a laissé) puis une **liste « Bien démarrer »** avec son anneau sur
Aujourd'hui, jusqu'à ce que tout soit configuré. Le § 5 (ancien parcours) est remplacé par celui-ci.

#### Constat (code et captures à 390 px, base de démo, octobre 2026)

Mesures prises sur la base de démo (hauteur de page ÷ 844 px d'écran de téléphone) :

| Écran | Hauteur | Ce qui pèse |
|---|---|---|
| Accueil, étape « Connecte ta banque » | 3,6 écrans | Assistant Enable Banking en 6 sous-étapes affiché d'emblée (« Crée ton application », fichier `.pem`) avant même de savoir s'il existe un chemin plus simple ; **deux** boutons principaux (« J'y suis, étape suivante » et « Continuer sans connexion ») plus « Je connecterai mes comptes plus tard » en bas, qui fait la même chose que le second |
| Accueil, étape « Qu'est-ce qui compte le plus pour toi ? » | **7,7 écrans** | Les 17 accords de `PURPOSES` empilés avec leur texte intégral (mémoire de l'assistant, courbe Linky, messageries…) alors que ni l'assistant, ni Linky, ni une messagerie ne sont reliés : on demande un accord sans contexte, ce qui pousse à tout refuser ou à tout accepter sans lire |
| Réglages | 20 écrans, 29 champs | Un champ de solde éditable par compte, Confidentialité = la même liste de 17 interrupteurs |
| Connecteurs | 10,7 écrans, 19 boutons | Neuf sections empilées sans sommaire ni état « relié / à faire » ; l'assistant Enable Banking s'ouvre en tête |
| Ma situation | 8,7 écrans, 55 boutons | Le questionnaire complet (8 thèmes) est le seul endroit où dire son foyer |
| Aujourd'hui | 2,3 écrans | Les initiatives de mise en route (`start:data`, `security:totp`, `planning:connect`) se mêlent aux vraies nouvelles : « 19 autres — voir » |
| Plus | 1,3 écran | Descriptions tronquées par l'ellipse (« et reçois ses… », « documents de l'État,… ») |

Et dans le code : l'état « onboarding en cours » vit dans `localStorage` (`bank:onboarding`) — faux sur un second
appareil, perdu en navigation privée ; `Welcome.tsx` (trois chemins) n'apparaît que si le budget est vide, donc
jamais après l'étape banque ; `Onboarding.tsx` pose des `style={{…}}` statiques (règle CSP du § 3.8).

#### Principes du parcours

1. **Une étape = un but, une action principale.** Une phrase de but, un écran, un bouton lumineux qui dit ce
   qu'il fait ; « Passer » toujours visible en discret, jamais culpabilisant. (Charge cognitive : un seul appel à
   l'action par point de décision.)
2. **Le plus utile d'abord, le reste plus tard.** Sept étapes au plus avant le tableau de bord ; tout ce qui
   est avancé (Powens, crypto, pilote, boîte mail, Linky, planning, assistant IA) va dans la liste, pas dans le
   parcours.
3. **Ne jamais redemander ce que Bank sait.** Les réponses déjà détectées (`suggestions` du questionnaire : salaire,
   loyer, statut) sont pré-remplies et se valident d'un geste ; une étape déjà faite (banque déjà reliée sur un
   autre appareil) est cochée et sautée.
4. **Vrai partout.** Fait / passé / terminé se calculent **côté serveur** à partir de l'état réel, pas d'un drapeau
   local : la liste dit la même chose sur le téléphone et sur l'ordinateur.
5. **Un accord se donne en connaissance de cause.** On montre ce qui part et vers qui, au moment où ça sert ;
   jamais d'accord coché d'avance, jamais d'accord accordé en silence (§ « Tes accords » ci-dessous).
6. **Honnête.** Les gains affichés viennent du serveur (`/api/opportunities/top`), jamais d'un chiffre inventé ;
   la durée annoncée est la durée réelle (l'application Enable Banking prend cinq minutes, on le dit).

#### (a) Le parcours

Entrée : juste après la création de l'accès (l'intro de trois écrans et le formulaire d'accès restent tels
quels). Il s'ouvre **seul une seule fois** (état `new`) ; dès qu'on le quitte, il ne s'impose plus et se reprend
depuis la liste (« Reprendre le parcours »). Adresse : `#/bienvenue`, `#/bienvenue/<étape>` (reprise et lien
profond).

Gabarit commun (plein écran, sans rail ni île) : en haut la marque, « Étape 2 sur 7 » en texte et une barre de
sept segments, un bouton « Quitter » (retour à Aujourd'hui, l'étape en cours est gardée) ; au centre une carte de
verre (`.card`) avec le titre de l'étape (`h1`, reçoit le focus), une phrase de but, le contenu, puis l'action
principale pleine largeur au téléphone ; dessous, en `.btn.ghost`, « Passer cette étape ». Une coche verte se
trace sur le segment quand le serveur confirme l'étape (2xx), sans gerbe : la fête est réservée à la fin.

| # | Étape | But (une phrase) | Ce qu'on voit | Action principale | Passer | « Fait » calculé côté serveur | Durée | Pourquoi ça compte |
|---|---|---|---|---|---|---|---|---|
| 1 | **Ta banque** | Que Bank connaisse ton argent sans que tu saisisses rien. | Trois chemins en cartes (repris de `Welcome.tsx`) : « Relier ma banque » (recommandé · 5 min une fois), « Importer un relevé » (3 min), « Trois chiffres » (30 s). L'assistant Enable Banking (`BankSetup`) ne s'ouvre qu'après le choix ; s'il est déjà configuré, on va droit aux tuiles de banques. Retour de la banque : synchronisation visible (existante) puis comptes reliés | Le chemin choisi, puis « Relier ma banque » | « Plus tard » | Une `BankConnection` active **ou** une opération importée **ou** un compte saisi avec un solde | 30 s à 5 min | Sans ça, pas de chiffre du jour ; relié, il n'y a plus jamais de relevé à pointer à la main |
| 2 | **Ton foyer** | Que Bank sache qui vit de ce budget, pour simuler tes droits. | Cinq questions du questionnaire, une par ligne, pré-remplies quand Bank les a lues dans tes relevés (« Lu dans tes relevés : 2 150 € — c'est juste ? ») : situation pro, revenu net, foyer, enfants à charge, logement (statut + loyer) ; puis, en option, ta commune ou ton adresse (voir l'accord « géocodage » en ligne, décoché) | « Enregistrer mon foyer » | « Plus tard » | Les réponses `employment`, `net_monthly_income`, `household`, `dependents`, `housing_status` existent (même endpoint `PUT /api/onboarding/{question}`) | 1 min | C'est ce qui permet de simuler prime d'activité, aides au logement, impôt : un droit non demandé, c'est de l'argent perdu chaque mois. Le montant estimé s'affiche s'il est calculé |
| 3 | **Tes accords** | Choisir ce que Bank peut aller chercher pour toi, en sachant ce qui part. | Les accords **utiles maintenant**, groupés par ce qu'ils envoient (tableau suivant), chacun avec une ligne « Envoie : … » et le texte complet dépliable ; « Tout activer pour les données publiques » en action principale ; interrupteurs individuels à côté | « Tout activer pour les données publiques » | « Pas maintenant » | Chaque accord listé a été **décidé** (`consents.decided`), oui ou non | 1 min | Barèmes à jour, jours fériés, Tempo : des prévisions justes sans rien saisir |
| 4 | **Ton premier papier** | Ranger un premier document que Bank lit pour toi. | La zone de dépôt lumineuse de Documents (photo ou PDF), avec trois idées : avis d'impôt, bulletin de paie, attestation CAF | « Prendre en photo ou déposer » | « Plus tard » | Au moins un `VaultDocument` | 1 min | Un avis d'impôt suffit à préremplir revenu fiscal, parts, échéances : moins de questions ensuite |
| 5 | **Tes priorités** | Dire par quoi Bank commence à chercher. | Les cinq pastilles actuelles (« Récupérer ce à quoi j'ai droit »…) ; dessous, les trois premières pistes du serveur | « Garder ces priorités » | « Plus tard » | `opportunity_priorities` non vide | 20 s | Les pistes qui comptent pour toi remontent en premier dans Gagner |
| 6 | **Où te prévenir** | Recevoir les alertes importantes (découvert, échéance) là où tu es. | Deux cartes : « Sur ce téléphone » (notifications : l'accord `push` s'affiche ici, avec son texte) et « Dans ta messagerie » (WhatsApp par QR code, Telegram… : ouvre Messageries) | « Activer les notifications » | « Plus tard » | Un appareil `push` actif **ou** un `ChannelLink` | 1 min | Un découvert vu trois jours avant coûte 0 € ; vu après, des frais et un rejet |
| 7 | **La visite** | Savoir où est quoi, en cinq écrans. | Voir (c) | « Suivant », puis « Voir mon tableau de bord » | « Passer la visite » | `tour_done_at` posé | 40 s | On ne cherche plus Papiers dans Plus |

Les étapes déjà faites quand on arrive (banque reliée ailleurs, foyer répondu dans Ma situation) apparaissent
cochées dans la barre et sont sautées. La dernière étape franchie déclenche **le** moment de fête du parcours
(gerbe `done` existante, toast `.is-success` « Bank est prêt. ») — uniquement si au moins une étape a été faite
pendant ce passage (rien à fêter si tout a été passé).

**Tes accords, groupés par ce qu'ils envoient.** Seuls les groupes 1 et 2 sont proposés dans le parcours ; les
autres se demandent **là où ils servent**, comme aujourd'hui (Messageries, notifications, boîte mail, crypto,
Linky, documents) : on ne demande pas « Mémoire de l'assistant » avant qu'il y ait un assistant.

| Groupe (titre affiché) | Accords | Ce qui part (ligne « Envoie : ») | Dans « Tout activer pour les données publiques » |
|---|---|---|---|
| Rien te concernant | `watch` (déjà actif par défaut), `baremes`, `tempo` | Seulement l'adresse de ton serveur ; des dates pour Tempo | Oui |
| Ta zone, pas ton adresse | `calendar` (zone, département ou académie), `home_value` (commune), `meteo` (coordonnées arrondies à ~10 km, si ton adresse est connue) | La zone, la commune ou la maille de 10 km | Oui |
| Ton adresse | `geocoding` (adresse tapée → IGN), `housing` (identifiant d'adresse BAN → ADEME) | Ton adresse, ou celle de ton immeuble | Non : `geocoding` à côté du champ adresse (étape 2), `housing` dans Ma situation › logement |
| Tes contenus, vers un service tiers | `ai_memory`, `ai_documents`, `web_search`, `channels`, `push`, `mail`, `crypto_network`, `linky_curve`, `sensitive_documents` | Ce que tu écris, tes documents, tes adresses crypto, ta courbe de charge… | Non : demandés au moment où tu relies le service |

« Tout activer pour les données publiques » : le bouton est **sous** la liste des six accords concernés, chacun
avec sa ligne « Envoie : … » et son texte complet dépliable ; un clic les accorde **tous ceux-là et eux seuls**
(le serveur refuse toute autre finalité dans cet appel), chacun journalisé comme un accord ordinaire
(`consent.grant`, version du texte). Le toast dit « 6 accords donnés — modifiables dans Réglages › Confidentialité ».
Les interrupteurs individuels restent à côté pour qui veut choisir. Rien n'est coché d'avance.

#### (b) La liste « Bien démarrer »

Place : sur Aujourd'hui, **sous** le héros (le chiffre du jour reste le premier contenu) et au-dessus de « La
prochaine chose à faire ». Carte de verre `.card` avec, à gauche, l'anneau `Ring` (48 px au téléphone, 64 au
bureau) « 5 sur 8 », titre `h2` « Bien démarrer », et **les trois prochains éléments** non faits ; « Voir les 3
autres » déplie le reste. Chaque élément : icône, titre court, une phrase « pourquoi » (gain ou temps), durée
(« 2 min »), et un lien qui va **directement** au bon endroit (étape du parcours ou section d'une page, ex.
`#/connecteurs` + ancre `energie`). Un élément fait passe en bas, coché, barré non (texte `--ink-2`, coche
`--positive`). En tête de carte : « Reprendre le parcours » tant que le parcours n'est pas `finished`.

Éléments comptés par l'anneau (ce qui sert à tout le monde), dans cet ordre :

1. Relier ta banque (ou importer) — étape 1
2. Décrire ton foyer — étape 2
3. Tes accords pour les données publiques — étape 3
4. Ranger un premier papier — étape 4
5. Choisir tes priorités — étape 5
6. Être prévenu (notifications ou messagerie) — étape 6
7. Protéger ton accès (double authentification **ou** une clé d'accès) — `User.totp_enabled` ou un `Passkey`
8. Vérifier ta sauvegarde — dernière `BackupRun` de type `verify` en `ok` (la sauvegarde automatique existe déjà ;
   la vérifier prouve qu'elle se restaure)

« Pour aller plus loin » (repli `<details>` fermé, **non compté** dans l'anneau, pour que 100 % soit atteignable
par tout le monde) : assistant IA (clé OpenRouter, `LlmProvider`), Linky (`EnergyConnection`), boîte mail
(`MailAccount`), planning de travail (`WorkPlan` ; montré en premier si la situation pro est à horaires variables),
banque via Powens (si aucune banque Enable Banking), crypto (si `outside_assets` mentionne de la crypto, sinon en
dernier), pilote automatique (proposé seulement quand banque + foyer + priorités sont faits).

Cycle de vie :
- **Visible** tant qu'au moins un élément compté n'est pas fait et que tu ne l'as pas masquée.
- **Terminée** : quand le serveur constate 8 sur 8 pour la première fois, il pose `checklist_completed_at`. Si
  c'est la conséquence d'une action faite dans la liste ou le parcours, l'anneau se ferme avec l'étincelle du
  § 3.6 et le toast « Bank est prêt. » ; jamais au simple chargement de la page.
- **Repliée** : pendant 7 jours après `checklist_completed_at`, la carte devient une pilule d'une ligne
  « Bien démarrer : terminé · Pour aller plus loin » (ouvre le repli).
- **Disparue pour de bon** : après ces 7 jours, ou dès « Masquer » (bouton discret, confirmation intégrée « Tu
  la retrouveras dans Réglages »), `dismissed_at` est posé. La liste reste accessible dans Réglages › « Bien
  démarrer » (ligne avec l'anneau), jamais plus sur Aujourd'hui.
- Un élément qui redevient faux (banque coupée) ne rouvre pas la liste masquée : c'est le rôle des initiatives
  (« Ta banque n'est plus reliée »).

#### (c) La visite, cinq écrans

Une carte par écran, l'île d'onglets dessinée en SVG inline au bas de la carte avec l'onglet concerné allumé
(bulle `--accent-grad`), un petit aperçu en CSS au-dessus (pas de capture : rien d'externe, rien de faux).
« Suivant » / « Précédent », points d'étape, « Passer la visite ». Pas d'avance automatique.

| Écran | Phrase |
|---|---|
| Jour | « Chaque matin, ce que tu peux dépenser aujourd'hui, et la prochaine chose à faire. » |
| Opérations | « Tout ce qui entre et sort, rangé pour toi ; tu corriges d'un geste. » |
| Gagner | « Les aides et économies que Bank a trouvées pour toi, et ta tirelire. » |
| Papiers | « Prends un papier en photo : Bank le lit, le range et prépare tes démarches. » |
| Plus | « Épargne, réglages, connecteurs et messageries : tout le reste est ici. » |

Le bouton flottant « Demande à Bank » est montré sur le dernier écran (« Une question ? Demande à Bank, en bas à
droite. ») sans sixième écran.

#### (d) API et données

`GET /api/onboarding` est **déjà pris** par le questionnaire de Ma situation (`api/agents.py`, utilisé par
Situation et les initiatives) : le parcours vit sous **`/api/start`** pour ne rien casser.

- `GET /api/start` →
  ```
  { "journey": { "status": "new" | "in_progress" | "finished", "current": "foyer" | null,
                 "steps": [{ "key": "banque", "title": "Ta banque", "done": true, "skipped": false,
                             "minutes": 5, "href": "#/bienvenue/banque" }, …] },
    "checklist": { "visible": true, "collapsed": false, "done": 5, "total": 8,
                   "completed_at": null,
                   "items": [{ "key": "securite", "title": "Protéger ton accès", "why": "…", "minutes": 2,
                               "done": false, "group": "essentiel" | "plus", "href": "#/reglages#sec-title",
                               "value_cents": null }, …] },
    "public_consents": [{ "purpose": "baremes", "title": "…", "sends": "…", "text": "…", "granted": false,
                          "decided": false }, …] }
  ```
  Tout `done` est recalculé à chaque appel à partir des tables réelles (requêtes `count`/`exists` légères, aucune
  lecture réseau) ; `value_cents` ne vient que d'une estimation déjà calculée par le serveur.
- `POST /api/start/steps/{key}` `{ "action": "skip" | "resume" | "current" }` → l'état ; `skip` sur une étape
  faite est sans effet.
- `POST /api/start/finish` → `status = finished` (fin de la visite ou « Voir mon tableau de bord »).
- `POST /api/start/checklist` `{ "action": "dismiss" | "restore" }`.
- `POST /api/start/public-consents` `{ "purposes": [...] }` → refuse (422) toute finalité hors du groupe public ;
  appelle `consents.set_consent(…, True, actor="user")` pour chacune, renvoie la liste à jour.
- Toutes en session humaine (`AuthDep`) avec CSRF, jamais exposées aux jetons d'agent ni au serveur MCP.

Données : **aucune table, aucune colonne**. Une clé `start_journey` dans `app_settings` (déjà là, JSON), lue et
écrite par `services/start.py` :
`{ "started_at", "current", "skipped": [clés], "finished_at", "tour_done_at", "checklist_completed_at",
"dismissed_at" }` — des dates et des clés d'étape, **jamais** une réponse, un montant, une adresse ou un secret.
Compte existant sans cette clé : si une banque ou des opérations existent déjà, le parcours est considéré
`finished` (on n'impose pas l'accueil à Micka) mais la liste s'affiche avec ce qui manque. Le drapeau
`localStorage` `bank:onboarding` disparaît : `App.tsx` lit `GET /api/start` après la connexion et n'ouvre
`#/bienvenue` que si `status == "new"`. Les initiatives de mise en route (`start:data`, `security:totp`,
`planning:connect`) sont tues tant que la liste est visible, pour ne pas poser deux fois la même question.

Tests : un test par règle « fait » (table vide → faux, une ligne → vrai), skip/resume/finish, migration d'un compte
existant, refus d'une finalité non publique dans `public-consents`, journal d'audit écrit ; côté front, focus sur le
`h1` de chaque étape, reprise à l'étape `current`, la liste masquée ne revient pas.

#### (e) Accessibilité et mouvement

- Chaque changement d'étape déplace le focus sur le `h1` de l'étape (`tabIndex={-1}`) et met à jour
  `document.title` (« Ton foyer · Bank ») ; la barre de progression est un `<ol>` avec `aria-current="step"` et un
  texte « Étape 2 sur 7 » ; une région `aria-live="polite"` annonce « Étape faite » / « Étape passée ».
- L'anneau garde `role="img"` + `aria-label` (« 5 éléments sur 8 faits ») ; l'élément « fait » est dit par le
  texte (« fait »), pas par la seule coche verte.
- Interrupteurs d'accords : `role="switch"` existant, nom = titre de l'accord, état dit ; le texte complet est dans
  un `<details>` relié par `aria-describedby` à la ligne « Envoie : … ».
- Cibles ≥ 44 px, action principale pleine largeur au téléphone, « Passer » et « Quitter » toujours atteignables au
  clavier, Échap = « Quitter » (avec la même conservation d'état). Aucune limite de temps : la visite n'avance
  jamais seule (l'intro actuelle à 3,6 s par écran garde son « Suivant » et s'arrête sur le dernier écran).
- Mouvement : entrée d'étape en `opacity` + `translateY(8px)` 200 ms `--ease-out`, coche qui se trace 400 ms,
  anneau qui avance 320 ms ; tout est coupé par `prefers-reduced-motion` (état final directement) ; la seule fête
  est celle de fin (§ 3.6), sous une seconde.
- Verre et contrastes du § 3.3 : texte des étapes sur `.card` (`--glass-strong`), jamais `--ink-3` sur le fond nu ;
  replis transparence réduite / contraste renforcé / couleurs forcées hérités des primitives.
- CSP : aucun `style` statique (les marges en ligne d'`Onboarding.tsx` deviennent des classes), aperçus de la
  visite en CSS et SVG inline ; section `/* ---- démarrer */` ajoutée à `styles.css` avec les jetons existants.

#### Cinq simplifications bon marché, incluses

| # | Où | Changement | Effort | Gain |
|---|---|---|---|---|
| 1 | Accueil › accords (7,7 écrans) et Réglages › Confidentialité | Un seul composant `ConsentGroups` : groupes par ce qui part, ligne « Envoie : … », texte complet replié ; le parcours n'en montre que deux groupes, Réglages tous | Faible (le texte est déjà dans `PURPOSES`, on ajoute `group` et `sends`) | La page la plus longue de l'accueil tombe à ~1 écran ; un accord se comprend en une ligne |
| 2 | Accueil › banque | Trois chemins d'abord, l'assistant Enable Banking après le choix ; **un** bouton principal par écran ; « Continuer sans connexion » et « Je connecterai mes comptes plus tard » fusionnés en « Plus tard » | Faible (réutilise `Welcome.tsx` et `BankSetup`) | Plus de mur technique en première impression ; 30 s possibles au lieu de 5 min |
| 3 | Connecteurs (10,7 écrans) | `SectionNav` en tête (déjà utilisé par Réglages) avec une pastille par section « relié » (`--positive`) / « à faire » ; les sections non reliées repliées sur une ligne « Relier » | Faible à moyen | On voit d'un coup d'œil ce qui est branché ; la page se lit en un écran |
| 4 | Aujourd'hui | Initiatives de mise en route tues tant que « Bien démarrer » est visible ; « 19 autres — voir » devient « Voir les 19 autres » | Faible | Une seule liste de choses à configurer, le bouton dit ce qu'il fait |
| 5 | Plus | Descriptions de tuiles en six mots au plus (« Écris à Bank sur WhatsApp, Signal… », « Banques, Linky, État, IA ») ; pas d'ellipse au téléphone | Faible (texte seul) | La tuile se lit entière, sans deviner la fin |

Hors de ce lot (déjà prévus ou plus gros) : mode tri rapide d'Opérations (16,5 écrans, 103 listes), champs de
solde de Réglages › Comptes en lecture avec « Modifier », Ma situation allégée maintenant que le foyer se dit dans
le parcours.

## 4. Architecture des composants

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

## 5. Parcours de première ouverture

*Remplacé par le § 3.10 « Démarrer avec Bank » (#44) ; gardé comme historique.*

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

## 6. Graphiques

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

## 7. Qualité

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
