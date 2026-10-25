// Les images du parcours (DESIGN.md § 3.10, § 3.7) : une petite scène en SVG inline par étape, qui s'anime une fois
// à l'arrivée (puis flotte doucement), et le ciel vivant derrière le parcours. Tout est décoratif (aria-hidden) ; les
// couleurs viennent des jetons, par classes (aucun attribut `style`, aucune couleur écrite ici) ; on n'anime que
// transform, opacity et stroke-dashoffset. Mouvement réduit : la règle globale coupe tout, et l'état de base de
// chaque forme est son état final — l'image est simplement là, immobile.

import { useId, type ReactNode } from "react";
import type { StartStepKey } from "../api";

function Scene({ name, children }: { name: string; children: (grad: string) => ReactNode }) {
  const grad = `art-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span className={`journey-art art-${name}`} aria-hidden="true">
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

/** La scène de l'étape : carte et flèches de synchro, maison et foyer, bouclier et interrupteurs… */
export function StepArt({ step }: { step: StartStepKey }) {
  switch (step) {
    case "banque":
      return (
        <Scene name="banque">
          {(g) => (
            <>
              <circle className="art-coin a-pop d1" cx="24" cy="34" r="6" />
              <circle className="art-coin a-pop d3" cx="98" cy="92" r="4.5" />
              <g className="art-card">
                <rect x="20" y="42" width="78" height="50" rx="10" fill={g} />
                <rect className="art-chip" x="31" y="55" width="15" height="11" rx="3" />
                <rect className="art-on-accent" x="31" y="75" width="44" height="5" rx="2.5" opacity="0.75" />
                <rect className="art-on-accent" x="80" y="75" width="9" height="5" rx="2.5" opacity="0.5" />
              </g>
              <g className="art-sync a-pop d2">
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
        <Scene name="foyer">
          {(g) => (
            <>
              <path className="art-heart" d="M60 33c-3.6-4.6-11-2.4-9.8 3.4.8 3.6 5 6.6 9.8 9.8 4.8-3.2 9-6.2 9.8-9.8 1.2-5.8-6.2-8-9.8-3.4Z" />
              <path className="art-house a-draw" pathLength={1} d="M28 64 60 38l32 26v28a4 4 0 0 1-4 4H32a4 4 0 0 1-4-4Z" />
              <rect className="art-window a-pop d2" x="36" y="68" width="12" height="11" rx="3" />
              <rect className="a-pop d3" x="53" y="74" width="15" height="22" rx="4" fill={g} />
              <g className="art-person a-rise d3">
                <circle cx="82" cy="78" r="6.5" />
                <path d="M72 98a10 10 0 0 1 20 0Z" />
              </g>
              <g className="art-person is-small a-rise d4">
                <circle cx="97" cy="86" r="4.5" />
                <path d="M90.5 99a6.5 6.5 0 0 1 13 0Z" />
              </g>
            </>
          )}
        </Scene>
      );
    case "accords":
      return (
        <Scene name="accords">
          {(g) => (
            <>
              <path className="a-pop d1" d="M60 18 88 28v24c0 19-12 31-28 38-16-7-28-19-28-38V28Z" fill={g} />
              <path className="art-check a-draw d3" pathLength={1} d="m46 53 10 10 19-21" />
              <g className="a-rise d4">
                <rect className="art-toggle is-on" x="74" y="90" width="32" height="17" rx="8.5" />
                <circle className="art-knob a-knob d5" cx="97.5" cy="98.5" r="6" />
              </g>
              <g className="a-rise d5">
                <rect className="art-toggle" x="14" y="90" width="32" height="17" rx="8.5" />
                <circle className="art-knob" cx="22.5" cy="98.5" r="6" />
              </g>
            </>
          )}
        </Scene>
      );
    case "papier":
      return (
        <Scene name="papier">
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
              <g className="a-pop d5">
                <circle className="art-ok" cx="92" cy="30" r="11" />
                <path className="art-check is-small" pathLength={1} d="m87 30 3.6 3.6L97 27" />
              </g>
            </>
          )}
        </Scene>
      );
    case "priorites":
      return (
        <Scene name="priorites">
          {(g) => (
            <>
              <circle className="art-ring a-pop d1" cx="56" cy="64" r="38" />
              <circle className="a-pop d2" cx="56" cy="64" r="27" fill={g} />
              <circle className="art-ring is-inner a-pop d3" cx="56" cy="64" r="16" />
              <circle className="art-bull a-pop d4" cx="56" cy="64" r="6.5" />
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
        <Scene name="prevenir">
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
              <rect className="art-line a-rise d4" x="42" y="72" width="30" height="5" rx="2.5" />
              <rect className="art-line a-rise d5" x="42" y="82" width="22" height="5" rx="2.5" />
              <g className="art-bell">
                <path className="art-bell-body" d="M96 22a11 11 0 0 1 11 11v9l4 6H81l4-6v-9a11 11 0 0 1 11-11Z" />
                <path className="art-bell-body" d="M92 51a4 4 0 0 0 8 0Z" />
              </g>
              <circle className="art-badge a-pop d4" cx="106" cy="24" r="5.5" />
            </>
          )}
        </Scene>
      );
    case "visite":
      return (
        <Scene name="visite">
          {(g) => (
            <>
              <g className="art-side is-left">
                <rect className="art-frame is-faint" x="10" y="32" width="30" height="56" rx="7" />
              </g>
              <g className="art-side is-right">
                <rect className="art-frame is-faint" x="80" y="32" width="30" height="56" rx="7" />
              </g>
              <g className="a-pop d1">
                <rect className="art-frame" x="38" y="16" width="44" height="82" rx="10" />
                <rect className="art-line is-accent a-rise d3" x="46" y="28" width="16" height="4" rx="2" />
                <rect className="a-rise d3" x="46" y="36" width="28" height="12" rx="3" fill={g} />
                <rect className="art-line a-rise d4" x="46" y="54" width="28" height="7" rx="3" />
                <rect className="art-line a-rise d5" x="46" y="65" width="28" height="7" rx="3" />
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

/** Des confettis en CSS seul, dans la carte de fin (jamais plein écran) ; cachés si le mouvement est réduit. */
export function Confetti() {
  return (
    <span className="finale-confetti" aria-hidden="true">
      {Array.from({ length: 22 }, (_, i) => <i key={i} />)}
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
