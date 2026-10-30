// Le simulateur ne doit jamais confondre projection officielle et hypothèse locale.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { MonthForecast } from "./api";
import { MonthChart } from "./components/MonthChart";
import { ScenarioLab } from "./components/ScenarioLab";
import { EMPTY_SCENARIO, simulateMonth, type ScenarioInput } from "./components/scenario-engine";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const forecast: MonthForecast = {
  month: "2026-10",
  today: "2026-10-29",
  days_in_month: 31,
  days_left: 3,
  current_balance_cents: 5000,
  income_received_cents: 0,
  spent_fixed_cents: 0,
  spent_variable_cents: 0,
  saved_cents: 0,
  expected_income_cents: 0,
  expected_fixed_cents: 0,
  expected_variable_cents: -2000,
  projected_end_balance_cents: 3000,
  safe_to_spend_cents: 3000,
  safe_to_spend_per_day_cents: 1000,
  daily_variable_rate_cents: 1000,
  lowest_balance_cents: 3000,
  lowest_balance_date: "2026-10-31",
  overdraft_date: null,
  status: "ok",
  upcoming: [],
  history: [],
  budgets: [],
  allowance: [],
  projection: [
    { date: "2026-10-29", balance_cents: 5000 },
    { date: "2026-10-30", balance_cents: 4000 },
    { date: "2026-10-31", balance_cents: 3000 },
  ],
};

describe("simulation de fin de mois", () => {
  it("keeps the official projection untouched without a scenario", () => {
    const before = JSON.stringify(forecast);
    const result = simulateMonth(forecast, EMPTY_SCENARIO);
    expect(result.active).toBe(false);
    expect(result.projection).toEqual(forecast.projection);
    expect(result.endBalanceCents).toBe(3000);
    expect(JSON.stringify(forecast)).toBe(before);
  });

  it("applies a surprise expense only from the selected date", () => {
    const result = simulateMonth(forecast, { expenseCents: 5000, expenseDate: "2026-10-30", savingPerDayCents: 0 });
    expect(result.projection.map((p) => p.balance_cents)).toEqual([5000, -1000, -2000]);
    expect(result.overdraftDate).toBe("2026-10-30");
    expect(result.lowestDate).toBe("2026-10-31");
    expect(result.differenceCents).toBe(-5000);
  });

  it("starts daily savings tomorrow and can combine both levers", () => {
    const result = simulateMonth(forecast, { expenseCents: 5000, expenseDate: "2026-10-30", savingPerDayCents: 500 });
    expect(result.projection.map((p) => p.balance_cents)).toEqual([5000, -500, -1000]);
    expect(result.totalSavedCents).toBe(1000);
    expect(result.differenceCents).toBe(-4000);
  });

  it("caps hypothetical savings at existing variable spending", () => {
    const result = simulateMonth(forecast, { expenseCents: 0, expenseDate: "", savingPerDayCents: 3000 });
    expect(result.savingPerDayCents).toBe(1000);
    expect(result.endBalanceCents).toBe(5000);
    expect(result.overdraftDate).toBeNull();
    expect(result.totalSavedCents).toBe(2000);
  });

  it("ignores invalid amounts without producing NaN", () => {
    const result = simulateMonth(forecast, { expenseCents: Number.POSITIVE_INFINITY, expenseDate: "", savingPerDayCents: -100 });
    expect(result.active).toBe(false);
    expect(result.endBalanceCents).toBe(3000);
    expect(result.expenseDate).toBe("2026-10-29");
  });

  it("flags a date outside the forecast and does not simulate the expense on another day", () => {
    // Saisie clavier hors du mois, ou prévision rechargée après minuit : le chiffre affiché ne doit jamais
    // correspondre à une autre date que celle que la personne voit.
    for (const bad of ["2027-11-30", "not-a-date", "2026-10-28"]) {
      const result = simulateMonth(forecast, { expenseCents: 1000, expenseDate: bad, savingPerDayCents: 0 });
      expect(result.dateInvalid).toBe(true);
      expect(result.active).toBe(false);
      expect(result.expenseCents).toBe(0);
      expect(result.projection).toEqual(forecast.projection);
    }
    const savingsOnly = simulateMonth(forecast, { expenseCents: 1000, expenseDate: "2027-11-30", savingPerDayCents: 500 });
    expect(savingsOnly.dateInvalid).toBe(true);
    expect(savingsOnly.active).toBe(true);
    expect(savingsOnly.projection.map((p) => p.balance_cents)).toEqual([5000, 4500, 4000]);
  });

  it("says when the low point dips under the safety buffer, like Bank's « serré » verdict", () => {
    const ok = simulateMonth(forecast, { expenseCents: 500, expenseDate: "2026-10-31", savingPerDayCents: 0 }, 2000);
    expect(ok.verdict).toBe("ok");
    expect(ok.underBufferDate).toBeNull();
    const tight = simulateMonth(forecast, { expenseCents: 1500, expenseDate: "2026-10-30", savingPerDayCents: 0 }, 2000);
    expect(tight.verdict).toBe("under_buffer");
    expect(tight.underBufferDate).toBe("2026-10-31");
    expect(tight.overdraftDate).toBeNull();
    const red = simulateMonth(forecast, { expenseCents: 5000, expenseDate: "2026-10-30", savingPerDayCents: 0 }, 2000);
    expect(red.verdict).toBe("overdraft");
    expect(simulateMonth(forecast, EMPTY_SCENARIO, 2000).verdict).toBe("none");
    expect(simulateMonth(forecast, { expenseCents: 1500, expenseDate: "2026-10-30", savingPerDayCents: 0 }).underBufferDate).toBeNull();
  });

  it("supports empty projections without inventing an overdraft", () => {
    const result = simulateMonth({ ...forecast, projection: [] }, { expenseCents: 5000, expenseDate: "", savingPerDayCents: 100 });
    expect(result.active).toBe(false);
    expect(result.projection).toEqual([]);
    expect(result.endBalanceCents).toBe(3000);
    expect(result.overdraftDate).toBeNull();
  });
});

describe("laboratoire accessible", () => {
  let host: HTMLDivElement;
  let root: Root;

  function Demo({ data = forecast, buffer = 2000 }: { data?: MonthForecast; buffer?: number }) {
    const [value, onChange] = useState<ScenarioInput>(EMPTY_SCENARIO);
    return <ScenarioLab forecast={data} bufferCents={buffer} value={value} result={simulateMonth(data, value, buffer)} onChange={onChange} />;
  }
  const type = (input: HTMLInputElement, text: string) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  beforeEach(async () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => root.render(<Demo />));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it("reveals the controls progressively and updates the result from a preset", async () => {
    expect(host.querySelector("details")?.hasAttribute("open")).toBe(false);
    expect(host.querySelector("summary")?.textContent).toContain("Et si on changeait la suite ?");
    const button = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Facture surprise"));
    expect(button).toBeDefined();
    await act(async () => button!.click());
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("true");
    expect(host.textContent).toContain("Voilà ce qui changerait");
    expect(host.querySelector('[role="status"]')?.textContent).toContain("solde passerait sous zéro");
    const reset = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Réinitialiser"))!;
    await act(async () => reset.click());
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("false");
  });

  it("uses a date constrained to the actual forecast horizon and descriptive labels", () => {
    const date = host.querySelector<HTMLInputElement>('input[type="date"]')!;
    expect(date.min).toBe("2026-10-29");
    expect(date.max).toBe("2026-10-31");
    expect(date.disabled).toBe(true);
    const labels = [...host.querySelectorAll<HTMLLabelElement>(".scenario-fields label")];
    expect(labels).toHaveLength(3);
    for (const l of labels) expect(l.control).not.toBeNull();
    expect(host.textContent).toContain("Rien n'est enregistré, envoyé ou exécuté.");
    expect(host.textContent).toContain("« Je peux m'acheter… ? »");
  });

  it("accepts French amounts, can be emptied, and refuses nonsense without losing the field", async () => {
    const expense = host.querySelector<HTMLInputElement>(".scenario-fields input")!;
    await type(expense, "12,50");
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("true");
    expect(host.textContent).toContain("Voilà ce qui changerait");
    expect(expense.value).toBe("12,50");
    await type(expense, "");
    expect(expense.value).toBe("");
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("false");
    await type(expense, "abc");
    expect(expense.getAttribute("aria-invalid")).toBe("true");
    expect(host.querySelector(".field-error")?.textContent).toContain("montant en euros");
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("false");
  });

  it("flags a date outside the month instead of silently simulating today", async () => {
    const expense = host.querySelector<HTMLInputElement>(".scenario-fields input")!;
    await type(expense, "10");
    const date = host.querySelector<HTMLInputElement>('input[type="date"]')!;
    expect(date.disabled).toBe(false);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(date, "2026-11-15");
      date.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(date.getAttribute("aria-invalid")).toBe("true");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("entre le 29 oct");
    expect(host.querySelector("details")?.getAttribute("data-active")).toBe("false");
    expect(host.textContent).not.toContain("Voilà ce qui changerait");
    const reset = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Réinitialiser"))!;
    expect(reset.disabled).toBe(false);
  });

  it("tells when the scenario stays above or dips under the safety buffer, in a permanent live region", async () => {
    const status = host.querySelector('[role="status"]')!;
    expect(status.getAttribute("aria-atomic")).toBe("true");
    expect(status.textContent).toBe("");
    const expense = host.querySelector<HTMLInputElement>(".scenario-fields input")!;
    await type(expense, "5");
    expect(host.querySelector('[role="status"]')?.textContent).toContain("reste au-dessus de ta réserve de sécurité");
    await type(expense, "15");
    expect(host.querySelector('[role="status"]')?.textContent?.replace(/[\u202f\u00a0]/g, " ")).toContain("passe sous ta réserve de sécurité (20 €)");
    expect(host.querySelector('[role="status"]')?.className).toContain("warn");
  });

  it("explains the missing daily lever when Bank has no variable spending yet", async () => {
    await act(async () => root.unmount());
    root = createRoot(host);
    await act(async () => root.render(<Demo data={{ ...forecast, daily_variable_rate_cents: 0 }} />));
    expect(host.textContent).toContain("Pas encore assez d'opérations");
    expect(host.textContent).not.toContain("inclut 0 €");
    const presets = [...host.querySelectorAll<HTMLButtonElement>(".scenario-presets button")];
    expect(presets.map((b) => b.disabled)).toEqual([false, true, true]);
    const saving = host.querySelectorAll<HTMLInputElement>(".scenario-fields input")[2]!;
    expect(saving.disabled).toBe(true);
  });
});

describe("courbe du scénario", () => {
  it("draws the dashed scenario line, its legend, and describes it for screen readers", async () => {
    class RO { observe() {} unobserve() {} disconnect() {} }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= RO;
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const result = simulateMonth(forecast, { expenseCents: 5000, expenseDate: "2026-10-30", savingPerDayCents: 0 });
    const chart = (scenario?: typeof result.projection) => (
      <MonthChart history={[]} projection={forecast.projection} events={[]} lowestCents={3000} lowestDate="2026-10-31"
                  daysInMonth={31} scenarioProjection={scenario} bare />
    );
    await act(async () => root.render(chart()));
    expect(host.querySelector("path.scenario-line")).toBeNull();
    expect(host.textContent).not.toContain("scénario testé");
    await act(async () => root.render(chart(result.projection)));
    expect(host.querySelector("path.scenario-line")?.getAttribute("d")).toMatch(/^M/);
    expect(host.querySelector(".legend .l-scenario")?.textContent).toBe("scénario testé");
    expect(host.querySelector("desc")?.textContent?.replace(/[\u202f\u00a0]/g, " ")).toContain("Scénario testé (courbe en pointillés) : point bas -20,00 € le 31 oct");
    await act(async () => root.unmount());
    host.remove();
  });
});
