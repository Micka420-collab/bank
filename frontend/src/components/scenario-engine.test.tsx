// Le simulateur ne doit jamais confondre projection officielle et hypothèse locale.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { MonthForecast } from "../api";
import { ScenarioLab } from "./ScenarioLab";
import { EMPTY_SCENARIO, simulateMonth, type ScenarioInput } from "./scenario-engine";

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

  it("ignores invalid amounts and invalid dates without producing NaN", () => {
    const result = simulateMonth(forecast, { expenseCents: Number.POSITIVE_INFINITY, expenseDate: "2027-11-30", savingPerDayCents: -100 });
    expect(result.active).toBe(false);
    expect(result.endBalanceCents).toBe(3000);
    const fallback = simulateMonth(forecast, { expenseCents: 1000, expenseDate: "not-a-date", savingPerDayCents: 0 });
    expect(fallback.projection.map((p) => p.balance_cents)).toEqual([4000, 3000, 2000]);
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

  function Demo() {
    const [value, onChange] = useState<ScenarioInput>(EMPTY_SCENARIO);
    return <ScenarioLab forecast={forecast} value={value} result={simulateMonth(forecast, value)} onChange={onChange} />;
  }

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
    expect(host.querySelectorAll(".scenario-fields label")).toHaveLength(3);
    expect(host.textContent).toContain("Rien n'est enregistré, envoyé ou exécuté.");
  });
});
