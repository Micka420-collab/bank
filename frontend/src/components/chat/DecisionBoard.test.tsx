import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionBoardBlock } from "../../api";
import { money } from "../../format";
import { ChatDecisionBoard, profileSaving } from "./DecisionBoard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const board: DecisionBoardBlock = {
  type: "decision_board", title: "Mon plan budgétaire interactif",
  source: "Bank · projection",
  daily_savings_cap_cents: 2_000, expense_cap_cents: 200_000,
  points: [
    { date: "2026-10-28", balance_cents: 10_000 },
    { date: "2026-10-29", balance_cents: 8_000 },
    { date: "2026-10-30", balance_cents: 6_000 },
    { date: "2026-10-31", balance_cents: 4_000 },
  ],
};

describe("sondage, courbe et plan liés dans le chat", () => {
  let element: HTMLDivElement;
  let root: Root;
  const ask = vi.fn();

  beforeEach(() => {
    ask.mockReset();
    element = document.createElement("div");
    document.body.appendChild(element);
    root = createRoot(element);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    element.remove();
  });
  const render = (disabled = false) => act(async () => {
    root.render(<ChatDecisionBoard block={board} disabled={disabled} onAsk={ask} />);
  });
  const click = (target: HTMLElement) => act(async () => { target.click(); });

  it("calculates bound profiles and does not mutate the Bank source points", () => {
    expect(profileSaving(2_000, "observe")).toBe(0);
    expect(profileSaving(2_000, "progressive")).toBe(500);
    expect(profileSaving(2_000, "focused")).toBe(1_000);
    expect(profileSaving(NaN, "focused")).toBe(0);
  });

  it("selecting the poll instantly synchronizes chart, figures and checklist without sending", async () => {
    await render();
    const radios = element.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    expect(radios).toHaveLength(3);
    expect(element.querySelector(".chat-board-choices")?.textContent).toContain("Progressif · 25 %");
    expect(element.querySelector(".chat-board-metrics")?.textContent).toContain(money(4_000));
    const original = element.querySelector(".chat-scenario-original")?.getAttribute("d");
    expect(element.querySelector(".chat-scenario-simulated")?.getAttribute("d")).toBe(original);
    expect(ask).not.toHaveBeenCalled();
    await click(radios[1]!);
    expect(radios[1]!.checked).toBe(true);
    expect(element.querySelector(".chat-board-metrics")?.textContent).toContain(money(5_500));
    expect(element.querySelector(".chat-scenario-simulated")?.getAttribute("d")).not.toBe(original);
    expect(element.querySelector(".chat-board-plan")?.textContent).toContain("Fixer un repère quotidien");
    expect(ask).not.toHaveBeenCalled();

    const checkbox = element.querySelector<HTMLInputElement>('.chat-board-plan input[type="checkbox"]')!;
    await click(checkbox);
    expect(element.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("1");
    expect(ask).not.toHaveBeenCalled();
    await click(radios[2]!);
    expect(element.querySelector(".chat-board-metrics")?.textContent).toContain(money(7_000));
    expect(element.querySelector(".chat-board-plan")?.textContent).toContain("Préserver une marge");
    expect(element.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("0");
    expect(ask).not.toHaveBeenCalled();
  });

  it("only sends after explicit confirmation and can retry after changing preferences", async () => {
    await render();
    const radios = element.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    await click(radios[1]!);
    await click(element.querySelector<HTMLInputElement>('.chat-board-plan input[type="checkbox"]')!);
    const button = element.querySelector<HTMLButtonElement>(".chat-board-footer button")!;
    await click(button);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("Progressif · 25 %");
    expect(ask.mock.calls[0]![0]).toContain("Étapes cochées : 1 sur 3");
    expect(button.disabled).toBe(true);
    await click(radios[2]!);
    expect(button.disabled).toBe(false);
    await click(button);
    expect(ask).toHaveBeenCalledTimes(2);
    expect(ask.mock.calls[1]![0]).toContain("Intensif · 50 %");
  });

  it("disables all inputs and submission during a running assistant response", async () => {
    await render(true);
    expect(element.querySelector<HTMLInputElement>('input[type="radio"]')?.disabled).toBe(true);
    expect(element.querySelector<HTMLInputElement>('.chat-board-plan input[type="checkbox"]')?.disabled).toBe(true);
    expect(element.querySelector<HTMLButtonElement>(".chat-board-footer button")?.disabled).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });
});
