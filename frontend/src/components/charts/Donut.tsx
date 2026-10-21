// Répartition par catégorie : anneau en SVG (un arc par catégorie, animé), liste lisible à côté, total au centre.

import { money } from "../../format";

export type Slice = { slug: string; name: string; color: string; cents: number; share: number };

export function Donut({ slices, total, label, centerLabel = "dépensé" }: { slices: Slice[]; total: number; label: string; centerLabel?: string }) {
  const R = 42;
  const C = 2 * Math.PI * R;
  let offset = 0;
  const arcs = slices.map((s) => {
    const len = C * s.share;
    const arc = { ...s, dash: `${len} ${C - len}`, offset: -offset };
    offset += len;
    return arc;
  });
  if (slices.length === 0) return <p className="muted">{centerLabel === "dépensé" ? "Aucune dépense sur cette période." : "Rien à répartir pour l'instant."}</p>;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 120 120" role="img" aria-label={`${label} : ${slices.map((s) => `${s.name} ${Math.round(s.share * 100)} %`).join(", ")}`}>
        <circle cx="60" cy="60" r={R} fill="none" stroke="var(--surface-2)" strokeWidth="18" />
        {arcs.map((a) => (
          <circle key={a.slug} className="donut-arc" cx="60" cy="60" r={R} stroke={a.color} strokeDasharray={a.dash} strokeDashoffset={a.offset} transform="rotate(-90 60 60)" />
        ))}
        <text x="60" y="57" textAnchor="middle" style={{ fontSize: 12, fill: "var(--ink-2)" }}>{centerLabel}</text>
        <text x="60" y="73" textAnchor="middle" style={{ fontSize: 14, fontWeight: 700, fill: "var(--ink)" }}>{money(total, { whole: true })}</text>
      </svg>
      <ul className="donut-list">
        {slices.map((s) => (
          <li key={s.slug} style={{ ["--c" as string]: s.color }}>
            <i aria-hidden="true" />
            <span>{s.name}</span>
            <b>{money(s.cents, { whole: true })} <span className="faint">{Math.round(s.share * 100)} %</span></b>
          </li>
        ))}
      </ul>
    </div>
  );
}
