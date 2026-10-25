// Teintes de données « Lagon » (--c1…--c6 de styles.css, DESIGN.md § 3) : le serveur envoie une clé (`tone`),
// l'interface dessine la teinte du thème en cours, si bien que le sombre suit. Une couleur choisie par
// l'utilisateur arrive sans clé et reste telle quelle.

export type ChartTone = "c1" | "c2" | "c3" | "c4" | "c5" | "c6" | "muted";

const TONE_VAR: Record<ChartTone, string> = {
  c1: "var(--c1)",
  c2: "var(--c2)",
  c3: "var(--c3)",
  c4: "var(--c4)",
  c5: "var(--c5)",
  c6: "var(--c6)",
  muted: "var(--c-muted)", // ce qui n'est pas classé, « Autres »
};

/** L'ordre de repli : les six teintes, puis deux gris pour une 7e et une 8e part. */
export const PALETTE = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c-muted)", "var(--ink-3)"];

export function toneVar(tone: string | null | undefined): string | null {
  return tone && tone in TONE_VAR ? TONE_VAR[tone as ChartTone] : null;
}

/** Une couleur par part, distinctes autant que possible. Les parts arrivent de la plus grosse à la plus petite :
 *  chacune prend sa teinte stable si elle est libre, sinon la première teinte libre ; une couleur choisie
 *  (sans `tone`) est gardée telle quelle ; une part sans rien prend la suivante de la palette. */
export function sliceColors(items: readonly { tone?: string | null; color?: string | null }[]): string[] {
  const used = new Set<string>();
  const free = () => PALETTE.find((c) => !used.has(c)) ?? PALETTE[used.size % PALETTE.length]!;
  return items.map((it) => {
    const wanted = toneVar(it.tone);
    const c = wanted ? (used.has(wanted) ? free() : wanted) : it.color || free();
    used.add(c);
    return c;
  });
}

/** Les parts d'un donut de catégories, avec la couleur du thème. */
export function toneSlices<T extends { tone?: string | null; color?: string | null }>(items: readonly T[]): (T & { color: string })[] {
  const colors = sliceColors(items);
  return items.map((it, i) => ({ ...it, color: colors[i]! }));
}
