// Le chat interactif : l'ouverture qui prend l'initiative (boutons « relier »), les blocs d'une réponse — boutons
// qui naviguent ou agissent, questions suivantes qui repartent au modèle, graphique dessiné avec les chiffres de
// Bank, schéma en SVG, sources avec liens sûrs — et l'accord « recherche web » demandé une fois.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantAnswer, AssistantOpening, AssistantStatus, ChatBlock, SearchStatus } from "./api";
import { Assistant } from "./components/Assistant";
import { ChatBlocks, layers } from "./components/chat/Blocks";
import { ToastProvider } from "./components/ui";
import { SearchSettings } from "./pages/SearchSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MODES = { full: "Complet", redacted: "Anonymisé", aggregates: "Chiffres seuls" };
const ON: AssistantStatus = {
  configured: true, privacy_modes: MODES, name: "Bank", memory: { consent: false, asked: true, available: true },
  search: { configured: true, consent: false, asked: false },
  provider: "openrouter", key_prefix: "sk-or-v1-012…", model: "anthropic/claude-haiku-5.5", privacy: "redacted",
  monthly_cap_usd_cents: 500, daily_calls_cap: 60, month_cost_usd_cents: 42, month_cost_microusd: 420_000,
  calls_today: 3, calls_month: 12, last_used_at: null, last_error: null, created_at: "2026-10-01T10:00:00+00:00",
};
const OPENING: AssistantOpening = {
  text: "Salut, c'est Bank. Je connais déjà l'essentiel (40 % de relié). Pour aller plus loin, relie ton bail et tes assurances.",
  blocks: [{ type: "actions", items: [
    { key: "connect:lease", label: "Déposer mon bail", kind: "navigate", page: "documents" },
    { key: "connect:insurance", label: "Déposer une attestation d'assurance", kind: "navigate", page: "documents" },
    { key: "ask:spend", label: "Où est parti mon argent ?", kind: "ask", prompt: "Où est parti mon argent ce mois-ci ?" },
  ] }],
  score: 40,
  next: [],
};
const BLOCKS: ChatBlock[] = [
  { type: "chart", kind: "donut", title: "Dépenses par catégorie · 3 derniers mois", unit: "eur", dataset: "spend_by_category", source: "Bank · opérations", items: [
    { label: "Logement", value: 195_000, color: "#2f5bea" }, { label: "Courses", value: 90_000 },
  ] },
  { type: "diagram", kind: "flow", title: "D'où vient le reste à dépenser", nodes: [
    { id: "rev", label: "Revenus", note: "salaire + aides" }, { id: "fix", label: "Charges fixes" }, { id: "rad", label: "Reste à dépenser" },
  ], edges: [{ from: "rev", to: "rad" }, { from: "fix", to: "rad", label: "moins" }] },
  { type: "sources", query: "apl 2026", items: [
    { n: 1, title: "APL — service-public.fr", url: "https://www.service-public.fr/F12006", host: "www.service-public.fr", snippet: "L'APL est calculée…", date: "2026-04-01" },
  ] },
  { type: "actions", items: [
    { key: "connect:lease", label: "Déposer mon bail", kind: "navigate", page: "documents" },
    { key: "card:obligation:suggest:caf_dtr:follow", label: "Suivre la déclaration trimestrielle", kind: "act", card: "obligation:suggest:caf_dtr", action: "follow" },
    { key: "followup:0", label: "Et mon loyer ?", kind: "ask", prompt: "Et mon loyer ?" },
  ] },
];

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
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const render = (node: React.ReactNode) => act(async () => root.render(node));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const body = (url: string) => JSON.parse(String(calls.find((c) => c.url === url && c.init?.method && c.init.method !== "GET")?.init?.body ?? "{}"));

function answerOf(answer: string, blocks: ChatBlock[] = []): AssistantAnswer {
  return {
    answer, blocks, conversation_id: 7, message_id: 11, model: "anthropic/claude-haiku-5.5", name: "Bank", privacy: "redacted",
    memory_on: false, memory: [], tools: [], cost_usd: 0.001, prompt_tokens: 1500, completion_tokens: 60, truncated: false,
    review_due: false, calls_today: 4, month_cost_usd_cents: 43,
  };
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  window.location.hash = "";
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("chat interactif", () => {
  it("opens with Bank's initiative: connect buttons that navigate, ready questions that ask", async () => {
    mockApi({
      "/api/assistant/conversations": () => [],
      "/api/assistant/opening": () => OPENING,
      "/api/assistant/ask": () => answerOf("Surtout le logement."),
      "/api/assistant": () => ({ ...ON, search: { configured: false, consent: false, asked: false } }),
    });
    const onClose = vi.fn();
    await render(<ToastProvider><Assistant open onClose={onClose} /></ToastProvider>);
    await settle();
    expect(host.querySelector(".assistant-opening")?.textContent).toContain("40 % de relié");
    expect(host.querySelectorAll(".chat-buttons .btn")).toHaveLength(2);
    await click("Où est parti mon argent ?");
    expect(body("/api/assistant/ask/stream")).toEqual({ question: "Où est parti mon argent ce mois-ci ?", conversation_id: null });
    expect(host.querySelector(".assistant-opening")).toBeNull(); // la conversation a commencé
    expect(host.textContent).toContain("Surtout le logement.");
  });

  it("renders the blocks of an answer and routes each button to the right place", async () => {
    mockApi({
      "/api/assistant/conversations": () => [],
      "/api/assistant/opening": () => OPENING,
      "/api/assistant/ask": () => answerOf("Voilà où part ton argent [1].", BLOCKS),
      "/api/initiatives/obligation%3Asuggest%3Acaf_dtr/act": () => ({ ok: true }),
      "/api/assistant": () => ({ ...ON, search: { configured: true, consent: true, asked: true } }),
    });
    const onClose = vi.fn();
    await render(<ToastProvider><Assistant open onClose={onClose} /></ToastProvider>);
    await settle();
    await click("Déposer une attestation");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("#/documents");
    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(ta, "Où est parti mon argent ?");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    // Graphique : un anneau avec les chiffres de Bank, sa source ; schéma : SVG avec les boîtes ; sources : liens sûrs.
    const chart = host.querySelector(".chat-chart")!;
    expect(chart.textContent).toContain("Dépenses par catégorie");
    expect(chart.textContent).toContain("Bank · opérations");
    expect(chart.querySelector("svg[role=img]")!.getAttribute("aria-label")).toContain("Logement 68 %");
    const flow = host.querySelector<SVGSVGElement>(".chat-flow")!;
    expect(flow.querySelectorAll(".chat-node")).toHaveLength(3);
    expect(flow.querySelectorAll(".chat-edge")).toHaveLength(2);
    expect(flow.getAttribute("aria-label")).toContain("Charges fixes → Reste à dépenser (moins)");
    expect(host.querySelector(".chat-diagram-notes")!.textContent).toContain("Revenus : salaire + aides");
    const link = host.querySelector<HTMLAnchorElement>(".chat-sources a")!;
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.href).toBe("https://www.service-public.fr/F12006");
    expect(host.querySelector(".chat-sources")!.textContent).toContain("1 avr. 2026");
    // Boutons : agir sur une carte passe par l'API des initiatives, puis le bouton reste coché.
    await click("Suivre la déclaration trimestrielle");
    expect(body("/api/initiatives/obligation%3Asuggest%3Acaf_dtr/act")).toEqual({ action: "follow" });
    expect(button("Suivre la déclaration trimestrielle").disabled).toBe(true);
    // Question suivante : repart au modèle dans la même conversation.
    await click("Et mon loyer ?");
    const asks = calls.filter((c) => c.url === "/api/assistant/ask/stream").map((c) => JSON.parse(String(c.init!.body)));
    expect(asks[1]).toEqual({ question: "Et mon loyer ?", conversation_id: 7 });
  });

  it("asks once for the web-search consent when a provider is configured", async () => {
    let status = ON;
    mockApi({
      "/api/assistant/conversations": () => [],
      "/api/assistant/opening": () => OPENING,
      "/api/consents/web_search": () => { status = { ...ON, search: { configured: true, consent: true, asked: true } }; return { granted: true }; },
      "/api/assistant": () => status,
    });
    await render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Je peux chercher sur le web");
    await click("Oui, cherche quand il faut");
    expect(body("/api/consents/web_search")).toEqual({ granted: true });
    expect(host.textContent).not.toContain("Je peux chercher sur le web");
  });

  it("draws a flow in layers (longest path), simple bars as rows or columns", async () => {
    expect(layers([{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }], [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "a", to: "c" }])).toEqual([["a"], ["b"], ["c"]]);
    expect(layers([{ id: "a", label: "A" }, { id: "b", label: "B" }], [{ from: "a", to: "b" }, { from: "b", to: "a" }])).toHaveLength(2); // un cycle ne boucle pas
    const handlers = { onAsk: vi.fn(), onNavigate: vi.fn() };
    await render(<ToastProvider><ChatBlocks handlers={handlers} blocks={[
      { type: "chart", kind: "bars", title: "Aides", unit: "eur", dataset: "aids", source: "", items: [{ label: "CAF", value: 240_000 }, { label: "France Travail", value: 120_000 }] },
      { type: "chart", kind: "bars", title: "Année", unit: "eur", dataset: "year_ahead", source: "", items: Array.from({ length: 12 }, (_, i) => ({ label: `m${i}`, value: i === 3 ? -5_000 : (i + 1) * 10_000 })) },
      { type: "chart", kind: "pairs", title: "Reçu/dépensé", unit: "eur", dataset: "income_vs_spend", source: "", items: [{ label: "sept.", a: 200_000, b: 150_000 }] },
      { type: "chart", kind: "line", title: "Solde", unit: "eur", dataset: "balance", source: "", items: [{ label: "2026-10-01", value: 50_000 }, { label: "2026-10-21", value: 64_000 }] },
      { type: "note", tone: "warning", text: "Pas de moteur de recherche branché." },
    ]} /></ToastProvider>);
    await settle();
    const rows = host.querySelectorAll(".chat-rows li");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent?.replace(/[  ]/g, " ")).toContain("CAF");
    expect(rows[0]!.textContent?.replace(/[  ]/g, " ")).toContain("2 400 €");
    expect(host.querySelectorAll(".chat-column")).toHaveLength(12);
    expect(host.querySelectorAll(".chat-column.is-negative")).toHaveLength(1);
    expect(host.querySelector(".bars-wrap")).not.toBeNull();
    expect(host.querySelector(".chat-note.is-warning")!.textContent).toContain("Pas de moteur");
  });

  it("configures a search engine with the password, shows the state, tests and removes it", async () => {
    let state: SearchStatus = { configured: false, consent: false, asked: false, kinds: { searxng: "SearXNG (chez toi)", brave: "Brave Search (clé d'API)" } };
    mockApi({
      "/api/search/test": () => ({ ok: true, results: 2, sample: ["service-public.fr"] }),
      "/api/search": (init) => {
        if (init?.method === "PUT") state = { ...state, configured: true, kind: "searxng", base_url: "http://searxng:8080", daily_cap: 25, used_today: 0, last_error: null };
        if (init?.method === "DELETE") state = { ...state, configured: false };
        return state;
      },
      "/api/consents/web_search": () => { state = { ...state, consent: true, asked: true }; return { granted: true }; },
    });
    await render(<ToastProvider><SearchSettings /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Aucun moteur branché");
    await click("Brancher un moteur");
    const form = host.querySelector<HTMLFormElement>("form[aria-label='Brancher un moteur de recherche']")!;
    const set = (sel: string, value: string) => {
      const el = form.querySelector<HTMLInputElement>(sel)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    await act(async () => {
      set("input[placeholder='http://searxng:8080']", "http://searxng:8080");
      set("input[type=number]", "25");
      set("input[autocomplete=current-password]", "phrase de passe");
    });
    await act(async () => form.requestSubmit());
    await settle();
    expect(body("/api/search")).toEqual({ password: "phrase de passe", kind: "searxng", base_url: "http://searxng:8080", api_key: null, daily_cap: 25 });
    expect(host.textContent).toContain("SearXNG (chez toi) · http://searxng:8080");
    expect(host.textContent).toContain("0 / 25 recherches");
    expect(host.textContent).toContain("pas donné");
    await click("Donner mon accord");
    expect(body("/api/consents/web_search")).toEqual({ granted: true });
    expect(host.textContent).toContain("donné");
    await click("Tester");
    expect(host.textContent).toContain("2 résultat(s)");
    vi.stubGlobal("confirm", () => true);
    await click("Retirer le moteur");
    expect(calls.some((c) => c.url === "/api/search" && c.init?.method === "DELETE")).toBe(true);
    expect(host.textContent).toContain("Aucun moteur branché");
  });
});
