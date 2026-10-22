// La réponse en direct : les statuts s'affichent, le texte arrive morceau par morceau, un « reset » l'efface, la
// réponse complète (avec ses blocs) prend la place ; une erreur en flux remet la question dans le champ. Et les
// petits gestes du téléphone : le bouton flottant s'efface quand on lit vers le bas, le bandeau hors ligne.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { askStream, parseSseChunk, type AssistantStatus } from "./api";
import { Assistant } from "./components/Assistant";
import { OfflineStrip, useScrollingDown } from "./components/mobile";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MODES = { full: "Complet", redacted: "Anonymisé", aggregates: "Chiffres seuls" };
const ON: AssistantStatus = {
  configured: true, privacy_modes: MODES, name: "Bank", memory: { consent: false, asked: true, available: true },
  search: { configured: false, consent: false, asked: false }, model: "anthropic/claude-haiku-5.5", privacy: "redacted",
};
const DONE = {
  answer: "Tu peux dépenser **75 €** par jour.", blocks: [{ type: "actions", items: [{ key: "go:aides", label: "Mes aides", kind: "navigate", page: "aides" }] }],
  conversation_id: 7, message_id: 11, model: "anthropic/claude-haiku-5.5", name: "Bank", privacy: "redacted", memory_on: false,
  memory: [], tools: ["show_chart"], cost_usd: 0.001, prompt_tokens: 1, completion_tokens: 1, truncated: false, review_due: false,
  calls_today: 1, month_cost_usd_cents: 1,
};
const sse = (...frames: [string, unknown][]) => frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");

let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };

function mock(stream: string, status = 200, type = "text/event-stream") {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/api/assistant/ask/stream") {
      expect(init?.headers).toMatchObject({ Accept: "text/event-stream", "X-CSRF-Token": "" });
      return Promise.resolve(new Response(stream, { status, headers: { "content-type": type } }));
    }
    if (url === "/api/assistant") return Promise.resolve(new Response(JSON.stringify(ON), { headers: { "content-type": "application/json" } }));
    if (url === "/api/assistant/opening") return Promise.resolve(new Response(JSON.stringify({ text: "Salut, c'est Bank.", blocks: [], score: 50, next: [] }), { headers: { "content-type": "application/json" } }));
    return Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } }));
  });
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

describe("réponse en direct", () => {
  it("parses SSE frames across chunk boundaries", () => {
    const a = parseSseChunk("event: delta\ndata: {\"text\":\"Tu \"}\n\nevent: st");
    expect(a.frames).toEqual([{ event: "delta", data: "{\"text\":\"Tu \"}" }]);
    expect(a.rest).toBe("event: st");
    const b = parseSseChunk(a.rest + "atus\ndata: {\"text\":\"x\"}\n\n: commentaire\n\n");
    expect(b.frames).toEqual([{ event: "status", data: "{\"text\":\"x\"}" }]);
    expect(b.rest).toBe("");
  });

  it("streams statuses and text, resets after a tool, then shows the full answer with its blocks", async () => {
    mock(sse(["status", { text: "lit ton dossier" }], ["delta", { text: "Je regarde… " }], ["reset", {}],
             ["status", { text: "dessine le graphique", tool: "show_chart" }], ["delta", { text: "Tu peux dépenser " }],
             ["delta", { text: "**75 €** par jour." }], ["done", DONE]));
    const seen: string[] = [];
    const r = await askStream({ question: "?", conversation_id: null }, {
      onStatus: (t) => seen.push(`status:${t}`), onDelta: (t) => seen.push(`delta:${t}`), onReset: () => seen.push("reset"),
    });
    expect(seen).toEqual(["status:lit ton dossier", "delta:Je regarde… ", "reset", "status:dessine le graphique", "delta:Tu peux dépenser ", "delta:**75 €** par jour."]);
    expect(r.answer).toBe(DONE.answer);
    await act(async () => root.render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>));
    await settle();
    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    expect(ta.getAttribute("enterkeyhint")).toBe("send");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(ta, "Combien ?");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    const turns = host.querySelectorAll(".assistant-turn:not(.assistant-opening)");
    expect(turns).toHaveLength(2);
    expect(turns[1]!.querySelector("strong")?.textContent).toBe("75 €");
    expect(turns[1]!.querySelector(".assistant-status")).toBeNull();
    expect(turns[1]!.querySelector(".chat-buttons .btn")?.textContent).toContain("Mes aides");
  });

  it("puts the question back when the stream reports an error, and falls back when streaming is unknown", async () => {
    mock(sse(["status", { text: "lit ton dossier" }], ["error", { detail: "Plafond d'appels atteint", status: 429 }]));
    await expect(askStream({ question: "?", conversation_id: null }, {})).rejects.toThrow("Plafond d'appels atteint");
    await act(async () => root.render(<ToastProvider><Assistant open onClose={() => undefined} /></ToastProvider>));
    await settle();
    const ta = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(ta, "Combien ?");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    expect(host.querySelector("[role=alert]")?.textContent).toContain("Plafond");
    expect(ta.value).toBe("Combien ?");
    expect(host.querySelectorAll(".assistant-turn:not(.assistant-opening)")).toHaveLength(0);
    // Un serveur sans flux (404) : repli sur la réponse en un bloc.
    vi.stubGlobal("fetch", (url: string) => {
      if (url === "/api/assistant/ask/stream") return Promise.resolve(new Response("{}", { status: 404 }));
      if (url === "/api/assistant/ask") return Promise.resolve(new Response(JSON.stringify(DONE), { headers: { "content-type": "application/json" } }));
      return Promise.resolve(new Response("{}", { status: 404 }));
    });
    const r = await askStream({ question: "?", conversation_id: null }, {});
    expect(r.answer).toBe(DONE.answer);
  });
});

describe("téléphone", () => {
  it("hides the floating button while scrolling down and shows it again when scrolling up", async () => {
    const states: boolean[] = [];
    function Probe() {
      const down = useScrollingDown(10);
      states.push(down);
      return <span data-down={String(down)} />;
    }
    await act(async () => root.render(<Probe />));
    const scrollTo = async (y: number) => {
      Object.defineProperty(window, "scrollY", { value: y, configurable: true });
      await act(async () => { window.dispatchEvent(new Event("scroll")); await new Promise((r) => requestAnimationFrame(() => r(null))); });
    };
    await scrollTo(200);
    expect(host.querySelector("span")?.getAttribute("data-down")).toBe("true");
    await scrollTo(150);
    expect(host.querySelector("span")?.getAttribute("data-down")).toBe("false");
    await scrollTo(400);
    await scrollTo(0);
    expect(host.querySelector("span")?.getAttribute("data-down")).toBe("false");
  });

  it("shows the offline strip only when the browser is offline", async () => {
    await act(async () => root.render(<OfflineStrip />));
    expect(host.querySelector(".offline-strip")).toBeNull();
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });
    await act(async () => { window.dispatchEvent(new Event("offline")); });
    expect(host.querySelector(".offline-strip")?.textContent).toContain("Hors ligne");
    Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(host.querySelector(".offline-strip")).toBeNull();
  });
});
