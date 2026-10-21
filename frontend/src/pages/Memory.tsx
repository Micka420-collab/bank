// « Ce que Bank retient » : les deux carnets de l'assistant (qui tu es, ton environnement financier), ce qu'il
// propose de corriger, vos conversations, et l'oubli. Chaque entrée dit d'où elle vient ; épinglée, lui seul ne
// peut plus la changer. C'est exactement ce qui est envoyé au modèle au début de chaque conversation.

import { useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type ConversationDetail,
  type ConversationSummary,
  type MemoryEntryView,
  type MemoryStore,
  type MemoryView,
} from "../api";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { shortDate, plural } from "../format";

const STORE_TITLE: Record<MemoryStore, string> = { user: "Qui tu es", notes: "Ton environnement financier" };
const STORE_HINT: Record<MemoryStore, string> = {
  user: "Ta situation, tes projets, tes préférences, ta façon de parler d'argent.",
  notes: "Tes habitudes, tes contraintes, ce qui marche pour toi, ce qu'il faut suivre.",
};
const STORE_PLACEHOLDER: Record<MemoryStore, string> = {
  user: "Ex. : Préfère des réponses courtes, avec un chiffre clé.",
  notes: "Ex. : Le loyer part le 5, la prime arrive en juin.",
};
const ORIGIN: Record<MemoryEntryView["origin"], string> = {
  chat: "retenu en conversation",
  review: "retenu à la relecture",
  user: "écrit par toi",
};
const ACTION_LABEL = { add: "Ajouter", patch: "Corriger", remove: "Oublier" } as const;

export function retentionLabel(days: number): string {
  if (days === 0) return "Sans limite";
  if (days % 365 === 0) return plural(days / 365, "an", "ans");
  return plural(days, "jour", "jours");
}

export function Memory() {
  const view = useApi<MemoryView>("/api/memory");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const name = view.data?.name ?? "Bank";

  const act = (fn: () => Promise<unknown>, done?: string) =>
    void run(async () => {
      await fn();
      if (done) toast(done);
      view.reload();
    });

  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!view.data) return <Loading what="Lecture des carnets" />;
  const v = view.data;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Ce que {name} retient</h1>
          <p className="muted">
            Deux carnets courts, relus au début de chaque conversation. Tu peux tout lire, corriger, épingler ou effacer :
            c'est exactement ce que voit le modèle, rien de plus.
          </p>
        </div>
        <span className={`memory-state${v.consent && v.available ? " is-on" : ""}`}>
          {v.consent ? (v.available ? "Mémoire active" : "Mémoire en pause") : "Mémoire éteinte"}
        </span>
      </div>

      {!v.consent && (
        <section className="card section memory-off" aria-labelledby="memory-off-title">
          <h2 id="memory-off-title">{name} ne retient rien pour l'instant</h2>
          <p>
            Avec ton accord, il retient l'essentiel de vos échanges (projets, préférences, ce qui marche pour toi) et
            s'améliore avec tes retours. Tout reste chiffré sur ton serveur ; ce qu'il retient est envoyé à ton modèle
            OpenRouter avec le dossier. Il ne garde jamais d'IBAN, de numéro de carte, de code ni de coordonnées.
          </p>
          <div className="actions">
            <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy}
                    onClick={() => act(() => api("/api/consents/ai_memory", { method: "PUT", body: { granted: true } }), "Mémoire activée.")}>
              Activer la mémoire
            </button>
          </div>
        </section>
      )}
      {v.consent && !v.available && (
        <div className="notice warn" role="status">
          <Icon name="alert" />
          <span>Confidentialité « chiffres seuls » : rien de ce que tu écris ne sort, donc la mémoire n'est pas utilisée. Tu peux la changer dans <a href="#/connecteurs">Connecteurs</a>.</span>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}

      {v.proposals.length > 0 && (
        <section className="card section" aria-labelledby="proposals-title">
          <div className="card-head">
            <h2 id="proposals-title">À valider</h2>
            <span className="faint">{name} ne corrige ni n'efface rien de lui-même après coup</span>
          </div>
          <ul className="memory-proposals">
            {v.proposals.map((p) => (
              <li key={p.id}>
                <div>
                  <b>{ACTION_LABEL[p.action]}</b> <span className="faint">· {STORE_TITLE[p.store]}</span>
                  {p.old_text && <p className="memory-old">« {p.old_text} »</p>}
                  {p.new_text && <p className="memory-new">« {p.new_text} »</p>}
                  {p.reason && <p className="faint">{p.reason}</p>}
                </div>
                <div className="actions">
                  <button type="button" className="btn sm" disabled={busy}
                          onClick={() => act(() => api(`/api/memory/proposals/${p.id}`, { method: "POST", body: { approve: true } }), "C'est noté.")}>
                    Valider
                  </button>
                  <button type="button" className="btn quiet sm" disabled={busy}
                          onClick={() => act(() => api(`/api/memory/proposals/${p.id}`, { method: "POST", body: { approve: false } }))}>
                    Écarter
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="memory-books">
        {(["user", "notes"] as const).map((s) => (
          <Notebook key={s} store={s} data={v.stores[s]} busy={busy}
                    onAdd={(content) => act(() => api("/api/memory/entries", { method: "POST", body: { store: s, content } }))}
                    onEdit={(id, body) => act(() => api(`/api/memory/entries/${id}`, { method: "PATCH", body }))}
                    onDelete={(id) => act(() => api(`/api/memory/entries/${id}`, { method: "DELETE" }), "Effacé.")} />
        ))}
      </div>

      <Conversations name={name} retention={v.retention_days} onChange={view.reload} />
      <MemorySettings view={v} busy={busy} onSave={(body, done) => act(() => api("/api/memory/settings", { method: "PUT", body }), done)} />
      <Forget name={name} onDone={view.reload} />
    </>
  );
}

// ------------------------------------------------------------------ un carnet
function Notebook({ store, data, busy, onAdd, onEdit, onDelete }: {
  store: MemoryStore;
  data: MemoryView["stores"][MemoryStore];
  busy: boolean;
  onAdd: (content: string) => void;
  onEdit: (id: number, body: { content?: string; pinned?: boolean }) => void;
  onDelete: (id: number) => void;
}) {
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [text, setText] = useState("");
  const ratio = Math.min(1, data.used / data.limit);
  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    onAdd(draft.trim());
    setDraft("");
  };
  return (
    <section className="card section memory-book" aria-labelledby={`book-${store}`}>
      <div className="card-head">
        <h2 id={`book-${store}`}>{STORE_TITLE[store]}</h2>
        <span className="faint">{Math.round(ratio * 100)} % rempli</span>
      </div>
      <p className="muted memory-hint">{STORE_HINT[store]}</p>
      <div className={`memory-gauge${ratio > 0.85 ? " is-full" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={data.limit}
           aria-valuenow={data.used} aria-label={`Carnet rempli : ${data.used} caractères sur ${data.limit}`}>
        <span style={{ width: `${ratio * 100}%` }} />
      </div>
      {data.entries.length === 0 ? (
        <p className="faint memory-empty">Rien pour l'instant.</p>
      ) : (
        <ul className="memory-entries">
          {data.entries.map((e) => (
            <li key={e.id} className={`memory-entry${e.pinned ? " is-pinned" : ""}${e.blocked ? " is-blocked" : ""}`}>
              <span className="memory-mark" aria-hidden="true">§</span>
              {editing === e.id ? (
                <form className="memory-edit" onSubmit={(ev) => {
                  ev.preventDefault();
                  onEdit(e.id, { content: text.trim() });
                  setEditing(null);
                }}>
                  <textarea value={text} onChange={(ev) => setText(ev.target.value)} maxLength={600} rows={2} aria-label="Corriger l'entrée" autoFocus />
                  <div className="actions">
                    <button className="btn sm" disabled={busy || !text.trim()}>Enregistrer</button>
                    <button type="button" className="btn quiet sm" onClick={() => setEditing(null)}>Annuler</button>
                  </div>
                </form>
              ) : (
                <div className="memory-entry-body">
                  <p>{e.content}</p>
                  <span className="faint">
                    {ORIGIN[e.origin]} · {shortDate(e.updated_at)}
                    {e.pinned && " · épinglée"}
                    {e.blocked && " · bloquée : jamais montrée au modèle"}
                  </span>
                </div>
              )}
              {editing !== e.id && (
                <div className="memory-entry-actions">
                  <button type="button" className={`btn ghost xs${e.pinned ? " is-on" : ""}`} aria-pressed={e.pinned} disabled={busy}
                          title={e.pinned ? "Désépingler" : "Épingler : il ne pourra plus la changer"} aria-label={e.pinned ? "Désépingler" : "Épingler"}
                          onClick={() => onEdit(e.id, { pinned: !e.pinned })}>
                    <Icon name="pin" />
                  </button>
                  <button type="button" className="btn ghost xs" title="Corriger" aria-label="Corriger" disabled={busy}
                          onClick={() => { setEditing(e.id); setText(e.content); }}>
                    <Icon name="edit" />
                  </button>
                  <button type="button" className="btn ghost xs" title="Effacer" aria-label="Effacer" disabled={busy}
                          onClick={() => onDelete(e.id)}>
                    <Icon name="trash" />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <form className="memory-add" onSubmit={add}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={600} placeholder={STORE_PLACEHOLDER[store]}
               aria-label={`Ajouter à « ${STORE_TITLE[store]} »`} />
        <button className="btn quiet sm" disabled={busy || !draft.trim()}><Icon name="plus" /> Ajouter</button>
      </form>
    </section>
  );
}

// ------------------------------------------------------------------ conversations
function Conversations({ name, retention, onChange }: { name: string; retention: number; onChange: () => void }) {
  const list = useApi<ConversationSummary[]>("/api/assistant/conversations?limit=50");
  const [open, setOpen] = useState<ConversationDetail | null>(null);
  const [busy, error, run] = useAsyncAction();
  const show = (id: number) =>
    void run(async () => {
      setOpen(open?.id === id ? null : await api<ConversationDetail>(`/api/assistant/conversations/${id}`));
    });
  const remove = (id: number) =>
    void run(async () => {
      await api(`/api/assistant/conversations/${id}`, { method: "DELETE" });
      if (open?.id === id) setOpen(null);
      list.reload();
      onChange();
    });
  return (
    <section className="card section" aria-labelledby="conversations-title">
      <div className="card-head">
        <h2 id="conversations-title">Conversations</h2>
        <span className="faint">Gardées {retention === 0 ? "sans limite" : retentionLabel(retention)}, chiffrées</span>
      </div>
      <p className="muted">{name} peut les relire pour retrouver ce dont vous avez parlé (sans les renvoyer en entier au modèle).</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!list.data && <Loading />}
      {list.data?.length === 0 && <p className="faint">Aucune conversation gardée.</p>}
      {list.data && list.data.length > 0 && (
        <ul className="conversation-list">
          {list.data.map((c) => (
            <li key={c.id}>
              <div className="conversation-id">
                <b>{c.title}</b>
                <span className="faint">{shortDate(c.started_at)} · {plural(c.turns, "question", "questions")}{c.closed ? "" : " · en cours"}{c.memory_on ? "" : " · sans mémoire"}</span>
                {c.summary && <p className="muted">{c.summary}</p>}
              </div>
              <div className="actions">
                <button type="button" className="btn link" aria-expanded={open?.id === c.id} onClick={() => show(c.id)} disabled={busy}>
                  {open?.id === c.id ? "Masquer" : "Relire"}
                </button>
                <button type="button" className="btn link" onClick={() => remove(c.id)} disabled={busy}>Effacer</button>
              </div>
              {open?.id === c.id && (
                <ol className="conversation-replay" aria-label={`Conversation « ${c.title} »`}>
                  {open.messages.map((m) => (
                    <li key={m.id} className={`is-${m.role}`}>
                      {m.role === "user" ? <p>{m.content}</p> : <Markdown source={m.content} />}
                      {m.feedback && <span className="faint">{m.feedback === "up" ? "Utile" : "À revoir"}</span>}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ réglages
function MemorySettings({ view, busy, onSave }: {
  view: MemoryView;
  busy: boolean;
  onSave: (body: { name?: string; retention_days?: number }, done: string) => void;
}) {
  const [name, setName] = useState(view.name);
  return (
    <section className="card section" aria-labelledby="memory-settings-title">
      <div className="card-head"><h2 id="memory-settings-title">Réglages</h2></div>
      <div className="form-grid">
        <form onSubmit={(e) => { e.preventDefault(); onSave({ name: name.trim() }, "Nouveau nom enregistré."); }}>
          <Field label="Nom de l'assistant" hint="Un prénom ou un surnom : c'est ainsi qu'il se présentera.">
            <div className="input-with-action">
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={24} autoComplete="off" />
              <button className="btn quiet sm" disabled={busy || !name.trim() || name.trim() === view.name}>Enregistrer</button>
            </div>
          </Field>
        </form>
        <Field label="Garder les conversations" hint="Au-delà, elles sont effacées ; ce qu'il a retenu dans ses carnets reste.">
          <select value={view.retention_days} disabled={busy}
                  onChange={(e) => onSave({ retention_days: Number(e.target.value) }, "Durée enregistrée.")}>
            {view.retention_choices.map((d) => <option key={d} value={d}>{retentionLabel(d)}</option>)}
          </select>
        </Field>
      </div>
    </section>
  );
}

function Forget({ name, onDone }: { name: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<MemoryView>("/api/memory/forget", { method: "POST", body: { password } });
      setPassword("");
      setOpen(false);
      toast(`${name} a tout oublié (${plural(r.forgotten?.entries ?? 0, "entrée", "entrées")}, ${plural(r.forgotten?.conversations ?? 0, "conversation", "conversations")}).`);
      onDone();
    });
  };
  return (
    <section className="card section memory-forget" aria-labelledby="forget-title">
      <div className="card-head">
        <h2 id="forget-title">Tout oublier</h2>
        {!open && <button type="button" className="btn danger sm" onClick={() => setOpen(true)}>Tout oublier…</button>}
      </div>
      <p className="muted">Efface les deux carnets, les propositions et toutes les conversations. Irréversible.</p>
      {open && (
        <form onSubmit={submit} className="panel card-quiet">
          <Field label="Ton mot de passe, pour confirmer">
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="actions">
            <button className={`btn danger${busy ? " is-loading" : ""}`} disabled={busy || !password}>Tout effacer</button>
            <button type="button" className="btn quiet" onClick={() => setOpen(false)}>Annuler</button>
          </div>
        </form>
      )}
    </section>
  );
}
