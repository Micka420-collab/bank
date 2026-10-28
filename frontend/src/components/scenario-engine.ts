/** Simulation purement locale à partir de la prévision quotidienne du backend.
 * Aucune modification des opérations, aucun appel réseau, aucune promesse de résultat.
 * La dépense ponctuelle est prise en compte le jour choisi ; les économies sur les dépenses
 * variables commencent demain (le solde actuel inclut déjà la journée en cours).
 */
import type { MonthForecast, Point } from "../api";

export type ScenarioInput = {
  expenseCents: number;
  expenseDate: string;
  savingPerDayCents: number;
};

export type ScenarioResult = {
  projection: Point[];
  active: boolean;
  expenseCents: number;
  savingPerDayCents: number;
  totalSavedCents: number;
  endBalanceCents: number;
  differenceCents: number;
  lowestBalanceCents: number;
  lowestDate: string;
  overdraftDate: string | null;
};

export const EMPTY_SCENARIO: ScenarioInput = { expenseCents: 0, expenseDate: "", savingPerDayCents: 0 };

function safeCents(value: number, cap: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(cap, Math.round(value)));
}

function dayDiff(start: string, end: string): number {
  // Les dates ISO sont des jours civils, jamais des timestamps dans le fuseau du navigateur.
  const a = Date.parse(start + "T00:00:00Z");
  const b = Date.parse(end + "T00:00:00Z");
  return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.round((b - a) / 86400000)) : 0;
}

export function simulateMonth(forecast: MonthForecast, input: ScenarioInput): ScenarioResult {
  const original = forecast.projection;
  const start = original[0]?.date ?? forecast.today;
  const end = original[original.length - 1]?.date ?? start;
  const expenseCents = safeCents(input.expenseCents, 10_000_000);
  // Une économie ne peut dépasser les dépenses variables déjà incluses dans la projection.
  const savingPerDayCents = safeCents(input.savingPerDayCents, Math.max(0, forecast.daily_variable_rate_cents));
  const expenseDate = original.some((p) => p.date === input.expenseDate) ? input.expenseDate : start;
  const active = original.length > 0 && (expenseCents > 0 || savingPerDayCents > 0);
  const projection = original.map((p) => ({
    date: p.date,
    balance_cents: p.balance_cents +
      savingPerDayCents * dayDiff(start, p.date) -
      (p.date >= expenseDate ? expenseCents : 0),
  }));
  const low = projection.reduce<Point | null>(
    (acc, p) => acc === null || p.balance_cents < acc.balance_cents ? p : acc,
    null,
  );
  const endBalanceCents = projection[projection.length - 1]?.balance_cents ?? forecast.projected_end_balance_cents;
  return {
    projection,
    active,
    expenseCents,
    savingPerDayCents,
    totalSavedCents: savingPerDayCents * dayDiff(start, end),
    endBalanceCents,
    differenceCents: endBalanceCents - (original[original.length - 1]?.balance_cents ?? forecast.projected_end_balance_cents),
    lowestBalanceCents: low?.balance_cents ?? forecast.lowest_balance_cents,
    lowestDate: low?.date ?? forecast.lowest_balance_date,
    overdraftDate: projection.find((p) => p.balance_cents < 0)?.date ?? null,
  };
}
