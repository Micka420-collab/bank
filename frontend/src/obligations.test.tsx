// Tes déclarations : rendu réel dans jsdom, API simulée. Une proposition se suit en un geste, un mois de
// déclaration manquant se règle sur place, les chiffres de la déclaration trimestrielle s'ouvrent sur leurs
// opérations, et « j'ai déclaré ces montants » envoie exactement les totaux affichés.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Obligation, ObligationsView, Prefill } from "./api";
import { Obligations } from "./components/Obligations";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

function mockApi(handlers: Record<string, (init?: RequestInit, url?: string) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    const out = handlers[key]!(init, url);
    if (out === undefined) return Promise.resolve(new Response(null, { status: 204 }));
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
const sent = (url: string, method: string) => calls.filter((c) => c.url.startsWith(url) && c.init?.method === method);
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;
const text = () => (host.textContent ?? "").replace(/[\u202f\u00a0]/g, " ");

const SPEC = {
  kind: "caf_dtr", label: "Déclaration trimestrielle de ressources (CAF)", organism: "caf", organism_name: "CAF",
  cadence_months: 3, what: "Tous les trois mois, vérifie et valide tes ressources.", consequence: "Prime suspendue.",
  url: "https://www.caf.fr/allocataires/mon-compte", source: "caf.fr, relevé le 22 octobre 2026",
  anchor: "month" as const, anchor_label: "Mois de ta prochaine déclaration",
};
const PERIOD = {
  key: "2026-10", label: "Ressources de juin 2026 à août 2026", opens: "2026-10-01", deadline: "2026-10-31",
  reference_months: ["2026-06", "2026-07", "2026-08"], state: "open" as const, done_on: null, how: null, proof: null,
  awaiting_payment: false, estimated: false, note: null, declared: null,
};
function obligation(over: Partial<Obligation> = {}): Obligation {
  return {
    id: 1, kind: "caf_dtr", label: SPEC.label, organism: "caf", organism_name: "CAF", active: true, anchor: "2026-10-01",
    anchor_kind: "month", anchor_label: SPEC.anchor_label, needs_anchor: false, cadence_months: 3, window_days: 30,
    what: SPEC.what, consequence: SPEC.consequence, url: SPEC.url, source: SPEC.source, automatic: false,
    current: PERIOD, periods: [PERIOD, { ...PERIOD, key: "2027-01", state: "upcoming", opens: "2027-01-01", deadline: "2027-01-31" }],
    ...over,
  };
}
function view(over: Partial<ObligationsView> = {}): ObligationsView {
  return { obligations: [], suggestions: [], catalog: [SPEC], checked_on: "22 octobre 2026", disclaimer: "Déclarer reste ton geste.", ...over };
}
const PREFILL: Prefill = {
  period: "2026-10", months: ["2026-06", "2026-07", "2026-08"], month_labels: ["juin 2026", "juillet 2026", "août 2026"],
  kinds: [{ key: "activite", label: "Salaires et revenus d'activité" }, { key: "chomage", label: "Allocations chômage" }, { key: "ij", label: "Indemnités journalières" }, { key: "pension", label: "Pensions" }, { key: "autres", label: "Autres ressources" }],
  cells: {
    activite: {
      "2026-06": { amount_cents: 215_000, items: [{ id: 11, day: "2026-06-28", label: "BRGM SALAIRE", amount_cents: 215_000 }], source: "releve" },
      "2026-07": { amount_cents: 215_000, items: [{ id: 12, day: "2026-07-28", label: "BRGM SALAIRE", amount_cents: 215_000 }], source: "releve" },
      "2026-08": { amount_cents: 241_500, items: [{ id: 13, day: "2026-08-28", label: "BRGM SALAIRE", amount_cents: 215_000 }], source: "bulletin", net_paid_cents: 215_000, documents: [42] },
    },
    chomage: { "2026-06": { amount_cents: 0, items: [], source: "releve" }, "2026-07": { amount_cents: 0, items: [], source: "releve" }, "2026-08": { amount_cents: 0, items: [], source: "releve" } },
    ij: { "2026-06": { amount_cents: 0, items: [], source: "releve" }, "2026-07": { amount_cents: 0, items: [], source: "releve" }, "2026-08": { amount_cents: 0, items: [], source: "releve" } },
    pension: { "2026-06": { amount_cents: 0, items: [], source: "releve" }, "2026-07": { amount_cents: 0, items: [], source: "releve" }, "2026-08": { amount_cents: 0, items: [], source: "releve" } },
    autres: { "2026-06": { amount_cents: 0, items: [], source: "releve" }, "2026-07": { amount_cents: 0, items: [], source: "releve" }, "2026-08": { amount_cents: 0, items: [], source: "releve" } },
  },
  totals: { activite: 671_500, chomage: 0, ij: 0, pension: 0, autres: 0 },
  excluded: [{ id: 20, day: "2026-07-05", label: "CAF PRIME ACTIVITE", amount_cents: 25_829, why: "déjà connu de l'organisme" }],
  to_check: [],
  notes: ["Pour les salaires, la CAF attend le montant net social."],
  summary: "salaires 6715 €",
};

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
  await act(async () => root.render(<ToastProvider><Obligations /></ToastProvider>));
  await settle();
};

describe("tes déclarations", () => {
  it("propose ce que les versements révèlent et le suit en un geste", async () => {
    let state = view({ suggestions: [SPEC] });
    mockApi({
      "/api/obligations/suggestions/caf_dtr/dismiss": () => state,
      "/api/obligations": (init) => {
        if (init?.method === "POST") state = view({ obligations: [obligation({ anchor: null, needs_anchor: true, current: null, periods: [] })] });
        return state;
      },
    });
    await render();
    expect(host.textContent).toContain("d'après tes versements, ça te concerne");
    await click("Suivre");
    expect(body(sent("/api/obligations", "POST")[0]!)).toEqual({ kind: "caf_dtr" });
    // Il manque le mois de déclaration : le formulaire est là, et le calendrier se calcule après.
    const month = host.querySelector<HTMLInputElement>("input[type=month]")!;
    expect(month).not.toBeNull();
    expect(host.textContent).toContain("Calculer mon calendrier");
  });

  it("ouvre les chiffres de la déclaration trimestrielle et envoie exactement les totaux déclarés", async () => {
    mockApi({
      "/api/obligations/1/prefill": () => PREFILL,
      "/api/obligations/1/done": () => view({ obligations: [obligation({ current: { ...PERIOD, state: "done", how: "user", done_on: "2026-10-21", declared: PREFILL.totals } })] }),
      "/api/obligations": () => view({ obligations: [obligation()] }),
    });
    await render();
    expect(host.querySelector(".ob-row .tag")?.textContent).toBe("à faire");
    expect(host.textContent).toContain("jusqu'au 31 oct");
    await click("Mes chiffres");
    expect(calls.some((c) => c.url.startsWith("/api/obligations/1/prefill?period=2026-10"))).toBe(true);
    expect(host.textContent).toContain("juin 2026");
    expect(text()).toContain("6 715,00");
    expect(host.querySelector(".ob-table .tag.ok")?.textContent).toBe("bulletin");
    const cell = [...host.querySelectorAll<HTMLButtonElement>(".ob-cell")].find((b) => (b.textContent ?? "").replace(/[\u202f\u00a0]/g, " ").includes("2 415"))!;
    await act(async () => { cell.click(); });
    expect(text()).toContain("Net versé sur le compte : 2 150,00");
    expect(host.textContent).toContain("Non déclarables (1)");
    await click("J'ai déclaré ces montants");
    const done = sent("/api/obligations/1/done", "POST");
    expect(done).toHaveLength(1);
    expect(body(done[0]!)).toEqual({ period: "2026-10", declared: PREFILL.totals });
  });

  it("montre l'urgence quand le versement attendu manque, et le détail avec sa source", async () => {
    mockApi({
      "/api/obligations": () => view({ obligations: [obligation({ kind: "ft_actualisation", label: "Actualisation France Travail", organism_name: "France Travail", anchor_kind: null, anchor: null, current: { ...PERIOD, key: "2026-09", label: "Allocation de septembre 2026", state: "late", awaiting_payment: true, deadline: "2026-10-15" } })] }),
    });
    await render();
    expect(host.querySelector(".ob-row .tag")?.textContent).toBe("versement attendu");
    expect(host.querySelector(".notice.danger")?.textContent).toContain("Déclaration faite ?");
    await click("Détail");
    expect(host.textContent).toContain("Source : caf.fr, relevé le 22 octobre 2026");
    expect(host.textContent).toContain("Si tu oublies :");
    expect(host.querySelector("input[type=month]")).toBeNull();
  });

  it("ajoute une obligation à soi avec son échéance et sa cadence", async () => {
    const custom = { ...SPEC, kind: "custom", label: "Autre obligation", anchor: "date" as const, anchor_label: "Prochaine échéance", what: "Une obligation à toi." };
    mockApi({ "/api/obligations": () => view({ catalog: [SPEC, custom] }) });
    await render();
    await click("Ajouter");
    const select = host.querySelector<HTMLSelectElement>("select")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(select, "custom");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const name = host.querySelector<HTMLInputElement>("input[placeholder='Attestation d\\'assurance au bailleur']")!;
    const setValue = (el: HTMLInputElement, v: string) => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await setValue(name, "Attestation d'assurance au bailleur");
    await setValue(host.querySelector<HTMLInputElement>("input[type=date]")!, "2026-11-05");
    await act(async () => { button("Suivre cette obligation").closest("form")!.requestSubmit(); });
    await settle();
    const posted = body(sent("/api/obligations", "POST")[0]!);
    expect(posted).toMatchObject({ kind: "custom", label: "Attestation d'assurance au bailleur", anchor: "2026-11-05", cadence_months: 12, window_days: 30 });
  });
});
