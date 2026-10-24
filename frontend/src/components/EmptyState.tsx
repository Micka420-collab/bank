// États vides illustrés (§ 3.4 de DESIGN.md) : un petit dessin au trait des icônes (SVG inline, 96 px), une phrase
// qui félicite ou invite, et l'action quand il y en a une. Le dessin est décoratif : le texte dit tout.

import type { ReactNode } from "react";

export type EmptyArt = "jar" | "folder" | "sun" | "check";

export function EmptyIllustration({ art }: { art: EmptyArt }) {
  return (
    <svg className={`empty-art is-${art}`} viewBox="0 0 96 96" aria-hidden="true" focusable="false">
      <circle className="ea-halo" cx="48" cy="50" r="40" />
      {art === "sun" && (
        <g className="ea-sun">
          <circle className="ea-fill" cx="48" cy="50" r="15" />
          <path className="ea-line" d="M48 22v-8M48 86v-8M20 50h-8M84 50h-8M28.2 30.2l-5.6-5.6M73.4 75.4l-5.6-5.6M28.2 69.8l-5.6 5.6M73.4 24.6l-5.6 5.6" />
          <path className="ea-line" d="M42 50.5c1.5 2.5 3.5 3.5 6 3.5s4.5-1 6-3.5M43 45h.01M53 45h.01" />
        </g>
      )}
      {art === "check" && (
        <g className="ea-check">
          <circle className="ea-fill" cx="48" cy="50" r="24" />
          <path className="ea-line ea-tick" d="M37 50.5 45 58.5 60 42" />
          <path className="ea-spark" d="M78 20v8M74 24h8M18 70v6M15 73h6" />
        </g>
      )}
      {art === "folder" && (
        <g className="ea-folder">
          <path className="ea-paper" d="M34 22h22l9 9v24H34z" />
          <path className="ea-line" d="M56 22v9h9M40 38h18M40 45h12" />
          <path className="ea-fill" d="M18 40h20l5 6h35v26a4 4 0 0 1-4 4H22a4 4 0 0 1-4-4z" />
          <path className="ea-line" d="M18 40h20l5 6h35v26a4 4 0 0 1-4 4H22a4 4 0 0 1-4-4z" />
          <path className="ea-spark" d="M80 24v6M77 27h6" />
        </g>
      )}
      {art === "jar" && (
        <g className="ea-jar">
          <path className="ea-paper" d="M32 34q0-5 6-5h20q6 0 6 5v36q0 8-8 8H40q-8 0-8-8z" />
          <path className="ea-fill" d="M32 56q8-3 16 0t16 0v14q0 8-8 8H40q-8 0-8-8z" />
          <path className="ea-line" d="M32 34q0-5 6-5h20q6 0 6 5v36q0 8-8 8H40q-8 0-8-8zM36 18h24v8H36z" />
          <circle className="ea-coin" cx="70" cy="22" r="7" />
          <path className="ea-coin-mark" d="M72 19.5a3 3 0 1 0 0 5M67.5 21.3h3.5M67.5 23h3.5" />
        </g>
      )}
    </svg>
  );
}

export function EmptyState({ art, title, children, actions, className = "" }: {
  art: EmptyArt;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`empty-state${className ? ` ${className}` : ""}`}>
      <EmptyIllustration art={art} />
      <div className="empty-state-body">
        <h2>{title}</h2>
        {children}
        {actions && <div className="actions">{actions}</div>}
      </div>
    </section>
  );
}
