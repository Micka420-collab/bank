// Assistant « Demande à Bank » via OpenRouter : ta clé (chiffrée, jamais réaffichée), le modèle, ce que le modèle
// a le droit de voir, et deux plafonds (crédits par mois, appels par jour). Le contenu des échanges n'est jamais
// stocké : seuls le modèle, les jetons et le coût le sont.

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, useApi, type AssistantModel, type AssistantPrivacy, type AssistantStatus } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { shortDate } from "../format";

/** Quelques modèles sûrs si la liste d'OpenRouter n'est pas joignable (serveur sans Internet au moment du réglage). */
export const SUGGESTED_MODELS: AssistantModel[] = [
  { id: "anthropic/claude-haiku-5.5", name: "Claude Haiku 5.5", context_length: 200_000, prompt_usd_per_million: 1, completion_usd_per_million: 5, free: false },
  { id: "openai/gpt-4.1-mini", name: "GPT-4.1 mini", context_length: 1_000_000, prompt_usd_per_million: 0.4, completion_usd_per_million: 1.6, free: false },
  { id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash", context_length: 1_000_000, prompt_usd_per_million: 0.3, completion_usd_per_million: 2.5, free: false },
  { id: "mistralai/mistral-small-3.2-24b-instruct", name: "Mistral Small 3.2", context_length: 128_000, prompt_usd_per_million: 0.1, completion_usd_per_million: 0.3, free: false },
  { id: "meta-llama/llama-3.3-70b-instruct:free", name: "Llama 3.3 70B (gratuit)", context_length: 131_000, prompt_usd_per_million: 0, completion_usd_per_million: 0, free: true },
];

export function usd(cents: number): string {
  return `${(cents / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`;
}

/** Prix lisible d'un modèle : « 1 $ / 5 $ par million de jetons », ou « gratuit ». */
export function priceLabel(m: AssistantModel): string {
  if (m.free) return "gratuit";
  const f = (v: number) => v.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
  return `${f(m.prompt_usd_per_million)} $ / ${f(m.completion_usd_per_million)} $ par million de jetons`;
}

const PRIVACY_ORDER: AssistantPrivacy[] = ["redacted", "aggregates", "full"];

export function AssistantSettings() {
  const st = useApi<AssistantStatus>("/api/assistant");
  const [mode, setMode] = useState<"view" | "key" | "settings">("view");
  const toast = useToast();
  const [busy, error, run] = useAsyncAction();

  const test = () =>
    void run(async () => {
      const r = await api<{ ok: boolean; model: string; answer: string; cost_usd: number }>("/api/assistant/test", { method: "POST" });
      toast(`${r.model} répond${r.cost_usd ? ` (${r.cost_usd.toLocaleString("fr-FR", { maximumFractionDigits: 5 })} $)` : ""}. Tout est en place.`);
      st.reload();
    });
  const remove = () =>
    void run(async () => {
      if (!window.confirm("Retirer la clé OpenRouter ? L'assistant et les catégories proposées s'arrêtent ; tes crédits restent chez OpenRouter.")) return;
      await api("/api/assistant", { method: "DELETE" });
      toast("Clé retirée.");
      setMode("view");
      st.reload();
    });

  return (
    <section className="section" aria-labelledby="assistant-title" id="assistant">
      <div className="section-head">
        <h2 id="assistant-title"><Icon name="spark" /> Assistant (OpenRouter)</h2>
        {st.data?.configured && mode === "view" && (
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={test}>Tester</button>
            <button type="button" className="btn quiet sm" onClick={() => setMode("settings")}><Icon name="settings" /> Modifier</button>
          </span>
        )}
      </div>
      <p className="section-note">
        « Demande à Bank » répond à tes questions à partir de ton dossier et propose des enveloppes pour les marchands
        non classés. Le modèle tourne chez OpenRouter avec <b>ta</b> clé et <b>tes</b> crédits ; Bank lui envoie un
        dossier anonymisé par défaut, jamais ta clé, et ne garde aucun échange. Il explique et propose : il n'applique rien.
      </p>
      {st.error && <ErrorNote message={st.error} onRetry={st.reload} />}
      {!st.data && !st.error && <Loading what="Assistant" />}
      {st.data && !st.data.configured && mode === "view" && (
        <div className="card">
          <p><b>Pas encore de clé.</b> Crée-la sur openrouter.ai (Keys), mets quelques dollars de crédits, et colle-la ici : elle est chiffrée en base et ne sera jamais réaffichée.</p>
          <div className="actions"><button type="button" className="btn" onClick={() => setMode("key")}><Icon name="plus" /> Ajouter ma clé OpenRouter</button></div>
        </div>
      )}
      {st.data?.configured && mode === "view" && <StatusCard status={st.data} onChangeKey={() => setMode("key")} onRemove={remove} busy={busy} />}
      {st.data && mode !== "view" && (
        <ConfigForm
          status={st.data}
          withKey={mode === "key" || !st.data.configured}
          onDone={() => { setMode("view"); st.reload(); }}
          onCancel={() => setMode("view")}
        />
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function StatusCard({ status: s, onChangeKey, onRemove, busy }: { status: AssistantStatus; onChangeKey: () => void; onRemove: () => void; busy: boolean }) {
  const cap = s.monthly_cap_usd_cents ?? 0;
  const spent = s.month_cost_usd_cents ?? 0;
  const ratio = cap > 0 ? Math.min(1, (s.month_cost_microusd ?? 0) / (cap * 10_000)) : 1;
  return (
    <div className="card stack">
      <div className="connected-head" style={{ border: 0, padding: 0 }}>
        <span className="bank-mark is-app" aria-hidden="true"><Icon name="spark" /></span>
        <div style={{ flex: "1 1 14rem" }}>
          <b>{s.model}</b> <span className="tag ok">active</span>
          <div className="faint">
            clé <code>{s.key_prefix}</code> · {s.privacy ? s.privacy_modes[s.privacy].split(" : ")[0] : ""}
            {s.last_used_at ? ` · dernier appel le ${shortDate(s.last_used_at)}` : " · jamais appelée"}
          </div>
        </div>
      </div>
      {s.last_error && <div className="notice danger"><Icon name="alert" /><span>{s.last_error}</span></div>}
      <div>
        <div className="envelope-head">
          <span>Crédits ce mois-ci : <b>{usd(spent)}</b> <span className="muted">sur {usd(cap)}</span></span>
          <span className="faint">{s.calls_month ?? 0} appel{(s.calls_month ?? 0) > 1 ? "s" : ""} · {s.calls_today ?? 0} aujourd'hui sur {s.daily_calls_cap}</span>
        </div>
        <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={spent} aria-label="Crédits consommés ce mois-ci"
             data-status={ratio >= 1 ? "over" : ratio >= 0.8 ? "warning" : undefined}>
          <i style={{ width: `${ratio * 100}%` }} />
        </div>
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="btn quiet sm" onClick={onChangeKey}>Changer la clé</button>
        <button type="button" className="btn ghost sm" disabled={busy} onClick={onRemove}>Retirer la clé</button>
      </div>
    </div>
  );
}

function ConfigForm({ status: s, withKey, onDone, onCancel }: { status: AssistantStatus; withKey: boolean; onDone: () => void; onCancel: () => void }) {
  const models = useApi<AssistantModel[]>("/api/assistant/models");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(s.model ?? SUGGESTED_MODELS[0]!.id);
  const [privacy, setPrivacy] = useState<AssistantPrivacy>(s.privacy ?? "redacted");
  const [cap, setCap] = useState(String((s.monthly_cap_usd_cents ?? 500) / 100));
  const [daily, setDaily] = useState(String(s.daily_calls_cap ?? 60));
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const [query, setQuery] = useState("");

  const catalogue = useMemo(() => {
    const fetched = models.data ?? [];
    const known = new Set(fetched.map((m) => m.id));
    return [...fetched, ...SUGGESTED_MODELS.filter((m) => !known.has(m.id))];
  }, [models.data]);
  const chosen = catalogue.find((m) => m.id === model);
  const shortlist = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = q ? catalogue.filter((m) => m.id.includes(q) || m.name.toLowerCase().includes(q)) : catalogue.filter((m) => SUGGESTED_MODELS.some((x) => x.id === m.id) || m.free);
    return pool.slice(0, 12);
  }, [catalogue, query]);
  useEffect(() => {
    if (!model && shortlist[0]) setModel(shortlist[0].id);
  }, [model, shortlist]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const capCents = Math.round(Number(cap.replace(",", ".")) * 100);
      const dailyCalls = Number.parseInt(daily, 10);
      if (!Number.isFinite(capCents) || capCents < 0) throw new Error("Plafond mensuel illisible");
      if (!Number.isFinite(dailyCalls) || dailyCalls < 0) throw new Error("Plafond d'appels illisible");
      if (withKey) {
        await api("/api/assistant", {
          method: "PUT",
          body: { password, api_key: apiKey.trim(), model, privacy, monthly_cap_usd_cents: capCents, daily_calls_cap: dailyCalls },
        });
        setApiKey("");
        setPassword("");
        toast("Clé enregistrée. Lance « Tester » pour vérifier qu'elle répond.");
      } else {
        await api("/api/assistant", { method: "PATCH", body: { model, privacy, monthly_cap_usd_cents: capCents, daily_calls_cap: dailyCalls } });
        toast("Réglages de l'assistant enregistrés.");
      }
      onDone();
    });
  };

  return (
    <form className="card stack" onSubmit={submit} aria-label="Réglages de l'assistant">
      {withKey && (
        <div className="form-grid">
          <Field label="Clé OpenRouter" hint="Commence par « sk-or- ». Chiffrée sur le serveur, jamais réaffichée.">
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} required minLength={10} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Ton mot de passe Bank" hint="Une clé ouvre tes crédits et ton dossier : on te le redemande.">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
          </Field>
        </div>
      )}
      <div className="form-grid">
        <Field label="Modèle" hint={chosen ? `${chosen.name} — ${priceLabel(chosen)}${chosen.context_length ? ` · contexte ${Math.round(chosen.context_length / 1000)} k` : ""}` : "Identifiant « fournisseur/modèle » d'OpenRouter."}>
          <input list="assistant-models" value={model} onChange={(e) => { setModel(e.target.value); setQuery(e.target.value); }} required
                 placeholder="fournisseur/modele" autoComplete="off" spellCheck={false} />
          <datalist id="assistant-models">
            {shortlist.map((m) => <option key={m.id} value={m.id}>{m.name} — {priceLabel(m)}</option>)}
          </datalist>
        </Field>
        <Field label="Plafond de crédits par mois ($)" hint="0 bloque l'assistant. OpenRouter facture au jeton ; Bank s'arrête avant ce montant.">
          <input inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} required />
        </Field>
        <Field label="Appels par jour, au plus">
          <input type="number" min={0} max={500} value={daily} onChange={(e) => setDaily(e.target.value)} required />
        </Field>
      </div>
      {models.error && <p className="faint">Liste des modèles indisponible pour l'instant ({models.error}) : quelques modèles courants sont proposés, tu peux aussi taper n'importe quel identifiant OpenRouter.</p>}
      <fieldset className="presets" aria-label="Ce que le modèle voit">
        <legend className="muted">Ce que le modèle voit</legend>
        {PRIVACY_ORDER.map((p) => (
          <label key={p} className="preset" data-checked={privacy === p}>
            <input type="radio" name="privacy" value={p} checked={privacy === p} onChange={() => setPrivacy(p)} />
            <b>{s.privacy_modes[p].split(" : ")[0]}</b>
            <span className="faint">{s.privacy_modes[p].split(" : ").slice(1).join(" : ")}</span>
          </label>
        ))}
      </fieldset>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions" style={{ marginTop: 0 }}>
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>{withKey ? "Enregistrer la clé" : "Enregistrer"}</button>
        <button type="button" className="btn ghost" onClick={onCancel}>Annuler</button>
      </div>
    </form>
  );
}
