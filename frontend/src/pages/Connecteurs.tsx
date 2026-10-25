// Connecteurs : tout ce qui alimente Bank en quelques clics — banques (DSP2), compteur Linky, documents
// de l'État (impôts, CAF, France Travail), et les agents IA.

import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  api,
  useApi,
  type BankSetupStatus,
  type Connection,
  type EnergyStatus,
  type OfficialFact,
  type ParsedDocument,
  type Portal,
  type StartView,
} from "../api";
import { BankConnect, BankMark } from "../components/BankConnect";
import { BankSetup, setupReady } from "../components/BankSetup";
import { PowensSetup } from "../components/PowensSetup";
import { EnergySignals } from "../components/EnergySignals";
import { StateAccounts } from "../components/StateAccounts";
import { Icon, type IconName } from "../components/Icon";
import { SectionNav, sectionFromHash, type SectionLink } from "../components/SectionNav";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useCelebrate, useToast } from "../components/ui";
import { money, shortDate } from "../format";
import { AgentTokens } from "./Agents";
import { AssistantSettings } from "./AssistantSettings";
import { MailSettings } from "./MailSettings";
import { SearchSettings } from "./SearchSettings";
import { Planning } from "./Planning";

export function Connecteurs({ bankResult, bankProvider = null }: { bankResult: string | null; bankProvider?: string | null }) {
  // Ce qui est branché, d'un coup d'œil : l'état vient du serveur (/api/start), le même que « Bien démarrer ».
  const start = useApi<StartView>("/api/start");
  const known = !!start.data;
  const linked = (key: string) => start.data?.connectors.find((c) => c.key === key)?.linked ?? false;
  const nav = useMemo<SectionLink[]>(
    () => (start.data?.connectors ?? []).map((c) => ({ id: c.section, label: c.title, state: c.linked ? "on" : "todo" })),
    [start.data],
  );
  const fold = (key: string) => ({ known, linked: linked(key), ...FOLDS[key]! });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Connecteurs</h1>
          <p className="muted">Branche tes sources en quelques clics : plus Bank en sait, plus ses conseils sont justes.</p>
        </div>
      </div>
      {nav.length > 0 && <SectionNav items={nav} label="Tes connecteurs" />}
      <Banks result={bankResult} provider={bankProvider} />
      <Fold {...fold("planning")}><Planning /></Fold>
      <Fold {...fold("linky")}><Linky /></Fold>
      <Fold {...fold("etat")}><Etat /></Fold>
      <Fold {...fold("assistant")}><AssistantSettings /></Fold>
      <Fold {...fold("recherche")}><SearchSettings /></Fold>
      <Fold {...fold("mail")}><MailSettings /></Fold>
      <Fold {...fold("agents")}><AgentTokens /></Fold>
      <section className="section" aria-labelledby="ch-link-title">
        <div className="section-head"><h2 id="ch-link-title"><Icon name="chat" /> Messageries</h2></div>
        <p className="section-note">
          Écris à Bank depuis WhatsApp, Telegram, Signal, Discord ou Slack, et reçois ses alertes là où tu es déjà.{" "}
          <a href="#/messageries">Ouvrir Messageries</a>
        </p>
      </section>
    </>
  );
}

// Les sections pas encore reliées tiennent sur une ligne : leur titre (le même id, pour le sommaire), « à faire » et
// « Relier », qui déplie la section complète. Tant que l'état n'est pas connu, rien n'est replié.
const FOLDS: Record<string, { section: string; title: string; icon: IconName; hint: string }> = {
  planning: { section: "planning-title", title: "Planning de travail", icon: "calendar", hint: "Ta paie prévue au jour près, depuis ton agenda de travail." },
  linky: { section: "linky-title", title: "Électricité (Linky)", icon: "bolt", hint: "Ta consommation et ta facture prévue, par Conso API." },
  etat: { section: "etat-title", title: "Comptes de l'État", icon: "doc", hint: "Impôts, CAF, France Travail : tes attestations lues pour toi." },
  assistant: { section: "assistant-title", title: "Assistant (OpenRouter)", icon: "spark", hint: "Tes questions d'argent en français, avec ta clé OpenRouter." },
  recherche: { section: "search-title", title: "Recherche web de l'assistant", icon: "search", hint: "SearXNG chez toi ou Brave, pour les questions d'actualité." },
  mail: { section: "mail-title", title: "Boîte mail, en lecture seule", icon: "doc", hint: "Factures et attestations reçues par mail, proposées dans Papiers." },
  agents: { section: "agents-title", title: "Agents IA", icon: "robot", hint: "Un jeton pour qu'un agent lise ton dossier, dans tes limites." },
};

function Fold({ known, linked, section, title, icon, hint, children }: {
  known: boolean;
  linked: boolean;
  section: string;
  title: string;
  icon: IconName;
  hint: string;
  children: ReactNode;
}) {
  const [opened, setOpened] = useState(false);
  const focusAfter = useRef(false);
  const folded = known && !linked && !opened && sectionFromHash() !== section;
  useEffect(() => {
    if (!opened || !focusAfter.current) return;
    focusAfter.current = false;
    // La section dépliée prend le focus sur son titre (elle charge ses données : on lui laisse un instant).
    const t = window.setTimeout(() => {
      const h = document.getElementById(section);
      if (h && !h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
      h?.focus();
    }, 50);
    return () => window.clearTimeout(t);
  }, [opened, section]);
  if (!folded) return <>{children}</>;
  return (
    <section className="section conn-fold" aria-labelledby={section}>
      <div className="conn-fold-row">
        <h2 id={section}><Icon name={icon} /> <span>{title} <span className="tag">à faire</span></span></h2>
        <button type="button" className="btn quiet sm" aria-expanded={false} onClick={() => { focusAfter.current = true; setOpened(true); }}>
          Relier <Icon name="arrow" />
        </button>
      </div>
      <p className="faint">{hint}</p>
    </section>
  );
}

// ------------------------------------------------------------------ banques (DSP2) en un clic
const CONNECTION_LABELS: Record<string, string> = { active: "active", expired: "à renouveler", revoked: "coupée", error: "erreur", pending: "en attente" };

export function Banks({ result, provider = null }: { result: string | null; provider?: string | null }) {
  const status = useApi<{ enabled: boolean; connections: Connection[] }>("/api/banks");
  const [aspsps, setAspsps] = useState<string[] | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [setupOk, setSetupOk] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const [listError, setListError] = useState<string | null>(null);
  const toast = useToast();
  const celebrate = useCelebrate();

  useEffect(() => {
    // le retour de Powens a ses propres messages, dans sa section
    if (provider !== "powens") {
      if (result === "connected") celebrate("done", "Banque connectée. Lance une synchronisation pour récupérer tes opérations.");
      if (result === "refused") toast("Connexion annulée côté banque.");
      if (result === "error") toast("La connexion a échoué ou a expiré : recommence.");
    }
    // Le retour de la banque ne se fête qu'une fois : sans ce paramètre, ni un rechargement ni « Retour » ne le rejouent.
    if (result && window.location.hash.includes("?")) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search + window.location.hash.split("?")[0]);
    }
  }, [result, provider, toast, celebrate]);

  const connections = status.data?.connections ?? [];
  const enabled = status.data?.enabled ?? false;
  // Sans banque connectée (ou à la demande), l'assistant Enable Banking guide jusqu'au choix de la banque.
  const wizard = !!status.data && (!enabled || connections.length === 0 || showSetup);
  const wantList = enabled && (showAdd || (connections.length === 0 && setupOk));
  const onSetup = (s: BankSetupStatus) => {
    setSetupOk(setupReady(s));
    if (s.configured !== enabled) status.reload();
  };
  useEffect(() => {
    if (wantList && aspsps === null) {
      api<{ name: string }[]>("/api/banks/aspsps?country=FR")
        .then((list) => setAspsps(list.map((a) => a.name)))
        .catch((e: Error) => setListError(e.message));
    }
  }, [wantList, aspsps]);

  const connect = async (name: string) => {
    setConnecting(name);
    setFailed(null);
    try {
      const r = await api<{ redirect_url: string }>("/api/banks/connect", { method: "POST", body: { aspsp_name: name, country: "FR" } });
      const url = new URL(r.redirect_url);
      if (url.protocol !== "https:") throw new Error("Adresse de redirection refusée");
      window.location.assign(url.toString());
    } catch (e) {
      setFailed(name);
      setConnecting(null);
      toast((e as Error).message);
    }
  };
  const sync = (id: number) =>
    void run(async () => {
      const r = await api<{ inserted: number; reconciled: number; errors: string[] }>(`/api/banks/${id}/sync`, { method: "POST" });
      toast(r.errors.length ? r.errors.join(" ") : `${r.inserted} nouvelles opérations, ${r.reconciled} rapprochées.`);
      status.reload();
    });
  const revoke = (id: number) =>
    void run(async () => {
      if (!window.confirm("Couper l'accès de Bank à cette banque ? L'historique déjà importé est conservé.")) return;
      await api(`/api/banks/${id}`, { method: "DELETE" });
      status.reload();
    });

  return (
    <section className="section" aria-labelledby="banks-title" id="banques">
      <div className="section-head">
        <h2 id="banks-title"><Icon name="bank" /> Banques</h2>
        {enabled && connections.length > 0 && (
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="btn ghost sm" aria-expanded={showSetup} onClick={() => setShowSetup((v) => !v)}>
              <Icon name={showSetup ? "close" : "lock"} /> Application Enable Banking
            </button>
            <button type="button" className="btn quiet sm" aria-expanded={showAdd} onClick={() => setShowAdd((v) => !v)}>
              <Icon name={showAdd ? "close" : "plus"} /> {showAdd ? "Fermer" : "Ajouter une banque"}
            </button>
          </span>
        )}
      </div>
      <p className="section-note">
        Lecture seule par DSP2, via Enable Banking (établissement agréé). Tu t'authentifies chez ta banque, Bank ne voit
        jamais tes identifiants. Accès valable 90 jours, renouvelable.{" "}
        <a href="#/operations?import=1">Sans connexion, l'import de relevés fonctionne pour toutes les banques.</a>
      </p>
      {status.error && <ErrorNote message={status.error} onRetry={status.reload} />}
      {!status.data && !status.error && <Loading what="Connexions" />}
      {wizard && (
        <BankSetup
          onChange={onSetup}
          chooser={connections.length === 0 ? (
            listError ? (
              <ErrorNote message={listError} onRetry={() => { setListError(null); setAspsps(null); }} />
            ) : (
              <BankConnect aspsps={aspsps} connections={connections} busy={connecting} failed={failed} onConnect={(n) => void connect(n)} />
            )
          ) : undefined}
        />
      )}
      {connections.length > 0 && (
        <ul className="connected" aria-label="Banques connectées">
          {connections.map((c) => (
            <li key={c.id}>
              <BankMark name={c.aspsp_name} />
              <div>
                <b>{c.aspsp_name}</b>
                <div className="faint">
                  {c.accounts.join(", ") || "comptes à venir"} ·{" "}
                  <span className={`tag ${c.status === "active" ? "ok" : c.status === "expired" ? "warn" : ""}`}>
                    {CONNECTION_LABELS[c.status] ?? c.status}
                  </span>
                  {c.valid_until && c.status === "active" && ` jusqu'au ${shortDate(c.valid_until)}`}
                  {c.last_sync_at ? ` · synchro ${shortDate(c.last_sync_at)}` : " · jamais synchronisée"}
                </div>
                {c.last_error && <div className="error">{c.last_error}</div>}
              </div>
              {c.status === "active" ? (
                <span className="actions" style={{ marginTop: 0 }}>
                  <button className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => sync(c.id)}>Synchroniser</button>
                  <button className="btn ghost sm" disabled={busy} onClick={() => revoke(c.id)}>Couper</button>
                </span>
              ) : c.status === "expired" ? (
                <button className="btn sm" disabled={connecting !== null} onClick={() => void connect(c.aspsp_name)}>Renouveler</button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {wantList && connections.length > 0 && (
        <div className="stack" style={{ marginTop: "1.25rem" }}>
          {listError ? (
            <ErrorNote message={listError} onRetry={() => { setListError(null); setAspsps(null); }} />
          ) : (
            <BankConnect aspsps={aspsps} connections={connections} busy={connecting} failed={failed} onConnect={(n) => void connect(n)} />
          )}
        </div>
      )}
      {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
      <PowensSetup result={provider === "powens" ? result : null} />
    </section>
  );
}

// ------------------------------------------------------------------ Linky (Conso API)
function Linky() {
  const st = useApi<EnergyStatus>("/api/energy");
  const [prm, setPrm] = useState("");
  const [token, setToken] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const connect = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<EnergyStatus>("/api/energy/linky", { method: "POST", body: { prm, token } });
      setToken("");
      toast(`Compteur connecté : ${r.imported ?? 0} jours récupérés.`);
      st.reload();
    });
  };
  const sync = () =>
    void run(async () => {
      const r = await api<EnergyStatus>("/api/energy/sync", { method: "POST" });
      toast(r.imported ? `${r.imported} jours mis à jour.` : (r.connection?.last_error ?? "Déjà à jour."));
      st.reload();
    });
  const disconnect = () =>
    void run(async () => {
      if (!window.confirm("Déconnecter le compteur ? Les relevés enregistrés sont supprimés.")) return;
      await api("/api/energy/linky", { method: "DELETE" });
      st.reload();
    });

  return (
    <section className="section" aria-labelledby="linky-title" id="energie">
      <div className="section-head"><h2 id="linky-title"><Icon name="bolt" /> Électricité (Linky)</h2></div>
      <p className="section-note">
        Ta consommation réelle, jour par jour, via Conso API (passerelle gratuite et open source vers Enedis). Bank en
        déduit ta facture annuelle probable et te prévient avant une régularisation. Tu peux révoquer l'accès à tout
        moment, ici ou dans ton compte Enedis.
      </p>
      {st.error && <ErrorNote message={st.error} onRetry={st.reload} />}
      {st.data && !st.data.connected && (
        <form className="panel stack" onSubmit={connect} aria-label="Connecter mon compteur Linky">
          <ol className="steps">
            <li>
              Donne ton accord chez Enedis depuis{" "}
              <a href="https://conso.boris.sh/" target="_blank" rel="noopener noreferrer">conso.boris.sh</a> (bouton
              « Donner mon consentement ») : tu te connectes à ton compte Enedis, et le site te remet un jeton personnel.
            </li>
            <li>Ton numéro de point de livraison (PRM, 14 chiffres) est sur ta facture ou sur l'écran du compteur.</li>
            <li>Colle les deux ici : Bank récupère un an d'historique, puis se met à jour chaque jour.</li>
          </ol>
          <div className="form-grid">
            <Field label="PRM (14 chiffres)">
              <input value={prm} onChange={(e) => setPrm(e.target.value)} inputMode="numeric" required minLength={14}
                     placeholder="12345678901234" autoComplete="off" />
            </Field>
            <Field label="Jeton Conso API" hint="Chiffré sur le serveur, jamais affiché ensuite.">
              <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required minLength={40}
                     autoComplete="off" />
            </Field>
            <button className="btn" disabled={busy}>Connecter le compteur</button>
          </div>
        </form>
      )}
      {st.data?.connected && st.data.connection && st.data.insights && (
        <EnergyPanel status={st.data} busy={busy} onSync={sync} onDisconnect={disconnect} onChanged={st.reload} />
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function EnergyPanel({ status, busy, onSync, onDisconnect, onChanged }: {
  status: EnergyStatus;
  busy: boolean;
  onSync: () => void;
  onDisconnect: () => void;
  onChanged: () => void;
}) {
  const c = status.connection!;
  const ins = status.insights!;
  const daily = status.daily ?? [];
  const max = Math.max(1, ...daily.map((d) => d.wh));
  const [custom, setCustom] = useState(false);
  const [price, setPrice] = useState(status.tariff.cents_per_100kwh);
  const [sub, setSub] = useState<number | null>(status.tariff.yearly_subscription_cents);
  const [tBusy, tError, tRun] = useAsyncAction();
  const toast = useToast();

  const applyTariff = (cents_per_100kwh: number, subscription_cents_per_year: number, label: string) =>
    void tRun(async () => {
      await api("/api/energy/tariff", { method: "PUT", body: { cents_per_100kwh, subscription_cents_per_year, label } });
      toast("Tarif enregistré.");
      setCustom(false);
      onChanged();
    });

  const reg = ins.regularisation_cents;
  return (
    <div className="stack">
      <div className="connected-head">
        <span className="logo" aria-hidden="true">⚡</span>
        <div>
          <b>Compteur {c.prm_masked}</b>
          <div className="faint">
            <span className={`tag ${c.status === "active" ? "ok" : "warn"}`}>{c.status === "active" ? "connecté" : "erreur"}</span>
            {" "}· données jusqu'au {ins.data_through ? shortDate(ins.data_through) : "—"}
            {ins.stale && <span className="tag warn"> en retard</span>}
            {c.last_error && <div className="error">{c.last_error}</div>}
          </div>
        </div>
        <span className="actions" style={{ marginTop: 0 }}>
          <button className="btn quiet" disabled={busy} onClick={onSync}>Synchroniser</button>
          <button className="btn danger" disabled={busy} onClick={onDisconnect}>Déconnecter</button>
        </span>
      </div>

      <dl className="figures">
        <div className="figure-item">
          <dt>30 derniers jours</dt>
          <dd>{Math.round(ins.last_30_days_wh / 1000)} kWh<small> · {money(ins.last_30_days_cost_cents, { whole: true })}</small></dd>
        </div>
        <div className="figure-item">
          <dt>Facture annuelle estimée</dt>
          <dd>{money(ins.yearly_estimate_cents, { whole: true })}{ins.yearly_is_extrapolated && <small> · extrapolée</small>}</dd>
        </div>
        <div className="figure-item">
          <dt>Mensualité conseillée</dt>
          <dd>{money(ins.recommended_monthly_cents, { whole: true })}{ins.monthly_payment_cents !== null && <small> · payée {money(ins.monthly_payment_cents, { whole: true })}</small>}</dd>
        </div>
        <div className="figure-item">
          <dt>Vs l'an dernier</dt>
          <dd>{ins.change_vs_last_year_percent === null ? "—" : `${ins.change_vs_last_year_percent > 0 ? "+" : ""}${ins.change_vs_last_year_percent} %`}</dd>
        </div>
      </dl>

      {reg !== null && Math.abs(reg) >= 6000 && (
        <div className={`notice ${reg > 0 ? "warn" : "ok"}`}>
          {reg > 0
            ? `À ce rythme, la régularisation annuelle te réclamera environ ${money(reg, { whole: true })}. Passe la mensualité à ${money(ins.recommended_monthly_cents, { whole: true })} ou mets l'écart de côté.`
            : `Tu paies environ ${money(-reg, { whole: true })} de trop par an : demande une baisse de mensualité ou attends le remboursement.`}
        </div>
      )}
      {ins.spike && <div className="notice warn">Consommation des 7 derniers jours nettement au-dessus de l'habitude (×{ins.spike_ratio}).</div>}
      <EnergySignals status={status} onChanged={onChanged} />

      {daily.length > 0 && (
        <figure className="energy-chart">
          <div className="bars" aria-label="Consommation quotidienne des 60 derniers jours">
            {daily.map((d) => <i key={d.day} style={{ height: `${Math.max(2, (d.wh / max) * 100)}%` }} title={`${shortDate(d.day)} : ${(d.wh / 1000).toFixed(1)} kWh`} />)}
          </div>
          <figcaption className="faint">60 derniers jours · moyenne {(ins.daily_average_wh / 1000).toFixed(1)} kWh/jour</figcaption>
        </figure>
      )}

      {ins.months.length > 1 && (
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Tableau, défile de côté"><table className="plain">
          <thead><tr><th>Mois</th><th className="num">kWh</th><th className="num">Coût estimé</th></tr></thead>
          <tbody>
            {ins.months.slice(-6).reverse().map((m) => (
              <tr key={m.month}>
                <td>{m.month}{m.days < 25 && <span className="faint"> ({m.days} j)</span>}</td>
                <td className="num">{Math.round(m.wh / 1000)}</td>
                <td className="num">{money(m.cost_cents, { whole: true })}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      <div>
        <p className="muted">Tarif utilisé : <b>{status.tariff.label}</b> — {(status.tariff.cents_per_100kwh / 10000).toFixed(4)} €/kWh, abonnement {money(status.tariff.yearly_subscription_cents, { whole: true })}/an.</p>
        <div className="pills">
          {status.tariff.options.map((o) => (
            <button key={o.id} type="button" className="pill" disabled={tBusy} aria-pressed={status.tariff.label === o.label}
                    onClick={() => applyTariff(o.cents_per_100kwh, o.yearly_subscription_cents, o.label)}>
              {o.label}
            </button>
          ))}
          <button type="button" className="pill" aria-pressed={custom} onClick={() => setCustom((v) => !v)}>Mon contrat</button>
        </div>
        {custom && (
          <form className="form-grid" style={{ marginTop: "0.75rem" }} onSubmit={(e) => { e.preventDefault(); applyTariff(price, sub ?? 0, "Mon contrat"); }}>
            <Field label="Prix du kWh TTC (€)">
              <input inputMode="decimal" value={(price / 10000).toString()} onChange={(e) => setPrice(Math.round(Number(e.target.value.replace(",", ".")) * 10000) || 0)} />
            </Field>
            <Field label="Abonnement par an TTC"><EuroInput value={sub} onChange={setSub} /></Field>
            <button className="btn" disabled={tBusy}>Enregistrer</button>
          </form>
        )}
        <p className="faint">{status.tariff.options[0]?.source}</p>
        {tError && <p className="error" role="alert">{tError}</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ comptes de l'État : documents
const FIELD_LABELS: Record<string, string> = {
  incomes_year: "Année des revenus",
  rfr_cents: "Revenu fiscal de référence",
  taxable_income_cents: "Revenu net imposable",
  parts: "Nombre de parts",
  income_tax_cents: "Impôt net",
  withholding_rate_percent: "Taux de prélèvement à la source",
  declarants: "Déclarants",
  prestations: "Prestations",
  month: "Mois",
  quotient_familial_cents: "Quotient familial",
  children: "Enfants à charge",
  daily_allowance_cents: "Allocation journalière",
  monthly_estimate_cents: "≈ par mois",
  end_of_rights: "Fin de droits",
  days_left: "Jours restants",
  allowance: "Allocation",
};

export function formatField(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (key === "prestations" && Array.isArray(value)) {
    return value.map((p) => `${(p as { label: string }).label} ${money((p as { monthly_cents: number }).monthly_cents)}`).join(" · ") || "aucune";
  }
  if (key.endsWith("_cents")) return money(Number(value));
  if (key.endsWith("_percent")) return `${String(value).replace(".", ",")} %`;
  if (key === "end_of_rights") return shortDate(String(value));
  return String(value);
}

function Etat() {
  const portals = useApi<Portal[]>("/api/documents/portals");
  const facts = useApi<OfficialFact[]>("/api/documents");
  const [preview, setPreview] = useState<ParsedDocument | null>(null);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const choose = (file: File | undefined) => {
    if (!file) return;
    void run(async () => {
      const form = new FormData();
      form.append("file", file);
      const parsed = await api<ParsedDocument>("/api/documents/preview", { method: "POST", form });
      setPreview(parsed);
      setEdits({});
    });
  };
  const apply = () => {
    if (!preview) return;
    void run(async () => {
      await api("/api/documents/apply", { method: "POST", body: { kind: preview.kind, fields: { ...preview.fields, ...edits } } });
      toast(`${preview.title} enregistré.`);
      setPreview(null);
      facts.reload();
    });
  };
  const remove = (id: number) =>
    void run(async () => {
      await api(`/api/documents/${id}`, { method: "DELETE" });
      facts.reload();
    });

  const editable = new Set(["incomes_year", "rfr_cents", "taxable_income_cents", "parts", "end_of_rights"]);

  return (
    <section className="section" aria-labelledby="etat-title" id="etat">
      <div className="section-head"><h2 id="etat-title"><Icon name="doc" /> Comptes de l'État</h2></div>
      <p className="section-note">
        Impôts, CAF et France Travail n'offrent pas de connexion directe à une application personnelle (FranceConnect
        est réservé aux services agréés). Le plus proche : tu télécharges tes documents depuis chaque espace, Bank en lit
        les chiffres utiles — et jette tout le reste (numéro fiscal, numéro d'allocataire, adresse ne sont jamais conservés).
      </p>
      <StateAccounts />
      <div className="portals">
        {portals.data?.map((p) => (
          <a key={p.id} className="portal" href={p.url} target="_blank" rel="noopener noreferrer">
            <b>{p.name}</b>
            <span>{p.what}</span>
            <small className="faint">{p.how}</small>
          </a>
        ))}
      </div>

      <div className="panel stack" style={{ marginTop: "1.25rem" }}>
        <label className="drop">
          <input className="sr-only" type="file" accept="application/pdf,.pdf" disabled={busy}
                 onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ""; }} />
          <span className="btn quiet" aria-hidden="true">{busy ? "Lecture…" : "Choisir un PDF"}</span>
          <span><b>Avis d'impôt, attestation CAF ou France Travail.</b> Aperçu d'abord : rien n'est enregistré sans ton accord.</span>
        </label>
        {preview && (
          <div className="stack" aria-live="polite">
            <h3>{preview.title}</h3>
            {preview.kind === "inconnu" ? (
              <div className="notice warn">{preview.warnings[0]}</div>
            ) : (
              <dl className="proposal-params">
                {Object.entries(preview.fields).map(([k, v]) => (
                  <div key={k}>
                    <dt>{FIELD_LABELS[k] ?? k}</dt>
                    <dd>
                      {editable.has(k) ? (
                        k.endsWith("_cents") ? (
                          <EuroInput value={typeof (edits[k] ?? v) === "number" ? (edits[k] ?? v) as number : null}
                                     onChange={(c) => setEdits((e) => ({ ...e, [k]: c ?? 0 }))} />
                        ) : (
                          <input value={String(edits[k] ?? v ?? "")} onChange={(e) => setEdits((x) => ({ ...x, [k]: k === "incomes_year" ? Number(e.target.value) : e.target.value }))} />
                        )
                      ) : formatField(k, v)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
            {preview.warnings.length > 0 && preview.kind !== "inconnu" && (
              <ul className="faint">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            )}
            {preview.discarded.length > 0 && (
              <p className="faint">Vu dans le document et <b>non conservé</b> : {preview.discarded.join(", ")}.</p>
            )}
            <div className="actions" style={{ marginTop: 0 }}>
              {preview.kind !== "inconnu" && <button className="btn" disabled={busy} onClick={apply}>Enregistrer ces chiffres</button>}
              <button className="btn link" type="button" onClick={() => setPreview(null)}>Annuler</button>
            </div>
          </div>
        )}
      </div>

      {facts.data && facts.data.length > 0 && (
        <ul className="connected" style={{ marginTop: "1.25rem" }}>
          {facts.data.map((f) => (
            <li key={f.id}>
              <span className="logo" aria-hidden="true">{f.kind === "avis_impot" ? "€" : f.kind === "caf" ? "CAF" : "FT"}</span>
              <div>
                <b>{f.title}</b>
                <div className="faint">
                  {Object.entries(f.fields).filter(([k]) => !["declarants", "monthly_estimate_cents"].includes(k)).slice(0, 4)
                    .map(([k, v]) => `${FIELD_LABELS[k] ?? k} : ${formatField(k, v)}`).join(" · ")}
                </div>
                <div className="faint">importé le {shortDate(f.imported_at)}</div>
              </div>
              <button className="btn link" disabled={busy} onClick={() => remove(f.id)}>Supprimer</button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
