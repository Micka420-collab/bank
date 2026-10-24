// Crédits : chaque prêt avec son tableau d'amortissement exact (restant dû, coût total, fin), sa mensualité
// reconnue dans les relevés, et deux simulateurs — rembourser par anticipation (indemnité plafonnée par la loi,
// datée) ou renégocier à un autre taux. Le restant dû entre dans le patrimoine net. Bank calcule, rien ne part.

import { useState, type FormEvent } from "react";
import { api, useApi, type EarlyRepayment, type LoanSchedule, type LoanView, type LoansResponse, type Renegotiation } from "../api";
import { money, shortDate } from "../format";
import { Icon } from "./Icon";
import { Segmented } from "./Segmented";
import { ErrorNote, EuroInput, Field, useAsyncAction, useToast } from "./ui";

/** « oct. 2030 » : la fin d'un prêt se lit avec son année. */
export function monthYear(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, 1)).toLocaleDateString("fr-FR", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function rateText(bp: number): string {
  return `${(bp / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
}

export function Loans({ onChange }: { onChange?: () => void }) {
  const view = useApi<LoansResponse>("/api/loans");
  const [adding, setAdding] = useState(false);
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  const data = view.data;
  const reload = () => { view.reload(); onChange?.(); };
  return (
    <section className="card section reveal loans" style={{ ["--i" as string]: 4 }} aria-labelledby="loans-title">
      <div className="card-head">
        <div>
          <h2 id="loans-title">Crédits</h2>
          {data && data.loans.length > 0 && (
            <p className="muted">{money(data.total_remaining_cents, { whole: true })} restant dû · {money(data.total_monthly_cents, { whole: true })} par mois</p>
          )}
        </div>
        <button type="button" className="btn quiet sm" aria-expanded={adding} onClick={() => setAdding((v) => !v)}>
          <Icon name={adding ? "close" : "plus"} /> {adding ? "Fermer" : "Ajouter un prêt"}
        </button>
      </div>
      {data && data.loans.length === 0 && !adding && (
        <p className="muted">Aucun prêt suivi. Ajoute-le avec ses chiffres de contrat : Bank calcule le tableau exact, retrouve la mensualité dans tes relevés et compte le restant dû dans ton patrimoine.</p>
      )}
      {adding && data && <AddLoan kinds={data.kinds} accounts={data.credit_accounts} onDone={() => { setAdding(false); reload(); }} />}
      {data?.loans.map((l) => <LoanCard key={l.id} loan={l} rules={data.rules} onChange={reload} />)}
      {data && <p className="faint">{data.disclaimer}</p>}
    </section>
  );
}

function AddLoan({ kinds, accounts, onDone }: { kinds: LoansResponse["kinds"]; accounts: LoansResponse["credit_accounts"]; onDone: () => void }) {
  const [name, setName] = useState("");
  const [lender, setLender] = useState("");
  const [kind, setKind] = useState<LoanView["kind"]>("consommation");
  const [principal, setPrincipal] = useState<number | null>(null);
  const [rate, setRate] = useState("");
  const [months, setMonths] = useState(60);
  const [first, setFirst] = useState("");
  const [insurance, setInsurance] = useState<number | null>(null);
  const [accountId, setAccountId] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!principal || !first) return;
    const bp = Math.round(Number(rate.replace(",", ".")) * 100);
    void run(async () => {
      await api("/api/loans", {
        method: "POST",
        body: {
          name, lender: lender || null, kind, principal_cents: principal, annual_rate_bp: Number.isFinite(bp) ? bp : 0,
          months, first_payment: first, insurance_monthly_cents: insurance ?? 0, account_id: accountId ? Number(accountId) : null,
        },
      });
      toast("Prêt ajouté : tableau calculé.");
      onDone();
    });
  };
  return (
    <form className="form-grid loan-add" onSubmit={submit} aria-label="Ajouter un prêt">
      <Field label="Nom"><input value={name} required maxLength={80} placeholder="Prêt auto" onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Prêteur"><input value={lender} maxLength={80} placeholder="Cofidis" onChange={(e) => setLender(e.target.value)} /></Field>
      <Field label="Genre">
        <select value={kind} onChange={(e) => setKind(e.target.value as LoanView["kind"])}>
          {kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      </Field>
      <Field label="Capital emprunté"><EuroInput value={principal} onChange={setPrincipal} required /></Field>
      <Field label="Taux nominal annuel (%)"><input inputMode="decimal" value={rate} required placeholder="3,50" onChange={(e) => setRate(e.target.value)} /></Field>
      <Field label="Durée (mois)"><input type="number" min={1} max={480} value={months} onChange={(e) => setMonths(Number(e.target.value))} /></Field>
      <Field label="Première échéance"><input type="date" required value={first} onChange={(e) => setFirst(e.target.value)} /></Field>
      <Field label="Assurance par mois" hint="0 si aucune"><EuroInput value={insurance} onChange={setInsurance} /></Field>
      {accounts.length > 0 && (
        <Field label="Compte crédit relié" hint="le solde de ta banque fera foi dans le patrimoine">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Aucun (Bank calcule le restant dû)</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
      )}
      {error && <ErrorNote message={error} />}
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Calculer et suivre</button>
    </form>
  );
}

function LoanCard({ loan, rules, onChange }: { loan: LoanView; rules: LoansResponse["rules"]; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const remove = () => void run(async () => { await api(`/api/loans/${loan.id}`, { method: "DELETE" }); onChange(); });
  const pct = Math.round(loan.progress * 100);
  return (
    <article className={`loan${open ? " is-open" : ""}`} data-kind={loan.kind}>
      <div className="loan-head">
        <div>
          <b>{loan.name}</b> <span className="faint">{loan.kind_label}{loan.lender ? ` · ${loan.lender}` : ""} · {rateText(loan.annual_rate_bp)}</span>
          <p className="loan-line">
            <span className="loan-remaining">{money(loan.remaining_cents, { whole: true })}</span> <span className="faint">restant dû · {loan.months_left} mois · fin {monthYear(loan.end)}</span>
          </p>
        </div>
        <button type="button" className="btn quiet sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? "Replier" : "Détail et simulateurs"}</button>
      </div>
      <div className="loan-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${pct} % du capital remboursé`}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <dl className="loan-stats">
        <div><dt>Mensualité</dt><dd>{money(loan.monthly_with_insurance_cents)}{loan.insurance_monthly_cents > 0 && <small className="faint"> dont {money(loan.insurance_monthly_cents)} d'assurance</small>}</dd></div>
        <div><dt>Prochaine échéance</dt><dd>{loan.next_payment ? shortDate(loan.next_payment) : "—"}</dd></div>
        <div><dt>Intérêts restants</dt><dd>{money(loan.interest_left_cents, { whole: true })}</dd></div>
        <div><dt>Coût total du prêt</dt><dd>{money(loan.total_cost_cents, { whole: true })}</dd></div>
      </dl>
      {loan.matched ? (
        <p className={`notice ${loan.matched.late ? "warn" : "ok"}`}>
          <Icon name={loan.matched.late ? "alert" : "check"} />
          <span>Mensualité reconnue dans tes relevés : <b>{loan.matched.merchant}</b>, {money(loan.matched.amount_cents)} le {shortDate(loan.matched.last_date)}{loan.matched.late ? " — le prélèvement attendu n'est pas encore passé." : "."}</span>
        </p>
      ) : (
        <p className="faint">Mensualité pas encore reconnue dans tes relevés (il faut deux prélèvements au même montant).</p>
      )}
      {loan.notes.map((n) => <p key={n} className="notice warn"><Icon name="alert" /><span>{n}</span></p>)}
      {open && (
        <div className="loan-detail">
          <Simulators loan={loan} rules={rules} />
          <ScheduleTable loanId={loan.id} />
          <div className="loan-foot">
            <button type="button" className="btn link" disabled={busy} onClick={remove}><Icon name="trash" /> Retirer ce prêt</button>
            {error && <p className="error" role="alert">{error}</p>}
          </div>
        </div>
      )}
    </article>
  );
}

function Simulators({ loan, rules }: { loan: LoanView; rules: LoansResponse["rules"] }) {
  const [tab, setTab] = useState<"early" | "renegotiate">("early");
  return (
    <div className="loan-sim">
      <Segmented value={tab} onChange={setTab} label="Simulateur" options={[{ value: "early", label: "Rembourser par anticipation" }, { value: "renegotiate", label: "Renégocier le taux" }]} />
      {tab === "early" ? <Early loan={loan} rules={rules} /> : <Renegotiate loan={loan} rules={rules} />}
    </div>
  );
}

function Early({ loan, rules }: { loan: LoanView; rules: LoansResponse["rules"] }) {
  const [amount, setAmount] = useState<number | null>(null);
  const [mode, setMode] = useState<"shorter" | "lower" | "full">("shorter");
  const [result, setResult] = useState<EarlyRepayment | null>(null);
  const [busy, error, run] = useAsyncAction();
  const rule = rules[loan.kind === "immobilier" ? "ira_immobilier" : "ira_consommation"];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      setResult(await api<EarlyRepayment>(`/api/loans/${loan.id}/early`, { method: "POST", body: { amount_cents: mode === "full" ? 0 : amount ?? 0, mode } }));
    });
  };
  return (
    <form className="loan-form" onSubmit={submit} aria-label="Remboursement anticipé">
      <Segmented value={mode} onChange={setMode} label="Que faire de l'avance" options={[{ value: "shorter", label: "Finir plus tôt" }, { value: "lower", label: "Payer moins par mois" }, { value: "full", label: "Tout solder" }]} />
      {mode !== "full" && <Field label="Montant remboursé"><EuroInput value={amount} onChange={setAmount} required /></Field>}
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || (mode !== "full" && !amount)}>Simuler</button>
      {error && <ErrorNote message={error} />}
      {result && (
        <div className="loan-result" role="status">
          <p className="loan-result-lead">
            {result.net_gain_cents > 0 ? <><b>{money(result.net_gain_cents, { whole: true })} de gain net</b> en remboursant {money(result.amount_cents, { whole: true })}</> : <><b>Pas de gain</b> : l'indemnité mange l'économie</>}
            {result.mode === "shorter" && result.new_end && <> — fin {monthYear(result.new_end)} au lieu de {monthYear(loan.end)}, même mensualité.</>}
            {result.mode === "lower" && <> — {money(result.new_payment_cents)} par mois au lieu de {money(result.old_payment_cents)}, même durée.</>}
            {result.mode === "full" && <> — prêt soldé.</>}
          </p>
          <dl className="loan-stats">
            <div><dt>Intérêts et assurance évités</dt><dd>{money(result.interest_saved_cents, { whole: true })}</dd></div>
            <div><dt>Indemnité (plafond légal)</dt><dd>{money(result.indemnity_cents)}</dd></div>
            <div><dt>Restant dû après</dt><dd>{money(result.remaining_after_cents, { whole: true })}</dd></div>
            <div><dt>Mois restants</dt><dd>{result.new_months_left} <small className="faint">au lieu de {result.old_months_left}</small></dd></div>
          </dl>
          <p className="faint">{result.indemnity_explanation}</p>
        </div>
      )}
      {rule && <p className="faint">{rule.url ? <a href={rule.url} target="_blank" rel="noopener noreferrer">{rule.title} <Icon name="external" size={12} /></a> : rule.title} · vérifié le {rule.checked}</p>}
    </form>
  );
}

function Renegotiate({ loan, rules }: { loan: LoanView; rules: LoansResponse["rules"] }) {
  const [rate, setRate] = useState("");
  const [fees, setFees] = useState<number | null>(null);
  const [buyout, setBuyout] = useState(false);
  const [result, setResult] = useState<Renegotiation | null>(null);
  const [busy, error, run] = useAsyncAction();
  const rule = rules[loan.kind === "immobilier" ? "ira_immobilier" : "ira_consommation"];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const bp = Math.round(Number(rate.replace(",", ".")) * 100);
    void run(async () => {
      setResult(await api<Renegotiation>(`/api/loans/${loan.id}/renegotiate`, { method: "POST", body: { new_rate_bp: bp, fees_cents: fees ?? 0, buyout } }));
    });
  };
  return (
    <form className="loan-form" onSubmit={submit} aria-label="Renégociation">
      <Field label="Nouveau taux (%)"><input inputMode="decimal" value={rate} required placeholder="2,50" onChange={(e) => setRate(e.target.value)} /></Field>
      <Field label="Frais (dossier, garantie)"><EuroInput value={fees} onChange={setFees} /></Field>
      <label className="check"><input type="checkbox" checked={buyout} onChange={(e) => setBuyout(e.target.checked)} /> Rachat par une autre banque (indemnité chez l'ancienne)</label>
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !rate}>Simuler</button>
      {error && <ErrorNote message={error} />}
      {result && (
        <div className="loan-result" role="status">
          <p className="loan-result-lead">
            {result.worth_it ? <><b>{money(result.net_gain_cents, { whole: true })} de gain net</b> sur les {result.months_left} mois restants</> : <><b>Ça ne vaut pas le coup</b> : {money(-result.net_gain_cents, { whole: true })} de perte</>}
            {" "}— {money(result.new_payment_cents)} par mois au lieu de {money(result.old_payment_cents)}.
          </p>
          <dl className="loan-stats">
            <div><dt>Intérêts restants aujourd'hui</dt><dd>{money(result.old_interest_left_cents, { whole: true })}</dd></div>
            <div><dt>Intérêts au nouveau taux</dt><dd>{money(result.new_interest_cents, { whole: true })}</dd></div>
            <div><dt>Frais</dt><dd>{money(result.fees_cents, { whole: true })}</dd></div>
            <div><dt>Indemnité</dt><dd>{money(result.indemnity_cents, { whole: true })}</dd></div>
          </dl>
        </div>
      )}
      {rule && buyout && <p className="faint">{rule.title} · vérifié le {rule.checked}</p>}
    </form>
  );
}

function ScheduleTable({ loanId }: { loanId: number }) {
  const sched = useApi<LoanSchedule>(`/api/loans/${loanId}/schedule`);
  if (sched.error) return <ErrorNote message={sched.error} onRetry={sched.reload} />;
  if (!sched.data) return null;
  return (
    <details className="loan-schedule">
      <summary>Tableau d'amortissement, année par année</summary>
      <table>
        <thead><tr><th>Année</th><th>Versé</th><th>Intérêts</th><th>Capital</th><th>Restant dû</th></tr></thead>
        <tbody>
          {sched.data.yearly.map((y) => (
            <tr key={y.year}>
              <td>{y.year} <small className="faint">({y.months} mois)</small></td>
              <td>{money(y.payments_cents, { whole: true })}</td>
              <td>{money(y.interest_cents, { whole: true })}</td>
              <td>{money(y.principal_cents, { whole: true })}</td>
              <td>{money(y.remaining_cents, { whole: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="faint">Mensualité hors assurance {money(sched.data.payment_cents)} · intérêts sur toute la durée {money(sched.data.total_interest_cents, { whole: true })}{sched.data.total_insurance_cents > 0 ? ` · assurance ${money(sched.data.total_insurance_cents, { whole: true })}` : ""}.</p>
    </details>
  );
}
