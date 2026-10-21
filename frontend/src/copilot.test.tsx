// Copilote : centre d'opportunités (obtenu / en cours jamais additionnés, lancement d'une démarche, décisions),
// démarches (provenance, contradiction, revue obligatoire, transmission déclarée), veille (pour toi d'abord,
// relève, sources) et la carte « Pour ton argent » — rendu réel dans jsdom, API simulée.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CopilotSummary, Opportunity, OpportunityCenter, ProcedureListing, ProcedureView, WatchListing } from "./api";
import { Copilot } from "./components/Copilot";
import { ToastProvider } from "./components/ui";
import { perPeriod } from "./format";
import { chanceLevel, nextMilestone, Opportunities } from "./pages/Opportunities";
import { Procedures } from "./pages/Procedures";
import { Watch } from "./pages/Watch";

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
const render = (node: React.ReactNode) => act(async () => root.render(<ToastProvider>{node}</ToastProvider>));
const button = (label: string): HTMLButtonElement => {
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent?.trim().startsWith(label) || x.getAttribute("aria-label") === label);
  if (!b) throw new Error(`bouton introuvable : ${label} dans ${host.textContent}`);
  return b;
};
const click = (label: string) => act(async () => { button(label).click(); });
const typeInto = (el: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const sent = (url: string, method: string) => calls.filter((c) => c.url === url && c.init?.method === method);
const body = (c: Call) => JSON.parse(String(c.init?.body)) as Record<string, unknown>;
const plain = (s: string | null | undefined) => (s ?? "").replace(/\s/g, " ");

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  window.location.hash = "";
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

// ============================================================================ opportunités
const opp = (over: Partial<Opportunity> = {}): Opportunity => ({
  key: "fees:2026-10", category: "recuperer", category_label: "Récupérer",
  title: "Frais bancaires d'octobre 2026 : 17,90 € au-delà des plafonds légaux",
  summary: "Une réclamation écrite qui cite les articles de loi, puis le médiateur si besoin.",
  amount_cents: 1_790, period: "once", yearly_cents: 1_790, probability: 0.8, effort_minutes: 15, cost_cents: 0,
  expected_net_cents: 1_057, confidence: "élevée",
  facts: ["01/10 — COMMISSION D INTERVENTION — 8,90 €"], estimates: ["Dépassement : commissions d'intervention au-delà de 80 € sur le mois."],
  hypotheses: [], sources: [{ title: "Code monétaire et financier, art. R. 312-4-1", url: null, verified_on: null, kind: "texte" }],
  action: { template: "contester_frais", initial: { montant: 1_790 } }, deadline: null, status: "new", procedure: null, ...over,
});
const center = (over: Partial<OpportunityCenter> = {}): OpportunityCenter => ({
  opportunities: [
    opp({ key: "sub:netflix", category: "reduire", category_label: "Réduire tes dépenses", title: "Netflix.com : 13,49 € par mois, tu t'en sers encore ?",
          amount_cents: 1_349, period: "month", yearly_cents: 16_188, probability: 0.25, effort_minutes: 10, expected_net_cents: 3_797,
          confidence: "faible", hypotheses: ["Tu ne t'en sers plus assez pour son prix — toi seul le sais."], action: { template: "resilier_abonnement" } }),
    opp(),
    opp({ key: "aid:simulate", category: "revenus", category_label: "Augmenter tes revenus", title: "Vérifie à quelles aides tu as droit",
          amount_cents: 0, period: "month", yearly_cents: 0, probability: 0.5, effort_minutes: 1, expected_net_cents: -25, confidence: "moyenne",
          action: { link: "#/aides", label: "Lancer la simulation" } }),
  ],
  hidden: 1,
  categories: { recuperer: "Récupérer", reduire: "Réduire tes dépenses", revenus: "Augmenter tes revenus", epargne: "Mieux épargner", anticiper: "Anticiper" },
  priorities: ["recuperer"],
  time_value_cents_per_hour: 1_500,
  value: {
    confirmed: { once: 1_790, month: 0, year: 0 }, confirmed_yearly_cents: 1_790,
    estimated: { once: 0, month: 0, year: 0 }, estimated_yearly_cents: 0,
    history: [{ key: "fees:2026-09", amount_cents: 1_790, period: "once", on: "2026-10-10", label: "Frais de septembre", evidence: "Constaté par Bank sur tes comptes, confirmé par toi", source: "auto" }],
    pending: [],
  },
  confirmations: [],
  disclaimer: "Des estimations, jamais des promesses. Bank n'est pas un conseiller financier agréé.",
  ...over,
});

describe("centre d'opportunités", () => {
  it("keeps obtained and estimated apart, explains each lead and launches the linked procedure", async () => {
    mockApi({
      "/api/opportunities/start": () => ({ procedure_id: 42 }),
      "/api/opportunities/decide": () => undefined,
      "/api/opportunities": () => center(),
    });
    await render(<Opportunities />);
    await settle();
    const piggy = host.querySelector(".piggy")!;
    expect(plain(piggy.querySelector(".piggy-figure")?.textContent)).toBe("17,90 €");
    expect(piggy.querySelector(".jar")?.getAttribute("aria-label")).toBe("Tirelire remplie à 72 % du prochain palier");
    expect(plain(piggy.querySelector(".piggy-next")?.textContent)).toContain("Prochain palier : 25 €");
    expect(piggy.querySelector(".piggy-road")?.textContent).toContain("rien pour l'instant"); // l'estimé n'est jamais ajouté
    const titles = [...host.querySelectorAll(".mission h3")].map((h) => h.textContent);
    expect(titles[0]).toContain("Netflix"); // l'ordre du serveur (gain net attendu) est respecté
    const netflix = host.querySelector(".mission.is-featured")!;
    expect(plain(netflix.querySelector(".reward")?.textContent)).toBe("+13,49 € par mois");
    expect(netflix.querySelector(".chance")?.getAttribute("aria-label")).toBe("Chance faible (25 %)");
    expect(netflix.querySelector(".opp-evidence.is-hypothesis")?.textContent).toContain("toi seul le sais");
    expect(host.querySelector(".reward.is-none")?.textContent).toBe("À vérifier"); // pas d'argent en jeu : pas de faux montant
    expect(host.textContent).toContain("1 mission écartée");
    expect(host.textContent).toContain("conseiller financier agréé");
    await click("Préparer la démarche");
    await settle();
    expect(body(sent("/api/opportunities/start", "POST")[0]!)).toEqual({ key: "sub:netflix" });
    expect(window.location.hash).toBe("#/demarches/42");
    await click("Plus tard");
    await settle();
    expect(body(sent("/api/opportunities/decide", "POST")[0]!)).toEqual({ key: "sub:netflix", decision: "snoozed", days: 30 });
  });

  it("filters by priority and asks before counting a gain as obtained", async () => {
    let data = center({
      confirmations: [{ key: "fees:2026-10", procedure_id: 7, title: "Réclamation de frais", label: "Remboursement de frais reçu le 05/11/2026 : REMBOURSEMENT FRAIS COMMISSION", amount_cents: 1_790, period: "once" }],
    });
    mockApi({
      "/api/opportunities/confirm": () => {
        data = center({ confirmations: [] });
        return undefined;
      },
      "/api/opportunities/settings": () => undefined,
      "/api/opportunities": () => data,
    });
    await render(<Opportunities />);
    await settle();
    await click("Tes priorités");
    expect([...host.querySelectorAll(".mission h3")].map((h) => h.textContent)).toEqual([opp().title]);
    expect(host.textContent).toContain("De l'argent est arrivé");
    await click("Confirmer ce gain");
    await settle();
    expect(body(sent("/api/opportunities/confirm", "POST")[0]!)).toMatchObject({ key: "fees:2026-10", amount_cents: 1_790, period: "once", procedure_id: 7 });
    expect(host.textContent).not.toContain("De l'argent est arrivé");
    expect(host.querySelector(".jar-coin.is-dropping")).not.toBeNull(); // la pièce tombe dans la tirelire
    await click("Modifier");
    await typeInto(host.querySelector<HTMLInputElement>("#tv")!, "20");
    await act(async () => { host.querySelector<HTMLInputElement>("#tv")!.dispatchEvent(new Event("blur", { bubbles: true })); host.querySelector<HTMLInputElement>("#tv")!.blur(); });
    await click("Enregistrer");
    await settle();
    expect(sent("/api/opportunities/settings", "PUT")).toHaveLength(1);
  });

  it("formats amounts by period and grades chances and milestones", () => {
    expect(chanceLevel(0.8).dots).toBe(3);
    expect(chanceLevel(0.5).label).toBe("Chance moyenne");
    expect(chanceLevel(0.1).dots).toBe(1);
    expect(nextMilestone(0)).toBe(2_500);
    expect(nextMilestone(2_500)).toBe(5_000); // un palier atteint ouvre le suivant
    expect(nextMilestone(1_200_000)).toBe(2_000_000);
    expect(plain(perPeriod(1_349, "month"))).toBe("13,49 € par mois");
    expect(plain(perPeriod(25_800, "year"))).toBe("258 € par an");
    expect(plain(perPeriod(1_790, "once"))).toBe("17,90 €");
  });
});

// ============================================================================ démarches
const procedure = (over: Partial<ProcedureView> = {}): ProcedureView => ({
  id: 7, title: "Demander la prime d'activité",
  template: {
    key: "prime_activite", title: "Demander la prime d'activité", summary: "", category: "aide", organisme: "CAF", channel: "online_official",
    recipient: "Ta Caf, par ton espace Mon Compte sur caf.fr", official_url: "https://www.caf.fr/",
    steps: ["Sur caf.fr : Aides et démarches › Mes démarches.", "Valider."],
    sources: [{ title: "Cnaf — fiche pratique", url: "https://www.caf.fr/fiche.pdf", verified_on: "2026-10-21", kind: "officiel" }],
    confidence: "moyenne",
    declarations: [
      { key: "exactitude", text: "Ce que tu déclares sur caf.fr engage ta responsabilité.", kind: "consequence" },
      { key: "trimestrielle", text: "Tu t'engages à déclarer tes ressources tous les trois mois.", kind: "engagement" },
    ],
    followup_label: "Regarde l'état de ta demande dans Mon Compte", has_letter: false,
  },
  stage: "ready", stage_label: "Prêt à valider",
  fields: [
    { key: "salaire_m1", label: "Montant net social — mois le plus récent", type: "amount", required: true, active: true, help: "", choices: [],
      value: 207_115, display: "2 071,15 €", missing: false, conflict: null,
      provenance: { source: "vault", label: "Lu dans le coffre", document_id: 3, document_title: "Bulletin de paie — septembre 2026", evidence: "Montant net social 2 071,15", period: "septembre 2026" } },
    { key: "allocataire", label: "Déjà allocataire de la Caf ?", type: "choice", required: true, active: true, help: "", choices: [{ value: "oui", label: "Oui" }, { value: "non", label: "Non" }],
      value: "oui", display: "Oui", missing: false, conflict: null, provenance: { source: "comptes", label: "Tes comptes" } },
    { key: "loyer", label: "Loyer mensuel", type: "amount", required: true, active: false, help: "", choices: [], value: null, display: "", missing: false, conflict: null, provenance: null },
  ],
  pieces: [{ key: "bulletins", label: "Bulletins de paie des 3 derniers mois", required: true, count: 3, help: "", missing: false,
             attached: [{ id: 3, title: "Bulletin de paie — septembre 2026", status: "verified", document_date: "2026-09-30" }], candidates: [] }],
  evaluation: { missing_fields: [], conflicts: [], missing_pieces: [], unverified: [], warnings: [] },
  letter: null, hash: "a".repeat(64), approved: false, approved_stale: false, reference: null, notes: "", sent_on: null, next_check: null,
  due: false, deadline: null, escalation: null, outcome: null, opportunity_key: null, created_at: "2026-10-21T10:00:00Z",
  updated_at: "2026-10-21T10:00:00Z", events: [{ ts: "2026-10-21T10:00:00Z", kind: "created", detail: {} }],
  ...over,
});

describe("démarches", () => {
  it("shows where each value comes from and requires every declaration before validating", async () => {
    let view = procedure();
    mockApi({
      "/api/procedures/7/approve": () => {
        view = procedure({ approved: true });
        return view;
      },
      "/api/procedures/7/sent": () => {
        view = procedure({ approved: true, stage: "waiting", stage_label: "En attente", sent_on: "2026-10-21", next_check: "2026-11-11" });
        return view;
      },
      "/api/procedures/7": () => view,
    });
    await render(<Procedures id={7} wantNew={false} />);
    await settle();
    expect(host.querySelectorAll(".proc-field")).toHaveLength(2); // le loyer ne concerne pas ce dossier
    const proof = host.querySelector(".proc-field-proof")!;
    expect(proof.textContent).toContain("Lu dans le coffre");
    expect(proof.querySelector("a")?.getAttribute("href")).toBe("#/documents/3");
    expect(proof.querySelector("q")?.textContent).toBe("Montant net social 2 071,15");
    expect(host.querySelector('[aria-current="step"]')?.textContent).toContain("Relecture");
    const validate = button("Je valide ce dossier");
    expect(validate.disabled).toBe(true);
    const boxes = [...host.querySelectorAll<HTMLInputElement>(".proc-declarations input")];
    expect(host.querySelector(".proc-decl.is-consequence")?.textContent).toContain("engage ta responsabilité");
    await act(async () => { boxes[0]!.click(); });
    expect(button("Je valide ce dossier").disabled).toBe(true); // un engagement non coché bloque encore
    await act(async () => { boxes[1]!.click(); });
    await click("Je valide ce dossier");
    await settle();
    expect(body(sent("/api/procedures/7/approve", "POST")[0]!)).toEqual({ hash: "a".repeat(64), declarations: ["exactitude", "trimestrielle"] });
    expect(host.textContent).toContain("Bank n'envoie rien à ta place");
    expect(host.querySelector('a[href="https://www.caf.fr/"]')?.getAttribute("rel")).toBe("noopener noreferrer");
    await typeInto(host.querySelector<HTMLInputElement>(".proc-sent input:not([type=date])")!, "CAF-2026-1234");
    await click("Je l'ai transmis");
    await settle();
    expect(body(sent("/api/procedures/7/sent", "POST")[0]!)).toMatchObject({ reference: "CAF-2026-1234" });
    expect(host.textContent).toContain("Prochain point");
  });

  it("blocks on a value contradicting its proof and restores it in one gesture", async () => {
    const conflict = { key: "salaire_m1", label: "Montant net social", value: "1 500,00 €", expected: "2 071,15 €", document_id: 3, evidence: "Montant net social 2 071,15" };
    let view = procedure({
      stage: "verify", stage_label: "À vérifier",
      fields: [{ ...procedure().fields[0]!, value: 150_000, display: "1 500,00 €", conflict, provenance: { source: "user", label: "Saisi par toi" } }],
      evaluation: { missing_fields: [], conflicts: [conflict], missing_pieces: [], unverified: [], warnings: [] },
    });
    mockApi({
      "/api/procedures/7/fields/salaire_m1/restore": () => {
        view = procedure();
        return view;
      },
      "/api/procedures/7": () => view,
    });
    await render(<Procedures id={7} wantNew={false} />);
    await settle();
    expect(host.querySelector(".proc-conflict")?.textContent).toContain("le justificatif indique");
    expect(host.querySelector('.proc-conflict a[href="#/documents/3"]')).not.toBeNull();
    expect(host.textContent).not.toContain("Je valide ce dossier");
    await click("Reprendre");
    await settle();
    expect(sent("/api/procedures/7/fields/salaire_m1/restore", "POST")).toHaveLength(1);
    expect(host.querySelector(".proc-conflict")).toBeNull();
    expect(host.textContent).toContain("Je valide ce dossier");
  });

  it("lays out the board by stage and starts a procedure from the catalogue", async () => {
    const listing: ProcedureListing = {
      procedures: [
        { id: 1, title: "Réclamation de frais", template: "contester_frais", stage: "waiting", stage_label: "En attente", next_check: "2026-10-20", due: true, deadline: null, updated_at: "", opportunity_key: "fees:2026-09" },
        { id: 2, title: "Résilier Netflix", template: "resilier_abonnement", stage: "verify", stage_label: "À vérifier", next_check: null, due: false, deadline: null, updated_at: "", opportunity_key: null },
      ],
      stages: [],
      templates: [procedure().template],
    };
    mockApi({
      "/api/procedures": (init) => (init?.method === "POST" ? procedure({ id: 9 }) : listing),
    });
    await render(<Procedures id={null} wantNew={false} />);
    await settle();
    expect(host.querySelector(".proc-col.is-prepare")?.textContent).toContain("Résilier Netflix");
    expect(host.querySelector(".proc-col.is-waiting .proc-card.is-due")?.textContent).toContain("à relancer");
    expect(host.querySelector(".proc-due")?.textContent).toContain("Réclamation de frais");
    await click("Nouvelle démarche");
    await click("Commencer");
    await settle();
    expect(body(sent("/api/procedures", "POST")[0]!)).toEqual({ template: "prime_activite" });
    expect(window.location.hash).toBe("#/demarches/9");
  });
});

// ============================================================================ veille
const watchListing = (over: Partial<WatchListing> = {}): WatchListing => ({
  enabled: true,
  interests: ["epargne", "impots"],
  interest_labels: { epargne: "Épargne", impots: "Impôts" },
  items: [
    { id: 1, title: "Livret A et LEP : les nouveaux taux", summary: "Le taux évolue.", url: "https://www.economie.gouv.fr/livret", published_at: "2026-10-21T13:12:28+00:00",
      first_seen_at: "2026-10-21T14:00:00+00:00", last_verified_at: "2026-10-21T14:00:00+00:00", changed_at: null, version: 1, topics: ["epargne"],
      topic_labels: ["Épargne"], relevant: true, status: "new", source: { name: "Ministère de l'Économie", trust: "officiel" } },
    { id: 2, title: "La French tech", summary: "", url: "javascript:alert(1)", published_at: null, first_seen_at: null, last_verified_at: null,
      changed_at: null, version: 1, topics: [], topic_labels: [], relevant: false, status: "seen", source: { name: "Ministère de l'Économie", trust: "officiel" } },
  ],
  counts: { new_relevant: 1, new: 1 },
  engine: { used: "176.1.2", latest: "177.0.0", latest_published_at: "2026-10-19T09:00:00+00:00", outdated: true, advice: "Mets à jour le conteneur du moteur fiscal." },
  references: [{ key: "rates", title: "Taux des livrets réglementés", as_of: "2026-08-01", next_check: "2027-02-01", status: "ok", detail: "Taux en vigueur.", source: null }],
  sources: [{ id: 1, name: "Ministère de l'Économie", url: "https://www.economie.gouv.fr/rss/toutesactualites", trust: "officiel", builtin: true, enabled: true, interval_hours: 12, last_checked_at: null, last_ok_at: "2026-10-21T14:00:00+00:00", last_error: null }],
  ...over,
});

describe("veille", () => {
  it("shows what concerns you first, refreshes on demand and manages sources", async () => {
    mockApi({
      "/api/watch/refresh": () => ({ ...watchListing(), report: { checked: 2, new: 3, changed: 1, errors: 0, skipped: 0 } }),
      "/api/watch/sources": () => ({ id: 2, name: "Mon blog" }),
      "/api/watch/items/1": () => undefined,
      "/api/watch": () => watchListing(),
    });
    await render(<Watch />);
    await settle();
    expect(host.querySelectorAll(".watch-item")).toHaveLength(1); // « Pour toi » par défaut
    expect(host.querySelector(".watch-engine")?.textContent).toContain("177.0.0");
    await click("Tout");
    expect(host.querySelectorAll(".watch-item")).toHaveLength(2);
    const links = [...host.querySelectorAll<HTMLAnchorElement>(".watch-item h3 a")];
    expect(links).toHaveLength(1); // le lien javascript: n'arrive jamais jusqu'ici, et un lien sans adresse n'est pas rendu
    expect(links[0]!.rel).toBe("noopener noreferrer");
    await click("Relever maintenant");
    await settle();
    expect(body(sent("/api/watch/refresh", "POST")[0]!)).toEqual({ force: true });
    await click("Lu");
    await settle();
    expect(body(sent("/api/watch/items/1", "PATCH")[0]!)).toEqual({ status: "seen" });
    await typeInto(host.querySelector<HTMLInputElement>('input[type="url"]')!, "https://blog.example.org/feed");
    await click("Ajouter");
    await settle();
    expect(body(sent("/api/watch/sources", "POST")[0]!)).toEqual({ url: "https://blog.example.org/feed", name: null });
  });
});

// ============================================================================ carte « Pour ton argent »
describe("carte « Pour ton argent »", () => {
  const summary = (over: Partial<CopilotSummary> = {}): CopilotSummary => ({
    opportunities: [], value: { confirmed_yearly_cents: 0, estimated_yearly_cents: 0 }, confirmations: 0,
    procedures: { open: 0, ready: 0, waiting: 0, due: [] }, watch: { enabled: true, new_relevant: 0, stale_references: 0, engine_outdated: false },
    ...over,
  });

  it("stays silent when there is nothing to do", async () => {
    mockApi({ "/api/copilot/summary": () => summary() });
    await render(<Copilot />);
    await settle();
    expect(host.querySelector(".copilot")).toBeNull();
  });

  it("lists leads, follow-ups and news, each linking where you act", async () => {
    mockApi({
      "/api/copilot/summary": () => summary({
        opportunities: [opp()],
        value: { confirmed_yearly_cents: 1_790, estimated_yearly_cents: 1_432 },
        procedures: { open: 1, ready: 1, waiting: 1, due: [{ id: 4, title: "Réclamation de frais", next_check: "2026-10-20", stage_label: "En attente" }] },
        watch: { enabled: true, new_relevant: 2, stale_references: 0, engine_outdated: true },
      }),
    });
    await render(<Copilot />);
    await settle();
    const card = host.querySelector(".copilot")!;
    expect(plain(card.querySelector(".copilot-value")?.textContent)).toContain("Tirelire 18 € / an");
    expect(card.querySelector('a[href="#/demarches/4"]')?.textContent).toBe("Réclamation de frais");
    expect(card.textContent).toContain("1 démarche prête à valider");
    expect(card.textContent).toContain("2 nouveautés officielles te concernent");
    expect(card.textContent).toContain("Nouvelle version du moteur");
    expect(plain(card.querySelector(".copilot-amount")?.textContent)).toBe("+17,90 €");
  });
});

// ============================================================================ navigation simple
describe("navigation en cinq destinations", () => {
  it("groups missions with aids and documents with procedures, as tabs", async () => {
    const { HubTabs, hubOf } = await import("./App");
    expect(hubOf("aides")).toBe("gagner");
    expect(hubOf("demarches")).toBe("papiers");
    expect(hubOf("operations")).toBe("operations");
    await render(<HubTabs page="aides" />);
    const links = [...host.querySelectorAll<HTMLAnchorElement>(".hub-tabs a")];
    expect(links.map((a) => a.textContent)).toEqual(["Missions", "Aides"]);
    expect(links[1]!.getAttribute("aria-current")).toBe("page");
    await render(<HubTabs page="operations" />);
    expect(host.querySelector(".hub-tabs")).toBeNull(); // pas d'onglets hors des pôles
  });
});
