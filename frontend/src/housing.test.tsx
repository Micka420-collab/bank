// Situation › Ton logement face aux données publiques (#34) : l'accord avant que rien ne parte, les DPE de l'adresse
// à reconnaître (Bank n'en retient aucun seul), la règle F/G avec sa source, la carte des loyers avec sa règle de
// fiabilité, et ce qui manque dit tel quel.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Housing, dpeWhere, m2Text, type HousingDpe, type HousingView } from "./components/Housing";
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
  rule: "Fiable seulement avec un R² ajusté d'au moins 0,5 et au moins 30 annonces dans la commune (ANIL).", year: null,
  published: null, page: null, credit: "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin", source: null,
};
const OFF: HousingView = {
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

  it("formate sans arrondir en silence", () => {
    expect(m2Text(13.9839)).toBe("13,98");
    expect(dpeWhere({ ...F, where: "", floor: 2, surface_m2: null })).toBe("étage 2 · appartement");
  });
});
