// Revenus et dépenses par période : deux barres par période, échelle commune, lisibles sans légende savante.

import { money } from "../../format";

export type Period = { key: string; label: string; income_cents: number; spent_cents: number };

export function Bars({ periods }: { periods: Period[] }) {
  if (periods.length === 0) return <p className="muted">Rien à comparer sur cette période.</p>;
  const max = Math.max(1, ...periods.flatMap((p) => [p.income_cents, p.spent_cents]));
  const shown = periods.slice(-12);
  return (
    <div>
      <div className="bars-wrap" role="img" aria-label={shown.map((p) => `${p.label} : reçu ${money(p.income_cents, { whole: true })}, dépensé ${money(p.spent_cents, { whole: true })}`).join(" ; ")}>
        {shown.map((p, i) => (
          <div className="bar-month" key={p.key}>
            <div className="bar-pair">
              <i style={{ height: `${(p.income_cents / max) * 100}%`, animationDelay: `${i * 40}ms` }} title={`Reçu ${money(p.income_cents)}`} />
              <i className="out" style={{ height: `${(p.spent_cents / max) * 100}%`, animationDelay: `${i * 40}ms` }} title={`Dépensé ${money(p.spent_cents)}`} />
            </div>
            <span>{p.label.replace("sem. du ", "")}</span>
          </div>
        ))}
      </div>
      <div className="legend">
        <span className="swatch" style={{ ["--c" as string]: "var(--positive)" }}>Reçu</span>
        <span className="swatch" style={{ ["--c" as string]: "var(--ink-2)" }}>Dépensé</span>
      </div>
    </div>
  );
}
