// Plan B « perte de revenu » : trois scénarios en un écran, le chiffre qui compte en premier (combien de mois),
// les aides recalculées avant/après, la fourchette d'ARE qui renvoie au simulateur officiel, et des démarches
// qui préparent (jamais n'envoient) : un bouton vers le catalogue, un lien officiel, une case à cocher.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanB as PlanBData, PlanBScenario } from "./api";
import { PlanB, monthsText } from "./components/PlanB";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const text = () => (host.textContent ?? "").replace(/[\u202f\u00a0]/g, " ");

const RULE = { key: "are", title: "France Travail — montant de l'allocation", url: "https://www.francetravail.fr/faq/are", checked: "2026-10-22", detail: "" };
const SIM = { key: "are_simulator", title: "Simulateurs France Travail", url: "https://candidat.francetravail.fr/portail-simulateurs/", checked: "2026-10-22", detail: "" };

function scenario(key: PlanBScenario["key"], over: Partial<PlanBScenario> = {}): PlanBScenario {
  return {
    key,
    title: key === "stop" ? "Ton salaire s'arrête" : key === "drop" ? "Ton salaire baisse de 30 %" : "Fin de droits",
    lead: "Du jour au lendemain.",
    incomes_after: { salary_cents: 0, unemployment_cents: 0, pension_cents: 0, aids_cents: 25829, other_cents: 0, total_cents: 25829 },
    income_before_cents: 240829,
    income_after_cents: 25829,
    lost_cents: 215000,
    needs_cents: 155000,
    gap_cents: 129171,
    cash_cents: 544000,
    months: 4.2,
    runway_month: "2027-02",
    runway_label: "févr. 27",
    status: "tight",
    are: null,
    aids: [],
    aids_total_before_cents: 25829,
    aids_total_after_cents: 25829,
    aids_note: "Prestations supposées inchangées : renseigne ton foyer.",
    css: "",
    actions: [],
    notes: [],
    ...over,
  };
}

const PLAN: PlanBData = {
  today: "2026-10-21",
  drop_percent: 30,
  incomes: { salary_cents: 215000, unemployment_cents: 0, pension_cents: 0, aids_cents: 25829, other_cents: 0, total_cents: 240829 },
  needs: { fixed_cents: 90000, variable_cents: 60000, provision_cents: 5000, total_cents: 155000 },
  cash_cents: 544000,
  main_income: "salary",
  scenarios: [
    scenario("stop", {
      income_after_cents: 182693,
      gap_cents: -27693,
      months: null,
      runway_month: null,
      runway_label: null,
      status: "ok",
      are: { low_cents: 156864, high_cents: 206400, formula_cents: 156864, sjr_day_cents: 9041, floor_applied: false, rule: RULE, simulator: SIM, note: "Fourchette indicative." },
      aids: [
        { variable: "ppa", label: "Prime d'activité", before_cents: 25829, after_cents: 0, delta_cents: -25829 },
        { variable: "aide_logement", label: "Aide au logement", before_cents: 12000, after_cents: 23000, delta_cents: 11000 },
      ],
      aids_note: "Prestations recalculées par OpenFisca-France 176.1.2.",
      css: "participation",
      actions: [
        { key: "ft_register", title: "S'inscrire à France Travail dès le lendemain", why: "L'allocation court à partir de l'inscription.", when: "J+1", kind: "link", amount_cents: 156864, url: SIM.url, rule: RULE },
        { key: "css", title: "Demander la Complémentaire santé solidaire", why: "Elle remplace la mutuelle.", when: "semaine 1", kind: "procedure", amount_cents: 0, template: "demander_css" },
        { key: "landlord", title: "Prévenir le bailleur", why: "Un mot avant un retard.", when: "semaine 1", kind: "check", amount_cents: 0 },
      ],
      notes: ["L'ARE entre à sa part basse, par prudence."],
    }),
    scenario("drop", { income_after_cents: 176329, gap_cents: -21329, months: null, runway_month: null, runway_label: null, status: "ok" }),
    scenario("end_of_rights"),
  ],
  sources: [
    { key: "incomes", title: "Revenus : médiane des trois derniers mois", kind: "operations" },
    { ...RULE, kind: "rule" },
  ],
  rules_checked: "2026-10-22",
  rules_recheck: false,
  notes: [],
  disclaimer: "Un ordre de grandeur, pas une promesse. Bank n'envoie rien à un organisme.",
};

function mockApi() {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url.startsWith("/api/planb")) {
      const drop = Number(new URL(url, "http://x").searchParams.get("drop") ?? 30);
      return json({ ...PLAN, drop_percent: drop, scenarios: PLAN.scenarios.map((s) => (s.key === "drop" ? { ...s, title: `Ton salaire baisse de ${drop} %` } : s)) });
    }
    if (url === "/api/procedures" && init?.method === "POST") return json({ id: 7, template: { key: JSON.parse(String(init.body)).template } }, 201);
    return json({}, 404);
  });
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  window.location.hash = "";
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("plan B « perte de revenu »", () => {
  it("says how long you hold, in words a person reads", () => {
    expect(monthsText(scenario("end_of_rights"))).toBe("Tu tiens 4,2 mois — jusqu'à févr. 27");
    expect(monthsText(scenario("stop", { months: null, runway_label: null }))).toBe("Tu tiens sans entamer ton épargne");
    expect(monthsText(scenario("stop", { months: 0 }))).toBe("Ça ne passe pas dès le premier mois");
    expect(monthsText(scenario("stop", { months: 14.6, runway_label: "déc. 27" }))).toBe("Tu tiens 15 mois — jusqu'à déc. 27");
  });

  it("shows three scenarios, the aids before/after, the ARE range with the official simulator, and prepares procedures", async () => {
    mockApi();
    await act(async () => root.render(<ToastProvider><PlanB open /></ToastProvider>));
    await settle();
    expect(calls[0]?.url).toBe("/api/planb?drop=30");
    const tabs = [...host.querySelectorAll(".segmented button")].map((b) => b.textContent?.trim());
    expect(tabs).toEqual(["Ça s'arrête", "Baisse de 30 %", "Fin de droits"]);
    expect(host.querySelector(".pb-figure")?.textContent).toBe("Tu tiens sans entamer ton épargne");
    expect(text()).toContain("entre 1 569 € et 2 064 € brut par mois");
    const sim = [...host.querySelectorAll("a")].find((a) => a.textContent?.includes("simulateur de France Travail"));
    expect(sim?.getAttribute("href")).toBe(SIM.url);
    expect(sim?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(host.textContent).toContain("Prime d'activité");
    expect(text()).toContain("258 € → 0 € (-258 €)");
    expect(text()).toContain("120 € → 230 € (+110 €)");
    expect(host.textContent).toContain("OpenFisca-France 176.1.2");
    expect(host.textContent).toContain("avec participation");
    // Les démarches : lien officiel, démarche du catalogue (préparée, jamais envoyée), case à cocher.
    const official = [...host.querySelectorAll("a.btn")].find((a) => a.textContent?.includes("site officiel"));
    expect(official?.getAttribute("href")).toBe(SIM.url);
    expect(official?.getAttribute("target")).toBe("_blank");
    await click("Préparer la démarche");
    const posted = calls.find((c) => c.url === "/api/procedures" && c.init?.method === "POST");
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ template: "demander_css" });
    expect(window.location.hash).toBe("#/demarches/7");
    expect(host.textContent).not.toContain("envoyée");
    await click("À faire");
    expect(button("Fait").getAttribute("aria-pressed")).toBe("true");
    // Fin de droits : le chiffre qui compte, et le statut qui colore.
    await click("Fin de droits");
    expect(host.querySelector(".pb-figure")?.textContent).toBe("Tu tiens 4,2 mois — jusqu'à févr. 27");
    expect(host.querySelector(".pb-scenario")?.getAttribute("data-status")).toBe("tight");
    expect(text()).toContain("Il manque1 292 € / mois");
    expect(host.textContent).toContain("Prestations supposées inchangées");
    // La baisse se règle en un geste : la vue est rechargée avec le nouveau pourcentage.
    await click("Baisse de 30 %");
    await click("−50 %");
    expect(calls.some((c) => c.url === "/api/planb?drop=50")).toBe(true);
    expect([...host.querySelectorAll(".segmented button")][1]?.textContent?.trim()).toBe("Baisse de 50 %");
    // Les sources, datées.
    expect(host.textContent).toContain("vérifié le 2026-10-22");
    expect(host.textContent).toContain("n'envoie rien");
  });
});
