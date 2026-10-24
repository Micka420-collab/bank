// Ton logement : l'accord avant tout téléchargement, la description (type, surface, commune cherchée par nom ou code
// postal, section et pièces facultatives), l'estimation en fourchette avec ses ventes, sa période, sa source et ses
// dates, la ligne nette en face du prêt, la mise à jour bornée au trimestre et l'échec qui garde l'ancienne extraction.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HomeEstimate, HomeView } from "./api";
import { fullDate, HomeValue, medianPosition } from "./components/HomeValue";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[\u00a0\u202f]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const type = async (label: string, value: string) => {
  const input = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
};
const bodyOf = (url: string, method = "PUT") => JSON.parse(String(calls.find((c) => c.url === url && c.init?.method === method)?.init?.body ?? "null"));

const ESTIMATE: HomeEstimate = {
  ok: true, kind: "appartement", surface: 60, count: 8, window_start: "2023-06-30", window_end: "2025-06-30",
  message: "Estimation sur 8 ventes comparables.", median_cents: 19800000, low_cents: 17700000, high_cents: 21900000,
  median_m2_cents: 330000, low_m2_cents: 295000, high_m2_cents: 365000, first_sale: "2024-01-10", last_sale: "2025-05-02",
  tolerance_percent: 30, scope: "commune", rooms_used: false, outliers: 1, notes: ["1 vente hors norme écartée (prix au m² très éloigné des autres)."],
  code: "45234",
};
const BASE: HomeView = {
  consent: false, profile: null, estimate: null,
  data: { matches: false, fetched_at: null, latest_sale: null, sales: 0, years: [], fresh: false, next_refresh: "2027-01-01", error: null },
  in_wealth: false, loans: [], loans_remaining_cents: 0, net_cents: null,
  kinds: [{ key: "appartement", label: "Appartement" }, { key: "maison", label: "Maison" }],
  bounds: { surface: [9, 1000], rooms: [1, 20] },
  source: "DVF, Etalab / DGFiP, Licence Ouverte 2.0", source_url: "https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/",
  disclaimer: "Repère statistique tiré des ventes publiées, pas une expertise.",
};
const PROFILE = { kind: "appartement" as const, surface: 60, code: "45234", commune: "Orléans", section: null, rooms: null, in_wealth: true };
const DONE: HomeView = {
  ...BASE, consent: true, profile: PROFILE, estimate: ESTIMATE, in_wealth: true,
  data: { matches: true, fetched_at: "2026-10-24T09:00:00+00:00", latest_sale: "2025-06-30", sales: 12, years: [2023, 2024, 2025], fresh: true, next_refresh: "2027-01-01", error: null },
  loans: [{ id: 2, name: "Prêt immo", remaining_cents: 12000000 }], loans_remaining_cents: 12000000, net_cents: 7800000,
};

function mockApi(state: { st: HomeView; refresh?: { status: string; message: string } }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/home") return json(state.st);
    if (url === "/api/consents") return json([{ purpose: "home_value", title: "Valeur de ton logement (ventes DVF)", text: "Ces serveurs voient la commune demandée, jamais ton adresse.", granted: false, explicit: false, decided: false, updated_at: null, default: false }]);
    if (url === "/api/consents/home_value") { state.st = { ...state.st, consent: true }; return json({}); }
    if (url.startsWith("/api/home/communes")) return json([{ code: "45234", name: "Orléans", postcodes: ["45000", "45100"] }]);
    if (url === "/api/home/profile") { state.st = DONE; return json({ result: { status: "refreshed", message: "12 ventes" }, view: DONE }); }
    if (url === "/api/home/refresh") return json({ result: state.refresh, view: state.st });
    return json({}, 404);
  });
}

async function mount(onChange = () => {}) {
  await act(async () => { root.render(<ToastProvider><HomeValue onChange={onChange} /></ToastProvider>); });
  await settle();
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

describe("ton logement", () => {
  it("places the median inside the range", () => {
    expect(medianPosition(ESTIMATE)).toBe(50);
    expect(medianPosition({ low_cents: 100, median_cents: 100, high_cents: 100 })).toBe(50);
    expect(medianPosition({ low_cents: 0, median_cents: 75, high_cents: 100 })).toBe(75);
    expect(fullDate("2026-10-24T09:00:00+00:00")).toMatch(/24 oct\.? 2026/);
  });

  it("asks consent, then the description, searching the commune by name", async () => {
    const state = { st: BASE };
    mockApi(state);
    const changed = vi.fn();
    await mount(changed);
    expect(text()).toContain("jamais ton adresse");
    expect(text()).not.toContain("Chercher ta commune");
    await click("Oui, estimer avec les ventes DVF");
    expect(calls.some((c) => c.url === "/api/consents/home_value" && c.init?.method === "PUT")).toBe(true);
    expect(text()).toContain("Chercher ta commune");

    await type("Surface habitable", "60");
    await type("Chercher ta commune", "Orléans");
    await click("Chercher");
    expect(calls.some((c) => c.url === "/api/home/communes?q=Orl%C3%A9ans")).toBe(true);
    await click("Orléans");
    await type("Section cadastrale", "ab");
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    await settle();
    expect(bodyOf("/api/home/profile")).toEqual({ kind: "appartement", surface: 60, code: "45234", commune: "Orléans", section: "AB", rooms: null, in_wealth: true });
    expect(changed).toHaveBeenCalled();
    expect(text()).toContain("198 000 €");
  });

  it("shows the range, the sales, the period, the source and the dates, with the loan in front", async () => {
    mockApi({ st: DONE });
    await mount();
    const t = text();
    expect(t).toContain("198 000 €");
    expect(t).toContain("fourchette 177 000 € – 219 000 €");
    expect(t).toContain("8 ventes comparables (toute la commune, surface ± 30 %)");
    expect(t).toMatch(/du 10 janv\.? 2024 au 2 mai 2025/);
    expect(t).toContain("1 vente hors norme écartée");
    expect(t).toContain("DVF, Etalab / DGFiP, Licence Ouverte 2.0");
    expect(t).toMatch(/extraction du 24 oct\.? 2026/);
    expect(t).toMatch(/vente la plus récente : 30 juin 2025/);
    expect(t).toContain("Logement (estimation DVF)198 000 €");
    expect(t).toContain("Prêt immo · restant dû−120 000 €");
    expect(t).toContain("Net78 000 €");
    expect(host.querySelector(".home-range")?.getAttribute("aria-label")?.replace(/[\u00a0\u202f]/g, " ")).toContain("médiane 198 000 €");
    expect(button("À jour jusqu'au").disabled).toBe(true);
  });

  it("says when there are not enough sales instead of inventing", async () => {
    mockApi({ st: { ...DONE, estimate: { ...ESTIMATE, ok: false, count: 2, median_cents: 0, message: "Pas assez de ventes comparables : 2 ventes de maison de taille proche en 24 mois (il en faut 5)." } } });
    await mount();
    expect(text()).toContain("Pas assez de ventes comparables");
    expect(text()).not.toContain("fourchette");
  });

  it("a failed refresh keeps the previous extraction and says so", async () => {
    const stale = { ...DONE, data: { ...DONE.data, fresh: false, error: "La source répond 503" } };
    mockApi({ st: stale, refresh: { status: "failed", message: "Téléchargement DVF en échec : La source répond 503. L'estimation garde l'extraction du 24/10/2026." } });
    await mount();
    expect(text()).toContain("Dernier essai en échec : La source répond 503");
    await click("Mettre à jour les ventes");
    expect(calls.some((c) => c.url === "/api/home/refresh" && c.init?.method === "POST")).toBe(true);
    expect(text()).toContain("garde l'extraction du 24/10/2026");
    expect(text()).toContain("198 000 €");
  });
});
