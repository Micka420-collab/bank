// « Verre lumineux », partie expérience : moments de fête (déclenchés seulement par un geste réussi), anneau de
// progression, états vides illustrés, salutation du jour, étapes de démarche — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Category, Transaction } from "./api";
import { PARTICLES } from "./components/Celebration";
import { EmptyState } from "./components/EmptyState";
import { Ring, ringPercent } from "./components/Ring";
import { ToastProvider, useCelebrate, useToast } from "./components/ui";
import { Operations, toSort } from "./pages/Operations";
import { rowPhase } from "./pages/Procedures";
import { greeting } from "./pages/Today";

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
    return Promise.resolve(new Response(JSON.stringify(out), { status: 200, headers: { "content-type": "application/json" } }));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
const render = (node: React.ReactNode) => act(async () => root.render(<ToastProvider>{node}</ToastProvider>));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label));
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });

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

function Trigger({ kind }: { kind: "coin" | "done" | "plain" }) {
  const celebrate = useCelebrate();
  const toast = useToast();
  return (
    <button type="button" onClick={() => (kind === "plain" ? toast("Enregistré.") : celebrate(kind, kind === "coin" ? "+5 € dans ta tirelire !" : "Dossier validé."))}>
      Go
    </button>
  );
}

describe("moments de fête", () => {
  it("announces the text in the live toast and bursts silently beside it", async () => {
    await render(<Trigger kind="coin" />);
    expect(host.querySelector(".celebrate")).toBeNull(); // rien au chargement
    await click("Go");
    const status = host.querySelector('[role="status"]')!;
    expect(status.querySelector(".toast.is-coin")?.textContent).toBe("+5 € dans ta tirelire !");
    const party = host.querySelector(".celebrate.is-coin")!;
    expect(party.getAttribute("aria-hidden")).toBe("true");
    expect(status.contains(party)).toBe(false); // la gerbe n'est pas relue par les lecteurs d'écran
    expect(party.querySelectorAll(".celebrate-burst > i")).toHaveLength(PARTICLES);
    expect(party.querySelector(".celebrate-glow")).not.toBeNull();
  });

  it("uses the success toast for a finished chore, and plain toasts never celebrate", async () => {
    await render(<><Trigger kind="done" /><Trigger kind="plain" /></>);
    const [done, plain] = [...host.querySelectorAll("button")];
    await act(async () => { done!.click(); });
    expect(host.querySelector(".toast.is-success")?.textContent).toBe("Dossier validé.");
    expect(host.querySelector(".celebrate.is-done")).not.toBeNull();
    await act(async () => { plain!.click(); });
    expect(host.querySelector(".toast")?.className).toBe("toast");
    expect(host.querySelector(".celebrate")).toBeNull();
  });

  it("celebrates only when the last operation to sort on the page gets its category", async () => {
    const txn = (id: number, merchant: string, category: string | null, over: Partial<Transaction> = {}): Transaction => ({
      id, account_id: 1, booking_date: "2026-10-20", amount_cents: -1_250, currency: "EUR", label: merchant.toUpperCase(), merchant,
      category, category_source: "rule", is_transfer: false, aid_kind: null, note: null, ...over,
    });
    let items: Transaction[] = [
      txn(1, "Boulangerie", null),
      txn(2, "Pharmacie", "a_categoriser"),
      txn(3, "Carrefour", "courses"),
      txn(4, "Virement", null, { is_transfer: true }),
    ];
    const categories: Category[] = [
      { slug: "a_categoriser", name: "À classer" },
      { slug: "courses", name: "Courses" },
      { slug: "sante", name: "Santé" },
    ] as Category[];
    mockApi({
      "/api/accounts": () => [{ id: 1, name: "Courant", kind: "checking", archived: false }],
      "/api/categories": () => categories,
      "/api/assistant/hints": () => ({}),
      "/api/assistant": () => ({ configured: false }),
      "/api/transactions/": () => ({ propagated: 0 }),
      "/api/transactions?": () => ({ items, total: items.length }),
    });
    await render(<Operations />);
    await settle();
    const ring = host.querySelector(".sort-progress .ring")!;
    expect(ring.getAttribute("aria-label")).toBe("1 sur 3 opérations classées en octobre 2026"); // le virement interne ne compte pas
    expect(host.querySelector("#sort-title")?.textContent).toBe("Encore 2 opérations à classer");

    const select = (name: string) => host.querySelector<HTMLSelectElement>(`select[aria-label="Catégorie de ${name}"]`)!;
    const choose = (name: string, slug: string) => act(async () => {
      select(name).value = slug;
      select(name).dispatchEvent(new Event("change", { bubbles: true }));
    });

    items = items.map((t) => (t.id === 1 ? { ...t, category: "courses" } : t));
    await choose("Boulangerie", "courses");
    await settle();
    expect(host.querySelector(".toast")?.textContent).toContain("Classé en Courses");
    expect(host.querySelector(".celebrate")).toBeNull(); // il reste la pharmacie : pas encore de fête

    items = items.map((t) => (t.id === 2 ? { ...t, category: "sante" } : t));
    await choose("Pharmacie", "sante");
    await settle();
    expect(host.querySelector(".toast.is-success")?.textContent).toContain("Tout est rangé.");
    expect(host.querySelector(".celebrate.is-done")).not.toBeNull();
    expect(host.querySelector("#sort-title")?.textContent).toBe("Tout est rangé.");
    expect(host.querySelector(".sort-progress .ring.is-full")).not.toBeNull();
  });
});

describe("progression et états vides", () => {
  it("computes the ring share without dividing by zero", () => {
    expect(ringPercent(3, 4)).toBe(75);
    expect(ringPercent(0, 0)).toBe(0);
    expect(ringPercent(9, 4)).toBe(100);
    expect(ringPercent(-1, 4)).toBe(0);
  });

  it("draws an accessible ring with an inline gradient", async () => {
    await render(<><Ring value={5} max={7} label="5 papiers sur 7 vérifiés" /><Ring value={0} max={3} label="rien" size="sm" /></>);
    const [ring, empty] = [...host.querySelectorAll(".ring")];
    expect(ring!.getAttribute("role")).toBe("img");
    expect(ring!.getAttribute("aria-label")).toBe("5 papiers sur 7 vérifiés");
    expect(ring!.textContent).toBe("71 %");
    const arc = ring!.querySelector(".ring-arc")!;
    expect(arc.getAttribute("stroke-dasharray")).toBe("71 100");
    const gid = ring!.querySelector("linearGradient")!.id;
    expect(arc.getAttribute("stroke")).toBe(`url(#${gid})`); // dégradé déclaré sur place, aucune URL externe
    expect(gid).toMatch(/^ring-[A-Za-z0-9_-]+$/);
    expect(empty!.classList.contains("ring-sm")).toBe(true);
    expect(empty!.querySelector(".ring-arc")).toBeNull();
    expect(host.querySelector("[style]")).toBeNull(); // CSP : aucun style en ligne
  });

  it("renders an illustrated empty state with its sentence and action", async () => {
    await render(
      <EmptyState art="folder" title="Ton coffre est vide" className="card vault-empty" actions={<button type="button">Prendre une photo</button>}>
        <p>Prends un papier en photo : Bank le lit pour toi.</p>
      </EmptyState>,
    );
    const box = host.querySelector(".empty-state.card.vault-empty")!;
    expect(box.querySelector("h2")?.textContent).toBe("Ton coffre est vide");
    expect(box.querySelector("svg.empty-art.is-folder")?.getAttribute("aria-hidden")).toBe("true");
    expect(box.querySelector(".actions button")?.textContent).toBe("Prendre une photo");
    expect(host.querySelector("[style]")).toBeNull();
  });

  it("greets by the hour and maps procedure stages to steps", () => {
    expect(greeting("micka", new Date(2026, 9, 24, 9))).toBe("Bonjour Micka");
    expect(greeting("micka", new Date(2026, 9, 24, 20))).toBe("Bonsoir Micka");
    expect(greeting(undefined, new Date(2026, 9, 24, 2))).toBe("Bonsoir");
    expect(greeting("  ", new Date(2026, 9, 24, 12))).toBe("Bonjour");
    expect(rowPhase("verify")).toBe(0);
    expect(rowPhase("ready")).toBe(2);
    expect(rowPhase("waiting")).toBe(4);
    expect(rowPhase("done")).toBe(5);
    expect(toSort({ category: null, is_transfer: false } as Transaction)).toBe(true);
    expect(toSort({ category: null, is_transfer: true } as Transaction)).toBe(false);
  });
});
