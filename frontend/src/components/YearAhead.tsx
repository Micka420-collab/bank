// L'année qui vient : douze mois projetés depuis la fin du mois en cours, les pics (assurance annuelle, impôts,
// factures trimestrielles) posés à leur mois, le point bas de l'année et la provision mensuelle qui lisse. Une
// phrase toujours visible, le détail en dessous.

import { useState, type FormEvent } from "react";
import { api, useApi, type YearAhead as YearAheadData } from "../api";
import { money, shortDate } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, EuroInput, Field, useAsyncAction, useToast } from "./ui";

// Douze colonnes sur 390 px : trois ou quatre lettres par mois, le libellé complet reste dans l'info-bulle.
const SHORT_MONTHS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "aoû", "sep", "oct", "nov", "déc"];
export function shortMonth(month: string): string {
  const n = Number(month.slice(5, 7));
  return SHORT_MONTHS[n - 1] ?? month;
}

function headline(y: YearAheadData): string {
  const low = `${money(y.lowest_cents, { whole: true })} en ${y.lowest_label}`;
  if (y.status === "risk") return `Sous zéro vers ${y.lowest_label} (${money(y.lowest_cents, { whole: true })}) si rien ne change`;
  if (y.status === "tight") return `Ça se resserre : point bas de l'année ${low}`;
  return `Année sans découvert · point bas ${low}`;
}

export function YearAhead() {
  const view = useApi<YearAheadData>("/api/forecast/year");
  const [adding, setAdding] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!view.data) return null;
  const y = view.data;
  const ignore = (key: string, ignored: boolean) => run(async () => {
    await api("/api/forecast/year/ignore", { method: "POST", body: { key, ignored } });
    toast(ignored ? "Écarté de la projection." : "Repris dans la projection.");
    view.reload();
  });
  const remove = (key: string) => run(async () => {
    await api(`/api/forecast/year/spikes/${encodeURIComponent(key)}`, { method: "DELETE" });
    view.reload();
  });
  const max = Math.max(1, ...y.points.map((p) => Math.abs(p.end_balance_cents)));
  return (
    <details className="card ya reveal" style={{ marginTop: "1rem" }} data-status={y.status}>
      <summary>
        <span>
          <b>L'année qui vient</b> <span className="faint">{headline(y)}</span>
          {y.provision_monthly_cents > 0 && <span className="faint"> · provision {money(y.provision_monthly_cents, { whole: true })}/mois</span>}
        </span>
        <Icon name="arrow" />
      </summary>
      <div className="ya-chart" role="img" aria-label={y.points.map((p) => `${p.label} : ${money(p.end_balance_cents, { whole: true })}`).join(" ; ")}>
        {y.points.map((p) => {
          const h = Math.max(3, (Math.abs(p.end_balance_cents) / max) * 100);
          return (
            <div className={`ya-month${p.end_balance_cents < 0 ? " is-negative" : ""}${p.month === y.lowest_month ? " is-lowest" : ""}`} key={p.month} title={`${p.label} : ${money(p.end_balance_cents)}${p.spikes.length ? ` · ${p.spikes.join(", ")}` : ""}`}>
              <div className="ya-bar"><i style={{ height: `${h}%` }} /></div>
              {p.spikes_cents > 0 && <span className="ya-dot" aria-hidden="true" />}
              <span className="ya-label">{shortMonth(p.month)}</span>
            </div>
          );
        })}
      </div>
      <YearCalendar y={y} />
      <dl className="ya-stats">
        <div><dt>Revenus réguliers</dt><dd>{money(y.income_monthly_cents, { whole: true })} / mois</dd></div>
        <div><dt>Charges fixes</dt><dd>{money(y.fixed_monthly_cents, { whole: true })} / mois</dd></div>
        <div><dt>Dépenses habituelles</dt><dd>{money(y.variable_monthly_cents, { whole: true })} / mois</dd></div>
        <div><dt>Pics sur 12 mois</dt><dd>{money(y.yearly_spikes_cents, { whole: true })}</dd></div>
      </dl>
      {y.provision_monthly_cents > 0 && (
        <p className="ya-provision">
          Pour lisser ces pics : <b>{money(y.provision_monthly_cents, { whole: true })} par mois</b> de côté
          {y.savings_goal_cents >= y.provision_monthly_cents ? " — déjà couvert par ton épargne visée." : " ; Bank te propose de les réserver sur la page du jour."}
        </p>
      )}
      {y.notes.map((n) => <p key={n} className="faint">{n}</p>)}
      <div className="card-head" style={{ marginTop: "0.75rem" }}>
        <h3>Les pics</h3>
        <button type="button" className="btn quiet sm" onClick={() => setAdding((a) => !a)} aria-expanded={adding}><Icon name="plus" /> Ajouter</button>
      </div>
      {adding && <AddSpike onDone={() => { setAdding(false); view.reload(); }} />}
      {y.spikes.length === 0 ? (
        <p className="muted">Aucun pic repéré : Bank en détecte un à sa deuxième occurrence (assurance annuelle, facture trimestrielle), ou tu l'ajoutes.</p>
      ) : (
        <ul className="ya-spikes">
          {y.spikes.map((s) => (
            <li key={`${s.key}:${s.day}`}>
              <span><b>{s.label}</b> <span className="faint">{shortDate(s.day)}{s.shifted_from && ` (décalé du ${shortDate(s.shifted_from)})`} · {s.cadence === "yearly" ? "chaque année" : s.cadence === "quarterly" ? "chaque trimestre" : s.source === "planned" ? "prévu" : "ajouté"}</span></span>
              <span className="ya-amount">{money(s.amount_cents, { whole: true })}</span>
              {s.source === "detected" && <button type="button" className="btn link" disabled={busy} onClick={() => void ignore(s.key, true)}>Écarter</button>}
              {s.source === "custom" && <button type="button" className="btn link" disabled={busy} onClick={() => void remove(s.key)}>Retirer</button>}
            </li>
          ))}
        </ul>
      )}
      {y.ignored.length > 0 && (
        <p className="faint">
          Écartés : {y.ignored.map((s) => s.label).filter((v, i, a) => a.indexOf(v) === i).join(", ")}.{" "}
          <button type="button" className="btn link" disabled={busy} onClick={() => void ignore(y.ignored[0]!.key, false)}>Reprendre</button>
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <p className="faint">{y.disclaimer}</p>
    </details>
  );
}

function AddSpike({ onDone }: { onDone: () => void }) {
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState<number | null>(null);
  const [day, setDay] = useState("");
  const [every, setEvery] = useState(12);
  const [busy, error, run] = useAsyncAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!amount || !day) return;
    void run(async () => {
      await api("/api/forecast/year/spikes", { method: "POST", body: { label, amount_cents: amount, next_date: day, every_months: every } });
      onDone();
    });
  };
  return (
    <form className="form-grid ya-add" onSubmit={submit} aria-label="Ajouter un pic">
      <Field label="Quoi"><input value={label} required maxLength={60} placeholder="Taxe foncière" onChange={(e) => setLabel(e.target.value)} /></Field>
      <Field label="Montant"><EuroInput value={amount} onChange={setAmount} /></Field>
      <Field label="Prochaine fois"><input type="date" required value={day} onChange={(e) => setDay(e.target.value)} /></Field>
      <Field label="Tous les (mois)"><input type="number" min={1} max={60} value={every} onChange={(e) => setEvery(Number(e.target.value))} /></Field>
      {error && <ErrorNote message={error} />}
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Ajouter à l'année</button>
    </form>
  );
}

/** Jours fériés et vacances scolaires de l'année qui vient, mois par mois (accord « calendrier » requis). */
export function YearCalendar({ y }: { y: Pick<YearAheadData, "points" | "calendar"> }) {
  const months = y.points.filter((p) => (p.holidays?.length ?? 0) + (p.school?.length ?? 0) > 0);
  if (!y.calendar || months.length === 0) return null;
  return (
    <div className="ya-calendar">
      <h3>Jours fériés et vacances{y.calendar.academy ? ` (académie de ${y.calendar.academy}${y.calendar.school_zone ? `, ${y.calendar.school_zone}` : ""})` : ""}</h3>
      <ul>
        {months.map((p) => (
          <li key={p.month}>
            <b>{p.label}</b> <span className="faint">{[...(p.holidays ?? []), ...(p.school ?? [])].join(" · ")}</span>
          </li>
        ))}
      </ul>
      <p className="faint">{y.calendar.note}</p>
    </div>
  );
}
