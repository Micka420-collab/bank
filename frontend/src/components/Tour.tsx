// La visite (DESIGN.md § 3.10 c) : cinq écrans, une phrase chacun, l'île d'onglets dessinée en SVG inline avec
// l'onglet concerné allumé et un petit aperçu en CSS au-dessus (rien d'externe, rien de faux). Elle n'avance jamais
// seule : « Suivant », « Précédent », des points d'étape.

import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "./Icon";

export const TOUR = [
  { key: "jour", tab: "Jour", text: "Chaque matin, ce que tu peux dépenser aujourd'hui, et la prochaine chose à faire." },
  { key: "operations", tab: "Opérations", text: "Tout ce qui entre et sort, rangé pour toi ; tu corriges d'un geste." },
  { key: "gagner", tab: "Gagner", text: "Les aides et économies que Bank a trouvées pour toi, et ta tirelire." },
  { key: "papiers", tab: "Papiers", text: "Prends un papier en photo : Bank le lit, le range et prépare tes démarches." },
  { key: "plus", tab: "Plus", text: "Épargne, réglages, connecteurs et messageries : tout le reste est ici." },
] as const;

export function Tour({ onFinish, busy = false }: { onFinish: () => void; busy?: boolean }) {
  const [i, setI] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const screen = TOUR[i]!;
  const last = i === TOUR.length - 1;

  useEffect(() => {
    // Au premier écran, le focus est sur le titre de l'étape ; ensuite il suit l'écran affiché.
    if (moved.current) heading.current?.focus();
  }, [i]);
  const go = (n: number) => {
    moved.current = true;
    setI(n);
  };

  return (
    <div className="tour">
      <div className="tour-screen" key={screen.key}>
        <TourPreview which={screen.key} />
        <h2 ref={heading} tabIndex={-1} className="tour-title">{screen.tab}</h2>
        <p className="tour-text">{screen.text}</p>
        {last && <p className="tour-ask"><Icon name="spark" /> Une question ? Demande à Bank, en bas à droite.</p>}
        <TabIsland active={i} />
      </div>
      <p className="sr-only" aria-live="polite">Écran {i + 1} sur {TOUR.length} : {screen.tab}</p>
      <ol className="tour-dots" aria-label="Écrans de la visite">
        {TOUR.map((s, k) => (
          <li key={s.key} aria-current={k === i ? "step" : undefined}>
            <button type="button" onClick={() => go(k)} aria-label={`${s.tab}, écran ${k + 1} sur ${TOUR.length}`}><span aria-hidden="true" /></button>
          </li>
        ))}
      </ol>
      <div className="actions journey-actions">
        {last ? (
          <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy} onClick={onFinish}>
            Voir mon tableau de bord <Icon name="arrow" />
          </button>
        ) : (
          <button type="button" className="btn lg" onClick={() => go(i + 1)}>Suivant <Icon name="arrow" /></button>
        )}
        {i > 0 && <button type="button" className="btn quiet lg" onClick={() => go(i - 1)}>Précédent</button>}
      </div>
    </div>
  );
}

/** L'île d'onglets du téléphone, dessinée : cinq onglets, celui de l'écran en cours dans sa bulle lumineuse. */
export function TabIsland({ active }: { active: number }) {
  const gid = `tour-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const w = 340;
  const slot = (w - 16) / TOUR.length;
  return (
    <svg className="tour-island" viewBox={`0 0 ${w} 64`} role="img" aria-label={`Onglet « ${TOUR[active]!.tab} », en bas de l'écran`}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" className="ring-stop-a" />
          <stop offset="1" className="ring-stop-b" />
        </linearGradient>
      </defs>
      <rect className="tour-island-plate" x="1" y="1" width={w - 2} height="62" rx="31" />
      <rect className="tour-island-bubble" x={8 + active * slot + 3} y="7" width={slot - 6} height="50" rx="25" fill={`url(#${gid})`} />
      {TOUR.map((s, k) => {
        const cx = 8 + k * slot + slot / 2;
        return (
          <g key={s.key} className={k === active ? "is-on" : undefined}>
            <circle cx={cx} cy="24" r="6" className="tour-island-dot" />
            <text x={cx} y="47" textAnchor="middle" className="tour-island-label">{s.tab}</text>
          </g>
        );
      })}
    </svg>
  );
}

function TourPreview({ which }: { which: (typeof TOUR)[number]["key"] }) {
  // Des aperçus abstraits (des formes, pas des chiffres) : on montre où est quoi, sans faire croire à un vrai compte.
  return (
    <div className={`tour-preview is-${which}`} aria-hidden="true">
      {which === "jour" && (<><i className="tp-label" /><i className="tp-figure" /><i className="tp-line" /><i className="tp-card" /></>)}
      {which === "operations" && (<><i className="tp-row" /><i className="tp-row" /><i className="tp-row" /><i className="tp-row" /></>)}
      {which === "gagner" && (<><i className="tp-coin" /><i className="tp-card" /><i className="tp-card is-short" /></>)}
      {which === "papiers" && (<><i className="tp-doc" /><i className="tp-doc" /><i className="tp-doc" /></>)}
      {which === "plus" && (<><i className="tp-tile" /><i className="tp-tile" /><i className="tp-tile" /><i className="tp-tile" /><i className="tp-fab" /></>)}
    </div>
  );
}
