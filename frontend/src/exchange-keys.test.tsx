// Plateformes reliées : une clé en lecture seule se relie après vérification (mot de passe Bank, droits), une clé
// trop large est refusée avec la raison, le secret n'apparaît jamais, une clé de portefeuille collée est arrêtée
// dans le navigateur, et une clé se relève ou se retire en un geste.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExchangeKeysView, ExchangeKeyView } from "./api";
import { ExchangeKeys, rightsText, syncText } from "./components/ExchangeKeys";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[   ]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const type = async (label: string, value: string) => {
  const field = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label));
  const input = field?.querySelector("input, textarea, select") as HTMLInputElement | HTMLTextAreaElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
  await settle();
};

const SECRET = "SuperSecretPrivateKey0123456789==";
const PLATFORMS: ExchangeKeysView["platforms"] = [
  { key: "kraken", label: "Kraken", help: "Settings › API : coche seulement « Query Funds ».", url: "https://www.kraken.com/u/security/api", key_label: "API Key", secret_label: "Private Key" },
  { key: "binance", label: "Binance", help: "Gestion API : laisse seulement « Activer la lecture ».", url: "https://www.binance.com/fr/my/settings/api-management", key_label: "API Key", secret_label: "Secret Key" },
  { key: "coinbase", label: "Coinbase", help: "Portail CDP : permission « View » seulement.", url: "https://portal.cdp.coinbase.com/access/api", key_label: "Nom de la clé", secret_label: "Clé privée EC (PEM)" },
];
const KEY: ExchangeKeyView = {
  id: 1, platform: "kraken", platform_label: "Kraken", label: "Kraken perso", key_hint: "…CD==", foreign: true,
  rights: { read: true, trade: false, withdraw: false, source: "probes", ip_restricted: null },
  verified_at: "2026-10-24T10:00:00+00:00", synced_at: "2026-10-24T10:00:00+00:00", error: null,
  assets: [{ asset: "BTC", quantity: "0.5" }, { asset: "ETH", quantity: "2" }],
};

function mockApi(state: { st: ExchangeKeysView }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/crypto/exchanges" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      if (body.password !== "bank-pass") return json({ detail: "Mot de passe incorrect" }, 403);
      if (body.api_key === "FULL-KEY") return json({ detail: "Cette clé peut retirer des fonds et passer des ordres : Bank la refuse et ne la garde pas." }, 422);
      const created = { ...KEY, id: 7, platform: body.platform, label: body.label || "Kraken", foreign: body.foreign };
      state.st = { ...state.st, keys: [...state.st.keys, created] };
      return json({ ...state.st, created_id: 7 }, 201);
    }
    if (url === "/api/crypto/exchanges/1/sync") {
      state.st = { ...state.st, keys: state.st.keys.map((k) => (k.id === 1 ? { ...k, error: "Kraken répond 503" } : k)) };
      return json(state.st);
    }
    if (url === "/api/crypto/exchanges/1" && init?.method === "DELETE") {
      state.st = { ...state.st, keys: state.st.keys.filter((k) => k.id !== 1) };
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url === "/api/crypto/exchanges") return json(state.st);
    return json({}, 404);
  });
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  vi.stubGlobal("confirm", () => true);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("plateformes reliées", () => {
  it("words the rights and the last sync", () => {
    expect(rightsText(KEY)).toBe("lecture seule · vérifiée par sondes");
    expect(rightsText({ ...KEY, rights: { read: true, trade: false, withdraw: false, source: "permissions" } })).toBe("lecture seule · droits lus sur la plateforme");
    expect(rightsText({ ...KEY, rights: { read: true, trade: true, withdraw: false } })).toBe("droits trop larges");
    expect(syncText(KEY).replace(/[  ]/g, " ")).toBe("relevée le 24 oct 2026 à 10:00 UTC");
    expect(syncText({ ...KEY, error: "Kraken répond 503" })).toBe("Kraken répond 503");
    expect(syncText({ ...KEY, synced_at: null })).toBe("jamais relevée");
  });

  it("links a read-only key after the password and rights checks, never showing the secret", async () => {
    const state = { st: { keys: [], platforms: PLATFORMS, network_consent: true, max_keys: 10 } as ExchangeKeysView };
    mockApi(state);
    const changed = vi.fn();
    await act(async () => root.render(<ToastProvider><ExchangeKeys consent onChange={changed} /></ToastProvider>));
    await settle();
    expect(text()).toContain("Aucune plateforme reliée.");
    await click("Relier une plateforme");
    expect(text()).toContain("coche seulement « Query Funds »");
    await type("Plateforme", "binance");
    expect(text()).toContain("laisse seulement « Activer la lecture »");
    await type("Plateforme", "kraken");
    await type("Nom", "Kraken perso");
    await type("API Key", "KRAKEN-KEY-ABCD");
    await type("Private Key", SECRET);
    await type("Ton mot de passe Bank", "mauvais");
    await click("Vérifier et relier");
    expect(text()).toContain("Mot de passe incorrect");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Vérifier et relier");
    const post = calls.filter((c) => c.url === "/api/crypto/exchanges" && c.init?.method === "POST").pop();
    expect(JSON.parse(String(post?.init?.body))).toEqual({
      password: "bank-pass", platform: "kraken", label: "Kraken perso", api_key: "KRAKEN-KEY-ABCD", api_secret: SECRET, foreign: true,
    });
    expect(text()).toContain("Kraken perso");
    expect(text()).toContain("…CD==");
    expect(text()).toContain("lecture seule · vérifiée par sondes");
    expect(text()).toContain("étrangère · 3916-bis");
    expect(text()).toContain("0,5 BTC · 2 ETH");
    expect(text()).not.toContain(SECRET);
    expect(text()).not.toContain("KRAKEN-KEY-ABCD");
    expect(changed).toHaveBeenCalled();
  });

  it("refuses a key with trade or withdrawal rights, and a pasted wallet key before it leaves the browser", async () => {
    const state = { st: { keys: [], platforms: PLATFORMS, network_consent: true, max_keys: 10 } as ExchangeKeysView };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><ExchangeKeys consent onChange={() => {}} /></ToastProvider>));
    await settle();
    await click("Relier une plateforme");
    await type("API Key", "FULL-KEY");
    await type("Private Key", SECRET);
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Vérifier et relier");
    expect(text()).toContain("Cette clé peut retirer des fonds et passer des ordres : Bank la refuse");
    expect(host.querySelectorAll(".xk-item")).toHaveLength(0);
    const posts = calls.filter((c) => c.init?.method === "POST").length;
    await type("API Key", "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about");
    expect(text()).toContain("ressemble à une phrase de récupération");
    expect(button("Vérifier et relier").disabled).toBe(true);
    await act(async () => { host.querySelector("form")?.requestSubmit(); });
    await settle();
    expect(calls.filter((c) => c.init?.method === "POST").length).toBe(posts);
  });

  it("syncs a key, shows its failure under it, and revokes it", async () => {
    const state = { st: { keys: [KEY], platforms: PLATFORMS, network_consent: true, max_keys: 10 } as ExchangeKeysView };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><ExchangeKeys consent onChange={() => {}} /></ToastProvider>));
    await settle();
    expect(text()).toContain("relevée le 24 oct 2026");
    await click("Relever");
    expect(calls.some((c) => c.url === "/api/crypto/exchanges/1/sync" && c.init?.method === "POST")).toBe(true);
    expect(text()).toContain("Kraken répond 503");
    await click("Retirer");
    expect(calls.some((c) => c.url === "/api/crypto/exchanges/1" && c.init?.method === "DELETE")).toBe(true);
    expect(host.querySelectorAll(".xk-item")).toHaveLength(0);
  });

  it("waits for the network consent", async () => {
    mockApi({ st: { keys: [], platforms: PLATFORMS, network_consent: false, max_keys: 10 } });
    await act(async () => root.render(<ToastProvider><ExchangeKeys consent={false} onChange={() => {}} /></ToastProvider>));
    await settle();
    expect(text()).toContain("Donne d'abord ton accord");
    expect(button("Relier une plateforme").disabled).toBe(true);
  });
});
