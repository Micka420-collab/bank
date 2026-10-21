// « Bank te demande » et la première ouverture : rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Card, Initiatives as InitiativesData } from "./api";
import { Initiatives, normalize } from "./components/Initiatives";
import { ToastProvider } from "./components/ui";
import { Welcome } from "./components/Welcome";
import { knowledgeLevel } from "./pages/Situation";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const card = (over: Partial<Card>): Card => ({
  key: "ask:x",
  kind: "question",
  priority: 10,
  title: "Question ?",
  body: "Corps.",
  why: null,
  evidence: null,
  topic: "profil",
  input: null,
  actions: [],
  ...over,
});

const SALARY = card({
  key: "fact:net_monthly_income",
  title: "Ton salaire net, c'est bien 2 150 € par mois ?",
  why: "C'est la base de tout calcul.",
  evidence: "Médiane de tes salaires sur 4 mois",
  input: { kind: "money" },
  actions: [
    { id: "yes", label: "C'est juste", style: "primary", effect: { type: "answer", question_id: "net_monthly_income", value: 215000 } },
    { id: "fix", label: "Corriger", style: "secondary", effect: { type: "answer", question_id: "net_monthly_income", from_input: true } },
    { id: "snooze", label: "Plus tard", style: "link", effect: { type: "snooze", days: 7 } },
  ],
});
const MERCHANT = card({
  key: "merchant:LECLERC ORLEANS",
  title: "LECLERC ORLEANS : 12 opérations non classées",
  input: { kind: "category", options: [{ value: "courses", label: "Courses" }, { value: "loisirs", label: "Loisirs" }] },
  actions: [
    { id: "classify", label: "Classer", style: "primary", effect: { type: "rule", merchant: "LECLERC ORLEANS", from_input: true } },
    { id: "dismiss", label: "Ignorer ce marchand", style: "link", effect: { type: "dismiss" } },
  ],
});
const CELEBRATE = card({ key: "celebrate:3mois", kind: "celebrate", title: "Trois mois d'historique", actions: [{ id: "done", label: "Super", style: "secondary", effect: { type: "done" } }] });

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

function mockApi(handlers: Record<string, (init?: RequestInit) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    return Promise.resolve(jsonResponse(handlers[key]!(init)));
  });
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

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

const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim() === label);
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });
const clickPath = (title: string) => act(async () => {
  const b = [...host.querySelectorAll<HTMLButtonElement>(".path")].find((x) => x.querySelector("b")?.textContent === title);
  if (!b) throw new Error(`chemin introuvable : ${title}`);
  b.click();
});

describe("Bank te demande", () => {
  it("shows one card at a time, answers in one tap and slides to the next", async () => {
    const first: InitiativesData = { cards: [SALARY, MERCHANT, CELEBRATE], total: 3, by_topic: {} };
    const after: InitiativesData = { cards: [MERCHANT, CELEBRATE], total: 2, by_topic: {} };
    mockApi({
      "/api/initiatives?": () => first,
      "/api/initiatives/fact%3Anet_monthly_income/act": () => ({ ...after, result: "Noté : ton agent le saura." }),
    });
    await act(async () => root.render(<ToastProvider><Initiatives /></ToastProvider>));
    await flush();
    expect(host.querySelectorAll(".ask")).toHaveLength(1);
    expect(host.textContent).toContain("Ton salaire net, c'est bien 2 150 € par mois ?");
    expect(host.textContent).toContain("2 autres — voir");
    expect(host.textContent).not.toContain("C'est la base de tout calcul");
    await click("Pourquoi ?");
    expect(host.textContent).toContain("C'est la base de tout calcul");

    await click("C'est juste");
    await flush();
    const post = calls.find((c) => c.url.includes("/act"));
    expect(post?.init?.method).toBe("POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ action: "yes", value: null });
    expect(host.querySelector(".ask.is-leaving")).not.toBeNull();
    await wait(250);
    expect(host.textContent).toContain("LECLERC ORLEANS");
    expect(host.textContent).not.toContain("Ton salaire net");
    expect(host.textContent).toContain("Noté : ton agent le saura.");
  });

  it("opens the inline input to correct a fact and refuses an unreadable amount", async () => {
    mockApi({
      "/api/initiatives?": () => ({ cards: [SALARY], total: 1, by_topic: {} }),
      "/api/initiatives/fact%3Anet_monthly_income/act": () => ({ cards: [], total: 0, by_topic: {}, result: "Noté." }),
    });
    await act(async () => root.render(<ToastProvider><Initiatives /></ToastProvider>));
    await flush();
    await click("Corriger");
    const input = host.querySelector<HTMLInputElement>("input[inputmode='decimal']")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "abc");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    expect(host.textContent).toContain("Indique un montant");
    expect(calls.some((c) => c.url.includes("/act"))).toBe(false);
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "1 980,50");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    await flush();
    const post = calls.find((c) => c.url.includes("/act"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({ action: "fix", value: 198050 });
  });

  it("classifies a merchant from the category picker and navigates for page actions", async () => {
    const nav = card({ key: "planning:connect", kind: "tip", title: "Relie ton agenda", actions: [{ id: "go:connecteurs", label: "Relier mon agenda", style: "primary", effect: { type: "navigate", page: "connecteurs" } }] });
    mockApi({
      "/api/initiatives?": () => ({ cards: [MERCHANT, nav], total: 2, by_topic: {} }),
      "/api/initiatives/merchant%3ALECLERC%20ORLEANS/act": () => ({ cards: [nav], total: 1, by_topic: {}, result: "Classé en Courses : 12 opération(s)." }),
    });
    await act(async () => root.render(<ToastProvider><Initiatives /></ToastProvider>));
    await flush();
    const select = host.querySelector<HTMLSelectElement>("select")!;
    expect(select).not.toBeNull();
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    expect(host.textContent).toContain("Choisis une réponse");
    await act(async () => {
      select.value = "courses";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    await flush();
    await wait(250);
    const post = calls.find((c) => c.url.includes("/act"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({ action: "classify", value: "courses" });
    expect(host.textContent).toContain("Relie ton agenda");
    window.location.hash = "";
    await click("Relier mon agenda");
    expect(window.location.hash).toBe("#/connecteurs");
    expect(calls.filter((c) => c.url.includes("/act"))).toHaveLength(1);
  });

  it("renders nothing when there is nothing to ask", async () => {
    mockApi({ "/api/initiatives?": () => ({ cards: [], total: 0, by_topic: {} }) });
    await act(async () => root.render(<ToastProvider><Initiatives /></ToastProvider>));
    await flush();
    expect(host.querySelector(".asks")).toBeNull();
  });

  it("normalizes inline inputs by kind", () => {
    expect(normalize(card({ input: { kind: "money" } }), "1 200,50")).toBe(120050);
    expect(normalize(card({ input: { kind: "money" } }), "-5")).toBeUndefined();
    expect(normalize(card({ input: { kind: "int", minimum: 0, maximum: 6 } }), "7")).toBeUndefined();
    expect(normalize(card({ input: { kind: "int", minimum: 0, maximum: 6 } }), 3)).toBe(3);
    expect(normalize(card({ input: { kind: "multi", options: [], max_items: 3 } }), [])).toBeUndefined();
    expect(normalize(card({ input: { kind: "multi", options: [], max_items: 3 } }), ["a"])).toEqual(["a"]);
    expect(normalize(card({ input: { kind: "choice", options: [] } }), "")).toBeUndefined();
    expect(normalize(card({ input: null }), "x")).toBe("x");
  });
});

describe("première ouverture", () => {
  it("offers three paths and the three-figures path creates the account and the first answers", async () => {
    mockApi({
      "/api/accounts": () => ({ id: 1, name: "Compte courant" }),
      "/api/onboarding/net_monthly_income": () => ({ value: 190000 }),
      "/api/onboarding/housing_cost": () => ({ value: 65000 }),
    });
    const started = vi.fn();
    await act(async () => root.render(<ToastProvider><Welcome onStarted={started} /></ToastProvider>));
    expect(host.textContent).toContain("Connecter ma banque");
    expect(host.textContent).toContain("Importer un relevé");
    await clickPath("Juste trois chiffres");
    const inputs = [...host.querySelectorAll<HTMLInputElement>(".figures-form input")];
    expect(inputs).toHaveLength(3);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      for (const [i, v] of ["640", "1 900", "650"].entries()) {
        setter.call(inputs[i], v);
        inputs[i]!.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    await act(async () => { host.querySelector("form")!.requestSubmit(); });
    await flush();
    await flush();
    const bodies = calls.map((c) => [c.url, c.init?.method, c.init?.body ? JSON.parse(String(c.init.body)) : null]);
    expect(bodies).toEqual([
      ["/api/accounts", "POST", { name: "Compte courant", kind: "checking", balance_cents: 64000 }],
      ["/api/onboarding/net_monthly_income", "PUT", { value: 190000 }],
      ["/api/onboarding/housing_cost", "PUT", { value: 65000 }],
    ]);
    expect(started).toHaveBeenCalledTimes(1);
  });

  it("words the knowledge level", () => {
    expect(knowledgeLevel(0)).toBe("Bank ne te connaît pas encore");
    expect(knowledgeLevel(35)).toBe("Bank commence à te connaître");
    expect(knowledgeLevel(60)).toBe("Bank te connaît");
    expect(knowledgeLevel(90)).toBe("Bank te connaît bien");
  });
});
