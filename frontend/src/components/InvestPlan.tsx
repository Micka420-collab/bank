// « Où placer mon argent ? » : le plan par priorités, calculé à partir de ta situation.

import { useApi, type InvestmentPlan } from "../api";
import { money } from "../format";
import { monthsLabel } from "./Decide";
import { ErrorNote, Loading } from "./ui";

const KIND_LABEL: Record<string, string> = {
  urgence: "Urgence",
  matelas: "Sécurité",
  dette: "Dette",
  projet: "Projet",
  long_terme: "Long terme",
  attente: "Plus tard",
};

const TAX_SOURCE: Record<string, string> = {
  declared: "tranche que tu as indiquée",
  document: "d'après ton avis d'impôt importé",
  estimated: "estimée à partir de ton salaire",
  unknown: "inconnue",
};

export function InvestPlan() {
  const plan = useApi<InvestmentPlan>("/api/decisions/investment-plan");
  if (plan.error) return <ErrorNote message={plan.error} onRetry={plan.reload} />;
  if (!plan.data) return <Loading what="Plan de placement" />;
  const p = plan.data;
  return (
    <section className="section" aria-labelledby="plan-title">
      <h2 id="plan-title">Où placer ton argent</h2>
      <p className="section-note">{p.summary}</p>
      {p.steps.length > 0 && (
        <ol className="plan">
          {p.steps.map((s) => (
            <li key={s.order} className={`plan-step kind-${s.kind}`}>
              <div className="plan-head">
                <span className="tag">{KIND_LABEL[s.kind] ?? s.kind}</span>
                <b>{s.title}</b>
                {s.monthly_cents > 0 && <span className="plan-amount">{money(s.monthly_cents, { whole: true })}<small>/mois</small></span>}
              </div>
              <div className="plan-where">{s.where}</div>
              <p className="muted">{s.why}</p>
              {s.months !== null && s.months > 0 && (
                <p className="faint">
                  {s.target_cents ? `Objectif ${money(s.target_cents, { whole: true })} : ` : ""}atteint en {monthsLabel(s.months)} à ce rythme.
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
      {p.long_term.length > 0 && (
        <div className="stack" style={{ marginTop: "1rem" }}>
          <h3>Répartition long terme — {money(p.long_term_monthly_cents, { whole: true })}/mois, {p.equity_share_percent} % d'actions</h3>
          <div className="stacked" aria-hidden="true">
            {p.long_term.map((a) => <i key={a.vehicle} style={{ width: `${a.share_percent}%`, background: a.vehicle === "pea" ? "var(--pen)" : a.vehicle === "per" ? "var(--warn)" : "var(--gain)" }} />)}
          </div>
          <ul className="alloc">
            {p.long_term.map((a) => (
              <li key={a.vehicle}>
                <b>{a.label}</b> — {a.share_percent} % · {money(a.monthly_cents, { whole: true })}/mois
                <div className="faint">{a.note}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="faint" style={{ marginTop: "1rem" }}>
        Tranche marginale d'imposition : <b>{p.tax.marginal_rate_percent} %</b> ({TAX_SOURCE[p.tax.source] ?? p.tax.source}).
        {p.excluded.length > 0 && <> Exclu par tes lignes rouges : {p.excluded.join(", ")}.</>}
      </p>
      <ul className="faint cautions">
        {p.cautions.map((c) => <li key={c}>{c}</li>)}
      </ul>
    </section>
  );
}
