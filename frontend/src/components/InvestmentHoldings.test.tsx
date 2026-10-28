// L'inventaire sépare solde connu, déclaration non vérifiée et cours périmé.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InvestmentHoldings } from "./InvestmentHoldings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const positions = {
  as_of: "2026-10-28",
  counts: { total: 3, unpriced_or_unverified: 2, potential_overlaps: 1 },
  note: "Inventaire sans total consolidé.",
  positions: [
    {
      key: "account:1", label: "PEA test", kind: "pea", origin: "compte",
      source_label: "Compte suivi", currency: "EUR", quantity: null,
      value_cents: 200_000, valuation_status: "solde_date", as_of: "2026-10-27",
      rate_pct: null, rate_as_of: null, rate_source: null, document_verified: false,
      required_facts: ["ISIN", "DIC", "Frais annuels"],
      potential_overlap: false, note: "Solde d'enveloppe, pas les titres.",
    },
    {
      key: "declared:0", label: "PEA déclaré", kind: "pea", origin: "declaration",
      source_label: "Déclaré", currency: "EUR", quantity: null,
      value_cents: 200_000, valuation_status: "valeur_declaree_non_verifiee", as_of: null,
      rate_pct: null, rate_as_of: null, rate_source: null, document_verified: false,
      required_facts: ["Frais", "DIC"], potential_overlap: true, note: "À vérifier.",
    },
    {
      key: "crypto:BTC", label: "BTC", kind: "crypto", origin: "crypto",
      source_label: "Portefeuille crypto", currency: "EUR", quantity: "1.2",
      value_cents: null, valuation_status: "cours_absent_ou_perime", as_of: "2026-10-01",
      rate_pct: null, rate_as_of: null, rate_source: null, document_verified: false,
      required_facts: ["Cotation datée"], potential_overlap: false, note: "Cours inconnu.",
    },
  ],
};
const res = (data: unknown): Response => new Response(JSON.stringify(data), {
  status: 200, headers: { "content-type": "application/json" },
});

describe("inventaire des placements réellement suivis", () => {
  let host: HTMLDivElement;
  let root: Root;
  let requests: string[];
  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    requests = [];
    vi.stubGlobal("fetch", (url: string) => {
      requests.push(url);
      return Promise.resolve(res(positions));
    });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });
  it("shows provenance, unknown crypto price and possible overlap instead of fake totals", async () => {
    await act(async () => root.render(<InvestmentHoldings />));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(requests).toHaveLength(1);
    expect(requests[0]).toContain("/api/watch/investments/holdings");
    expect(host.querySelectorAll(".investment-holdings-card")).toHaveLength(3);
    expect(host.textContent).toContain("Valorisation EUR inconnue");
    expect(host.textContent).toContain("Possiblement déjà compté");
    expect(host.textContent).toContain("Inventaire sans total consolidé");
    expect(host.querySelectorAll('a[href="#/documents"]')).toHaveLength(3);
    expect(host.textContent).toContain("Frais annuels");
    expect(host.textContent).not.toContain("Zéro euro");
  });
});
