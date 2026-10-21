// Coffre-fort : bibliothèque (dépôt, filtres, lecture en cours), détail (preuves, corrections, vérification,
// lecture assistée, chiffres officiels), formats d'affichage — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VaultDetail, VaultListing, VaultSummary } from "./api";
import { ToastProvider } from "./components/ui";
import { Documents, editValue, monthName, showValue } from "./pages/Documents";

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
const settle = async () => { for (let i = 0; i < 5; i++) await flush(); };
const render = (node: React.ReactNode) => act(async () => root.render(<ToastProvider>{node}</ToastProvider>));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label) || x.getAttribute("aria-label") === label);
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });
const typeInto = (el: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const sent = (url: string, method: string) => calls.filter((c) => c.url === url && c.init?.method === method);

const summary = (over: Partial<VaultSummary> = {}): VaultSummary => ({
  id: 1, has_thumb: true, kind: "payslip", kind_label: "Bulletin de paie", title: "Bulletin de paie — septembre 2026",
  status: "to_review", error: null, sensitive: false, mime: "image/jpeg", size: 124_000, pages: 1, source: "camera",
  issuer: null, document_date: "2026-09-30", expires_on: null, retention_until: null, created_at: "2026-10-21T10:00:00Z",
  verified_at: null, fields_count: 2, missing_count: 1, warnings_count: 0, ...over,
});

const detail = (over: Partial<VaultDetail> = {}): VaultDetail => ({
  ...summary(),
  filename: "IMG_2041.jpg",
  method: "ocr",
  confidence: 0.92,
  fields: [
    { key: "net_paye", label: "Net payé", type: "amount", value: 196_919, original: null, evidence: "NET PAYE EN EUROS 1 969,19", confidence: 0.9, source: "extraction" },
    { key: "pas_taux", label: "Taux du prélèvement à la source", type: "percent", value: "4.5", original: null, evidence: "Taux 4,5 %", confidence: 0.5, source: "extraction" },
  ],
  missing: [{ key: "employeur", label: "Employeur", type: "text" }],
  warnings: [],
  retention: { label: "Jusqu'à la liquidation de ta retraite", title: "Service-Public.fr", url: "https://www.service-public.gouv.fr/particuliers/vosdroits/F19134", verified_on: "2026-10-13" },
  official: false,
  kinds: [{ key: "payslip", label: "Bulletin de paie", sensitive: false }, { key: "identity", label: "Pièce d'identité", sensitive: true }],
  text_available: true,
  ...over,
});

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("affichage des valeurs", () => {
  it("formats each kind of value the French way and pre-fills corrections", () => {
    expect(showValue("amount", 196_919).replace(/\s/g, " ")).toBe("1 969,19 €");
    expect(showValue("date", "2026-09-30")).toBe("30 septembre 2026");
    expect(showValue("month", "2026-09")).toBe("septembre 2026");
    expect(showValue("percent", "4.5")).toBe("4,5 %");
    expect(editValue("amount", 196_919).replace(/\s/g, " ")).toBe("1 969,19");
    expect(editValue("date", "2026-09-30")).toBe("30/09/2026");
    expect(editValue("month", "2026-09")).toBe("09/2026");
    expect(monthName("2026-12")).toBe("décembre 2026");
  });
});

describe("bibliothèque du coffre", () => {
  it("uploads several files, reports what was set aside, and filters by type", async () => {
    let listing: VaultListing = {
      documents: [summary(), summary({ id: 2, kind: "invoice", kind_label: "Facture", title: "Facture EDF", has_thumb: false, mime: "application/pdf", expires_on: null })],
      counts: { payslip: 1, invoice: 1 }, kinds: [{ key: "payslip", label: "Bulletin de paie", sensitive: false }, { key: "invoice", label: "Facture", sensitive: false }],
      processing: false, ocr_available: true, total_bytes: 232_000,
    };
    mockApi({
      "/api/vault/upload": () => {
        listing = { ...listing, documents: [summary({ id: 3, status: "processing", title: "scan.pdf" }), ...listing.documents], processing: true };
        return { documents: [summary({ id: 3, status: "processing" })], skipped: [{ filename: "notes.txt", reason: "Format non pris en charge : PDF, JPEG, PNG ou WebP" }] };
      },
      "/api/vault": () => listing,
    });
    await render(<Documents docId={null} />);
    await settle();
    expect(host.querySelectorAll(".vault-tile")).toHaveLength(2);
    expect(host.textContent).toContain("2 documents attendent ta vérification");
    expect(host.querySelector(".vault-tile img")?.getAttribute("src")).toBe("/api/vault/1/thumb");
    expect(host.querySelectorAll(".vault-tile img")).toHaveLength(1); // pas de vignette pour un PDF texte : pas de 404
    const input = host.querySelector<HTMLInputElement>("input[multiple]")!;
    const files = [new File(["%PDF-1.4"], "scan.pdf", { type: "application/pdf" }), new File(["x"], "notes.txt")];
    Object.defineProperty(input, "files", { value: files, configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
    const upload = sent("/api/vault/upload", "POST")[0]!;
    const form = upload.init!.body as FormData;
    expect(form.getAll("files")).toHaveLength(2);
    expect(form.get("source")).toBe("upload");
    expect(host.textContent).toContain("notes.txt : Format non pris en charge");
    expect(host.textContent).toContain("Lecture…");
    await click("Facture");
    await settle();
    expect(calls.some((c) => c.url === "/api/vault?kind=invoice")).toBe(true);
  });

  it("warns about documents that expire soon", async () => {
    const soon = new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10);
    mockApi({
      "/api/vault": () => ({
        documents: [summary({ id: 7, kind: "identity", kind_label: "Pièce d'identité", title: "Carte d'identité", sensitive: true, expires_on: soon, status: "verified" })],
        counts: { identity: 1 }, kinds: [], processing: false, ocr_available: false, total_bytes: 1000,
      }),
    });
    await render(<Documents docId={null} />);
    await settle();
    expect(host.textContent).toContain("Carte d'identité expire le");
    expect(host.textContent).toContain("dans 10 jours");
    expect(host.querySelector(".vault-lock")).not.toBeNull();
    expect(host.textContent).toContain("Tesseract absent");
  });
});

describe("détail d'un document", () => {
  it("shows each value with its proof, lets it be corrected or added, then verified", async () => {
    let current = detail();
    mockApi({
      "/api/vault/1/fields/employeur": () => (current = detail({ fields: [...current.fields, { key: "employeur", label: "Employeur", type: "text", value: "ACME", original: null, evidence: null, confidence: 1, source: "user" }], missing: [] })),
      "/api/vault/1/fields/net_paye": () => (current = detail({ fields: [{ ...current.fields[0]!, value: 186_919, original: 196_919, source: "user", confidence: 1 }, current.fields[1]!] })),
      "/api/vault/1/verify": () => (current = { ...current, status: "verified" }),
      "/api/vault/1": () => current,
    });
    await render(<Documents docId={1} />);
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Bulletin de paie — septembre 2026");
    expect(host.querySelector(".vault-field q")?.textContent).toBe("NET PAYE EN EUROS 1 969,19");
    expect(host.querySelector(".vault-field.is-low")?.textContent).toContain("À vérifier"); // confiance basse signalée
    expect(host.textContent).toContain("Non trouvé");
    expect(host.textContent).toContain("Service-Public.fr");
    await click("Corriger : Net payé");
    const input = host.querySelector<HTMLInputElement>(".vault-field-edit input")!;
    expect(input.value.replace(/\s/g, " ")).toBe("1 969,19");
    await typeInto(input, "1 869,19");
    await act(async () => host.querySelector<HTMLFormElement>(".vault-field-edit")!.requestSubmit());
    await settle();
    expect(JSON.parse(String(sent("/api/vault/1/fields/net_paye", "PUT")[0]!.init!.body))).toEqual({ value: "1 869,19" });
    expect(host.textContent).toContain("Corrigé par toi · lu : 1");
    await click("Ajouter");
    await typeInto(host.querySelector<HTMLInputElement>(".vault-field-edit input")!, "ACME");
    await act(async () => host.querySelector<HTMLFormElement>(".vault-field-edit")!.requestSubmit());
    await settle();
    expect(host.textContent).toContain("Saisi par toi");
    await click("C'est bon");
    await settle();
    expect(sent("/api/vault/1/verify", "POST")).toHaveLength(1);
    expect(host.querySelector(".vault-head .vault-status")?.textContent).toContain("Vérifié");
  });

  it("runs the assisted reading and says what was set aside", async () => {
    let current = detail();
    mockApi({
      "/api/vault/1/assist": () => (current = { ...detail({ missing: [] }), assist: { added: ["employeur"], rejected: 2, cost_usd: 0.001 } }),
      "/api/vault/1": () => current,
    });
    await render(<Documents docId={1} />);
    await settle();
    await click("Lecture assistée");
    await settle();
    expect(host.textContent).toContain("1 valeur trouvée · 2 propositions écartées (sans citation exacte du document)");
  });

  it("feeds official figures, keeps sensitive pieces away from models, and deletes", async () => {
    let current = detail({ kind: "tax_notice", kind_label: "Avis d'impôt", official: true, missing: [] });
    mockApi({
      "/api/vault/1/apply": () => ({ kind: "avis_impot", period: "2025" }),
      "/api/vault/1": (init) => {
        if (init?.method === "PATCH") current = { ...current, sensitive: true };
        if (init?.method === "DELETE") return new Response(null, { status: 204 });
        return current;
      },
    });
    await render(<Documents docId={1} />);
    await settle();
    expect(host.textContent).not.toContain("Lecture assistée"); // rien ne manque : rien à demander
    await click("Utiliser ces chiffres dans Bank");
    await settle();
    expect(sent("/api/vault/1/apply", "POST")).toHaveLength(1);
    const box = [...host.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find((x) => x.closest("label")?.textContent?.includes("Pièce sensible"))!;
    await act(async () => box.click());
    await settle();
    expect(JSON.parse(String(sent("/api/vault/1", "PATCH")[0]!.init!.body))).toEqual({ sensitive: true });
    await click("Supprimer");
    await settle();
    expect(sent("/api/vault/1", "DELETE")).toHaveLength(1);
    expect(window.location.hash).toBe("#/documents");
  });

  it("explains a refused sensitive piece", async () => {
    mockApi({
      "/api/vault/5": () => detail({ id: 5, status: "refused", sensitive: true, kind: "identity", kind_label: "Pièce d'identité", title: "Pièce d'identité", error: "Pièce sensible : elle n'a pas été gardée.", fields: [], missing: [], has_thumb: false }),
    });
    await render(<Documents docId={5} />);
    await settle();
    expect(host.querySelector(".notice.danger")?.textContent).toContain("n'a pas été gardée");
    expect(host.textContent).not.toContain("Télécharger");
    expect(host.textContent).not.toContain("C'est bon");
  });
});
