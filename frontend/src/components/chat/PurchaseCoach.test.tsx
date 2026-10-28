import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PurchaseCoachBlock } from "../../api";
import { money } from "../../format";
import { ChatPurchaseCoach, purchaseReflection } from "./PurchaseCoach";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const card: PurchaseCoachBlock = {
  type: "purchase_coach",
  title: "Dois-je vraiment l'acheter ?",
  label: "Vélo",
  answer: "oui_mais",
  headline: "Oui, mais cela réduit ta marge de sécurité.",
  amount_cents: 35_000,
  before: {
    per_day_cents: 4_000, lowest_cents: 20_000, lowest_date: "2026-10-28",
    overdraft_date: null, end_balance_cents: 45_000,
  },
  after: {
    per_day_cents: 1_500, lowest_cents: 8_000, lowest_date: "2026-10-29",
    overdraft_date: null, end_balance_cents: 10_000,
  },
  best_date: "2026-10-30",
  paths: [{ label: "En épargnant", months: 2, note: "Sans toucher à la réserve." }],
  explanation: "L'achat réduit ta marge.",
  source: "Bank · prévision et plan d'épargne",
};

describe("coach d'achat dans le chat", () => {
  let host: HTMLDivElement;
  let root: Root;
  const ask = vi.fn();

  beforeEach(() => {
    ask.mockReset();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });
  const render = (data = card, disabled = false) => act(async () => {
    root.render(<ChatPurchaseCoach block={data} onAsk={ask} disabled={disabled} />);
  });
  const click = (node: HTMLElement) => act(async () => node.click());

  it("displays Bank's actual verdict and true before/after impact", async () => {
    await render();
    expect(host.querySelector(".chat-purchase-verdict")?.textContent).toContain("Possible, mais attention");
    expect(host.querySelector(".chat-purchase-head")?.textContent).toContain(money(35_000));
    expect(host.querySelector(".chat-purchase-metrics")?.textContent).toContain(money(20_000));
    expect(host.querySelector(".chat-purchase-metrics")?.textContent).toContain(money(8_000));
    expect(host.querySelector(".chat-purchase-paths")?.textContent).toContain("2 mois");
    expect(ask).not.toHaveBeenCalled();
  });

  it("reasons without pressuring the user, and sends answers only on button click", async () => {
    await render();
    expect(purchaseReflection("impulse", "today", true)).toContain("48 heures");
    const whys = host.querySelectorAll<HTMLInputElement>('.chat-purchase-choices input[name$="-why"]');
    expect(whys).toHaveLength(4);
    await click(whys[3]!);
    expect(host.querySelector(".chat-purchase-reflection")?.textContent).toContain("48 heures");
    expect(ask).not.toHaveBeenCalled();
    await click(host.querySelector<HTMLButtonElement>(".chat-purchase-buttons button")!);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("Nature de l'achat : impulsif");
    expect(ask.mock.calls[0]![0]).toContain("sans culpabiliser");
    expect(host.querySelector<HTMLButtonElement>(".chat-purchase-buttons button")?.disabled).toBe(true);
  });

  it("Raisonne-moi asks the assistant to challenge an unwise purchase with numbers, not guilt", async () => {
    await render({ ...card, after: { ...card.after, overdraft_date: "2026-10-30" } });
    expect(host.querySelector(".chat-scenario-warning")?.textContent).toContain("découvert");
    const buttons = [...host.querySelectorAll<HTMLButtonElement>(".chat-purchase-buttons button")];
    expect(buttons.map((b) => b.textContent).join(" ")).toContain("Raisonne-moi");
    expect(ask).not.toHaveBeenCalled();
    await click(buttons[1]!);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("Je veux que tu me raisonnes");
    expect(ask.mock.calls[0]![0]).toContain("fondés sur mon budget");
    expect(buttons[0]!.disabled).toBe(true);
    expect(buttons[1]!.disabled).toBe(true);
  });

  it("changing the price shows that the old verdict is stale and requests a fresh Bank calculation", async () => {
    await render();
    const input = host.querySelector<HTMLInputElement>('input[type="number"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "250");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(host.querySelector(".chat-purchase-caution")?.textContent).toContain("Recalcul nécessaire");
    expect(host.querySelector(".chat-purchase-verdict")?.textContent).toContain("Possible, mais attention");
    expect(host.querySelector(".chat-purchase-buttons button")?.textContent).toContain("Recalculer avec Bank");
    expect(ask).not.toHaveBeenCalled();
    await click(host.querySelector<HTMLButtonElement>(".chat-purchase-buttons button")!);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask.mock.calls[0]![0]).toContain("Recalcule avec le prix de 250.00 €");
  });

  it("disables selections and button while assistant is responding", async () => {
    await render(card, true);
    expect(host.querySelector<HTMLInputElement>('input[type="radio"]')?.closest("fieldset")?.disabled).toBe(true);
    expect(host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.closest("fieldset")?.disabled).toBe(true);
    expect(host.querySelector<HTMLButtonElement>(".chat-purchase-buttons button")?.disabled).toBe(true);
  });

  it("distinguishes financially unaffordable purchases from needs or preferences", async () => {
    await render({ ...card, answer: "hors_de_portee", headline: "Pas raisonnable aujourd'hui" });
    expect(host.querySelector(".chat-purchase-verdict")?.textContent).toContain("Déconseillé aujourd'hui");
    expect(host.querySelector(".chat-purchase-reflection")?.textContent).toContain("achat plaisir");
    expect(ask).not.toHaveBeenCalled();
  });
});
