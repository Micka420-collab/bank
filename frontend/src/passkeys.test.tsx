// Passkeys côté navigateur : les options du serveur sont converties en tampons, la réponse de l'authentificateur
// est renvoyée telle quelle (jamais une clé), le bouton n'apparaît que si le navigateur sait faire.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Login } from "./pages/Auth";
import { fromBase64url, loginWithPasskey, registerPasskey, supportsPasskeys, toBase64url } from "./webauthn";

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
    return Promise.resolve(new Response(JSON.stringify(handlers[key]!(init)), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };

function fakeCredential(kind: "create" | "get"): PublicKeyCredential {
  const bytes = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer;
  const response = kind === "create"
    ? { clientDataJSON: bytes("cd"), attestationObject: bytes("att"), getTransports: () => ["internal"] }
    : { clientDataJSON: bytes("cd"), authenticatorData: bytes("ad"), signature: bytes("sig"), userHandle: bytes("uh") };
  return { id: "Y3JlZA", rawId: bytes("cred"), type: "public-key", response, getClientExtensionResults: () => ({}) } as unknown as PublicKeyCredential;
}

function installCredentials(kind: "create" | "get") {
  const created: unknown[] = [];
  const fake = { create: vi.fn(async (o: unknown) => { created.push(o); return fakeCredential("create"); }), get: vi.fn(async (o: unknown) => { created.push(o); return fakeCredential("get"); }) };
  vi.stubGlobal("navigator", { ...navigator, credentials: fake });
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  void kind;
  return { fake, created };
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

describe("passkeys", () => {
  it("convertit base64url dans les deux sens", () => {
    const buf = fromBase64url("AQID_-8");
    expect([...new Uint8Array(buf)]).toEqual([1, 2, 3, 255, 239]);
    expect(toBase64url(buf)).toBe("AQID_-8");
    expect(toBase64url(null)).toBeNull();
  });

  it("enregistre : options converties en tampons, réponse renvoyée avec le mot de passe", async () => {
    const { fake, created } = installCredentials("create");
    mockApi({
      "/api/auth/passkeys/register/options": () => ({ options: { challenge: "AQID", rp: { id: "testserver", name: "Bank" }, user: { id: "AAEC", name: "micka", displayName: "micka" }, excludeCredentials: [{ id: "BAUG", type: "public-key" }], pubKeyCredParams: [] }, state: "st" }),
      "/api/auth/passkeys/register": () => ({ passkey: { id: 1, name: "iPhone", created_at: "2026-10-22", last_used_at: null, backed_up: true, transports: ["internal"] } }),
    });
    const out = await registerPasskey("iPhone", "pw");
    expect(out.name).toBe("iPhone");
    const opts = (created[0] as { publicKey: PublicKeyCredentialCreationOptions }).publicKey;
    expect(opts.challenge).toBeInstanceOf(ArrayBuffer);
    expect([...new Uint8Array(opts.user.id as ArrayBuffer)]).toEqual([0, 1, 2]);
    expect(opts.excludeCredentials?.[0]?.id).toBeInstanceOf(ArrayBuffer);
    expect(fake.create).toHaveBeenCalledTimes(1);
    const posted = body(calls.find((c) => c.url === "/api/auth/passkeys/register")!);
    expect(posted.state).toBe("st");
    expect(posted.password).toBe("pw");
    expect((posted.credential as { response: { transports: string[]; attestationObject: string } }).response.transports).toEqual(["internal"]);
    expect((posted.credential as { response: { attestationObject: string } }).response.attestationObject).toBe(toBase64url(new TextEncoder().encode("att").buffer as ArrayBuffer));
  });

  it("se connecte : le bouton n'existe que si le navigateur sait faire, et la réponse part telle quelle", async () => {
    mockApi({ "/api/auth/passkeys/login/options": () => ({ options: { challenge: "AQID", rpId: "testserver", allowCredentials: [] }, state: "st" }), "/api/auth/passkeys/login": () => ({ ok: true, username: "micka" }) });
    vi.stubGlobal("PublicKeyCredential", undefined);
    expect(supportsPasskeys()).toBe(false);
    await act(async () => root.render(<Login onDone={() => undefined} />));
    await settle();
    expect(host.textContent).not.toContain("Se connecter avec une passkey");
    await act(async () => root.unmount());
    root = createRoot(host);
    installCredentials("get");
    expect(supportsPasskeys()).toBe(true);
    const done = vi.fn();
    await act(async () => root.render(<Login onDone={done} />));
    await settle();
    const btn = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Se connecter avec une passkey"))!;
    expect(btn).toBeDefined();
    await act(async () => { btn.click(); });
    await settle();
    expect(done).toHaveBeenCalledTimes(1);
    const posted = body(calls.find((c) => c.url === "/api/auth/passkeys/login")!);
    expect(posted.state).toBe("st");
    const cred = posted.credential as { response: { signature: string; userHandle: string } };
    expect(cred.response.signature).toBe(toBase64url(new TextEncoder().encode("sig").buffer as ArrayBuffer));
    expect(cred.response.userHandle).toBe(toBase64url(new TextEncoder().encode("uh").buffer as ArrayBuffer));
    const r = await loginWithPasskey();
    expect(r.username).toBe("micka");
  });
});
