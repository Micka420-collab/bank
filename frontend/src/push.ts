// Notifications sur ce téléphone (Web Push) : demander la permission, s'abonner auprès du navigateur avec la clé
// publique du serveur, enregistrer l'abonnement chez Bank. L'appareil courant est reconnu par l'identifiant que
// Bank lui a donné, gardé en local (jamais l'adresse d'abonnement).

import { api } from "./api";

export type PushDevice = { id: number; label: string; linked_at: string; last_notified_at: string | null; failures: number; last_error: string | null };
export type PushStatus = { public_key: string; consent: boolean; asked: boolean; devices: PushDevice[]; max: number };

const DEVICE_KEY = "bank:push-device";

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export function pushPermission(): NotificationPermission | "unsupported" {
  return pushSupported() ? Notification.permission : "unsupported";
}

export function fromBase64url(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function thisDeviceId(): number | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

function rememberDevice(id: number | null): void {
  try {
    if (id === null) localStorage.removeItem(DEVICE_KEY);
    else localStorage.setItem(DEVICE_KEY, String(id));
  } catch {
    /* stockage indisponible : l'appareil sera simplement listé sans être reconnu */
  }
}

export function deviceLabel(): string {
  const ua = navigator.userAgent;
  const os = /iPhone|iPad/.test(ua) ? "iPhone" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "Appareil";
  const browser = /Firefox/.test(ua) ? "Firefox" : /Edg\//.test(ua) ? "Edge" : /Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "navigateur";
  return `${os} · ${browser}`;
}

/** Active les notifications sur cet appareil : permission, abonnement navigateur, enregistrement chez Bank. */
export async function enablePush(publicKey: string): Promise<PushDevice> {
  if (!pushSupported()) throw new Error("Ce navigateur ne sait pas afficher de notifications.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notifications refusées par le navigateur : autorise-les dans ses réglages.");
  const reg = await navigator.serviceWorker.ready;
  const existing = await reg.pushManager.getSubscription();
  const sub = existing ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: fromBase64url(publicKey) as BufferSource }));
  const r = await api<{ device: PushDevice }>("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON(), label: deviceLabel() } });
  rememberDevice(r.device.id);
  return r.device;
}

/** Coupe les notifications sur cet appareil : côté Bank, puis côté navigateur. */
export async function disablePush(deviceId: number): Promise<void> {
  await api(`/api/push/${deviceId}`, { method: "DELETE" });
  if (thisDeviceId() === deviceId) {
    rememberDevice(null);
    if (pushSupported()) {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      await sub?.unsubscribe().catch(() => false);
    }
  }
}
