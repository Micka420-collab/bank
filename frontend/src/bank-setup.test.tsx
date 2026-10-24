// Assistant Enable Banking : six étapes visibles, l'adresse de retour se copie, le .pem déposé remplit l'identifiant,
// la clé part une fois (mot de passe Bank) et n'est plus jamais affichée, chaque problème dit quoi faire, et
// l'application activée mène au choix de la banque.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BankSetupIssue, BankSetupStatus } from "./api";
import { appIdFromFileName, BankSetup, setupReady, setupStep } from "./components/BankSetup";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const APP_ID = "4f1c2b3a-9d8e-4c7b-a6f5-0e1d2c3b4a59";
const PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7SECRETSECRET\n-----END PRIVATE KEY-----\n";
const EMPTY: BankSetupStatus = {
  configured: false, source: null, app_id_hint: null, key_fingerprint: null, updated_at: null,
  redirect_url: "https://bank.example/api/banks/callback", console_url: "https://enablebanking.com/cp/applications",
  check: null, connections: 0,
};
const checked = (issues: BankSetupIssue[]): BankSetupStatus => ({
  ...EMPTY, configured: true, source: "app", app_id_hint: "…3b4a59", key_fingerprint: "ab12 cd34 ef56 7890",
  updated_at: "2026-10-24T10:00:00Z",
  check: { ok: issues.every((i) => i.code === "sandbox"), issues, app_name: "Bank perso", environment: "PRODUCTION", checked_at: "2026-10-24T10:00:00Z" },
});
const INACTIVE = { code: "inactive" as const, message: "Ton application n'est pas encore active. Clique « Activate by linking accounts »." };
const REDIRECT = { code: "redirect" as const, message: "Ajoute exactement https://bank.example/api/banks/callback aux « Allowed redirect URLs »." };

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
let copied: string[];
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
  const input = field?.querySelector("input, textarea") as HTMLInputElement | HTMLTextAreaElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
};
const drop = async (file: File) => {
  const input = host.querySelector<HTMLInputElement>("input[type=file]")!;
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await settle();
};
const activeStep = () => host.querySelector(".wizard-steps li.is-active")?.textContent ?? "";

function mockApi(state: { st: BankSetupStatus; afterPut?: BankSetupStatus; afterCheck?: BankSetupStatus }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/banks/setup" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      if (body.password !== "bank-pass") return json({ detail: "Mot de passe incorrect" }, 403);
      if (body.app_id !== APP_ID) return json({ detail: "Enable Banking ne reconnaît pas cette clé pour cet identifiant d'application." }, 400);
      state.st = state.afterPut ?? checked([]);
      return json(state.st);
    }
    if (url === "/api/banks/setup/check") {
      state.st = state.afterCheck ?? state.st;
      return json(state.st);
    }
    if (url === "/api/banks/setup" && init?.method === "DELETE") {
      const body = JSON.parse(String(init.body));
      if (body.password !== "bank-pass") return json({ detail: "Mot de passe incorrect" }, 403);
      state.st = EMPTY;
      return json(state.st);
    }
    if (url === "/api/banks/setup") return json(state.st);
    return json({}, 404);
  });
}

const mount = (onChange = vi.fn()) =>
  act(async () => root.render(<ToastProvider><BankSetup chooser={<p>CHOIX-DE-LA-BANQUE</p>} onChange={onChange} /></ToastProvider>));

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  copied = [];
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: (t: string) => { copied.push(t); return Promise.resolve(); } } });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("assistant Enable Banking", () => {
  it("knows where you are and reads the app id from the .pem file name", () => {
    expect(setupStep(null)).toBe(0);
    expect(setupStep(EMPTY)).toBe(0);
    expect(setupStep({ ...checked([]), check: null })).toBe(3);
    expect(setupStep(checked([REDIRECT]))).toBe(3);
    expect(setupStep(checked([INACTIVE]))).toBe(4);
    expect(setupStep(checked([]))).toBe(5);
    expect(setupReady(checked([]))).toBe(true);
    expect(setupReady(checked([INACTIVE]))).toBe(false);
    expect(appIdFromFileName(`${APP_ID}.pem`)).toBe(APP_ID);
    expect(appIdFromFileName(`${APP_ID} (1).pem`)).toBe(APP_ID);
    expect(appIdFromFileName("cle.pem")).toBeNull();
  });

  it("walks from creating the app to choosing the bank, the key sent once and never shown", async () => {
    const state = { st: EMPTY, afterPut: checked([INACTIVE]), afterCheck: checked([]) };
    mockApi(state);
    const onChange = vi.fn();
    await mount(onChange);
    await settle();
    expect(host.querySelectorAll(".wizard-steps li")).toHaveLength(6);
    expect(activeStep()).toContain("Créer l'application");
    expect(text()).toContain("Production");
    expect(host.querySelector<HTMLAnchorElement>(`a[href="${EMPTY.console_url}"]`)?.target).toBe("_blank");

    await click("J'y suis");
    expect(activeStep()).toContain("Adresse de retour");
    expect(text()).toContain("https://bank.example/api/banks/callback");
    await click("Copier");
    expect(copied).toEqual(["https://bank.example/api/banks/callback"]);

    await click("C'est fait");
    expect(activeStep()).toContain("Ta clé");
    await drop(new File([PEM], `${APP_ID}.pem`, { type: "application/x-pem-file" }));
    expect(text()).toContain(`Clé chargée : ${APP_ID}.pem`);
    const appId = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith("Identifiant"))!.querySelector("input")!;
    expect(appId.value).toBe(APP_ID); // repris du nom du fichier
    await type("Ton mot de passe Bank", "mauvais");
    await click("Enregistrer et vérifier");
    expect(text()).toContain("Mot de passe incorrect");
    expect(activeStep()).toContain("Ta clé");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Enregistrer et vérifier");
    const put = calls.filter((c) => c.init?.method === "PUT").pop()!;
    expect(JSON.parse(String(put.init!.body))).toEqual({ password: "bank-pass", app_id: APP_ID, private_key: PEM.trim() });

    // Clé bonne, application pas encore active : l'étape 5 explique le geste à faire chez Enable Banking.
    expect(activeStep()).toContain("Activation");
    expect(text()).toContain("Activate by linking accounts");
    expect(text()).toContain("…3b4a59");
    expect(text()).toContain("ab12 cd34 ef56 7890");
    expect(text()).not.toContain("SECRETSECRET");
    expect(host.innerHTML).not.toContain("BEGIN PRIVATE KEY-----\nMII");

    await click("J'ai lié mes comptes");
    expect(calls.some((c) => c.url === "/api/banks/setup/check" && c.init?.method === "POST")).toBe(true);
    expect(activeStep()).toContain("Ta banque");
    expect(text()).toContain("« Bank perso » vérifiée");
    expect(text()).toContain("CHOIX-DE-LA-BANQUE");
    expect(setupReady(onChange.mock.calls.at(-1)![0])).toBe(true);
  });

  it("refuses a file that is not a private key before sending anything, and explains a refused key", async () => {
    mockApi({ st: EMPTY });
    await mount();
    await settle();
    await click("J'y suis");
    await click("C'est fait");
    await drop(new File(["-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"], "cert.pem"));
    expect(text()).toContain("ne contient pas de clé privée");
    await drop(new File([PEM], "cle.pem"));
    await type("Identifiant de l'application", "0000aaaa-1111-2222-3333-444455556666");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Enregistrer et vérifier");
    expect(text()).toContain("ne reconnaît pas cette clé");
    expect(activeStep()).toContain("Ta clé");
  });

  it("says what to fix when the redirect address is missing, with the address to copy", async () => {
    mockApi({ st: checked([REDIRECT]), afterCheck: checked([]) });
    await mount();
    await settle();
    expect(activeStep()).toContain("Vérification");
    expect(text()).toContain("Allowed redirect URLs");
    await click("Copier");
    expect(copied).toEqual(["https://bank.example/api/banks/callback"]);
    await click("Vérifier à nouveau");
    expect(activeStep()).toContain("Ta banque");
  });

  it("checks a server configuration once by itself and never offers to edit it", async () => {
    const server = { ...checked([]), source: "server" as const, check: null };
    mockApi({ st: server, afterCheck: { ...server, check: checked([]).check } });
    await mount();
    await settle();
    expect(calls.filter((c) => c.url === "/api/banks/setup/check")).toHaveLength(1);
    expect(activeStep()).toContain("Ta banque");
    expect(text()).toContain("configurée par le serveur");
    expect([...host.querySelectorAll("button")].some((b) => b.textContent?.includes("Retirer"))).toBe(false);
  });

  it("removes the app with the Bank password", async () => {
    mockApi({ st: checked([]) });
    await mount();
    await settle();
    await click("Retirer");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Retirer l'application");
    const del = calls.find((c) => c.init?.method === "DELETE")!;
    expect(JSON.parse(String(del.init!.body))).toEqual({ password: "bank-pass" });
    expect(activeStep()).toContain("Créer l'application");
  });
});
