// Crypto › « Plateformes reliées » : une clé d'API en lecture seule par compte (Kraken, Binance, Coinbase). Bank
// vérifie les droits AVANT de garder quoi que ce soit — droits renvoyés par la plateforme, ou opérations inoffensives
// qui doivent échouer — et refuse une clé qui peut retirer ou passer un ordre. La clé est chiffrée ; l'écran n'en
// montre que la fin ; les soldes relevés rejoignent le portefeuille et se relèvent avec les adresses suivies.

import { useState, type FormEvent } from "react";
import { api, useApi, type ExchangeKeysView, type ExchangeKeyView, type ExchangePlatform, type ExchangePlatformKey } from "../api";
import { shortDate } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Field, useAsyncAction, useToast } from "./ui";
import { looksSecret, qty } from "../crypto-format";

export function rightsText(k: ExchangeKeyView): string {
  const r = k.rights;
  if (r.trade || r.withdraw) return "droits trop larges";
  const how = r.source === "probes" ? "vérifiée par sondes" : "droits lus sur la plateforme";
  return `lecture seule · ${how}`;
}

export function syncText(k: ExchangeKeyView): string {
  if (k.error) return k.error;
  if (!k.synced_at) return "jamais relevée";
  const d = k.synced_at.slice(0, 10);
  return `relevée le ${shortDate(d)} ${d.slice(0, 4)} à ${k.synced_at.slice(11, 16)} UTC`;
}

export function ExchangeKeys({ consent, onChange }: { consent: boolean; onChange: () => void }) {
  const st = useApi<ExchangeKeysView>("/api/crypto/exchanges");
  const [adding, setAdding] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const [working, setWorking] = useState<number | null>(null);
  const toast = useToast();
  const data = st.data;
  const reload = () => { st.reload(); onChange(); };
  const sync = (k: ExchangeKeyView) =>
    void run(async () => {
      setWorking(k.id);
      try {
        const out = await api<ExchangeKeysView>(`/api/crypto/exchanges/${k.id}/sync`, { method: "POST" });
        const fresh = out.keys.find((x) => x.id === k.id);
        toast(fresh?.error ? `${k.label} : ${fresh.error}` : `${k.label} : soldes relevés.`);
        reload();
      } finally {
        setWorking(null);
      }
    });
  const revoke = (k: ExchangeKeyView) =>
    void run(async () => {
      if (!window.confirm(`Retirer la clé « ${k.label} » ? Ses soldes disparaissent de Bank. Pense aussi à la supprimer sur ${k.platform_label}.`)) return;
      await api(`/api/crypto/exchanges/${k.id}`, { method: "DELETE" });
      toast("Clé retirée.");
      reload();
    });
  return (
    <section className="card section reveal xk" aria-labelledby="xk-title">
      <div className="card-head">
        <div>
          <h2 id="xk-title"><Icon name="lock" /> Plateformes reliées</h2>
          <p className="muted">Une clé d'API en lecture seule par compte : Bank vérifie qu'elle ne peut ni retirer ni passer d'ordre avant de la garder, puis relève tes soldes avec tes adresses.</p>
        </div>
        {data && (
          <button type="button" className="btn quiet sm" aria-expanded={adding} onClick={() => setAdding((v) => !v)} disabled={!consent || data.keys.length >= data.max_keys}>
            <Icon name={adding ? "close" : "plus"} /> {adding ? "Fermer" : "Relier une plateforme"}
          </button>
        )}
      </div>
      {st.error && <ErrorNote message={st.error} onRetry={st.reload} />}
      {!consent && <p className="muted">Donne d'abord ton accord pour les relevés automatiques (plus haut) : une clé ne sert qu'à interroger la plateforme.</p>}
      {data && data.keys.length === 0 && !adding && consent && (
        <p className="muted">Aucune plateforme reliée. Kraken, Binance ou Coinbase : crée une clé avec les seuls droits de lecture, colle-la ici, Bank fait le reste.</p>
      )}
      {data && data.keys.length > 0 && (
        <ul className="xk-list">
          {data.keys.map((k) => (
            <li key={k.id} className="xk-item" data-platform={k.platform}>
              <div className="xk-text">
                <b>{k.label}</b> <span className="tag">{k.platform_label}</span> <span className="faint xk-hint">{k.key_hint}</span>
                {k.foreign && <span className="tag warn">étrangère · 3916-bis</span>}
                <p className="muted">
                  <Icon name={k.error ? "alert" : "shield"} /> {rightsText(k)} · {syncText(k)}
                  {k.rights.ip_restricted === false && " · pense à restreindre la clé à l'adresse IP de ton serveur"}
                </p>
                <p className="faint">{k.assets.length ? k.assets.map((a) => `${qty(a.quantity)} ${a.asset}`).join(" · ") : "aucun solde"}</p>
              </div>
              <div className="xk-do">
                <button type="button" className={`btn sm quiet${working === k.id ? " is-loading" : ""}`} disabled={busy || !consent} onClick={() => sync(k)}><Icon name="undo" /> Relever</button>
                <button type="button" className="btn sm quiet" disabled={busy} onClick={() => revoke(k)}>Retirer</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && <ErrorNote message={error} />}
      {adding && data && <ConnectForm platforms={data.platforms} onDone={() => { setAdding(false); reload(); }} />}
    </section>
  );
}

function ConnectForm({ platforms, onDone }: { platforms: ExchangePlatform[]; onDone: () => void }) {
  const [platform, setPlatform] = useState<ExchangePlatformKey>(platforms[0]?.key ?? "kraken");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [foreign, setForeign] = useState(true);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const info = platforms.find((p) => p.key === platform) ?? platforms[0];
  const pasted = looksSecret(apiKey) ?? looksSecret(label) ?? (platform === "coinbase" ? null : looksSecret(secret));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (pasted) return;
    void run(async () => {
      const out = await api<ExchangeKeysView>("/api/crypto/exchanges", {
        method: "POST",
        body: { password, platform, label, api_key: apiKey.trim(), api_secret: secret.trim(), foreign },
      });
      const created = out.keys.find((k) => k.id === out.created_id);
      toast(`${created?.label ?? info?.label} reliée : ${created?.assets.length ?? 0} actif(s) relevé(s).`);
      setApiKey(""); setSecret(""); setPassword("");
      onDone();
    });
  };
  return (
    <form className="form-grid xk-form" onSubmit={submit} aria-label="Relier une plateforme">
      <Field label="Plateforme">
        <select value={platform} onChange={(e) => setPlatform(e.target.value as ExchangePlatformKey)}>
          {platforms.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
      </Field>
      <Field label="Nom" hint="pour la reconnaître (ex. « Kraken perso »)"><input value={label} maxLength={60} placeholder={info?.label} onChange={(e) => setLabel(e.target.value)} /></Field>
      {info && (
        <p className="muted xk-help">
          {info.help} <a href={info.url} target="_blank" rel="noreferrer noopener">Créer la clé sur {info.label} <Icon name="external" /></a>
        </p>
      )}
      <Field label={info?.key_label ?? "API Key"}><input value={apiKey} required maxLength={400} autoComplete="off" spellCheck={false} onChange={(e) => setApiKey(e.target.value)} /></Field>
      <Field label={info?.secret_label ?? "Secret"} hint="jamais affiché ensuite ; chiffré dans Bank">
        {platform === "coinbase"
          ? <textarea value={secret} required rows={4} maxLength={4000} autoComplete="off" spellCheck={false} placeholder="-----BEGIN EC PRIVATE KEY-----" onChange={(e) => setSecret(e.target.value)} />
          : <input type="password" value={secret} required maxLength={4000} autoComplete="off" onChange={(e) => setSecret(e.target.value)} />}
      </Field>
      <label className="check"><input type="checkbox" checked={foreign} onChange={(e) => setForeign(e.target.checked)} /> Compte tenu hors de France (à déclarer : 3916-bis)</label>
      <Field label="Ton mot de passe Bank" hint="exigé pour garder une clé"><input type="password" value={password} required autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} /></Field>
      {pasted && <p className="notice warn" role="alert"><Icon name="alert" /><span>Ceci ressemble à {pasted} : Bank ne garde jamais une clé de portefeuille. Il faut une clé d'API en lecture seule.</span></p>}
      {error && <ErrorNote message={error} />}
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !!pasted}><Icon name="shield" /> Vérifier et relier</button>
    </form>
  );
}
