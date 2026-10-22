// Recherche web de l'assistant : brancher SearXNG (chez toi) ou Brave (clé chiffrée), tester, fixer un plafond par
// jour, donner ou retirer l'accord. Sans moteur ou sans accord, l'assistant ne cherche pas et le dit.

import { useState, type FormEvent } from "react";
import { api, useApi, type SearchStatus } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";

export function SearchSettings() {
  const st = useApi<SearchStatus>("/api/search");
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  const [busy, error, run] = useAsyncAction();

  const test = () =>
    void run(async () => {
      const r = await api<{ ok: boolean; results: number; sample: string[] }>("/api/search/test", { method: "POST" });
      toast(`Le moteur répond : ${r.results} résultat(s)${r.sample.length ? ` (${r.sample.join(", ")})` : ""}.`);
      st.reload();
    });
  const remove = () =>
    void run(async () => {
      if (!window.confirm("Retirer le moteur de recherche ? L'assistant ne cherchera plus sur le web.")) return;
      await api("/api/search", { method: "DELETE" });
      toast("Moteur retiré.");
      st.reload();
    });
  const consent = (granted: boolean) =>
    void run(async () => {
      await api("/api/consents/web_search", { method: "PUT", body: { granted } });
      st.reload();
    });

  const s = st.data;
  return (
    <section className="section" aria-labelledby="search-title" id="recherche">
      <div className="section-head">
        <h2 id="search-title"><Icon name="search" /> Recherche web de l'assistant</h2>
        {s?.configured && !editing && (
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={test}>Tester</button>
            <button type="button" className="btn quiet sm" onClick={() => setEditing(true)}><Icon name="settings" /> Modifier</button>
          </span>
        )}
      </div>
      <p className="section-note">
        Pour une règle, un barème ou une actualité, l'assistant peut chercher sur le web et citer ses sources. La requête
        est rédigée par le modèle puis nettoyée avant de partir (jamais d'IBAN, de numéro, d'e-mail, de téléphone ni de
        nom) ; les pages sont lues depuis ton serveur, adresses publiques seulement. Deux moteurs possibles :
        <b> SearXNG</b> chez toi (le plus discret ; active le format JSON dans ses réglages) ou <b>Brave Search</b> avec
        une clé d'API, chiffrée ici et jamais réaffichée.
      </p>
      {st.error && <ErrorNote message={st.error} onRetry={st.reload} />}
      {!s && !st.error && <Loading what="Recherche web" />}
      {s && !s.configured && !editing && (
        <div className="card">
          <p><b>Aucun moteur branché.</b> Sans moteur, l'assistant répond avec ton dossier et dit quand une information lui manque.</p>
          <div className="actions"><button type="button" className="btn" onClick={() => setEditing(true)}><Icon name="plus" /> Brancher un moteur</button></div>
        </div>
      )}
      {s?.configured && !editing && (
        <div className="card">
          <dl className="ch-kv">
            <dt>Moteur</dt>
            <dd>{s.kinds[s.kind ?? "searxng"]}{s.kind === "searxng" ? ` · ${s.base_url}` : ` · clé ${s.key_prefix}`}</dd>
            <dt>Aujourd'hui</dt>
            <dd>{s.used_today ?? 0} / {s.daily_cap} recherches</dd>
            <dt>Accord</dt>
            <dd>
              {s.consent ? "donné" : "pas donné : l'assistant ne cherche pas"}{" "}
              <button type="button" className="btn link" disabled={busy} onClick={() => consent(!s.consent)}>{s.consent ? "Retirer" : "Donner mon accord"}</button>
            </dd>
            {s.last_used_at && <><dt>Dernière recherche</dt><dd>{new Date(s.last_used_at).toLocaleString("fr-FR")}</dd></>}
            {s.last_error && <><dt>Dernière erreur</dt><dd className="error">{s.last_error}</dd></>}
          </dl>
          <div className="actions">
            <button type="button" className="btn link" disabled={busy} onClick={remove}><Icon name="trash" /> Retirer le moteur</button>
          </div>
        </div>
      )}
      {s && editing && <SearchForm status={s} onDone={() => { setEditing(false); st.reload(); }} onCancel={() => setEditing(false)} />}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function SearchForm({ status: s, onDone, onCancel }: { status: SearchStatus; onDone: () => void; onCancel: () => void }) {
  const [kind, setKind] = useState<"searxng" | "brave">(s.kind ?? "searxng");
  const [baseUrl, setBaseUrl] = useState(s.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [cap, setCap] = useState(s.daily_cap ?? 40);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/search", {
        method: "PUT",
        body: { password, kind, base_url: kind === "searxng" ? baseUrl : null, api_key: kind === "brave" ? apiKey : null, daily_cap: cap },
      });
      toast("Moteur enregistré : lance « Tester » pour vérifier qu'il répond.");
      onDone();
    });
  };
  return (
    <form className="card form-grid" onSubmit={submit} aria-label="Brancher un moteur de recherche">
      <Field label="Moteur">
        <select value={kind} onChange={(e) => setKind(e.target.value as "searxng" | "brave")}>
          <option value="searxng">{s.kinds.searxng}</option>
          <option value="brave">{s.kinds.brave}</option>
        </select>
      </Field>
      {kind === "searxng" ? (
        <Field label="Adresse de ton SearXNG" hint="Exemple : http://searxng:8080 ou https://recherche.nimbus.lan — sans paramètres. Dans settings.yml : search.formats doit contenir json.">
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://searxng:8080" required maxLength={300} autoComplete="off" />
        </Field>
      ) : (
        <Field label="Clé d'API Brave Search" hint="Créée sur api.search.brave.com (abonnement Search). Chiffrée ici, jamais réaffichée.">
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" required minLength={20} maxLength={120} autoComplete="off" />
        </Field>
      )}
      <Field label="Plafond par jour" hint="0 bloque la recherche. Chaque recherche est une requête vers le moteur.">
        <input type="number" min={0} max={200} value={cap} onChange={(e) => setCap(Number(e.target.value))} required />
      </Field>
      <Field label="Ton mot de passe Bank" hint="Un moteur voit tes requêtes : on confirme que c'est bien toi.">
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
      </Field>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer</button>
        <button type="button" className="btn quiet" onClick={onCancel}>Annuler</button>
      </div>
    </form>
  );
}
