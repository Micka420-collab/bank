// Situation › Ton logement face aux données publiques (#34) : l'accord avant que rien ne parte, les DPE de l'adresse
// à reconnaître (Bank n'en retient aucun seul), la règle F/G avec sa source, la carte des loyers avec sa règle de
// fiabilité, et ce qui manque dit tel quel.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Housing, dpeWhere, m2Text, type HousingDpe, type HousingView } from "./components/Housing";
import { rateText, type HousingPublicView } from "./components/HousingPublic";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");
const buttons = (label: string) => [...host.querySelectorAll("button")].filter((x) => x.textContent?.trim().startsWith(label));
const click = async (b: HTMLButtonElement | undefined) => {
  if (!b) throw new Error("bouton introuvable");
  await act(async () => { b.click(); });
  await settle();
};

const F: HousingDpe = {
  number: "2631E0066371D", letter: "F", ges: "C", established: "2026-01-10", valid_until: "2036-01-09",
  building: "appartement", address: "14 Rue Dalmatie 31500 Toulouse", where: "Etage RDC; Porte 2", floor: 0,
  surface_m2: 65.3, depcode: "31",
};
const E: HousingDpe = { ...F, number: "2631E0120274I", letter: "E", established: "2026-01-15", where: "App au RDC", surface_m2: 65 };
const RULE = {
  text: "Réviser le loyer est interdit pour un logement classé F ou G quand le bail est signé, renouvelé ou tacitement reconduit depuis le 24 août 2022 en métropole.",
  source: "Service-Public, fiche F1311", url: "https://www.service-public.gouv.fr/particuliers/vosdroits/F1311", checked_on: "24 octobre 2026",
};
const MARKET_EMPTY: HousingView["market"] = {
  missing: null, file_label: "appartement (toutes tailles)", row: null, reliable: null,
  rule: "L'ANIL invite à la prudence quand le R² est inférieur à 0,5 ou que la commune compte moins de 30 annonces (carte des loyers, data.gouv.fr) : Bank ne compare pas ton loyer dans ce cas.", year: null,
  published: null, page: null, credit: "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin", source: null,
};
const LIST = {
  source: "Service-Public, fiche F1314", url: "https://www.service-public.gouv.fr/particuliers/vosdroits/F1314",
  verified: "2026-08-01", checked_on: "24 octobre 2026", end: "2026-11-24",
};
const PUBLIC_EMPTY: HousingPublicView = {
  details: { epoque: null, rooms: null, epoques: [{ key: "Avant 1946", label: "avant 1946" }, { key: "1946-1970", label: "de 1946 à 1970" }] },
  control: { commune: "Toulouse", territory: null, list: LIST, page: null, paris: null, cdc: { source: "F1216", url: "https://www.service-public.gouv.fr/particuliers/vosdroits/F1216" }, source: null, paris_source: null },
  taxes: { code: null, known: null, forecast: null, rates: [], notes: [], page: "https://data.economie.gouv.fr/explore/dataset/fiscalite-locale-des-particuliers/", bases: { source: "Service-Public, fiche F59", url: "https://www.service-public.gouv.fr/particuliers/vosdroits/F59" }, source: null },
  risks: { reachable: null, checked_at: null, last_error: null, page: "https://www.georisques.gouv.fr/", insurance: null, source: null },
};
const OFF: HousingView = {
  ...PUBLIC_EMPTY,
  consent: false,
  dpe: { missing: null, candidates: [], total: null, chosen: null, none_mine: false, chosen_at: null, source: null, page: "https://data.ademe.fr/datasets/dpe03existant" },
  rule: RULE,
  home: { kind: "appartement", kind_source: "ta description du logement", surface_m2: 65, surface_source: "ta description du logement", commune: "Toulouse" },
  market: MARKET_EMPTY,
  findings: [],
  notes: [],
};
const LISTED: HousingView = {
  ...OFF,
  consent: true,
  dpe: { ...OFF.dpe, candidates: [E, F], total: 2, source: { name: "ADEME", url: null, fetched_at: "2026-10-24T09:00:00+00:00", last_error: null } },
  notes: ["Classe énergie du logement inconnue : reconnais ton DPE parmi ceux de ton adresse, ou dépose ton bail dans Papiers (la classe y est souvent écrite)."],
};
const CHOSEN: HousingView = {
  ...LISTED,
  dpe: { ...LISTED.dpe, chosen: F, chosen_at: "2026-10-24T09:05:00+00:00" },
  market: {
    ...MARKET_EMPTY,
    row: { commune: "Toulouse", m2: 13.9839, low: 11.1703, high: 17.5062, level: "commune", obs: 225512, r2: 0.8363, year: 2025 },
    reliable: true, year: 2025, page: "https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025",
  },
  findings: [{ key: "rent_dpe_frozen:1", kind: "rent_dpe_frozen", title: "Loyer révisé alors que le logement est classé F" }],
  notes: [],
};

function mockApi(state: { st: HousingView }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/housing") return json(state.st);
    if (url === "/api/consents") return json([{ purpose: "housing", title: "Ton logement face aux données publiques", text: "Bank envoie à l'ADEME l'identifiant de ton adresse.", granted: false, explicit: false, decided: false, updated_at: null, default: false }]);
    if (url === "/api/consents/housing") { state.st = { ...state.st, consent: true }; return json({}); }
    if (url === "/api/housing/dpe/refresh") { state.st = LISTED; return json({ result: { status: "refreshed", message: "2 DPE à ton adresse : indique lequel est ton logement." }, view: LISTED }); }
    if (url === "/api/housing/dpe/choose") { state.st = CHOSEN; return json(CHOSEN); }
    if (url === "/api/housing/details") { state.st = { ...state.st, details: { ...state.st.details, epoque: "1946-1970", rooms: 2 } }; return json(state.st); }
    return json({}, 404);
  });
}

async function mount() {
  await act(async () => { root.render(<ToastProvider><Housing /></ToastProvider>); });
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

describe("Ton logement face aux données publiques", () => {
  it("demande l'accord avant toute relève", async () => {
    const state = { st: OFF };
    mockApi(state);
    await mount();
    expect(text()).toContain("Bank envoie à l'ADEME l'identifiant de ton adresse.");
    expect(calls.some((c) => c.url.includes("/refresh"))).toBe(false);
    await click(buttons("Oui, comparer mon logement")[0]);
    expect(calls.some((c) => c.url === "/api/consents/housing" && c.init?.method === "PUT")).toBe(true);
    expect(text()).toContain("Chercher les DPE de mon adresse");
  });

  it("liste les DPE de l'adresse sans en retenir aucun seul, puis retient ton choix", async () => {
    const state = { st: { ...OFF, consent: true } };
    mockApi(state);
    await mount();
    await click(buttons("Chercher les DPE")[0]);
    expect(text()).toContain("2 DPE à ton adresse");
    expect(text()).toContain("Bank n'en retient aucun seul");
    expect(text()).toContain("Etage RDC; Porte 2 · 65,3 m² · appartement");
    expect(text()).toContain("Classe énergie du logement inconnue");
    expect(text()).toContain("Service-Public, fiche F1311");
    await click(buttons("C'est le mien")[1]);
    const choose = calls.find((c) => c.url === "/api/housing/dpe/choose");
    expect(JSON.parse(String(choose?.init?.body))).toEqual({ number: "2631E0066371D" });
    expect(text()).toContain("Ton logement : classe F, DPE n° 2631E0066371D du 10/01/2026");
    expect(text()).toContain("de 11,17 à 17,51 €/m² charges comprises");
    expect(text()).toContain("225512 annonces, R² 0,84");
    expect(text()).toContain("Un constat dans « Ce qui est lié » : Loyer révisé alors que le logement est classé F.");
    expect(text()).not.toMatch(/abusi/i);
  });

  it("encadrement à Paris, taxe foncière prévue et Géorisques injoignable, sans rien deviner", async () => {
    const paris: HousingView = {
      ...CHOSEN,
      control: {
        ...PUBLIC_EMPTY.control, commune: "Paris",
        territory: { name: "Paris", start: "2019-07-01", partial: false, simulator: "https://teleservices.paris.fr/encadrementdesloyers/", simulator_label: "Paris : tester mon loyer", open_data: true },
        page: { verified: "2026-08-01", missing: [], changed: false },
        paris: { quartier: "Sainte-Marguerite", years: [2024, 2025], page: "https://opendata.paris.fr/explore/dataset/logement-encadrement-des-loyers/", source: "Ville de Paris", arrete_2026: "https://cdn.paris.fr/x.pdf" },
      },
      taxes: {
        ...PUBLIC_EMPTY.taxes, code: "75056",
        known: { year: 2026, amount_cents: 101000, teom_cents: null, source: "prelevements", monthly: false },
        forecast: { target: 2027, amount_cents: 101000, due: "2027-10-15", unknown_rates: [2027] },
        rates: [{ year: 2024, commune: "VILLE DE PARIS", tfb: 21.19, teom: 6.21 }, { year: 2025, commune: "VILLE DE PARIS", tfb: 21.21, teom: 6.21 }],
      },
      risks: { ...PUBLIC_EMPTY.risks, reachable: false, checked_at: "2026-10-24T09:00:00+00:00", last_error: "Source injoignable (réseau)" },
    };
    const state = { st: paris };
    mockApi(state);
    await mount();
    expect(text()).toContain("Paris : loyer plafonné pour un bail signé ou renouvelé depuis le 01/07/2019");
    expect(text()).toContain("Ton quartier : Sainte-Marguerite");
    expect(text()).toContain("fiche F1314 vérifiée le 01/08/2026");
    expect(text().replace(/\s/g, " ")).toContain("2027 : 1 010,00 € à bases inchangées · échéance vers le 15/10/2027 · taux 2027 pas encore publiés");
    expect(text()).toContain("2025 : taxe foncière 21,21 %, TEOM 6,21 %");
    expect(text()).toContain("revalorisation des bases non chiffrée");
    expect(text()).toContain("Géorisques injoignable depuis ton serveur");
    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "1946-1970";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const input = host.querySelector('input[type="number"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "2");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(buttons("Enregistrer")[0]);
    const saved = calls.find((c) => c.url === "/api/housing/details");
    expect(JSON.parse(String(saved?.init?.body))).toEqual({ epoque: "1946-1970", rooms: 2 });
    expect(text()).not.toMatch(/abusi|conseill|recommand/i);
  });

  it("formate sans arrondir en silence", () => {
    expect(rateText(49.67)).toBe("49,67 %");
    expect(m2Text(13.9839)).toBe("13,98");
    expect(dpeWhere({ ...F, where: "", floor: 2, surface_m2: null })).toBe("étage 2 · appartement");
  });
});
