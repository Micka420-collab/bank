// Crédits : un prêt ajouté avec ses chiffres de contrat, le restant dû et la progression, la mensualité reconnue
// dans les relevés, puis les deux simulateurs (anticipé, renégociation) qui rendent un gain net et l'indemnité
// légale datée. Le taux est saisi en pourcentage à la française (« 3,50 ») et envoyé en points de base.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LoanView, LoansResponse } from "./api";
import { Loans, monthYear, rateText } from "./components/Loans";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const type = async (label: string, value: string) => {
  const input = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); // EuroInput lit le montant en quittant le champ (React écoute focusout)
  });
};

const LOAN: LoanView = {
  id: 3, name: "Prêt auto Cofidis", lender: "Cofidis", kind: "consommation", kind_label: "Crédit à la consommation",
  principal_cents: 900000, annual_rate_bp: 750, months: 60, first_payment: "2025-11-15", insurance_monthly_cents: 0, account_id: null,
  bank_remaining_cents: null, payment_cents: 18034, monthly_with_insurance_cents: 18034, total_interest_cents: 182040, total_insurance_cents: 0,
  total_cost_cents: 182040, end: "2030-10-15", paid_rows: 12, remaining_cents: 748000, paid_principal_cents: 152000, paid_interest_cents: 64408,
  paid_insurance_cents: 0, next_payment: "2026-11-15", next_payment_cents: 18034, months_left: 48, interest_left_cents: 117632, progress: 0.1689,
  rules_recheck: false, notes: [],
  matched: { merchant: "COFIDIS ECHEANCE PRET", amount_cents: 18000, last_date: "2026-10-15", next_date: "2026-11-15", late: false, occurrences: 4 },
};
const RULES = {
  ira_consommation: { title: "Crédit à la consommation : rien sous 10 000 €, puis 1 % ou 0,5 %", url: "https://www.service-public.fr/particuliers/vosdroits/F2440", checked: "2026-10-22" },
  ira_immobilier: { title: "Prêt immobilier : 6 mois d'intérêts / 3 %", url: "https://www.service-public.fr/particuliers/vosdroits/F1669", checked: "2026-10-22" },
};
const EMPTY: LoansResponse = { loans: [], total_remaining_cents: 0, total_monthly_cents: 0, credit_accounts: [{ id: 9, name: "Crédit Cofidis", balance_cents: -750000 }], kinds: [{ key: "consommation", label: "Crédit à la consommation" }, { key: "immobilier", label: "Prêt immobilier" }], rules: RULES, disclaimer: "Exact au centime pour un prêt classique." };

function mockApi(state: { st: LoansResponse }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/loans" && init?.method === "POST") { state.st = { ...state.st, loans: [LOAN], total_remaining_cents: 748000, total_monthly_cents: 18034 }; return json({ ...state.st, created_id: 3 }, 201); }
    if (url === "/api/loans") return json(state.st);
    if (url === "/api/loans/3/schedule") return json({ payment_cents: 18034, rows: [], yearly: [{ year: 2025, payments_cents: 36068, interest_cents: 11150, principal_cents: 24918, insurance_cents: 0, remaining_cents: 875082, months: 2 }], total_interest_cents: 182040, total_insurance_cents: 0 });
    if (url === "/api/loans/3/early") return json({ on: "2026-10-21", amount_cents: 300000, mode: "shorter", indemnity_cents: 0, indemnity_rule: RULES.ira_consommation, indemnity_explanation: "Aucune indemnité : moins de 10 000 € remboursés par anticipation sur douze mois.", remaining_before_cents: 748000, remaining_after_cents: 448000, old_payment_cents: 18034, new_payment_cents: 18034, old_months_left: 48, new_months_left: 27, interest_saved_cents: 61500, insurance_saved_cents: 0, net_gain_cents: 61500, new_end: "2029-01-15" });
    if (url === "/api/loans/3/renegotiate") return json({ on: "2026-10-21", old_rate_bp: 750, new_rate_bp: 400, remaining_cents: 748000, months_left: 48, old_payment_cents: 18034, new_payment_cents: 16889, old_interest_left_cents: 117632, new_interest_cents: 62700, fees_cents: 5000, indemnity_cents: 0, net_gain_cents: 49932, worth_it: true });
    return json({}, 404);
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

describe("crédits", () => {
  it("formats rates the French way", () => {
    expect(rateText(350).replace(/\u00a0/g, " ")).toBe("3,50 %");
    expect(rateText(0).replace(/\u00a0/g, " ")).toBe("0,00 %");
    expect(monthYear("2030-10-15")).toBe("oct. 2030");
  });

  it("adds a loan from its contract figures, shows what is left and matched, then simulates", async () => {
    const state = { st: EMPTY };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><Loans /></ToastProvider>));
    await settle();
    expect(text()).toContain("Aucun prêt suivi");
    await click("Ajouter un prêt");
    await type("Nom", "Prêt auto Cofidis");
    await type("Prêteur", "Cofidis");
    await type("Capital emprunté", "9000");
    await type("Taux nominal annuel", "7,50");
    await type("Durée", "60");
    await type("Première échéance", "2025-11-15");
    await click("Calculer et suivre");
    const posted = calls.find((c) => c.url === "/api/loans" && c.init?.method === "POST");
    const body = JSON.parse(String(posted?.init?.body));
    expect(body).toMatchObject({ name: "Prêt auto Cofidis", lender: "Cofidis", kind: "consommation", principal_cents: 900000, annual_rate_bp: 750, months: 60, first_payment: "2025-11-15", insurance_monthly_cents: 0, account_id: null });
    expect(text()).toContain("7 480 €");
    expect(text()).toContain("restant dû · 48 mois · fin oct. 2030");
    expect(host.querySelector(".loan-bar")?.getAttribute("aria-valuenow")).toBe("17");
    expect(text()).toContain("Mensualité reconnue dans tes relevés : COFIDIS ECHEANCE PRET");
    expect(text()).toContain("7 480 € restant dû · 180 € par mois");
    // Simulateurs : gain net et indemnité légale, année par année en dessous.
    await click("Détail et simulateurs");
    await type("Montant remboursé", "3000");
    await click("Simuler");
    expect(JSON.parse(String(calls.find((c) => c.url === "/api/loans/3/early")?.init?.body))).toEqual({ amount_cents: 300000, mode: "shorter" });
    expect(text()).toContain("615 € de gain net");
    expect(text()).toContain("fin janv. 2029 au lieu de oct. 2030");
    expect(text()).toContain("Aucune indemnité");
    expect([...host.querySelectorAll("a")].some((a) => a.getAttribute("href") === RULES.ira_consommation.url && a.getAttribute("rel") === "noopener noreferrer")).toBe(true);
    await click("Renégocier le taux");
    await type("Nouveau taux", "4");
    await click("Simuler");
    expect(JSON.parse(String(calls.find((c) => c.url === "/api/loans/3/renegotiate")?.init?.body))).toEqual({ new_rate_bp: 400, fees_cents: 0, buyout: false });
    expect(text()).toContain("499 € de gain net");
    expect(text()).toContain("168,89 € par mois au lieu de 180,34 €");
    expect(text()).toContain("Tableau d'amortissement, année par année");
    expect(text()).toContain("2025 (2 mois)");
  });
});
