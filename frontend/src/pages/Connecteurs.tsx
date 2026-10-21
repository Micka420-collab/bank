// Connecteurs : tout ce qui alimente Bank en quelques clics — banques (DSP2), compteur Linky, documents
// de l'État (impôts, CAF, France Travail), et les agents IA.

import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type Connection,
  type EnergyStatus,
  type OfficialFact,
  type ParsedDocument,
  type Portal,
} from "../api";
import { BANK_BRANDS, brandOf, initials, matchBrand, searchAspsps, type BankBrand } from "../banks";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, shortDate } from "../format";
import { AgentTokens } from "./Agents";

export function Connecteurs({ bankResult }: { bankResult: string | null }) {
  return (
    <>
      <div className="page-head">
        <h1>Connecteurs</h1>
        <p className="muted">Branche tes sources en quelques clics : plus Bank en sait, plus ses conseils sont justes.</p>
      </div>
      <Banks result={bankResult} />
      <Linky />
      <Etat />
      <AgentTokens />
    </>
  );
}

// ------------------------------------------------------------------ banques (DSP2) en un clic
export function Banks({ result }: { result: string | null }) {
  const status = useApi<{ enabled: boolean; connections: Connection[] }>("/api/banks");
  const [aspsps, setAspsps] = useState<string[] | null>(null);
  const [picked, setPicked] = useState<BankBrand | null>(null);
  const [query, setQuery] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  useEffect(() => {
    if (result === "connected") toast("Banque connectée. Lance une synchronisation pour récupérer tes opérations.");
    if (result === "refused") toast("Connexion annulée côté banque.");
    if (result === "error") toast("La connexion a échoué ou a expiré : recommence.");
  }, [result, toast]);

  useEffect(() => {
    if (status.data?.enabled && aspsps === null) {
      void run(async () => {
        const list = await api<{ name: string }[]>("/api/banks/aspsps?country=FR");
        setAspsps(list.map((a) => a.name));
      });
    }
  }, [status.data, aspsps, run]);

  const connect = (name: string) =>
    void run(async () => {
      const r = await api<{ redirect_url: string }>("/api/banks/connect", { method: "POST", body: { aspsp_name: name, country: "FR" } });
      const url = new URL(r.redirect_url);
      if (url.protocol !== "https:") throw new Error("Adresse de redirection refusée");
      window.location.assign(url.toString());
    });
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

  const names = aspsps ?? [];
  const brands = useMemo(
    () => BANK_BRANDS.map((b) => ({ brand: b, names: matchBrand(b, names) })).filter((x) => x.names.length > 0),
    [names],
  );
  const results = useMemo(() => searchAspsps(query, names), [query, names]);
  const choices = picked ? matchBrand(picked, names) : [];

  return (
    <section className="section" aria-labelledby="banks-title" id="banques">
      <h2 id="banks-title">Banques</h2>
      <p className="section-note">
        Lecture seule par DSP2, via Enable Banking (établissement agréé). Tu t'authentifies chez ta banque, Bank ne voit
        jamais tes identifiants. Accès valable 90 jours, renouvelable.{" "}
        <a href="#/operations">Sans connexion, l'import de relevés fonctionne pour toutes les banques.</a>
      </p>
      {status.error && <ErrorNote message={status.error} onRetry={status.reload} />}
      {status.data && !status.data.enabled && (
        <div className="notice">
          La synchronisation n'est pas configurée sur le serveur (BANK_ENABLEBANKING_APP_ID et clé privée) : voir le README.
          En attendant, importe tes relevés CSV / OFX depuis Opérations.
        </div>
      )}
      {status.data && status.data.connections.length > 0 && (
        <ul className="connected">
          {status.data.connections.map((c) => (
            <li key={c.id}>
              <span className="logo" aria-hidden="true">{initials(c.aspsp_name)}</span>
              <div>
                <b>{c.aspsp_name}</b>
                <div className="faint">
                  {c.accounts.join(", ") || "comptes à venir"} ·{" "}
                  <span className={`tag ${c.status === "active" ? "ok" : c.status === "expired" ? "warn" : ""}`}>
                    {{ active: "active", expired: "à renouveler", revoked: "coupée", error: "erreur", pending: "en attente" }[c.status] ?? c.status}
                  </span>
                  {c.valid_until && c.status === "active" && ` jusqu'au ${shortDate(c.valid_until)}`}
                  {c.last_sync_at ? ` · synchro ${shortDate(c.last_sync_at)}` : " · jamais synchronisée"}
                </div>
                {c.last_error && <div className="error">{c.last_error}</div>}
              </div>
              {c.status === "active" && (
                <span className="actions" style={{ marginTop: 0 }}>
                  <button className="btn quiet" disabled={busy} onClick={() => sync(c.id)}>Synchroniser</button>
                  <button className="btn danger" disabled={busy} onClick={() => revoke(c.id)}>Couper</button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {status.data?.enabled && (
        <>
          <h3 style={{ marginTop: "1.5rem" }}>Connecter une banque</h3>
          {aspsps === null ? (
            <Loading what="Liste des banques" />
          ) : (
            <>
              <div className="tiles" role="list" aria-label="Banques courantes">
                {brands.map(({ brand, names: n }) => (
                  <button
                    key={brand.id}
                    type="button"
                    role="listitem"
                    className="tile"
                    aria-pressed={picked?.id === brand.id}
                    disabled={busy}
                    onClick={() => (n.length === 1 ? connect(n[0]!) : setPicked(picked?.id === brand.id ? null : brand))}
                  >
                    <span className="logo" aria-hidden="true">{initials(brand.name)}</span>
                    <span>{brand.name}</span>
                    {n.length > 1 && <small className="faint">{n.length} caisses</small>}
                  </button>
                ))}
              </div>
              {picked && choices.length > 1 && (
                <div className="panel" style={{ marginTop: "1rem" }}>
                  <p><b>{picked.name}</b> : laquelle ?</p>
                  <div className="pills">
                    {choices.map((n) => (
                      <button key={n} type="button" className="pill" disabled={busy} onClick={() => connect(n)}>
                        {n.replace(new RegExp(picked.name, "i"), "").trim() || n}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <Field label="Une autre banque ?" hint={`${names.length} établissements disponibles`}>
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tape le nom de ta banque" />
              </Field>
              {results.length > 0 && (
                <ul className="results">
                  {results.map((n) => (
                    <li key={n}>
                      <button type="button" className="btn link" disabled={busy} onClick={() => connect(n)}>
                        <span className="logo small" aria-hidden="true">{initials(brandOf(n)?.name ?? n)}</span>
                        {n}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
      {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
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
      <h2 id="linky-title">Électricité (Linky)</h2>
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

      {daily.length > 0 && (
        <figure className="energy-chart">
          <div className="bars" aria-label="Consommation quotidienne des 60 derniers jours">
            {daily.map((d) => <i key={d.day} style={{ height: `${Math.max(2, (d.wh / max) * 100)}%` }} title={`${shortDate(d.day)} : ${(d.wh / 1000).toFixed(1)} kWh`} />)}
          </div>
          <figcaption className="faint">60 derniers jours · moyenne {(ins.daily_average_wh / 1000).toFixed(1)} kWh/jour</figcaption>
        </figure>
      )}

      {ins.months.length > 1 && (
        <table className="plain">
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
        </table>
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
      <h2 id="etat-title">Comptes de l'État</h2>
      <p className="section-note">
        Impôts, CAF et France Travail n'offrent pas de connexion directe à une application personnelle (FranceConnect
        est réservé aux services agréés). Le plus proche : tu télécharges tes documents depuis chaque espace, Bank en lit
        les chiffres utiles — et jette tout le reste (numéro fiscal, numéro d'allocataire, adresse ne sont jamais conservés).
      </p>
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
