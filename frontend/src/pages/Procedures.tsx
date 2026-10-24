// Démarches : Bank prépare, vérifie et explique ; c'est toi qui transmets. Chaque valeur dit d'où elle vient, une
// valeur qui contredit son justificatif bloque, la validation porte sur le dossier exact relu (un changement
// l'annule), « je l'ai transmis » ne compte qu'une fois, puis Bank suit la réponse et te dit quand relancer.

import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type Period,
  type ProcedureField,
  type ProcedureListing,
  type ProcedurePiece,
  type ProcedureRow,
  type ProcedureTemplate,
  type ProcedureView,
} from "../api";
import { Icon, type IconName } from "../components/Icon";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useCelebrate, useToast } from "../components/ui";
import { money, plural, shortDate } from "../format";
import { editValue, fullDate } from "./Documents";
import { Sources } from "./Opportunities";

const CHANNEL: Record<ProcedureTemplate["channel"], { label: string; icon: IconName }> = {
  online_official: { label: "En ligne, sur le site officiel", icon: "external" },
  letter: { label: "Par courrier ou sur le site du destinataire", icon: "send" },
  counter: { label: "Auprès de ta banque", icon: "bank" },
  libre: { label: "Suivi libre", icon: "route" },
};
const COLUMNS: { key: string; title: string; stages: string[]; hint: string }[] = [
  { key: "prepare", title: "À préparer", stages: ["verify", "documents"], hint: "Valeurs ou pièces à compléter" },
  { key: "ready", title: "Prêt à valider", stages: ["ready"], hint: "Relis, valide, puis transmets" },
  { key: "waiting", title: "Transmis, en attente", stages: ["sent", "waiting"], hint: "Bank te dit quand relancer" },
  { key: "done", title: "Terminé", stages: ["done", "abandoned"], hint: "Résultat enregistré" },
];
const PHASES = ["Valeurs", "Pièces", "Relecture", "Transmission", "Réponse", "Terminé"];
const EVENT_LABEL: Record<string, string> = {
  created: "Démarche créée, préremplie",
  fields: "Valeurs modifiées",
  pieces: "Pièces modifiées",
  approved: "Dossier relu et validé",
  sent: "Transmis par toi",
  snoozed: "Relance repoussée",
  closed: "Résultat enregistré",
};
const OUTCOME_LABEL: Record<string, string> = { obtenu: "Obtenu", refuse: "Refusé", termine: "Terminé", abandon: "Abandonné" };

export function Procedures({ id, wantNew }: { id: number | null; wantNew: boolean }) {
  return id === null ? <Board wantNew={wantNew} /> : <Detail id={id} />;
}

// ------------------------------------------------------------------ tableau et catalogue
function Board({ wantNew }: { wantNew: boolean }) {
  const listing = useApi<ProcedureListing>("/api/procedures");
  const [catalogue, setCatalogue] = useState(wantNew);
  if (listing.error) return <ErrorNote message={listing.error} onRetry={listing.reload} />;
  if (!listing.data) return <Loading what="Ouverture des démarches" />;
  const rows = listing.data.procedures;
  const due = rows.filter((r) => r.due);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Démarches</h1>
          <p className="muted">Bank prépare tes dossiers avec ce qu'il sait déjà, vérifie chaque valeur contre ses justificatifs et te dit quoi faire. C'est toi qui transmets : rien ne part sans ta validation.</p>
        </div>
        <button type="button" className="btn" onClick={() => setCatalogue(!catalogue)} aria-expanded={catalogue}>
          <Icon name={catalogue ? "close" : "plus"} /> {catalogue ? "Fermer" : "Nouvelle démarche"}
        </button>
      </div>
      {due.length > 0 && (
        <div className="stack proc-due">
          {due.map((r) => (
            <p key={r.id} className="notice warn"><Icon name="clock" /><span><a href={`#/demarches/${r.id}`}>{r.title}</a> : c'est le moment de vérifier la réponse ou de relancer.</span></p>
          ))}
        </div>
      )}
      {(catalogue || rows.length === 0) && <Catalogue templates={listing.data.templates} />}
      {rows.length > 0 && (
        <div className="proc-board">
          {COLUMNS.map((col) => {
            const items = rows.filter((r) => col.stages.includes(r.stage));
            return (
              <section key={col.key} className={`proc-col is-${col.key}`} aria-labelledby={`col-${col.key}`}>
                <h2 id={`col-${col.key}`}>{col.title} <span className="chip-count">{items.length}</span></h2>
                <p className="faint">{col.hint}</p>
                <ul>{items.map((r) => <ProcCard key={r.id} row={r} />)}</ul>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}

/** Étape d'une démarche vue de la liste (sans savoir si le dossier prêt est déjà validé). */
export function rowPhase(stage: ProcedureRow["stage"]): number {
  return ({ verify: 0, documents: 1, ready: 2, sent: 4, waiting: 4 } as Record<string, number>)[stage] ?? 5;
}

function ProcCard({ row }: { row: ProcedureRow }) {
  const step = rowPhase(row.stage);
  const done = row.stage === "done";
  return (
    <li className={`proc-card${row.due ? " is-due" : ""}`}>
      <a href={`#/demarches/${row.id}`}>
        <b>{row.title}</b>
        <span className="faint">{row.stage_label}</span>
        {row.stage !== "abandoned" && (
          <span className="proc-progress" role="img" aria-label={done ? "Dossier terminé" : `Étape ${step + 1} sur ${PHASES.length} : ${PHASES[step]}`}>
            {PHASES.map((label, i) => <i key={label} className={i < step || done ? "is-done" : i === step ? "is-active" : undefined} />)}
          </span>
        )}
        {row.next_check && row.stage === "waiting" && (
          <span className={`tag ${row.due ? "warn" : ""}`}><Icon name="clock" size={12} /> {row.due ? "à relancer" : `point le ${shortDate(row.next_check)}`}</span>
        )}
      </a>
    </li>
  );
}

function Catalogue({ templates }: { templates: ProcedureTemplate[] }) {
  const [busy, error, run] = useAsyncAction();
  const start = (key: string) =>
    void run(async () => {
      const proc = await api<ProcedureView>("/api/procedures", { method: "POST", body: { template: key } });
      window.location.hash = `#/demarches/${proc.id}`;
    });
  return (
    <section className="section" aria-labelledby="catalogue-title">
      <h2 id="catalogue-title">Commencer une démarche</h2>
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="proc-catalogue">
        {templates.map((t) => (
          <li key={t.key} className="card">
            <span className="faint proc-channel"><Icon name={CHANNEL[t.channel].icon} size={14} /> {t.organisme !== "—" ? `${t.organisme} · ` : ""}{CHANNEL[t.channel].label}</span>
            <h3>{t.title}</h3>
            <p className="muted">{t.summary}</p>
            {t.sources.length > 0 && <p className="faint">Sources vérifiées le {fullDate(t.sources.map((s) => s.verified_on ?? "").filter(Boolean).sort()[0] ?? "")}</p>}
            <button type="button" className="btn quiet sm" disabled={busy} onClick={() => start(t.key)}>Commencer</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ une démarche
function phase(p: ProcedureView): number {
  if (p.stage === "verify") return 0;
  if (p.stage === "documents") return 1;
  if (p.stage === "ready") return p.approved ? 3 : 2;
  if (p.stage === "sent" || p.stage === "waiting") return 4;
  return 5;
}

function Detail({ id }: { id: number }) {
  const proc = useApi<ProcedureView>(`/api/procedures/${id}`);
  const [view, setView] = useState<ProcedureView | null>(null);
  useEffect(() => setView(proc.data), [proc.data]);
  if (proc.error) return <><BackLink /><ErrorNote message={proc.error} onRetry={proc.reload} /></>;
  if (!view) return <><BackLink /><Loading what="Ouverture de la démarche" /></>;
  const p = view;
  const current = phase(p);
  const open = ["verify", "documents", "ready"].includes(p.stage);
  return (
    <>
      <BackLink />
      <div className="page-head proc-head">
        <div>
          <h1>{p.title}</h1>
          <p className="muted">{p.template.recipient}</p>
          <span className="faint proc-channel"><Icon name={CHANNEL[p.template.channel].icon} size={14} /> {CHANNEL[p.template.channel].label}</span>
        </div>
        <span className={`tag proc-stage is-${p.stage}`}>{p.stage_label}</span>
      </div>
      <ol className="proc-phases" aria-label="Avancement">
        {PHASES.map((label, i) => (
          <li key={label} className={i < current ? "is-done" : i === current ? "is-active" : ""} aria-current={i === current ? "step" : undefined}>
            <span aria-hidden="true">{i < current ? <Icon name="check" size={14} /> : i + 1}</span><span className="phase-label">{label}</span>
          </li>
        ))}
      </ol>
      {p.approved_stale && <p className="notice warn"><Icon name="alert" /><span>Le dossier a changé depuis ta validation : relis-le et valide-le à nouveau avant de le transmettre.</span></p>}
      {p.evaluation.warnings.map((w) => <p key={w} className="notice warn"><Icon name="alert" /><span>{w}</span></p>)}

      <div className="proc-layout">
        <div className="proc-main">
          <Values view={p} editable={open} onView={setView} />
          {p.pieces.length > 0 && <Pieces view={p} editable={open} onView={setView} />}
          {p.letter && <Letter view={p} />}
          {open && p.stage === "ready" && <Review view={p} onView={setView} />}
          {open && p.stage !== "ready" && (
            <section className="card section proc-locked">
              <h2>Relire et valider</h2>
              <p className="muted">
                {p.stage === "verify"
                  ? `Complète ou corrige d'abord ${plural(p.evaluation.missing_fields.length + p.evaluation.conflicts.length, "valeur", "valeurs")} signalée${p.evaluation.missing_fields.length + p.evaluation.conflicts.length > 1 ? "s" : ""} ci-dessus.`
                  : "Joins ou vérifie d'abord les pièces signalées ci-dessus."}
              </p>
            </section>
          )}
          {!open && <FollowUp view={p} onView={setView} />}
        </div>
        <aside className="proc-aside">
          <HowTo template={p.template} />
          <Notes view={p} onView={setView} />
          <Timeline view={p} />
          <DeleteProcedure id={p.id} />
        </aside>
      </div>
    </>
  );
}

function BackLink() {
  return <a className="back-link" href="#/demarches"><Icon name="back" /> Démarches</a>;
}

// ------------------------------------------------------------------ valeurs
function Values({ view, editable, onView }: { view: ProcedureView; editable: boolean; onView: (v: ProcedureView) => void }) {
  const fields = view.fields.filter((f) => f.active);
  return (
    <section className="card section" aria-labelledby="values-title">
      <div className="card-head">
        <h2 id="values-title">Les valeurs du dossier</h2>
        <span className="faint">{fields.filter((f) => f.value !== null && f.value !== "").length} sur {fields.length} renseignées</span>
      </div>
      <ul className="proc-fields">
        {fields.map((f) => <FieldLine key={f.key} procId={view.id} field={f} editable={editable} onView={onView} />)}
      </ul>
    </section>
  );
}

function initialDraft(f: ProcedureField): string {
  if (f.value === null || f.value === undefined || typeof f.value === "boolean") return "";
  if (f.type === "choice" || f.type === "longtext") return String(f.value);
  if (f.type === "date") return String(f.value);
  return editValue(f.type as Parameters<typeof editValue>[0], f.value);
}

function FieldLine({ procId, field: f, editable, onView }: { procId: number; field: ProcedureField; editable: boolean; onView: (v: ProcedureView) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, error, run] = useAsyncAction();
  const save = (value: unknown) =>
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${procId}/fields`, { method: "PATCH", body: { values: { [f.key]: value } } }));
      setEditing(false);
    });
  const restore = () =>
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${procId}/fields/${f.key}/restore`, { method: "POST" }));
    });
  const empty = f.value === null || f.value === "";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save(draft.trim() === "" ? null : draft);
  };
  return (
    <li className={`proc-field is-${f.type}${f.conflict ? " is-conflict" : f.missing ? " is-missing" : ""}`}>
      <span className="proc-field-label">{f.label}{f.required && <span className="faint" aria-label="obligatoire"> *</span>}</span>
      {editing ? (
        <form className="proc-field-edit" onSubmit={submit}>
          {f.type === "choice" ? (
            <select value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus aria-label={f.label}>
              <option value="">—</option>
              {f.choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          ) : f.type === "longtext" ? (
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} autoFocus aria-label={f.label} maxLength={2000} />
          ) : f.type === "date" ? (
            <input type="date" value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus aria-label={f.label} />
          ) : (
            <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus aria-label={f.label} inputMode={f.type === "amount" || f.type === "number" ? "decimal" : undefined}
                   placeholder={f.type === "amount" ? "1 234,56" : ""} />
          )}
          <button className="btn sm" disabled={busy}>Enregistrer</button>
          <button type="button" className="btn quiet sm" onClick={() => setEditing(false)}>Annuler</button>
        </form>
      ) : f.type === "bool" ? (
        <label className="check proc-field-value">
          <input type="checkbox" checked={f.value === true} disabled={!editable || busy} onChange={(e) => save(e.target.checked)} />
          {f.value === true ? "Oui" : "Non"}
        </label>
      ) : (
        <span className={`proc-field-value${empty ? " faint" : ""}`}>{empty ? (f.required ? "À renseigner" : "Facultatif") : f.display}</span>
      )}
      {editable && !editing && f.type !== "bool" && (
        <button type="button" className="btn ghost xs" aria-label={`${empty ? "Renseigner" : "Modifier"} : ${f.label}`} onClick={() => { setDraft(initialDraft(f)); setEditing(true); }}>
          <Icon name={empty ? "plus" : "edit"} />
        </button>
      )}
      {f.help && <span className="faint proc-field-help">{f.help}</span>}
      {f.provenance && !f.conflict && (
        <span className="proc-field-proof">
          <span className={`proof-chip is-${f.provenance.source}`}>{f.provenance.label}</span>
          {f.provenance.document_id && <a href={`#/documents/${f.provenance.document_id}`}>{f.provenance.document_title}</a>}
          {f.provenance.period && <span className="faint"> · {f.provenance.period}</span>}
          {f.provenance.evidence && <q>{f.provenance.evidence}</q>}
        </span>
      )}
      {f.conflict && (
        <div className="notice danger proc-conflict" role="alert">
          <Icon name="alert" />
          <div>
            <p>Tu as saisi <b>{f.conflict.value}</b>, mais le justificatif indique <b>{f.conflict.expected}</b>{f.conflict.evidence && <> (<q>{f.conflict.evidence}</q>)</>}. Le dossier reste bloqué tant que les deux ne concordent pas.</p>
            <div className="actions">
              {editable && <button type="button" className="btn sm" disabled={busy} onClick={restore}>Reprendre {f.conflict.expected}</button>}
              {f.conflict.document_id && <a className="btn quiet sm" href={`#/documents/${f.conflict.document_id}`}>Le document se trompe : le corriger</a>}
            </div>
          </div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}

// ------------------------------------------------------------------ pièces
function Pieces({ view, editable, onView }: { view: ProcedureView; editable: boolean; onView: (v: ProcedureView) => void }) {
  const [busy, error, run] = useAsyncAction();
  const set = (piece: ProcedurePiece, ids: number[]) =>
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${view.id}/pieces/${piece.key}`, { method: "PUT", body: { document_ids: ids } }));
    });
  return (
    <section className="card section" aria-labelledby="pieces-title">
      <div className="card-head">
        <h2 id="pieces-title">Les pièces, tirées de ton coffre</h2>
        <a className="btn link" href="#/documents">Ouvrir le coffre</a>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="proc-pieces">
        {view.pieces.map((piece) => {
          const ids = piece.attached.map((d) => d.id);
          const toCheck = piece.attached.some((d) => d.status !== "verified");
          return (
            <li key={piece.key} className={piece.missing ? "is-missing" : ""}>
              <div className="proc-piece-head">
                <b>{piece.label}</b>
                <span className={`tag ${piece.missing || toCheck ? "warn" : piece.attached.length ? "ok" : ""}`}>
                  {piece.missing
                    ? piece.count > 1 ? `${piece.attached.length} sur ${piece.count}` : "Manquante"
                    : toCheck ? "À vérifier" : piece.attached.length ? "Jointe" : piece.required ? "À joindre" : "Facultative"}
                </span>
              </div>
              {piece.help && <p className="faint">{piece.help}</p>}
              <ul className="plain-list">
                {piece.attached.map((d) => (
                  <li key={d.id} className="proc-doc">
                    <Icon name="doc" size={16} />
                    <a href={`#/documents/${d.id}`}>{d.title}</a>
                    {d.document_date && <span className="faint">{fullDate(d.document_date)}</span>}
                    {d.status !== "verified" && <a className="tag warn" href={`#/documents/${d.id}`}>à vérifier dans le coffre</a>}
                    {editable && <button type="button" className="btn ghost xs" aria-label={`Retirer ${d.title}`} disabled={busy} onClick={() => set(piece, ids.filter((x) => x !== d.id))}><Icon name="close" /></button>}
                  </li>
                ))}
              </ul>
              {editable && piece.candidates.length > 0 && (
                <details className="proc-candidates">
                  <summary>Joindre un document du coffre ({piece.candidates.length})</summary>
                  <ul className="plain-list">
                    {piece.candidates.map((d) => (
                      <li key={d.id} className="proc-doc">
                        <Icon name="doc" size={16} />
                        <span>{d.title}</span>
                        {d.document_date && <span className="faint">{fullDate(d.document_date)}</span>}
                        <button type="button" className="btn quiet xs" disabled={busy} onClick={() => set(piece, [...ids, d.id])}>Joindre</button>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {editable && piece.missing && piece.candidates.length === 0 && (
                <p className="faint">Pas encore dans ton coffre : <a href="#/documents">dépose-le</a> (photo ou PDF), Bank le joindra ici.</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ lettre
function Letter({ view }: { view: ProcedureView }) {
  const toast = useToast();
  const copy = () => {
    void navigator.clipboard?.writeText(view.letter ?? "").then(
      () => toast("Lettre copiée."),
      () => toast("Copie impossible : sélectionne le texte."),
    );
  };
  return (
    <section className="card section" aria-labelledby="letter-title">
      <div className="card-head">
        <h2 id="letter-title">La lettre</h2>
        <div className="actions" style={{ marginTop: 0 }}>
          <button type="button" className="btn quiet sm" onClick={copy}><Icon name="copy" /> Copier</button>
          <a className="btn quiet sm" href={`/api/procedures/${view.id}/letter`}><Icon name="download" /> Télécharger</a>
        </div>
      </div>
      <pre className="proc-letter" tabIndex={0} aria-label="Texte de la lettre">{view.letter}</pre>
    </section>
  );
}

// ------------------------------------------------------------------ revue obligatoire
function Review({ view, onView }: { view: ProcedureView; onView: (v: ProcedureView) => void }) {
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [busy, error, run] = useAsyncAction();
  const celebrate = useCelebrate();
  const decls = view.template.declarations;
  const all = decls.every((d) => checked.has(d.key));
  const approve = () =>
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${view.id}/approve`, { method: "POST", body: { hash: view.hash, declarations: [...checked] } }));
      celebrate("done", "Dossier validé : tu peux le transmettre.");
    });
  const filled = view.fields.filter((f) => f.active && f.value !== null && f.value !== "");
  const attached = view.pieces.flatMap((p) => p.attached.map((d) => ({ piece: p.label, doc: d })));
  return (
    <section className="card section proc-review" aria-labelledby="review-title">
      <div className="card-head">
        <h2 id="review-title">{view.approved ? "Dossier validé" : "Relire et valider"}</h2>
        <span className="faint">Empreinte {view.hash.slice(0, 12)}</span>
      </div>
      <p className="muted">Voici exactement ce qui sera transmis. Ta validation porte sur cette version : si une valeur ou une pièce change ensuite, il faudra relire.</p>
      <dl className="proc-recap">
        <div><dt>Destinataire</dt><dd>{view.template.recipient}</dd></div>
        {filled.map((f) => <div key={f.key}><dt>{f.label}</dt><dd>{f.type === "bool" ? (f.value ? "Oui" : "Non") : f.display}</dd></div>)}
        <div>
          <dt>Pièces jointes</dt>
          <dd>{attached.length ? attached.map((a) => `${a.doc.title} (${a.piece.toLowerCase()})`).join(" ; ") : "Aucune"}</dd>
        </div>
        {view.letter && <div><dt>Lettre</dt><dd>Le texte ci-dessus, daté du jour de l'envoi.</dd></div>}
      </dl>
      {!view.approved && (
        <>
          {decls.length > 0 && (
            <fieldset className="proc-declarations">
              <legend>Ce que tu engages en transmettant</legend>
              {decls.map((d) => (
                <label key={d.key} className={`check proc-decl is-${d.kind}`}>
                  <input type="checkbox" checked={checked.has(d.key)} onChange={(e) => {
                    const next = new Set(checked);
                    if (e.target.checked) next.add(d.key);
                    else next.delete(d.key);
                    setChecked(next);
                  }} />
                  <span><b>{d.kind === "consequence" ? "Conséquence" : d.kind === "engagement" ? "Engagement" : "Sur l'honneur"}</b> — {d.text}</span>
                </label>
              ))}
            </fieldset>
          )}
          <div className="actions">
            <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy || !all} onClick={approve}><Icon name="check" /> Je valide ce dossier</button>
            {!all && <span className="faint">Coche chaque point pour valider.</span>}
          </div>
        </>
      )}
      {view.approved && <Transmit view={view} onView={onView} />}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function Transmit({ view, onView }: { view: ProcedureView; onView: (v: ProcedureView) => void }) {
  const today = new Date().toLocaleDateString("sv-SE"); // AAAA-MM-JJ à l'heure locale
  const [reference, setReference] = useState("");
  const [sentOn, setSentOn] = useState(today);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${view.id}/sent`, { method: "POST", body: { reference: reference || null, sent_on: sentOn } }));
      toast("C'est noté : Bank suit la réponse.");
    });
  };
  return (
    <div className="proc-transmit">
      <p className="notice"><Icon name="send" /><span><b>À toi de transmettre.</b> Bank n'envoie rien à ta place : {view.template.channel === "online_official" ? "fais la demande sur le site officiel en suivant les étapes, avec ces valeurs" : view.template.channel === "letter" ? "envoie la lettre (en ligne si le destinataire le permet, sinon en recommandé)" : "présente ce dossier à ta banque"}, puis reviens le dire ici.</span></p>
      {view.template.official_url && (
        <a className="btn quiet sm" href={view.template.official_url} target="_blank" rel="noopener noreferrer">Ouvrir le site officiel <Icon name="external" /></a>
      )}
      <form className="form-grid proc-sent" onSubmit={submit}>
        <Field label="Transmis le"><input type="date" value={sentOn} max={today} onChange={(e) => setSentOn(e.target.value)} required /></Field>
        <Field label="Référence (facultatif)" hint="Numéro de dossier, d'accusé de réception ou de recommandé"><input value={reference} maxLength={80} onChange={(e) => setReference(e.target.value)} /></Field>
        <div className="actions"><button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="send" /> Je l'ai transmis</button></div>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ suivi et résultat
function FollowUp({ view, onView }: { view: ProcedureView; onView: (v: ProcedureView) => void }) {
  const [outcome, setOutcome] = useState<string>("obtenu");
  const [amount, setAmount] = useState<number | null>(null);
  const [period, setPeriod] = useState<Period>("once");
  const [note, setNote] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const celebrate = useCelebrate();
  const waiting = view.stage === "waiting" || view.stage === "sent";
  const snooze = (days: number) =>
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${view.id}/snooze`, { method: "POST", body: { days } }));
      toast(`Prochain point dans ${days} jours.`);
    });
  const close = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      onView(await api<ProcedureView>(`/api/procedures/${view.id}/close`, {
        method: "POST",
        body: { outcome, amount_cents: outcome === "obtenu" ? amount : null, period, note: note || null },
      }));
      // Un résultat obtenu avec son montant se fête comme un gain confirmé ; le reste est simplement noté.
      if (outcome === "obtenu" && amount) celebrate("coin", "Résultat enregistré : compté comme obtenu.");
      else toast("Résultat enregistré.");
    });
  };
  return (
    <section className="card section" aria-labelledby="follow-title">
      <h2 id="follow-title">Suivi</h2>
      <dl className="proc-recap">
        {view.sent_on && <div><dt>Transmis le</dt><dd>{fullDate(view.sent_on)}</dd></div>}
        {view.reference && <div><dt>Référence</dt><dd>{view.reference}</dd></div>}
        {waiting && view.next_check && <div><dt>Prochain point</dt><dd>{fullDate(view.next_check)}{view.template.followup_label ? ` — ${view.template.followup_label}` : ""}</dd></div>}
        {view.escalation && waiting && <div><dt>Si pas de réponse</dt><dd>{view.escalation.label} à partir du {fullDate(view.escalation.from)}, au plus tard le {fullDate(view.escalation.until)}.</dd></div>}
        {view.outcome && <div><dt>Résultat</dt><dd>{OUTCOME_LABEL[view.outcome.result] ?? view.outcome.result}{view.outcome.amount_cents ? ` — ${money(view.outcome.amount_cents)}${view.outcome.period === "month" ? " par mois" : view.outcome.period === "year" ? " par an" : ""}` : ""}{view.outcome.note ? ` · ${view.outcome.note}` : ""}</dd></div>}
      </dl>
      {view.due && <p className="notice warn"><Icon name="clock" /><span>C'est le moment de vérifier : réponse reçue ? Sinon, relance l'organisme ou {view.escalation ? "saisis le médiateur" : "repousse le prochain point"}.</span></p>}
      {waiting && (
        <>
          <div className="actions">
            <button type="button" className="btn quiet sm" disabled={busy} onClick={() => snooze(15)}>Pas encore de réponse : refaire le point dans 15 jours</button>
          </div>
          <form className="proc-close" onSubmit={close}>
            <fieldset>
              <legend>Tu as la réponse ?</legend>
              <div className="pills" role="radiogroup" aria-label="Résultat">
                {Object.entries(OUTCOME_LABEL).map(([k, label]) => (
                  <button key={k} type="button" role="radio" className="pill" aria-checked={outcome === k} onClick={() => setOutcome(k)}>{label}</button>
                ))}
              </div>
              {outcome === "obtenu" && (
                <div className="form-grid">
                  <Field label="Montant obtenu" hint="Ce qui est arrivé (ou arrivera) sur ton compte"><EuroInput value={amount} onChange={setAmount} /></Field>
                  <Field label="Périodicité">
                    <select value={period} onChange={(e) => setPeriod(e.target.value as Period)}>
                      <option value="once">Une fois</option>
                      <option value="month">Par mois</option>
                      <option value="year">Par an</option>
                    </select>
                  </Field>
                </div>
              )}
              <Field label="Note (facultatif)"><input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} /></Field>
            </fieldset>
            <div className="actions"><button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer le résultat</button></div>
          </form>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ colonne de droite
function HowTo({ template: t }: { template: ProcedureTemplate }) {
  if (t.steps.length === 0 && t.sources.length === 0) return null;
  return (
    <section className="card proc-howto" aria-labelledby="howto-title">
      <h2 id="howto-title">Comment faire</h2>
      {t.steps.length > 0 && <ol className="steps">{t.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
      {t.official_url && <a className="btn quiet sm" href={t.official_url} target="_blank" rel="noopener noreferrer">Site officiel <Icon name="external" /></a>}
      {t.sources.length > 0 && <Sources sources={t.sources} />}
      {t.confidence !== "—" && <p className="faint">Confiance dans ces étapes : {t.confidence}. Les sites changent : en cas d'écart, le site officiel fait foi.</p>}
    </section>
  );
}

function Notes({ view, onView }: { view: ProcedureView; onView: (v: ProcedureView) => void }) {
  const [text, setText] = useState(view.notes);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const dirty = text !== view.notes;
  return (
    <section className="card proc-notes" aria-labelledby="notes-title">
      <h2 id="notes-title">Notes</h2>
      <textarea value={text} rows={4} maxLength={4000} onChange={(e) => setText(e.target.value)} aria-label="Tes notes sur cette démarche" placeholder="Appel du 12/11, conseiller…" />
      {dirty && (
        <button type="button" className="btn quiet sm" disabled={busy} onClick={() => void run(async () => {
          onView(await api<ProcedureView>(`/api/procedures/${view.id}`, { method: "PATCH", body: { notes: text } }));
          toast("Notes enregistrées.");
        })}>Enregistrer</button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function Timeline({ view }: { view: ProcedureView }) {
  return (
    <section className="card proc-timeline" aria-labelledby="timeline-title">
      <h2 id="timeline-title">Historique</h2>
      <ol className="plain-list">
        {view.events.map((e, i) => (
          <li key={i}><span className="faint">{shortDate(e.ts)}</span> {EVENT_LABEL[e.kind] ?? e.kind}</li>
        ))}
      </ol>
    </section>
  );
}

function DeleteProcedure({ id }: { id: number }) {
  const [armed, setArmed] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  return (
    <div className="proc-delete">
      {armed ? (
        <>
          <button type="button" className="btn danger sm" disabled={busy} onClick={() => void run(async () => {
            await api(`/api/procedures/${id}`, { method: "DELETE" });
            toast("Démarche supprimée.");
            window.location.hash = "#/demarches";
          })}>Confirmer la suppression</button>
          <button type="button" className="btn quiet sm" onClick={() => setArmed(false)}>Annuler</button>
        </>
      ) : (
        <button type="button" className="btn link" onClick={() => setArmed(true)}><Icon name="trash" size={14} /> Supprimer cette démarche</button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
