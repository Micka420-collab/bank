// « Ce qui est lié » : un doublon d'assurance s'affiche avec ses pièces et son enjeu, la démarche se prépare en
// un geste, une piste statistique reste rangée dans les pistes, et « pas un problème » écarte.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Correlation, CorrelationsView } from "./api";
import { Correlations } from "./components/Correlations";
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

const DUP: Correlation = {
  key: "dup_home:MAIF+PACIFICA", kind: "duplicate_insurance", level: "danger", topic: "assurances", topic_label: "Assurances",
  title: "Deux assurances habitation ?", body: "MAIF et Pacifica (Crédit Agricole) te prélèvent chacun une assurance habitation.",
  evidence: [
    { label: "MAIF", value: "38,00 € par mois", ref: "series:maif" },
    { label: "Pacifica (Crédit Agricole)", value: "12,00 € par mois", ref: "series:pacifica" },
    { label: "Assureur nommé dans le bail", value: "MAIF", ref: "doc:7" },
  ],
  yearly_cents: 14_400, action: { template: "resilier_abonnement", label: "Préparer la résiliation", merchant: "PACIFICA" },
  confidence: 0.95, statistical: false, status: "new", procedure_id: null,
};
const HINT: Correlation = {
  key: "stat:revenu:loisirs", kind: "statistical_link", level: "info", topic: "habitudes", topic_label: "Habitudes",
  title: "Piste : ton revenu du mois et Loisirs montent ensemble", body: "Sur 12 mois… Ce n'est pas une cause.",
  evidence: [], yearly_cents: null, action: null, confidence: 0.6, statistical: true, status: "new", procedure_id: null,
};
const view = (over: Partial<CorrelationsView> = {}): CorrelationsView => ({
  findings: [DUP, HINT], hidden: 0, irl_source: "INSEE, 3e trimestre 2026", disclaimer: "Chaque constat renvoie à ses pièces.", ...over,
});

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

const render = async () => {
  await act(async () => root.render(<ToastProvider><Correlations /></ToastProvider>));
  await settle();
};

describe("ce qui est lié", () => {
  it("montre le doublon avec ses pièces et son enjeu, et range la piste à part", async () => {
    mockApi({ "/api/correlations": () => view() });
    await render();
    expect(host.querySelector(".corr-row.is-danger .tag")?.textContent).toBe("à régler");
    expect(text()).toContain("144 € par an");
    await click("Les pièces (3)");
    expect(host.querySelector("a[href='#/documents/7']")).not.toBeNull();
    expect(host.querySelector(".corr-hints")?.textContent).toContain("Pistes (1)");
    expect(host.querySelector(".corr-hints .tag")?.textContent).toBe("bon à savoir");
  });

  it("prépare la démarche en un geste et écarte un constat", async () => {
    let hidden = 0;
    mockApi({
      "/api/correlations/start": () => ({ procedure_id: 42 }),
      "/api/correlations/decide": () => { hidden = 1; return view({ findings: [HINT], hidden }); },
      "/api/correlations": () => view({ hidden }),
    });
    await render();
    await click("Préparer la résiliation");
    expect(body(calls.find((c) => c.url === "/api/correlations/start")!)).toEqual({ key: DUP.key });
    expect(window.location.hash).toBe("#/demarches/42");
    await click("Pas un problème");
    expect(body(calls.find((c) => c.url === "/api/correlations/decide")!)).toEqual({ key: DUP.key, decision: "dismissed" });
    expect(text()).toContain("1 constat écarté");
  });

  it("invite à déposer le bail quand rien n'est lié", async () => {
    mockApi({ "/api/correlations": () => view({ findings: [], hidden: 0 }) });
    await render();
    expect(text()).toContain("Dépose ton bail et tes attestations d'assurance");
  });
});

describe("crédit et coûts", () => {
  const RENEG: Correlation = {
    key: "renegociation:9:2026-Q4", kind: "renegotiation", level: "info", topic: "credits", topic_label: "Crédits",
    title: "Maison : à 4,05 %, le simulateur de renégociation donne environ 5 000 € de gain net", body: "Estimation Bank…",
    evidence: [{ label: "Simulateur", value: "nouveau taux 4,05 %", ref: "loan:9:405" }],
    yearly_cents: null, action: null, confidence: 0.5, statistical: false, status: "new", procedure_id: null,
  };
  const ASK: Correlation = {
    key: "frais_reels_ask:2026", kind: "real_expenses_ask", level: "info", topic: "impots", topic_label: "Impôts",
    title: "Frais réels 2026 : il manque ta distance domicile-travail et ton véhicule", body: "Ton agenda compte 180 jours…",
    evidence: [], yearly_cents: null, action: null, confidence: 0.6, statistical: false, status: "new", procedure_id: null,
  };

  it("ouvre le simulateur du prêt avec le taux du constat, et demande le trajet pour les frais réels", async () => {
    mockApi({
      "/api/correlations/commute": () => view({ findings: [], commute: { one_way_km: "38.0", fiscal_hp: 6, electric: false } }),
      "/api/correlations": () => view({ findings: [RENEG, ASK] }),
    });
    await render();
    await click("Les pièces");
    const link = [...host.querySelectorAll("a")].find((a) => a.textContent === "ouvrir le simulateur");
    expect(link?.getAttribute("href")).toBe("#/patrimoine?pret=9&taux=405");
    const form = host.querySelector<HTMLFormElement>("form.corr-commute")!;
    const [km, hp] = [...form.querySelectorAll<HTMLInputElement>("input:not([type=checkbox])")];
    const set = (el: HTMLInputElement, v: string) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    await act(async () => { set(km!, "38,5"); set(hp!, "6"); });
    await click("Comparer");
    expect(body(calls.find((c) => c.url === "/api/correlations/commute")!)).toEqual({ one_way_km: "38.5", fiscal_hp: 6, electric: false });
  });
});
