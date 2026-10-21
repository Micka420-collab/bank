// « Demande à Bank » (OpenRouter) : réglages de la clé, panneau de conversation — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantAnswer, AssistantStatus, ConsentView, MemoryView } from "./api";
import { Assistant, costLabel, SUGGESTIONS } from "./components/Assistant";
import { ToastProvider } from "./components/ui";
import { AssistantSettings, priceLabel, SUGGESTED_MODELS, usd } from "./pages/AssistantSettings";
import { Memory, retentionLabel } from "./pages/Memory";
import { Privacy } from "./pages/Settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MODES = {
  full: "Complet : libellés tels quels (dont les noms de particuliers dans les virements)",
  redacted: "Anonymisé : marchands et montants, jamais de nom de personne, d'IBAN ni de numéro",
  aggregates: "Chiffres seuls : totaux et moyennes, aucun libellé",
};
const OFF: AssistantStatus = { configured: false, privacy_modes: MODES, name: "Bank", memory: { consent: false, asked: false, available: true } };
const ON: AssistantStatus = {
  configured: true,
  privacy_modes: MODES,
  name: "Bank",
  memory: { consent: false, asked: true, available: true },
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

function answerOf(answer: string, over: Partial<AssistantAnswer> = {}): AssistantAnswer {
  return {
    answer, conversation_id: 7, message_id: 11, model: "anthropic/claude-haiku-5.5", name: "Bank", privacy: "redacted",
    memory_on: false, memory: [], tools: [], cost_usd: 0.0021, prompt_tokens: 1500, completion_tokens: 60, truncated: false,
    review_due: false, calls_today: 4, month_cost_usd_cents: 43, ...over,
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
      "/api/assistant/conversations": () => [],
      "/api/assistant/ask": () => {
        n += 1;
        return answerOf(n === 1 ? "Tu peux dépenser **75 €** par jour." : "- Loyer : 650 €", { truncated: n === 2, message_id: 10 + n });
      },
      "/api/assistant": () => ON,
    });
    const onClose = vi.fn();
    await render(<Assistant open onClose={onClose} />);
    await settle();
    expect(SUGGESTIONS.every((s) => host.textContent?.includes(s))).toBe(true);
    await click(SUGGESTIONS[0]!);
    await settle();
    expect(body("/api/assistant/ask")).toEqual({ question: SUGGESTIONS[0], conversation_id: null });
    expect(host.querySelector(".assistant-turn.is-assistant strong")?.textContent).toBe("75 €");
    expect(host.textContent).toContain("anthropic/claude-haiku-5.5 · 0,21 ¢ · dossier anonymisé");

    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await typeInto(ta, "Et mon loyer ?");
    await act(async () => { ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    const second = JSON.parse(String(calls.filter((c) => c.url === "/api/assistant/ask")[1]!.init!.body));
    expect(second).toEqual({ question: "Et mon loyer ?", conversation_id: 7 }); // l'historique vit côté serveur
    expect(host.textContent).toContain("réponse coupée");
    expect(host.textContent).toContain("Sans mémoire : rien n'est appris");

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


describe("mémoire de l'assistant", () => {
  const MEMORY_ON: AssistantStatus = { ...ON, memory: { consent: true, asked: true, available: true } };

  it("asks once whether it may remember, and records the answer", async () => {
    let status: AssistantStatus = { ...ON, memory: { consent: false, asked: false, available: true } };
    mockApi({
      "/api/assistant/conversations": () => [],
      "/api/consents/ai_memory": () => {
        status = MEMORY_ON;
        return { purpose: "ai_memory", granted: true };
      },
      "/api/assistant": () => status,
    });
    await render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Je peux me souvenir de toi");
    await click("Oui, souviens-toi");
    await settle();
    expect(body("/api/consents/ai_memory")).toEqual({ granted: true });
    expect(host.textContent).not.toContain("Je peux me souvenir de toi");
    expect(host.textContent).toContain("se souvient de toi");
    expect(host.querySelector("a[href='#/memoire']")).not.toBeNull();
  });

  it("shows what was just remembered, lets it be undone, and takes feedback", async () => {
    mockApi({
      "/api/assistant/conversations": () => [],
      "/api/assistant/ask": () => answerOf("C'est noté, je ferai court.", {
        memory_on: true,
        memory: [{ action: "add", store: "user", id: 42, content: "Préfère qu'on aille droit au but." }],
      }),
      "/api/assistant/messages/11/feedback": () => new Response(null, { status: 204 }),
      "/api/memory/entries/42": () => new Response(null, { status: 204 }),
      "/api/assistant": () => MEMORY_ON,
    });
    await render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>);
    await settle();
    await click(SUGGESTIONS[1]!);
    await settle();
    const note = host.querySelector(".memory-notes li")!;
    expect(note.textContent).toContain("Retenu « Préfère qu'on aille droit au but. »");
    await click("Annuler");
    await settle();
    expect(calls.some((c) => c.url === "/api/memory/entries/42" && c.init?.method === "DELETE")).toBe(true);
    expect(host.querySelector(".memory-notes li")!.className).toContain("is-undone");
    expect(host.querySelector(".memory-notes li")!.textContent).toContain("Annulé");
    // 👎 puis un mot d'explication : deux envois, le second avec la note
    await click("Réponse à revoir");
    await settle();
    const input = host.querySelector<HTMLInputElement>(".assistant-note input")!;
    await typeInto(input, "trop vague");
    await act(async () => host.querySelector<HTMLFormElement>(".assistant-note")!.requestSubmit());
    await settle();
    const sent = calls.filter((c) => c.url === "/api/assistant/messages/11/feedback").map((c) => JSON.parse(String(c.init!.body)));
    expect(sent).toEqual([{ value: "down", note: null }, { value: "down", note: "trop vague" }]);
    expect(button("Réponse à revoir").getAttribute("aria-pressed")).toBe("true");
    expect(host.textContent).toContain("visible et effaçable");
  });

  it("picks up a recent open conversation and can start a new one", async () => {
    const recent = new Date(Date.now() - 20 * 60_000).toISOString();
    mockApi({
      "/api/assistant/conversations/5/close": () => new Response(null, { status: 204 }),
      "/api/assistant/conversations/5": () => ({
        id: 5, title: "Vacances", summary: null, memory_on: true, closed: false,
        messages: [
          { id: 1, role: "user", content: "On parlait du Japon", ts: recent, meta: {}, feedback: null },
          { id: 2, role: "assistant", content: "Oui, **300 €** par mois.", ts: recent, meta: { model: "m/x", cost_usd: 0, privacy: "redacted" }, feedback: "up" },
        ],
      }),
      "/api/assistant/conversations": () => [{ id: 5, title: "Vacances", summary: null, started_at: recent, last_at: recent, turns: 1, memory_on: true, closed: false }],
      "/api/assistant": () => MEMORY_ON,
    });
    await render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Suite de votre dernière conversation");
    expect(host.querySelector(".assistant-turn.is-assistant strong")?.textContent).toBe("300 €");
    expect(button("Réponse utile").getAttribute("aria-pressed")).toBe("true");
    await click("Nouvelle conversation");
    await settle();
    expect(calls.some((c) => c.url === "/api/assistant/conversations/5/close" && c.init?.method === "POST")).toBe(true);
    expect(host.querySelectorAll(".assistant-turn")).toHaveLength(0);
    expect(SUGGESTIONS.every((x) => host.textContent?.includes(x))).toBe(true);
  });

  it("does not resume an old or closed conversation", async () => {
    const old = new Date(Date.now() - 8 * 3_600_000).toISOString();
    mockApi({
      "/api/assistant/conversations": () => [{ id: 6, title: "Vieux", summary: null, started_at: old, last_at: old, turns: 3, memory_on: true, closed: false }],
      "/api/assistant": () => MEMORY_ON,
    });
    await render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>);
    await settle();
    expect(calls.some((c) => c.url === "/api/assistant/conversations/6")).toBe(false);
    expect(host.textContent).not.toContain("Suite de votre dernière conversation");
  });
});

describe("page « Ce que Bank retient »", () => {
  const view = (over: Partial<MemoryView> = {}): MemoryView => ({
    stores: {
      user: {
        limit: 1500, used: 1400, usage: "93 %",
        entries: [
          { id: 1, content: "Vit à Lyon.", pinned: true, origin: "user", blocked: false, created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z" },
          { id: 2, content: "[injection]", pinned: false, origin: "chat", blocked: true, created_at: "2026-10-02T10:00:00Z", updated_at: "2026-10-02T10:00:00Z" },
        ],
      },
      notes: { limit: 2200, used: 0, usage: "0 %", entries: [] },
    },
    proposals: [{ id: 9, store: "user", action: "patch", old_text: "Vit à Lyon.", new_text: "Vit à Villeurbanne.", reason: "a déménagé", created_at: "2026-10-03T10:00:00Z" }],
    name: "Nova", retention_days: 365, retention_choices: [30, 90, 365, 1095, 0], conversations: 1,
    consent: true, available: true, titles: { user: "", notes: "" }, ...over,
  });

  it("shows both notebooks, validates a proposal, adds, pins and forgets", async () => {
    let current = view();
    mockApi({
      "/api/memory/proposals/9": () => (current = view({ proposals: [] })),
      "/api/memory/entries/1": () => current,
      "/api/memory/entries": () => current,
      "/api/memory/forget": (init) => {
        const sent = JSON.parse(String(init?.body));
        if (sent.password !== "bon mot de passe") return new Response(JSON.stringify({ detail: "Mot de passe incorrect" }), { status: 403, headers: { "content-type": "application/json" } });
        return (current = { ...view({ proposals: [] }), forgotten: { entries: 2, conversations: 1 } });
      },
      "/api/memory": () => current,
      "/api/assistant/conversations": () => [{ id: 3, title: "Budget vacances", summary: "Point sur l'été.", started_at: "2026-10-03T10:00:00Z", last_at: "2026-10-03T10:00:00Z", turns: 8, memory_on: true, closed: true }],
    });
    await render(<ToastProvider><Memory /></ToastProvider>);
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Ce que Nova retient");
    expect(host.textContent).toContain("Mémoire active");
    expect(host.textContent).toContain("bloquée : jamais montrée au modèle");
    expect(host.querySelector(".memory-gauge")!.className).toContain("is-full");
    expect(host.textContent).toContain("Budget vacances");
    await click("Valider");
    await settle();
    expect(body("/api/memory/proposals/9")).toEqual({ approve: true });
    expect(host.textContent).not.toContain("À valider");
    const add = host.querySelectorAll<HTMLInputElement>(".memory-add input")[1]!;
    await typeInto(add, "Le loyer part le 5.");
    await act(async () => host.querySelectorAll<HTMLFormElement>(".memory-add")[1]!.requestSubmit());
    await settle();
    expect(body("/api/memory/entries")).toEqual({ store: "notes", content: "Le loyer part le 5." });
    await click("Désépingler");
    await settle();
    expect(body("/api/memory/entries/1")).toEqual({ pinned: false });
    await click("Tout oublier…");
    const pw = host.querySelector<HTMLInputElement>(".memory-forget input[type=password]")!;
    await typeInto(pw, "faux");
    await act(async () => host.querySelector<HTMLFormElement>(".memory-forget form")!.requestSubmit());
    await settle();
    expect(host.querySelector(".memory-forget [role=alert]")?.textContent).toContain("Mot de passe incorrect");
    await typeInto(pw, "bon mot de passe");
    await act(async () => host.querySelector<HTMLFormElement>(".memory-forget form")!.requestSubmit());
    await settle();
    expect(host.textContent).toContain("Nova a tout oublié (2 entrées, 1 conversation)");
  });

  it("offers to turn memory on when it is off", async () => {
    let current = view({ consent: false, proposals: [] });
    mockApi({
      "/api/consents/ai_memory": () => {
        current = view({ proposals: [] });
        return { granted: true };
      },
      "/api/memory": () => current,
      "/api/assistant/conversations": () => [],
    });
    await render(<ToastProvider><Memory /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Mémoire éteinte");
    await click("Activer la mémoire");
    await settle();
    expect(body("/api/consents/ai_memory")).toEqual({ granted: true });
    expect(host.textContent).toContain("Mémoire active");
  });

  it("labels retention durations plainly", () => {
    expect(retentionLabel(0)).toBe("Sans limite");
    expect(retentionLabel(365)).toBe("1 an");
    expect(retentionLabel(1095)).toBe("3 ans");
    expect(retentionLabel(30)).toBe("30 jours");
  });
});

describe("confidentialité (réglages)", () => {
  it("lists every purpose and flips one", async () => {
    let rows: ConsentView[] = [
      { purpose: "ai_memory", title: "Mémoire de l'assistant", text: "…", granted: false, explicit: false, decided: false, updated_at: null, default: false },
      { purpose: "watch", title: "Veille sur les sources officielles", text: "…", granted: true, explicit: false, decided: false, updated_at: null, default: true },
    ];
    mockApi({
      "/api/consents/ai_memory": () => {
        rows = rows.map((r) => (r.purpose === "ai_memory" ? { ...r, granted: true, explicit: true, decided: true, updated_at: "2026-10-21T10:00:00Z" } : r));
        return rows[0];
      },
      "/api/consents": () => rows,
    });
    await render(<ToastProvider><Privacy /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Pas encore demandé");
    expect(host.textContent).toContain("Actif par défaut");
    const sw = host.querySelector<HTMLInputElement>("input[role=switch]")!;
    await act(async () => sw.click());
    await settle();
    expect(body("/api/consents/ai_memory")).toEqual({ granted: true });
    expect(host.textContent).toContain("Accordé le 21 oct");
  });
});
