// Passkeys côté navigateur : convertir les options du serveur (base64url) en tampons pour navigator.credentials,
// et la réponse de l'authentificateur en JSON pour le serveur. Aucune clé ne passe par ici : le navigateur et
// l'authentificateur gardent le secret, Bank ne voit que la clé publique et des signatures.

import { api } from "./api";

export function supportsPasskeys(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential === "function" && !!navigator.credentials;
}

export function fromBase64url(text: string): ArrayBuffer {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

export function toBase64url(buf: ArrayBuffer | null | undefined): string | null {
  if (!buf) return null;
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type ServerCreationOptions = Record<string, unknown> & {
  challenge: string;
  user: { id: string; name: string; displayName: string };
  excludeCredentials?: { id: string; type: string; transports?: string[] }[];
};
type ServerRequestOptions = Record<string, unknown> & {
  challenge: string;
  allowCredentials?: { id: string; type: string; transports?: string[] }[];
};

function creationOptions(o: ServerCreationOptions): PublicKeyCredentialCreationOptions {
  return {
    ...(o as unknown as PublicKeyCredentialCreationOptions),
    challenge: fromBase64url(o.challenge),
    user: { ...o.user, id: fromBase64url(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c) => ({ ...c, id: fromBase64url(c.id), type: "public-key" as const, transports: c.transports as AuthenticatorTransport[] | undefined })),
  };
}

function requestOptions(o: ServerRequestOptions): PublicKeyCredentialRequestOptions {
  return {
    ...(o as unknown as PublicKeyCredentialRequestOptions),
    challenge: fromBase64url(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c) => ({ ...c, id: fromBase64url(c.id), type: "public-key" as const, transports: c.transports as AuthenticatorTransport[] | undefined })),
  };
}

function serializeCreation(cred: PublicKeyCredential): Record<string, unknown> {
  const r = cred.response as AuthenticatorAttestationResponse;
  const transports = typeof r.getTransports === "function" ? r.getTransports() : [];
  return {
    id: cred.id,
    rawId: toBase64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: (cred as PublicKeyCredential & { authenticatorAttachment?: string }).authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: { clientDataJSON: toBase64url(r.clientDataJSON), attestationObject: toBase64url(r.attestationObject), transports },
  };
}

function serializeAssertion(cred: PublicKeyCredential): Record<string, unknown> {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toBase64url(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: {
      clientDataJSON: toBase64url(r.clientDataJSON),
      authenticatorData: toBase64url(r.authenticatorData),
      signature: toBase64url(r.signature),
      userHandle: toBase64url(r.userHandle),
    },
  };
}

export type PasskeyView = { id: number; name: string; created_at: string; last_used_at: string | null; backed_up: boolean; transports: string[] };

/** Ajouter une passkey : options du serveur → authentificateur → vérification (mot de passe exigé). */
export async function registerPasskey(name: string, password: string): Promise<PasskeyView> {
  const { options, state } = await api<{ options: ServerCreationOptions; state: string }>("/api/auth/passkeys/register/options", { method: "POST" });
  const cred = (await navigator.credentials.create({ publicKey: creationOptions(options) })) as PublicKeyCredential | null;
  if (!cred) throw new Error("Aucune passkey créée.");
  const r = await api<{ passkey: PasskeyView }>("/api/auth/passkeys/register", {
    method: "POST",
    body: { state, credential: serializeCreation(cred), name, password },
  });
  return r.passkey;
}

/** Se connecter : défi du serveur → authentificateur (passkeys découvrables) → session. */
export async function loginWithPasskey(): Promise<{ username: string }> {
  const { options, state } = await api<{ options: ServerRequestOptions; state: string }>("/api/auth/passkeys/login/options", { method: "POST" });
  const cred = (await navigator.credentials.get({ publicKey: requestOptions(options) })) as PublicKeyCredential | null;
  if (!cred) throw new Error("Connexion annulée.");
  return api<{ ok: boolean; username: string }>("/api/auth/passkeys/login", { method: "POST", body: { state, credential: serializeAssertion(cred) } });
}
