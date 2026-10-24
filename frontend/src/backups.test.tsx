// Réglages › Sauvegardes : l'état en une phrase, la dernière sauvegarde et la dernière restauration d'essai avec leur
// verdict, les deux gestes (sauvegarder, tester) qui rechargent l'état, l'échec dit en clair.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BackupRun, BackupStatus } from "./api";
import { ToastProvider } from "./components/ui";
import { Backups, bytesText } from "./pages/Settings";

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

const OK_BACKUP: BackupRun = { id: 1, kind: "backup", status: "ok", trigger: "manual", started_at: "2026-10-24T03:00:00+00:00", finished_at: "2026-10-24T03:00:02+00:00", details: { file: "bank-20261024-030000.bank", cipher_bytes: 2_400_000, tables: { users: 1, transactions: 812 }, copies: ["/mnt/usb/bank"] } };
const OK_VERIFY: BackupRun = { id: 2, kind: "verify", status: "ok", trigger: "auto", started_at: "2026-10-25T03:05:00+00:00", finished_at: null, details: { file: "bank-20261024-030000.bank", tables: { users: 1, transactions: 812 }, plain_bytes: 5_000_000 } };
const BAD_VERIFY: BackupRun = { id: 3, kind: "verify", status: "failed", trigger: "manual", started_at: "2026-10-26T10:00:00+00:00", finished_at: null, details: { file: "bank-20261024-030000.bank", tables: {}, problems: ["Sauvegarde altérée ou mauvaise clé"] } };

function status(over: Partial<BackupStatus> = {}): BackupStatus {
  return {
    enabled: true, level: "none", directory: "/data/backups", remote_directory: "/mnt/usb/bank", remote_reachable: true,
    hour: 3, keep: 14, verify_weekday: 6, next_backup: "2026-10-25T03:00:00+01:00", last_backup: null, last_ok_backup: null,
    last_verify: null, runs: [], files: [], remote_files: [],
    restore_hint: "Restaurer : python -m scripts.restore … avec la même clé maîtresse.",
    ...over,
  };
}

function mockApi(state: { st: BackupStatus }) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown, s = 200) => Promise.resolve(new Response(JSON.stringify(v), { status: s, headers: { "content-type": "application/json" } }));
    if (url === "/api/system/backups/run") { state.st = status({ level: "unverified", last_backup: OK_BACKUP, last_ok_backup: OK_BACKUP, runs: [OK_BACKUP], files: [{ name: OK_BACKUP.details.file!, bytes: 2_400_000, modified_at: OK_BACKUP.started_at, manifest: true }] }); return json({ ...state.st, run: OK_BACKUP }); }
    if (url === "/api/system/backups/verify") {
      const failing = state.st.level === "ok";
      state.st = status({ ...state.st, level: failing ? "failed" : "ok", last_verify: failing ? BAD_VERIFY : OK_VERIFY, runs: [failing ? BAD_VERIFY : OK_VERIFY, ...state.st.runs] });
      return json({ ...state.st, run: failing ? BAD_VERIFY : OK_VERIFY });
    }
    if (url === "/api/system/backups") return json(state.st);
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

describe("sauvegardes", () => {
  it("formats sizes for a person", () => {
    expect(bytesText(2_400_000)).toBe("2,4 Mo");
    expect(bytesText(52_000)).toBe("52 ko");
    expect(bytesText(1_500_000_000)).toBe("1,5 Go");
  });

  it("shows the state, runs a backup, tests the restore, and says plainly when it fails", async () => {
    const state = { st: status() };
    mockApi(state);
    await act(async () => root.render(<ToastProvider><Backups /></ToastProvider>));
    await settle();
    expect(text()).toContain("Aucune sauvegarde pour l'instant.");
    expect(text()).toContain("chaque jour à 3 h, 14 copies gardées, restauration d'essai le dimanche");
    expect(text()).toContain("/mnt/usb/bank");
    expect(button("Tester la restauration").disabled).toBe(true);
    await click("Sauvegarder maintenant");
    expect(calls.some((c) => c.url === "/api/system/backups/run" && c.init?.method === "POST")).toBe(true);
    expect(text()).toContain("Sauvegardée, mais la restauration d'essai n'a pas encore tourné.");
    expect(text()).toContain("bank-20261024-030000.bank · 2,4 Mo · copiée sur le second emplacement");
    expect(text()).toContain("1 fichier sur le serveur");
    await click("Tester la restauration");
    expect(text()).toContain("Sauvegardée et relue : tu peux dormir tranquille.");
    expect(text()).toContain("813 lignes relues, tout concorde");
    await click("Tester la restauration");
    expect(text()).toContain("Le dernier passage a échoué");
    expect(text()).toContain("Sauvegarde altérée ou mauvaise clé");
    expect(host.querySelector("section.backups")?.getAttribute("data-level")).toBe("failed");
    expect(text()).toContain("clé maîtresse");
  });
});
