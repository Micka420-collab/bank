// Mouvement (DESIGN.md § 3.7) : les petites aides partagées par le parcours et « Bien démarrer ».
// Tout part d'une règle : sans préférence de mouvement connue (pas de matchMedia, tests compris) ou avec
// « réduire les animations », on va droit à l'état final. Rien ici n'écrit d'attribut `style`.

import { useEffect, useRef, useState } from "react";

/** Vrai quand il faut aller droit à l'état final : mouvement réduit demandé, ou préférence inconnue. */
export function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

type Transition = { finished: Promise<void> };
type TransitionDocument = Document & { startViewTransition?: (update: () => Promise<void>) => Transition };

/**
 * Change d'état dans une transition de vue (View Transitions API) quand le navigateur la connaît et que le mouvement
 * n'est pas réduit ; sinon applique le changement tout de suite (le CSS joue alors l'entrée de repli).
 * `update` déclenche le changement ; `ready` est appelée par l'appelant quand le nouvel état est dans le DOM — elle est
 * renvoyée pour être rangée, et un garde-fou la remplace au bout de 450 ms, pour ne jamais figer l'écran.
 */
export function viewTransition(update: (ready: () => void) => void): boolean {
  const doc = document as TransitionDocument;
  if (reducedMotion() || typeof doc.startViewTransition !== "function") {
    update(() => undefined);
    return false;
  }
  const root = document.documentElement;
  try {
    const t = doc.startViewTransition(
      () =>
        new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, 450);
          update(() => {
            window.clearTimeout(timer);
            resolve();
          });
        }),
    );
    root.classList.add("is-view-transition");
    void t.finished.catch(() => undefined).finally(() => root.classList.remove("is-view-transition"));
    return true;
  } catch {
    update(() => undefined);
    return false;
  }
}

/** Un nombre qui se compte jusqu'à `target` (ease-out, ~0,9 s), depuis la dernière valeur montrée. */
export function useCountUp(target: number, { duration = 900, delay = 0 }: { duration?: number; delay?: number } = {}): number {
  const [value, setValue] = useState(() => (reducedMotion() ? target : 0));
  const shown = useRef(value);
  useEffect(() => {
    if (reducedMotion() || typeof window.requestAnimationFrame !== "function" || shown.current === target) {
      shown.current = target;
      setValue(target);
      return;
    }
    const from = shown.current;
    let raf = 0;
    let start: number | null = null;
    const tick = (now: number) => {
      if (start === null) start = now + delay;
      const t = Math.min(1, Math.max(0, (now - start) / duration));
      const eased = 1 - Math.pow(1 - t, 3);
      const v = Math.round(from + (target - from) * eased);
      if (v !== shown.current) {
        shown.current = v;
        setValue(v);
      }
      if (t < 1) raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(raf);
    };
  }, [target, duration, delay]);
  return value;
}
