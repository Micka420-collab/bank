import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SalaryPowerView } from "./PurchasingPower";
import { PurchasingPower } from "./PurchasingPower";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const benchmark: SalaryPowerView["benchmark"] = {
  as_of: "2026-09", published_on: "2026-10-15", annual_pct: 3.1,
  source: "Insee", url: "https://www.insee.fr/fr/statistiques/9080209",
  status: "definitif", stale: false, note: "Version publiée",
};
const complete: SalaryPowerView = {
  available: true, benchmark, note: "Simulation informative",
  comparison: {
    ready: true,
    recent_months: ["2026-07","2026-08","2026-09"],
    comparison_months: ["2025-07","2025-08","2025-09"],
    salary_months_found: 6, current_salary_avg_cents: 205_000,
    previous_salary_avg_cents: 200_000,
    nominal_salary_change_pct: 2.5, real_salary_change_pct: -0.58,
    salary_to_keep_up_cents: 206_200, monthly_real_gap_cents: -1_200,
    current_spending_avg_cents: 112_000, previous_spending_avg_cents: 100_000,
    spending_change_pct: 12, spending_share_change_points: 4.63,
    spending_drivers: [{ category: "courses", current_avg_cents: 112_000,
      old_avg_cents: 100_000, delta_cents: 12_000 }],
    note: "Comparaison sur trois mois complets.",
  },
};
const yieldResult = {
  nominal_after_fees_and_tax_pct: 3.46, real_annual_pct: 0.35,
  assumptions: {gross_pct:6,fees_pct:1,tax_pct:30,inflation_pct:3.1},
  note: "Scénario théorique, aucun rendement futur garanti.",
};

function res(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

describe("salaire, inflation et vrai rendement des placements", () => {
  let host: HTMLDivElement;
  let root: Root;
  let requests: { url: string; init?: RequestInit }[];
  let data: SalaryPowerView;
  const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    requests = [];
    data = complete;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      requests.push({ url, init });
      return Promise.resolve(res(url.includes("/yield") ? yieldResult : data));
    });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it("labels wage rise but real purchasing-power loss and evidenced expense changes", async () => {
    await act(async () => root.render(<PurchasingPower />));
    await flush();
    expect(host.textContent).toContain("Salaire moyen");
    expect(host.textContent?.replace(/\\s/g, " ")).toContain("2 050");
    expect(host.textContent).toContain("−0,58 %");
    expect(host.textContent).toContain("12 %");
    expect(host.textContent).toContain("courses");
    expect(host.querySelector('a[href="https://www.insee.fr/fr/statistiques/9080209"]')).not.toBeNull();
    expect(requests).toHaveLength(1);
  });

  it("does not invent a salary trend when only five months are supported", async () => {
    data = { ...complete, comparison: {
      ...complete.comparison!, ready: false, salary_months_found: 5,
      current_salary_avg_cents: null, previous_salary_avg_cents: null,
      real_salary_change_pct: null, nominal_salary_change_pct: null, monthly_real_gap_cents: null,
    } };
    await act(async () => root.render(<PurchasingPower />));
    await flush();
    expect(host.textContent).toContain("5/6");
    expect(host.textContent).toContain("salaire nul");
    expect(host.textContent).not.toContain("2 050");
  });

  it("requires manual assumptions and sends them only after an explicit calculation", async () => {
    await act(async () => root.render(<PurchasingPower />));
    await flush();
    const inputs = [...host.querySelectorAll<HTMLInputElement>(".purchasing-power-yield input")];
    expect(inputs).toHaveLength(4);
    const button = host.querySelector<HTMLButtonElement>(".purchasing-power-yield button[type=submit]")!;
    expect(button.disabled).toBe(true);
    const set = async (el: HTMLInputElement, value: string) => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await set(inputs[0]!, "6");
    await set(inputs[1]!, "1");
    await set(inputs[2]!, "30");
    expect(button.disabled).toBe(false);
    expect(requests).toHaveLength(1);
    await act(async () => { host.querySelector<HTMLFormElement>(".purchasing-power-yield form")!.requestSubmit(); });
    await flush();
    const post = requests.find((x) => x.url.includes("/yield"));
    expect(post).toBeDefined();
    expect(JSON.parse(String(post!.init?.body)).gross_pct).toBe(6);
    expect(JSON.parse(String(post!.init?.body)).inflation_pct).toBe(3.1);
    expect(host.textContent).toContain("Scénario théorique");
  });
});
