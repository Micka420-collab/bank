// Apparence : clair, sombre, ou comme l'appareil. Le choix reste sur cet appareil (localStorage) et s'applique
// avant le premier rendu pour éviter un flash. Sans stockage disponible, on suit simplement l'appareil.

export type ThemePref = "auto" | "light" | "dark";

const KEY = "bank:theme";

export function readTheme(): ThemePref {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function applyTheme(pref: ThemePref): void {
  const root = document.documentElement;
  if (pref === "auto") delete root.dataset.theme;
  else root.dataset.theme = pref;
  try {
    if (pref === "auto") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, pref);
  } catch {
    /* stockage indisponible : le choix vaut pour cette page seulement */
  }
  // La barre d'état du navigateur suit la couleur de fond effective.
  const dark = pref === "dark" || (pref === "auto" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    meta.content = dark ? "#0c0b1d" : "#f4f3fb";
  }
}

export function initTheme(): void {
  applyTheme(readTheme());
}
