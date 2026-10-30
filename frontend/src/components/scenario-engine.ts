/** Simulation purement locale à partir de la prévision quotidienne du backend.
 * Aucune modification des opérations, aucun appel réseau, aucune promesse de résultat.
 * La dépense ponctuelle est prise en compte le jour choisi ; les économies sur les dépenses
 * variables commencent demain (le solde actuel inclut déjà la journée en cours).
 * Ce n'est pas le moteur de décision de Bank (« Je peux m'acheter… ? », qui rejoue la prévision avec la réserve de
 * sécurité et l'enveloppe de la catégorie) : un aperçu instantané, dont le résultat dit s'il passe sous la réserve.
 */
import type { MonthForecast, Point } from "../api";

export type ScenarioInput = {
  expenseCents: number;
  expenseDate: string;
  savingPerDayCents: number;
};

export type ScenarioVerdict = "none" | "ok" | "under_buffer" | "overdraft";

export type ScenarioResult = {
  projection: Point[];
  active: boolean;
  expenseCents: number;
  /** La date effectivement simulée (aujourd'hui quand aucune n'est choisie). */
  expenseDate: string;
  /** La date choisie n'est pas un jour de la projection (saisie clavier, mois suivant, prévision rechargée) :
   *  la dépense n'est pas simulée tant qu'elle n'est pas corrigée, pour ne jamais afficher un chiffre qui ne
   *  correspond pas à l'hypothèse affichée. */
  dateInvalid: boolean;
  savingPerDayCents: number;
  totalSavedCents: number;
  endBalanceCents: number;
  differenceCents: number;
  lowestBalanceCents: number;
  lowestDate: string;
  overdraftDate: string | null;
  /** Le point bas passe sous la réserve de sécurité (`settings.buffer_cents`), comme le « serré » de Bank. */
  underBufferDate: string | null;
  verdict: ScenarioVerdict;
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

export function simulateMonth(forecast: MonthForecast, input: ScenarioInput, bufferCents = 0): ScenarioResult {
  const original = forecast.projection;
  const start = original[0]?.date ?? forecast.today;
  const end = original[original.length - 1]?.date ?? start;
  const wanted = safeCents(input.expenseCents, 10_000_000);
  // Une économie ne peut dépasser les dépenses variables déjà incluses dans la projection.
  const savingPerDayCents = safeCents(input.savingPerDayCents, Math.max(0, forecast.daily_variable_rate_cents));
  const dateInvalid = input.expenseDate !== "" && !original.some((p) => p.date === input.expenseDate);
  const expenseDate = input.expenseDate === "" || dateInvalid ? start : input.expenseDate;
  const expenseCents = dateInvalid ? 0 : wanted;
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
  const overdraftDate = projection.find((p) => p.balance_cents < 0)?.date ?? null;
  const buffer = Math.max(0, bufferCents);
  const underBufferDate = buffer > 0 ? projection.find((p) => p.balance_cents < buffer)?.date ?? null : null;
  const verdict: ScenarioVerdict = !active ? "none" : overdraftDate ? "overdraft" : underBufferDate ? "under_buffer" : "ok";
  return {
    projection,
    active,
    expenseCents,
    expenseDate,
    dateInvalid,
    savingPerDayCents,
    totalSavedCents: savingPerDayCents * dayDiff(start, end),
    endBalanceCents,
    differenceCents: endBalanceCents - (original[original.length - 1]?.balance_cents ?? forecast.projected_end_balance_cents),
    lowestBalanceCents: low?.balance_cents ?? forecast.lowest_balance_cents,
    lowestDate: low?.date ?? forecast.lowest_balance_date,
    overdraftDate,
    underBufferDate,
    verdict,
  };
}
