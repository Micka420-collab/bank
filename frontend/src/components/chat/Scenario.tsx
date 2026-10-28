// Simulateur local du chat. Seules des projections Bank alimentent ce composant.
// Un curseur ne sauvegarde aucune donnée et n'agit pas sur les comptes.
import { useState } from "react";
import type { ScenarioBlock } from "../../api";
import { money, shortDate } from "../../format";
import { Icon } from "../Icon";

export type ScenarioInput = {
  savingsPerDayCents: number;
  unexpectedExpenseCents: number;
  expenseDate: string;
};
export type ScenarioPreview = {
  points: { date: string; balance_cents: number }[];
  finalBalanceCents: number;
  lowestBalanceCents: number;
  lowestDate: string;
  overdraftDate: string | null;
  possibleSavingsCents: number;
  differenceCents: number;
};

const numeric = (n: number, ceiling: number): number =>
  Number.isFinite(n) ? Math.max(0, Math.min(Math.round(n), ceiling)) : 0;

/** Une modification locale, indépendante de la prévision officielle. J0 reste le solde actuel. */
export function previewScenario(block: ScenarioBlock, input: ScenarioInput): ScenarioPreview {
  const points = block.points;
  const savings = numeric(input.savingsPerDayCents, block.daily_savings_cap_cents);
  const expense = numeric(input.unexpectedExpenseCents, block.expense_cap_cents);
  const date = points.slice(1).some((p) => p.date === input.expenseDate)
    ? input.expenseDate : points[1]?.date;
  let remainingExpense = expense;
  const adjusted = points.map((p, index) => {
    if (index === 0) return { ...p };
    if (p.date === date) remainingExpense = 0;
    return {
      ...p,
      balance_cents: p.balance_cents + savings * index - (remainingExpense === 0 ? expense : 0),
    };
  });
  const last = adjusted.at(-1);
  const low = adjusted.reduce((a, b) => b.balance_cents < a.balance_cents ? b : a, adjusted[0]!);
  const originalEnd = points.at(-1)?.balance_cents ?? 0;
  return {
    points: adjusted,
    finalBalanceCents: last?.balance_cents ?? 0,
    lowestBalanceCents: low.balance_cents,
    lowestDate: low.date,
    overdraftDate: adjusted.find((p) => p.balance_cents < 0)?.date ?? null,
    possibleSavingsCents: savings * Math.max(0, adjusted.length - 1),
    differenceCents: (last?.balance_cents ?? 0) - originalEnd,
  };
}

/** Deux chemins SVG superposés : prévision officielle / hypothèse en pointillé. */
function ScenarioChart({ original, hypothetical }: {
  original: ScenarioBlock["points"];
  hypothetical: ScenarioPreview["points"];
}) {
  const w = 420;
  const h = 168;
  const pad = { l: 14, r: 14, t: 15, b: 28 };
  const values = [...original, ...hypothetical].map((p) => p.balance_cents);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = Math.max(1, max - min);
  const y = (value: number) => pad.t + (1 - (value - min + span * 0.08) / (span * 1.16)) * (h - pad.t - pad.b);
  const x = (i: number) => pad.l + (i / Math.max(1, original.length - 1)) * (w - pad.l - pad.r);
  const path = (arr: typeof original) =>
    arr.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)} ${y(p.balance_cents).toFixed(2)}`).join(" ");
  return (
    <figure className="chat-scenario-chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Prévision Bank : ${money(original.at(-1)?.balance_cents ?? 0)} en fin de mois. Ton scénario : ${money(hypothetical.at(-1)?.balance_cents ?? 0)}.`}>
        <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} className="chat-scenario-zero" />
        <path d={path(original)} className="chat-scenario-original" />
        <path d={path(hypothetical)} className="chat-scenario-simulated" />
        <circle cx={x(hypothetical.length - 1)} cy={y(hypothetical.at(-1)?.balance_cents ?? 0)} r={4} className="chat-scenario-end" />
        <text x={pad.l} y={h - 6} className="chat-scenario-axis">{shortDate(original[0]!.date)}</text>
        <text x={w - pad.r} y={h - 6} textAnchor="end" className="chat-scenario-axis">{shortDate(original.at(-1)!.date)}</text>
      </svg>
      <figcaption className="chat-scenario-legend">
        <span><i className="chat-scenario-legend-real" />Prévision Bank</span>
        <span><i className="chat-scenario-legend-alt" />Ton scénario</span>
      </figcaption>
    </figure>
  );
}

export function ChatScenario({ block, onAsk, disabled = false }: {
  block: ScenarioBlock;
  onAsk: (prompt: string) => void;
  disabled?: boolean;
}) {
  const [savings, setSavings] = useState(0);
  const [expense, setExpense] = useState(0);
  const [expenseDate, setExpenseDate] = useState(block.points[1]?.date ?? "");
  const [sent, setSent] = useState(false);
  const cap = block.daily_savings_cap_cents;
  const maxExpenseEur = block.expense_cap_cents / 100;
  const input = { savingsPerDayCents: savings, unexpectedExpenseCents: expense, expenseDate };
  const result = previewScenario(block, input);
  const isActive = savings > 0 || expense > 0;
  const end = block.points.at(-1)?.balance_cents ?? 0;
  const talk = () => {
    if (disabled || sent) return;
    onAsk(
      `J'ai testé le simulateur « ${block.title} » de Bank. Dépense imprévue : ${money(expense)} ` +
      `le ${expenseDate}. Réduction hypothétique des dépenses quotidiennes : ${money(savings)} par jour. ` +
      `Solde prévu sans changement : ${money(end)} ; avec ce scénario : ${money(result.finalBalanceCents)} ; ` +
      `point bas : ${money(result.lowestBalanceCents)} le ${result.lowestDate}. ` +
      `Explique-moi ce scénario sans le prendre pour une certitude, et conseille-moi une prochaine étape.`,
    );
    setSent(true);
  };

  return (
    <section className="chat-scenario" aria-label={block.title}>
      <header className="chat-scenario-head">
        <span className="chat-interactive-icon"><Icon name="trendUp" /></span>
        <div>
          <span className="chat-interactive-type">Simulateur en direct</span>
          <h3>{block.title}</h3>
          <p className="muted">Modifie une hypothèse : la courbe et les résultats évoluent immédiatement.</p>
        </div>
      </header>
      <ScenarioChart original={block.points} hypothetical={result.points} />
      <div className="chat-scenario-fields">
        <div>
          <label htmlFor="chat-scenario-saving">Dépenser moins chaque jour : <b>{money(savings)}</b></label>
          <input id="chat-scenario-saving" type="range" min={0} max={Math.max(0, cap)} step={Math.max(1, Math.round(cap / 100))}
                 value={savings} disabled={disabled || cap === 0}
                 onChange={(e) => { setSavings(Number(e.target.value)); setSent(false); }} />
          <p className="faint">Plafonné au rythme de dépenses variables prévu par Bank : {money(cap)} par jour.</p>
        </div>
        <div className="chat-scenario-expense">
          <label htmlFor="chat-scenario-expense">Une dépense imprévue (€)</label>
          <input id="chat-scenario-expense" type="number" min={0} max={maxExpenseEur} step={1} inputMode="decimal"
                 value={expense / 100} disabled={disabled}
                 onChange={(e) => { setExpense(numeric(Number(e.target.value) * 100, block.expense_cap_cents)); setSent(false); }} />
          <label htmlFor="chat-scenario-date">À quelle date ?</label>
          <input id="chat-scenario-date" type="date" min={block.points[1]?.date} max={block.points.at(-1)?.date}
                 value={expenseDate} disabled={disabled || expense === 0}
                 onChange={(e) => { setExpenseDate(e.target.value); setSent(false); }} />
        </div>
      </div>
      <div className="chat-scenario-results" aria-live="polite">
        <div><span>Fin de mois envisagée</span><strong>{money(result.finalBalanceCents)}</strong></div>
        <div><span>Écart avec Bank</span><strong>{money(result.differenceCents, { signed: true })}</strong></div>
        <div><span>Point bas envisagé</span><strong>{money(result.lowestBalanceCents)}</strong><small>{shortDate(result.lowestDate)}</small></div>
      </div>
      {result.overdraftDate && <p className="chat-scenario-warning" role="status">
        <Icon name="alert" /> Ce scénario passe sous 0 € le {shortDate(result.overdraftDate)}.
      </p>}
      <div className="chat-scenario-foot">
        <p className="faint">{block.source}. {isActive ? "Simulation hypothétique, pas une économie réalisée." : "Déplace un curseur pour explorer."} Aucun compte n'est modifié.</p>
        <button type="button" className="btn sm" disabled={disabled || sent}
                onClick={talk}><Icon name="spark" /> {sent ? "Scénario envoyé" : "En discuter avec Bank"}</button>
      </div>
    </section>
  );
}
