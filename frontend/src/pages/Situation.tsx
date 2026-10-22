// « Ma situation » : ce que ton agent IA sait de toi, ce qu'il te propose, et ce qu'il a fait.
// L'onboarding se remplit ici ou en discutant avec HERMÈS ; le mandat (son rôle, tes lignes rouges)
// ne se règle QU'ICI.

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  api,
  useApi,
  type AgentActivity,
  type Category,
  type ItemField,
  type OnboardingResponse,
  type PrivacyMode,
  type Proposal,
  type Question,
  type StoredAnswer,
  type Suggestion,
} from "../api";
import { Correlations } from "../components/Correlations";
import { Initiatives } from "../components/Initiatives";
import { Markdown } from "../components/Markdown";
import { ErrorNote, EuroInput, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, shortDate } from "../format";
import type { PlanningStatus } from "../api";

// ------------------------------------------------------------------ libellés
export function sourceLabel(source: string): string {
  if (source.startsWith("user")) return "déclaré par toi";
  if (source.startsWith("agent:")) return `dit à ${source.slice(6)}`;
  return source;
}

export function prettyOption(value: string): string {
  const s = value.replaceAll("_", " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function summarize(q: Question, value: unknown): string {
  const label = (v: unknown) => q.options.find((o) => o.value === v)?.label ?? String(v);
  if (value === null || value === undefined) return "—";
  switch (q.kind) {
    case "choice":
      return label(value);
    case "multi":
      return Array.isArray(value) ? value.map(label).join(", ") : String(value);
    case "money":
      return typeof value === "number" ? money(value, { whole: value % 100 === 0 }) : String(value);
    case "list": {
      if (!Array.isArray(value) || value.length === 0) return "Aucun";
      const names = value.map((it) => String((it as Record<string, unknown>).label ?? (it as Record<string, unknown>).rule ?? ""));
      return names.filter(Boolean).join(" · ") || `${value.length} élément(s)`;
    }
    case "text":
      return String(value).length > 90 ? `${String(value).slice(0, 90)}…` : String(value);
    default:
      return String(value);
  }
}

const RISK_LABEL: Record<string, string> = {
  prudent: "Prudent",
  equilibre: "Équilibré",
  dynamique: "Dynamique",
  inconnu: "À préciser",
};

// ------------------------------------------------------------------ page
export function Situation() {
  const ob = useApi<OnboardingResponse>("/api/onboarding");
  const proposals = useApi<Proposal[]>("/api/agents/proposals");
  const [section, setSection] = useState<string | null>(null);

  if (ob.error) return <ErrorNote message={ob.error} onRetry={ob.reload} />;
  if (!ob.data) return <Loading />;
  const data = ob.data;
  const firstMissing = data.questions.find((q) => data.missing.includes(q.id))?.section;
  const active = section ?? firstMissing ?? data.sections[0]?.id ?? "travail";
  const refresh = () => {
    ob.reload();
    proposals.reload();
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Ma situation</h1>
          <p className="muted">Ce que Bank sait de toi, ce qu'il a lu dans tes relevés, ce que ton agent propose.</p>
        </div>
      </div>

      <section className="card card-hero knowledge reveal" style={{ ["--i" as string]: 0 }} aria-labelledby="gauge-title">
        <p className="today-lead" id="gauge-title">Ce que Bank sait de toi</p>
        <div className="gauge" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={data.percent}
             aria-label="Complétude de ta situation">
          <i style={{ width: `${data.percent}%` }} />
        </div>
        <p className="muted">
          <b>{knowledgeLevel(data.percent)}</b> — {data.answered} réponses sur {data.total}. Bank te demande le reste
          une question à la fois, en disant pourquoi ; tu peux aussi répondre en discutant avec HERMÈS.
        </p>
        <p className="faint">
          Profil de risque : <b>{RISK_LABEL[data.risk_profile.label]}</b>
          {data.risk_profile.capacity_limited && ` — capacité limitée : ${data.risk_profile.reason.toLowerCase()}`}.
        </p>
      </section>

      <Knowledge data={data} onEdit={(s) => setSection(s)} />

      <Initiatives onChanged={refresh} />

      <Correlations />

      {proposals.data && proposals.data.length > 0 && (
        <ProposalsInbox items={proposals.data} questions={data.questions} onDecided={refresh} />
      )}

      {data.suggestions.length > 0 && (
        <DetectedFacts data={data} onSaved={ob.reload} onEdit={(s) => setSection(s)} />
      )}

      <section className="section" aria-labelledby="q-title">
        <h2 id="q-title">Questionnaire</h2>
        <nav className="chips" aria-label="Sections du questionnaire">
          {data.sections.map((s) => {
            const qs = data.questions.filter((q) => q.section === s.id && q.weight > 0);
            const done = qs.filter((q) => q.id in data.answers).length;
            return (
              <button key={s.id} type="button" className="chip" aria-pressed={s.id === active} onClick={() => setSection(s.id)}>
                {s.title}
                <span className="chip-count">{done}/{qs.length}</span>
              </button>
            );
          })}
        </nav>
        {active === "mandat" && (
          <div className="notice" style={{ marginBottom: "1.5rem" }}>
            Le mandat fixe ce que ton agent a le droit de faire. Il ne peut pas le modifier lui-même, même si on le lui
            demande : seul toi, ici, connecté à Bank.
          </div>
        )}
        <div className="questions">
          {data.questions
            .filter((q) => q.section === active)
            .map((q) => (
              <QuestionCard
                key={q.id}
                q={q}
                answer={data.answers[q.id]}
                suggestion={data.suggestions.find((s) => s.question_id === q.id)}
                onSaved={ob.reload}
              />
            ))}
        </div>
      </section>

      <DossierPreview />
      <Activity questions={data.questions} />
    </>
  );
}

// ------------------------------------------------------------------ ce que Bank sait (lisible d'un coup d'œil)
export function knowledgeLevel(percent: number): string {
  if (percent >= 80) return "Bank te connaît bien";
  if (percent >= 50) return "Bank te connaît";
  if (percent >= 20) return "Bank commence à te connaître";
  return "Bank ne te connaît pas encore";
}

function Knowledge({ data, onEdit }: { data: OnboardingResponse; onEdit: (section: string) => void }) {
  const planning = useApi<PlanningStatus>("/api/planning");
  const w = planning.data;
  const employer = w?.employer && w.usual_pay_cents
    ? `${w.employer} te verse ${money(w.usual_pay_cents, { whole: true })}${w.pay_day ? ` vers le ${w.pay_day}` : ""} de chaque mois` +
      (w.predictions[0] ? ` ; prochaine paie prévue ${money(w.predictions[0].amount_cents, { whole: true })} le ${shortDate(w.predictions[0].pay_date)}` : "") + "."
    : null;
  const detected = data.suggestions
    .map((s) => ({ s, q: data.questions.find((x) => x.id === s.question_id) }))
    .filter((x): x is { s: Suggestion; q: Question } => Boolean(x.q) && !(x.q!.id in data.answers));
  const bySection = data.sections.map((sec) => {
    const qs = data.questions.filter((q) => q.section === sec.id && q.weight > 0);
    const known = qs.filter((q) => q.id in data.answers);
    return { ...sec, known, total: qs.length };
  });
  const jump = (id: string) => window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  return (
    <section className="card section fiche" aria-labelledby="fiche-title">
      <div className="card-head"><h2 id="fiche-title">Ta fiche</h2><span className="faint">Chaque ligne se corrige en un clic</span></div>
      <dl className="fiche-lines">
        {(employer || detected.length > 0) && (
          <div className="is-observed">
            <dt>
              Lu dans tes relevés
              {detected.length > 0 && <button type="button" className="btn link" onClick={() => jump("asks-title")}>confirmer</button>}
            </dt>
            <dd>
              {employer && <span className="fiche-fact">{employer}</span>}
              {detected.map(({ s, q }) => (
                <span key={q.id} className="fiche-fact is-probable" title={s.evidence}>
                  <span className="faint">{SHORT_LABELS[q.id] ?? q.question}</span> {summarize(q, s.value)} <span className="faint">?</span>
                </span>
              ))}
            </dd>
          </div>
        )}
        {bySection.map((sec) => (
          <div key={sec.id} className={sec.known.length === 0 ? "is-empty" : ""}>
            <dt>
              {sec.title}
              <button type="button" className="btn link" onClick={() => { onEdit(sec.id); jump("q-title"); }}>
                {sec.known.length === 0 ? "compléter" : "modifier"}
              </button>
            </dt>
            <dd>
              {sec.known.length === 0
                ? <span className="faint">Rien encore ({sec.total} question{sec.total > 1 ? "s" : ""}).</span>
                : sec.known.map((q) => (
                    <span key={q.id} className="fiche-fact">
                      <span className="faint">{SHORT_LABELS[q.id] ?? q.question}</span> {summarize(q, data.answers[q.id]!.value)}
                    </span>
                  ))}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// ------------------------------------------------------------------ propositions de l'agent
function ProposalsInbox({ items, questions, onDecided }: { items: Proposal[]; questions: Question[]; onDecided: () => void }) {
  const cats = useApi<Category[]>("/api/categories");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const catName = (slug: unknown) => cats.data?.find((c) => c.slug === slug)?.name ?? String(slug);

  const decide = (p: Proposal, approve: boolean) =>
    void run(async () => {
      const r = await api<Proposal>(`/api/agents/proposals/${p.id}/${approve ? "approve" : "reject"}`, { method: "POST" });
      toast(r.status === "applied" ? (r.result ?? "Appliqué.") : r.status === "rejected" ? "Proposition refusée." : (r.result ?? r.status));
      onDecided();
    });

  return (
    <section className="section" aria-labelledby="prop-title">
      <h2 id="prop-title">Propositions à valider</h2>
      <p className="section-note">
        Ton agent ne change rien seul : il propose, tu décides. Rien n'est appliqué sans ton clic, et tout est journalisé.
      </p>
      <div className="stack">
        {items.map((p) => (
          <article className="proposal" key={p.id} aria-labelledby={`p-${p.id}`}>
            <header>
              <h3 id={`p-${p.id}`}>{p.title}</h3>
              <span className="faint">par {p.agent} · expire le {shortDate(p.expires_at)}</span>
            </header>
            {p.kind === "set_answer" ? (
              <dl className="proposal-params">
                <div>
                  <dt>{p.question ?? String(p.params.question_id)}</dt>
                  <dd>{answerPreview(p, questions)}</dd>
                </div>
              </dl>
            ) : (
              <dl className="proposal-params">
                {orderedParams(p.params).map(([k, v]) => (
                  <div key={k}>
                    <dt>{PARAM_LABELS[k] ?? k}</dt>
                    <dd>{formatParam(k, v, catName)}</dd>
                  </div>
                ))}
                {p.impact !== undefined && (
                  <div>
                    <dt>Effet</dt>
                    <dd>{p.impact} opération{p.impact > 1 ? "s" : ""} reclassée{p.impact > 1 ? "s" : ""}</dd>
                  </div>
                )}
              </dl>
            )}
            <blockquote>{p.rationale}</blockquote>
            <div className="actions">
              <button className="btn" disabled={busy} onClick={() => decide(p, true)}>Appliquer</button>
              <button className="btn quiet" disabled={busy} onClick={() => decide(p, false)}>Refuser</button>
            </div>
          </article>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

const PARAM_LABELS: Record<string, string> = {
  name: "Nom",
  category: "Catégorie",
  pattern: "Libellé contenant",
  match: "Correspondance",
  target_eur: "Objectif",
  target_date: "Échéance",
  amount_eur: "Montant",
  monthly_eur: "Par mois",
};

const PARAM_ORDER = Object.keys(PARAM_LABELS);

/** Valeur proposée pour une réponse, lisible (montants en euros, listes résumées). */
export function answerPreview(p: Proposal, questions: Question[]): string {
  const q = questions.find((x) => x.id === p.params.question_id);
  const value = p.params.value;
  if (!q) return JSON.stringify(value);
  if (q.kind === "list" && Array.isArray(value)) {
    return value
      .map((it) => {
        const item = it as Record<string, unknown>;
        const amount = Object.entries(item).find(([k]) => k.endsWith("_cents"));
        const date = item.date ?? item.target_date ?? item.end_date;
        return [item.label ?? item.rule, amount ? money(Number(amount[1]), { whole: true }) : null,
                typeof date === "string" ? shortDate(date) : null].filter(Boolean).join(" · ");
      })
      .join(" ; ") || "Aucun";
  }
  return summarize(q, value);
}

export function orderedParams(params: Record<string, unknown>): [string, unknown][] {
  const rank = (k: string) => {
    const i = PARAM_ORDER.indexOf(k);
    return i < 0 ? PARAM_ORDER.length : i;
  };
  return Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .sort(([a], [b]) => rank(a) - rank(b));
}

export function formatParam(key: string, value: unknown, catName: (slug: unknown) => string): string {
  if (value === null || value === undefined) return "—";
  if (key.endsWith("_eur")) return money(Math.round(Number(value) * 100));
  if (key === "category") return catName(value);
  if (key.endsWith("_date") && typeof value === "string") return shortDate(value);
  if (key === "match") return value === "merchant" ? "marchand exact" : "contient";
  return String(value);
}

// ------------------------------------------------------------------ faits détectés à confirmer
function DetectedFacts({ data, onSaved, onEdit }: { data: OnboardingResponse; onSaved: () => void; onEdit: (section: string) => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const byId = useMemo(() => new Map(data.questions.map((q) => [q.id, q])), [data.questions]);

  const confirm = (s: Suggestion) =>
    void run(async () => {
      await api(`/api/onboarding/${s.question_id}`, { method: "PUT", body: { value: s.value } });
      toast("Confirmé.");
      onSaved();
    });
  const confirmAll = () =>
    void run(async () => {
      for (const s of data.suggestions) {
        await api(`/api/onboarding/${s.question_id}`, { method: "PUT", body: { value: s.value } });
      }
      toast(`${data.suggestions.length} réponses confirmées.`);
      onSaved();
    });

  return (
    <section className="section" aria-labelledby="detect-title">
      <h2 id="detect-title">Lu dans tes relevés</h2>
      <p className="section-note">
        Bank a déduit ces réponses de tes opérations. Confirme ce qui est juste : ton agent saura que c'est vérifié.
      </p>
      <ul className="ledger detected">
        {data.suggestions.map((s) => {
          const q = byId.get(s.question_id);
          if (!q) return null;
          return (
            <li className="ledger-row" key={s.question_id}>
              <div className="ledger-label">
                <b>{q.question}</b>
                <div className="faint">{s.evidence}</div>
              </div>
              <div className="detected-value">
                <span>{summarize(q, s.value)}</span>
                <span className="actions" style={{ marginTop: 0 }}>
                  <button className="btn quiet" disabled={busy} onClick={() => confirm(s)}>C'est juste</button>
                  <button className="btn link" type="button" onClick={() => {
                    onEdit(q.section);
                    window.setTimeout(() => document.getElementById(`q-${q.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
                  }}>Corriger</button>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      {data.suggestions.length > 1 && (
        <div className="actions">
          <button className="btn" disabled={busy} onClick={confirmAll}>Tout confirmer</button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ une question
function QuestionCard({ q, answer, suggestion, onSaved }: {
  q: Question;
  answer: StoredAnswer | undefined;
  suggestion: Suggestion | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const open = editing || !answer;

  const save = (value: unknown) =>
    void run(async () => {
      await api(`/api/onboarding/${q.id}`, { method: "PUT", body: { value } });
      toast("Enregistré.");
      setEditing(false);
      onSaved();
    });
  const clear = () =>
    void run(async () => {
      await api(`/api/onboarding/${q.id}`, { method: "DELETE" });
      onSaved();
    });

  return (
    <article className={`question${answer ? " is-answered" : ""}`} id={`q-${q.id}`} aria-labelledby={`ql-${q.id}`}>
      <header>
        <h3 id={`ql-${q.id}`}>{q.question}</h3>
        {answer && <span className={`tag ${answer.source.startsWith("agent:") ? "" : "ok"}`}>{sourceLabel(answer.source)}</span>}
      </header>
      <p className="faint">{q.why}</p>
      {!open && answer && (
        <div className="answer-line">
          <span className="answer-value">{summarize(q, answer.value)}</span>
          <span className="actions" style={{ marginTop: 0 }}>
            <button className="btn link" type="button" onClick={() => setEditing(true)}>Modifier</button>
            <button className="btn link" type="button" disabled={busy} onClick={clear}>Effacer</button>
          </span>
        </div>
      )}
      {open && (
        <Editor q={q} initial={answer?.value ?? suggestion?.value} busy={busy} onSave={save}
                onCancel={answer ? () => setEditing(false) : undefined} />
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

function Editor({ q, initial, busy, onSave, onCancel }: {
  q: Question;
  initial: unknown;
  busy: boolean;
  onSave: (value: unknown) => void;
  onCancel?: () => void;
}) {
  const cancel = onCancel && <button type="button" className="btn link" onClick={onCancel}>Annuler</button>;

  if (q.kind === "choice") {
    return (
      <div className="pills" role="radiogroup" aria-label={q.question}>
        {q.options.map((o) => (
          <button key={o.value} type="button" role="radio" aria-checked={initial === o.value} className="pill"
                  disabled={busy} onClick={() => onSave(o.value)}>
            {o.label}
          </button>
        ))}
        {cancel}
      </div>
    );
  }
  if (q.kind === "int" && q.minimum !== null && q.maximum !== null && q.maximum - q.minimum <= 10) {
    const range = Array.from({ length: q.maximum - q.minimum + 1 }, (_, i) => (q.minimum ?? 0) + i);
    return (
      <div className="pills" role="radiogroup" aria-label={q.question}>
        {range.map((n) => (
          <button key={n} type="button" role="radio" aria-checked={initial === n} className="pill num" disabled={busy}
                  onClick={() => onSave(n)}>
            {n}
          </button>
        ))}
        {cancel}
      </div>
    );
  }
  if (q.kind === "multi") return <MultiEditor q={q} initial={initial} busy={busy} onSave={onSave} cancel={cancel} />;
  if (q.kind === "list") return <ListEditor q={q} initial={initial} busy={busy} onSave={onSave} cancel={cancel} />;
  return <ScalarEditor q={q} initial={initial} busy={busy} onSave={onSave} cancel={cancel} />;
}

function MultiEditor({ q, initial, busy, onSave, cancel }: EditorProps) {
  const [chosen, setChosen] = useState<string[]>(Array.isArray(initial) ? (initial as string[]) : []);
  const toggle = (v: string) =>
    setChosen((c) => (c.includes(v) ? c.filter((x) => x !== v) : c.length < q.max_items ? [...c, v] : c));
  return (
    <div className="stack">
      <div className="pills" role="group" aria-label={q.question}>
        {q.options.map((o) => (
          <button key={o.value} type="button" className="pill" aria-pressed={chosen.includes(o.value)} onClick={() => toggle(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
      <p className="faint">{chosen.length}/{q.max_items} choisis.</p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn" disabled={busy || chosen.length === 0} onClick={() => onSave(chosen)}>Enregistrer</button>
        {cancel}
      </div>
    </div>
  );
}

type EditorProps = { q: Question; initial: unknown; busy: boolean; onSave: (value: unknown) => void; cancel: ReactNode };

function ScalarEditor({ q, initial, busy, onSave, cancel }: EditorProps) {
  const [cents, setCents] = useState<number | null>(typeof initial === "number" ? initial : null);
  const [text, setText] = useState(typeof initial === "string" ? initial : typeof initial === "number" ? String(initial) : "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.kind === "money") onSave(cents ?? 0);
    else if (q.kind === "int") onSave(Number.parseInt(text, 10));
    else onSave(text);
  };
  return (
    <form className="answer-form" onSubmit={submit}>
      {q.kind === "money" ? (
        <span className="euro-field">
          <EuroInput value={cents} onChange={setCents} required aria-label={q.question} />
          <span aria-hidden="true">€</span>
        </span>
      ) : q.kind === "int" ? (
        <input type="number" inputMode="numeric" min={q.minimum ?? undefined} max={q.maximum ?? undefined}
               value={text} onChange={(e) => setText(e.target.value)} required aria-label={q.question} />
      ) : (
        <textarea rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} aria-label={q.question} />
      )}
      <span className="actions" style={{ marginTop: 0 }}>
        <button className="btn" disabled={busy}>Enregistrer</button>
        {cancel}
      </span>
    </form>
  );
}

type Item = Record<string, unknown>;

export function blankItem(fields: ItemField[]): Item {
  const item: Item = {};
  for (const f of fields) {
    if (f.kind === "choice") item[f.name] = f.options.includes("autre") ? "autre" : f.options[0];
    else if (f.kind === "int") item[f.name] = f.name === "priority" ? 2 : 0;
    else if (f.kind === "decimal") item[f.name] = "0";
    else if (f.kind === "money") item[f.name] = f.required ? null : 0;
    else if (f.kind === "date") item[f.name] = null;
    else item[f.name] = "";
  }
  return item;
}

export function missingFields(item: Item, fields: ItemField[]): string[] {
  return fields
    .filter((f) => f.required && (item[f.name] === null || item[f.name] === undefined || item[f.name] === ""))
    .map((f) => f.label);
}

function ListEditor({ q, initial, busy, onSave, cancel }: EditorProps) {
  const fields = q.item_fields ?? [];
  const [items, setItems] = useState<Item[]>(Array.isArray(initial) ? (initial as Item[]).map((i) => ({ ...i })) : []);
  const set = (i: number, name: string, value: unknown) =>
    setItems((list) => list.map((it, j) => (j === i ? { ...it, [name]: value } : it)));
  const incomplete = items.flatMap((it, i) => missingFields(it, fields).map((f) => `ligne ${i + 1} : ${f}`));

  return (
    <div className="stack">
      {items.length === 0 && <p className="muted">Aucun pour l'instant. Enregistre la liste vide si c'est le cas : c'est aussi une réponse.</p>}
      {items.map((it, i) => (
        <fieldset className="item" key={i}>
          <legend className="sr-only">Élément {i + 1}</legend>
          {fields.map((f) => (
            <label key={f.name} className={`field${f.name === "label" || f.name === "rule" ? " wide" : ""}`}>
              {f.label}
              <FieldInput field={f} value={it[f.name]} onChange={(v) => set(i, f.name, v)} />
            </label>
          ))}
          <button type="button" className="btn link" onClick={() => setItems((l) => l.filter((_, j) => j !== i))}>Retirer</button>
        </fieldset>
      ))}
      <div className="actions" style={{ marginTop: 0 }}>
        {items.length < q.max_items && (
          <button type="button" className="btn quiet" onClick={() => setItems((l) => [...l, blankItem(fields)])}>Ajouter</button>
        )}
        <button className="btn" disabled={busy || incomplete.length > 0} onClick={() => onSave(items)}>Enregistrer la liste</button>
        {cancel}
      </div>
      {incomplete.length > 0 && <p className="faint">À compléter : {incomplete.join(", ")}.</p>}
    </div>
  );
}

function FieldInput({ field, value, onChange }: { field: ItemField; value: unknown; onChange: (v: unknown) => void }) {
  switch (field.kind) {
    case "money":
      return <EuroInput value={typeof value === "number" ? value : null} onChange={onChange} />;
    case "choice":
      return (
        <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => <option key={o} value={o}>{prettyOption(o)}</option>)}
        </select>
      );
    case "date":
      return <input type="date" value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value || null)} />;
    case "int":
      return <input type="number" inputMode="numeric" value={String(value ?? "")} onChange={(e) => onChange(Number.parseInt(e.target.value, 10) || 0)} />;
    case "decimal":
      return <input inputMode="decimal" value={String(value ?? "")} onChange={(e) => onChange(e.target.value.replace(",", "."))} />;
    default:
      return <input value={String(value ?? "")} maxLength={160} onChange={(e) => onChange(e.target.value)} />;
  }
}

// ------------------------------------------------------------------ ce que l'agent voit
function DossierPreview() {
  const [privacy, setPrivacy] = useState<PrivacyMode>("redacted");
  const [open, setOpen] = useState(false);
  const preview = useApi<{ markdown: string }>(open ? `/api/agents/dossier?privacy=${privacy}` : null);
  return (
    <section className="section" aria-labelledby="dossier-title">
      <h2 id="dossier-title">Ce que ton agent voit</h2>
      <p className="section-note">
        Le dossier exact qu'un agent reçoit en début de session, selon le niveau de confidentialité de son jeton.
        Les noms de particuliers, IBAN, e-mails et téléphones sont masqués par défaut.
      </p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn quiet" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Masquer" : "Afficher le dossier"}
        </button>
        {open && (
          <select className="inline" value={privacy} onChange={(e) => setPrivacy(e.target.value as PrivacyMode)} aria-label="Confidentialité">
            <option value="redacted">Anonymisé</option>
            <option value="aggregates">Agrégats seuls</option>
            <option value="full">Complet</option>
          </select>
        )}
      </div>
      {open && (preview.error ? <ErrorNote message={preview.error} onRetry={preview.reload} />
        : preview.data ? <Markdown source={preview.data.markdown} /> : <Loading />)}
    </section>
  );
}

const ACTIVITY: Record<string, string> = {
  "mcp.get_dossier": "a lu ton dossier",
  "mcp.get_month": "a consulté le mois",
  "mcp.get_alerts": "a consulté les alertes",
  "mcp.spending_summary": "a analysé tes dépenses",
  "mcp.list_transactions": "a parcouru des opérations",
  "mcp.get_aids": "a consulté tes aides",
  "mcp.simulate_aids": "a simulé tes droits",
  "mcp.get_wealth": "a consulté ton patrimoine",
  "mcp.evaluate_purchase": "a évalué un achat",
  "mcp.what_if_savings": "a simulé une épargne",
  "mcp.onboarding_status": "a regardé ce qu'il lui manque",
  "mcp.record_answer": "a noté une réponse",
  "onboarding.answer": "a enregistré une réponse",
  "mcp.categorize_transaction": "a reclassé une opération",
  "mcp.propose_action": "a fait une proposition",
  "proposal.create": "a créé une proposition",
  "mcp.list_proposals": "a relu ses propositions",
  "mcp.denied": "s'est vu refuser un accès",
  "mcp.refused": "s'est vu refuser une action",
  "mcp.auth_failed": "a présenté un jeton refusé",
};

export type ActivityGroup = { first: string; last: string; agent: string; action: string; count: number; subjects: string[] };

/** Regroupe les actions identiques et consécutives d'un même agent (8 réponses notées = 1 ligne). */
export function groupActivity(items: AgentActivity[], subjectOf: (a: AgentActivity) => string | null): ActivityGroup[] {
  const groups: ActivityGroup[] = [];
  for (const a of items) {
    const g = groups[groups.length - 1];
    const subject = subjectOf(a);
    if (g && g.agent === a.agent && g.action === a.action) {
      g.count += 1;
      g.first = a.at;
      if (subject && !g.subjects.includes(subject)) g.subjects.push(subject);
      continue;
    }
    groups.push({ first: a.at, last: a.at, agent: a.agent, action: a.action, count: 1, subjects: subject ? [subject] : [] });
  }
  return groups;
}

const PLURAL_ACTIVITY: Record<string, string> = {
  "mcp.record_answer": "a noté {n} réponses",
  "mcp.propose_action": "a fait {n} propositions",
  "mcp.evaluate_purchase": "a évalué {n} achats",
  "mcp.categorize_transaction": "a reclassé {n} opérations",
};

/** Noms courts pour le journal : « a noté 3 réponses — salaire · foyer · crédits ». */
export const SHORT_LABELS: Record<string, string> = {
  employment: "situation pro",
  net_monthly_income: "salaire",
  income_variability: "variabilité du revenu",
  yearly_extras: "primes",
  side_income: "autres revenus",
  tax_bracket: "tranche d'impôt",
  household: "foyer",
  dependents: "personnes à charge",
  housing_status: "logement",
  housing_cost: "coût du logement",
  housing_project: "projet logement",
  debts: "crédits",
  outside_assets: "placements",
  emergency_target_months: "matelas visé",
  savings_capacity: "capacité d'épargne",
  life_goals: "projets",
  planned_expenses: "dépenses prévues",
  risk_horizon: "horizon",
  risk_drop: "réaction à une baisse",
  risk_experience: "expérience",
  risk_priority: "sécurité ou rendement",
  priorities: "valeurs",
  money_stress: "stress",
  notes: "notes",
  autonomy: "rôle",
  report_frequency: "fréquence des points",
  alert_threshold: "seuil d'alerte",
  red_lines: "lignes rouges",
  tone: "ton",
  set_envelope: "enveloppe",
  set_savings_goal: "épargne mensuelle",
  set_buffer: "coussin",
  create_goal: "objectif",
  create_rule: "règle de classement",
  set_answer: "réponse à valider",
};

function Activity({ questions }: { questions: Question[] }) {
  const log = useApi<AgentActivity[]>("/api/agents/activity?limit=60");
  if (!log.data || log.data.length === 0) return null;
  const known = new Set(questions.map((q) => q.id));
  const subjectOf = (a: AgentActivity): string | null => {
    const q = a.details.question;
    if (typeof q === "string") return SHORT_LABELS[q] ?? (known.has(q) ? q : null);
    if ((a.action === "mcp.denied" || a.action === "mcp.refused") && a.details.tool) return String(a.details.tool);
    if (a.action === "mcp.auth_failed" && a.details.ip) return `depuis ${String(a.details.ip)}`;
    if (typeof a.details.kind === "string") return SHORT_LABELS[a.details.kind] ?? a.details.kind;
    return null;
  };
  const groups = groupActivity(log.data, subjectOf).slice(0, 25);
  return (
    <section className="section" aria-labelledby="act-title">
      <h2 id="act-title">Ce que ton agent a fait</h2>
      <ul className="activity">
        {groups.map((g, i) => {
          const verb = g.count > 1 && PLURAL_ACTIVITY[g.action]
            ? PLURAL_ACTIVITY[g.action]!.replace("{n}", String(g.count))
            : (ACTIVITY[g.action] ?? g.action) + (g.count > 1 ? ` (×${g.count})` : "");
          return (
            <li key={i}>
              <time dateTime={g.last}>{new Date(g.last).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</time>
              <span>
                <b>{g.agent === "?" ? "Inconnu" : g.agent}</b> {verb}
                {g.subjects.length > 0 && <span className="faint"> — {g.subjects.join(" · ")}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
