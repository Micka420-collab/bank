// Alertes de cours et « pilote vs consigne » : rendu réel dans jsdom, API simulée. Armer, réarmer, supprimer une
// alerte ; lire chaque rendez-vous d'une consigne (à temps, en retard, manqué, montant différent, éteint).

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PilotSlot, PilotTrack as PilotTrackData, PriceAlert, PriceAlertsView } from "./api";
import { PilotTrack, gapText, slotLabel, statusText } from "./components/PilotTrack";
import { PriceAlerts, alertText, eurText, firedText } from "./components/PriceAlerts";
import { ToastProvider } from "./components/ui";
import { ruleText } from "./pages/Pilot";

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
    if (out instanceof Response) return Promise.resolve(out);
    if (out === undefined) return Promise.resolve(new Response(null, { status: 204 }));
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label) || x.getAttribute("aria-label")?.startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const typeInto = (el: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const sent = (url: string, method: string) => calls.filter((c) => c.url.startsWith(url) && c.init?.method === method);

const ARMED: PriceAlert = {
  id: 1, asset: "BTC", kind: "below", threshold: "50000", threshold_text: "50 000 €", label: "Plancher", state: "armed", fires: 0,
  triggered_at: null, triggered_price: null, price_eur: "60000", change_24h: "2.50", created_at: "2026-10-21T10:00:00+00:00",
};
const FIRED: PriceAlert = {
  ...ARMED, id: 2, kind: "above", threshold: "61000", threshold_text: "61 000 €", label: null, state: "triggered", fires: 1,
  triggered_at: "2026-10-21T12:05:00+00:00", triggered_price: "61500",
};
function alertsView(alerts: PriceAlert[] = [ARMED, FIRED]): PriceAlertsView {
  return {
    alerts, assets: ["BTC", "ETH"], max: 20,
    kinds: { above: "Seuil haut", below: "Seuil bas", change: "Variation sur 24 h" },
    bounds: { change_min: "2", change_max: "90", margin_percent: "1" },
  };
}

function slot(over: Partial<PilotSlot>): PilotSlot {
  return {
    period: "2026-W43", start: "2026-10-19", end: "2026-10-25", due: "2026-10-19", status: "on_time", move_id: 1, done_on: "2026-10-19",
    delay_days: 0, amount_cents: 5000, gap_cents: 0, gap_percent: 0, amount_differs: false, trial: false, note: null, refusals: [], ...over,
  };
}
const TRACK: PilotTrackData = {
  since: "2026-07-21",
  rules: [{
    id: "rmon", label: "Acheter 50,00 € de BTC chaque lundi", every: "week", on: 1, amount_cents: 5000,
    totals: { due: 4, on_time: 2, late: 1, missed: 1, different: 1, expected_cents: 20000, done_cents: 14800, gap_cents: -5200 },
    slots: [
      slot({ period: "2026-W39", start: "2026-09-21", due: "2026-09-21" }),
      slot({ period: "2026-W40", start: "2026-09-28", due: "2026-09-28", status: "late", delay_days: 2, done_on: "2026-09-30" }),
      slot({ period: "2026-W41", start: "2026-10-05", due: "2026-10-05", status: "off", move_id: null, done_on: null, amount_cents: null }),
      slot({ period: "2026-W42", start: "2026-10-12", due: "2026-10-12", status: "missed", move_id: null, done_on: null, amount_cents: null, refusals: ["Plafond des 24 h atteint"] }),
      slot({ period: "2026-W43", amount_cents: 4800, gap_cents: -200, gap_percent: -4, amount_differs: true }),
    ],
  }],
  outside: [{ move_id: 9, text: "achat de 9,00 € de ETH", status: "executed", amount_cents: 900, created_at: "2026-10-21T09:00:00+00:00", why: "Sans consigne (mode libre)" }],
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

describe("alertes de cours", () => {
  it("écrit chaque alerte comme un constat", () => {
    expect(alertText(ARMED)).toBe("BTC sous 50 000 €");
    expect(alertText({ ...ARMED, kind: "change", threshold_text: "8 %" })).toBe("BTC varie de 8 % sur 24 h");
    expect(eurText("0.000123")).toMatch(/0,000123/);
    expect(eurText(null)).toBe("—");
    // 0 h 30 dans ton fuseau : le jour aussi est le tien, pas celui d'UTC.
    expect(firedText(new Date(2026, 9, 22, 0, 30).toISOString())).toBe("le 22 oct à 0 h 30");
  });

  it("liste, arme, réarme et supprime", async () => {
    let view = alertsView();
    mockApi({
      "/api/crypto/alerts/2/rearm": () => (view = alertsView([ARMED, { ...FIRED, state: "armed" }])),
      "/api/crypto/alerts/1": () => { view = alertsView([FIRED]); return undefined; },
      "/api/crypto/alerts": (init) => {
        if (init?.method === "POST") view = alertsView([ARMED, FIRED, { ...ARMED, id: 3, asset: "ETH", kind: "change", threshold_text: "8 %", label: null }]);
        return view;
      },
    });
    await act(async () => root.render(<ToastProvider><PriceAlerts /></ToastProvider>));
    await settle();
    const text = host.textContent ?? "";
    expect(text).toContain("Alertes de cours");
    expect(text).toContain("2 / 20");
    expect(text).toContain("armée");
    expect(text).toMatch(/déclenchée le 21 oct/);
    expect(text).toContain("Jamais un conseil");
    // Variation sur 24 h, sur ETH.
    await act(async () => { host.querySelector<HTMLSelectElement>("select")!.value = "ETH"; host.querySelector("select")!.dispatchEvent(new Event("change", { bubbles: true })); });
    await click("Variation sur 24 h");
    await typeInto(host.querySelector<HTMLInputElement>("input[inputmode=decimal]")!, "8");
    await act(async () => { button("Armer l'alerte").closest("form")!.requestSubmit(); });
    await settle();
    const post = sent("/api/crypto/alerts", "POST").find((c) => c.url === "/api/crypto/alerts")!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ asset: "ETH", kind: "change", value: "8", label: null });
    expect(host.textContent).toContain("ETH varie de 8 % sur 24 h");
    await click("Réarmer");
    expect(sent("/api/crypto/alerts/2/rearm", "POST")).toHaveLength(1);
    await click("Supprimer l'alerte BTC sous");
    expect(sent("/api/crypto/alerts/1", "DELETE")).toHaveLength(1);
  });

  it("invite à suivre un actif avant d'armer une alerte", async () => {
    mockApi({ "/api/crypto/alerts": () => ({ ...alertsView([]), assets: [] }) });
    await act(async () => root.render(<ToastProvider><PriceAlerts /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("ajoute d'abord un avoir");
    expect(host.querySelector("form")).toBeNull();
  });
});

describe("pilote vs consigne", () => {
  it("nomme chaque rendez-vous et son état", () => {
    const rule = TRACK.rules[0]!;
    expect(slotLabel(rule, rule.slots[0]!)).toBe("sem. du 21 sept");
    expect(slotLabel({ every: "day" }, rule.slots[0]!)).toBe("21 sept");
    expect(slotLabel({ every: "month" }, slot({ start: "2026-02-01" }))).toBe("février 2026");
    expect(statusText(rule.slots[1]!)).toBe("en retard de 2 jours");
    expect(statusText(rule.slots[3]!)).toBe("manqué");
    expect(gapText(rule.slots[4]!)).toMatch(/-2,00\s€\s\(-4\s%\)/);
    expect(gapText(rule.slots[0]!)).toBeNull();
    expect(ruleText({ kind: "buy", asset: "BTC", to_asset: null, destination: null, amount_cents: 5000, every: "week", on: 1 }, {})).toMatch(/chaque lundi$/);
    expect(ruleText({ kind: "buy", asset: "BTC", to_asset: null, destination: null, amount_cents: 5000, every: "month", on: 1 }, {})).toMatch(/le 1er de chaque mois$/);
  });

  it("montre le prévu face au fait, le rythme et les mouvements hors consigne", async () => {
    mockApi({ "/api/autonomy/track": () => TRACK });
    await act(async () => root.render(<PilotTrack />));
    await settle();
    const text = (host.textContent ?? "").replace(/\s/g, " ");
    expect(text).toContain("Pilote vs consigne");
    expect(text).toMatch(/Prévu 200,00 € · fait 148,00 € \(-52,00 €\)/);
    expect(text).toContain("4 rendez-vous passés : 2 à temps, 1 en retard, 1 manqué, 1 montant différent");
    expect(host.querySelectorAll(".pl-track-strip li")).toHaveLength(5);
    expect(host.querySelector(".pl-track-strip li.is-missed")).not.toBeNull();
    expect(text).toContain("Refusé par Bank : Plafond des 24 h atteint");
    expect(text).toContain("pilote éteint");
    expect(text).toContain("Hors consigne");
    expect(text).toContain("Sans consigne (mode libre)");
  });

  it("explique quoi faire sans consigne", async () => {
    mockApi({ "/api/autonomy/track": () => ({ rules: [], outside: [], since: "2026-07-21" }) });
    await act(async () => root.render(<PilotTrack />));
    await settle();
    expect(host.textContent).toContain("Aucune consigne suivie");
  });
});
