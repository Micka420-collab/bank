// Connecteurs › Comptes de l'État (#30, #32) : un bouton par organisme vers son espace officiel, avec son état ; les
// documents à récupérer (mail d'avis sans pièce jointe, attente de saison), « Je l'ai » et « Pas pour moi ».

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { labelAction } from "./audit-labels";
import { StateAccounts, bodyStatus, taskOrigin, type StateAccountsView } from "./components/StateAccounts";
import { ToastProvider } from "./components/ui";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let calls: { url: string; init?: RequestInit }[];
let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const text = () => (host.textContent ?? "").replace(/[  ]/g, " ");

const TASK = {
  id: 3, body: "impots", body_name: "Impôts (espace particulier)", origin: "mail" as const, expected: "avis d'impôt sur le revenu",
  subject: "Votre avis d'impôt est disponible", received_at: "2026-08-03T07:00:00+00:00", due: "2026-08-17", late: true,
  status: "open" as const, entry_url: "https://cfspart.impots.gouv.fr/", path: "Espace particulier › Documents",
  document_id: null, closed_at: null, self_closing: true,
};
const VIEW: StateAccountsView = {
  bodies: [
    { slug: "impots", name: "Impôts (espace particulier)", entry_url: "https://cfspart.impots.gouv.fr/", path: "Espace particulier › Documents",
      expected: "document de ton espace particulier", last_document: null, open_tasks: 1, status: "to_fetch" },
    { slug: "caf", name: "CAF", entry_url: "https://wwwd.caf.fr/wps/portal/caffr/login", path: "Mon Compte › Mes attestations",
      expected: "attestation de paiement", last_document: { title: "Attestation CAF 2025", day: "2025-06-01", read_at: "2025-06-01T08:00:00+00:00" },
      open_tasks: 0, status: "renew" },
  ],
  tasks: [TASK],
  recent: [],
  checked_on: "24 octobre 2026",
  note: "Bank ne garde aucun identifiant de ces comptes.",
};

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  calls = [];
  let st = VIEW;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = (v: unknown) => Promise.resolve(new Response(JSON.stringify(v), { status: 200, headers: { "content-type": "application/json" } }));
    if (url === "/api/state-accounts/tasks/3") {
      st = { ...VIEW, tasks: [], recent: [{ ...TASK, status: "done", closed_at: "2026-10-24T09:00:00+00:00" }] };
      return json(st);
    }
    return json(st);
  });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("Connecteurs › Comptes de l'État", () => {
  it("opens each official space and says what is due", async () => {
    await act(async () => { root.render(<ToastProvider><StateAccounts /></ToastProvider>); });
    await settle();
    const links = [...host.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toContain("https://cfspart.impots.gouv.fr/");
    expect(links).toContain("https://wwwd.caf.fr/wps/portal/caffr/login");
    expect(text()).toContain("1 à récupérer");
    expect(text()).toContain("à renouveler");
    expect(text()).toContain("Dernier document : Attestation CAF 2025, du 01/06/2025");
    expect(text()).toContain("avis d'impôt sur le revenu · Impôts (espace particulier) en retard");
    expect(text()).toContain("annoncé par mail le 03/08/2026 : « Votre avis d'impôt est disponible »");
    expect(text()).toContain("se ferme seul quand le PDF arrive dans Papiers");
    expect(text()).toContain("Pages d'entrée vérifiées le 24 octobre 2026");
    const ok = [...host.querySelectorAll("button")].find((b) => b.textContent === "Je l'ai")!;
    await act(async () => { ok.click(); });
    await settle();
    expect(JSON.parse(String(calls.find((c) => c.url === "/api/state-accounts/tasks/3")?.init?.body))).toEqual({ status: "done" });
    expect(text()).toContain("Récemment : avis d'impôt sur le revenu (reçu)");
  });

  it("words statuses, origins and journal entries", () => {
    expect(bodyStatus({ ...VIEW.bodies[0]!, status: "ok" }).text).toBe("à jour");
    expect(bodyStatus({ ...VIEW.bodies[0]!, status: "none" }).text).toBe("aucun document lu");
    expect(taskOrigin({ ...TASK, origin: "season" })).toBe("attente de saison");
    expect(labelAction("papers.task_done")).toBe("Documents à récupérer : arrivés dans Papiers");
  });
});
