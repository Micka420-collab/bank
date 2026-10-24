// Écritures et garde-fous partagés de l'écran Crypto (quantités, cours, secrets collés, réseau deviné) : un module à
// part pour que les composants (plateformes reliées…) n'importent pas la page entière.

/** Écriture française d'une quantité exacte (texte décimal du serveur) : « 0,00012345 », « 1 250,5 ».
 * Pour la lecture, les décimales sont bornées selon la taille (8 sous 1, 6 jusqu'à 1 000, 2 au-delà) ; la
 * valeur exacte reste dans l'infobulle. */
export function qty(text: string, exact = false): string {
  const [int = "0", dec = ""] = text.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f");
  const keep = exact ? dec.length : int.length >= 4 ? 2 : int !== "0" ? 6 : 8;
  const shown = dec.slice(0, keep).replace(/0+$/, "");
  return shown ? `${grouped},${shown}` : grouped;
}

export function price(eur: string | null): string {
  if (eur === null) return "—";
  const n = Number(eur);
  return n.toLocaleString("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: n < 1 ? 4 : 2, minimumFractionDigits: n < 1 ? 2 : 2 });
}

/** Ce qui ressemble à un secret : on l'arrête dans le navigateur, il ne part même pas au serveur.
 * `words` : dans un champ « adresse » ou « nom », douze mots ou plus ne peuvent être qu'une phrase de récupération
 * (dans un message à vérifier, c'est le serveur qui tranche avec les listes BIP39). */
export function looksSecret(text: string, { words = true }: { words?: boolean } = {}): string | null {
  const t = text.trim();
  if (/\b[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,}/.test(t)) return "une clé privée étendue (xprv)";
  if (/^(0x)?[0-9a-fA-F]{64}$/.test(t)) return "une clé privée";
  if (/^[5KL][1-9A-HJ-NP-Za-km-z]{50,51}$/.test(t)) return "une clé privée (WIF)";
  if (/^\[\s*(\d{1,3}\s*,\s*){63}\d{1,3}\s*\]$/.test(t)) return "une clé privée";
  if (words && (t.match(/\p{L}{3,}/gu) ?? []).length >= 12) return "une phrase de récupération";
  return null;
}

/** Réseau probable d'une adresse collée : pour préremplir, jamais pour décider (le serveur vérifie la somme). */
export function guessChain(address: string): string | null {
  const a = address.trim();
  if (/^0x[0-9a-fA-F]{40}$/.test(a)) return "ethereum";
  if (/^(bc1|BC1)[0-9a-zA-Z]{11,71}$/.test(a) || /^[13][1-9A-HJ-NP-Za-km-z]{25,34}$/.test(a)) return "bitcoin";
  if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) return "solana";
  return null;
}
