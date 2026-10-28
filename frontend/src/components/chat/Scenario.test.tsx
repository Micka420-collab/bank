import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScenarioBlock } from "../../api";
import { ChatScenario, previewScenario } from "./Scenario";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const budget: ScenarioBlock = {
  type: "scenario",
  title: "Et si… ? Explore ton mois",
  source: "Bank · prévision du mois",
  daily_savings_cap_cents: 2_000,
  expense_cap_cents: 200_000,
  points: [
    { date: "2026-10-28", balance_cents: 10_000 },
    { date: "2026-10-29", balance_cents: 8_000 },
    { date: "2026-10-30", balance_cents: 6_000 },
    { date: "2026-10-31", balance_cents: 4_000 },
  ],
};

describe("simulation financière dans le chat", () => {
  let container: HTMLDivElement;
  let root: Root;
  const ask = vi.fn();

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    ask.mockReset();
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const render = () => act(async () => root.render(<ChatScenario block={budget} onAsk={ask} />));

  it("keeps the current balance unchanged, adds daily savings after today and subtracts an expense once", () => {
    const copy = budget.points.map((p) => ({ ...p }));
    const result = previewScenario(budget, {
      savingsPerDayCents: 1_000,
      unexpectedExpenseCents: 3_000,
      expenseDate: "2026-10-30",
    });
    expect(result.points.map((p) => p.balance_cents)).toEqual([10_000, 9_000, 5_000, 4_000]);
    expect(result.finalBalanceCents).toBe(4_000);
    expect(result.possibleSavingsCents).toBe(3_000);
    expect(result.differenceCents).toBe(0);
    expect(result.lowestBalanceCents).toBe(4_000);
    expect(budget.points).toEqual(copy);
  });

  it("clamps invalid values and flags the first predicted overdraft date", () => {
    const result = previewScenario(budget, {
      savingsPerDayCents: 50_000,
      unexpectedExpenseCents: 200_000,
      expenseDate: "2026-10-29",
    });
    expect(result.possibleSavingsCents).toBe(6_000);
    expect(result.points[0]!.balance_cents).toBe(10_000);
    expect(result.points[1]!.balance_cents).toBe(8_000 + 2_000 - 200_000);
    expect(result.overdraftDate).toBe("2026-10-29");
    const reset = previewScenario(budget, { savingsPerDayCents: NaN, unexpectedExpenseCents: -500, expenseDate: "bad" });
    expect(reset.finalBalanceCents).toBe(4_000);
    expect(reset.overdraftDate).toBeNull();
  });

  it("shows live bank and scenario curves, reacts to a surprise expense, never sends automatically", async () => {
    await render();
    expect(container.querySelector(".chat-scenario-chart svg")).not.toBeNull();
    expect(container.querySelector(".chat-scenario-results")?.textContent).toContain("40,00");
    const original = container.querySelector(".chat-scenario-original")?.getAttribute("d");
    const modifiedInitial = container.querySelector(".chat-scenario-simulated")?.getAttribute("d");
    expect(modifiedInitial).toBe(original);
    expect(ask).not.toHaveBeenCalled();

    const input = container.querySelector<HTMLInputElement>("#chat-scenario-expense")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "50");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector(".chat-scenario-results")?.textContent).toContain("10,00");
    expect(container.querySelector(".chat-scenario-warning")?.textContent).toContain("sous 0");
    expect(container.querySelector(".chat-scenario-original")?.getAttribute("d")).not.toBe(container.querySelector(".chat-scenario-simulated")?.getAttribute("d"));
    expect(ask).not.toHaveBeenCalled();
    await act(async () => {
      const button = [...container.querySelectorAll("button")].find((x) => x.textContent?.includes("En discuter avec Bank"))!;
      button.click();
    });
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("Dépense imprévue : 50,00");
    expect(ask.mock.calls[0]![0]).toContain("certitude");
  });
});
