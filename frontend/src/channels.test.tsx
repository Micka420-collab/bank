// Messageries : accord, tuiles, réglages (secret jamais réaffiché), code d'appairage avec lien direct, comptes
// reliés, alertes — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChannelPlatform, ChannelsView } from "./api";
import { ToastProvider } from "./components/ui";
import { Messageries } from "./pages/Messageries";

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
    if (out === undefined) return Promise.resolve(new Response(null, { status: 204 }));
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const render = () => act(async () => root.render(<ToastProvider><Messageries /></ToastProvider>));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = async (label: string) => { await act(async () => { button(label).click(); }); await settle(); };
const typeInto = (el: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const sent = (url: string, method: string) => calls.filter((c) => c.url === url && c.init?.method === method);
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;

function platform(key: string, label: string, over: Partial<ChannelPlatform> = {}): ChannelPlatform {
  return {
    key, label, inbound: key !== "ntfy", e2e: key === "signal",
    fields: key === "telegram"
      ? [{ key: "token", label: "Jeton du robot", secret: true, required: true, example: "123456789:AAH…", set: false, hint: null, value: null }]
      : [],
    configured: false, enabled: false, status: "off", bot_name: null, privacy: "redacted", last_error: null,
    last_ok_at: null, ignored: 0, links: [], extra: {}, ...over,
  };
}
function view(over: Partial<ChannelsView> = {}, tg: Partial<ChannelPlatform> = {}): ChannelsView {
  return {
    server_enabled: true, consent: true, pairing: { active: false, expires_at: null }, quiet_hours: [21, 8], weekly: true,
    platforms: [
      platform("telegram", "Telegram", tg),
      platform("whatsapp", "WhatsApp", {
        configured: true, enabled: true, status: "error", last_error: "WhatsApp : Jeton d'accès WhatsApp expiré ou révoqué",
        extra: { webhook_url: "https://bank.example/api/channels/whatsapp/webhook", verify_token: "verif-123", display_phone: "33612345678" },
      }),
      platform("signal", "Signal"), platform("discord", "Discord"), platform("slack", "Slack"),
      platform("ntfy", "ntfy", { configured: true, enabled: true, status: "online", extra: { subscribe_url: "https://ntfy.sh/bank-abc" },
                                  links: [{ id: 9, label: "Alertes ntfy", notify: true, linked_at: "2026-10-20T10:00:00+00:00", last_seen_at: null, last_notified_at: null, failures: 0, last_error: null }] }),
    ],
    ...over,
  };
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
  vi.useRealTimers();
});

describe("messageries", () => {
  it("asks for consent first, then lists every messaging app with its state", async () => {
    let consent = false;
    mockApi({
      "/api/channels": () => view({ consent }),
      "/api/consents/channels": () => { consent = true; return { purpose: "channels", granted: true }; },
      "/api/consents": () => [{ purpose: "channels", title: "Bank dans tes messageries", text: "Ce que Bank t'écrit passe par les serveurs de la messagerie.", granted: false, explicit: false, decided: false, updated_at: null, default: false }],
    });
    await render();
    await settle();
    expect(host.querySelector("h1")?.textContent).toBe("Messageries");
    expect(host.textContent).toContain("Ce que Bank t'écrit passe par les serveurs");
    await click("J'accepte");
    expect(body(sent("/api/consents/channels", "PUT")[0]!)).toEqual({ granted: true });
    expect(host.textContent).not.toContain("J'accepte");
    const tiles = [...host.querySelectorAll(".ch-tile")];
    expect(tiles.map((t) => t.querySelector(".ch-name")?.textContent)).toEqual(["Telegram", "WhatsApp", "Signal", "Discord", "Slack", "ntfy"]);
    expect(tiles[0]!.querySelector(".ch-status")?.textContent).toBe("À configurer");
    expect(tiles[1]!.querySelector(".ch-status")?.textContent).toBe("Erreur");
    expect(tiles[2]!.textContent).toContain("bout en bout");
    expect(tiles[5]!.textContent).toContain("1 relié");
    expect(host.textContent).toContain("/budget");
  });

  it("saves a token without ever showing it again, then pairs with a one-tap Telegram link", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let state: Partial<ChannelPlatform> = {};
    mockApi({
      "/api/channels/telegram": () => {
        state = {
          configured: true, enabled: true, status: "online", bot_name: "@bank_bot", extra: { username: "bank_bot" },
          fields: [{ key: "token", label: "Jeton du robot", secret: true, required: true, example: "123456789:AAH…", set: true, hint: "123456…", value: null }],
        };
        return view({}, state);
      },
      "/api/channels/pairing": () => ({ code: "ABCD-EFGH", raw: "ABCDEFGH", expires_at: new Date(Date.now() + 15 * 60_000).toISOString() }),
      "/api/channels": () => view({}, state),
    });
    await render();
    await settle();
    await click("TTelegram");
    expect(host.querySelector(".ch-guide")?.textContent).toContain("@BotFather");
    const input = host.querySelector<HTMLInputElement>("form[aria-label='Réglages Telegram'] input")!;
    expect(input.type).toBe("password");
    await typeInto(input, "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
    await act(async () => host.querySelector<HTMLFormElement>("form[aria-label='Réglages Telegram']")!.requestSubmit());
    await settle();
    expect(body(sent("/api/channels/telegram", "PUT")[0]!)).toEqual({
      values: { token: "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" }, privacy: "redacted", enabled: true,
    });
    const saved = host.querySelector<HTMLInputElement>("form[aria-label='Réglages Telegram'] input")!;
    expect(saved.value).toBe("");
    expect(host.textContent).toContain("Enregistré (123456…)");
    expect(host.querySelector(".ch-panel .ch-status")?.textContent).toBe("Connecté · @bank_bot");
    // Relier : mot de passe, code, lien direct, et l'écran voit arriver le compte tout seul.
    const pw = host.querySelector<HTMLInputElement>("form[aria-label='Relier mon compte Telegram'] input")!;
    await typeInto(pw, "une phrase de passe");
    await act(async () => host.querySelector<HTMLFormElement>("form[aria-label='Relier mon compte Telegram']")!.requestSubmit());
    await settle();
    expect(body(sent("/api/channels/pairing", "POST")[0]!)).toEqual({ password: "une phrase de passe" });
    expect(host.querySelector(".ch-code")?.textContent).toBe("ABCD-EFGH");
    const link = host.querySelector<HTMLAnchorElement>(".ch-pair a.btn")!;
    expect(link.href).toBe("https://t.me/bank_bot?start=ABCDEFGH");
    expect(link.rel).toContain("noopener");
    expect(host.querySelector(".ch-wait")?.textContent).toMatch(/En attente de ton message… code valable encore 1[45] min/);
    state = { ...state, links: [{ id: 3, label: "@micka", notify: true, linked_at: "2026-10-21T10:00:00+00:00", last_seen_at: "2026-10-21T10:00:00+00:00", last_notified_at: null, failures: 0, last_error: null }] };
    await act(async () => { vi.advanceTimersByTime(3100); });
    await settle();
    expect(host.querySelector(".ch-code")).toBeNull();
    expect(host.textContent).toContain("C'est relié !");
    expect(host.querySelector(".ch-links")?.textContent).toContain("@micka");
  });

  it("manages linked accounts and quiet hours", async () => {
    mockApi({
      "/api/channels/links/9/test": () => ({ ok: true }),
      "/api/channels/links/9": () => ({ id: 9, notify: false }),
      "/api/channels/preferences": () => ({ ok: true }),
      "/api/channels": () => view(),
    });
    await render();
    await settle();
    await click("nntfy");
    expect(host.textContent).toContain("https://ntfy.sh/bank-abc");
    await act(async () => { host.querySelector<HTMLInputElement>(".ch-links input[type=checkbox]")!.click(); });
    await settle();
    expect(body(sent("/api/channels/links/9", "PATCH")[0]!)).toEqual({ notify: false });
    await click("Envoyer un essai");
    expect(sent("/api/channels/links/9/test", "POST")).toHaveLength(1);
    expect(host.textContent).toContain("Essai envoyé");
    expect(host.querySelector(".ch-links")?.textContent).not.toContain("Délier"); // ntfy : rien à délier, juste à retirer
    const [from] = [...host.querySelectorAll<HTMLSelectElement>("section[aria-labelledby='ch-alerts'] select")];
    await act(async () => {
      from!.value = "22";
      from!.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(body(sent("/api/channels/preferences", "PUT")[0]!)).toEqual({ quiet_hours: [22, 8] });
  });

  it("shows WhatsApp's webhook, verify token, error and the 24-hour rule", async () => {
    mockApi({ "/api/channels": () => view() });
    await render();
    await settle();
    await click("WWhatsApp");
    const panel = host.querySelector(".ch-panel")!;
    expect(panel.textContent).toContain("https://bank.example/api/channels/whatsapp/webhook");
    expect(panel.textContent).toContain("verif-123");
    expect(panel.textContent).toContain("24 h");
    expect(panel.querySelector("[role=alert]")?.textContent).toContain("expiré ou révoqué");
  });

  it("explains when the server has messaging turned off", async () => {
    mockApi({ "/api/channels": () => view({ server_enabled: false }) });
    await render();
    await settle();
    expect(host.textContent).toContain("BANK_CHANNELS_ENABLED");
    expect(host.querySelector(".ch-tile")).toBeNull();
  });
});
