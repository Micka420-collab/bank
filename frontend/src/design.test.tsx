// Refonte UI : connexion bancaire, première ouverture, graphiques, thème — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, statusMessage } from "./api";
import { App } from "./App";
import { caisseLabel, BANK_BRANDS } from "./banks";
import { BankConnect, tileState } from "./components/BankConnect";
import { Bars } from "./components/charts/Bars";
import { Donut } from "./components/charts/Donut";
import { scaleY, TrendChart } from "./components/charts/TrendChart";
import { Journey } from "./components/Journey";
import { Onboarding } from "./components/Onboarding";
import { ToastProvider } from "./components/ui";
import { merchantCase } from "./format";
import { Login } from "./pages/Auth";
import { bucketSlices, sliceRange } from "./pages/Wealth";
import { applyTheme, readTheme } from "./theme";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ASPSPS = ["Crédit Agricole Centre Loire", "Crédit Agricole Val de France", "BNP Paribas", "Boursorama", "Caisse d'Epargne Loire Centre", "Banque Delubac"];
// Assistant Enable Banking : prêt (application vérifiée) ou rien de configuré.
const SETUP_READY = {
  configured: true, source: "server", app_id_hint: "…3b4a59", key_fingerprint: "ab12 cd34 ef56 7890", updated_at: null,
  redirect_url: "https://bank.example/api/banks/callback", console_url: "https://enablebanking.com/cp/applications",
  check: { ok: true, issues: [], app_name: "Bank", environment: "PRODUCTION", checked_at: "2026-10-24T10:00:00Z" }, connections: 0,
};
const SETUP_EMPTY = { ...SETUP_READY, configured: false, source: null, app_id_hint: null, key_fingerprint: null, check: null };
const CA = BANK_BRANDS.find((b) => b.id === "ca")!;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function mockApi(handlers: Record<string, (init?: RequestInit) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    const out = handlers[key]!(init);
    return Promise.resolve(out instanceof Response ? out : jsonResponse(out));
  });
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
/** Laisse passer une chaîne requête → état → effet → requête (chaque act rend puis lance les effets). */
const settle = async () => { for (let i = 0; i < 5; i++) await flush(); };
const clean = (s: string | null | undefined) => (s ?? "").replace(/[\u202f\u00a0]/g, " ");
const render = (node: React.ReactNode) => act(async () => root.render(node));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });
const type = (el: HTMLInputElement, value: string) => act(async () => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  window.localStorage.clear();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("connexion bancaire", () => {
  it("groups regional banks behind one tile and names the branch without the brand", () => {
    expect(caisseLabel(CA, "Crédit Agricole Centre Loire")).toBe("Centre Loire");
    expect(caisseLabel(BANK_BRANDS.find((b) => b.id === "ce")!, "Caisse d'Epargne Ile-de-France")).toBe("Ile-de-France");
    expect(caisseLabel(BANK_BRANDS.find((b) => b.id === "bp")!, "BRED Banque Populaire")).toBe("BRED");
    expect(caisseLabel(CA, "Crédit Agricole")).toBe("Crédit Agricole");
    expect(tileState("BNP Paribas", [], "BNP Paribas", null)).toBe("busy");
    expect(tileState("BNP Paribas", [], null, "BNP Paribas")).toBe("error");
    expect(tileState("BNP Paribas", [{ id: 1, aspsp_name: "BNP Paribas", aspsp_country: "FR", status: "active", valid_until: null, last_sync_at: null, last_error: null, accounts: [] }], null, null)).toBe("connected");
  });

  it("shows a skeleton, then tiles, search and the branch chooser; connects in one tap", async () => {
    const onConnect = vi.fn();
    await render(<BankConnect aspsps={null} connections={[]} busy={null} failed={null} onConnect={onConnect} />);
    expect(host.querySelector("[aria-busy='true']")).not.toBeNull();

    await render(<BankConnect aspsps={ASPSPS} connections={[]} busy={null} failed={null} onConnect={onConnect} />);
    const tiles = [...host.querySelectorAll<HTMLButtonElement>(".bank-tile")];
    expect(tiles.map((t) => t.querySelector("b")?.textContent)).toEqual(["Crédit Agricole", "BNP Paribas", "Caisse d'Épargne", "BoursoBank"]);
    expect(tiles[0]!.textContent).toContain("2 caisses");

    await act(async () => tiles[1]!.click());
    expect(onConnect).toHaveBeenCalledWith("BNP Paribas");

    await act(async () => tiles[0]!.click());
    expect(host.querySelector(".bank-grid")).toBeNull();
    expect(host.textContent).toContain("Crédit Agricole — laquelle est la tienne ?");
    await click("Val de France");
    expect(onConnect).toHaveBeenLastCalledWith("Crédit Agricole Val de France");
    await click("Toutes les banques");
    expect(host.querySelector(".bank-grid")).not.toBeNull();

    const search = host.querySelector<HTMLInputElement>("input[aria-label='Rechercher une banque']")!;
    await type(search, "delu");
    expect(host.querySelector(".bank-grid")).toBeNull();
    expect(host.textContent).toContain("Banque Delubac");
    await type(search, "zzz");
    expect(host.textContent).toContain("Aucun établissement ne correspond");
  });

  it("marks connected, busy and failed banks on their tiles", async () => {
    const conn = { id: 1, aspsp_name: "Boursorama", aspsp_country: "FR", status: "active", valid_until: null, last_sync_at: null, last_error: null, accounts: [] };
    await render(<BankConnect aspsps={ASPSPS} connections={[conn]} busy="BNP Paribas" failed={null} onConnect={() => undefined} />);
    expect(host.querySelector(".bank-tile.is-connected b")?.textContent).toBe("BoursoBank");
    expect(host.querySelector("[aria-label='BNP Paribas, connexion en cours'] .bank-state.busy")).not.toBeNull();
    expect([...host.querySelectorAll<HTMLButtonElement>(".bank-tile")].every((t) => t.disabled)).toBe(true);
    await render(<BankConnect aspsps={ASPSPS} connections={[]} busy={null} failed="BNP Paribas" onConnect={() => undefined} />);
    expect(host.querySelector(".bank-tile.is-error b")?.textContent).toBe("BNP Paribas");
  });
});

describe("première ouverture", () => {
  it("walks intro → access, then hands over to the journey", async () => {
    mockApi({
      "/api/setup": () => ({ ok: true }),
      "/api/auth/login": () => ({ mfa_required: false }),
    });
    const onAccess = vi.fn();
    await render(<ToastProvider><Onboarding onAccess={onAccess} /></ToastProvider>);
    expect(host.textContent).toContain("Combien tu peux dépenser aujourd'hui");
    expect(host.querySelectorAll(".onboarding-steps i")).toHaveLength(2);
    // Aucun style en ligne (CSP) : les marges et les délais d'animation sont des classes.
    expect(host.querySelector("[style]")).toBeNull();
    await click("Suivant");
    expect(host.textContent).toContain("Tes aides, ta paie, tes abonnements");
    await click("Passer l'introduction");
    expect(host.querySelector("form[aria-label='Créer ton accès']")).not.toBeNull();

    const inputs = [...host.querySelectorAll<HTMLInputElement>("form input")];
    await type(inputs[0]!, "setup-token");
    await type(inputs[1]!, "micka");
    await type(inputs[2]!, "une phrase de passe longue");
    await type(inputs[3]!, "une phrase de passe longue");
    await act(async () => host.querySelector("form")!.requestSubmit());
    await flush();
    expect(calls.map((c) => c.url)).toEqual(["/api/setup", "/api/auth/login"]);
    expect(onAccess).toHaveBeenCalled();
    // Plus de drapeau local : c'est le serveur qui dit si le parcours reste à faire.
    expect(window.localStorage.getItem("bank:onboarding")).toBeNull();
  });

  it("opens the journey once for a new account, and Quitter lands on the dashboard", async () => {
    window.location.hash = "#/aujourdhui";
    let status = "new";
    mockApi({
      "/api/setup/status": () => ({ needs_setup: false }),
      "/api/auth/me": () => ({ username: "micka", totp_enabled: false, recovery_codes_left: 0, session_expires_at: "2026-10-22T10:00:00Z" }),
      "/api/start/steps/banque": () => { status = "in_progress"; return startView(status); },
      "/api/start": () => startView(status),
      "/api/banks/setup": () => SETUP_EMPTY,
      "/api/banks": () => ({ enabled: false, connections: [] }),
    });
    await render(<App />);
    await settle();
    await settle();
    expect(host.querySelector(".journey")).not.toBeNull();
    expect(host.querySelector("h1")?.textContent).toBe("Ta banque");
    await click("Quitter");
    await settle();
    expect(host.querySelector(".journey")).toBeNull();
    expect(host.querySelector(".shell")).not.toBeNull();
    expect(window.location.hash).toBe("#/aujourdhui");
    window.location.hash = "";
  });

  it("guides through the Enable Banking setup only after you choose to link your bank", async () => {
    mockApi({
      "/api/start/steps": () => startView("in_progress"),
      "/api/start": () => startView("in_progress"),
      "/api/banks/setup": () => SETUP_EMPTY,
      "/api/banks": () => ({ enabled: false, connections: [] }),
    });
    await render(<ToastProvider><Journey stepKey="banque" onExit={() => undefined} /></ToastProvider>);
    await settle();
    // Trois chemins d'abord, aucun assistant technique en première impression ; un seul « Plus tard ».
    expect(host.querySelectorAll(".path")).toHaveLength(3);
    expect(host.textContent).not.toContain("Crée ton application Enable Banking");
    expect([...host.querySelectorAll("button")].filter((b) => /^(Plus tard|Je connecterai|Continuer sans)/.test(b.textContent?.trim() ?? ""))).toHaveLength(1);
    // Sans Enable Banking configuré, la carte dit ce qu'elle demande : un compte tiers, ~15 min, plutôt sur ordinateur.
    expect(host.querySelector(".path")?.textContent).toContain("créer un compte gratuit chez Enable Banking");
    expect(host.querySelector(".path")?.textContent).not.toContain("Recommandé");
    await act(async () => { [...host.querySelectorAll<HTMLButtonElement>(".path")][0]!.click(); });
    await settle();
    expect(host.textContent).toContain("Avant ta banque, un compte chez Enable Banking.");
    expect(host.textContent).not.toContain("Crée ton application Enable Banking");
    await act(async () => { [...host.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Configurer Enable Banking"))!.click(); });
    await settle();
    expect(host.textContent).toContain("Crée ton application Enable Banking");
    expect(host.querySelector(".wizard-steps li.is-active")?.textContent).toContain("Créer l'application");
    expect(host.querySelector(".bank-grid")).toBeNull();
    expect(calls.some((c) => c.url.startsWith("/api/banks/aspsps"))).toBe(false);
  });

  it("back from the bank: visible sync steps, then the connected accounts and a way to add another bank", async () => {
    mockApi({
      "/api/start/steps": () => startView("in_progress"),
      "/api/start": () => startView("in_progress"),
      "/api/banks/1/sync": () => ({ inserted: 184, reconciled: 0, errors: [] }),
      "/api/banks": () => ({ enabled: true, connections: [{ id: 1, aspsp_name: "Boursorama", aspsp_country: "FR", status: "active", valid_until: null, last_sync_at: null, last_error: null, accounts: ["Compte courant"] }] }),
      "/api/accounts": () => [{ id: 1, name: "Compte courant", kind: "checking", institution: "Boursorama", iban_masked: "FR76 •••• 1234", balance_cents: 64000, balance_at: null, include_in_budget: true, archived: false, provider: "enablebanking" }],
    });
    await render(<ToastProvider><Journey stepKey="banque" bankResult="connected" onExit={() => undefined} /></ToastProvider>);
    await settle();
    expect(host.textContent).toContain("Bank lit tes comptes");
    expect(calls.find((c) => c.url === "/api/banks/1/sync")?.init?.method).toBe("POST");
    expect(host.textContent).toContain("Opérations lues (184)");
    for (let i = 0; i < 8; i++) await wait(600); // les étapes avancent toutes les 900 ms, chaque act rend l'état courant
    await settle();
    expect(host.textContent).toContain("1 compte relié");
    expect(host.textContent).toContain("FR76 •••• 1234");
    expect(button("Ajouter une autre banque")).not.toBeNull();
  }, 10000);
});

/** Un état de parcours minimal, comme le renvoie /api/start. */
function startView(status: string) {
  const keys = ["banque", "foyer", "accords", "papier", "priorites", "prevenir", "visite"];
  const titles = ["Ta banque", "Ton foyer", "Tes accords", "Ton premier papier", "Tes priorités", "Où te prévenir", "La visite"];
  return {
    journey: { status, current: "banque", steps: keys.map((k, i) => ({ key: k, title: titles[i], done: false, skipped: false, minutes: 1, href: `#/bienvenue/${k}` })) },
    checklist: { visible: true, collapsed: false, dismissed: false, done: 0, total: 8, completed_at: null, just_completed: false, items: [] },
    public_consents: [],
    connectors: [],
  };
}

describe("connexion rapide", () => {
  it("greets a remembered user and validates the six-digit code by itself", async () => {
    window.localStorage.setItem("bank:username", "micka");
    mockApi({
      "/api/auth/login": () => ({ mfa_required: true, mfa_token: "t" }),
      "/api/auth/mfa": () => ({ ok: true }),
    });
    const onDone = vi.fn();
    await render(<Login onDone={onDone} />);
    expect(host.querySelector("h1")?.textContent).toBe("Bonjour micka");
    expect(host.querySelector("input[autocomplete=username]")).toBeNull();
    expect(host.querySelector<HTMLInputElement>("input[autocomplete=current-password]")!.type).toBe("password");
    await click("Afficher");
    const pw = host.querySelector<HTMLInputElement>("input[autocomplete=current-password]")!;
    expect(pw.type).toBe("text");
    await type(pw, "demo demo demo 42");
    await act(async () => host.querySelector("form")!.requestSubmit());
    await flush();
    expect(JSON.parse(String(calls[0]!.init?.body))).toEqual({ username: "micka", password: "demo demo demo 42" });
    expect(host.querySelector("h1")?.textContent).toBe("Code de vérification");
    const code = host.querySelector<HTMLInputElement>("input[autocomplete=one-time-code]")!;
    await type(code, "123 456");
    await settle();
    expect(calls.at(-1)?.url).toBe("/api/auth/mfa");
    expect(JSON.parse(String(calls.at(-1)!.init?.body))).toEqual({ mfa_token: "t", code: "123456" });
    expect(onDone).toHaveBeenCalled();
  });
});

describe("graphiques", () => {
  it("scales a balance around zero and a net worth around its own values", () => {
    const zero = scaleY([400_000, 450_000], "zero");
    expect(zero.min).toBeLessThan(0);
    const auto = scaleY([400_000, 450_000], "auto");
    expect(auto.min).toBeGreaterThan(390_000);
    expect(auto.max).toBeLessThan(460_000);
    const flat = scaleY([1000, 1000], "auto");
    expect(flat.max - flat.min).toBeGreaterThan(0);
  });

  it("draws the trend with a readable summary and a keyboard-driven tooltip", async () => {
    const points = Array.from({ length: 5 }, (_, i) => ({ date: `2026-10-${String(10 + i).padStart(2, "0")}`, value: 100_000 + i * 10_000 }));
    await render(<TrendChart points={points} label="Solde" />);
    const svg = host.querySelector<SVGSVGElement>("svg.chart")!;
    expect(clean(svg.getAttribute("aria-label"))).toContain("Solde : du 10 oct au 14 oct, 1 000 € puis 1 400 € (+400 €)");
    expect(host.querySelector(".chart-tip")).toBeNull();
    await act(async () => { svg.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })); });
    expect(clean(host.querySelector(".chart-tip")?.textContent)).toContain("1 300 €");
    await act(async () => { svg.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(host.querySelector(".chart-tip")).toBeNull();
    await render(<TrendChart points={[]} label="Solde" />);
    expect(host.textContent).toContain("Pas encore de données");
  });

  it("splits spending into a ring with a legend, and periods into paired bars", async () => {
    const slices = [
      { slug: "logement", name: "Logement", color: "#111", cents: 65_000, share: 0.65 },
      { slug: "courses", name: "Courses", color: "#222", cents: 35_000, share: 0.35 },
    ];
    await render(<Donut slices={slices} total={100_000} label="Dépenses" />);
    expect(host.querySelectorAll(".donut-arc")).toHaveLength(2);
    expect(host.querySelector("svg")?.getAttribute("aria-label")).toContain("Logement 65 %");
    expect(host.querySelectorAll(".donut-list li")).toHaveLength(2);
    await render(<Donut slices={[]} total={0} label="Actifs" centerLabel="actifs" />);
    expect(host.textContent).toContain("Rien à répartir");
    await render(<Bars periods={[{ key: "2026-09", label: "sept. 26", income_cents: 200_000, spent_cents: 150_000 }]} />);
    expect(host.querySelectorAll(".bar-pair i")).toHaveLength(2);
    expect(host.querySelector(".bar-pair i")?.getAttribute("style")).toContain("height: 100%");
  });

  it("buckets net worth for the ring and slices the daily series by period", () => {
    const slices = bucketSlices({ liquidites: 64_000, epargne_disponible: 790_000, dettes: -20_000, autres_actifs: 0 });
    expect(slices.map((s) => s.slug)).toEqual(["epargne_disponible", "liquidites"]);
    expect(slices[0]!.share + slices[1]!.share).toBeCloseTo(1);
    const series = Array.from({ length: 400 }, (_, i) => i);
    expect(sliceRange(series, "7d")).toHaveLength(7);
    expect(sliceRange(series, "1y")).toHaveLength(365);
    expect(sliceRange(series, "all")).toHaveLength(400);
  });

  it("formats merchants without shouting", () => {
    expect(merchantCase("TOTALENERGIES ORLEANS")).toBe("Totalenergies Orleans");
  });
});

describe("thème", () => {
  it("applies and remembers the chosen theme, or follows the device", () => {
    expect(readTheme()).toBe("auto");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(readTheme()).toBe("dark");
    applyTheme("auto");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(window.localStorage.getItem("bank:theme")).toBeNull();
    window.localStorage.setItem("bank:theme", "nope");
    expect(readTheme()).toBe("auto");
  });
});

// Une limite de débit (nginx renvoie du HTML, pas du JSON) ne doit jamais se lire « serveur en panne » ni « Erreur 429 ».
describe("erreurs qui disent quoi faire", () => {
  const nginx429 = () => new Response("<html><body><h1>429 Too Many Requests</h1></body></html>", { status: 429, headers: { "content-type": "text/html" } });

  it("turns bare HTTP statuses into plain advice, and keeps the server's own words when it gives some", async () => {
    expect(statusMessage(429)).toMatch(/patiente une minute/);
    expect(statusMessage(413)).toBe("Fichier trop lourd pour être envoyé.");
    expect(statusMessage(502)).toMatch(/ne répond pas pour l'instant/);
    expect(statusMessage(500)).toBe("Erreur 500");
    mockApi({ "/api/transactions": () => nginx429() });
    const err = await api("/api/transactions").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toBe(statusMessage(429));
    mockApi({ "/api/auth/login": () => jsonResponse({ detail: "Trop de tentatives, réessaie dans quelques minutes" }, 429) });
    const own = await api("/api/auth/login", { method: "POST", body: {} }).catch((e: unknown) => e);
    expect((own as ApiError).message).toBe("Trop de tentatives, réessaie dans quelques minutes");
  });

  it("opens on « patiente » rather than « serveur en panne » when the server throttles, then retries into the login", async () => {
    let throttled = true;
    mockApi({
      "/api/setup/status": () => (throttled ? nginx429() : { needs_setup: false }),
      "/api/auth/me": () => jsonResponse({ detail: "Non connecté" }, 401),
    });
    await render(<App />);
    await settle();
    const alert = host.querySelector("[role=alert]");
    expect(alert?.textContent).toMatch(/patiente une minute/);
    expect(alert?.textContent).not.toMatch(/ne répond pas/);
    throttled = false;
    await click("Réessayer");
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Connexion");
  });

  it("still says the server is down when nothing answers at all", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    await render(<App />);
    await settle();
    expect(host.querySelector("[role=alert]")?.textContent).toMatch(/ne répond pas\. Vérifie qu'il est démarré/);
  });

  it("shows the advice on the login form instead of a status code", async () => {
    mockApi({ "/api/auth/login": () => nginx429() });
    await render(<Login onDone={() => undefined} />);
    await type(host.querySelector<HTMLInputElement>("input[autocomplete=username]")!, "micka");
    await type(host.querySelector<HTMLInputElement>("input[autocomplete=current-password]")!, "mot de passe");
    await act(async () => host.querySelector("form")!.requestSubmit());
    await settle();
    const text = host.textContent ?? "";
    expect(text).toMatch(/patiente une minute/);
    expect(text).not.toMatch(/Erreur 429/);
  });
});
