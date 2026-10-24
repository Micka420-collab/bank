// Réglages › Barèmes : l'accord avant toute relève, les valeurs en vigueur avec leur date et leur source, les mises à
// jour faites par Bank (ancienne → nouvelle valeur, source) et le retour au fichier, les indices relevés, la source
// en échec qui garde les valeurs précédentes, « Vérifier maintenant » et le libellé du journal.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { labelAction } from "./audit-labels";
import { Baremes, dayText, type BaremesView } from "./components/Baremes";
import { ToastProvider } from "./components/ui";
import { everyText } from "./pages/Watch";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };

const CRE = "CRE, tarifs réglementés de vente de l'électricité (données ouvertes), grille TTC en vigueur depuis le 01/08/2026, relue le 24/10/2026";
const SOURCES: BaremesView["sources"] = [
  { slug: "bareme-cre", name: "CRE — tarifs réglementés de l'électricité", url: "https://www.cre.fr/fileadmin/Documents/Open_data/Marches_de_detail/Option_Base.csv", enabled: true, last_checked_at: null, last_ok_at: null, last_error: null, next_check: null },
  { slug: "bareme-insee", name: "INSEE — inflation (IPC base 2025) et indice de référence des loyers", url: "https://api.insee.fr/series/BDM/data/SERIES_BDM/011814056?lastNObservations=6", enabled: true, last_checked_at: null, last_ok_at: null, last_error: null, next_check: null },
];
const GROUPS = (updated: boolean): BaremesView["in_effect"] => [
  { key: "livrets", title: "Livrets réglementés", as_of: "2026-08-01", source: "info.gouv.fr — taux des livrets réglementés au 1er août 2026", updated: false, items: [{ label: "Livret A", value: "1,70 %" }] },
  { key: "electricity", title: "Tarif réglementé de l'électricité (TTC)", as_of: "2026-08-01", source: updated ? CRE : "Tarif Bleu EDF", updated,
    items: [{ label: "Base 6 kVA", value: `${updated ? "190,81" : "190,35"} € par an + 20,01 c€/kWh` }] },
];
const BEFORE: BaremesView = { consent: false, sources: SOURCES, in_effect: GROUPS(false), updates: [], indices: [], irl: null, notes: [] };
const AFTER: BaremesView = {
  consent: true,
  sources: [
    { ...SOURCES[0]!, last_checked_at: "2026-10-24T09:00:00+00:00", last_ok_at: "2026-10-24T09:00:00+00:00", next_check: "2026-11-23T09:00:00+00:00" },
    { ...SOURCES[1]!, last_checked_at: "2026-10-24T09:00:00+00:00", last_error: "La source répond 503" },
  ],
  in_effect: GROUPS(true),
  updates: [
    { path: "electricity_trv/options:base_6/yearly_subscription_cents", label: "Abonnement Base 6 kVA", shown_old: "190,35 € par an", shown_new: "190,81 € par an",
      effective_from: "2026-08-01", source: CRE, source_url: SOURCES[0]!.url, applied_at: "2026-10-24T09:00:00+00:00", reverted_at: null, state: "active" },
  ],
  indices: [
    { key: "insee.irl", label: "Indice de référence des loyers (IRL)", value: "148,06", period: "2026-Q3", period_label: "3e trimestre 2026", note: null,
      source_url: "https://api.insee.fr/series/BDM/data/SERIES_BDM/001515333?lastNObservations=6", fetched_at: "2026-10-24T09:00:00+00:00" },
  ],
  irl: { checked: 3, gaps: [], missing: [] },
  notes: [{ at: "2026-10-24T09:00:00+00:00", text: "Caisse des Dépôts : dernier mois publié 03/2026, plus ancien que les taux en vigueur dans Bank (depuis le 01/08/2026) : rien de changé." }],
};

function mockApi(state: { st: BaremesView }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/baremes") return json(state.st);
    if (url === "/api/consents") return json([{ purpose: "baremes", title: "Barèmes tenus à jour", text: "Rien te concernant n'est envoyé : ces serveurs voient seulement l'adresse de ton serveur.", granted: false, explicit: false, decided: false, updated_at: null, default: false }]);
    if (url === "/api/consents/baremes") { state.st = { ...state.st, consent: true }; return json({}); }
    if (url === "/api/baremes/refresh") { state.st = AFTER; return json({ result: { status: "checked", message: "1 valeur mise à jour : détail ci-dessous et dans la Veille.", changes: [{}] }, view: AFTER }); }
    if (url === "/api/baremes/revert") {
      state.st = { ...AFTER, in_effect: GROUPS(false), updates: [{ ...AFTER.updates[0]!, state: "reverted", reverted_at: "2026-10-24T10:00:00+00:00" }] };
      return json(state.st);
    }
    return json({}, 404);
  });
}

async function mount() {
  await act(async () => { root.render(<ToastProvider><Baremes /></ToastProvider>); });
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

describe("Réglages › Barèmes", () => {
  it("asks before anything leaves, then checks and shows old → new with its source", async () => {
    const state = { st: BEFORE };
    mockApi(state);
    await mount();
    expect(text()).toContain("ces serveurs voient seulement l'adresse de ton serveur");
    expect(text()).toContain("190,35 € par an");
    expect(text()).toContain("En vigueur depuis le 01/08/2026");
    expect(() => button("Vérifier maintenant")).toThrow();
    expect(calls.some((c) => c.url === "/api/baremes/refresh")).toBe(false);

    await click("Oui, tenir les barèmes à jour");
    expect(calls.find((c) => c.url === "/api/consents/baremes")?.init?.method).toBe("PUT");
    expect(calls.some((c) => c.url === "/api/baremes/refresh" && c.init?.method === "POST")).toBe(true);
    expect(text()).toContain("1 valeur mise à jour");
    expect(text()).toContain("mis à jour par Bank");
    expect(text()).toContain("Abonnement Base 6 kVA : 190,35 € par an → 190,81 € par an");
    expect(text()).toContain("Depuis le 01/08/2026");
    const link = [...host.querySelectorAll("a")].find((a) => a.textContent === CRE);
    expect(link?.getAttribute("href")).toBe(SOURCES[0]!.url);
    expect(text()).toContain("Indice de référence des loyers (IRL) (3e trimestre 2026)148,06");
    expect(text()).toContain("les 3 trimestres relus à l'INSEE concordent");
    expect(text()).toContain("Caisse des Dépôts : dernier mois publié 03/2026");
    expect(text()).toContain("dernier essai en échec : La source répond 503, valeurs précédentes gardées");
    expect(text()).toContain("prochaine le 23/11/2026");
  });

  it("returns a value to the shipped file", async () => {
    const state = { st: AFTER };
    mockApi(state);
    await mount();
    await click("Revenir au fichier");
    const body = JSON.parse(String(calls.find((c) => c.url === "/api/baremes/revert")?.init?.body));
    expect(body).toEqual({ path: "electricity_trv/options:base_6/yearly_subscription_cents" });
    expect(text()).toContain("écartée");
    expect(() => button("Revenir au fichier")).toThrow();
  });

  it("formats dates, intervals and the journal entry", () => {
    expect(dayText("2026-08-01")).toBe("01/08/2026");
    expect(dayText("2026-10-24T09:00:00+00:00")).toBe("24/10/2026");
    expect(everyText(720)).toBe("chaque mois");
    expect(everyText(72)).toBe("tous les 3 jours");
    expect(everyText(12)).toBe("toutes les 12 h");
    expect(labelAction("rates.update")).toBe("Barèmes mis à jour (sources officielles)");
  });
});
