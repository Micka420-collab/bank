// Comparateur : capital et horizon identiques, performances fictives non présentées comme des cours.
// Le repère d'inflation (IPC Insee) vient de la section Pouvoir d'achat : un seul appel pour la page.
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { useAsyncAction } from "./ui";
import type { SalaryPowerView } from "./PurchasingPower";

type Projection = {
  future_nominal_cents: number; future_real_cents: number; net_gain_cents: number;
  real_gain_cents: number; inflation_only_threshold_cents: number;
  hypothetical_shock_value_cents: number; hypothetical_shock_loss_cents: number;
  effective_annual_real_pct: number;
};
type Scenario = {
  kind: string; label: string; status: "taux_dated" | "hypothese"; gross_pct: number;
  fees_pct: number; tax_pct: number; risk_level: string; rates_as_of: string | null;
  room_cents: number | null; source: string; warning: string;
  projection: Projection;
  suitability: { eligible_to_consider: boolean; blockers: string[]; cautions: string[] };
};
type Comparison = {
  capital_cents: number; horizon_months: number; inflation_pct: number;
  context: {
    risk_profile: string; risk_capacity_limited: boolean; risk_reason: string;
    declared_horizon: string | null; reserve_gap_cents: number | null;
    overdraft_risk: boolean | null; data_complete: boolean;
  };
  scenarios: Scenario[]; note: string;
};
export type InflationBenchmark = SalaryPowerView["benchmark"];

/** Rouge/vert réservés au signe du gain réel écrit en toutes lettres ; un gain nul reste neutre. */
export function gainClass(cents: number): string {
  return cents < 0 ? "is-negative" : cents > 0 ? "is-positive" : "is-neutral";
}

export function InvestmentComparator({ suggestedCapitalCents, benchmark }: {
  suggestedCapitalCents?: number | null;
  benchmark?: InflationBenchmark | null;
}) {
  const [capital, setCapital] = useState("1000");
  const [months, setMonths] = useState("60");
  const [inflation, setInflation] = useState("");
  const [includeMarket, setIncludeMarket] = useState(false);
  const [gross, setGross] = useState("5");
  const [fees, setFees] = useState("0.5");
  const [tax, setTax] = useState("30");
  const [loss, setLoss] = useState("25");
  const [lock, setLock] = useState("0");
  const [result, setResult] = useState<Comparison | null>(null);
  const [busy, error, run] = useAsyncAction();
  useEffect(() => {
    if (suggestedCapitalCents !== null && suggestedCapitalCents !== undefined
      && suggestedCapitalCents >= 10_000 && suggestedCapitalCents <= 100_000_000) {
      setCapital(String(suggestedCapitalCents / 100));
      setResult(null);
    }
  }, [suggestedCapitalCents]);

  const reference = benchmark ?? null;
  const currentInflation = inflation === "" ? String(reference?.annual_pct ?? 0) : inflation;
  const valid = [capital, months, currentInflation, ...(includeMarket ? [gross, fees, tax, loss, lock] : [])]
    .every((x) => x.trim() !== "" && Number.isFinite(Number(x)))
    && Number(capital) >= 100 && Number(capital) <= 1_000_000
    && Number(months) >= 1 && Number(months) <= 360 && Number.isInteger(Number(months))
    && Number(currentInflation) > -29 && Number(currentInflation) <= 100
    && (!includeMarket || (
      Number(gross) > -99 && Number(gross) <= 200 && Number(fees) >= 0 && Number(fees) <= 30
      && Number(tax) >= 0 && Number(tax) <= 100 && Number(loss) >= 0 && Number(loss) <= 100
      && Number(lock) >= 0 && Number(lock) <= 360 && Number.isInteger(Number(lock))
    ));
  const change = (set: (v: string) => void, value: string) => { set(value); setResult(null); };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid || !reference) return;
    void run(async () => {
      const data = await api<Comparison>("/api/watch/investments/compare", {
        method: "POST",
        body: {
          capital_cents: Math.round(Number(capital) * 100), horizon_months: Number(months),
          inflation_pct: Number(currentInflation),
          ...(includeMarket ? {
            custom_gross_pct: Number(gross), custom_fees_pct: Number(fees),
            custom_tax_pct: Number(tax), custom_loss_pct: Number(loss),
            custom_locked_months: Number(lock),
          } : {}),
        },
      });
      setResult(data);
    });
  };

  return (
    <section className="section investment-comparator" aria-labelledby="investment-comparator-title">
      <div className="section-head">
        <h2 id="investment-comparator-title"><Icon name="coin" /> Comparer les placements dans ta situation</h2>
        <span className="tag">Simulation vérifiable</span>
      </div>
      <p className="section-note">
        Même capital, même durée et même inflation pour tous. Bank confronte les résultats à ton épargne de
        précaution et à ton horizon avant de parler de rendement. Rien n'est acheté automatiquement.
      </p>
      {!reference && <p className="muted">Le repère d'inflation de la section « Salaire, pouvoir d'achat et placements » est nécessaire pour comparer : il s'affiche dès qu'il est chargé.</p>}
      {reference && <form onSubmit={submit} className="investment-comparator-form">
        <label>Somme à comparer (€)
          <input type="number" value={capital} onChange={e => change(setCapital, e.target.value)} min="100" max="1000000" step="100" required />
        </label>
        <label>Durée (mois)
          <input type="number" value={months} onChange={e => change(setMonths, e.target.value)} min="1" max="360" step="1" required />
        </label>
        <label>Inflation annuelle supposée (%)
          <input type="number" value={currentInflation} onChange={e => change(setInflation, e.target.value)}
            min="-29" max="100" step="0.1" required />
          <span className="faint">Repère Insee {reference.as_of}{reference.stale ? " · ancien, à actualiser" : ""} — hypothèse modifiable</span>
        </label>
        <label className="investment-comparator-switch">
          <input type="checkbox" checked={includeMarket} onChange={e => { setIncludeMarket(e.target.checked); setResult(null); }} />
          Ajouter un placement de marché hypothétique
        </label>
        {includeMarket && <div className="investment-comparator-market">
          <label>Rendement brut annuel supposé (%)
            <input type="number" value={gross} onChange={e => change(setGross, e.target.value)} min="-99" max="200" step="0.1" required />
          </label>
          <label>Frais annuels (%)
            <input type="number" value={fees} onChange={e => change(setFees, e.target.value)} min="0" max="30" step="0.1" required />
          </label>
          <label>Impôt simplifié sur les gains à la sortie (%)
            <input type="number" value={tax} onChange={e => change(setTax, e.target.value)} min="0" max="100" step="0.1" required />
          </label>
          <label>Perte ponctuelle à tester (%)
            <input type="number" value={loss} onChange={e => change(setLoss, e.target.value)} min="0" max="100" step="1" required />
          </label>
          <label>Durée de blocage supposée (mois)
            <input type="number" value={lock} onChange={e => change(setLock, e.target.value)} min="0" max="360" step="1" required />
          </label>
        </div>}
        <button type="submit" className="btn" disabled={!valid || busy}>
          <Icon name="arrow" /> {busy ? "Calcul en cours..." : "Comparer les scénarios"}
        </button>
      </form>}
      {error && <p className="error" role="alert">{error}</p>}
      {result && <div className="investment-comparator-results" role="region" aria-label="Comparaison des placements">
        <div className="investment-comparator-context">
          <strong>Avant de placer</strong>
          <p>Profil calculé : {result.context.risk_profile}. {result.context.risk_reason}.</p>
          <p>{result.context.reserve_gap_cents === null
            ? "Matelas de sécurité : données insuffisantes pour le vérifier."
            : result.context.reserve_gap_cents > 0
              ? <>Matelas à compléter : <b>{money(result.context.reserve_gap_cents)}</b> avant d'envisager un risque de marché.</>
              : "Objectif de réserve de sécurité atteint selon les données actuelles."}
          </p>
          {result.context.overdraft_risk && <p className="error">Découvert prévisible : priorité à la trésorerie.</p>}
        </div>
        <div className="investment-comparator-grid">
          {result.scenarios.map(row => <article key={row.kind} className="investment-comparator-card">
            <div className="investment-comparator-head">
              <h3>{row.label}</h3>
              <span className={row.suitability.eligible_to_consider ? "tag ok" : "tag warn"}>
                {row.suitability.eligible_to_consider ? "À étudier" : "Conditions non remplies"}
              </span>
            </div>
            <p className="faint">{row.status === "hypothese"
              ? "Hypothèses saisies · pas de cours ni de rendement attendu"
              : `Taux annuel à la date du ${row.rates_as_of} · ${row.source}`}</p>
            <div className="investment-comparator-numbers">
              <div><span>Taux brut supposé</span><strong>{row.gross_pct.toLocaleString("fr-FR")} %</strong></div>
              <div><span>Capital nominal simulé</span><strong>{money(row.projection.future_nominal_cents)}</strong></div>
              <div><span>Valeur en euros d'aujourd'hui</span>
                <strong>{money(row.projection.future_real_cents)}</strong>
              </div>
              <div><span>Gain réel hypothétique</span>
                <strong className={gainClass(row.projection.real_gain_cents)}>
                  {money(row.projection.real_gain_cents, { signed: true })}
                </strong>
              </div>
            </div>
            {row.status === "hypothese" &&
              <p className="investment-comparator-stress">Choc négatif isolé : {money(row.projection.hypothetical_shock_loss_cents)}
                de perte sur le capital initial ; ce n'est pas une perte maximale.</p>}
            {row.room_cents !== null && <p className="faint">Place disponible estimée : {money(row.room_cents)}</p>}
            {row.suitability.blockers.length > 0 && <ul className="investment-comparator-blockers">
              {row.suitability.blockers.map(msg => <li key={msg}>{msg}</li>)}
            </ul>}
            {row.suitability.cautions.length > 0 && <ul className="investment-comparator-cautions">
              {row.suitability.cautions.map(msg => <li key={msg}>{msg}</li>)}
            </ul>}
            <p className="faint">{row.warning}</p>
          </article>)}
        </div>
        <p className="faint">{result.note}</p>
      </div>}
    </section>
  );
}
