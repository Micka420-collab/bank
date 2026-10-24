// Courbe de tendance interactive : aire sous la courbe, grille discrète, infobulle au survol et au toucher,
// navigation au clavier (flèches), tracé animé quand la série change. Un seul composant pour le solde et la
// valeur nette.

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { longDate, money, shortDate } from "../../format";

export type TrendPoint = { date: string; value: number };

const W = 720;
const H = 220;
const PAD = { top: 16, right: 12, bottom: 8, left: 12 };

/** Échelle verticale : « zero » garde le zéro visible (un solde, où le découvert est la référence) ;
 * « auto » cadre les valeurs elles-mêmes (une valeur nette, dont les variations comptent plus que la distance à zéro). */
export function scaleY(values: number[], baseline: "zero" | "auto" = "zero"): { min: number; max: number } {
  const lo = baseline === "zero" ? Math.min(0, ...values) : Math.min(...values);
  const hi = baseline === "zero" ? Math.max(...values, 1) : Math.max(...values);
  const span = Math.max(1, hi - lo, Math.abs(hi) * 0.02);
  return { min: lo - span * 0.08, max: hi + span * 0.08 };
}

export function TrendChart({ points, label, format = (v) => money(v, { whole: true }), markers = [], baseline = "zero" }: {
  points: TrendPoint[];
  label: string;
  format?: (v: number) => string;
  markers?: { date: string; label: string }[];
  baseline?: "zero" | "auto";
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [len, setLen] = useState(2000);
  const pathRef = useRef<SVGPathElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const n = points.length;
  const { min, max } = useMemo(() => scaleY(points.map((p) => p.value), baseline), [points, baseline]);
  const x = (i: number) => PAD.left + (n <= 1 ? (W - PAD.left - PAD.right) / 2 : (i / (n - 1)) * (W - PAD.left - PAD.right));
  const y = (v: number) => PAD.top + (1 - (v - min) / (max - min)) * (H - PAD.top - PAD.bottom);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const area = n > 0 ? `${line}L${x(n - 1).toFixed(1)},${y(min).toFixed(1)}L${x(0).toFixed(1)},${y(min).toFixed(1)}Z` : "";
  const key = points.map((p) => p.date).join("|") + n;

  useLayoutEffect(() => {
    // jsdom ne mesure pas les tracés : on garde alors la longueur par défaut (le dessin reste correct, sans animation).
    const el = pathRef.current;
    if (el && typeof el.getTotalLength === "function") setLen(Math.ceil(el.getTotalLength()) + 10);
  }, [key]);
  useEffect(() => setHover(null), [key]);

  const pick = (e: PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? n - 1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n - 1) - 1));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  const ticks = n >= 3 ? [0, Math.floor((n - 1) / 2), n - 1] : points.map((_, i) => i);
  const gridValues = [max - (max - min) * 0.25, max - (max - min) * 0.5, max - (max - min) * 0.75];
  const h = hover !== null ? points[hover] : null;
  const first = points[0];
  const last = points[n - 1];
  const delta = first && last ? last.value - first.value : 0;
  const hostRect = hostRef.current?.getBoundingClientRect();

  if (n === 0) return <p className="muted">Pas encore de données sur cette période.</p>;
  return (
    <div className="chart-host" ref={hostRef}>
      <svg
        className="chart"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        tabIndex={0}
        aria-label={`${label} : du ${shortDate(first!.date)} au ${shortDate(last!.date)}, ${format(first!.value)} puis ${format(last!.value)} (${delta >= 0 ? "+" : "−"}${format(Math.abs(delta))}). Flèches pour parcourir.`}
        onPointerMove={pick}
        onPointerDown={pick}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKey}
      >
        {gridValues.map((v) => <line key={v} className="grid-line" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />)}
        {min < 0 && max > 0 && <line className="zero" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />}
        <path d={area} className="area" key={`a${key}`} />
        <path d={line} className="line draw" ref={pathRef} key={`l${key}`} style={{ strokeDasharray: len, ["--len" as string]: len }} />
        {markers.map((m) => {
          const i = points.findIndex((p) => p.date === m.date);
          return i >= 0 ? <line key={m.date} className="today-mark" x1={x(i)} x2={x(i)} y1={PAD.top} y2={H - PAD.bottom} /> : null;
        })}
        {h && hover !== null && (
          <>
            <line className="cursor" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} />
            <circle className="dot" cx={x(hover)} cy={y(h.value)} r={5} />
          </>
        )}
      </svg>
      {/* Les dates de l'axe en HTML, sous le dessin : elles gardent leur taille quand le SVG rétrécit au téléphone. */}
      <div className="chart-ticks" aria-hidden="true">
        {ticks.map((i) => <span key={i}>{shortDate(points[i]!.date)}</span>)}
      </div>
      {h && hover !== null && hostRect && (
        <div className="chart-tip" role="status" style={{ left: `${(x(hover) / W) * 100}%`, top: `${(y(h.value) / H) * 100}%` }}>
          {longDate(h.date)}
          <b>{format(h.value)}</b>
          {hover > 0 && <span>{h.value - points[hover - 1]!.value >= 0 ? "+" : "−"}{format(Math.abs(h.value - points[hover - 1]!.value))} ce jour</span>}
        </div>
      )}
    </div>
  );
}
