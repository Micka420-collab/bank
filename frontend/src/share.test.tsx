// Partage d'un papier : bouton « Partager » du détail, lien montré une fois avec copie, journal et révocation ; page
// du destinataire (jeton dans le fragment, un seul téléchargement, lien refusé sans détail) — rendu réel dans jsdom.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VaultDetail, VaultShare } from "./api";
import { ToastProvider } from "./components/ui";
import { Documents } from "./pages/Documents";
import { Received, sharedToken } from "./pages/Received";

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

const detail = (over: Partial<VaultDetail> = {}): VaultDetail => ({
  id: 1, has_thumb: false, kind: "rent_receipt", kind_label: "Quittance de loyer", title: "Quittance — septembre 2026",
  status: "verified", error: null, sensitive: false, mime: "application/pdf", size: 24_000, pages: 1, source: "upload",
  issuer: null, document_date: "2026-09-30", expires_on: null, retention_until: null, created_at: "2026-10-21T10:00:00Z",
  verified_at: null, fields_count: 0, missing_count: 0, warnings_count: 0, filename: "quittance.pdf", method: "pdf_text",
  confidence: 0.98, fields: [], missing: [], warnings: [],
  retention: { label: "3 ans", title: "Service-Public.fr", url: "https://www.service-public.gouv.fr/", verified_on: "2026-10-13" },
  official: false, kinds: [], text_available: true, ...over,
});
const share = (over: Partial<VaultShare> = {}): VaultShare => ({
  id: 7, recipient: "Agence Dupont", created_at: "2026-10-21T10:00:00Z", expires_at: "2026-10-23T10:00:00Z",
  used_at: null, revoked_at: null, state: "active", ...over,
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
});

describe("partager un papier", () => {
  it("asks for the recipient, the duration and the password, shows the link once, then revokes it", async () => {
    let journal: VaultShare[] = [share({ id: 3, recipient: "CAF", state: "used", used_at: "2026-10-20T09:00:00Z" })];
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    mockApi({
      "/api/vault/1/shares/7/revoke": () => {
        journal = journal.map((s) => (s.id === 7 ? { ...s, state: "revoked" as const } : s));
        return journal[0];
      },
      "/api/vault/1/shares": (init) => {
        if (init?.method !== "POST") return { shares: journal };
        journal = [share(), ...journal];
        return { share: share(), link: "https://bank.example/#/recu/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG" };
      },
      "/api/vault/1": () => detail(),
    });
    await render(<Documents docId={1} />);
    await settle();
    expect(host.querySelector(".vault-share")).toBeNull();
    await click("Partager");
    await settle();
    expect(document.activeElement).toBe(host.querySelector(".vault-share-form input")); // à l'écran, prêt à écrire
    expect(host.textContent).toContain("Téléchargé le"); // le journal : ce qui a déjà été transmis
    expect(button("Créer le lien").disabled).toBe(true); // ni destinataire ni mot de passe
    const [recipient, password] = [
      host.querySelector<HTMLInputElement>(".vault-share-form input:not([type=password])")!,
      host.querySelector<HTMLInputElement>(".vault-share-form input[type=password]")!,
    ];
    await typeInto(recipient, "Agence Dupont");
    await typeInto(password, "secret");
    const select = host.querySelector<HTMLSelectElement>(".vault-share-form select")!;
    await act(async () => {
      select.value = "24";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => host.querySelector<HTMLFormElement>(".vault-share-form")!.requestSubmit());
    await settle();
    expect(JSON.parse(String(sent("/api/vault/1/shares", "POST")[0]!.init!.body))).toEqual({ recipient: "Agence Dupont", hours: 24, password: "secret" });
    const link = host.querySelector<HTMLInputElement>(".vault-share-link input")!;
    expect(link.value).toContain("#/recu/");
    expect(password.value).toBe(""); // le mot de passe ne reste pas dans le formulaire
    await click("Copier");
    expect(writeText).toHaveBeenCalledWith(link.value);
    await click("Révoquer");
    await settle();
    expect(sent("/api/vault/1/shares/7/revoke", "POST")).toHaveLength(1);
    expect(host.querySelector(".vault-share-link")).toBeNull(); // le lien révoqué n'est plus proposé
    expect(host.textContent).toContain("Révoqué");
    await click("Fermer le partage");
    expect(host.querySelector(".vault-share")).toBeNull();
  });

  it("cannot share a piece that was not kept", async () => {
    mockApi({ "/api/vault/1": () => detail({ status: "refused" }) });
    await render(<Documents docId={1} />);
    await settle();
    expect([...host.querySelectorAll("button")].some((b) => b.textContent?.includes("Partager"))).toBe(false);
  });
});

describe("page du destinataire", () => {
  it("reads the token from the fragment only", () => {
    expect(sharedToken("#/recu/abcdefghijklmnopqrstuvwxyz_-0123")).toBe("abcdefghijklmnopqrstuvwxyz_-0123");
    expect(sharedToken("#/recu/court")).toBeNull();
    expect(sharedToken("#/documents/1")).toBeNull();
    expect(sharedToken("#/recu/abcdefghijklmnopqrstuvwxyz?x=<script>")).toBeNull();
  });

  it("downloads once without cookies, then says the link is spent", async () => {
    mockApi({ "/api/share/open": () => new Response("%PDF-1.7", { status: 200, headers: { "content-type": "application/pdf" } }) });
    const createObjectURL = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    window.location.hash = "#/recu/jeton-de-test-assez-long-0123";
    await render(<Received token="jeton-de-test-assez-long-0123" />);
    await click("Télécharger le document");
    await settle();
    const call = sent("/api/share/open", "POST")[0]!;
    expect(JSON.parse(String(call.init!.body))).toEqual({ token: "jeton-de-test-assez-long-0123" });
    expect(call.init!.credentials).toBe("omit");
    expect(createObjectURL).toHaveBeenCalled();
    expect(host.querySelector("h1")?.textContent).toBe("Document reçu");
    expect(window.location.hash).not.toContain("jeton");
  });

  it("keeps the PDF for a second try until the page is closed", async () => {
    mockApi({ "/api/share/open": () => new Response("%PDF-1.7", { status: 200, headers: { "content-type": "application/pdf" } }) });
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL }));
    const attached: boolean[] = [];
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      attached.push(this.isConnected); // Safari iOS ignore le clic sur une ancre hors du document
    });
    try {
      await render(<Received token="jeton-de-test-assez-long-0123" />);
      await click("Télécharger le document");
      await settle();
      expect(attached).toEqual([true]);
      expect(document.querySelectorAll("a[hidden]")).toHaveLength(0); // retirée après le clic
      expect(revokeObjectURL).not.toHaveBeenCalled(); // le premier téléchargement a pu être ignoré
      const save = host.querySelector<HTMLAnchorElement>("a[download]")!;
      expect(save.getAttribute("href")).toBe("blob:x");
      expect(save.getAttribute("download")).toBe("document.pdf");
      expect(save.textContent).toContain("Enregistrer le PDF");
      await act(async () => root.unmount());
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:x"); // libéré quand la page se ferme
      root = createRoot(host);
    } finally {
      clickSpy.mockRestore();
    }
  });

  it("explains a spent, expired or revoked link the same way, and rate limits", async () => {
    mockApi({ "/api/share/open": () => new Response(JSON.stringify({ detail: "Ce lien n'est plus valable" }), { status: 404 }) });
    await render(<Received token="jeton-de-test-assez-long-0123" />);
    await click("Télécharger le document");
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Ce lien n'est plus valable");
    await act(async () => root.unmount());
    root = createRoot(host);
    mockApi({ "/api/share/open": () => new Response("", { status: 429 }) });
    await render(<Received token="jeton-de-test-assez-long-0123" />);
    await click("Télécharger le document");
    await settle();
    expect(host.querySelector("[role=alert]")?.textContent).toContain("réessayez dans quelques minutes");
  });
});
