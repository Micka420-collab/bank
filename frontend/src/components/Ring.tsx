// Anneau de progression (§ 3.4 de DESIGN.md) : piste pâle, arc en dégradé d'accent, bout rond, valeur au centre.
// SVG inline, dégradé déclaré dans le JSX (aucune URL externe) ; les couleurs viennent des jetons via les classes.

import { useId, type ReactNode } from "react";

export type RingSize = "sm" | "md" | "lg";

/** Part accomplie, de 0 à 100, arrondie ; 0 quand il n'y a rien à faire (et non une division par zéro). */
export function ringPercent(value: number, max: number): number {
  if (!(max > 0)) return 0;
  return Math.max(0, Math.min(100, Math.round((value / max) * 100)));
}

export function Ring({ value, max, label, size = "md", live = false, children }: {
  /** Peut être fractionnaire quand l'anneau est piloté par une animation (`live`). */
  value: number;
  max: number;
  /** Phrase lue par les lecteurs d'écran, par exemple « 5 papiers sur 7 vérifiés ». */
  label: string;
  size?: RingSize;
  /** L'arc suit `value` tel quel, sans son tracé CSS d'arrivée : un script l'anime (useTween), avec son chiffre. */
  live?: boolean;
  /** Ce qui s'écrit au centre ; le pourcentage par défaut. */
  children?: ReactNode;
}) {
  const pct = ringPercent(value, max);
  const arc = live && max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : pct;
  // useId peut contenir des caractères qu'une référence url(#…) ne supporte pas partout.
  const gid = `ring-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span className={`ring ring-${size}${pct >= 100 ? " is-full" : ""}${live ? " is-live" : ""}`} role="img" aria-label={label}>
      <svg viewBox="0 0 36 36" aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" className="ring-stop-a" />
            <stop offset="1" className="ring-stop-b" />
          </linearGradient>
        </defs>
        <circle className="ring-track" cx="18" cy="18" r="15.5" />
        {arc > 0 && (
          <circle className="ring-arc" cx="18" cy="18" r="15.5" pathLength={100} stroke={`url(#${gid})`}
                  strokeDasharray={`${live ? +arc.toFixed(2) : pct} 100`} transform="rotate(-90 18 18)" />
        )}
      </svg>
      <span className="ring-value" aria-hidden="true">{children ?? `${pct} %`}</span>
    </span>
  );
}
