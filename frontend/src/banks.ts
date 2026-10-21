// Connexion rapide : les enseignes françaises courantes, reliées aux noms exacts (ASPSP) d'Enable Banking.
// Les banques régionales (Crédit Agricole, Caisse d'Épargne, Banque Populaire) ont une entrée par caisse :
// la tuile regroupe, puis tu choisis ta caisse.

// `color` : la couleur dominante de l'enseigne, pour la pastille (les logos officiels sont des marques déposées :
// déposer `public/banks/<id>.svg` affiche le logo à la place de la pastille).
export type BankBrand = { id: string; name: string; patterns: RegExp[]; regional?: boolean; color: string; mark?: string };

export const BANK_BRANDS: BankBrand[] = [
  { id: "ca", name: "Crédit Agricole", patterns: [/credit agricole/, /\bca\b/], regional: true, color: "#006a4e", mark: "CA" },
  { id: "bnp", name: "BNP Paribas", patterns: [/bnp/], color: "#00915a", mark: "BNP" },
  { id: "sg", name: "Société Générale", patterns: [/societe generale/, /\bsg\b/], color: "#e60028", mark: "SG" },
  { id: "ce", name: "Caisse d'Épargne", patterns: [/caisse d'?epargne/], regional: true, color: "#c8102e", mark: "CE" },
  { id: "bp", name: "Banque Populaire", patterns: [/banque populaire/, /\bbred\b/], regional: true, color: "#004c97", mark: "BP" },
  { id: "cm", name: "Crédit Mutuel", patterns: [/credit mutuel/, /\bcmb\b/, /\bcmso\b/], regional: true, color: "#0a3d73", mark: "CM" },
  { id: "cic", name: "CIC", patterns: [/\bcic\b/], color: "#0e6eb8", mark: "CIC" },
  { id: "lcl", name: "LCL", patterns: [/\blcl\b/, /credit lyonnais/], color: "#0052a5", mark: "LCL" },
  { id: "lbp", name: "La Banque Postale", patterns: [/banque postale/], color: "#1c3f94", mark: "LBP" },
  { id: "bourso", name: "BoursoBank", patterns: [/bourso/], color: "#d81b60", mark: "B" },
  { id: "fortuneo", name: "Fortuneo", patterns: [/fortuneo/], color: "#00a0e2", mark: "F" },
  { id: "hello", name: "Hello bank!", patterns: [/hello ?bank/], color: "#1ba3e0", mark: "H!" },
  { id: "revolut", name: "Revolut", patterns: [/revolut/], color: "#191c1f", mark: "R" },
  { id: "n26", name: "N26", patterns: [/\bn26\b/], color: "#36a18b", mark: "N26" },
  { id: "nickel", name: "Nickel", patterns: [/nickel/], color: "#f26522", mark: "N" },
  { id: "monabanq", name: "Monabanq", patterns: [/monabanq/], color: "#6ab023", mark: "M" },
  { id: "axa", name: "AXA Banque", patterns: [/\baxa\b/], color: "#00008f", mark: "AXA" },
  { id: "coop", name: "Crédit Coopératif", patterns: [/credit cooperatif/], color: "#e4032e", mark: "CC" },
  { id: "bforbank", name: "BforBank", patterns: [/bforbank/], color: "#00b5cc", mark: "Bf" },
  { id: "qonto", name: "Qonto", patterns: [/qonto/], color: "#2b2d42", mark: "Q" },
  { id: "lydia", name: "Lydia / Sumeria", patterns: [/lydia/, /sumeria/], color: "#5b6bff", mark: "L" },
];

/** Couleur stable (teinte dérivée du nom) pour un établissement hors de la liste : la pastille reste lisible. */
export function colorFor(name: string): string {
  const brand = brandOf(name);
  if (brand) return brand.color;
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 38%)`;
}

export function normalizeName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[’`]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

/** Noms ASPSP correspondant à une enseigne (toutes ses caisses régionales le cas échéant). */
export function matchBrand(brand: BankBrand, aspspNames: string[]): string[] {
  return aspspNames.filter((n) => brand.patterns.some((p) => p.test(normalizeName(n)))).sort((a, b) => a.localeCompare(b, "fr"));
}

/** Enseigne « probable » d'un nom ASPSP, pour afficher un logo-lettre cohérent. */
export function brandOf(aspspName: string): BankBrand | undefined {
  const n = normalizeName(aspspName);
  return BANK_BRANDS.find((b) => b.patterns.some((p) => p.test(n)));
}

export function searchAspsps(query: string, aspspNames: string[], limit = 12): string[] {
  const q = normalizeName(query);
  if (q.length < 2) return [];
  return aspspNames
    .filter((n) => normalizeName(n).includes(q))
    .sort((a, b) => normalizeName(a).indexOf(q) - normalizeName(b).indexOf(q) || a.localeCompare(b, "fr"))
    .slice(0, limit);
}

/** Libellé d'une caisse sans le nom de l'enseigne : « Crédit Agricole Alpes Provence » → « Alpes Provence ». */
export function caisseLabel(brand: BankBrand, aspspName: string): string {
  const brandWords = new Set(normalizeName(brand.name).split(" "));
  const kept = aspspName.split(/\s+/).filter((w) => !brandWords.has(normalizeName(w)));
  const label = kept.join(" ").replace(/^[\s\-–—:]+|[\s\-–—:]+$/g, "");
  return label || aspspName;
}

export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}
