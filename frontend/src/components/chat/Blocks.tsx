// Blocs interactifs d'une réponse de l'assistant : boutons (relier, ouvrir, agir, question suivante), graphique
// dessiné avec les chiffres de Bank, schéma (boîtes et flèches, ou étapes), sources d'une recherche web. Tout
// arrive validé du serveur ; ici on dessine, on ne décide rien.

import { useMemo, useState } from "react";
import { api, type ChatActionItem, type ChatBlock } from "../../api";
import { money } from "../../format";
import { Bars } from "../charts/Bars";
import { Donut } from "../charts/Donut";
import { TrendChart } from "../charts/TrendChart";
import { Icon } from "../Icon";
import { useToast } from "../ui";

export type BlockHandlers = {
  onAsk: (prompt: string) => void;
  onNavigate: (page: string) => void;
  onActed?: () => void;
};

const PALETTE = ["#2f5bea", "#1f9d55", "#e0a100", "#c0392b", "#7b61ff", "#0f8b8d", "#d35400", "#8a91ac"];

function unitFormat(unit: string, v: number, whole = true): string {
  if (unit === "eur") return money(v, { whole });
  if (unit === "pct") return `${v} %`;
  if (unit === "kwh") return `${v.toLocaleString("fr-FR")} kWh`;
  return v.toLocaleString("fr-FR");
}

export function ChatBlocks({ blocks, handlers }: { blocks: ChatBlock[]; handlers: BlockHandlers }) {
  if (blocks.length === 0) return null;
  return (
    <div className="chat-blocks">
      {blocks.map((b, i) => {
        if (b.type === "actions") return <Actions key={i} items={b.items} handlers={handlers} />;
        if (b.type === "chart") return <ChatChart key={i} block={b} />;
        if (b.type === "diagram") return <Diagram key={i} block={b} />;
        if (b.type === "sources") return <Sources key={i} block={b} />;
        return <p key={i} className={`chat-note faint${b.tone === "warning" ? " is-warning" : ""}`}>{b.text}</p>;
      })}
    </div>
  );
}

// ------------------------------------------------------------------ boutons
function Actions({ items, handlers }: { items: ChatActionItem[]; handlers: BlockHandlers }) {
  const toast = useToast();
  const [done, setDone] = useState<Record<string, "busy" | "done" | "failed">>({});
  const buttons = items.filter((it) => it.kind !== "ask");
  const asks = items.filter((it): it is Extract<ChatActionItem, { kind: "ask" }> => it.kind === "ask");
  const run = async (it: Extract<ChatActionItem, { kind: "act" }>) => {
    if (done[it.key] === "busy" || done[it.key] === "done") return;
    setDone((d) => ({ ...d, [it.key]: "busy" }));
    try {
      await api(`/api/initiatives/${encodeURIComponent(it.card)}/act`, { method: "POST", body: { action: it.action } });
      setDone((d) => ({ ...d, [it.key]: "done" }));
      toast("Fait.");
      handlers.onActed?.();
    } catch (e) {
      setDone((d) => ({ ...d, [it.key]: "failed" }));
      toast((e as Error).message);
    }
  };
  return (
    <div className="chat-actions" role="group" aria-label="Actions proposées">
      {buttons.length > 0 && (
        <div className="chat-buttons">
          {buttons.map((it) =>
            it.kind === "navigate" ? (
              <button key={it.key} type="button" className="btn sm" onClick={() => handlers.onNavigate(it.page)}>
                {it.label} <Icon name="arrow" />
              </button>
            ) : (
              <button key={it.key} type="button" className={`btn sm secondary${done[it.key] === "done" ? " is-done" : ""}`} disabled={done[it.key] === "busy" || done[it.key] === "done"} onClick={() => void run(it)}>
                {done[it.key] === "done" ? <Icon name="check" /> : null} {it.label}
              </button>
            ),
          )}
        </div>
      )}
      {asks.length > 0 && (
        <div className="chat-followups">
          {asks.map((it) => (
            <button key={it.key} type="button" className="pill" onClick={() => handlers.onAsk(it.prompt)}>{it.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ graphiques
function ChatChart({ block }: { block: Extract<ChatBlock, { type: "chart" }> }) {
  const { kind, items, unit } = block;
  let body: React.ReactNode;
  if (kind === "donut") {
    const total = items.reduce((s, it) => s + Math.max(0, it.value ?? 0), 0);
    const slices = items.map((it, i) => ({ slug: `${i}`, name: it.label, color: it.color ?? PALETTE[i % PALETTE.length]!, cents: it.value ?? 0, share: total ? Math.max(0, it.value ?? 0) / total : 0 }));
    body = <Donut slices={slices} total={total} label={block.title} centerLabel={unit === "eur" ? "dépensé" : "total"} />;
  } else if (kind === "pairs") {
    body = <Bars periods={items.map((it, i) => ({ key: `${i}`, label: it.label, income_cents: it.a ?? 0, spent_cents: it.b ?? 0 }))} />;
  } else if (kind === "line") {
    body = <TrendChart points={items.map((it) => ({ date: it.label, value: it.value ?? 0 }))} label={block.title} format={(v) => unitFormat(unit, v)} baseline="zero" />;
  } else {
    body = <SimpleBars items={items} unit={unit} />;
  }
  return (
    <figure className="chat-chart">
      <figcaption><b>{block.title}</b></figcaption>
      {body}
      {block.source && <p className="faint chat-chart-source">{block.source}</p>}
    </figure>
  );
}

/** Une série : colonnes quand il y a beaucoup de points (des mois), lignes horizontales sinon (des catégories). */
export function SimpleBars({ items, unit }: { items: { label: string; value?: number }[]; unit: string }) {
  const values = items.map((it) => it.value ?? 0);
  const max = Math.max(1, ...values.map((v) => Math.abs(v)));
  if (items.length === 0) return <p className="muted">Rien à montrer.</p>;
  if (items.length >= 7) {
    return (
      <div className="chat-columns" role="img" aria-label={items.map((it) => `${it.label} : ${unitFormat(unit, it.value ?? 0)}`).join(" ; ")}>
        {items.map((it, i) => {
          const v = it.value ?? 0;
          return (
            <div className={`chat-column${v < 0 ? " is-negative" : ""}`} key={i} title={`${it.label} : ${unitFormat(unit, v)}`}>
              <div className="chat-column-bar"><i style={{ height: `${(Math.abs(v) / max) * 100}%` }} /></div>
              <span>{it.label.split(" ")[0]}</span>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <ul className="chat-rows" aria-label="Valeurs">
      {items.map((it, i) => {
        const v = it.value ?? 0;
        return (
          <li key={i}>
            <span className="chat-row-label">{it.label}</span>
            <span className="chat-row-bar" aria-hidden="true"><i style={{ width: `${(Math.abs(v) / max) * 100}%` }} /></span>
            <span className="chat-row-value">{unitFormat(unit, v)}</span>
          </li>
        );
      })}
    </ul>
  );
}

// ------------------------------------------------------------------ schémas
type Node = { id: string; label: string; note?: string };
type Edge = { from: string; to: string; label?: string };

/** Couches d'un graphe orienté : la profondeur d'une boîte = le plus long chemin depuis une source
 * (les arêtes qui reviendraient en arrière sont ignorées pour le dessin). */
export function layers(nodes: Node[], edges: Edge[]): string[][] {
  const ids = nodes.map((n) => n.id);
  const depth = new Map<string, number>(ids.map((id) => [id, 0]));
  const incoming = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of edges) incoming.get(e.to)?.push(e.from);
  // Itération bornée : chaque passe pousse les cibles un cran plus loin que leurs sources ; un cycle s'arrête à n passes.
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false;
    for (const id of ids) {
      const d = Math.max(0, ...(incoming.get(id) ?? []).map((src) => (depth.get(src) ?? 0) + 1));
      if (d > (depth.get(id) ?? 0) && d < ids.length) { depth.set(id, d); changed = true; }
    }
    if (!changed) break;
  }
  const out: string[][] = [];
  for (const id of ids) {
    const d = depth.get(id) ?? 0;
    (out[d] ??= []).push(id);
  }
  return out.filter((row) => row.length > 0);
}

function wrap(text: string, max: number): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}

function Diagram({ block }: { block: Extract<ChatBlock, { type: "diagram" }> }) {
  if (block.kind === "steps") {
    return (
      <figure className="chat-diagram">
        {block.title && <figcaption><b>{block.title}</b></figcaption>}
        <ol className="chat-steps">
          {block.nodes.map((n) => (
            <li key={n.id}><b>{n.label}</b>{n.note && <span className="faint"> — {n.note}</span>}</li>
          ))}
        </ol>
        <p className="faint chat-chart-source">Schéma proposé par le modèle, à lire avec tes chiffres</p>
      </figure>
    );
  }
  return <FlowDiagram block={block} />;
}

const W = 360;
const BOX_H = 44;
const GAP_Y = 46;
const CHAR_W = 6.2;

function FlowDiagram({ block }: { block: Extract<ChatBlock, { type: "diagram" }> }) {
  const { nodes, edges } = block;
  const layout = useMemo(() => {
    const rows = layers(nodes, edges);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const pos = new Map<string, { x: number; y: number; w: number; lines: string[] }>();
    rows.forEach((row, r) => {
      const gap = 10;
      const w = Math.min(160, (W - gap * (row.length + 1)) / row.length);
      const maxChars = Math.max(8, Math.floor(w / CHAR_W));
      row.forEach((id, i) => {
        const x = gap + i * (w + gap) + (W - (row.length * (w + gap) + gap)) / 2 + gap / 2;
        pos.set(id, { x, y: r * (BOX_H + GAP_Y) + 4, w, lines: wrap(byId.get(id)?.label ?? id, maxChars) });
      });
    });
    const height = rows.length * (BOX_H + GAP_Y) - GAP_Y + 8;
    return { pos, height };
  }, [nodes, edges]);
  const label = `${block.title || "Schéma"} : ${edges.map((e) => `${nodes.find((n) => n.id === e.from)?.label} → ${nodes.find((n) => n.id === e.to)?.label}${e.label ? ` (${e.label})` : ""}`).join(" ; ")}`;
  return (
    <figure className="chat-diagram">
      {block.title && <figcaption><b>{block.title}</b></figcaption>}
      <svg viewBox={`0 0 ${W} ${layout.height}`} className="chat-flow" role="img" aria-label={label}>
        <defs>
          <marker id="chat-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const a = layout.pos.get(e.from);
          const b = layout.pos.get(e.to);
          if (!a || !b) return null;
          const x1 = a.x + a.w / 2;
          const y1 = a.y + BOX_H;
          const x2 = b.x + b.w / 2;
          const y2 = b.y;
          const forward = y2 > y1;
          const mid = (y1 + y2) / 2;
          const d = forward ? `M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}` : `M${x1},${a.y} C${x1 - 40},${a.y - 30} ${x2 - 40},${b.y + BOX_H + 30} ${x2},${b.y + BOX_H}`;
          return (
            <g key={i} className="chat-edge">
              <path d={d} fill="none" markerEnd="url(#chat-arrow)" />
              {e.label && <text x={(x1 + x2) / 2} y={mid - 4} textAnchor="middle">{e.label}</text>}
            </g>
          );
        })}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id);
          if (!p) return null;
          return (
            <g key={n.id} className="chat-node">
              <rect x={p.x} y={p.y} width={p.w} height={BOX_H} rx="8" />
              {p.lines.map((line, i) => (
                <text key={i} x={p.x + p.w / 2} y={p.y + BOX_H / 2 + (i - (p.lines.length - 1) / 2) * 12 + 4} textAnchor="middle">{line}</text>
              ))}
              {n.note && <title>{n.note}</title>}
            </g>
          );
        })}
      </svg>
      {nodes.some((n) => n.note) && (
        <ul className="chat-diagram-notes faint">
          {nodes.filter((n) => n.note).map((n) => <li key={n.id}><b>{n.label}</b> : {n.note}</li>)}
        </ul>
      )}
      <p className="faint chat-chart-source">Schéma proposé par le modèle, à lire avec tes chiffres</p>
    </figure>
  );
}

// ------------------------------------------------------------------ sources
function Sources({ block }: { block: Extract<ChatBlock, { type: "sources" }> }) {
  return (
    <section className="chat-sources" aria-label="Sources">
      <p className="faint"><Icon name="search" /> Sources{block.query ? ` · « ${block.query} »` : ""}</p>
      <ol>
        {block.items.map((s) => (
          <li key={s.n} value={s.n}>
            <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title} <Icon name="external" /></a>
            <span className="faint"> {s.host}{s.date ? ` · ${safeDate(s.date)}` : ""}</span>
            {s.snippet && <p className="chat-snippet">{s.snippet}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

function safeDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}
