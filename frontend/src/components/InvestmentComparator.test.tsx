// The investment comparator never pulls a made-up market return and exposes safety constraints.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InvestmentComparator } from "./InvestmentComparator";
import type { SalaryPowerView } from "./PurchasingPower";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const benchmark: SalaryPowerView = {
  available: false, comparison: null, note: "",
  benchmark: {
    as_of: "2026-09", published_on: "2026-10-15", annual_pct: 3.1,
    source: "Insee", url: "https://www.insee.fr/fr/statistiques/9080209",
    status: "definitif", stale: false, note: "",
  },
};
const comparison = {
  capital_cents: 100_000, horizon_months: 60, inflation_pct: 3.1,
  context: {
    risk_profile: "prudent", risk_capacity_limited: true, risk_reason: "Matelas incomplet",
    declared_horizon: "plus_10", reserve_gap_cents: 200_000,
    overdraft_risk: false, data_complete: true,
  },
  inventory: {
    holdings: [{ kind: "pea", label: "PEA connecté", value_cents: 150_000,
      source: "compte_enregistre", valuation: "solde_compte",
      document_status: "non_verifie", missing: ["frais_produit"] }],
    holdings_count: 1, total_cents: null,
    total_note: "Total non additionné : un avoir déclaré peut recouvrir un compte connecté.",
    source_note: "Soldes, pas des cotations en direct.",
    documents_note: "Aucun document vérifié.",
  },
  scenarios: [
    {
      kind: "livret_a", label: "Livret A", status: "taux_dated", gross_pct: 1.7,
      fees_pct: 0, tax_pct: 0, risk_level: "capital_protege_sous_conditions",
      rates_as_of: "2026-08-01", room_cents: 2_295_000, source: "Source officielle",
      warning: "Taux révisable", projection: {
        future_nominal_cents: 108_800, future_real_cents: 92_000,
        real_gain_cents: -8_000, net_gain_cents: 8_800,
        inflation_only_threshold_cents: 116_500, hypothetical_shock_value_cents: 100_000,
        hypothetical_shock_loss_cents: 0, effective_annual_real_pct: -1.5,
      }, suitability: { eligible_to_consider: true, blockers: [], cautions: [] },
    },
    {
      kind: "market", label: "Placement de marché (hypothèse utilisateur)",
      status: "hypothese", gross_pct: 5, fees_pct: 0.5, tax_pct: 30,
      risk_level: "perte_possible", rates_as_of: null, room_cents: null, source: "Saisie manuelle",
      warning: "Perte totale possible", projection: {
        future_nominal_cents: 120_000, future_real_cents: 104_000,
        real_gain_cents: 4_000, net_gain_cents: 20_000,
        inflation_only_threshold_cents: 116_500, hypothetical_shock_value_cents: 75_000,
        hypothetical_shock_loss_cents: 25_000, effective_annual_real_pct: 0.8,
      }, suitability: { eligible_to_consider: false,
        blockers: ["Constituer l'épargne de précaution."], cautions: [] },
    },
  ],
  note: "Pas de recommandation de marché.",
};

const response = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });

describe("comparateur de placements personnels", () => {
  let host: HTMLDivElement;
  let root: Root;
  let requests: { url: string; init?: RequestInit }[];
  const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    requests = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      requests.push({ url, init });
      return Promise.resolve(response(url.includes("/investments/compare") ? comparison : benchmark));
    });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it("loads dated CPI but never requests or displays a fabricated return until manual action", async () => {
    await act(async () => root.render(<InvestmentComparator />));
    await flush();
    expect(host.textContent).toContain("Repère Insee 2026-09");
    expect(requests).toHaveLength(1);
    expect(host.textContent).not.toContain("Placement de marché (hypothèse utilisateur)");
    expect(host.querySelectorAll(".investment-comparator-card")).toHaveLength(0);
  });

  it("prepares but does not silently submit a fresh observed holding amount", async () => {
    await act(async () => root.render(<InvestmentComparator suggestedCapitalCents={200_000} />));
    await flush();
    const capital = host.querySelector<HTMLInputElement>(".investment-comparator-form input")!;
    expect(capital.value).toBe("2000");
    expect(requests.filter(x => x.url.includes("/investments/compare"))).toHaveLength(0);
  });

  it("sends explicit scenarios and displays both gain and safety blockers", async () => {
    await act(async () => root.render(<InvestmentComparator />));
    await flush();
    await act(async () => {
      host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
    });
    await act(async () => {
      host.querySelector<HTMLFormElement>(".investment-comparator-form")!.requestSubmit();
    });
    await flush();
    const req = requests.find(x => x.url.includes("/investments/compare"))!;
    expect(req).toBeDefined();
    const sent = JSON.parse(String(req.init?.body));
    expect(sent.capital_cents).toBe(100_000);
    expect(sent.horizon_months).toBe(60);
    expect(sent.inflation_pct).toBe(3.1);
    expect(sent.custom_gross_pct).toBe(5);
    expect(sent.custom_loss_pct).toBe(25);
    expect(host.querySelectorAll(".investment-comparator-card")).toHaveLength(2);
    expect(host.textContent).toContain("Matelas à compléter");
    expect(host.textContent).toContain("Constituer l'épargne de précaution.");
    expect(host.textContent).toContain("Choc négatif isolé");
    expect(host.textContent).toContain("PEA connecté");
    expect(host.textContent).toContain("Documents non vérifiés");
    expect(host.textContent).toContain("Taux annuel à la date du 2026-08-01");
  });
});
