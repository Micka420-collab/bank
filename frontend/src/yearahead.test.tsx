// L'année qui vient : la phrase du point bas toujours visible, les douze barres, les pics avec leur source,
// un pic ajouté à la main envoyé tel quel, un pic détecté écarté d'un geste.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { YearAhead as YearAheadData } from "./api";
import { YearAhead } from "./components/YearAhead";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

function mockApi(handlers: Record<string, (init?: RequestInit) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    const out = handlers[key]!(init);
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");
const typeInto = (el: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});

const MONTHS = ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06", "2027-07", "2027-08", "2027-09", "2027-10"];
function view(over: Partial<YearAheadData> = {}): YearAheadData {
  return {
    start_month: "2026-11", start_balance_cents: 64_000,
    points: MONTHS.map((m, i) => ({ month: m, label: `m${i}`, income_cents: 215_000, fixed_cents: 95_000, variable_cents: 60_000, spikes_cents: m === "2027-01" ? 54_000 : 0, spikes: m === "2027-01" ? ["Maif Auto 540 €"] : [], end_balance_cents: 64_000 + (i + 1) * 60_000 - (i >= 2 ? 54_000 : 0) })),
    spikes: [
      { key: "MAIF AUTO:out", label: "Maif Auto", amount_cents: 54_000, day: "2027-01-15", cadence: "yearly", source: "detected", category: "assurances", occurrences: 2, confidence: 0.9 },
      { key: "custom:abc", label: "Taxe foncière", amount_cents: 90_000, day: "2027-10-15", cadence: "custom", source: "custom", category: null, occurrences: 0, confidence: 1 },
    ],
    ignored: [], income_monthly_cents: 215_000, fixed_monthly_cents: 95_000, variable_monthly_cents: 60_000,
    yearly_spikes_cents: 144_000, provision_monthly_cents: 12_000, lowest_month: "2026-11", lowest_label: "nov. 26", lowest_cents: 124_000,
    status: "ok", notes: [], savings_goal_cents: 0, buffer_cents: 10_000,
    custom: [{ key: "custom:abc", label: "Taxe foncière", amount_cents: 90_000, next_date: "2027-10-15", every_months: 12 }],
    disclaimer: "Un ordre de grandeur, pas une promesse.", ...over,
  };
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const render = async () => {
  await act(async () => root.render(<ToastProvider><YearAhead /></ToastProvider>));
  await settle();
};

describe("l'année qui vient", () => {
  it("dit le point bas en une phrase, dessine douze mois et liste les pics", async () => {
    mockApi({ "/api/forecast/year": () => view() });
    await render();
    expect(text()).toContain("Année sans découvert · point bas 1 240 € en nov. 26");
    expect([...host.querySelectorAll(".ya-label")].map((l) => l.textContent)).toEqual(["nov", "déc", "jan", "fév", "mar", "avr", "mai", "juin", "juil", "aoû", "sep", "oct"]);
    expect(text()).toContain("provision 120 €/mois");
    expect(host.querySelectorAll(".ya-month")).toHaveLength(12);
    expect(host.querySelectorAll(".ya-dot")).toHaveLength(1);
    expect(text()).toContain("Maif Auto");
    expect(text()).toContain("chaque année");
    expect(text()).toContain("Taxe foncière");
    expect(text()).toContain("Bank te propose de les réserver");
  });

  it("montre le rouge quand l'année passe sous zéro", async () => {
    mockApi({ "/api/forecast/year": () => view({ status: "risk", lowest_cents: -4_900, lowest_label: "juin 27" }) });
    await render();
    expect(text()).toContain("Sous zéro vers juin 27");
    expect(host.querySelector(".ya")?.getAttribute("data-status")).toBe("risk");
  });

  it("écarte un pic détecté et ajoute une dépense annuelle telle quelle", async () => {
    mockApi({
      "/api/forecast/year/ignore": () => view({ spikes: [], ignored: [view().spikes[0]!] }),
      "/api/forecast/year/spikes": () => view(),
      "/api/forecast/year": () => view(),
    });
    await render();
    await click("Écarter");
    expect(body(calls.find((c) => c.url === "/api/forecast/year/ignore")!)).toEqual({ key: "MAIF AUTO:out", ignored: true });
    await click("Ajouter");
    await typeInto(host.querySelector<HTMLInputElement>("input[placeholder='Taxe foncière']")!, "Assurance auto");
    const euro = host.querySelector<HTMLInputElement>(".ya-add input[inputmode='decimal']")!;
    await typeInto(euro, "600");
    await act(async () => { euro.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });  // le montant se fixe à la sortie du champ
    await typeInto(host.querySelector<HTMLInputElement>(".ya-add input[type=date]")!, "2026-12-15");
    await act(async () => { button("Ajouter à l'année").closest("form")!.requestSubmit(); });
    await settle();
    const posted = body(calls.find((c) => c.url === "/api/forecast/year/spikes" && c.init?.method === "POST")!);
    expect(posted).toEqual({ label: "Assurance auto", amount_cents: 60_000, next_date: "2026-12-15", every_months: 12 });
  });
  it("montre le solde d'impôt estimé par son mois, sans jour inventé ni bouton", async () => {
    const estimated = { key: "estimated:2027-09:Solde", label: "Solde d'impôt 2026 estimé", amount_cents: 24_000, day: "2027-09-25", cadence: "planned" as const, source: "estimated" as const, category: null, occurrences: 0, confidence: 1 };
    mockApi({ "/api/forecast/year": () => view({ spikes: [estimated] }) });
    await render();
    const row = [...host.querySelectorAll(".ya-spikes li")].find((li) => li.textContent?.includes("Solde d'impôt"));
    expect(row?.textContent).toContain("septembre 2027");
    expect(row?.textContent).toContain("estimation Bank");
    expect(row?.textContent).not.toContain("25/09");
    expect(row?.querySelector("button")).toBeNull();
  });
});
