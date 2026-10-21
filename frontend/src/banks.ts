// Connexion rapide : les enseignes françaises courantes, reliées aux noms exacts (ASPSP) d'Enable Banking.
// Les banques régionales (Crédit Agricole, Caisse d'Épargne, Banque Populaire) ont une entrée par caisse :
// la tuile regroupe, puis tu choisis ta caisse.

export type BankBrand = { id: string; name: string; patterns: RegExp[]; regional?: boolean };

export const BANK_BRANDS: BankBrand[] = [
  { id: "ca", name: "Crédit Agricole", patterns: [/credit agricole/, /\bca\b/], regional: true },
  { id: "bnp", name: "BNP Paribas", patterns: [/bnp/] },
  { id: "sg", name: "Société Générale", patterns: [/societe generale/, /\bsg\b/] },
  { id: "ce", name: "Caisse d'Épargne", patterns: [/caisse d'?epargne/], regional: true },
  { id: "bp", name: "Banque Populaire", patterns: [/banque populaire/, /\bbred\b/], regional: true },
  { id: "cm", name: "Crédit Mutuel", patterns: [/credit mutuel/, /\bcmb\b/, /\bcmso\b/], regional: true },
  { id: "cic", name: "CIC", patterns: [/\bcic\b/] },
  { id: "lcl", name: "LCL", patterns: [/\blcl\b/, /credit lyonnais/] },
  { id: "lbp", name: "La Banque Postale", patterns: [/banque postale/] },
  { id: "bourso", name: "BoursoBank", patterns: [/bourso/] },
  { id: "fortuneo", name: "Fortuneo", patterns: [/fortuneo/] },
  { id: "hello", name: "Hello bank!", patterns: [/hello ?bank/] },
  { id: "revolut", name: "Revolut", patterns: [/revolut/] },
  { id: "n26", name: "N26", patterns: [/\bn26\b/] },
  { id: "nickel", name: "Nickel", patterns: [/nickel/] },
  { id: "monabanq", name: "Monabanq", patterns: [/monabanq/] },
  { id: "axa", name: "AXA Banque", patterns: [/\baxa\b/] },
  { id: "coop", name: "Crédit Coopératif", patterns: [/credit cooperatif/] },
  { id: "bforbank", name: "BforBank", patterns: [/bforbank/] },
  { id: "qonto", name: "Qonto", patterns: [/qonto/] },
  { id: "lydia", name: "Lydia / Sumeria", patterns: [/lydia/, /sumeria/] },
];

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

export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}
