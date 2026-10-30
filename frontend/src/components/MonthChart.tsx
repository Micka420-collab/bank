// La courbe du mois sur papier Seyès : trait plein = solde réel, pointillé = projection,
// ligne rouge = zéro. Ce qui passe sous la ligne est teinté : un découvert se voit avant d'arriver.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Point, UpcomingEvent } from "../api";
import { dayNumber, money, shortDate } from "../format";

type Props = {
  history: Point[];
  projection: Point[];
  events: UpcomingEvent[];
  lowestDate: string;
  lowestCents: number;
  daysInMonth: number;
  scenarioProjection?: Point[]; // second tracé : hypothèse locale, jamais la prévision officielle
  bare?: boolean; // sans cadre : la carte qui l'héberge fournit le sien
};

const PAD = { l: 8, r: 8, t: 22, b: 26 };

/** Largeur réelle du conteneur : le SVG est dessiné à l'échelle 1, les textes gardent leur taille sur mobile. */
function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(760);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, Math.round(entry.contentRect.width)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export function MonthChart({ history, projection, events, lowestDate, lowestCents, daysInMonth, scenarioProjection, bare = false }: Props) {
  const [wrapRef, W] = useWidth<HTMLElement>();
  const H = Math.round(Math.min(280, Math.max(200, W * 0.34)));
  const geo = useMemo(() => {
    const all = [...history, ...projection, ...(scenarioProjection ?? [])];
    const values = all.map((p) => p.balance_cents);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = Math.max(1, max - min);
    const lo = min - span * 0.08;
    const hi = max + span * 0.12;
    const x = (iso: string) => PAD.l + ((Number(dayNumber(iso)) - 1) / Math.max(1, daysInMonth - 1)) * (W - PAD.l - PAD.r);
    const y = (cents: number) => PAD.t + (1 - (cents - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
    const path = (pts: Point[]) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.balance_cents).toFixed(1)}`).join("");
    return { x, y, path, zeroY: y(0) };
  }, [history, projection, scenarioProjection, daysInMonth, W, H]);

  if (projection.length === 0) return null;
  const today = projection[0]!;
  const scenarioLow = scenarioProjection?.length
    ? scenarioProjection.reduce((acc, p) => (p.balance_cents < acc.balance_cents ? p : acc))
    : null;
  const pastPath = geo.path([...history, today]);
  const futurePath = geo.path(projection);
  const all = [...history, ...projection];
  const first = all[0]!;
  const last = all[all.length - 1]!;
  // Zone sous zéro : polygone entre la courbe et la ligne du zéro, clippé au demi-plan inférieur.
  const area = `${geo.path(all)}L${geo.x(last.date)},${geo.zeroY}L${geo.x(first.date)},${geo.zeroY}Z`;
  const seyesMajor = 8;
  const rows = Array.from({ length: Math.ceil(H / seyesMajor) }, (_, i) => i * seyesMajor);
  const cols = Array.from({ length: Math.floor(W / 32) + 1 }, (_, i) => i * 32);
  const lowX = geo.x(lowestDate);
  const lowY = geo.y(lowestCents);
  const labelAnchor = lowX > W * 0.75 ? "end" : lowX < W * 0.25 ? "start" : "middle";
  const ticks = W < 480 ? [1, 15, daysInMonth] : [1, 10, 20, daysInMonth];

  return (
    <figure className={bare ? "chart-bare" : "chart-wrap"} aria-label="Évolution du solde ce mois-ci" ref={wrapRef}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby="chart-desc">
        <desc id="chart-desc">
          Solde le plus bas prévu : {money(lowestCents)} le {shortDate(lowestDate)}. Solde aujourd'hui :{" "}
          {money(today.balance_cents)}.
          {scenarioLow && ` Scénario testé (courbe en pointillés) : point bas ${money(scenarioLow.balance_cents)} le ${shortDate(scenarioLow.date)}.`}
        </desc>
        <defs>
          <clipPath id="below-zero">
            <rect x="0" y={geo.zeroY} width={W} height={H} />
          </clipPath>
        </defs>
        {rows.filter((r) => r % (bare ? 64 : 32) === 0).map((r) => (
          <line key={`h${r}`} x1="0" x2={W} y1={r} y2={r} className="grid-line" />
        ))}
        {!bare && cols.filter((c) => c % 64 === 0).map((c) => (
          <line key={`v${c}`} x1={c} x2={c} y1="0" y2={H} className="grid-line" />
        ))}
        {bare && ticks.map((d) => {
          const iso = `${today.date.slice(0, 7)}-${String(d).padStart(2, "0")}`;
          return <line key={`t${d}`} x1={geo.x(iso)} x2={geo.x(iso)} y1={PAD.t} y2={H - PAD.b} className="grid-line" />;
        })}
        <path d={area} className="below" clipPath="url(#below-zero)" />
        <line x1="0" x2={W} y1={geo.zeroY} y2={geo.zeroY} className="zero" />
        <text x={W - 2} y={geo.zeroY - 5} textAnchor="end">0 €</text>
        <line x1={geo.x(today.date)} x2={geo.x(today.date)} y1={PAD.t - 12} y2={H - PAD.b} className="today-mark" />
        <text x={geo.x(today.date)} y={PAD.t - 14} textAnchor="middle">
          aujourd'hui
        </text>
        {history.length > 0 && <path d={pastPath} className="past" />}
        <path d={futurePath} className="future" />
        {scenarioProjection && scenarioProjection.length > 0 && <path d={geo.path(scenarioProjection)} className="scenario-line" />}
        {events.map((e, i) => (
          <circle
            key={`${e.date}-${i}`}
            cx={geo.x(e.date)}
            cy={geo.y(projection.find((p) => p.date === e.date)?.balance_cents ?? 0)}
            r="4.5"
            className={e.amount_cents > 0 ? "event-in" : "event-out"}
          >
            <title>
              {`${shortDate(e.date)}, ${e.label} : ${money(e.amount_cents, { signed: true })}`}
            </title>
          </circle>
        ))}
        <circle cx={lowX} cy={lowY} r="5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <text x={lowX} y={lowY + 20} textAnchor={labelAnchor} className="low-label">
          point bas {money(lowestCents, { whole: true })}
        </text>
        {ticks.map((d) => (
          <text
            key={d}
            x={PAD.l + ((d - 1) / Math.max(1, daysInMonth - 1)) * (W - PAD.l - PAD.r)}
            y={H - 6}
            textAnchor={d === 1 ? "start" : d === daysInMonth ? "end" : "middle"}
          >
            {d}
          </text>
        ))}
      </svg>
      <figcaption className="legend">
        <span>solde réel</span>
        <span className="l-future">projection actuelle</span>
        {scenarioProjection && scenarioProjection.length > 0 && <span className="l-scenario">scénario testé</span>}
        <span className="l-zero">zéro</span>
      </figcaption>
    </figure>
  );
}
