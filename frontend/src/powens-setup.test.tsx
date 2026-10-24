// Powens (sur contrat) : la carte reste discrète tant que rien n'est configuré, l'assistant mène du contrat à la
// webview, le secret part une fois (mot de passe Bank) et n'est plus jamais affiché, le bac à sable est signalé, et
// une banque qui demande une nouvelle authentification forte se reconnecte d'un clic.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PowensConnection, PowensStatus } from "./api";
import { navigation, PowensSetup, powensStep, SANDBOX_NOTE } from "./components/PowensSetup";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SECRET = "s3cr3t-Powens-0123456789abcdef";
const EMPTY: PowensStatus = {
  configured: false, domain: null, client_id_hint: null, secret_fingerprint: null, sandbox: false, updated_at: null,
  redirect_url: "https://bank.example/api/banks/powens/callback", console_url: "https://console.powens.com",
  check: null, connections: [],
};
const conn = (status: PowensConnection["status"], last_error: string | null = null): PowensConnection => ({
  id: 3, aspsp_name: "Connecteur de test", status, last_sync_at: null, last_error, accounts: ["Compte chèque"],
});
const READY: PowensStatus = {
  ...EMPTY, configured: true, domain: "bankperso-sandbox", client_id_hint: "…3977", secret_fingerprint: "ab12 cd34 ef56 7890",
  sandbox: true, updated_at: "2026-10-24T10:00:00Z",
  check: { ok: true, issues: [{ code: "sandbox", message: "Bac à sable" }], environment: "SANDBOX", checked_at: "2026-10-24T10:00:00Z" },
};

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
let assigned: string[];
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[   ]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const type = async (label: string, value: string) => {
  const field = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label));
  const input = field?.querySelector("input") as HTMLInputElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
};
const activeStep = () => host.querySelector(".wizard-steps li.is-active")?.textContent ?? "";

function mockApi(state: { st: PowensStatus; afterPut?: PowensStatus }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/banks/powens/setup" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      if (body.password !== "bank-pass") return json({ detail: "Mot de passe incorrect" }, 403);
      if (body.client_secret !== SECRET) return json({ detail: "Powens refuse ces identifiants." }, 400);
      state.st = state.afterPut ?? READY;
      return json(state.st);
    }
    if (url === "/api/banks/powens/connect" || url.endsWith("/reconnect")) {
      const flow = url.endsWith("/reconnect") ? "reconnect" : "connect";
      return json({ redirect_url: `https://webview.powens.com/fr/${flow}?domain=bankperso-sandbox.biapi.pro&code=c&state=s` });
    }
    if (url === "/api/banks/powens") return json(state.st);
    return json({}, 404);
  });
}

const mount = () => act(async () => root.render(<ToastProvider><PowensSetup /></ToastProvider>));

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  assigned = [];
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: () => Promise.resolve() } });
  vi.spyOn(navigation, "go").mockImplementation((u: string) => { assigned.push(u); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Powens (sur contrat)", () => {
  it("knows where you are", () => {
    expect(powensStep(null)).toBe(0);
    expect(powensStep(EMPTY)).toBe(0);
    expect(powensStep({ ...READY, check: null })).toBe(3);
    expect(powensStep({ ...READY, check: { ...READY.check!, ok: false, issues: [{ code: "bad_key", message: "x" }] } })).toBe(3);
    expect(powensStep(READY)).toBe(4);
  });

  it("stays a quiet card until you open it, then walks to the webview; the secret is never shown", async () => {
    const state = { st: EMPTY };
    mockApi(state);
    await mount();
    await settle();
    expect(text()).toContain("sur contrat");
    expect(host.querySelector(".wizard")).toBeNull();

    await click("Configurer Powens");
    expect(activeStep()).toContain("Ton contrat");
    expect(text()).toContain("banque de démonstration");
    await click("J'ai mes identifiants");
    expect(activeStep()).toContain("Adresse de retour");
    expect(text()).toContain("https://bank.example/api/banks/powens/callback");
    await click("C'est fait");
    expect(activeStep()).toContain("Identifiants");
    await type("Domaine", "bankperso-sandbox");
    await type("Client ID", "48213977");
    await type("Client secret", "faux-secret-0123456789");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Enregistrer et vérifier");
    expect(text()).toContain("Powens refuse ces identifiants");
    expect(activeStep()).toContain("Identifiants");
    await type("Client secret", SECRET);
    await click("Enregistrer et vérifier");
    const put = calls.filter((c) => c.init?.method === "PUT").pop()!;
    expect(JSON.parse(String(put.init!.body))).toEqual({
      password: "bank-pass", domain: "bankperso-sandbox", client_id: "48213977", client_secret: SECRET,
    });

    expect(activeStep()).toContain("Tes banques");
    expect(text()).toContain(SANDBOX_NOTE);
    expect(text()).toContain("…3977");
    expect(text()).toContain("ab12 cd34 ef56 7890");
    expect(host.innerHTML).not.toContain(SECRET);

    await click("Connecter une banque via Powens");
    expect(assigned).toEqual(["https://webview.powens.com/fr/connect?domain=bankperso-sandbox.biapi.pro&code=c&state=s"]);
  });

  it("offers to reconnect a bank that asks for strong authentication again", async () => {
    mockApi({ st: { ...READY, connections: [conn("expired", "Ta banque demande une nouvelle authentification forte : reconnecte-la.")] } });
    await mount();
    await settle();
    expect(text()).toContain("à reconnecter");
    expect(text()).toContain("authentification forte");
    await click("Reconnecter");
    expect(calls.some((c) => c.url === "/api/banks/powens/3/reconnect" && c.init?.method === "POST")).toBe(true);
    expect(assigned[0]).toContain("https://webview.powens.com/fr/reconnect");
  });

  it("greets the return from the Powens webview and lets you cut an unfinished connection", async () => {
    mockApi({ st: { ...READY, connections: [conn("error", "Connexion inachevée chez Powens : coupe-la puis recommence.")] } });
    await act(async () => root.render(<ToastProvider><PowensSetup result="connected" /></ToastProvider>));
    await settle();
    expect(text()).toContain("Banque connectée via Powens");
    expect(text()).toContain("inachevée");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await click("Couper");
    expect(calls.some((c) => c.url === "/api/banks/powens/3" && c.init?.method === "DELETE")).toBe(true);
  });

  it("removes Powens with the Bank password", async () => {
    mockApi({ st: READY });
    await mount();
    await settle();
    await click("Retirer");
    await type("Ton mot de passe Bank", "bank-pass");
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(new Response(JSON.stringify(EMPTY), { status: 200, headers: { "content-type": "application/json" } }));
    });
    await click("Retirer Powens");
    const del = calls.find((c) => c.init?.method === "DELETE")!;
    expect(del.url).toBe("/api/banks/powens/setup");
    expect(JSON.parse(String(del.init!.body))).toEqual({ password: "bank-pass" });
    expect(host.querySelector(".wizard")).toBeNull();
  });
});
