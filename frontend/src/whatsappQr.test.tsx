// WhatsApp par QR code : la carte de Messageries, rendue dans jsdom avec l'API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WhatsAppQrStatus } from "./api";
import { ToastProvider } from "./components/ui";
import { WhatsAppQr } from "./components/WhatsAppQr";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const QR = "data:image/png;base64,iVBORw0KGgo=";
type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let status: WhatsAppQrStatus;
let root: Root;
let host: HTMLDivElement;

function mockApi() {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    let body: unknown = {};
    if (url === "/api/channels/whatsapp_qr/status") body = status;
    else if (url === "/api/channels/whatsapp_qr/qr") body = { paired: false, qr: QR, refresh_in: 20 };
    else if (url === "/api/channels/whatsapp_qr/pairing") {
      status = { ...status, window: true };
      body = { expires_at: "2026-10-24T10:10:00Z" };
    } else if (url === "/api/channels/whatsapp_qr/logout") status = { ...status, paired: false, linked: false, phone: null };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
  });
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

async function typePassword(value: string) {
  const input = host.querySelector<HTMLInputElement>("input[type='password']")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { host.querySelector("form")!.requestSubmit(); });
  await flush();
}

beforeEach(() => {
  status = { bridge: true, error: null, paired: false, phone: null, linked: false, window: false };
  mockApi();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("WhatsApp par QR code", () => {
  it("asks the password, shows the QR, then celebrates once the phone is linked", async () => {
    const onLinked = vi.fn();
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={onLinked} /></ToastProvider>));
    await flush();
    expect(host.textContent).toContain("Afficher le QR code");
    expect(host.querySelector("img")).toBeNull();

    await typePassword("mon-mot-de-passe");
    expect(calls.find((c) => c.url.endsWith("/pairing"))).toMatchObject({ method: "POST", body: { password: "mon-mot-de-passe" } });
    await flush();
    const img = host.querySelector("img")!;
    expect(img.getAttribute("src")).toBe(QR);
    expect(host.textContent).toContain("Appareils connectés");
    expect(calls.every((c) => c.url.startsWith("/api/"))).toBe(true);

    status = { ...status, paired: true, linked: true, window: false, phone: "+33…78" };
    await wait(3100);
    expect(host.textContent).toContain("Relié : +33…78");
    expect(host.textContent).toContain("conversation « Moi »");
    expect(host.textContent).toContain("C'est relié");
    expect(onLinked).toHaveBeenCalledTimes(1);
  });

  it("unlinks with the password", async () => {
    status = { ...status, paired: true, linked: true, phone: "+33…78" };
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={() => {}} /></ToastProvider>));
    await flush();
    await typePassword("mon-mot-de-passe");
    expect(calls.find((c) => c.url.endsWith("/logout"))).toMatchObject({ method: "POST", body: { password: "mon-mot-de-passe" } });
    expect(host.textContent).toContain("Relier ton WhatsApp");
  });

  it("says when the bridge is unreachable", async () => {
    status = { ...status, bridge: false, error: "Passerelle WhatsApp injoignable : vérifie son adresse et qu'elle tourne" };
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={() => {}} /></ToastProvider>));
    await flush();
    expect(host.textContent).toContain("Passerelle injoignable");
    expect(host.querySelector("form")).toBeNull();
  });

  it("still lets you unlink when the bridge is unreachable", async () => {
    status = { ...status, bridge: false, linked: true, error: "Passerelle WhatsApp injoignable" };
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={() => {}} /></ToastProvider>));
    await flush();
    expect(host.textContent).toContain("Passerelle injoignable");
    await typePassword("mon-mot-de-passe");
    expect(calls.find((c) => c.url.endsWith("/logout"))).toMatchObject({ method: "POST", body: { password: "mon-mot-de-passe" } });
  });

  it("says the phone unlinked Bank, and offers to rescan or unlink", async () => {
    status = { ...status, paired: false, linked: true };
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={() => {}} /></ToastProvider>));
    await flush();
    expect(host.textContent).toContain("délié depuis ton téléphone");
    expect(host.textContent).toContain("Rescanner le QR code");
    expect(host.textContent).toContain("Délier");
    await typePassword("mon-mot-de-passe"); // Entrée : le premier bouton, rescanner
    expect(calls.find((c) => c.url.endsWith("/pairing"))).toMatchObject({ method: "POST", body: { password: "mon-mot-de-passe" } });
    expect(calls.find((c) => c.url.endsWith("/logout"))).toBeUndefined();
    await flush();
    expect(host.querySelector("img")?.getAttribute("src")).toBe(QR);
  });

  it("unlinks from the « phone unlinked » card with the Délier button", async () => {
    status = { ...status, paired: false, linked: true };
    await act(async () => root.render(<ToastProvider><WhatsAppQr onLinked={() => {}} /></ToastProvider>));
    await flush();
    const input = host.querySelector<HTMLInputElement>("input[type='password']")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "mon-mot-de-passe");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { host.querySelector<HTMLButtonElement>("button[name='unlink']")!.click(); });
    await flush();
    expect(calls.find((c) => c.url.endsWith("/logout"))).toMatchObject({ method: "POST", body: { password: "mon-mot-de-passe" } });
    expect(calls.find((c) => c.url.endsWith("/pairing"))).toBeUndefined();
  });
});
