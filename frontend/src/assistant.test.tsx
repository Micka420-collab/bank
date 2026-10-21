// « Demande à Bank » (OpenRouter) : réglages de la clé, panneau de conversation — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantStatus } from "./api";
import { Assistant, costLabel, SUGGESTIONS } from "./components/Assistant";
import { ToastProvider } from "./components/ui";
import { AssistantSettings, priceLabel, SUGGESTED_MODELS, usd } from "./pages/AssistantSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MODES = {
  full: "Complet : libellés tels quels (dont les noms de particuliers dans les virements)",
  redacted: "Anonymisé : marchands et montants, jamais de nom de personne, d'IBAN ni de numéro",
  aggregates: "Chiffres seuls : totaux et moyennes, aucun libellé",
};
const OFF: AssistantStatus = { configured: false, privacy_modes: MODES };
const ON: AssistantStatus = {
  configured: true,
  privacy_modes: MODES,
  provider: "openrouter",
  key_prefix: "sk-or-v1-012…",
  model: "anthropic/claude-haiku-5.5",
  privacy: "redacted",
  monthly_cap_usd_cents: 500,
  daily_calls_cap: 60,
  month_cost_usd_cents: 42,
  month_cost_microusd: 420_000,
  calls_today: 3,
  calls_month: 12,
  last_used_at: "2026-10-21T10:00:00+00:00",
  last_error: null,
  created_at: "2026-10-01T10:00:00+00:00",
};

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
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 5; i++) await flush(); };
const render = (node: React.ReactNode) => act(async () => root.render(node));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label) || x.getAttribute("aria-label") === label);
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });
const typeInto = (el: HTMLInputElement | HTMLTextAreaElement, value: string) => act(async () => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const body = (url: string) => JSON.parse(String(calls.find((c) => c.url === url && c.init?.method && c.init.method !== "GET")?.init?.body ?? "{}"));

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

describe("réglages de l'assistant", () => {
  it("adds a key with the password, never shows it back, and lets the model be picked offline", async () => {
    let status = OFF;
    mockApi({
      "/api/assistant/models": () => new Response(JSON.stringify({ detail: "OpenRouter injoignable (réseau)" }), { status: 502, headers: { "content-type": "application/json" } }),
      "/api/assistant": (init) => {
        if (init?.method === "PUT") status = ON;
        return status;
      },
    });
    await render(<ToastProvider><AssistantSettings /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Pas encore de clé");
    await click("Ajouter ma clé OpenRouter");
    await settle();
    // Liste indisponible : des modèles courants restent proposés.
    expect(host.textContent).toContain("Liste des modèles indisponible");
    expect(host.querySelectorAll("#assistant-models option").length).toBeGreaterThan(0);
    const key = host.querySelector<HTMLInputElement>("input[autocomplete=off][type=password]")!;
    const pw = host.querySelector<HTMLInputElement>("input[autocomplete=current-password]")!;
    await typeInto(key, "sk-or-v1-0123456789abcdef0123456789abcdef");
    await typeInto(pw, "demo demo demo 42");
    await act(async () => host.querySelector("form")!.requestSubmit());
    await settle();
    const sent = body("/api/assistant");
    expect(sent).toMatchObject({ api_key: "sk-or-v1-0123456789abcdef0123456789abcdef", password: "demo demo demo 42", privacy: "redacted", monthly_cap_usd_cents: 500, daily_calls_cap: 60 });
    expect(host.textContent).toContain("anthropic/claude-haiku-5.5");
    expect(host.textContent).toContain("sk-or-v1-012…");
    expect(host.textContent).not.toContain("0123456789abcdef0123");
    expect(host.textContent).toContain("0,42 $");
  });

  it("tests the key and shows a provider error plainly", async () => {
    mockApi({
      "/api/assistant/test": () => new Response(JSON.stringify({ detail: "Clé OpenRouter refusée (401) : vérifie-la ou crée-en une nouvelle" }), { status: 409, headers: { "content-type": "application/json" } }),
      "/api/assistant": () => ON,
    });
    await render(<ToastProvider><AssistantSettings /></ToastProvider>);
    await settle();
    await click("Tester");
    await settle();
    expect(host.querySelector("[role=alert]")?.textContent).toContain("Clé OpenRouter refusée");
  });

  it("formats prices and money without surprises", () => {
    expect(usd(500).replace(/\s/g, " ")).toBe("5,00 $");
    expect(priceLabel(SUGGESTED_MODELS.find((m) => m.free)!)).toBe("gratuit");
    expect(priceLabel({ ...SUGGESTED_MODELS[0]!, free: false, prompt_usd_per_million: 1, completion_usd_per_million: 5 })).toBe("1 $ / 5 $ par million de jetons");
    expect(costLabel(0)).toBe("gratuit");
    expect(costLabel(0.0042)).toBe("0,42 ¢");
    expect(costLabel(0.25).replace(/\s/g, " ")).toBe("0,25 $");
  });
});

describe("panneau « Demande à Bank »", () => {
  it("points to Connecteurs when no key is set", async () => {
    mockApi({ "/api/assistant": () => OFF });
    await render(<Assistant open onClose={() => undefined} />);
    await settle();
    expect(host.textContent).toContain("pas encore branché");
    expect(host.querySelector("a[href='#/connecteurs']")).not.toBeNull();
    expect(host.querySelector("textarea")).toBeNull();
  });

  it("asks with the recent history, shows who answered and at what cost, and keeps nothing on close", async () => {
    let n = 0;
    mockApi({
      "/api/assistant/ask": () => {
        n += 1;
        return { answer: n === 1 ? "Tu peux dépenser **75 €** par jour." : "- Loyer : 650 €", model: "anthropic/claude-haiku-5.5", privacy: "redacted", cost_usd: 0.0021, prompt_tokens: 1500, completion_tokens: 60, truncated: n === 2, calls_today: 3 + n, month_cost_usd_cents: 43 };
      },
      "/api/assistant": () => ON,
    });
    const onClose = vi.fn();
    await render(<Assistant open onClose={onClose} />);
    await settle();
    expect(SUGGESTIONS.every((s) => host.textContent?.includes(s))).toBe(true);
    await click(SUGGESTIONS[0]!);
    await settle();
    expect(body("/api/assistant/ask")).toEqual({ question: SUGGESTIONS[0], history: [] });
    expect(host.querySelector(".assistant-turn.is-assistant strong")?.textContent).toBe("75 €");
    expect(host.textContent).toContain("anthropic/claude-haiku-5.5 · 0,21 ¢ · dossier anonymisé");

    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await typeInto(ta, "Et mon loyer ?");
    await act(async () => { ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    const second = JSON.parse(String(calls.filter((c) => c.url === "/api/assistant/ask")[1]!.init!.body));
    expect(second.question).toBe("Et mon loyer ?");
    expect(second.history.map((t: { role: string }) => t.role)).toEqual(["user", "assistant"]);
    expect(host.textContent).toContain("réponse coupée");

    await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(onClose).toHaveBeenCalled();
  });

  it("puts the question back when the model fails", async () => {
    mockApi({
      "/api/assistant/ask": () => new Response(JSON.stringify({ detail: "Plafond mensuel de crédits atteint (5.00 $)" }), { status: 429, headers: { "content-type": "application/json" } }),
      "/api/assistant": () => ON,
    });
    await render(<Assistant open onClose={() => undefined} />);
    await settle();
    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await typeInto(ta, "Combien sur mon livret ?");
    await act(async () => host.querySelector("form")!.requestSubmit());
    await settle();
    expect(host.querySelector("[role=alert]")?.textContent).toContain("Plafond mensuel");
    expect(host.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe("Combien sur mon livret ?");
    expect(host.querySelectorAll(".assistant-turn.is-user")).toHaveLength(0);
  });
});
