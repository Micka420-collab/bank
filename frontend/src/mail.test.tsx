// Boîte mail en lecture seule : relier (mot de passe d'application + mot de passe Bank, expéditeurs un par ligne),
// l'état sans jamais afficher le mot de passe, puis les PDF proposés dans Papiers — importer ou écarter.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MailStatus } from "./api";
import { MailProposals } from "./components/MailProposals";
import { ToastProvider } from "./components/ui";
import { MailSettings } from "./pages/MailSettings";

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
  const field = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label));
  const input = field?.querySelector("input, textarea, select") as HTMLInputElement | HTMLTextAreaElement | undefined;
  if (!input) throw new Error(`champ introuvable : ${label}`);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

const EMPTY: MailStatus = { configured: false, consent: false, asked: false, account: null, proposals: [], recent: [], limits: { messages_per_scan: 40, attachment_bytes: 15_000_000, every_minutes: 55 } };
const LINKED: MailStatus = {
  ...EMPTY, configured: true, consent: true, asked: true,
  account: { host: "imap.free.fr", port: 993, security: "ssl", username_hint: "mi…@free.fr", folder: "INBOX", senders: ["caf.fr"], status: "active", last_sync_at: "2026-10-24T09:00:00+00:00", last_error: null, last_uid: 42 },
  proposals: [
    { id: 7, sender: "Caf du Loiret <ne-pas-repondre@caf.fr>", sender_domain: "caf.fr", subject: "Votre attestation de paiement", filename: "attestation.pdf", size: 48_000, kind_guess: "caf", kind_label: "Attestation CAF", confidence: 0.9, received_at: "2026-10-23T10:00:00+00:00", status: "proposed", document_id: null },
    { id: 8, sender: "Free Mobile <facture@free-mobile.fr>", sender_domain: "free-mobile.fr", subject: "Votre facture", filename: "Facture_Free.pdf", size: 120_000, kind_guess: "invoice", kind_label: "Facture", confidence: 0.7, received_at: "2026-10-22T10:00:00+00:00", status: "proposed", document_id: null },
  ],
};

function mockApi(state: { st: MailStatus }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/mail" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      if (body.password !== "bank-pass") return json({ detail: "Mot de passe incorrect" }, 403);
      state.st = { ...LINKED, proposals: [], consent: false, asked: false };
      return json(state.st);
    }
    if (url === "/api/mail/test") return json({ ok: true, messages: 12 });
    if (url === "/api/consents/mail") { state.st = { ...state.st, consent: true, asked: true }; return json({ granted: true }); }
    if (url === "/api/mail/proposals/7/import") { state.st = { ...state.st, proposals: state.st.proposals.filter((p) => p.id !== 7) }; return json({ ...state.st, document_ids: [31] }, 201); }
    if (url === "/api/mail/proposals/8/dismiss") { state.st = { ...state.st, proposals: state.st.proposals.filter((p) => p.id !== 8) }; return json(state.st); }
    if (url === "/api/mail") return json(state.st);
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

describe("boîte mail en lecture seule", () => {
  it("links a mailbox with an app password, never shows it back, then tests and consents", async () => {
    const state = { st: EMPTY };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><MailSettings /></ToastProvider>));
    await settle();
    expect(text()).toContain("Aucune boîte reliée.");
    await click("Relier ma boîte mail");
    await type("Serveur IMAP", "imap.free.fr");
    await type("Identifiant", "micka@free.fr");
    await type("Mot de passe d'application", "app-secret-xyz");
    await type("Expéditeurs retenus", "caf.fr\nfrancetravail.fr, impots.gouv.fr");
    await type("Ton mot de passe Bank", "mauvais");
    await click("Essayer et enregistrer");
    expect(text()).toContain("Mot de passe incorrect");
    await type("Ton mot de passe Bank", "bank-pass");
    await click("Essayer et enregistrer");
    const put = calls.filter((c) => c.url === "/api/mail" && c.init?.method === "PUT").pop();
    expect(JSON.parse(String(put?.init?.body))).toEqual({
      password: "bank-pass", host: "imap.free.fr", port: 993, security: "ssl", username: "micka@free.fr",
      mail_password: "app-secret-xyz", folder: "INBOX", senders: ["caf.fr", "francetravail.fr", "impots.gouv.fr"],
    });
    expect(text()).toContain("mi…@free.fr · imap.free.fr:993 (IMAPS) · dossier « INBOX »");
    expect(text()).not.toContain("app-secret-xyz");
    expect(text()).toContain("pas donné : Bank ne lit rien");
    expect(button("Lire maintenant").disabled).toBe(true);
    await click("Donner mon accord");
    expect(calls.some((c) => c.url === "/api/consents/mail")).toBe(true);
    expect(button("Lire maintenant").disabled).toBe(false);
    await click("Tester");
    expect(calls.some((c) => c.url === "/api/mail/test")).toBe(true);
    expect(text()).toContain("12 message(s)");
  });

  it("lists proposed PDFs in Papiers and imports or dismisses them", async () => {
    const state = { st: LINKED };
    mockApi(state);
    const imported = vi.fn();
    await act(async () => root.render(<ToastProvider><MailProposals onImported={imported} /></ToastProvider>));
    await settle();
    expect(text()).toContain("Proposés par ta boîte mail");
    expect(text()).toContain("2 PDF");
    expect(text()).toContain("attestation.pdf");
    expect(text()).toContain("Attestation CAF");
    expect(text()).toContain("Caf du Loiret <ne-pas-repondre@caf.fr> · Votre attestation de paiement");
    expect(text()).toContain("48 ko");
    await click("Importer");
    expect(calls.some((c) => c.url === "/api/mail/proposals/7/import" && c.init?.method === "POST")).toBe(true);
    expect(imported).toHaveBeenCalledTimes(1);
    expect(host.querySelector(".mail-list")?.textContent).not.toContain("attestation.pdf");
    await click("Écarter");
    expect(calls.some((c) => c.url === "/api/mail/proposals/8/dismiss")).toBe(true);
    expect(host.querySelector(".mail-proposals")).toBeNull();
  });

  it("stays invisible without a mailbox", async () => {
    mockApi({ st: EMPTY });
    await act(async () => root.render(<ToastProvider><MailProposals /></ToastProvider>));
    await settle();
    expect(host.querySelector(".mail-proposals")).toBeNull();
  });
});
