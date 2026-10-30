// Radar : prévisions chiffrées et contexte macro restent séparés ; rien n'est inventé sans compte courant,
// rien n'est lu sans accord « veille ». Rendu réel dans jsdom, /api/watch/radar simulé (forme de
// backend/app/services/future_radar.py::view).

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FutureRadar, inDays, secureHref, type RadarView } from "./components/FutureRadar";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const DISCLAIMER = "Scénarios construits à partir des opérations connues. Une actualité mondiale ne prouve ni une baisse future de tes investissements ni une relation de cause à effet.";
const noCrypto = {
  tracked_count: 0, priced_count: 0, stale_count: 0, assets: [], valued_cents: 0,
  shock_10_cents: 0, shock_25_cents: 0, note: "Chocs fictifs de -10 % / -25 %.",
};
const radar = (over: Partial<RadarView> = {}): RadarView => ({
  horizon_days: 45,
  crypto_stress: noCrypto,
  available: true,
  news_consent: true,
  signals: [],
  macro_context: [],
  disclaimer: DISCLAIMER,
  ...over,
});
const signals: RadarView["signals"] = [
  { key: "radar:planned:7:2026-10-31", label: "Taxe foncière", on: "2026-10-31", days: 1, amount_cents: 120_000,
    projected_month_end_cents: -12_000, level: "risk", basis: "Dépense saisie par toi", source: "planned", confidence: "declared" },
  { key: "radar:month:2026-11-02:Pneus:40000", label: "Pneus hiver", on: "2026-11-02", days: 3, amount_cents: 40_000,
    projected_month_end_cents: 5_000, level: "tight", basis: "Dépense saisie par toi", source: "planned", confidence: "declared" },
  { key: "radar:fixed:loyer:2026-11-11", label: "Loyer", on: "2026-11-11", days: 12, amount_cents: 85_000,
    projected_month_end_cents: 48_000, level: "watch", basis: "Récurrence détectée", source: "detected", confidence: "estimated" },
];
const articles: RadarView["macro_context"] = [
  { id: 11, title: "Monetary policy decisions", published_at: "2026-10-20",
    url: "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp261020.en.html", source: "Banque centrale européenne", topics: ["marches"] },
  { id: 12, title: "Sanctions : nouveau paquet adopté", published_at: "2026-10-22", url: null,
    source: "Conseil de l'UE", topics: ["geopolitique", "transparence"] },
];

let host: HTMLDivElement;
let root: Root;
let calls: string[];
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const plain = () => (host.textContent ?? "").replace(/\s/g, " ");

function mockRadar(view: RadarView) {
  calls = [];
  vi.stubGlobal("fetch", (url: string) => {
    calls.push(url);
    const body = url.startsWith("/api/watch/radar") ? view : {};
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
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

describe("radar des 45 prochains jours", () => {
  it("invites to connect an account and invents no signal when no budget account feeds the model", async () => {
    mockRadar(radar({ available: false, signals: [], macro_context: [] }));
    await act(async () => root.render(<FutureRadar />));
    await flush();
    expect(calls).toEqual(["/api/watch/radar"]);
    expect(host.textContent).toContain("Radar · 45 prochains jours");
    expect(host.textContent).toContain("Aucune prévision personnelle fiable sans compte courant budgétaire");
    expect(host.querySelector('a[href="#/connecteurs"]')).not.toBeNull();
    expect(host.querySelectorAll(".future-radar-signal")).toHaveLength(0);
    // Ni faux signal, ni fausse réassurance « aucun prélèvement » : on ne sait pas.
    expect(host.textContent).not.toContain("Aucun gros prélèvement identifié");
    expect(host.textContent).not.toContain("fin du mois estimée");
    expect(host.textContent).toContain(DISCLAIMER);
  });

  it("lists dated signals with amounts, level and plural-aware delay, and macro context apart with source, date and https-only links", async () => {
    mockRadar(radar({ signals, macro_context: articles }));
    await act(async () => root.render(<FutureRadar />));
    await flush();
    const rows = [...host.querySelectorAll(".future-radar-signal")];
    expect(rows).toHaveLength(3);

    const risk = rows[0]!;
    expect(risk.classList.contains("is-risk")).toBe(true);
    expect(risk.textContent).toContain("Taxe foncière");
    expect(risk.textContent).toContain("Dans 1 jour");
    expect(risk.textContent).not.toContain("1 jours");
    expect(risk.querySelector(".tag")?.textContent).toBe("Compte sous zéro");
    expect(risk.querySelector(".tag")?.classList.contains("danger")).toBe(true);
    const riskText = (risk.textContent ?? "").replace(/\s/g, " ");
    expect(riskText).toContain("1 200,00 € prévus");
    expect(riskText).toContain("-120,00 €");
    expect(risk.textContent).toContain("Dépense saisie par toi");
    expect(risk.textContent).toContain("Saisi par toi");

    // Le mois en cours : la référence est le point bas du mois, et « sous le matelas » n'est pas « sous zéro ».
    const tight = rows[1]!;
    expect(tight.classList.contains("is-tight")).toBe(true);
    expect(tight.querySelector(".tag")?.textContent).toBe("Marge à surveiller");
    expect(tight.querySelector(".tag")?.classList.contains("warn")).toBe(true);
    expect(tight.textContent).toContain("Dans 3 jours");
    expect(tight.textContent).toContain("point bas du mois estimé");

    const watch = rows[2]!;
    expect(watch.classList.contains("is-watch")).toBe(true);
    expect(watch.textContent).toContain("Loyer");
    expect(watch.textContent).toContain("Dans 12 jours");
    expect(watch.querySelector(".tag")?.textContent).toBe("Dépense à prévoir");
    expect(watch.querySelector(".tag")?.classList.contains("warn")).toBe(false);
    const watchText = (watch.textContent ?? "").replace(/\s/g, " ");
    expect(watchText).toContain("850,00 € prévus");
    expect(watchText).toContain("480,00 €");
    expect(watch.textContent).toContain("Récurrence détectée");
    expect(watch.textContent).toContain("Estimation à confirmer");

    // Contexte macro : à part, sourcé et daté ; lien seulement vers https.
    const items = [...host.querySelectorAll(".future-radar-articles li")];
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain("Monetary policy decisions");
    expect(items[0]!.textContent).toContain("Banque centrale européenne");
    expect(items[0]!.textContent).toContain("20 oct");
    expect(items[1]!.textContent).toContain("Conseil de l'UE");
    expect(items[1]!.textContent).toContain("22 oct");
    expect(items[1]!.textContent).toContain("geopolitique, transparence");
    const links = [...host.querySelectorAll<HTMLAnchorElement>(".future-radar-articles a")];
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute("href")).toBe(articles[0]!.url);
    expect(links[0]!.rel).toBe("noopener noreferrer");
    expect(links[0]!.target).toBe("_blank");
    expect(items[1]!.querySelector("a")).toBeNull();
    expect(host.textContent).toContain("ne prédit pas leur performance");
    expect(plain()).not.toContain("La veille réseau est désactivée");
  });

  it("shows no macro context and names the missing consent when the watch is off", async () => {
    mockRadar(radar({ news_consent: false, signals: [], macro_context: [] }));
    await act(async () => root.render(<FutureRadar />));
    await flush();
    expect(host.querySelector(".future-radar-articles")).toBeNull();
    expect(host.textContent).toContain("La veille réseau est désactivée");
    expect(host.textContent).toContain("Donner ton accord dans Confidentialité");
    expect(host.querySelector('.future-radar-context a[href="#/reglages"]')).not.toBeNull();
    expect(host.textContent).not.toContain("Aucun titre officiel récent");
    expect(host.textContent).toContain("Aucun gros prélèvement identifié");
  });

  it("formats delays and only trusts https links", () => {
    expect(inDays(0)).toBe("Aujourd'hui");
    expect(inDays(1)).toBe("Dans 1 jour");
    expect(inDays(2)).toBe("Dans 2 jours");
    expect(inDays(45)).toBe("Dans 45 jours");
    expect(secureHref("https://www.ecb.europa.eu/press")).toBe("https://www.ecb.europa.eu/press");
    expect(secureHref("http://example.org/feed")).toBeNull();
    expect(secureHref("javascript:alert(1)")).toBeNull();
    expect(secureHref(null)).toBeNull();
  });
});
