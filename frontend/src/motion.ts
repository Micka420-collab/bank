// Mouvement (DESIGN.md § 3.7) : les petites aides partagées par le parcours et « Bien démarrer ».
// Tout part d'une règle : sans préférence de mouvement connue (pas de matchMedia, tests compris) ou avec
// « réduire les animations », on va droit à l'état final. Rien ici n'écrit d'attribut `style`.

import { useEffect, useRef, useState } from "react";

const REDUCE = "(prefers-reduced-motion: reduce)";

/** Vrai quand il faut aller droit à l'état final : mouvement réduit demandé, ou préférence inconnue. */
export function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia(REDUCE).matches;
}

/** Appelle `onReduce` si la personne demande « réduire les animations » pendant qu'une animation tourne. */
function onReducedMotion(onReduce: () => void): () => void {
  const mql = typeof window.matchMedia === "function" ? window.matchMedia(REDUCE) : null;
  if (!mql || typeof mql.addEventListener !== "function") return () => undefined;
  const listener = (e: MediaQueryListEvent) => { if (e.matches) onReduce(); };
  mql.addEventListener("change", listener);
  return () => mql.removeEventListener("change", listener);
}

type Transition = { finished: Promise<void> };
type TransitionDocument = Document & { startViewTransition?: (update: () => Promise<void>) => Transition };

/** La transition de vue en cours : seule elle retire la classe `is-view-transition` (une suivante peut l'avoir coupée). */
let current: Transition | null = null;

/**
 * Change d'état dans une transition de vue (View Transitions API) quand le navigateur la connaît et que le mouvement
 * n'est pas réduit ; sinon applique le changement tout de suite (le CSS joue alors l'entrée de repli).
 * L'état qui part est capturé sous le nom `journey-out` (classe `vt-old` sur la racine, retirée avant le changement) :
 * il s'efface sur place, à l'endroit où la personne le voyait, pendant que le nouveau arrive en haut — deux groupes
 * séparés, jamais un fondu de l'un dans l'autre.
 * `update` déclenche le changement ; `ready` est appelée par l'appelant quand le nouvel état est prêt (contenu chargé) —
 * elle est renvoyée pour être rangée, et un garde-fou la remplace au bout de 450 ms, pour ne jamais figer l'écran.
 */
export function viewTransition(update: (ready: () => void) => void): boolean {
  const doc = document as TransitionDocument;
  if (reducedMotion() || typeof doc.startViewTransition !== "function") {
    update(() => undefined);
    return false;
  }
  const root = document.documentElement;
  root.classList.add("vt-old");
  try {
    const t: Transition = doc.startViewTransition(
      () =>
        new Promise<void>((resolve) => {
          root.classList.remove("vt-old");
          root.classList.add("is-view-transition");
          const timer = window.setTimeout(resolve, 450);
          update(() => {
            window.clearTimeout(timer);
            resolve();
          });
        }),
    );
    current = t;
    void t.finished.catch(() => undefined).finally(() => {
      if (current !== t) return;
      current = null;
      root.classList.remove("is-view-transition");
    });
    return true;
  } catch {
    root.classList.remove("vt-old");
    update(() => undefined);
    return false;
  }
}

/**
 * Une valeur qui glisse jusqu'à `target` (même courbe que var(--ease-out) en CSS), depuis la dernière valeur montrée —
 * ou depuis `from` au premier rendu (par défaut : la cible elle-même, donc rien ne bouge au chargement).
 * Non arrondie : l'anneau la suit au centième, le chiffre l'arrondit (voir useCountUp) — un seul minutage pour les deux.
 */
export function useTween(target: number, { duration = 900, delay = 0, from }: { duration?: number; delay?: number; from?: number } = {}): number {
  const [value, setValue] = useState(() => (reducedMotion() || from === undefined ? target : from));
  const shown = useRef(value);
  useEffect(() => {
    const land = () => {
      shown.current = target;
      setValue(target);
    };
    if (reducedMotion() || typeof window.requestAnimationFrame !== "function" || shown.current === target) {
      land();
      return;
    }
    const start0 = shown.current;
    let raf = 0;
    let start: number | null = null;
    const tick = (now: number) => {
      if (start === null) start = now + delay;
      const t = Math.min(1, Math.max(0, (now - start) / duration));
      const v = start0 + (target - start0) * easeOut(t);
      shown.current = v;
      setValue(v);
      if (t < 1) raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    const stop = onReducedMotion(() => {
      window.cancelAnimationFrame(raf);
      land();
    });
    return () => {
      window.cancelAnimationFrame(raf);
      stop();
    };
  }, [target, duration, delay]);
  return value;
}

/** Un nombre entier qui se compte jusqu'à `target` (voir useTween). */
export function useCountUp(target: number, opts?: { duration?: number; delay?: number; from?: number }): number {
  return Math.round(useTween(target, opts));
}

/** cubic-bezier(0.2, 0.8, 0.2, 1) — le jeton --ease-out — résolu en x par dichotomie, pour suivre le CSS au plus près. */
export function easeOut(x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const bez = (t: number, a: number, b: number) => 3 * (1 - t) * (1 - t) * t * a + 3 * (1 - t) * t * t * b + t * t * t;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 20; i++) {
    const mid = (lo + hi) / 2;
    if (bez(mid, 0.2, 0.2) < x) lo = mid;
    else hi = mid;
  }
  return bez((lo + hi) / 2, 0.8, 1);
}
