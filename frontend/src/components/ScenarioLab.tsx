// « Et si… ? » : expérimentation sans écriture, ni transmission de données.
import type { MonthForecast } from "../api";
import { money, shortDate } from "../format";
import { Icon } from "./Icon";
import { EMPTY_SCENARIO, type ScenarioInput, type ScenarioResult } from "./scenario-engine";

type Props = {
  forecast: MonthForecast;
  value: ScenarioInput;
  result: ScenarioResult;
  onChange: (next: ScenarioInput) => void;
};

function eurosToCents(value: string, maximum: number): number {
  const euros = Number(value.replace(",", "."));
  return Number.isFinite(euros) ? Math.max(0, Math.min(maximum, Math.round(euros * 100))) : 0;
}

export function ScenarioLab({ forecast, value, result, onChange }: Props) {
  if (!forecast.projection.length) return null;
  const lastDate = forecast.projection[forecast.projection.length - 1]!.date;
  const allowedSavings = Math.max(0, forecast.daily_variable_rate_cents);
  const presetSaving = Math.min(500, allowedSavings);
  const expenseDate = value.expenseDate || forecast.today;
  const set = (fields: Partial<ScenarioInput>) => onChange({ ...value, ...fields });
  const preset = (expenseCents: number, savingPerDayCents: number) =>
    onChange({ expenseCents, savingPerDayCents, expenseDate: forecast.today });

  const originalEnd = forecast.projection[forecast.projection.length - 1]!.balance_cents;
  const originalOverdraft = forecast.overdraft_date;

  return (
    <details className="scenario-lab" data-active={result.active}>
      <summary>
        <span className="scenario-lab-symbol"><Icon name="spark" /></span>
        <span className="scenario-lab-heading">
          <b>Et si on changeait la suite ?</b>
          <small>Teste une facture surprise ou une économie, sans toucher à tes comptes.</small>
        </span>
        <span className="scenario-lab-arrow"><Icon name="arrow" /></span>
      </summary>
      <div className="scenario-lab-body">
        <fieldset className="scenario-presets">
          <legend>Commencer par un exemple</legend>
          <div className="scenario-presets-row">
            <button className="btn ghost sm" type="button" aria-pressed={value.expenseCents === 15000 && value.savingPerDayCents === 0}
                    onClick={() => preset(15000, 0)}>
              Facture surprise · 150 €
            </button>
            <button className="btn ghost sm" type="button" disabled={presetSaving === 0}
                    aria-pressed={value.expenseCents === 0 && value.savingPerDayCents === presetSaving && presetSaving > 0}
                    onClick={() => preset(0, presetSaving)}>
              Dépenser {money(presetSaving)} de moins par jour
            </button>
            <button className="btn ghost sm" type="button" disabled={presetSaving === 0}
                    aria-pressed={value.expenseCents === 15000 && value.savingPerDayCents === presetSaving && presetSaving > 0}
                    onClick={() => preset(15000, presetSaving)}>
              Combiner les deux
            </button>
          </div>
        </fieldset>

        <div className="scenario-fields">
          <label className="field">
            <span>Dépense imprévue (€)</span>
            <input type="number" min={0} max={100000} step={0.01} inputMode="decimal"
                   value={value.expenseCents / 100} onChange={(e) => set({ expenseCents: eurosToCents(e.target.value, 10_000_000) })} />
          </label>
          <label className="field">
            <span>Le jour de la dépense</span>
            <input type="date" min={forecast.today} max={lastDate} disabled={value.expenseCents === 0}
                   value={expenseDate} onChange={(e) => set({ expenseDate: e.target.value })} />
          </label>
          <label className="field">
            <span>Moins de dépenses courantes par jour (€)</span>
            <input type="number" min={0} max={allowedSavings / 100} step={0.01} inputMode="decimal"
                   value={value.savingPerDayCents / 100}
                   disabled={allowedSavings === 0}
                   onChange={(e) => set({ savingPerDayCents: eurosToCents(e.target.value, allowedSavings) })} />
          </label>
        </div>

        <div className="scenario-helper">
          <p className="faint">L'estimation actuelle inclut {money(allowedSavings, { whole: true })} de dépenses variables par jour.
            On ne peut pas économiser davantage que cette somme dans ce modèle.</p>
          <button type="button" className="btn link sm" disabled={!result.active}
                  onClick={() => onChange(EMPTY_SCENARIO)}>
            <Icon name="undo" /> Réinitialiser
          </button>
        </div>

        {result.active ? (
          <div className="scenario-outcome">
            <div className="scenario-outcome-head">
              <div>
                <span className="tag">Simulation</span>
                <h3>Voilà ce qui changerait</h3>
              </div>
              <p className={result.differenceCents >= 0 ? "scenario-diff gain" : "scenario-diff loss"}>
                {money(result.differenceCents, { signed: true })} <small>à la fin du mois</small>
              </p>
            </div>
            <dl className="scenario-results">
              <div>
                <dt>Solde en fin de mois</dt>
                <dd>{money(result.endBalanceCents, { whole: true })}</dd>
                <small>Avant : {money(originalEnd, { whole: true })}</small>
              </div>
              <div>
                <dt>Solde au plus bas</dt>
                <dd>{money(result.lowestBalanceCents, { whole: true })}</dd>
                <small>Le {shortDate(result.lowestDate)} · avant : {money(forecast.lowest_balance_cents, { whole: true })}</small>
              </div>
              <div>
                <dt>Premier découvert estimé</dt>
                <dd>{result.overdraftDate ? shortDate(result.overdraftDate) : "Aucun"}</dd>
                <small>Avant : {originalOverdraft ? shortDate(originalOverdraft) : "aucun"}</small>
              </div>
            </dl>
            <p role="status" className={result.overdraftDate ? "notice warn" : "notice"}>
              <Icon name={result.overdraftDate ? "alert" : "check"} />
              <span>{result.overdraftDate
                ? "Avec ces hypothèses, le solde passerait sous zéro le " + shortDate(result.overdraftDate) + "."
                : "Avec ces hypothèses, aucun solde négatif n'apparaît dans la projection."}</span>
            </p>
            <p className="faint">
              Calcul : {money(result.expenseCents, { whole: true })} de dépense ponctuelle, moins {money(result.totalSavedCents, { whole: true })}
              d'économies cumulées du lendemain à la fin du mois. La courbe en pointillés montre le scénario.
            </p>
          </div>
        ) : (
          <p className="scenario-start muted">Choisis un exemple ou ajuste les montants : le résultat et la courbe se mettront à jour immédiatement.</p>
        )}
        <p className="scenario-disclaimer">
          Simulation indicative à partir des opérations et échéances déjà prévues. Les revenus, prélèvements et dépenses réels peuvent différer.
          Rien n'est enregistré, envoyé ou exécuté.
        </p>
      </div>
    </details>
  );
}
