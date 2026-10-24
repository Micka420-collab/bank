// Crypto et pilote automatique : rendu réel dans jsdom, API simulée. Une phrase de récupération collée ne part
// jamais au serveur ; arrêter le pilote ne demande rien ; régler le cadre exige le mot de passe.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CryptoView, PilotView } from "./api";
import { ToastProvider } from "./components/ui";
import { Crypto, guessChain, looksSecret, price, qty } from "./pages/Crypto";
import { Pilot, ruleText } from "./pages/Pilot";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

const NO_EXCHANGES = { keys: [], platforms: [], network_consent: true, max_keys: 10 };  // plateformes reliées : testées à part

// Les cartes « Alertes de cours » et « Pilote vs consigne » chargent leurs propres données : vides par défaut.
const QUIET: Record<string, () => unknown> = {
  "/api/crypto/alerts": () => ({ alerts: [], assets: [], kinds: { above: "Seuil haut", below: "Seuil bas", change: "Variation sur 24 h" }, max: 20, bounds: { change_min: "2", change_max: "90", margin_percent: "1" } }),
  "/api/autonomy/track": () => ({ rules: [], outside: [], since: "2026-07-21" }),
};

function mockApi(given: Record<string, (init?: RequestInit) => unknown>) {
  const handlers = { ...QUIET, ...given };
  calls = [];
  const all: Record<string, (init?: RequestInit) => unknown> = { "/api/crypto/exchanges": () => NO_EXCHANGES, ...handlers };
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(all).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    const out = all[key]!(init);
    if (out instanceof Response) return Promise.resolve(out);
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
const typeInto = (el: HTMLInputElement | HTMLTextAreaElement, value: string) => act(async () => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const sent = (url: string, method: string) => calls.filter((c) => c.url.startsWith(url) && c.init?.method === method);
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;

const SEED = "abandon ability able about above absent absorb abstract absurd abuse access accident";
const ETH = "0x00000000219ab540356cBB839Cbe05303d7705Fa";

function cryptoView(over: Partial<CryptoView> = {}): CryptoView {
  return {
    total_cents: 6_661_750,
    change_24h_cents: 120_000,
    assets: [
      { asset: "BTC", name: "Bitcoin", quantity: "1", value_cents: 6_000_000, share_percent: 90.1, price_eur: "60000", change_24h: "2.50", price_at: "2026-10-21T10:00:00+00:00", stale: false, stable: null, sources: [{ where: "Cold", quantity: "1", watched: true }] },
      { asset: "ETH", name: "Ether", quantity: "0.2", value_cents: 600_100, share_percent: 9, price_eur: "3000.5", change_24h: "-1.25", price_at: "2026-10-21T10:00:00+00:00", stale: false, stable: null, sources: [{ where: "Ledger", quantity: "0.2", watched: true }] },
      { asset: "PEPE", name: "PEPE", quantity: "1000000", value_cents: 0, share_percent: 0, price_eur: null, change_24h: null, price_at: null, stale: true, stable: null, sources: [{ where: "Binance", quantity: "1000000", watched: false }] },
    ],
    wallets: [{ id: 1, label: "Ledger", chain: "ethereum", chain_label: "Ethereum", address: ETH, masked: "0x0000…05Fa", synced_at: "2026-10-21T10:00:00+00:00", error: null }],
    manual: [{ id: 4, asset: "PEPE", quantity: "1000000", platform: "Binance", foreign: true, coingecko_id: "pepe", updated_at: "2026-10-21T10:00:00+00:00" }],
    history: [{ day: "2026-10-19", value_cents: 6_000_000 }, { day: "2026-10-20", value_cents: 6_300_000 }, { day: "2026-10-21", value_cents: 6_661_750 }],
    sentiment: { value: 72, label: "Avidité" },
    tax: {
      year: 2026, error: null, total_price_cents: 100_000, threshold_cents: 30_500, exempt: false, net_gain_cents: 59_000, taxable_cents: 59_000,
      estimated_tax_cents: 18_526,
      rate: { total_percent: "31.4", social_percent: "18.6", status: "référence", note: "Flat tax de 31,4 %", checked: "2026-10-21" },
      cessions: [], acquisition_basis_cents: 60_000, current_value_cents: 6_661_750, unrealized_cents: 6_601_750,
      foreign_accounts: ["Binance"], reminders: ["Cessions en 2026 : formulaire 2086…", "1 compte sur une plateforme étrangère : un formulaire 3916-bis par compte…"],
      events: [], sources: [{ label: "impots.gouv.fr", url: "https://www.impots.gouv.fr/x" }], disclaimer: "Estimation.",
    },
    guards: [{ level: "warning", text: "La crypto pèse 80 % de ce que tu possèdes." }],
    network_consent: true,
    endpoints: { ethereum: { custom: false, shown: "https://ethereum-rpc.publicnode.com" } },
    chains: [{ key: "ethereum", label: "Ethereum", native: "ETH", tokens: ["USDC"] }, { key: "bitcoin", label: "Bitcoin", native: "BTC", tokens: [] }],
    catalog: [{ symbol: "BTC", name: "Bitcoin" }, { symbol: "ETH", name: "Ether" }],
    in_wealth: true,
    autopilot: { active: false, trial: true, agent: null, expires_at: null },
    resources: [{ label: "Info Escroqueries : 0 805 805 817", url: "" }, { label: "Pharos", url: "https://www.internet-signalement.gouv.fr" }],
    disclaimer: "Suivi et repères informatifs.",
    ...over,
  };
}

function pilotView(over: Partial<PilotView> = {}): PilotView {
  return {
    active: true, enabled: true, trial: false, agent_token_id: 3, agent_name: "hermes", mode: "rules",
    scopes: ["crypto_spot"], assets: ["BTC", "ETH"],
    rules: [{ id: "r1", label: "Acheter 20,00 € de BTC chaque semaine", kind: "buy", asset: "BTC", to_asset: null, destination: null, amount_cents: 2000, every: "week", done_this_period: true }],
    destinations: [],
    limits: { per_op_cents: 2500, per_day_cents: 5000, per_month_cents: 20000, max_moves_per_day: 5, max_loss_cents: 10000 },
    enabled_at: "2026-10-20T10:00:00+00:00", expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString(),
    disabled_at: null, disabled_reason: null,
    usage: { day_cents: 2000, month_cents: 2000, moves_day: 1, loss_cents: 0 },
    agents: [{ id: 3, name: "hermes", ip_locked: false }], no_crypto: false,
    scope_labels: { crypto_spot: "Acheter, vendre, échanger", transfer: "Envoyer vers tes adresses" },
    every_labels: { day: "chaque jour", week: "chaque semaine", month: "chaque mois" },
    chains: ["bitcoin", "ethereum"], hard_rules: ["Jamais d'effet de levier."],
    hard_max: { per_op_cents: 100000, per_day_cents: 200000, per_month_cents: 1000000, days: 90 },
    journal: [
      { id: 2, agent: "hermes", kind: "perps", asset: "ETH", to_asset: null, chain: null, amount_cents: 500, executed_cents: null, pnl_cents: null, destination: null, rule: null, reason: "long 10x", status: "refused", refusal: "Jamais en autonome : les contrats à terme (perps)", tx_ref: null, text: "perps de 5,00 € de ETH", created_at: "2026-10-21T09:00:00+00:00", reported_at: null },
      { id: 1, agent: "hermes", kind: "buy", asset: "BTC", to_asset: null, chain: null, amount_cents: 2000, executed_cents: 2000, pnl_cents: null, destination: null, rule: "Acheter 20,00 € de BTC chaque semaine", reason: "DCA", status: "executed", refusal: null, tx_ref: "0xabc", text: "achat de 20,00 € de BTC", created_at: "2026-10-21T08:00:00+00:00", reported_at: "2026-10-21T08:01:00+00:00" },
    ],
    ...over,
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

describe("helpers crypto", () => {
  it("écrit les quantités exactes à la française", () => {
    expect(qty("0.00012345")).toBe("0,00012345");
    expect(qty("1250000.5")).toBe("1 250 000,5");
    expect(qty("92582220.583693586399106838")).toBe("92 582 220,58");
    expect(qty("92582220.583693586399106838", true)).toBe("92 582 220,583693586399106838");
    expect(qty("1.350000001")).toBe("1,35");
    expect(qty("0.000000014")).toBe("0,00000001");
    expect(price("0.92")).toMatch(/0,92/);
    expect(price(null)).toBe("—");
  });
  it("arrête une phrase de récupération ou une clé dans le navigateur", () => {
    expect(looksSecret(SEED)).toBe("une phrase de récupération");
    expect(looksSecret("ab".repeat(32))).toBe("une clé privée");
    expect(looksSecret("5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ")).toMatch(/WIF/);
    expect(looksSecret(ETH)).toBeNull();
    expect(looksSecret("bonjour je voudrais envoyer mes bitcoins a mon ami pour son anniversaire demain", { words: false })).toBeNull();
  });
  it("devine le réseau d'une adresse collée", () => {
    expect(guessChain(ETH)).toBe("ethereum");
    expect(guessChain("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")).toBe("bitcoin");
    expect(guessChain("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")).toBe("solana");
    expect(guessChain("bonjour")).toBeNull();
  });
});

describe("écran Crypto", () => {
  it("montre la valeur, le ruban, les actifs, la fiscalité et l'humeur", async () => {
    mockApi({ "/api/crypto": () => cryptoView(), "/api/consents": () => [] });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    const text = host.textContent ?? "";
    expect(text).toContain("Ta crypto aujourd'hui");
    expect(host.querySelector(".cx-ribbon-bar")?.getAttribute("aria-label")).toContain("BTC 90 %");
    expect(text).toContain("sans cours");
    expect(text).toContain("3916-bis");
    expect(text).toContain("31,4 %");
    expect(host.querySelector("[aria-label^='Humeur du marché : 72']")).not.toBeNull();
    expect(text).toContain("La crypto pèse 80 %");
  });

  it("relit les alertes de cours après un relevé, qui peut en déclencher une", async () => {
    mockApi({
      "/api/crypto/sync": () => ({ result: { wallets_synced: 1, wallets_failed: 0, prices: 2, alerts: 1 }, view: cryptoView() }),
      "/api/crypto": () => cryptoView(),
      "/api/consents": () => [],
    });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    const reads = () => calls.filter((c) => c.url === "/api/crypto/alerts" && c.init?.method === "GET").length;
    expect(reads()).toBe(1);
    await click("Relever maintenant");
    expect(sent("/api/crypto/sync", "POST")).toHaveLength(1);
    expect(reads()).toBe(2);
  });

  it("ne laisse jamais partir une phrase de récupération collée comme adresse", async () => {
    mockApi({ "/api/crypto": () => cryptoView({ wallets: [] }), "/api/consents": () => [] });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    const [label, address] = [...host.querySelectorAll<HTMLInputElement>("#cx-wallets ~ form input, form.stack input")];
    await typeInto(label!, "Ledger");
    await typeInto(address!, SEED);
    expect(host.textContent).toContain("Stop : ceci ressemble à une phrase de récupération");
    expect(button("Suivre cette adresse").disabled).toBe(true);
    await act(async () => { button("Suivre cette adresse").closest("form")!.requestSubmit(); });
    await settle();
    expect(sent("/api/crypto/wallets", "POST")).toHaveLength(0);
    expect(calls.some((c) => String(c.init?.body ?? "").includes("abandon"))).toBe(false);
  });

  it("suit une adresse et propose de confirmer un sosie", async () => {
    let first = true;
    mockApi({
      "/api/crypto/wallets": () => {
        if (first) {
          first = false;
          return new Response(JSON.stringify({ detail: "Attention : cette adresse ressemble à « Ledger »… empoisonnement d'adresse." }), { status: 422, headers: { "content-type": "application/json" } });
        }
        return { id: 2, address: ETH };
      },
      "/api/crypto": () => cryptoView(),
      "/api/consents": () => [],
    });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    const inputs = [...host.querySelectorAll<HTMLInputElement>("form.stack input")];
    await typeInto(inputs[0]!, "Copie");
    await typeInto(inputs[1]!, ETH);
    await act(async () => { button("Suivre cette adresse").closest("form")!.requestSubmit(); });
    await settle();
    expect(host.textContent).toContain("empoisonnement");
    await click("J'ai comparé caractère par caractère");
    const posts = sent("/api/crypto/wallets", "POST");
    expect(posts).toHaveLength(2);
    expect(body(posts[1]!)).toMatchObject({ label: "Copie", chain: "ethereum", confirm_lookalike: true });
  });

  it("demande l'accord avant toute relève réseau", async () => {
    mockApi({
      "/api/crypto": () => cryptoView({ network_consent: false }),
      "/api/consents/crypto_network": () => ({ ok: true }),
      "/api/consents": () => [{ purpose: "crypto_network", title: "Suivi", text: "Ils voient l'adresse demandée.", granted: false, explicit: false, decided: false, updated_at: null, default: false }],
    });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("Ils voient l'adresse demandée.");
    expect(() => button("Relever maintenant")).toThrow();
    await click("Oui, relever mes adresses");
    expect(body(sent("/api/consents/crypto_network", "PUT")[0]!)).toEqual({ granted: true });
  });

  it("vérifie un message suspect", async () => {
    mockApi({
      "/api/crypto/check": () => ({ level: "danger", verdict: "Très probablement une arnaque", signals: [{ key: "seed", label: "On te demande ta phrase" }], advice: [], resources: [] }),
      "/api/crypto": () => cryptoView(),
      "/api/consents": () => [],
    });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    await typeInto(host.querySelector("textarea")!, "Ici le support Ledger, donnez votre phrase de récupération");
    await act(async () => { host.querySelector("textarea")!.closest("form")!.requestSubmit(); });
    await settle();
    expect(host.querySelector(".cx-verdict.is-danger")?.textContent).toContain("Très probablement une arnaque");
    expect(host.textContent).toContain("0 805 805 817");
  });
});

describe("réglages du suivi crypto", () => {
  it("ne montre jamais un nœud perso en entier et exige le mot de passe pour en mettre un", async () => {
    const custom = cryptoView({ endpoints: { ethereum: { custom: true, shown: "https://node.example.org/…" } } });
    mockApi({ "/api/crypto/settings": () => custom, "/api/crypto": () => custom, "/api/consents": () => [] });
    await act(async () => root.render(<ToastProvider><Crypto /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("Ton nœud : https://node.example.org/…");
    const input = host.querySelector<HTMLInputElement>("input[placeholder='Remplacer par…']")!;
    expect(input.value).toBe("");
    expect(button("Enregistrer les nœuds").disabled).toBe(true);
    await typeInto(input, "https://autre.example.org/v3/cle");
    await settle();
    const pw = host.querySelector<HTMLInputElement>(".cx-details input[type=password]")!;
    expect(pw).not.toBeNull();
    await typeInto(pw, "ma phrase de passe");
    await act(async () => { button("Enregistrer les nœuds").closest("form")!.requestSubmit(); });
    await settle();
    const put = sent("/api/crypto/settings", "PUT");
    expect(body(put[0]!)).toEqual({ endpoints: { ethereum: "https://autre.example.org/v3/cle" }, password: "ma phrase de passe" });
    await click("Nœud public");
    expect(body(sent("/api/crypto/settings", "PUT")[1]!)).toEqual({ endpoints: { ethereum: null } });
  });
});

describe("écran Pilote automatique", () => {
  it("affiche l'état, les jauges et le journal, et s'arrête sans mot de passe", async () => {
    mockApi({
      "/api/autonomy/stop": () => pilotView({ active: false, enabled: false, disabled_reason: "Arrêté par toi", disabled_at: "2026-10-21T10:00:00+00:00" }),
      "/api/autonomy": () => pilotView(),
      "/api/auth/me": () => ({ username: "micka", totp_enabled: true, recovery_codes_left: 8, session_expires_at: "" }),
    });
    await act(async () => root.render(<ToastProvider><Pilot /></ToastProvider>));
    await settle();
    expect(host.querySelector(".pl-state")?.textContent).toBe("En marche");
    expect(host.textContent).toContain("Jamais en autonome : les contrats à terme (perps)");
    expect(host.textContent).toContain("Code de double authentification");
    await click("Tout arrêter");
    const stop = sent("/api/autonomy/stop", "POST");
    expect(stop).toHaveLength(1);
    expect(stop[0]!.init?.body).toBeUndefined();
    expect(host.querySelector(".pl-state")?.textContent).toBe("Arrêté");
  });

  it("règle le cadre avec le mot de passe, en essai par défaut", async () => {
    mockApi({
      "/api/autonomy": (init) => (init?.method === "PUT" ? pilotView({ trial: true }) : pilotView({ active: false, enabled: false, rules: [] })),
      "/api/auth/me": () => ({ username: "micka", totp_enabled: false, recovery_codes_left: 8, session_expires_at: "" }),
    });
    await act(async () => root.render(<ToastProvider><Pilot /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("Ce jeton marche depuis n'importe quelle adresse");
    expect(host.textContent).toContain("0 € : dès la première perte");
    await click("Ajouter la consigne");
    await typeInto(host.querySelector<HTMLInputElement>("input[type=password]")!, "phrase de passe");
    await act(async () => { button("Lancer l'essai").closest("form")!.requestSubmit(); });
    await settle();
    const put = sent("/api/autonomy", "PUT");
    expect(put).toHaveLength(1);
    const b = body(put[0]!);
    expect(b).toMatchObject({ enabled: true, trial: true, password: "phrase de passe", agent_token_id: 3, days: 30, mode: "rules" });
    expect((b.rules as unknown[]).length).toBe(1);
    expect(host.querySelector(".pl-state")?.textContent).toBe("En essai");
  });

  it("explique une consigne en clair", () => {
    expect(ruleText({ kind: "transfer", asset: "USDC", to_asset: null, destination: "Ledger", amount_cents: 5000, every: "month" }, { month: "chaque mois" }))
      .toBe("Envoyer 50,00 € de USDC vers « Ledger » chaque mois");
  });
});
