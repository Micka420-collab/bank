// Import d'un export de plateforme : rendu réel dans jsdom, API simulée. Rien n'est noté sans aperçu ; une vente
// sans valeur du portefeuille attend la tienne ; les lignes écartées disent pourquoi.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CryptoImportEvent, CryptoImportPreview, CryptoView } from "./api";
import { ToastProvider } from "./components/ui";
import { PlatformImport } from "./pages/Crypto";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Call = { url: string; init?: RequestInit };
let calls: Call[];
let root: Root;
let host: HTMLDivElement;

function mockApi(handlers: Record<string, (init?: RequestInit) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(handlers).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
    const out = handlers[key]!(init);
    if (out instanceof Response) return Promise.resolve(out);
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label}`);
  return b;
};
const sent = (url: string) => calls.filter((c) => c.url === url && c.init?.method === "POST");

function event(over: Partial<CryptoImportEvent>): CryptoImportEvent {
  return {
    key: "a".repeat(64), line: 3, day: "2026-01-16", kind: "acquisition", asset: "BTC", quantity: "0.006", amount_cents: 30_000,
    fees_cents: 0, portfolio_value_cents: null, value_source: null, value_day: null, status: "new", issue: null, ...over,
  };
}
function preview(over: Partial<CryptoImportPreview> = {}): CryptoImportPreview {
  return {
    platform: "binance", platform_label: "Binance", format: "binance", rows: 13,
    counts: { acquisitions: 1, cessions: 0, to_complete: 1, duplicates: 0, ignored: 6, manual_matches: 0, excluded: 0 },
    events: [
      event({}),
      event({ key: "b".repeat(64), line: 6, day: "2026-05-20", kind: "cession", amount_cents: 14_000, fees_cents: 14, status: "to_complete", issue: "Pas de relevé du portefeuille ce jour-là : indique sa valeur globale juste avant la vente" }),
    ],
    truncated: false,
    reasons: [{ reason: "Échange crypto contre crypto : pas imposable (sursis d'imposition, art. 150 VH bis)", count: 1 }, { reason: "Récompense (staking, intérêts, airdrop…)", count: 1 }],
    ignored: [{ line: 9, reason: "Échange crypto contre crypto" }],
    years: [2026],
    ...over,
  };
}

async function render() {
  const imported: CryptoView[] = [];
  await act(async () => {
    root.render(<ToastProvider><PlatformImport onImported={(v) => imported.push(v)} /></ToastProvider>);
  });
  return imported;
}
async function choose(name: string) {
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { value: [new File(["User_ID,UTC_Time"], name, { type: "text/csv" })], configurable: true });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await settle();
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

describe("import d'un export de plateforme", () => {
  it("montre l'aperçu, demande la valeur d'une vente non suivie, puis importe avec elle", async () => {
    mockApi({
      "/api/crypto/import/preview": () => preview(),
      "/api/crypto/import": () => ({ result: { ...preview(), created: 2 }, view: { total_cents: 0 } }),
    });
    const imported = await render();
    await choose("binance.csv");
    expect(sent("/api/crypto/import/preview")).toHaveLength(1);
    expect(sent("/api/crypto/import")).toHaveLength(0);
    const text = host.textContent ?? "";
    expect(text).toContain("Binance : 13 lignes lues dans binance.csv");
    expect(text).toContain("À compléter1");
    expect(text).toContain("Pas de relevé du portefeuille");
    expect(text).toContain("Échange crypto contre crypto : pas imposable (sursis d'imposition, art. 150 VH bis) (1)");
    expect(button("Importer").textContent).toContain("Importer 1 opération");

    const value = host.querySelector<HTMLInputElement>('input[aria-label^="Valeur du portefeuille"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(value, "5000");
      value.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { value.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    expect(button("Importer").textContent).toContain("Importer 2 opérations");

    await act(async () => { button("Importer").click(); });
    await settle();
    const form = sent("/api/crypto/import")[0]!.init!.body as FormData;
    expect((form.get("file") as File).name).toBe("binance.csv");
    expect(JSON.parse(String(form.get("values")))).toEqual({ ["b".repeat(64)]: 500_000 });
    expect(imported).toHaveLength(1);
    expect(host.textContent).toContain("2 opérations notées");
    expect(host.querySelector(".cx-import-counts")).toBeNull();
  });

  it("ne propose rien quand tout est déjà là, et dit d'où vient la valeur du portefeuille", async () => {
    mockApi({
      "/api/crypto/import/preview": () => preview({
        counts: { acquisitions: 0, cessions: 0, to_complete: 0, duplicates: 2, ignored: 0, manual_matches: 0, excluded: 0 },
        events: [event({ status: "duplicate" }), event({ key: "c".repeat(64), kind: "cession", status: "duplicate", portfolio_value_cents: 2_000_000, value_source: "suivi" })],
        reasons: [],
      }),
    });
    await render();
    await choose("ledgers.csv");
    expect(host.textContent).toContain("déjà importée");
    expect(host.textContent).toMatch(/20\s000\s€ \(suivi\)/);
    expect(button("Rien de nouveau").disabled).toBe(true);
    await act(async () => { button("Annuler").click(); });
    expect(host.querySelector("table")).toBeNull();
  });

  it("pré-remplit la valeur du suivi sans l'imposer, et ne reprend une vente déjà notée que cochée", async () => {
    const tracked = event({ key: "c".repeat(64), day: "2026-07-01", kind: "cession", amount_cents: 30_000, portfolio_value_cents: 2_000_000, value_source: "suivi", value_day: "2026-06-30" });
    const twin = event({ key: "d".repeat(64), day: "2026-07-02", kind: "cession", amount_cents: 30_000, portfolio_value_cents: 2_000_000, value_source: "suivi", value_day: "2026-07-01", status: "manual_match", issue: "Déjà notée à la main (même jour, même montant)" });
    mockApi({
      "/api/crypto/import/preview": () => preview({
        counts: { acquisitions: 0, cessions: 1, to_complete: 0, duplicates: 0, ignored: 0, manual_matches: 1, excluded: 0 },
        events: [tracked, twin],
        reasons: [],
      }),
      "/api/crypto/import": () => ({ result: { ...preview(), created: 1, counts: { ...preview().counts, to_complete: 2 } }, view: { total_cents: 0 } }),
    });
    await render();
    await choose("ledgers.csv");
    expect(host.textContent).toContain("Relevé du suivi du 30/06/2026 : à vérifier");
    expect(host.textContent).toContain("déjà notée à la main");
    expect(button("Importer").textContent).toContain("Importer 1 opération");
    const inputs = () => [...host.querySelectorAll<HTMLInputElement>('input[aria-label^="Valeur du portefeuille"]')];
    expect(inputs()).toHaveLength(1);

    // Corrigée à la main : la valeur saisie part, plus celle du suivi.
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(inputs()[0], "25000");
      inputs()[0]!.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { inputs()[0]!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });

    const box = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    await act(async () => { box.click(); });
    expect(inputs()).toHaveLength(2);
    expect(button("Importer").textContent).toContain("Importer 2 opérations");

    await act(async () => { button("Importer").click(); });
    await settle();
    const form = sent("/api/crypto/import")[0]!.init!.body as FormData;
    expect(JSON.parse(String(form.get("values")))).toEqual({ ["c".repeat(64)]: 2_500_000, ["d".repeat(64)]: 2_000_000 });
    expect(JSON.parse(String(form.get("include")))).toEqual(["d".repeat(64)]);
    expect(host.textContent).toContain("2 ventes attendent leur valeur");
  });

  it("affiche le refus du serveur pour un format inconnu", async () => {
    mockApi({
      "/api/crypto/import/preview": () => new Response(JSON.stringify({ detail: "Format non reconnu : exporte le grand livre de Kraken" }), { status: 422, headers: { "content-type": "application/json" } }),
    });
    await render();
    await choose("releve.csv");
    expect(host.textContent).toContain("Format non reconnu");
    expect(host.querySelector(".cx-import-counts")).toBeNull();
  });
});
