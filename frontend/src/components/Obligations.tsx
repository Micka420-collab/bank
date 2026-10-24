// Tes déclarations : ce que tu dois déclarer pour garder tes aides, quand, et la preuve que c'est fait. Bank
// rappelle, prépare les chiffres de la déclaration trimestrielle et constate le versement ; déclarer reste ton
// geste, sur le site de l'organisme.

import { useState, type FormEvent } from "react";
import { api, useApi, type Obligation, type ObligationPeriod, type ObligationSpec, type ObligationsView, type Prefill } from "../api";
import { longDate, money, shortDate } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "./ui";

const STATE: Record<ObligationPeriod["state"], { text: string; cls: string }> = {
  late: { text: "en retard", cls: "late" },
  soon: { text: "dans quelques jours", cls: "warn" },
  open: { text: "à faire", cls: "warn" },
  upcoming: { text: "à venir", cls: "" },
  done: { text: "fait", cls: "ok" },
  missed: { text: "manqué", cls: "late" },
};

export function Obligations() {
  const view = useApi<ObligationsView>("/api/obligations");
  const [adding, setAdding] = useState(false);
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!view.data) return <Loading what="Tes déclarations" />;
  const data = view.data;
  const onChange = () => view.reload();
  const empty = data.obligations.length === 0 && data.suggestions.length === 0;

  return (
    <section className="section ob" aria-labelledby="ob-title">
      <div className="section-head">
        <h2 id="ob-title"><Icon name="calendar" /> Tes déclarations</h2>
        <button type="button" className="btn quiet sm" onClick={() => setAdding((a) => !a)} aria-expanded={adding}>
          <Icon name="plus" /> Ajouter
        </button>
      </div>
      <p className="section-note">
        Un oubli suspend l'aide ou crée un indu. Bank te prévient à l'ouverture, avant l'échéance et en cas de retard,
        prépare les chiffres de la déclaration trimestrielle et constate le versement qui suit. Déclarer reste ton geste.
      </p>
      {adding && <AddForm catalog={data.catalog} onDone={() => { setAdding(false); onChange(); }} />}
      {data.suggestions.length > 0 && (
        <div className="stack" style={{ marginBottom: "1rem" }}>
          {data.suggestions.map((s) => <Suggestion key={s.kind} spec={s} onChange={onChange} />)}
        </div>
      )}
      {empty && !adding && (
        <p className="muted">
          Rien de suivi pour l'instant. Bank te proposera l'actualisation France Travail ou la déclaration trimestrielle
          dès qu'il reconnaît ces versements dans tes relevés ; tu peux aussi ajouter une obligation à toi.
        </p>
      )}
      <ul className="streams ob-list">
        {data.obligations.map((o) => <Row key={o.id} o={o} onChange={onChange} />)}
      </ul>
      <p className="faint" style={{ marginTop: "1rem" }}>{data.disclaimer} Règles relevées le {data.checked_on}.</p>
    </section>
  );
}

function Suggestion({ spec, onChange }: { spec: ObligationSpec; onChange: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const follow = () => run(async () => {
    await api<ObligationsView>("/api/obligations", { method: "POST", body: { kind: spec.kind } });
    toast("Je m'en occupe.");
    onChange();
  });
  const dismiss = () => run(async () => {
    await api<ObligationsView>(`/api/obligations/suggestions/${spec.kind}/dismiss`, { method: "POST" });
    onChange();
  });
  return (
    <div className="notice ob-suggest" role="note">
      <Icon name="spark" />
      <span>
        <b>{spec.label}</b> : d'après tes versements, ça te concerne. {spec.what}
        <span className="actions" style={{ marginTop: "0.5rem" }}>
          <button type="button" className="btn sm" disabled={busy} onClick={() => void follow()}>Suivre</button>
          <button type="button" className="btn link" disabled={busy} onClick={() => void dismiss()}>Non merci</button>
        </span>
        {error && <span className="error" role="alert"> {error}</span>}
      </span>
    </div>
  );
}

function Row({ o, onChange }: { o: Obligation; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const cur = o.current;
  const isDtr = o.kind === "caf_dtr" || o.kind === "msa_dtr";

  const done = (period: string, declared?: Record<string, number>) => run(async () => {
    await api<ObligationsView>(`/api/obligations/${o.id}/done`, { method: "POST", body: { period, declared } });
    toast("Noté comme fait. Je vérifierai que le versement suit.");
    setPrefill(null);
    onChange();
  });
  const undo = (period: string) => run(async () => {
    await api<ObligationsView>(`/api/obligations/${o.id}/done/${period}`, { method: "DELETE" });
    onChange();
  });
  const toggle = () => run(async () => {
    await api<ObligationsView>(`/api/obligations/${o.id}`, { method: "PATCH", body: { active: !o.active } });
    onChange();
  });
  const remove = () => run(async () => {
    await api(`/api/obligations/${o.id}`, { method: "DELETE" });
    onChange();
  });
  const figures = (period: string) => run(async () => {
    setPrefill(await api<Prefill>(`/api/obligations/${o.id}/prefill?period=${encodeURIComponent(period)}`));
  });

  return (
    <li className={`ob-row${o.active ? "" : " is-off"}`}>
      <div className="stream-head">
        <div>
          <b>{o.label}</b>{" "}
          {cur && <span className={`tag ${STATE[cur.state].cls}`}>{cur.awaiting_payment ? "versement attendu" : STATE[cur.state].text}</span>}
          {!o.active && <span className="tag">en pause</span>}
          {o.automatic && <span className="tag ok">automatique pour toi</span>}
          <div className="faint">
            {o.url ? <a href={o.url} target="_blank" rel="noopener noreferrer">{o.organism_name} <Icon name="external" /></a> : o.organism_name}
            {cur && (
              <>
                {" · "}{cur.label}
                {" · "}{cur.state === "upcoming" ? `s'ouvre le ${shortDate(cur.opens)}` : cur.state === "done" ? `fait le ${shortDate(cur.done_on ?? cur.deadline)}` : `jusqu'au ${shortDate(cur.deadline)}`}
                {cur.estimated && " (date estimée)"}
              </>
            )}
          </div>
          {cur?.awaiting_payment && (
            <div className="notice danger" style={{ marginTop: "0.5rem" }}>
              <Icon name="alert" />
              <span>Le versement qui devait suivre n'est pas arrivé. Déclaration faite ? Vérifie vite sur le site de {o.organism_name}.</span>
            </div>
          )}
          {cur?.note && <div className="faint">{cur.note}</div>}
        </div>
        <div className="actions ob-actions" style={{ marginTop: 0 }}>
          {o.needs_anchor ? null : cur && (cur.state === "open" || cur.state === "soon" || cur.state === "late") ? (
            <>
              {isDtr && <button type="button" className="btn sm" disabled={busy} onClick={() => void figures(cur.key)}>Mes chiffres</button>}
              <button type="button" className="btn quiet sm" disabled={busy} onClick={() => void done(cur.key)}>C'est fait</button>
            </>
          ) : cur?.state === "done" && cur.how === "user" ? (
            <button type="button" className="btn link" disabled={busy} onClick={() => void undo(cur.key)}>Pas fait, finalement</button>
          ) : null}
          <button type="button" className="btn link" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? "Réduire" : "Détail"}</button>
        </div>
      </div>
      {o.needs_anchor && <AnchorForm o={o} onChange={onChange} />}
      {prefill && cur && <PrefillPanel o={o} pre={prefill} onDeclared={(totals) => void done(cur.key, totals)} onClose={() => setPrefill(null)} busy={busy} />}
      {open && (
        <div className="ob-detail">
          <p>{o.what}</p>
          <p className="muted"><b>Si tu oublies :</b> {o.consequence}</p>
          <p className="faint">Source : {o.source}.</p>
          {cur && cur.proof && <p className="faint">Constaté par le virement du {longDate(cur.proof.day)} ({money(cur.proof.amount_cents)}).</p>}
          <ul className="ob-periods">
            {o.periods.map((p) => (
              <li key={p.key}>
                <span className={`tag ${STATE[p.state].cls}`}>{STATE[p.state].text}</span> {p.label}
                <span className="faint"> · {shortDate(p.opens)} → {shortDate(p.deadline)}</span>
                {p.declared && <span className="faint"> · déclaré : {Object.entries(p.declared).filter(([, v]) => v).map(([k, v]) => `${k} ${money(v)}`).join(", ")}</span>}
              </li>
            ))}
          </ul>
          {!o.needs_anchor && o.anchor_kind && <AnchorForm o={o} onChange={onChange} compact />}
          <div className="actions">
            <button type="button" className="btn link" disabled={busy} onClick={() => void toggle()}>{o.active ? "Mettre en pause" : "Reprendre"}</button>
            <button type="button" className="btn link" disabled={busy} onClick={() => void remove()}><Icon name="trash" /> Retirer</button>
          </div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}

function AnchorForm({ o, onChange, compact = false }: { o: Obligation; onChange: () => void; compact?: boolean }) {
  const [value, setValue] = useState(o.anchor ? (o.anchor_kind === "month" ? o.anchor.slice(0, 7) : o.anchor) : "");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!value) return;
    void run(async () => {
      const anchor = o.anchor_kind === "month" ? `${value}-01` : value;
      await api<ObligationsView>(`/api/obligations/${o.id}`, { method: "PATCH", body: { anchor } });
      toast("Calendrier calculé.");
      onChange();
    });
  };
  return (
    <form className={`ob-anchor${compact ? " is-compact" : ""}`} onSubmit={submit}>
      <Field label={o.anchor_label ?? "Prochaine échéance"} hint={o.anchor_kind === "month" ? "Le mois où tu déclares (visible dans Mon Compte CAF)." : undefined}>
        <input type={o.anchor_kind === "month" ? "month" : "date"} value={value} required onChange={(e) => setValue(e.target.value)} />
      </Field>
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>{compact ? "Changer la date" : "Calculer mon calendrier"}</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

function PrefillPanel({ o, pre, onDeclared, onClose, busy }: { o: Obligation; pre: Prefill; onDeclared: (totals: Record<string, number>) => void; onClose: () => void; busy: boolean }) {
  const [openCell, setOpenCell] = useState<string | null>(null);
  const kinds = pre.kinds.filter((k) => pre.totals[k.key] || k.key === "activite");
  return (
    <div className="card ob-prefill" role="region" aria-label={`Chiffres pour ${o.label}`}>
      <div className="card-head">
        <h3>Tes chiffres pour {pre.month_labels[0]} → {pre.month_labels[pre.month_labels.length - 1]}</h3>
        <button type="button" className="btn link" onClick={onClose}>Fermer</button>
      </div>
      <p className="muted">Repris de tes relevés{Object.values(pre.cells.activite ?? {}).some((c) => c.source === "bulletin") ? " et de tes bulletins de paie" : ""}. Recopie-les dans ton espace {o.organism_name}, après vérification.</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Tableau, défile de côté">
        <table className="plain ob-table">
          <thead>
            <tr><th scope="col">Ressource</th>{pre.month_labels.map((m) => <th key={m} scope="col">{m}</th>)}<th scope="col">Total</th></tr>
          </thead>
          <tbody>
            {kinds.map((k) => (
              <tr key={k.key}>
                <th scope="row">{k.label}</th>
                {pre.months.map((m) => {
                  const cell = pre.cells[k.key]?.[m];
                  const id = `${k.key}:${m}`;
                  if (!cell) return <td key={m}>—</td>;
                  return (
                    <td key={m}>
                      <button type="button" className="btn link ob-cell" aria-expanded={openCell === id} onClick={() => setOpenCell(openCell === id ? null : id)} disabled={cell.items.length === 0 && cell.source !== "bulletin"}>
                        {money(cell.amount_cents)}
                      </button>
                      {cell.source === "bulletin" && <span className="tag ok" title="Montant net social lu sur le bulletin">bulletin</span>}
                      {openCell === id && (
                        <ul className="ob-items">
                          {cell.source === "bulletin" && cell.net_paid_cents !== undefined && <li className="faint">Net versé sur le compte : {money(cell.net_paid_cents)} ; la CAF attend le montant net social.</li>}
                          {cell.items.map((it) => <li key={it.id}>{shortDate(it.day)} · {it.label} · {money(it.amount_cents)}</li>)}
                        </ul>
                      )}
                    </td>
                  );
                })}
                <td><b>{money(pre.totals[k.key] ?? 0)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pre.to_check.length > 0 && (
        <details className="ob-more">
          <summary>À regarder ({pre.to_check.length}) : des entrées dont la nature est à préciser</summary>
          <ul className="ob-items">{pre.to_check.map((it) => <li key={it.id}>{shortDate(it.day)} · {it.label} · {money(it.amount_cents)} — {it.why}</li>)}</ul>
        </details>
      )}
      {pre.excluded.length > 0 && (
        <details className="ob-more">
          <summary>Non déclarables ({pre.excluded.length}) : prestations, remboursements, virements internes</summary>
          <ul className="ob-items">{pre.excluded.map((it) => <li key={it.id}>{shortDate(it.day)} · {it.label} · {money(it.amount_cents)} — {it.why}</li>)}</ul>
        </details>
      )}
      <ul className="ob-notes">{pre.notes.map((n) => <li key={n} className="faint">{n}</li>)}</ul>
      <div className="actions">
        <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => onDeclared(pre.totals)}>J'ai déclaré ces montants</button>
        <span className="faint">Bank les garde pour les comparer au versement qui suit.</span>
      </div>
    </div>
  );
}

function AddForm({ catalog, onDone }: { catalog: ObligationSpec[]; onDone: () => void }) {
  const [kind, setKind] = useState(catalog[0]?.kind ?? "custom");
  const [label, setLabel] = useState("");
  const [anchor, setAnchor] = useState("");
  const [cadence, setCadence] = useState(12);
  const [window, setWindow] = useState(30);
  const [consequence, setConsequence] = useState("");
  const [url, setUrl] = useState("");
  const [busy, error, run] = useAsyncAction();
  const spec = catalog.find((c) => c.kind === kind) ?? catalog[0]!;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const body: Record<string, unknown> = { kind };
      if (spec.anchor && anchor) body.anchor = spec.anchor === "month" ? `${anchor}-01` : anchor;
      if (kind === "custom") Object.assign(body, { label, cadence_months: cadence, window_days: window, consequence: consequence || null, url: url || null });
      await api<ObligationsView>("/api/obligations", { method: "POST", body });
      onDone();
    });
  };
  return (
    <form className="card ob-add" onSubmit={submit} aria-label="Ajouter une obligation">
      <div className="form-grid">
        <Field label="Quoi">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {catalog.map((c) => <option key={c.kind} value={c.kind}>{c.label}</option>)}
          </select>
        </Field>
        {kind === "custom" && (
          <Field label="Nom"><input value={label} required maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="Attestation d'assurance au bailleur" /></Field>
        )}
        {spec.anchor && (
          <Field label={spec.anchor_label ?? "Prochaine échéance"}>
            <input type={spec.anchor === "month" ? "month" : "date"} value={anchor} required={kind === "custom"} onChange={(e) => setAnchor(e.target.value)} />
          </Field>
        )}
        {kind === "custom" && (
          <>
            <Field label="Tous les (mois)"><input type="number" min={1} max={60} value={cadence} onChange={(e) => setCadence(Number(e.target.value))} /></Field>
            <Field label="Me prévenir (jours avant)"><input type="number" min={1} max={120} value={window} onChange={(e) => setWindow(Number(e.target.value))} /></Field>
            <Field label="Si j'oublie" hint="Facultatif"><input value={consequence} maxLength={300} onChange={(e) => setConsequence(e.target.value)} /></Field>
            <Field label="Lien" hint="https://… facultatif"><input value={url} inputMode="url" onChange={(e) => setUrl(e.target.value)} /></Field>
          </>
        )}
      </div>
      <p className="faint" style={{ marginTop: "0.5rem" }}>{spec.what}</p>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Suivre cette obligation</button>
      </div>
    </form>
  );
}
