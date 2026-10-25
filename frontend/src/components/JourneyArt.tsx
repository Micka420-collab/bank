// Les images du parcours (DESIGN.md § 3.10, § 3.7) : une petite scène en SVG inline par étape, qui s'anime une fois
// à l'arrivée — l'image entière « pope » (un seul calque HTML, composé hors du fil principal), puis deux ou trois gestes
// signature dans le SVG (la carte, la coche, la cloche) ; elle flotte un peu, puis se pose. Le ciel vivant derrière. Tout est décoratif (aria-hidden) ; les
// couleurs viennent des jetons, par classes (aucun attribut `style`, aucune couleur écrite ici) ; on n'anime que
// transform, opacity et stroke-dashoffset. Mouvement réduit : la règle globale coupe tout, et l'état de base de
// chaque forme est son état final — l'image est simplement là, immobile.

import { useId, type ReactNode } from "react";
import type { StartStepKey } from "../api";

function Scene({ name, still, children }: { name: string; still: boolean; children: (grad: string) => ReactNode }) {
  const grad = `art-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span className={`journey-art art-${name}${still ? " is-still" : ""}`} aria-hidden="true">
      <svg viewBox="0 0 120 120" focusable="false">
        <defs>
          <linearGradient id={grad} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" className="ring-stop-a" />
            <stop offset="1" className="ring-stop-b" />
          </linearGradient>
        </defs>
        <circle className="art-disc" cx="60" cy="60" r="54" />
        {children(`url(#${grad})`)}
      </svg>
    </span>
  );
}

/**
 * La scène de l'étape : carte et flèches de synchro, maison et foyer, bouclier et interrupteurs… `still` : déjà vue
 * pendant ce passage, elle est simplement là. Toutes les formes restent dans le disque (rayon ≈ 50 sur 120).
 */
export function StepArt({ step, still = false }: { step: StartStepKey; still?: boolean }) {
  switch (step) {
    case "banque":
      return (
        <Scene still={still} name="banque">
          {(g) => (
            <>
              <circle className="art-coin" cx="24" cy="34" r="6" />
              <circle className="art-coin" cx="96" cy="92" r="4.5" />
              <g className="art-card">
                <rect x="20" y="42" width="78" height="50" rx="10" fill={g} />
                <rect className="art-chip" x="31" y="55" width="15" height="11" rx="3" />
                <rect className="art-on-accent" x="31" y="75" width="44" height="5" rx="2.5" opacity="0.75" />
                <rect className="art-on-accent" x="80" y="75" width="9" height="5" rx="2.5" opacity="0.5" />
              </g>
              <g className="art-sync">
                <circle className="art-plate" cx="92" cy="34" r="17" />
                <g className="art-spin">
                  <path className="art-stroke" d="M84 31.5a8.5 8.5 0 0 1 14.6-3.6" />
                  <path className="art-stroke" d="M100 36.5a8.5 8.5 0 0 1-14.6 3.6" />
                  <path className="art-stroke" d="M99.4 22.6v5.6h-5.6" />
                  <path className="art-stroke" d="M84.6 45.4v-5.6h5.6" />
                </g>
              </g>
            </>
          )}
        </Scene>
      );
    case "foyer":
      return (
        <Scene still={still} name="foyer">
          {(g) => (
            <>
              {/* Le cœur flotte au-dessus du toit, le foyer se tient devant la maison, tout dans le disque. */}
              <path className="art-heart" d="M60 20c-3.6-4.6-11-2.4-9.8 3.4.8 3.6 5 6.6 9.8 9.8 4.8-3.2 9-6.2 9.8-9.8 1.2-5.8-6.2-8-9.8-3.4Z" />
              <path className="art-house a-draw" pathLength={1} d="M28 64 60 38l32 26v28a4 4 0 0 1-4 4H32a4 4 0 0 1-4-4Z" />
              <rect className="art-window" x="36" y="68" width="12" height="11" rx="3" />
              <rect x="50" y="74" width="14" height="22" rx="4" fill={g} />
              <g className="a-rise">
                <g className="art-person">
                  <circle cx="78" cy="76" r="6" />
                  <path d="M69 95a9 9 0 0 1 18 0Z" />
                </g>
                <g className="art-person is-small">
                  <circle cx="91" cy="84" r="4.5" />
                  <path d="M85.5 96a5.5 5.5 0 0 1 11 0Z" />
                </g>
              </g>
            </>
          )}
        </Scene>
      );
    case "accords":
      return (
        <Scene still={still} name="accords">
          {(g) => (
            <>
              {/* Un bouclier un peu plus petit, ses deux interrupteurs posés dans le disque, de part et d'autre de sa pointe. */}
              <path d="M60 18 84 26.5v20.5c0 16-10 26.5-24 32.5-14-6-24-16.5-24-32.5V26.5Z" fill={g} />
              <path className="art-check a-draw d2" pathLength={1} d="m48 48 9 9 16-18" />
              <rect className="art-toggle is-on" x="70" y="80" width="26" height="14" rx="7" />
              <circle className="art-knob a-knob d3" cx="89" cy="87" r="5" />
              <rect className="art-toggle" x="24" y="80" width="26" height="14" rx="7" />
              <circle className="art-knob" cx="31" cy="87" r="5" />
            </>
          )}
        </Scene>
      );
    case "papier":
      return (
        <Scene still={still} name="papier">
          {(g) => (
            <>
              <path className="art-folder-back" d="M22 46a4 4 0 0 1 4-4h22l7 7h39a4 4 0 0 1 4 4v38a4 4 0 0 1-4 4H26a4 4 0 0 1-4-4Z" />
              <g className="art-doc">
                <rect className="art-paper" x="42" y="20" width="36" height="46" rx="5" />
                <rect className="art-line" x="49" y="30" width="22" height="4" rx="2" />
                <rect className="art-line" x="49" y="39" width="16" height="4" rx="2" />
                <rect className="art-line is-accent" x="49" y="48" width="12" height="4" rx="2" />
              </g>
              <path d="M20 62a4 4 0 0 1 4-4h72a4 4 0 0 1 4 4.6l-3.6 30A4 4 0 0 1 92.4 96H27.6a4 4 0 0 1-4-3.4Z" fill={g} />
              <circle className="art-ok" cx="92" cy="30" r="11" />
              <path className="art-check is-small" pathLength={1} d="m87 30 3.6 3.6L97 27" />
            </>
          )}
        </Scene>
      );
    case "priorites":
      return (
        <Scene still={still} name="priorites">
          {(g) => (
            <>
              <circle className="art-ring" cx="56" cy="64" r="38" />
              <circle cx="56" cy="64" r="27" fill={g} />
              <circle className="art-ring is-inner" cx="56" cy="64" r="16" />
              <circle className="art-bull" cx="56" cy="64" r="6.5" />
              <g className="art-arrow">
                <path className="art-shaft" d="M57 63 98 22" />
                <path className="art-fletch" d="m92 18 8-2-2 8-8 2Z" />
              </g>
            </>
          )}
        </Scene>
      );
    case "prevenir":
      return (
        <Scene still={still} name="prevenir">
          {(g) => (
            <>
              <rect className="art-phone" x="34" y="18" width="46" height="86" rx="11" />
              <rect className="art-notch" x="49" y="23" width="16" height="4" rx="2" />
              <g className="art-notif">
                <rect x="24" y="40" width="66" height="22" rx="8" fill={g} />
                <circle className="art-on-accent" cx="35" cy="51" r="4.5" />
                <rect className="art-on-accent" x="44" y="46" width="34" height="4" rx="2" opacity="0.85" />
                <rect className="art-on-accent" x="44" y="53" width="22" height="4" rx="2" opacity="0.6" />
              </g>
              <rect className="art-line" x="42" y="72" width="30" height="5" rx="2.5" />
              <rect className="art-line" x="42" y="82" width="22" height="5" rx="2.5" />
              <g className="art-bell">
                <path className="art-bell-body" d="M96 22a11 11 0 0 1 11 11v9l4 6H81l4-6v-9a11 11 0 0 1 11-11Z" />
                <path className="art-bell-body" d="M92 51a4 4 0 0 0 8 0Z" />
              </g>
              <circle className="art-badge a-pop d4" cx="104" cy="25" r="5.5" />
            </>
          )}
        </Scene>
      );
    case "visite":
      return (
        <Scene still={still} name="visite">
          {(g) => (
            <>
              <g className="art-side is-left">
                <rect className="art-frame is-faint" x="10" y="32" width="30" height="56" rx="7" />
              </g>
              <g className="art-side is-right">
                <rect className="art-frame is-faint" x="80" y="32" width="30" height="56" rx="7" />
              </g>
              <g>
                <rect className="art-frame" x="38" y="16" width="44" height="82" rx="10" />
                <rect className="art-line is-accent" x="46" y="28" width="16" height="4" rx="2" />
                <rect x="46" y="36" width="28" height="12" rx="3" fill={g} />
                <rect className="art-line" x="46" y="54" width="28" height="7" rx="3" />
                <rect className="art-line" x="46" y="65" width="28" height="7" rx="3" />
                <rect className="art-island" x="44" y="82" width="32" height="10" rx="5" />
                <rect className="art-island-on" x="46" y="84" width="9" height="6" rx="3" fill={g} />
              </g>
            </>
          )}
        </Scene>
      );
  }
}

/** Le badge de la fin : un disque qui « pope », une coche qui se trace. */
export function FinaleBadge() {
  const grad = `art-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span className="finale-badge" aria-hidden="true">
      <svg viewBox="0 0 80 80" focusable="false">
        <defs>
          <linearGradient id={grad} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" className="ring-stop-a" />
            <stop offset="1" className="ring-stop-b" />
          </linearGradient>
        </defs>
        <circle className="finale-badge-halo" cx="40" cy="40" r="38" />
        <circle cx="40" cy="40" r="28" fill={`url(#${grad})`} />
        <path className="finale-badge-check" pathLength={1} d="m28 41 8.5 8.5L53 32" />
      </svg>
    </span>
  );
}

/**
 * Des confettis en CSS seul, dans la carte de fin (jamais plein écran), sous le texte : ils partent du badge une fois
 * la coche tracée et retombent sans traverser le message ; cachés si le mouvement est réduit.
 */
export function Confetti() {
  return (
    <span className="finale-confetti" aria-hidden="true">
      {Array.from({ length: 14 }, (_, i) => <i key={i} />)}
    </span>
  );
}

/** Le ciel vivant du parcours : trois halos qui dérivent lentement (transform seul), fixes si le mouvement est réduit. */
export function JourneySky() {
  return (
    <span className="journey-sky" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
