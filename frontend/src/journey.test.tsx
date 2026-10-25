// Démarrer avec Bank (#44) : parcours plein écran (navigation, reprise, passer, focus), liste « Bien démarrer »
// (anneau, liens, repli, fête), accords groupés (« Tout activer » n'accorde que les données publiques, après un clic),
// mouvement (transitions de vue qui gardent le focus, mouvement réduit qui va droit à l'état final).

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicConsent, StartStepKey, StartView } from "./api";
import { ConsentGroups, groupConsents } from "./components/ConsentGroups";
import { Journey, nextAfter } from "./components/Journey";
import { StartChecklist } from "./components/StartChecklist";
import { moreLabel } from "./components/StartChecklist";
import { ToastProvider } from "./components/ui";

// Les notifications du navigateur, pilotables : jsdom n'a ni service worker ni PushManager.
const push = vi.hoisted(() => ({ supported: false, permission: "default" as NotificationPermission, enable: null as null | (() => Promise<unknown>) }));
vi.mock("./push", () => ({
  pushSupported: () => push.supported,
  pushPermission: () => push.permission,
  enablePush: () => (push.enable ? push.enable() : Promise.reject(new Error("indisponible"))),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const KEYS: StartStepKey[] = ["banque", "foyer", "accords", "papier", "priorites", "prevenir", "visite"];
const TITLES = ["Ta banque", "Ton foyer", "Tes accords", "Ton premier papier", "Tes priorités", "Où te prévenir", "La visite"];
const PUBLIC: PublicConsent[] = [
  ["watch", "public", "Veille sur les sources officielles"],
  ["baremes", "public", "Barèmes tenus à jour"],
  ["tempo", "public", "Couleur Tempo"],
  ["calendar", "zone", "Jours fériés et vacances scolaires"],
  ["home_value", "zone", "Valeur de ton logement (ventes DVF)"],
  ["meteo", "zone", "Météo de ta commune"],
].map(([purpose, group, title]) => ({ purpose: purpose!, group: group as "public" | "zone", title: title!, sends: `Ce qui part pour ${purpose}.`, text: `Texte complet de ${purpose}.`, granted: false, decided: false }));
const ESSENTIALS: [string, string, string][] = [
  ["banque", "Relier ta banque", "#/bienvenue/banque"],
  ["foyer", "Décrire ton foyer", "#/bienvenue/foyer"],
  ["accords", "Tes accords pour les données publiques", "#/bienvenue/accords"],
  ["papier", "Ranger un premier papier", "#/bienvenue/papier"],
  ["priorites", "Choisir tes priorités", "#/bienvenue/priorites"],
  ["prevenir", "Être prévenu", "#/bienvenue/prevenir"],
  ["securite", "Protéger ton accès", "#/reglages?section=sec-title"],
  ["sauvegarde", "Vérifier ta sauvegarde", "#/reglages?section=backup-title"],
];

/** Un petit serveur /api/start en mémoire, avec les mêmes règles que services/start.py (étape en cours, passer). */
class FakeStart {
  status: "new" | "in_progress" | "finished" = "new";
  done = new Set<string>();
  skipped = new Set<string>();
  current: StartStepKey | null = null;
  completed: string | null = null;
  dismissed = false;
  consents = PUBLIC.map((c) => ({ ...c }));

  view(): StartView {
    let cur = this.current;
    const open = (k: StartStepKey) => !this.done.has(k) && !this.skipped.has(k);
    if (!cur || this.done.has(cur)) cur = KEYS.slice(cur ? KEYS.indexOf(cur) + 1 : 0).find(open) ?? KEYS.find(open) ?? null;
    const count = ESSENTIALS.filter(([k]) => this.done.has(k)).length;
    return {
      journey: {
        status: this.status,
        current: this.status === "finished" ? null : cur,
        steps: KEYS.map((k, i) => ({ key: k, title: TITLES[i]!, done: this.done.has(k), skipped: this.skipped.has(k) && !this.done.has(k), minutes: 1, href: `#/bienvenue/${k}` })),
      },
      checklist: {
        visible: !this.dismissed,
        collapsed: this.completed !== null && !this.dismissed,
        dismissed: this.dismissed,
        done: count,
        total: 8,
        completed_at: this.completed,
        just_completed: false,
        items: [
          ...ESSENTIALS.map(([key, title, href]) => ({ key, title, why: `Pourquoi ${key}.`, minutes: 1, done: this.done.has(key), group: "essentiel" as const, href, value_cents: null })),
          { key: "linky", title: "Relier ton compteur Linky", why: "Ta facture prévue.", minutes: 3, done: false, group: "plus" as const, href: "#/connecteurs?section=linky-title", value_cents: null },
        ],
      },
      public_consents: this.consents,
      connectors: [],
    };
  }

  handle(url: string, init?: RequestInit): unknown {
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    const step = url.match(/^\/api\/start\/steps\/(\w+)/);
    if (step) {
      const key = step[1] as StartStepKey;
      if (this.status === "new") this.status = "in_progress";
      if (body.action === "skip" && !this.done.has(key)) {
        this.skipped.add(key);
        const from = KEYS.indexOf(key);
        this.current = KEYS.slice(from + 1).find((k) => !this.done.has(k) && !this.skipped.has(k)) ?? null;
      } else if (body.action === "resume") {
        this.skipped.delete(key);
        this.current = key;
      } else this.current = key;
      return this.view();
    }
    if (url === "/api/start/finish") {
      this.status = "finished";
      if (body?.tour_done) this.done.add("visite");
      return this.view();
    }
    if (url === "/api/start/checklist") {
      this.dismissed = body.action === "dismiss";
      return this.view();
    }
    if (url === "/api/start/public-consents") {
      const refused = (body.purposes as string[]).filter((p) => !PUBLIC.some((c) => c.purpose === p));
      if (refused.length) return new Response(JSON.stringify({ detail: "refusé" }), { status: 422 });
      for (const c of this.consents) if (body.purposes.includes(c.purpose)) Object.assign(c, { granted: true, decided: true });
      this.done.add("accords");
      return this.consents;
    }
    if (url.startsWith("/api/start")) return this.view();
    return undefined;
  }
}

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
let server: FakeStart;

function serve(extra: Record<string, (init?: RequestInit) => unknown> = {}) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const out = server.handle(url, init) ?? Object.entries(extra).find(([k]) => url.startsWith(k))?.[1](init);
    if (out === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
    if (out instanceof Response) return Promise.resolve(out);
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const render = (node: React.ReactNode) => act(async () => root.render(<ToastProvider>{node}</ToastProvider>));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const posted = (path: string) => calls.filter((c) => c.url === path && c.init?.method === "POST").map((c) => JSON.parse(String(c.init?.body ?? "null")));
/** Rend le parcours à l'étape que dit l'adresse, comme App le fait à chaque « hashchange ». */
const journeyAt = (onExit = vi.fn()) => {
  const step = window.location.hash.replace(/^#\/bienvenue\/?/, "") || null;
  return render(<Journey stepKey={step} onExit={onExit} />);
};

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  server = new FakeStart();
  window.location.hash = "#/bienvenue";
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("le parcours", () => {
  it("resumes where the server says, focuses the step title and shows done steps in the bar", async () => {
    server.status = "in_progress";
    server.done = new Set(["banque", "foyer"]);
    serve({ "/api/opportunities/top": () => ({ opportunities: [], value: {}, priorities: [] }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    // Les étapes faites sont sautées : reprise à « Tes accords », l'adresse le dit.
    expect(window.location.hash).toBe("#/bienvenue/accords");
    const h1 = host.querySelector("h1")!;
    expect(h1.textContent).toBe("Tes accords");
    expect(document.activeElement).toBe(h1);
    expect(document.title).toBe("Tes accords · Bank");
    expect(host.textContent).toContain("Étape 3 sur 7");
    const items = [...host.querySelectorAll(".journey-progress li")];
    expect(items.map((li) => li.getAttribute("aria-current"))).toEqual([null, null, "step", null, null, null, null]);
    expect(items[0]!.classList.contains("is-done")).toBe(true);
    expect(items[0]!.textContent).toContain("Ta banque, faite");
    expect(posted("/api/start/steps/accords")).toEqual([{ action: "current" }]);
    expect(host.querySelector("[style]")).toBeNull(); // CSP : aucun style en ligne
  });

  it("« Plus tard » skips to the next step, says so, and moves the focus to the new title", async () => {
    serve({ "/api/onboarding": () => ({ questions: [], answers: {}, suggestions: [] }), "/api/territory": () => ({ consents: { geocoding: false }, place: null }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Ta banque");
    await click("Plus tard");
    expect(posted("/api/start/steps/banque")).toEqual([{ action: "current" }, { action: "skip" }]);
    expect(window.location.hash).toBe("#/bienvenue/foyer");
    await journeyAt();
    await settle();
    const h1 = host.querySelector("h1")!;
    expect(h1.textContent).toBe("Ton foyer");
    expect(document.activeElement).toBe(h1);
    // Le titre qui reçoit le focus est décrit par la confirmation : lue d'un trait, pas d'annonce coupée.
    const flash = document.getElementById(h1.getAttribute("aria-describedby")!);
    expect(flash?.textContent).toContain("Étape passée : Ta banque.");
    expect(document.querySelector(".toast")).toBeNull();
    expect(host.querySelector(".journey-progress li")?.classList.contains("is-skipped")).toBe(true);
  });

  it("a deep link opens that step and the server remembers it; Échap quits without finishing", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/papier";
    serve();
    const onExit = vi.fn();
    await journeyAt(onExit);
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Ton premier papier");
    expect(server.current).toBe("papier");
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(onExit).toHaveBeenCalledTimes(1);
    expect(posted("/api/start/finish")).toEqual([]);
  });

  it("Échap dans un champ sert au champ : le parcours ne se ferme pas", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/papier";
    serve();
    const onExit = vi.fn();
    await journeyAt(onExit);
    await settle();
    const field = document.createElement("input");
    host.querySelector(".journey-card")!.appendChild(field);
    field.focus();
    await act(async () => { field.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(onExit).not.toHaveBeenCalled();
  });

  it("the tour never advances on its own; its end finishes the journey and celebrates", async () => {
    server.status = "in_progress";
    server.done = new Set(["banque"]);
    window.location.hash = "#/bienvenue/visite";
    serve();
    const onExit = vi.fn();
    await journeyAt(onExit);
    await settle();
    expect(host.querySelector(".tour-title")?.textContent).toBe("Jour, écran 1 sur 5");
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(host.querySelector(".tour-title")?.textContent).toBe("Jour, écran 1 sur 5");
    await click("Suivant");
    expect(host.querySelector(".tour-title")?.textContent).toBe("Opérations, écran 2 sur 5");
    expect(host.querySelector(".tour [aria-live], .journey [aria-live]")).toBeNull(); // la position est dans le titre, pas annoncée deux fois
    expect(document.activeElement).toBe(host.querySelector(".tour-title"));
    expect(host.querySelector(".tour-island")?.getAttribute("aria-label")).toContain("Opérations");
    for (let i = 0; i < 3; i++) await click("Suivant");
    expect(host.textContent).toContain("Demande à Bank, en bas à droite");
    // Dernier écran : une action, « Précédent » en lien, plus de « Passer la visite » qui ferait la même chose.
    expect([...host.querySelectorAll("button")].some((b) => b.textContent?.includes("Passer la visite"))).toBe(false);
    expect(button("Précédent").classList.contains("link")).toBe(true);
    await click("Voir mon tableau de bord");
    expect(posted("/api/start/finish")).toEqual([{ tour_done: true }]);
    expect(onExit).toHaveBeenCalled();
    // Rien d'autre n'a été fait pendant ce passage : regarder la visite ne se fête pas.
    expect(document.querySelector(".celebrate")).toBeNull();
  });

  it("the end celebrates only the steps done during this pass, and says what is left", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/priorites";
    serve({
      "/api/opportunities/top": () => ({ opportunities: [], value: {}, priorities: ["reduire"] }),
      "/api/opportunities/settings": () => { server.done.add("priorites"); return {}; },
    });
    server.skipped = new Set(["banque", "foyer", "accords", "papier", "prevenir"]);
    const onExit = vi.fn();
    await journeyAt(onExit);
    await settle();
    await click("Garder ces priorités");
    expect(window.location.hash).toBe("#/bienvenue/visite");
    await journeyAt(onExit);
    await settle();
    expect(host.textContent).toContain("Étape faite : Tes priorités.");
    await click("Passer la visite");
    // La fête est l'écran de fin, dans le parcours : son titre reçoit le focus et dit ce qui est fait et ce qui reste.
    expect(onExit).not.toHaveBeenCalled();
    const h1 = host.querySelector<HTMLHeadingElement>(".journey-finale h1")!;
    expect(h1.textContent).toBe("C'est parti !");
    expect(document.activeElement).toBe(h1);
    expect(document.getElementById(h1.getAttribute("aria-describedby")!)?.textContent).toBe("1 étape faite — il en reste 5, quand tu veux.");
    expect(host.textContent).not.toContain("Bank est prêt");
    expect(host.querySelector(".finale-confetti")).not.toBeNull();
    expect(host.querySelector(".finale-count .ring")?.getAttribute("aria-label")).toBe("1 éléments sur 8 faits");
    expect(host.querySelector(".finale-count .ring-value")?.textContent).toBe("1/8");
    expect(host.querySelector("[style]")).toBeNull(); // CSP : les confettis sont réglés en CSS, pas en ligne
    await click("Voir mon tableau de bord");
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("« Bank est prêt » only when the eight items are done", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/priorites";
    serve({
      "/api/opportunities/top": () => ({ opportunities: [], value: {}, priorities: ["reduire"] }),
      "/api/opportunities/settings": () => { server.done.add("priorites"); return {}; },
    });
    server.done = new Set(ESSENTIALS.map(([k]) => k).filter((k) => k !== "priorites"));
    await journeyAt();
    await settle();
    await click("Garder ces priorités");
    await journeyAt();
    await settle();
    await click("Passer la visite");
    expect(host.querySelector(".journey-finale h1")?.textContent).toBe("Bank est prêt.");
    expect(host.textContent).toContain("1 étape faite : le parcours est complet.");
    expect(host.querySelectorAll(".finale-list li.is-done")).toHaveLength(6);
  });

  it("skipping the tour finishes without a celebration: nothing to celebrate", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/visite";
    serve();
    const onExit = vi.fn();
    await journeyAt(onExit);
    await settle();
    await click("Passer la visite");
    expect(posted("/api/start/finish")).toEqual([{ tour_done: false }]);
    expect(onExit).toHaveBeenCalled();
    expect(document.querySelector(".celebrate")).toBeNull();
  });

  it("picks the next step that is neither done nor skipped, then the tour", () => {
    server.done = new Set(["foyer"]);
    server.skipped = new Set(["accords"]);
    expect(nextAfter(server.view(), "banque")).toBe("papier");
    server.done = new Set(KEYS.filter((k) => k !== "visite"));
    expect(nextAfter(server.view(), "prevenir")).toBe("visite");
    server.done.add("visite");
    expect(nextAfter(server.view(), "visite")).toBeNull();
  });
});

describe("mouvement", () => {
  type Started = { update: () => Promise<void>; done: Promise<void> };
  let started: Started[];
  /** Préférence de mouvement du navigateur ; et une View Transitions API factice qui attend le nouvel état. */
  const motion = (reduce: boolean) => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (q: string) => ({ matches: reduce && q.includes("reduce"), media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }),
    });
    started = [];
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: (update: () => Promise<void>) => {
        const done = update();
        started.push({ update, done });
        return { finished: done, ready: Promise.resolve(), updateCallbackDone: done, skipTransition() {} };
      },
    });
  };
  afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    delete (document as { startViewTransition?: unknown }).startViewTransition;
  });

  it("a step change runs in a view transition, slides the right way and still lands the focus on the new title", async () => {
    motion(false);
    window.location.hash = "#/bienvenue/banque";
    serve({ "/api/onboarding": () => ({ questions: [], answers: {}, suggestions: [] }), "/api/territory": () => ({ consents: { geocoding: false }, place: null }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    await click("Plus tard");
    expect(started).toHaveLength(1);
    expect(document.documentElement.dataset.journeyDir).toBe("forward");
    expect(document.documentElement.classList.contains("is-view-transition")).toBe(true);
    // App rend la nouvelle étape au « hashchange » : la transition est libérée dès qu'elle est dans le DOM.
    await journeyAt();
    await settle();
    await started[0]!.done;
    await settle();
    expect(document.documentElement.classList.contains("is-view-transition")).toBe(false);
    const h1 = host.querySelector("h1")!;
    expect(h1.textContent).toBe("Ton foyer");
    expect(document.activeElement).toBe(h1);
    expect(document.getElementById(h1.getAttribute("aria-describedby")!)?.textContent).toContain("Étape passée : Ta banque.");
    // Revenir en arrière par la barre : même transition, dans l'autre sens.
    await act(async () => { host.querySelector<HTMLAnchorElement>(".journey-progress a")!.click(); });
    expect(started).toHaveLength(2);
    expect(document.documentElement.dataset.journeyDir).toBe("back");
    expect(window.location.hash).toBe("#/bienvenue/banque");
    await journeyAt();
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Ta banque");
    expect(document.activeElement).toBe(host.querySelector("h1"));
    // Née dans une transition de vue, l'étape ne rejoue pas d'entrée CSS par-dessus (ni à la fin de la transition).
    expect(host.querySelector(".journey-body")?.className).toBe("journey-body");
  });

  it("a transition that never sees the new step is released by its safety timer", async () => {
    motion(false);
    window.location.hash = "#/bienvenue/banque";
    serve({ "/api/onboarding": () => ({ questions: [], answers: {}, suggestions: [] }), "/api/territory": () => ({ consents: { geocoding: false }, place: null }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    await click("Plus tard");
    let released = false;
    void started[0]!.done.then(() => { released = true; });
    await act(async () => { await new Promise((r) => setTimeout(r, 500)); });
    expect(released).toBe(true);
  });

  it("with reduced motion: no view transition, the focus moves at once, numbers are final straight away", async () => {
    motion(true);
    serve({ "/api/onboarding": () => ({ questions: [], answers: {}, suggestions: [] }), "/api/territory": () => ({ consents: { geocoding: false }, place: null }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    await click("Plus tard");
    expect(started).toHaveLength(0);
    expect(document.documentElement.classList.contains("is-view-transition")).toBe(false);
    await journeyAt();
    await settle();
    expect(document.activeElement).toBe(host.querySelector("h1"));
    expect(host.querySelector("h1")?.textContent).toBe("Ton foyer");
    expect(host.querySelector(".journey-body")?.classList.contains("is-forward")).toBe(true); // l'entrée CSS, coupée par le média

    await act(async () => root.unmount());
    root = createRoot(host);
    server.done = new Set(["banque", "foyer", "accords", "papier", "priorites"]);
    await render(<StartChecklist />);
    await settle();
    expect(host.querySelector(".start-card .ring-value")?.textContent).toBe("5/8");
  });

  it("without reduced motion, the checklist count climbs to its value", async () => {
    motion(false);
    server.status = "in_progress";
    server.done = new Set(["banque", "foyer", "accords", "papier", "priorites"]);
    serve();
    await render(<StartChecklist />);
    await settle();
    const value = () => host.querySelector(".start-card .ring-value")?.textContent;
    expect(value()).not.toBe("5/8"); // il compte encore
    await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
    expect(value()).toBe("5/8");
    expect(host.querySelector(".ring")?.getAttribute("aria-label")).toBe("5 éléments sur 8 faits"); // le nom ne compte pas
  });

  it("an item done while the page is open ticks itself; nothing ticks on load", async () => {
    server.status = "in_progress";
    server.done = new Set(["banque"]);
    serve();
    await render(<StartChecklist />);
    await settle();
    expect(host.querySelector(".is-fresh")).toBeNull();
    server.done.add("foyer");
    await act(async () => { window.dispatchEvent(new Event("bank:changed")); await new Promise((r) => setTimeout(r, 450)); });
    await settle();
    await click("Voir");
    expect([...host.querySelectorAll(".start-items li.is-fresh")].map((li) => li.textContent)).toEqual([expect.stringContaining("Décrire ton foyer")]);
  });
});

describe("tes accords", () => {
  it("groups the public consents by what they send, nothing pre-checked, nothing sent before the click", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/accords";
    serve();
    await journeyAt();
    await settle();
    expect([...host.querySelectorAll(".consent-group h3")].map((h) => h.textContent)).toEqual(["Rien te concernant", "Ta zone, pas ton adresse"]);
    const switches = [...host.querySelectorAll<HTMLInputElement>("input[role='switch']")];
    expect(switches).toHaveLength(6);
    expect(switches.every((s) => !s.checked)).toBe(true);
    // Chaque interrupteur est nommé par l'accord et décrit par sa ligne « Envoie : … ».
    const meteo = switches[5]!;
    expect(document.getElementById(meteo.getAttribute("aria-labelledby")!)?.textContent).toBe("Météo de ta commune");
    expect(document.getElementById(meteo.getAttribute("aria-describedby")!)?.textContent).toBe("Envoie : Ce qui part pour meteo.");
    expect(host.querySelectorAll(".consent-more")).toHaveLength(6); // texte complet replié
    expect(calls.some((c) => c.url.startsWith("/api/consents") || c.url === "/api/start/public-consents")).toBe(false);

    await click("Activer ces 6 accords");
    const sent = posted("/api/start/public-consents");
    expect(sent).toEqual([{ purposes: ["watch", "baremes", "tempo", "calendar", "home_value", "meteo"] }]);
    expect(calls.some((c) => c.url.startsWith("/api/consents/") && c.init?.method === "PUT")).toBe(false);
    await journeyAt();
    await settle();
    expect(host.querySelector(".journey-flash")?.textContent).toContain("6 accords donnés, modifiables dans Réglages › Confidentialité.");
    expect(window.location.hash).toBe("#/bienvenue/papier");
  });

  it("the count comes from the server: nothing new granted, no « 6 accords donnés »", async () => {
    server.status = "in_progress";
    server.consents = PUBLIC.map((c) => ({ ...c, granted: c.purpose !== "meteo", decided: true }));
    window.location.hash = "#/bienvenue/accords";
    serve();
    await journeyAt();
    await settle();
    await click("Activer ces 6 accords");
    await journeyAt();
    await settle();
    expect(host.querySelector(".journey-flash")?.textContent).toContain("1 accord donné,");
  });

  it("« Continuer avec ces choix » records each undecided switch as shown, the default-on watch included", async () => {
    server.status = "in_progress";
    server.consents = PUBLIC.map((c) => ({ ...c, granted: c.purpose === "watch" || c.purpose === "tempo", decided: c.purpose === "tempo" }));
    window.location.hash = "#/bienvenue/accords";
    serve({ "/api/consents/": () => ({}) });
    await journeyAt();
    await settle();
    await click("Continuer avec ces choix");
    const puts = calls.filter((c) => c.init?.method === "PUT").map((c) => [c.url.replace("/api/consents/", ""), JSON.parse(String(c.init?.body)).granted]);
    expect(puts).toEqual([["watch", true], ["baremes", false], ["calendar", false], ["home_value", false], ["meteo", false]]);
    expect(posted("/api/start/public-consents")).toEqual([]);
    await journeyAt();
    await settle();
    expect(host.querySelector(".journey-flash")?.textContent).toContain("2 accords donnés sur 6");
  });

  it("a single switch changes only that consent", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/accords";
    serve({ "/api/consents/tempo": () => ({ purpose: "tempo", granted: true }) });
    await journeyAt();
    await settle();
    await act(async () => { host.querySelectorAll<HTMLInputElement>("input[role='switch']")[2]!.click(); });
    await settle();
    const puts = calls.filter((c) => c.init?.method === "PUT");
    expect(puts.map((c) => [c.url, JSON.parse(String(c.init?.body))])).toEqual([["/api/consents/tempo", { granted: true }]]);
    expect(posted("/api/start/public-consents")).toEqual([]);
  });

  it("Réglages shows every group, with the same component", async () => {
    const rows = [
      { purpose: "baremes", title: "Barèmes", text: "t", granted: false, group: "public" as const, sends: "rien." },
      { purpose: "meteo", title: "Météo", text: "t", granted: false, group: "zone" as const, sends: "10 km." },
      { purpose: "geocoding", title: "Adresse", text: "t", granted: false, group: "adresse" as const, sends: "ton adresse." },
      { purpose: "ai_memory", title: "Mémoire", text: "t", granted: true, group: "contenus" as const, sends: "ce qu'il retient." },
    ];
    expect(groupConsents(rows, ["public", "zone"]).map((g) => g.group)).toEqual(["public", "zone"]);
    await render(<ConsentGroups consents={rows} busy={false} onToggle={() => undefined} />);
    expect([...host.querySelectorAll(".consent-group h3")].map((h) => h.textContent)).toEqual(["Rien te concernant", "Ta zone, pas ton adresse", "Ton adresse", "Tes contenus, vers un service tiers"]);
  });
});

describe("Bien démarrer", () => {
  it("shows the ring, the next three items with direct links, and the rest on demand", async () => {
    server.status = "in_progress";
    server.current = "prevenir";
    server.done = new Set(["banque", "foyer", "accords", "papier", "priorites"]);
    serve();
    await render(<StartChecklist />);
    await settle();
    expect(host.querySelector(".ring")?.getAttribute("aria-label")).toBe("5 éléments sur 8 faits");
    expect(host.querySelector(".ring-value")?.textContent).toBe("5/8");
    const links = () => [...host.querySelectorAll<HTMLAnchorElement>(".start-card > .start-items a")].map((a) => [a.textContent?.includes("fait") ? "fait" : "à faire", a.getAttribute("href")]);
    expect(links()).toEqual([["à faire", "#/bienvenue/prevenir"], ["à faire", "#/reglages?section=sec-title"], ["à faire", "#/reglages?section=backup-title"]]);
    expect(host.querySelector<HTMLAnchorElement>(".start-resume")?.getAttribute("href")).toBe("#/bienvenue/prevenir");
    await click("Voir 5 faits");
    expect(host.querySelectorAll(".start-items li.is-done")).toHaveLength(5);
    // « Pour aller plus loin » : replié, non compté.
    expect(host.querySelector<HTMLDetailsElement>(".start-more")?.open).toBe(false);
    expect(host.querySelector(".start-more a")?.getAttribute("href")).toBe("#/connecteurs?section=linky-title");
  });

  it("a finished list folds into a pill and does not celebrate on page load", async () => {
    server.status = "finished";
    server.done = new Set(ESSENTIALS.map(([k]) => k));
    server.completed = "2026-10-24T10:00:00+00:00";
    serve();
    await render(<StartChecklist />);
    await settle();
    expect(host.querySelector(".start-pill summary")?.textContent).toContain("Bien démarrer : terminé");
    expect(host.querySelector(".start-card")).toBeNull();
    expect(document.querySelector(".celebrate")).toBeNull();
  });

  it("celebrates when the ring closes after a real action, not before", async () => {
    server.status = "finished";
    server.done = new Set(ESSENTIALS.map(([k]) => k).filter((k) => k !== "sauvegarde"));
    serve();
    await render(<StartChecklist />);
    await settle();
    expect(document.querySelector(".celebrate")).toBeNull();
    // Ailleurs dans l'application, la restauration d'essai réussit : le serveur constate 8 sur 8.
    server.done.add("sauvegarde");
    server.completed = "2026-10-24T10:00:00+00:00";
    await act(async () => { window.dispatchEvent(new Event("bank:changed")); await new Promise((r) => setTimeout(r, 450)); });
    await settle();
    expect(document.querySelector(".celebrate")).not.toBeNull();
    expect(host.querySelector(".ring")).toBeNull(); // repliée en pilule
  });

  it("« Masquer » asks inline, then hides it for good", async () => {
    server.status = "in_progress";
    serve();
    await render(<StartChecklist />);
    await settle();
    await click("Masquer");
    expect(host.textContent).toContain("Tu la retrouveras dans Réglages.");
    expect(posted("/api/start/checklist")).toEqual([]);
    await act(async () => { host.querySelector<HTMLButtonElement>(".start-confirm .btn.quiet")!.click(); });
    await settle();
    expect(posted("/api/start/checklist")).toEqual([{ action: "dismiss" }]);
    expect(host.querySelector(".start-card")).toBeNull();
    await act(async () => root.unmount());
    root = createRoot(host);
    await render(<StartChecklist />);
    await settle();
    expect(host.querySelector(".start-card")).toBeNull();
  });
});

describe("où te prévenir", () => {
  afterEach(() => {
    push.supported = false;
    push.enable = null;
  });

  it("if the browser subscription fails, the push consent given by that click is taken back", async () => {
    push.supported = true;
    push.enable = () => Promise.reject(new Error("Permission refusée"));
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/prevenir";
    serve({
      "/api/push": () => ({ public_key: "k", consent: false, asked: false, devices: [], max: 5 }),
      "/api/consents/push": () => ({}),
      "/api/consents": () => [{ purpose: "push", title: "Notifications", text: "t", granted: false, sends: "Un message chiffré." }],
    });
    await journeyAt();
    await settle();
    await click("Accepter et activer les notifications");
    const puts = calls.filter((c) => c.url === "/api/consents/push" && c.init?.method === "PUT").map((c) => JSON.parse(String(c.init?.body)));
    expect(puts).toEqual([{ granted: true }, { granted: false }]);
    expect(host.textContent).toContain("Permission refusée");
    expect(window.location.hash).toBe("#/bienvenue/prevenir");
  });

  it("without usable notifications, « Plus tard » is the clear action and Messageries links back", async () => {
    server.status = "in_progress";
    window.location.hash = "#/bienvenue/prevenir";
    serve({ "/api/push": () => ({ public_key: "k", consent: false, asked: false, devices: [], max: 5 }), "/api/consents": () => [] });
    await journeyAt();
    await settle();
    expect([...host.querySelectorAll("button")].filter((b) => b.textContent?.trim().startsWith("Plus tard"))).toHaveLength(1);
    expect(button("Plus tard").classList.contains("ghost")).toBe(false);
    expect(host.querySelector<HTMLAnchorElement>("a[href^='#/messageries']")?.getAttribute("href")).toBe("#/messageries?from=bienvenue/prevenir");
  });
});

describe("« Voir … » de la liste", () => {
  it("never adds what is left to what is done", () => {
    expect(moreLabel(1, 4)).toBe("Voir 1 autre à faire · 4 faits");
    expect(moreLabel(0, 5)).toBe("Voir 5 faits");
    expect(moreLabel(2, 1)).toBe("Voir 2 autres à faire · 1 fait");
  });
});
