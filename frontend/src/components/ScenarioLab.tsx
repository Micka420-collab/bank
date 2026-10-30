// « Et si… ? » : expérimentation sans écriture, ni transmission de données.
import { useEffect, useId, useState } from "react";
import type { MonthForecast } from "../api";
import { money, parseEuros, shortDate } from "../format";
import { Icon } from "./Icon";
import { EMPTY_SCENARIO, type ScenarioInput, type ScenarioResult } from "./scenario-engine";

type Props = {
  forecast: MonthForecast;
  /** La réserve de sécurité réglée dans Bank : le résultat dit si le scénario passe dessous. */
  bufferCents: number;
  value: ScenarioInput;
  result: ScenarioResult;
  onChange: (next: ScenarioInput) => void;
};

/** Champ en euros écrit à la française (« 12,50 », « 1 250 ») : texte local, centimes bornés remontés au parent. */
function EuroField({ id, label, cents, max, disabled, hint, onChange }: {
  id: string;
  label: string;
  cents: number;
  max: number;
  disabled?: boolean;
  hint?: string;
  onChange: (cents: number) => void;
}) {
  const [text, setText] = useState(cents ? String(cents / 100).replace(".", ",") : "");
  useEffect(() => {
    // Un préréglage ou une remise à zéro change la valeur sans passer par le champ : on le resynchronise.
    const shown = parseEuros(text);
    if ((shown ?? 0) !== cents) setText(cents ? String(cents / 100).replace(".", ",") : "");
  }, [cents]); // eslint-disable-line react-hooks/exhaustive-deps
  const parsed = text.trim() === "" ? 0 : parseEuros(text);
  const invalid = parsed === null || parsed < 0;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <span className="euro-field">
        <input id={id} inputMode="decimal" autoComplete="off" value={text} disabled={disabled} placeholder="0"
               aria-invalid={invalid || undefined} aria-describedby={hint ? `${id}-hint` : undefined}
               onChange={(e) => {
                 const next = e.target.value;
                 setText(next);
                 const v = next.trim() === "" ? 0 : parseEuros(next);
                 if (v !== null && v >= 0) onChange(Math.min(max, v));
               }} />
        <span aria-hidden="true">€</span>
      </span>
      {hint && <small id={`${id}-hint`} className="faint">{hint}</small>}
      {invalid && <small role="alert" className="field-error">Un montant en euros, par exemple 12,50.</small>}
    </div>
  );
}

const STATUS = {
  none: "",
  ok: "Avec ces hypothèses, le solde reste au-dessus de ta réserve de sécurité tout le mois.",
  under_buffer: "Avec ces hypothèses, le solde passe sous ta réserve de sécurité",
  overdraft: "Avec ces hypothèses, le solde passerait sous zéro",
} as const;

export function ScenarioLab({ forecast, bufferCents, value, result, onChange }: Props) {
  const uid = useId();
  if (!forecast.projection.length) return null;
  const lastDate = forecast.projection[forecast.projection.length - 1]!.date;
  const allowedSavings = Math.max(0, forecast.daily_variable_rate_cents);
  const presetSaving = Math.min(500, allowedSavings);
  const set = (fields: Partial<ScenarioInput>) => onChange({ ...value, ...fields });
  const preset = (expenseCents: number, savingPerDayCents: number) =>
    onChange({ expenseCents, savingPerDayCents, expenseDate: forecast.today });

  const originalEnd = forecast.projection[forecast.projection.length - 1]!.balance_cents;
  const originalOverdraft = forecast.overdraft_date;
  const buffer = Math.max(0, bufferCents);
  const statusText = result.verdict === "none" ? "" : result.verdict === "ok"
    ? (buffer > 0 ? STATUS.ok : "Avec ces hypothèses, aucun solde négatif n'apparaît dans la projection.")
    : result.verdict === "overdraft"
      ? `${STATUS.overdraft} le ${shortDate(result.overdraftDate!)}.`
      : `${STATUS.under_buffer} (${money(buffer, { whole: true })}) le ${shortDate(result.underBufferDate!)}.`;

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
          <EuroField id={`${uid}-expense`} label="Dépense imprévue" cents={value.expenseCents} max={10_000_000}
                     onChange={(expenseCents) => set({ expenseCents })} />
          <div className="field">
            <label htmlFor={`${uid}-date`}>Le jour de la dépense</label>
            <input id={`${uid}-date`} type="date" min={forecast.today} max={lastDate} disabled={value.expenseCents === 0}
                   value={value.expenseDate || forecast.today} aria-invalid={result.dateInvalid || undefined}
                   aria-describedby={result.dateInvalid ? `${uid}-date-error` : undefined}
                   onChange={(e) => set({ expenseDate: e.target.value })} />
            {result.dateInvalid && (
              <small id={`${uid}-date-error`} role="alert" className="field-error">
                Choisis un jour entre le {shortDate(forecast.today)} et le {shortDate(lastDate)} : la dépense n'est pas simulée tant que la date est hors du mois.
              </small>
            )}
          </div>
          <EuroField id={`${uid}-saving`} label="Moins de dépenses courantes par jour" cents={value.savingPerDayCents}
                     max={allowedSavings} disabled={allowedSavings === 0}
                     hint={allowedSavings > 0 ? `Au plus ${money(allowedSavings, { whole: true })} : ce que la prévision compte déjà de dépenses variables par jour.` : undefined}
                     onChange={(savingPerDayCents) => set({ savingPerDayCents })} />
        </div>

        <div className="scenario-helper">
          <p className="faint">
            {allowedSavings > 0
              ? `L'estimation actuelle inclut ${money(allowedSavings, { whole: true })} de dépenses variables par jour. On ne peut pas économiser davantage que cette somme dans ce modèle.`
              : "Pas encore assez d'opérations pour estimer tes dépenses courantes : seul le levier « dépense imprévue » est disponible."}
          </p>
          <button type="button" className="btn link sm" disabled={!result.active && !result.dateInvalid}
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
                <dd>{money(result.endBalanceCents, { whole: true })} <small>avant : {money(originalEnd, { whole: true })}</small></dd>
              </div>
              <div>
                <dt>Solde au plus bas</dt>
                <dd>{money(result.lowestBalanceCents, { whole: true })} <small>le {shortDate(result.lowestDate)} · avant : {money(forecast.lowest_balance_cents, { whole: true })}</small></dd>
              </div>
              <div>
                <dt>Premier découvert estimé</dt>
                <dd>{result.overdraftDate ? shortDate(result.overdraftDate) : "Aucun"} <small>avant : {originalOverdraft ? shortDate(originalOverdraft) : "aucun"}</small></dd>
              </div>
            </dl>
            <p className="faint">
              Calcul : {money(result.expenseCents, { whole: true })} de dépense ponctuelle{result.expenseCents > 0 ? ` le ${shortDate(result.expenseDate)}` : ""},
              moins {money(result.totalSavedCents, { whole: true })} d'économies cumulées du lendemain à la fin du mois. La courbe en pointillés montre le scénario.
            </p>
          </div>
        ) : (
          <p className="scenario-start muted">Choisis un exemple ou ajuste les montants : le résultat et la courbe se mettront à jour immédiatement.</p>
        )}
        {/* Région vivante toujours présente (une région créée avec son contenu n'est pas annoncée) ; son texte ne
            change qu'avec le verdict et sa date, pas à chaque frappe. */}
        <p role="status" aria-atomic="true" className={result.verdict === "none" ? "scenario-status" : result.verdict === "ok" ? "notice" : "notice warn"}>
          {result.verdict !== "none" && <Icon name={result.verdict === "ok" ? "check" : "alert"} />}
          <span>{statusText}</span>
        </p>
        <p className="scenario-disclaimer">
          Simulation indicative à partir des opérations et échéances déjà prévues, hors enveloppes par catégorie. Les revenus,
          prélèvements et dépenses réels peuvent différer. Pour un achat précis, « Je peux m'acheter… ? » ci-dessous donne le
          verdict de Bank. Rien n'est enregistré, envoyé ou exécuté.
        </p>
      </div>
    </details>
  );
}
