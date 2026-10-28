// Un seul tableau : salaires observés, pression des dépenses, inflation datée
// et rendement hypothétique net après frais/fiscalité/inflation.
import { useState, type FormEvent } from "react";
import { api, useApi } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Loading, useAsyncAction } from "./ui";

export type SalaryPowerComparison = {
  ready: boolean;
  recent_months: string[];
  comparison_months: string[];
  salary_months_found: number;
  current_salary_avg_cents: number | null;
  previous_salary_avg_cents: number | null;
  nominal_salary_change_pct: number | null;
  real_salary_change_pct: number | null;
  salary_to_keep_up_cents: number | null;
  monthly_real_gap_cents: number | null;
  current_spending_avg_cents: number | null;
  previous_spending_avg_cents: number | null;
  spending_change_pct: number | null;
  spending_share_change_points: number | null;
  spending_drivers: { category: string; old_avg_cents: number; current_avg_cents: number; delta_cents: number }[];
  correlations?: { key: string; level: "watch"; title: string; evidence: string; action: string }[];
  note: string;
};
export type SalaryPowerView = {
  available: boolean;
  benchmark: {
    as_of: string;
    published_on: string;
    annual_pct: number;
    source: string;
    url: string;
    status: string;
    stale: boolean;
    note: string;
  };
  comparison: SalaryPowerComparison | null;
  note: string;
};
type YieldResult = {
  nominal_after_fees_and_tax_pct: number;
  real_annual_pct: number;
  assumptions: { gross_pct: number; fees_pct: number; tax_pct: number; inflation_pct: number };
  note: string;
};
const signed = (n: number) => {
  const number = n.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
  return `${n < 0 ? "−" + number.slice(1) : n > 0 ? "+" + number : number} %`;
};

export function PurchasingPower() {
  const view = useApi<SalaryPowerView>("/api/watch/purchasing-power");
  const [gross, setGross] = useState("");
  const [fees, setFees] = useState("0");
  const [tax, setTax] = useState("0");
  const [inflation, setInflation] = useState("");
  const [calculated, setCalculated] = useState<YieldResult | null>(null);
  const [busy, error, run] = useAsyncAction();

  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!view.data?.benchmark) return <Loading what="Analyse du pouvoir d'achat" />;
  const data = view.data;
  const comparison = data.comparison;
  const modelInflation = inflation === "" ? String(data.benchmark.annual_pct) : inflation;
  const valid = gross.trim() !== ""
    && [gross, fees, tax, modelInflation].every((x) => x.trim() !== "" && Number.isFinite(Number(x)))
    && Number(gross) > -100 && Number(gross) <= 200
    && Number(fees) >= 0 && Number(fees) <= 30
    && Number(tax) >= 0 && Number(tax) <= 100
    && Number(modelInflation) > -30 && Number(modelInflation) <= 100;
  const calculate = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    void run(async () => {
      const result = await api<YieldResult>("/api/watch/purchasing-power/yield", {
        method: "POST",
        body: {
          gross_pct: Number(gross), fees_pct: Number(fees), tax_pct: Number(tax),
          inflation_pct: Number(modelInflation),
        },
      });
      setCalculated(result);
    });
  };
  return (
    <section className="section purchasing-power" aria-labelledby="purchasing-power-title">
      <div className="section-head">
        <h2 id="purchasing-power-title"><Icon name="trendUp" /> Salaire, pouvoir d'achat et placements</h2>
        <span className="tag">Chiffres sourcés</span>
      </div>
      <p className="section-note">
        Une augmentation ne signifie pas forcément que tu peux acheter davantage. Bank sépare
        l'évolution du salaire, le rythme de tes dépenses et l'inflation officielle.
      </p>
      <div className="purchasing-power-benchmark">
        <div>
          <span>Inflation nationale annuelle · {data.benchmark.as_of}</span>
          <strong>{signed(data.benchmark.annual_pct)}</strong>
        </div>
        <p>
          {data.benchmark.source} · publié le {data.benchmark.published_on}.
          {" "}<a href={data.benchmark.url} rel="noopener noreferrer" target="_blank">Consulter l'Insee <Icon name="external" size={13} /></a>
          {data.benchmark.stale && <span className="tag warn">Référence ancienne : à actualiser</span>}
        </p>
      </div>
      {!data.available ? (
        <p className="notice warn">Pour cette analyse personnalisée, <a href="#/connecteurs">connecte tes comptes</a> ou importe tes relevés.</p>
      ) : comparison?.ready ? (
        <>
          <div className="purchasing-power-figures">
            <div><span>Salaire moyen · 3 mois récents</span><strong>{money(comparison.current_salary_avg_cents!)}</strong></div>
            <div><span>Hausse nominale sur un an</span><strong>{signed(comparison.nominal_salary_change_pct!)}</strong></div>
            <div><span>Évolution réelle estimée</span><strong className={comparison.real_salary_change_pct! < 0 ? "is-negative" : "is-positive"}>{signed(comparison.real_salary_change_pct!)}</strong></div>
          </div>
          {comparison.monthly_real_gap_cents !== null && (
            <p className={`purchasing-power-insight${comparison.monthly_real_gap_cents < 0 ? " is-risk" : ""}`}>
              <Icon name={comparison.monthly_real_gap_cents < 0 ? "alert" : "check"} />
              {comparison.monthly_real_gap_cents < 0
                ? <>Il te manque environ <strong>{money(-comparison.monthly_real_gap_cents)}</strong> de salaire mensuel pour retrouver le pouvoir d'achat de référence, à inflation égale.</>
                : <>Ton salaire moyen dépasse de <strong>{money(comparison.monthly_real_gap_cents)}</strong> le montant nécessaire pour compenser cette inflation indicative.</>}
            </p>
          )}
          {(comparison.correlations?.length ?? 0) > 0 && (
            <div className="purchasing-power-correlations" aria-labelledby="power-correlations-title">
              <h3 id="power-correlations-title"><Icon name="link" /> Les signaux qui se recoupent</h3>
              <ul>
                {comparison.correlations?.map((finding) => (
                  <li key={finding.key}>
                    <strong>{finding.title}</strong>
                    <p>{finding.evidence}</p>
                    <p className="faint">{finding.action}</p>
                  </li>
                ))}
              </ul>
              <p className="faint">Rapprochements statistiques, pas une preuve de causalité.</p>
            </div>
          )}
          {comparison.spending_change_pct !== null && (
            <div className="purchasing-power-spending">
              <h3>Tes dépenses observées</h3>
              <p>Dépenses moyennes des trois mois récents : <b>{money(comparison.current_spending_avg_cents!)}</b>/mois,
                contre <b>{money(comparison.previous_spending_avg_cents!)}</b> sur les mêmes mois un an avant.
                Évolution : <b>{signed(comparison.spending_change_pct)}</b>.</p>
              {comparison.spending_share_change_points !== null && (
                <p>Part des dépenses dans ton salaire : <b>{comparison.spending_share_change_points > 0 ? "+" : ""}{comparison.spending_share_change_points.toLocaleString("fr-FR")} points</b> par rapport à l'an passé.</p>
              )}
              {comparison.spending_drivers.length > 0 && (
                <ul>{comparison.spending_drivers.map((d) => <li key={d.category}>
                  <span>{d.category.replaceAll("_", " ")}</span>
                  <strong>{d.delta_cents > 0 ? "+" : "−"}{money(Math.abs(d.delta_cents))}/mois</strong>
                </li>)}</ul>
              )}
            </div>
          )}
        </>
      ) : <p className="notice warn">
        Comparaison salariale non disponible : <b>{comparison?.salary_months_found ?? 0}/6</b> mois de salaires classés
        retrouvés sur les périodes correspondantes. Bank ne considère pas un mois manquant comme un salaire nul.
      </p>}
      {comparison && <p className="faint">{comparison.note}</p>}
      <div className="purchasing-power-yield">
        <h3><Icon name="coin" /> Ton simulateur de rendement réel</h3>
        <p className="muted">Compare une hypothèse de placement : son rendement supposé doit rester supérieur à l'inflation et à ses coûts pour préserver le pouvoir d'achat. Saisis les caractéristiques d'un produit réel, issues de son document d'information.</p>
        <form onSubmit={calculate}>
          <label>Rendement annuel brut supposé (%)
            <input type="number" value={gross} onChange={(e) => { setGross(e.target.value); setCalculated(null); }} placeholder="Ex. : 4" step="0.1" min="-99" max="200" required />
          </label>
          <label>Frais annuels supposés (%)
            <input type="number" value={fees} onChange={(e) => { setFees(e.target.value); setCalculated(null); }} step="0.1" min="0" max="30" required />
          </label>
          <label>Fiscalité sur gains supposée (%)
            <input type="number" value={tax} onChange={(e) => { setTax(e.target.value); setCalculated(null); }} step="0.1" min="0" max="100" required />
          </label>
          <label>Inflation annuelle de comparaison (%)
            <input type="number" value={modelInflation} onChange={(e) => { setInflation(e.target.value); setCalculated(null); }} step="0.1" min="-29" max="100" required />
          </label>
          <button type="submit" className="btn sm" disabled={!valid || busy}><Icon name="arrow" /> {busy ? "Calcul..." : "Comparer mon hypothèse"}</button>
        </form>
        {error && <p className="error" role="alert">{error}</p>}
        {calculated && (
          <div className="purchasing-power-yield-result" role="status">
            <div><span>Rendement annuel après frais et impôt simplifié</span><strong>{signed(calculated.nominal_after_fees_and_tax_pct)}</strong></div>
            <div><span>Rendement annuel réel hypothétique</span><strong className={calculated.real_annual_pct < 0 ? "is-negative" : "is-positive"}>{signed(calculated.real_annual_pct)}</strong></div>
            <p className="faint">{calculated.note}</p>
          </div>
        )}
        <p className="faint">Cette formule n'évalue ni la volatilité, ni la liquidité, ni le coût d'une sortie anticipée, ni les impôts exacts d'une enveloppe. Aucun rendement futur n'est garanti.</p>
        <a href="#/patrimoine">Consulter ton plan de placement personnalisé (priorités, épargne de sécurité, horizon et profil de risque)</a>
      </div>
      <p className="faint">{data.note}</p>
    </section>
  );
}
