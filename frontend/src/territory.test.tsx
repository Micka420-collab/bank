// Réglages › Ton adresse et ton calendrier (#32) : l'accord avant que l'adresse parte, l'adresse située (commune,
// code INSEE, coordonnées, source et date), les jours fériés et les vacances de l'académie avec leurs sources, la
// relève ratée qui garde les données ; dans l'année qui vient, les fériés et vacances mois par mois.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { YearPoint } from "./api";
import { labelAction } from "./audit-labels";
import { Territory, breakText, precisionText, type TerritoryView } from "./components/Territory";
import { ToastProvider } from "./components/ui";
import { YearCalendar } from "./components/YearAhead";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };

const EMPTY: TerritoryView = {
  consents: { geocoding: false, calendar: false }, place: null, zone: "metropole", zone_label: "métropole",
  holidays: [], school: null, sources: [], school_page: "https://www.education.gouv.fr/calendrier-scolaire-100148",
};
const PLACE = {
  query: "Place de l'Hôtel de Ville 80000 Amiens", label: "Place de l'Hôtel de Ville 80000 Amiens", city: "Amiens",
  citycode: "80021", postcode: "80000", depcode: "80", lat: 49.893238, lon: 2.295394, score: 0.97, precision: "street",
  academy: "Amiens", geocoded_at: "2026-10-24T09:00:00+00:00", source: "Géoplateforme (IGN), service de géocodage",
  source_url: "https://data.geopf.fr/geocodage/search",
};
const FULL: TerritoryView = {
  ...EMPTY,
  consents: { geocoding: true, calendar: true },
  place: PLACE,
  holidays: [{ day: "2026-11-01", name: "Toussaint" }, { day: "2026-11-11", name: "11 novembre" }],
  school: { academy: "Amiens", zone: "Zone B", breaks: [{ label: "Vacances de Noël", start: "2026-12-19", end: "2027-01-03" }] },
  sources: [
    { name: "Jours fériés 2026", url: "https://calendrier.api.gouv.fr/jours-feries/metropole/2026.json", fetched_at: "2026-10-24T09:00:00+00:00", last_error: null },
    { name: "Vacances scolaires", url: "https://data.education.gouv.fr/x", fetched_at: "2026-10-24T09:00:00+00:00", last_error: "La source répond 503" },
  ],
};

function mockApi(state: { st: TerritoryView }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/territory") return json(state.st);
    if (url === "/api/consents") return json([{ purpose: "geocoding", title: "Ton adresse située sur la carte", text: "Bank l'envoie telle que tu l'as tapée au géocodeur public de l'IGN.", granted: false, explicit: false, decided: false, updated_at: null, default: false }]);
    if (url === "/api/consents/geocoding") { state.st = { ...state.st, consents: { ...state.st.consents, geocoding: true } }; return json({}); }
    if (url === "/api/territory/geocode") { state.st = { ...FULL, consents: { geocoding: true, calendar: false }, holidays: [], school: null, sources: [] }; return json(state.st); }
    if (url === "/api/territory/calendar/refresh") { state.st = FULL; return json({ result: { errors: [] }, view: FULL }); }
    if (url === "/api/consents/calendar") return json({});
    return json({}, 404);
  });
}

async function mount() {
  await act(async () => { root.render(<ToastProvider><Territory /></ToastProvider>); });
  await settle();
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("Réglages › Ton adresse et ton calendrier", () => {
  it("asks before the address leaves, then shows where it is and where it comes from", async () => {
    const state = { st: EMPTY };
    mockApi(state);
    await mount();
    expect(text()).toContain("telle que tu l'as tapée au géocodeur public de l'IGN");
    expect(host.querySelector("input")).toBeNull();
    await click("Oui, situer mon adresse");
    expect(calls.find((c) => c.url === "/api/consents/geocoding")?.init?.method).toBe("PUT");
    const input = host.querySelector("input")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "Place de l'Hôtel de Ville 80000 Amiens");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("Situer");
    const sent = JSON.parse(String(calls.find((c) => c.url === "/api/territory/geocode")?.init?.body));
    expect(sent).toEqual({ query: "Place de l'Hôtel de Ville 80000 Amiens" });
    expect(text()).toContain("Amiens · code INSEE 80021");
    expect(text()).toContain("(à la rue près)");
    expect(text()).toContain("49,89324 ; 2,29539");
    expect(text()).toContain("Situé le 24/10/2026");
    expect(text()).toContain("Sans accord, la prévision ne décale aucun prélèvement.");
  });

  it("shows holidays, school breaks and a failed source that kept its data", async () => {
    mockApi({ st: FULL });
    await mount();
    expect(text()).toContain("Zone des jours fériés : métropole");
    expect(text()).toContain("11/11/2026 11 novembre");
    expect(text()).toContain("Vacances de l'académie de Amiens (Zone B)");
    expect(text()).toContain("Vacances de Noël : du 19/12/2026 au 03/01/2027");
    expect(text()).toContain("dernier essai en échec : La source répond 503, données précédentes gardées");
    await click("Relire maintenant");
    expect(calls.some((c) => c.url === "/api/territory/calendar/refresh" && c.init?.method === "POST")).toBe(true);
  });

  it("formats precision, breaks and the journal entries", () => {
    expect(precisionText("housenumber")).toBe("au numéro près");
    expect(precisionText(null)).toBe("précision inconnue");
    expect(breakText({ label: "Pont de l'Ascension", start: "2027-05-07", end: "2027-05-07" })).toBe("Pont de l'Ascension : le 07/05/2027");
    expect(labelAction("place.geocode")).toBe("Adresse située sur la carte (Géoplateforme)");
  });
});

describe("L'année qui vient : fériés et vacances", () => {
  const point = (month: string, holidays: string[], school: string[]): YearPoint => ({
    month, label: month, income_cents: 0, fixed_cents: 0, variable_cents: 0, spikes_cents: 0, spikes: [], end_balance_cents: 0, holidays, school,
  });
  it("lists only the months that have one, with the academy", async () => {
    const y = {
      points: [point("nov. 26", ["01/11 : Toussaint"], []), point("déc. 26", [], ["Vacances de Noël du 19/12 au 03/01"]), point("janv. 27", [], [])],
      calendar: { zone: "metropole", academy: "Amiens", school_zone: "Zone B", note: "Un prélèvement qui tombe un dimanche ou un jour férié est placé au jour ouvré suivant." },
    };
    await act(async () => { root.render(<YearCalendar y={y} />); });
    expect(text()).toContain("Jours fériés et vacances (académie de Amiens, Zone B)");
    expect(text()).toContain("nov. 26 01/11 : Toussaint");
    expect(text()).toContain("déc. 26 Vacances de Noël du 19/12 au 03/01");
    expect(text()).not.toContain("janv. 27");
    await act(async () => { root.render(<YearCalendar y={{ ...y, calendar: null }} />); });
    expect(text()).toBe("");
  });
});
