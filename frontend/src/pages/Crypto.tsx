// Crypto : ton portefeuille sous verre. Lecture seule — des adresses publiques, jamais une clé : Bank ne peut
// rien signer ni envoyer. La valeur et sa composition d'abord (un ruban qui se dessine une fois), puis les
// actifs, les adresses suivies, les avoirs saisis, l'humeur du marché, le vérificateur d'arnaques, la fiscalité.
// Une phrase de récupération ou une clé collée par erreur est arrêtée ici, avant même de partir au serveur.

import { useMemo, useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type ConsentView,
  type CryptoAsset,
  type CryptoTax,
  type CryptoView,
  type ScamCheck,
} from "../api";
import { TrendChart } from "../components/charts/TrendChart";
import { CountUp } from "../components/CountUp";
import { ExchangeKeys } from "../components/ExchangeKeys";
import { Icon } from "../components/Icon";
import { Segmented } from "../components/Segmented";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { amountOnly, money, safeHref, shortDate } from "../format";

const SWATCH = 6; // nombre de teintes du ruban (--c1 … --c6), réutilisées dans l'ordre

import { guessChain, looksSecret, price, qty } from "../crypto-format";

export { guessChain, looksSecret, price, qty };

function change(pct: string | null): { text: string; tone: string } | null {
  if (pct === null) return null;
  const n = Number(pct);
  return { text: `${n > 0 ? "+" : ""}${n.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} %`, tone: n > 0 ? "up" : n < 0 ? "down" : "flat" };
}

export function Crypto() {
  const v = useApi<CryptoView>("/api/crypto");
  const toast = useToast();
  const [busy, error, run] = useAsyncAction();
  const [view, setView] = useState<CryptoView | null>(null);
  const data = view ?? v.data;
  if (v.error) return <ErrorNote message={v.error} onRetry={v.reload} />;
  if (!data) return <Loading />;
  const reload = () => {
    setView(null);
    v.reload();
  };

  const sync = () =>
    run(async () => {
      const r = await api<{ result: { wallets_synced: number; wallets_failed: number; prices: number }; view: CryptoView }>("/api/crypto/sync", { method: "POST" });
      setView(r.view);
      const { wallets_synced: ok, wallets_failed: ko, prices } = r.result;
      toast(ko ? `${ok} adresse(s) relevée(s), ${ko} en échec — détail sous chaque adresse.` : `Relevé : ${ok} adresse(s), ${prices} cours à jour.`);
    });

  const empty = data.assets.length === 0;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Crypto</h1>
          <p className="muted">Suivie en lecture seule : Bank ne détient aucune clé et ne peut rien envoyer.</p>
        </div>
        {data.network_consent && (data.wallets.length > 0 || data.manual.length > 0 || data.assets.length > 0) && (
          <button type="button" className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => void sync()}>
            <Icon name="undo" /> Relever maintenant
          </button>
        )}
      </div>
      {error && <ErrorNote message={error} />}

      <Vault data={data} />

      {data.guards.map((g) => (
        <p key={g.text} className={`notice ${g.level === "warning" ? "warn" : ""} reveal`} style={{ marginTop: "1rem" }}>
          <Icon name={g.level === "warning" ? "alert" : "shield"} /> <span>{g.text}</span>
        </p>
      ))}

      {!data.network_consent && <NetworkConsent onDone={reload} />}

      {!empty && <Holdings assets={data.assets} />}

      <div className="split cx-split" style={{ marginTop: "1rem" }}>
        <Wallets data={data} onChange={reload} />
        <Manual data={data} onChange={reload} />
      </div>

      <ExchangeKeys consent={data.network_consent} onChange={reload} />

      <div className="split cx-split" style={{ marginTop: "1rem" }}>
        <Mood sentiment={data.sentiment} />
        <Checker resources={data.resources} />
      </div>

      <Taxes initial={data.tax} currentValue={data.total_cents} />

      <section className="card cx-pilot reveal" style={{ marginTop: "1rem" }} aria-labelledby="cx-pilot">
        <div>
          <h2 id="cx-pilot">Pilote automatique</h2>
          <p className="muted">
            {data.autopilot.active
              ? `${data.autopilot.trial ? "En essai" : "En marche"} pour « ${data.autopilot.agent} »${data.autopilot.expires_at ? `, jusqu'au ${shortDate(data.autopilot.expires_at.slice(0, 10))}` : ""}.`
              : "Éteint : aucun mouvement sans toi. Tu peux laisser ton agent acheter ou envoyer seul, dans un cadre que tu fixes."}
          </p>
        </div>
        <a className="btn quiet" href="#/pilote">{data.autopilot.active ? "Voir le journal" : "Régler le pilote"} <Icon name="arrow" /></a>
      </section>

      <Settings data={data} onChange={setView} />

      <section className="card card-quiet" style={{ marginTop: "1rem" }} aria-labelledby="cx-help">
        <h2 id="cx-help" className="cx-small-title">Un doute, une arnaque ?</h2>
        <ul className="cx-links">
          {data.resources.map((r) => (
            <li key={r.label}>{safeHref(r.url) ? <a href={safeHref(r.url)!} target="_blank" rel="noreferrer noopener">{r.label} <Icon name="external" /></a> : r.label}</li>
          ))}
        </ul>
        <p className="faint" style={{ marginTop: "0.75rem" }}>{data.disclaimer}</p>
      </section>
    </>
  );
}

// ------------------------------------------------------------------ la valeur et son ruban
function Vault({ data }: { data: CryptoView }) {
  const delta = data.change_24h_cents;
  const history = data.history;
  return (
    <section className="card card-hero cx-vault reveal" aria-labelledby="cx-value">
      <p className="today-lead" id="cx-value">Ta crypto aujourd'hui</p>
      <p className="today-figure cx-figure">
        <CountUp value={Math.round(data.total_cents / 100)} format={(n) => amountOnly(n * 100)} /> €
      </p>
      {data.assets.length > 0 ? (
        <>
          <p className="muted cx-sub">
            {delta !== 0 && (
              <span className={`cx-delta ${delta > 0 ? "up" : "down"}`}>
                <Icon name={delta > 0 ? "trendUp" : "trendDown"} /> {money(delta, { signed: true })} en 24 h
              </span>
            )}
            <span>{data.in_wealth ? "Comptée dans ton patrimoine." : "Non comptée dans ton patrimoine."}</span>
          </p>
          <Ribbon assets={data.assets} />
          {history.length > 2 && (
            <div style={{ marginTop: "1.25rem" }}>
              <TrendChart points={history.map((p) => ({ date: p.day, value: p.value_cents }))} label="Valeur de la crypto" baseline="auto" />
            </div>
          )}
        </>
      ) : (
        <p className="muted">
          Rien de suivi pour l'instant. Ajoute une adresse publique (ton Ledger, MetaMask, Phantom…) ou saisis ce que tu as sur une
          plateforme : Bank calcule la valeur, la courbe et ce qu'il y aura à déclarer.
        </p>
      )}
    </section>
  );
}

export function Ribbon({ assets }: { assets: CryptoAsset[] }) {
  const shown = assets.filter((a) => a.value_cents > 0);
  if (shown.length === 0) return <p className="faint">En attente des cours pour dessiner la répartition.</p>;
  const label = shown.map((a) => `${a.asset} ${Math.round(a.share_percent)} %`).join(", ");
  return (
    <figure className="cx-ribbon" aria-label={`Répartition : ${label}`}>
      <div className="cx-ribbon-bar" role="img" aria-label={`Répartition : ${label}`}>
        {shown.map((a, i) => (
          <i key={a.asset} style={{ flexGrow: Math.max(a.share_percent, 0.8), ["--c" as string]: `var(--c${(i % SWATCH) + 1})`, ["--d" as string]: `${i * 70}ms` }} />
        ))}
      </div>
      <figcaption className="cx-ribbon-legend">
        {shown.map((a, i) => (
          <span key={a.asset} style={{ ["--c" as string]: `var(--c${(i % SWATCH) + 1})` }}>
            <i aria-hidden="true" />
            <b>{a.asset}</b> {a.share_percent.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

// ------------------------------------------------------------------ actifs
function Holdings({ assets }: { assets: CryptoAsset[] }) {
  return (
    <section className="card reveal" style={{ marginTop: "1rem" }} aria-labelledby="cx-assets">
      <div className="card-head"><h2 id="cx-assets">Tes actifs</h2><span className="faint">Cours CoinGecko, en euros</span></div>
      <ul className="cx-assets">
        {assets.map((a, i) => {
          const c = change(a.change_24h);
          return (
            <li key={a.asset} style={{ ["--c" as string]: `var(--c${(i % SWATCH) + 1})` }}>
              <span className="cx-ticker" aria-hidden="true">{a.asset.slice(0, 4)}</span>
              <div className="cx-asset-main">
                <b>{a.name}</b>
                <span className="faint" title={`${qty(a.quantity, true)} ${a.asset}`}>
                  {qty(a.quantity)} {a.asset} · {a.sources.map((s) => s.where).join(", ")}
                </span>
              </div>
              <div className="cx-asset-price">
                <span>{price(a.price_eur)}</span>
                {c && <span className={`cx-change ${c.tone}`}>{c.text}</span>}
                {a.stale && <span className="tag warn">{a.price_eur ? "cours ancien" : "sans cours"}</span>}
              </div>
              <b className="cx-asset-value">{money(a.value_cents)}</b>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ accord réseau
function NetworkConsent({ onDone }: { onDone: () => void }) {
  const consents = useApi<ConsentView[]>("/api/consents");
  const spec = consents.data?.find((c) => c.purpose === "crypto_network");
  const [busy, error, run] = useAsyncAction();
  return (
    <section className="card reveal cx-consent" style={{ marginTop: "1rem" }} aria-labelledby="cx-consent">
      <h2 id="cx-consent">Relever tes soldes automatiquement ?</h2>
      <p className="muted">{spec?.text ?? "Bank peut lire le solde de tes adresses publiques auprès de nœuds publics, avec ton accord."}</p>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy}
                onClick={() => void run(async () => { await api("/api/consents/crypto_network", { method: "PUT", body: { granted: true } }); onDone(); })}>
          Oui, relever mes adresses
        </button>
        <span className="faint">Sinon, saisis tes quantités à la main : tout le reste fonctionne.</span>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ adresses suivies
function Wallets({ data, onChange }: { data: CryptoView; onChange: () => void }) {
  const toast = useToast();
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [chain, setChain] = useState("");
  const [lookalike, setLookalike] = useState<string | null>(null);
  const [busy, error, run] = useAsyncAction();
  const secret = looksSecret(address) ?? looksSecret(label);
  const chosen = chain || guessChain(address) || "";

  const submit = (e: FormEvent, confirm = false) => {
    e.preventDefault();
    if (secret) return;
    void run(async () => {
      try {
        await api("/api/crypto/wallets", { method: "POST", body: { label, chain: chosen, address: address.trim(), confirm_lookalike: confirm } });
      } catch (err) {
        const msg = (err as Error).message;
        if (/empoisonnement/.test(msg)) {
          setLookalike(msg);
          return;
        }
        throw err;
      }
      setLabel("");
      setAddress("");
      setChain("");
      setLookalike(null);
      toast("Adresse suivie. Elle sera relevée au prochain passage.");
      onChange();
    });
  };
  const remove = (id: number, name: string) =>
    run(async () => {
      if (!window.confirm(`Ne plus suivre « ${name} » ? Ses soldes relevés disparaissent de Bank (rien ne change sur la blockchain).`)) return;
      await api(`/api/crypto/wallets/${id}`, { method: "DELETE" });
      onChange();
    });

  return (
    <section className="card reveal" aria-labelledby="cx-wallets">
      <div className="card-head"><h2 id="cx-wallets">Adresses suivies</h2><span className="faint">{data.wallets.length} / 20</span></div>
      {data.wallets.length === 0 ? (
        <p className="muted">Colle une adresse publique : celle qu'on te donne pour <em>recevoir</em>. Jamais tes 12 ou 24 mots.</p>
      ) : (
        <ul className="cx-wallets">
          {data.wallets.map((w) => (
            <li key={w.id}>
              <div>
                <b>{w.label}</b> <span className="tag">{w.chain_label}</span>
                <span className="cx-address" title={w.address}>{w.masked}</span>
                <span className="faint">{w.error ? <span className="cx-error">{w.error}</span> : w.synced_at ? `relevée le ${shortDate(w.synced_at.slice(0, 10))}` : "pas encore relevée"}</span>
              </div>
              <button type="button" className="btn link" onClick={() => void remove(w.id, w.label)} aria-label={`Ne plus suivre ${w.label}`}><Icon name="trash" /></button>
            </li>
          ))}
        </ul>
      )}
      <form className="stack" style={{ marginTop: "1rem" }} onSubmit={(e) => submit(e)}>
        <Field label="Nom">
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Ledger, MetaMask, Phantom…" maxLength={60} required />
        </Field>
        <Field label="Adresse publique" hint={chosen ? `Réseau : ${data.chains.find((c) => c.key === chosen)?.label ?? chosen}` : undefined}>
          <input value={address} onChange={(e) => { setAddress(e.target.value); setLookalike(null); }} placeholder="0x…, bc1…, ou adresse Solana"
                 autoComplete="off" autoCorrect="off" spellCheck={false} required aria-invalid={secret ? true : undefined} />
        </Field>
        {secret && (
          <p className="notice danger" role="alert">
            <Icon name="alert" /> <span>Stop : ceci ressemble à {secret}. Ne la colle jamais nulle part — Bank n'en a pas besoin et ne l'a pas reçue.
            Si quelqu'un te l'a demandée, c'est une arnaque : crée un nouveau portefeuille et déplaces-y tes fonds.</span>
          </p>
        )}
        <Field label="Réseau">
          <select value={chain} onChange={(e) => setChain(e.target.value)}>
            <option value="">{guessChain(address) ? "Deviné d'après l'adresse" : "Choisis"}</option>
            {data.chains.map((c) => <option key={c.key} value={c.key}>{c.label}{c.tokens.length ? ` (${c.native}, ${c.tokens.join(", ")})` : ` (${c.native})`}</option>)}
          </select>
        </Field>
        {lookalike && (
          <div className="notice warn" role="alert">
            <Icon name="alert" />
            <div>
              <p>{lookalike}</p>
              <button type="button" className="btn sm quiet" style={{ marginTop: "0.5rem" }} onClick={(e) => submit(e as unknown as FormEvent, true)}>
                J'ai comparé caractère par caractère : c'est bien la mienne
              </button>
            </div>
          </div>
        )}
        {error && <ErrorNote message={error} />}
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || !!secret || !chosen}><Icon name="plus" /> Suivre cette adresse</button>
      </form>
    </section>
  );
}

// ------------------------------------------------------------------ avoirs saisis
function Manual({ data, onChange }: { data: CryptoView; onChange: () => void }) {
  const [asset, setAsset] = useState("BTC");
  const [quantity, setQuantity] = useState("");
  const [platform, setPlatform] = useState("");
  const [foreign, setForeign] = useState(true);
  const [busy, error, run] = useAsyncAction();
  const known = data.catalog.some((c) => c.symbol === asset.toUpperCase());
  const [cg, setCg] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/crypto/holdings", { method: "POST", body: { asset, quantity, platform: platform || null, foreign: !!platform && foreign, coingecko_id: known ? null : cg || null } });
      setQuantity("");
      onChange();
    });
  };
  const remove = (id: number) => run(async () => { await api(`/api/crypto/holdings/${id}`, { method: "DELETE" }); onChange(); });

  return (
    <section className="card reveal" aria-labelledby="cx-manual">
      <div className="card-head"><h2 id="cx-manual">Sur une plateforme</h2><span className="faint">Saisie à la main</span></div>
      {data.manual.length === 0 ? (
        <p className="muted">Ce que tu as sur Coinbase, Kraken, Bitpanda… : la quantité suffit. Une plateforme étrangère se déclare (3916-bis).</p>
      ) : (
        <ul className="cx-wallets">
          {data.manual.map((m) => (
            <li key={m.id}>
              <div>
                <b>{qty(m.quantity)} {m.asset}</b> <span className="faint">{m.platform ?? "sans plateforme"}</span>
                {m.foreign && <span className="tag warn">étrangère · 3916-bis</span>}
              </div>
              <button type="button" className="btn link" onClick={() => void remove(m.id)} aria-label={`Retirer ${m.asset}`}><Icon name="trash" /></button>
            </li>
          ))}
        </ul>
      )}
      <form className="form-grid" style={{ marginTop: "1rem" }} onSubmit={submit}>
        <Field label="Actif">
          <input value={asset} onChange={(e) => setAsset(e.target.value.toUpperCase())} list="cx-catalog" maxLength={12} required />
          <datalist id="cx-catalog">{data.catalog.map((c) => <option key={c.symbol} value={c.symbol}>{c.name}</option>)}</datalist>
        </Field>
        <Field label="Quantité"><input value={quantity} onChange={(e) => setQuantity(e.target.value)} inputMode="decimal" placeholder="0,05" required /></Field>
        <Field label="Plateforme"><input value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="Kraken, Ledger…" maxLength={60} /></Field>
        {!known && asset.length >= 2 && (
          <Field label="Identifiant CoinGecko" hint="Pour avoir son cours (ex. pepe)"><input value={cg} onChange={(e) => setCg(e.target.value.toLowerCase())} maxLength={64} /></Field>
        )}
        {platform && (
          <label className="check"><input type="checkbox" checked={foreign} onChange={(e) => setForeign(e.target.checked)} /> Plateforme hors de France</label>
        )}
        {error && <ErrorNote message={error} />}
        <button className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="plus" /> Ajouter</button>
      </form>
    </section>
  );
}

// ------------------------------------------------------------------ humeur du marché
export function Mood({ sentiment }: { sentiment: CryptoView["sentiment"] }) {
  const value = sentiment?.value ?? null;
  const angle = value === null ? 0 : -90 + (value / 100) * 180;
  return (
    <section className="card reveal" aria-labelledby="cx-mood">
      <div className="card-head"><h2 id="cx-mood">Humeur du marché</h2><span className="faint">Indice peur et avidité</span></div>
      {value === null ? (
        <p className="muted">Relevée avec les cours, si tu as donné ton accord au suivi.</p>
      ) : (
        <div className="cx-mood">
          <svg viewBox="0 0 200 112" role="img" aria-label={`Humeur du marché : ${value} sur 100, ${sentiment?.label}`}>
            <defs>
              <linearGradient id="cx-mood-grad" x1="0" x2="1">
                <stop offset="0" stopColor="var(--negative)" />
                <stop offset="0.5" stopColor="var(--warning)" />
                <stop offset="1" stopColor="var(--positive)" />
              </linearGradient>
            </defs>
            <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#cx-mood-grad)" strokeWidth="14" strokeLinecap="round" />
            <g className="cx-needle" style={{ transform: `rotate(${angle}deg)` }}>
              <line x1="100" y1="100" x2="100" y2="34" stroke="var(--ink)" strokeWidth="3" strokeLinecap="round" />
            </g>
            <circle cx="100" cy="100" r="6" fill="var(--ink)" />
          </svg>
          <p><b className="cx-mood-value">{value}</b> <span>{sentiment?.label}</span></p>
        </div>
      )}
      <p className="faint">Un thermomètre de l'humeur des marchés, pas un signal d'achat ou de vente.</p>
    </section>
  );
}

// ------------------------------------------------------------------ vérificateur
function Checker({ resources }: { resources: CryptoView["resources"] }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<ScamCheck | null>(null);
  const [busy, error, run] = useAsyncAction();
  const local = looksSecret(text, { words: false });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (local) {
      setResult({ level: "danger", verdict: `Ce texte contient ${local} : ne l'envoie à personne. (Il n'est pas parti de ton navigateur.)`, signals: [], advice: [], resources });
      return;
    }
    void run(async () => setResult(await api<ScamCheck>("/api/crypto/check", { method: "POST", body: { text } })));
  };
  return (
    <section className="card reveal" aria-labelledby="cx-check">
      <div className="card-head"><h2 id="cx-check">C'est une arnaque ?</h2><span className="faint">Rien n'est gardé</span></div>
      <form className="stack" onSubmit={submit}>
        <Field label="Colle le message, l'offre ou l'adresse reçue">
          <textarea value={text} onChange={(e) => { setText(e.target.value); setResult(null); }} rows={4} maxLength={5000}
                    placeholder="« Bonjour, ici le support Ledger… »" required />
        </Field>
        {error && <ErrorNote message={error} />}
        <button className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="shield" /> Vérifier</button>
      </form>
      {result && (
        <div className={`cx-verdict is-${result.level}`} role="status">
          <p><b>{result.verdict}</b></p>
          {result.signals.length > 0 && <ul>{result.signals.map((s) => <li key={s.key + s.label}>{s.label}</li>)}</ul>}
          {result.level !== "ok" && (
            <p className="faint">Info Escroqueries : 0 805 805 817 (gratuit). Signalement : internet-signalement.gouv.fr.</p>
          )}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ fiscalité
function Taxes({ initial, currentValue }: { initial: CryptoTax; currentValue: number }) {
  const [tax, setTax] = useState(initial);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"acquisition" | "cession">("acquisition");
  const today = new Date().toISOString().slice(0, 10);
  const [day, setDay] = useState(today);
  const [amount, setAmount] = useState<number | null>(null);
  const [fees, setFees] = useState<number | null>(0);
  const [portfolio, setPortfolio] = useState<number | null>(null);
  const [busy, error, run] = useAsyncAction();
  const progress = Math.min(100, (tax.total_price_cents / tax.threshold_cents) * 100);
  const rate = tax.rate?.total_percent ? `${tax.rate.total_percent.replace(".", ",")} %` : null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<{ tax: CryptoTax }>("/api/crypto/tax", {
        method: "POST",
        body: { kind, day, amount_cents: amount, fees_cents: fees ?? 0, portfolio_value_cents: kind === "cession" ? portfolio : null },
      });
      setTax(r.tax);
      setAmount(null);
      setPortfolio(null);
    });
  };
  const remove = (id: number) =>
    run(async () => {
      await api(`/api/crypto/tax/${id}`, { method: "DELETE" });
      setTax(await api<CryptoTax>(`/api/crypto/tax?year=${tax.year}`));
    });

  return (
    <section className="card reveal" style={{ marginTop: "1rem" }} aria-labelledby="cx-tax">
      <div className="card-head"><h2 id="cx-tax">Impôts {tax.year}</h2><span className="faint">Article 150 VH bis</span></div>
      {tax.error && <p className="notice warn"><Icon name="alert" /> <span>{tax.error}</span></p>}
      <div className="cx-tax-grid">
        <div>
          <p className="faint">Ventes contre euros cette année</p>
          <p className="cx-tax-figure">{money(tax.total_price_cents, { whole: true })}</p>
          <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={tax.threshold_cents} aria-valuenow={tax.total_price_cents}
               aria-label="Ventes par rapport au seuil de 305 €" data-status={tax.exempt ? undefined : "warning"}>
            <i style={{ width: `${progress}%` }} />
          </div>
          <p className="faint">{tax.exempt ? `Sous ${money(tax.threshold_cents, { whole: true })} de ventes : plus-values exonérées (le 2086 reste à joindre s'il y a eu des ventes).` : `Au-delà de ${money(tax.threshold_cents, { whole: true })} : la plus-value nette est imposable.`}</p>
        </div>
        <div>
          <p className="faint">Plus-value nette</p>
          <p className={`cx-tax-figure ${tax.net_gain_cents < 0 ? "down" : ""}`}>{money(tax.net_gain_cents, { whole: true, signed: tax.net_gain_cents !== 0 })}</p>
          <p className="faint">
            {tax.estimated_tax_cents !== null && rate
              ? `≈ ${money(tax.estimated_tax_cents, { whole: true })} d'impôt et prélèvements (${rate}), sauf option pour le barème.`
              : tax.rate?.note ?? "Taux non référencé pour cette année : vérifie dans ta déclaration."}
          </p>
        </div>
        <div>
          <p className="faint">Prix d'achat encore « dans » ton portefeuille</p>
          <p className="cx-tax-figure">{money(tax.acquisition_basis_cents, { whole: true })}</p>
          {tax.unrealized_cents !== null && (
            <p className="faint">Latent : {money(tax.unrealized_cents, { whole: true, signed: true })} (pas imposé tant que tu ne vends pas contre des euros).</p>
          )}
        </div>
      </div>
      <ul className="cx-reminders">{tax.reminders.map((r) => <li key={r}>{r}</li>)}</ul>
      {tax.rate?.note && <p className="faint">{tax.rate.note} (vérifié le {shortDate(tax.rate.checked)})</p>}

      <details className="cx-details" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary>Tes achats et ventes en euros ({tax.events.length})</summary>
        {tax.events.length > 0 && (
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Tableau, défile de côté"><table className="plain">
            <thead><tr><th>Date</th><th>Type</th><th className="num">Montant</th><th className="num">Plus-value</th><th /></tr></thead>
            <tbody>
              {tax.events.map((e) => {
                const c = tax.cessions.find((x) => x.id === e.id);
                return (
                  <tr key={e.id}>
                    <td>{shortDate(e.day)}</td>
                    <td>{e.kind === "acquisition" ? "Achat" : "Vente"}{e.asset ? ` ${e.asset}` : ""}</td>
                    <td className="num">{money(e.amount_cents)}</td>
                    <td className="num">{c ? money(c.gain_cents, { signed: true }) : "—"}</td>
                    <td><button type="button" className="btn link" onClick={() => void remove(e.id)} aria-label="Supprimer"><Icon name="trash" /></button></td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
        <form className="form-grid" style={{ marginTop: "1rem" }} onSubmit={submit}>
          <Segmented value={kind} onChange={setKind} label="Type" options={[{ value: "acquisition", label: "Achat en euros" }, { value: "cession", label: "Vente contre euros" }]} />
          <Field label="Date"><input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} required /></Field>
          <Field label={kind === "acquisition" ? "Prix payé" : "Prix de vente"}><EuroInput value={amount} onChange={setAmount} required /></Field>
          <Field label="Frais"><EuroInput value={fees} onChange={setFees} /></Field>
          {kind === "cession" && (
            <Field label="Valeur de tout ton portefeuille juste avant" hint={currentValue > 0 && day === today ? `Bank suit ${money(currentValue, { whole: true })} aujourd'hui` : "Tous tes actifs crypto, au moment de la vente"}>
              <EuroInput value={portfolio} onChange={setPortfolio} required />
            </Field>
          )}
          {kind === "cession" && currentValue > 0 && day === today && portfolio === null && (
            <button type="button" className="btn sm link" onClick={() => setPortfolio(currentValue)}>Reprendre la valeur suivie</button>
          )}
          {error && <ErrorNote message={error} />}
          <button className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy || !amount}><Icon name="plus" /> Noter</button>
        </form>
        <p className="faint" style={{ marginTop: "0.75rem" }}>Un échange crypto contre crypto ne se note pas : il n'est pas imposable.</p>
      </details>
      <ul className="cx-links" style={{ marginTop: "0.75rem" }}>
        {tax.sources.map((s) => <li key={s.url}><a href={safeHref(s.url) ?? "#"} target="_blank" rel="noreferrer noopener">{s.label} <Icon name="external" /></a></li>)}
      </ul>
      <p className="faint">{tax.disclaimer}</p>
    </section>
  );
}

// ------------------------------------------------------------------ réglages
function Settings({ data, onChange }: { data: CryptoView; onChange: (v: CryptoView) => void }) {
  // Un nœud perso n'est jamais renvoyé en entier (il porte souvent une clé d'accès) : on en saisit un nouveau,
  // on ne réécrit pas l'ancien.
  const [nodes, setNodes] = useState<Record<string, string>>({});
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const chains = useMemo(() => Object.keys(data.endpoints), [data.endpoints]);
  const typed = Object.fromEntries(Object.entries(nodes).filter(([, v]) => v.trim()));
  const save = (body: Record<string, unknown>, done = "Enregistré.") =>
    run(async () => {
      onChange(await api<CryptoView>("/api/crypto/settings", { method: "PUT", body }));
      setNodes({});
      setPassword("");
      toast(done);
    });
  return (
    <details className="card cx-details" style={{ marginTop: "1rem" }}>
      <summary>Réglages du suivi</summary>
      <label className="check" style={{ marginTop: "0.75rem" }}>
        <input type="checkbox" checked={data.in_wealth} onChange={(e) => void save({ in_wealth: e.target.checked })} />
        Compter ma crypto dans mon patrimoine (jamais dans l'argent disponible)
      </label>
      <p className="muted" style={{ marginTop: "1rem" }}>
        Ton propre nœud, en https, est le plus discret : personne d'autre ne voit tes adresses. Son adresse est
        chiffrée et n'est plus jamais affichée en entier.
      </p>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          if (Object.keys(typed).length) void save({ endpoints: typed, password }, "Nœuds enregistrés.");
        }}
      >
        {chains.map((c) => {
          const now = data.endpoints[c] ?? { custom: false, shown: "" };
          return (
            <Field key={c} label={data.chains.find((x) => x.key === c)?.label ?? c} hint={now.custom ? `Ton nœud : ${now.shown}` : undefined}>
              <div className="cx-node">
                <input
                  value={nodes[c] ?? ""}
                  placeholder={now.custom ? "Remplacer par…" : now.shown}
                  onChange={(e) => setNodes({ ...nodes, [c]: e.target.value })}
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                />
                {now.custom && (
                  <button type="button" className="btn quiet small" disabled={busy}
                    onClick={() => void save({ endpoints: { [c]: null } }, "Retour au nœud public.")}>
                    Nœud public
                  </button>
                )}
              </div>
            </Field>
          );
        })}
        {Object.keys(typed).length > 0 && (
          <Field label="Ton mot de passe" hint="Envoyer tes adresses à un autre nœud se confirme.">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </Field>
        )}
        {error && <ErrorNote message={error} />}
        <button className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy || !Object.keys(typed).length}>Enregistrer les nœuds</button>
      </form>
    </details>
  );
}
