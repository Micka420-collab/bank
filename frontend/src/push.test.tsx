// Notifications sur ce téléphone : l'activation enchaîne accord, permission du navigateur, abonnement et
// enregistrement chez Bank (jamais l'adresse d'abonnement en local) ; cet appareil est reconnu ; tester et retirer.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "./components/ui";
import { Notifications } from "./pages/Settings";
import { fromBase64url, type PushStatus } from "./push";

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
const body = (url: string) => JSON.parse(String(calls.find((c) => c.url === url && c.init?.method && c.init.method !== "GET")?.init?.body ?? "{}"));

const SUBSCRIPTION = { endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: { p256dh: "BCVx", auth: "BTBZ" } };

function installBrowser(permission: NotificationPermission = "default") {
  const subscribe = vi.fn(async () => ({ toJSON: () => SUBSCRIPTION, unsubscribe: async () => true }));
  const getSubscription = vi.fn(async () => null);
  const reg = { pushManager: { subscribe, getSubscription } };
  vi.stubGlobal("navigator", { ...navigator, serviceWorker: { ready: Promise.resolve(reg) }, userAgent: "Mozilla/5.0 (Linux; Android 14) Chrome/130" });
  const requestPermission = vi.fn(async () => "granted" as NotificationPermission);
  vi.stubGlobal("Notification", { permission, requestPermission });
  vi.stubGlobal("PushManager", function PushManager() {});
  return { subscribe, requestPermission };
}

function mockApi(state: { st: PushStatus }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } }));
    if (url === "/api/consents/push") { state.st = { ...state.st, consent: true, asked: true }; return json({ granted: true }); }
    if (url === "/api/push/subscribe") {
      const device = { id: 5, label: JSON.parse(String(init?.body)).label, linked_at: "2026-10-22", last_notified_at: null, failures: 0, last_error: null };
      state.st = { ...state.st, devices: [device] };
      return json({ device, ...state.st }, 201);
    }
    if (url === "/api/push/5/test") return json({ ok: true });
    if (url === "/api/push/5" && init?.method === "DELETE") { state.st = { ...state.st, devices: [] }; return json(state.st); }
    if (url === "/api/push") return json(state.st);
    return json({}, 404);
  });
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  localStorage.clear();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("notifications sur ce téléphone", () => {
  it("decodes the server key and activates this device end to end, then tests and removes it", async () => {
    expect([...fromBase64url("AQID_-8")]).toEqual([1, 2, 3, 255, 239]);
    const { subscribe, requestPermission } = installBrowser();
    const state = { st: { public_key: "BCVx", consent: false, asked: false, devices: [], max: 8 } as PushStatus };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><Notifications /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("Activer sur cet appareil");
    await click("Activer sur cet appareil");
    expect(body("/api/consents/push")).toEqual({ granted: true });
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    const posted = body("/api/push/subscribe");
    expect(posted.subscription).toEqual(SUBSCRIPTION);
    expect(posted.label).toBe("Android · Chrome");
    expect(localStorage.getItem("bank:push-device")).toBe("5");
    expect(localStorage.getItem("bank:push-device")).not.toContain("fcm");
    expect(host.textContent).toContain("Activées sur cet appareil");
    expect(host.textContent).toContain("(cet appareil)");
    await click("Tester");
    expect(calls.some((c) => c.url === "/api/push/5/test" && c.init?.method === "POST")).toBe(true);
    await click("Retirer");
    expect(calls.some((c) => c.url === "/api/push/5" && c.init?.method === "DELETE")).toBe(true);
    expect(localStorage.getItem("bank:push-device")).toBeNull();
    expect(host.textContent).toContain("Activer sur cet appareil");
  });

  it("explains when the browser cannot or when notifications are blocked", async () => {
    vi.stubGlobal("PushManager", undefined);
    const state = { st: { public_key: "BCVx", consent: true, asked: true, devices: [], max: 8 } as PushStatus };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><Notifications /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("ne sait pas recevoir de notifications");
    await act(async () => root.unmount());
    root = createRoot(host);
    installBrowser("denied");
    await act(async () => root.render(<ToastProvider><Notifications /></ToastProvider>));
    await settle();
    expect(host.textContent).toContain("bloquées par le navigateur");
    expect(host.querySelector("button")).toBeNull();
  });
});
