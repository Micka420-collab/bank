// Veille : Bank lit des sources publiques (ministère de l'Économie, versions du moteur de calcul des aides, flux
// que tu ajoutes) et te signale ce qui change pour toi. Chaque élément dit d'où il vient, quand il a été publié,
// quand Bank l'a vu et revérifié, et s'il a été modifié depuis. Les données de référence de Bank (taux, barèmes,
// sources des démarches) portent une date de revérification : passée, c'est signalé ici.

import { useState, type FormEvent } from "react";
import { api, useApi, type ReferenceCheck, type WatchItemView, type WatchListing, type WatchSourceView } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { plural, safeHref, shortDate } from "../format";
import { fullDate } from "./Documents";

const TRUST: Record<string, string> = {
  officiel: "Source officielle",
  technique: "Source technique",
  secondaire: "Source secondaire",
  perso: "Ta source",
};

function when(iso: string | null): string {
  return iso ? fullDate(iso) : "—";
}

export function Watch() {
  const listing = useApi<WatchListing>("/api/watch");
  const [data, setData] = useState<WatchListing | null>(null);
  const [onlyMine, setOnlyMine] = useState(true);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const view = data ?? listing.data;
  if (listing.error) return <ErrorNote message={listing.error} onRetry={listing.reload} />;
  if (!view) return <Loading what="Ouverture de la veille" />;
  const refresh = () =>
    void run(async () => {
      const r = await api<WatchListing>("/api/watch/refresh", { method: "POST", body: { force: true } });
      setData(r);
      const rep = r.report;
      if (rep?.skipped) toast("Veille désactivée : rien n'a été relevé.");
      else if (rep) toast(rep.errors ? `${plural(rep.errors, "source injoignable", "sources injoignables")} : voir plus bas.` : `${plural(rep.new, "nouveauté", "nouveautés")}, ${plural(rep.changed, "mise à jour", "mises à jour")}.`);
    });
  const reload = () => {
    setData(null);
    listing.reload();
  };
  const items = view.items.filter((i) => !onlyMine || i.relevant);
  const stale = view.references.filter((r) => r.status === "stale");
  const fresh = view.references.filter((r) => r.status !== "stale");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Veille</h1>
          <p className="muted">Bank lit des sources publiques et te signale ce qui change pour toi. Rien te concernant ne quitte ton serveur : le rapprochement avec ta situation se fait ici.</p>
        </div>
        <button type="button" className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy || !view.enabled} onClick={refresh}><Icon name="news" /> Relever maintenant</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!view.enabled && (
        <p className="notice warn"><Icon name="alert" /><span>La veille est désactivée. <a href="#/reglages">Réactive-la dans Réglages › Confidentialité</a> pour que Bank relève les sources.</span></p>
      )}

      {view.engine && (
        <section className={`card watch-engine${view.engine.outdated ? " is-outdated" : ""}`} aria-labelledby="engine-title">
          <h2 id="engine-title"><Icon name="spark" /> Moteur de calcul des aides</h2>
          <p>
            {view.engine.used ? <>Ta version : <b>{view.engine.used}</b> · </> : null}
            Dernière publiée : <b>{view.engine.latest}</b>{view.engine.latest_published_at ? ` (le ${fullDate(view.engine.latest_published_at)})` : ""}.
          </p>
          {view.engine.advice ? <p className="muted">{view.engine.advice}</p> : <p className="faint">{view.engine.used ? "À jour." : "Lance une simulation de tes aides pour comparer."}</p>}
        </section>
      )}

      <section className="section" aria-labelledby="news-title">
        <div className="section-head">
          <h2 id="news-title"><Icon name="news" /> Ce qui change</h2>
          <div className="chips" role="group" aria-label="Filtrer" style={{ marginBottom: 0 }}>
            <button type="button" className="chip" aria-pressed={onlyMine} onClick={() => setOnlyMine(true)}>Pour toi <span className="chip-count">{view.items.filter((i) => i.relevant).length}</span></button>
            <button type="button" className="chip" aria-pressed={!onlyMine} onClick={() => setOnlyMine(false)}>Tout <span className="chip-count">{view.items.length}</span></button>
          </div>
        </div>
        <p className="section-note">
          Tes thèmes : {view.interests.filter((t) => t !== "moteur").map((t) => view.interest_labels[t] ?? t).join(", ")}.
          {view.counts.new > 0 && <> <button type="button" className="btn link" onClick={() => void run(async () => { await api("/api/watch/items/seen", { method: "POST" }); reload(); })}>Tout marquer comme lu</button></>}
        </p>
        {items.length === 0 ? (
          <p className="muted">{view.items.length === 0 ? "Rien de relevé pour l'instant." : "Rien de nouveau qui te concerne. Regarde « Tout » pour le reste."}</p>
        ) : (
          <ul className="watch-items">{items.map((i) => <Item key={i.id} item={i} onChange={reload} />)}</ul>
        )}
      </section>

      <section className="section" aria-labelledby="refs-title">
        <div className="section-head">
          <h2 id="refs-title"><Icon name="clock" /> Données de référence de Bank</h2>
          {stale.length > 0 && <span className="tag warn">{plural(stale.length, "à revérifier", "à revérifier")}</span>}
        </div>
        <p className="section-note">Taux, barèmes et sources qui servent aux calculs et aux démarches : chacun a une date de revérification.</p>
        {stale.length > 0 && <References items={stale} />}
        {fresh.length > 0 && (
          <details className="watch-fresh" open={stale.length === 0 && fresh.length <= 3}>
            <summary>{plural(fresh.length, "donnée à jour", "données à jour")} · prochaine vérification le {fullDate(fresh.map((r) => r.next_check).sort()[0]!)}</summary>
            <References items={fresh} />
          </details>
        )}
      </section>

      <Sources sources={view.sources} onChange={reload} />
    </>
  );
}

function References({ items }: { items: ReferenceCheck[] }) {
  return (
    <ul className="watch-refs">
      {items.map((r) => (
        <li key={r.key} className={`is-${r.status}`}>
          <span className={`tag ${r.status === "stale" ? "warn" : "ok"}`}>{r.status === "stale" ? "À revérifier" : "À jour"}</span>
          <div>
            <b>{r.title}</b>
            <p className="muted">{r.detail}</p>
            <span className="faint">{r.as_of ? `Donnée du ${fullDate(r.as_of)} · ` : ""}prochaine vérification le {fullDate(r.next_check)}{r.source ? ` · ${r.source}` : ""}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Item({ item, onChange }: { item: WatchItemView; onChange: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const href = safeHref(item.url);
  const set = (status: "seen" | "dismissed") =>
    void run(async () => {
      await api(`/api/watch/items/${item.id}`, { method: "PATCH", body: { status } });
      onChange();
    });
  return (
    <li className={`watch-item${item.status === "new" ? " is-new" : ""}`}>
      <div className="watch-item-tags">
        {item.status === "new" && <span className="tag ok">{item.version > 1 ? "Mis à jour" : "Nouveau"}</span>}
        {item.relevant && <span className="tag">Pour toi</span>}
        {item.topic_labels.map((t) => <span key={t} className="tag">{t}</span>)}
      </div>
      <h3>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{item.title} <Icon name="external" size={14} /></a> : item.title}</h3>
      {item.summary && <p className="muted">{item.summary}</p>}
      <p className="faint watch-meta">
        {item.source ? <>{item.source.name} · {TRUST[item.source.trust] ?? item.source.trust} · </> : null}
        publié le {when(item.published_at)} · vu le {when(item.first_seen_at)} · revérifié le {when(item.last_verified_at)}
        {item.changed_at ? ` · modifié le ${fullDate(item.changed_at)} (version ${item.version})` : ""}
      </p>
      <div className="actions">
        {item.status === "new" && <button type="button" className="btn quiet sm" disabled={busy} onClick={() => set("seen")}>Lu</button>}
        <button type="button" className="btn ghost sm" disabled={busy} onClick={() => set("dismissed")}>Masquer</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}

/** « toutes les 12 h », « chaque mois » (barèmes relus une fois par mois). */
export function everyText(hours: number): string {
  if (hours >= 24 * 28) return "chaque mois";
  if (hours >= 48 && hours % 24 === 0) return `tous les ${hours / 24} jours`;
  return `toutes les ${hours} h`;
}

function Sources({ sources, onChange }: { sources: WatchSourceView[]; onChange: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const add = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/watch/sources", { method: "POST", body: { url, name: name || null } });
      setUrl("");
      setName("");
      toast("Source ajoutée : elle sera relevée à la prochaine relève.");
      onChange();
    });
  };
  const act = (fn: () => Promise<unknown>) => void run(async () => { await fn(); onChange(); });
  return (
    <section className="section" aria-labelledby="sources-title">
      <h2 id="sources-title">Sources suivies</h2>
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="watch-sources">
        {sources.map((s) => (
          <li key={s.id}>
            <div>
              <b>{s.name}</b> <span className="tag">{TRUST[s.trust] ?? s.trust}</span>
              <p className="faint watch-url">{s.url}</p>
              <p className="faint">
                {s.last_ok_at ? `Relevée le ${shortDate(s.last_ok_at)}` : "Jamais relevée"} · {everyText(s.interval_hours)}
                {s.last_error && <span className="error"> · {s.last_error}</span>}
              </p>
            </div>
            <div className="watch-source-actions">
              <label className="switch">
                <input type="checkbox" role="switch" checked={s.enabled} disabled={busy} aria-label={`${s.name} : ${s.enabled ? "suivie" : "en pause"}`}
                       onChange={() => act(() => api(`/api/watch/sources/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } }))} />
                <span aria-hidden="true" />
              </label>
              {!s.builtin && (
                <button type="button" className="btn ghost xs" aria-label={`Retirer ${s.name}`} disabled={busy}
                        onClick={() => act(() => api(`/api/watch/sources/${s.id}`, { method: "DELETE" }))}><Icon name="trash" /></button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <form className="card card-quiet watch-add" onSubmit={add}>
        <h3>Ajouter un flux (RSS ou Atom)</h3>
        <div className="form-grid">
          <Field label="Adresse du flux" hint="https:// seulement ; jamais une adresse de ton réseau local">
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://exemple.fr/rss.xml" required />
          </Field>
          <Field label="Nom (facultatif)"><input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></Field>
        </div>
        <div className="actions"><button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !url}>Ajouter</button></div>
      </form>
    </section>
  );
}
