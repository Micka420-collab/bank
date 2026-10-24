// Retraite : trois départs comparés en fourchette (âge légal, taux plein, 67 ans), le détail du départ choisi, un âge
// au choix au curseur, l'effet d'un versement PER, et le relevé de carrière saisi puis oublié. Sans date de
// naissance, Bank demande au lieu de deviner.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Retirement as RetirementData, RetirementDeparture } from "./api";
import { ageText, rangeText, Retirement, statusText } from "./components/Retirement";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const norm = (s: string) => s.replace(/[\u202f\u00a0\u2009]/g, " ");
const text = () => norm(host.textContent ?? "");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const type = async (label: string, value: string, blur = false) => {
  const field = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label));
  const input = (field?.control ?? field?.querySelector("input, textarea, select")) as HTMLInputElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (blur) input.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  });
  await settle();
};

function dep(over: Partial<RetirementDeparture>): RetirementDeparture {
  return {
    at: "2055-04-12", age_years: 64, age_months: 0, quarters_total: 163, quarters_required: 172, missing_quarters: 9,
    extra_quarters: 0, rate_percent: "44.375", status: "decote", base_monthly_low_cents: 98_124, base_monthly_high_cents: 98_124,
    complementary_monthly_low_cents: 21_000, complementary_monthly_high_cents: 26_000, total_low_cents: 119_124,
    total_high_cents: 124_124, points: "2345.60", replacement_percent: 58,
    notes: ["La complémentaire baisse aussi quand la base est minorée (coefficient d'anticipation Agirc-Arrco)."], ...over,
  };
}

const BASE: RetirementData = {
  profile: {},
  notes: ["Sans ton relevé de carrière, Bank compte les trimestres depuis l'année de tes 20 ans, 4 par an : saisis le vrai chiffre (info-retraite.fr)."],
  rules: { age: { title: "Âge légal et durée requise par génération", url: "https://www.service-public.fr/particuliers/vosdroits/F14043", checked: "2026-10-24" } },
  rules_checked: "2026-10-24",
  rules_recheck: false,
  disclaimer: "Un ordre de grandeur… Rien n'est envoyé à une caisse.",
  career: {
    birth_date: "1991-04-12", age_years: 35, quarters_acquired: 60, as_of: "2026-10-21", quarters_required: 172, legal_age_years: 64,
    legal_age_months: 0, gross_annual_cents: 3_302_400, salary_source: "salaire net des relevés, converti en brut (× 1,28)",
    best_years_average_cents: null, agirc_arrco_points: null, points_per_year: "101.42", working: true, quarter_salary_cents: 180_300,
  },
  departures: {
    legal: dep({}),
    full_rate: dep({ at: "2057-06-30", age_years: 66, age_months: 2, quarters_total: 172, missing_quarters: 0, rate_percent: "50.000", status: "full", base_monthly_low_cents: 116_667, base_monthly_high_cents: 116_667, total_low_cents: 142_000, total_high_cents: 149_000, notes: [] }),
    age_67: dep({ at: "2058-04-12", age_years: 67, age_months: 0, quarters_total: 175, missing_quarters: 0, extra_quarters: 3, rate_percent: "51.875", status: "surcote", total_low_cents: 150_000, total_high_cents: 158_000, notes: [] }),
  },
  per: { payment_cents: 165_120, ceiling_cents: 471_000, deductible_cents: 165_120, marginal_rate_percent: 11, tax_saved_cents: 18_163, effort_cents: 146_957, notes: ["L'économie d'impôt est une avance : la sortie sera imposée."], tax_source: "estimated", tax_detail: "estimée depuis ton salaire." },
};

function mockApi(initial: RetirementData) {
  calls = [];
  let state = initial;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/retirement/profile" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      state = { ...state, profile: body, notes: [], career: state.career && { ...state.career, quarters_acquired: body.quarters_acquired } };
      return json(state);
    }
    if (url === "/api/retirement/profile" && init?.method === "DELETE") {
      state = { ...initial };
      return json(state);
    }
    if (url.startsWith("/api/retirement")) {
      const params = new URL(url, "http://bank").searchParams;
      const out: RetirementData = { ...state, departures: { ...state.departures } };
      const age = params.get("age_years");
      if (age) out.departures.custom = dep({ at: `${1991 + Number(age)}-04-12`, age_years: Number(age), status: Number(age) < 64 ? "before_legal" : "decote", total_low_cents: 90_000, total_high_cents: 95_000, notes: ["Avant l'âge légal de ta génération (64 ans) : départ impossible hors carrière longue, handicap ou inaptitude."] });
      const per = params.get("per_cents");
      if (per && out.per) out.per = { ...out.per, payment_cents: Number(per), deductible_cents: Math.min(Number(per), 471_000), tax_saved_cents: Math.round(Math.min(Number(per), 471_000) * 0.11) };
      return json(out);
    }
    return json({}, 404);
  });
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("retraite", () => {
  it("formats ranges, ages and statuses", () => {
    expect(norm(rangeText(119_124, 124_124))).toBe("1 191 – 1 241 €");
    expect(norm(rangeText(145_833, 145_833))).toBe("1 458 €");
    expect(ageText(62, 6)).toBe("62 ans 6 mois");
    expect(statusText(dep({}))).toBe("décote · 9 trimestres manquants");
    expect(statusText(dep({ status: "surcote", extra_quarters: 1 }))).toBe("surcote · +1 trimestre");
    expect(statusText(dep({ status: "full" }))).toBe("taux plein");
  });

  it("compares the three departures, opens one, tries another age and a PER payment", async () => {
    mockApi(BASE);
    await act(async () => root.render(<ToastProvider><Retirement open /></ToastProvider>));
    await settle();
    expect(text()).toContain("Ta retraite");
    expect(text()).toContain("Génération 1991 : âge légal 64 ans, 172 trimestres pour le taux plein.");
    expect(text()).toContain("60 trimestres validés au 21 oct 2026 et un brut de 33 024 € par an");
    expect(text()).toContain("saisis le vrai chiffre (info-retraite.fr)");
    const rows = [...host.querySelectorAll(".rt-dep")];
    expect(rows.map((r) => r.getAttribute("data-key"))).toEqual(["legal", "full_rate", "age_67"]);
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["decote", "full", "surcote"]);
    // Le taux plein est ouvert d'office : détail base + complémentaire + taux + trimestres.
    expect(host.querySelector(".rt-dep.is-current")?.getAttribute("data-key")).toBe("full_rate");
    expect(text()).toContain("Pension de base1 167 €");
    expect(text()).toContain("Trimestres172 / 172");
    expect(text()).toContain("De ton net actuel≈ 58 %");
    await click("Âge légal");
    expect(host.querySelector(".rt-dep.is-current")?.getAttribute("data-key")).toBe("legal");
    expect(text()).toContain("décote · 9 trimestres manquants");
    expect(text()).toContain("Taux44,375 %");
    expect(text()).toContain("coefficient d'anticipation");
    // Les barres : une fourchette placée par rapport au départ le plus élevé.
    const bar = host.querySelector('.rt-dep[data-key="age_67"] .rt-range i') as HTMLElement;
    expect(bar.style.width).not.toBe("");
    expect(parseFloat(bar.style.left)).toBeGreaterThan(90);
    // Un autre âge : le curseur interroge Bank (après une courte pause) et ajoute « Ton choix ».
    await type("Et si je partais à", "62");
    expect(calls.some((c) => c.url.includes("age_years=62"))).toBe(false);
    await wait(300);
    await settle();
    expect(calls.some((c) => c.url === "/api/retirement?age_years=62")).toBe(true);
    expect(text()).toContain("Ton choix62 ans");
    expect(host.querySelector(".rt-dep.is-current")?.getAttribute("data-key")).toBe("custom");
    expect(text()).toContain("avant l'âge légal");
    expect(text()).toContain("départ impossible hors carrière longue");
    // PER : déductible, plafond, économie à la tranche connue, effort réel.
    expect(text()).toContain("Déductible : 1 651 € (plafond 4 710 €). À 11 % d'impôt, l'État en reprend 182 € : l'effort réel est de 1 470 €.");
    await type("Versement", "600", true);
    expect(calls.some((c) => c.url === "/api/retirement?age_years=62&per_cents=60000")).toBe(true);
    expect(text()).toContain("l'État en reprend 66 €");
    expect(text()).toContain("Tranche : estimée depuis ton salaire.");
    expect(text()).not.toContain("salaire..");
    expect(text()).toContain("vérifiées le 24 oct 2026");
    expect(text()).toContain("Rien n'est envoyé à une caisse.");
  });

  it("saves the career statement, then forgets it", async () => {
    mockApi(BASE);
    await act(async () => root.render(<ToastProvider><Retirement open /></ToastProvider>));
    await settle();
    await click("Saisir mon relevé de carrière");
    expect(text()).toContain("info-retraite.fr, rubrique « Ma carrière »");
    expect([...host.querySelectorAll("label")].some((l) => l.textContent?.startsWith("Date de naissance"))).toBe(false);
    await type("Trimestres validés", "48");
    await type("À la date du", "2026-06-30");
    await type("Salaire annuel moyen", "28000", true);
    await type("Points Agirc-Arrco", "1 250,5");
    await type("Année de début de carrière", "2013");
    await click("Enregistrer");
    const put = calls.filter((c) => c.url === "/api/retirement/profile" && c.init?.method === "PUT").pop();
    expect(JSON.parse(String(put?.init?.body))).toEqual({
      quarters_acquired: 48, as_of: "2026-06-30", best_years_average_cents: 2_800_000, agirc_arrco_points: "1 250,5",
      career_start_year: 2013, working: true, birth_date: null,
    });
    expect(text()).toContain("Relevé enregistré");
    expect(text()).toContain("48 trimestres validés");
    expect(text()).not.toContain("saisis le vrai chiffre");
    await click("Modifier mon relevé");
    await click("Oublier mon relevé");
    expect(calls.some((c) => c.url === "/api/retirement/profile" && c.init?.method === "DELETE")).toBe(true);
    expect(text()).toContain("60 trimestres validés");
  });

  it("shows one row when the full rate comes at the legal age", async () => {
    mockApi({ ...BASE, departures: { ...BASE.departures, full_rate: { ...BASE.departures.legal! } } });
    await act(async () => root.render(<ToastProvider><Retirement open /></ToastProvider>));
    await settle();
    const rows = [...host.querySelectorAll(".rt-dep")];
    expect(rows.map((r) => r.getAttribute("data-key"))).toEqual(["legal", "age_67"]);
    expect(text()).toContain("Âge légal · taux plein");
    expect(text()).toContain("Taux plein automatique67 ans");
    expect(host.querySelector(".rt-dep.is-current")?.getAttribute("data-key")).toBe("legal");
    await click("Taux plein automatique");
    expect(host.querySelector(".rt-dep.is-current")?.getAttribute("data-key")).toBe("age_67");
  });

  it("asks for the birth date instead of guessing", async () => {
    mockApi({ ...BASE, career: null, departures: {}, per: null, notes: ["Renseigne ta date de naissance (Ma situation) ou ici : la génération fixe l'âge légal et la durée."] });
    await act(async () => root.render(<ToastProvider><Retirement open /></ToastProvider>));
    await settle();
    expect(text()).toContain("Renseigne ta date de naissance");
    expect(host.querySelector(".rt-departures")).toBeNull();
    await click("Renseigner ma date de naissance et mon relevé");
    expect([...host.querySelectorAll("label")].some((l) => l.textContent?.startsWith("Date de naissance"))).toBe(true);
    expect(button("Replier").getAttribute("aria-expanded")).toBe("true");
  });
});
