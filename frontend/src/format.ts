// Formatage français. Les montants circulent en centimes entiers ; on ne convertit qu'à l'affichage.

const eur = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const eurWhole = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const plain = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });

export function money(cents: number, opts: { whole?: boolean; signed?: boolean } = {}): string {
  const value = cents / 100;
  const text = (opts.whole ? eurWhole : eur).format(value);
  return opts.signed && cents > 0 ? `+${text}` : text;
}

/** Nombre seul, sans symbole (pour les gros chiffres où « € » est composé à part). */
export function amountOnly(cents: number): string {
  return plain.format(Math.round(cents / 100));
}

/** « 12,50 », « 1 234 », « -3.5 » -> centimes. null si illisible. */
export function parseEuros(input: string): number | null {
  const s = input.replace(/[\s  €]/g, "").replace(",", ".");
  if (!/^-?\d+(\.\d{0,2})?$/.test(s)) return null;
  return Math.round(Number(s) * 100);
}

function asDate(iso: string): Date {
  // Une date ISO « 2026-10-21 » est une date civile : on évite le décalage UTC de new Date(iso).
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

export function dayNumber(iso: string): string {
  return String(asDate(iso).getDate());
}

export function shortDate(iso: string): string {
  // « 27 oct. » -> « 27 oct » : le point d'abréviation se télescope avec la ponctuation des phrases.
  return asDate(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" }).replace(/\.$/, "");
}

export function longDate(iso: string): string {
  return asDate(iso).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

export function monthLabel(key: string): string {
  return asDate(`${key}-01`).toLocaleDateString("fr-FR", { month: "short" }).replace(".", "");
}

export function percent(value: number, digits = 1): string {
  return `${value.toLocaleString("fr-FR", { maximumFractionDigits: digits })} %`;
}

export function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("fr-FR")} ${Math.abs(n) >= 2 ? many : one}`;
}

const PARTICLES = new Set(["de", "du", "des", "la", "le", "les", "et", "au", "aux", "en", "sur", "pour", "par", "a", "à", "d", "l", "da", "di", "del", "von", "van", "of", "the"]);
const ACRONYMS = new Set([
  "edf", "gdf", "caf", "cpam", "msa", "sncf", "ratp", "dgfip", "urssaf", "apl", "rsa", "ppa", "aah", "are", "ass", "bnp", "lcl", "cic",
  "sg", "sfr", "tgv", "ter", "brgm", "tva", "pel", "cel", "lep", "hlm", "rte", "smic", "cnav", "agirc", "arrco", "edf", "eni", "ovh",
  "ikea", "fnac", "cb", "sepa", "iban", "ald", "bpce", "cnp", "axa", "maif", "macif", "gmf", "mgen", "cdc",
]);

/** « CARREFOUR MARKET ORLEANS » → « Carrefour Market Orleans » ; sigles et particules respectés.
 * Un libellé déjà en casse mixte est laissé tel quel : la banque savait ce qu'elle faisait. */
export function merchantCase(name: string): string {
  if (!name || name !== name.toUpperCase()) return name;
  return name
    .split(/(\s+)/)
    .map((word, i) => {
      if (!word.trim()) return word;
      const low = word.toLowerCase();
      if (/\d/.test(word)) return word;
      if (ACRONYMS.has(low)) return word;
      if (i > 0 && PARTICLES.has(low)) return low;
      if (low.length <= 3 && !PARTICLES.has(low)) return word;
      return low.replace(/^(\p{L})/u, (c) => c.toUpperCase()).replace(/([-'’])(\p{L})/gu, (_m, sep: string, c: string) => sep + c.toUpperCase());
    })
    .join("");
}

/** « 13,49 € par mois », « 258 € par an », « 17,90 € » (une fois). */
export function perPeriod(cents: number, period: "once" | "month" | "year"): string {
  const unit = period === "month" ? " par mois" : period === "year" ? " par an" : "";
  return `${money(cents, { whole: cents >= 10_000 })}${unit}`;
}

/** Une adresse web (http ou https) ou rien : défense en profondeur contre un lien javascript: ou data: venu d'un flux. */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https?:\/\/[^\s<>"']+$/i.test(url.trim()) ? url.trim() : null;
}
