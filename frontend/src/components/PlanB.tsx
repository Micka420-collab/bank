// Plan B « perte de revenu » : trois scénarios en un écran — le revenu principal s'arrête, baisse de X %, fin de
// droits. Pour chacun : combien de mois l'épargne mobilisable tient face aux charges réelles, ce que les aides
// recalculées rattrapent, la fourchette d'ARE (règle officielle, jamais le centime) et quoi faire la première
// semaine, en démarches qui existent déjà dans Bank. Rien ne part vers un organisme : chaque bouton prépare,
// c'est toi qui transmets.

import { useEffect, useRef, useState } from "react";
import { api, useApi, type PlanB as PlanBData, type PlanBAction, type PlanBScenario, type ProcedureView } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { Segmented } from "./Segmented";
import { ErrorNote, useAsyncAction, useToast } from "./ui";

const DROPS = [20, 30, 50] as const;
const TAB_LABEL: Record<PlanBScenario["key"], string> = { stop: "Ça s'arrête", drop: "Ça baisse", end_of_rights: "Fin de droits" };
const WHEN_LABEL: Record<string, string> = { "J+1": "dès le lendemain", "semaine 1": "la première semaine", "avant la fin des droits": "avant la fin des droits" };

export function monthsText(s: PlanBScenario): string {
  if (s.months === null) return "Tu tiens sans entamer ton épargne";
  if (s.months === 0) return "Ça ne passe pas dès le premier mois";
  const n = s.months >= 10 ? Math.round(s.months) : s.months;
  const until = s.runway_label ? ` — jusqu'à ${s.runway_label}` : "";
  return `Tu tiens ${String(n).replace(".", ",")} mois${until}`;
}

export function PlanB({ open = false }: { open?: boolean }) {
  const [drop, setDrop] = useState<number>(30);
  const view = useApi<PlanBData>(`/api/planb?drop=${drop}`);
  const [tab, setTab] = useState<PlanBScenario["key"]>("stop");
  const [shown, setShown] = useState(open);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (open) {
      setShown(true);
      ref.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [open]);
  const data = view.data;
  const scenarios = data?.scenarios ?? [];
  const current = scenarios.find((s) => s.key === tab) ?? scenarios[0];
  const worst = scenarios.reduce<PlanBScenario | null>((w, s) => (!w || rank(s) > rank(w) ? s : w), null);
  return (
    <section ref={ref} className="card pb reveal" aria-labelledby="pb-title" data-status={current?.status ?? "ok"} id="plan-b">
      <div className="card-head">
        <div>
          <h2 id="pb-title"><Icon name="shield" /> Ton plan B</h2>
          <p className="muted">Si ton revenu s'arrête ou baisse : combien de temps tu tiens, ce qui se déclenche, quoi faire la première semaine.</p>
        </div>
        <button type="button" className="btn quiet sm" onClick={() => setShown((v) => !v)} aria-expanded={shown} aria-controls="pb-body">
          {shown ? "Replier" : worst ? headlineOf(worst) : "Voir"}
        </button>
      </div>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {shown && data && current && (
        <div id="pb-body" className="pb-body">
          <Segmented
            value={current.key}
            onChange={(k) => setTab(k)}
            label="Scénario"
            options={scenarios.map((s) => ({ value: s.key, label: s.key === "drop" ? `Baisse de ${data.drop_percent} %` : TAB_LABEL[s.key] }))}
          />
          <ScenarioView s={current} data={data} drop={drop} onDrop={setDrop} />
          <details className="pb-sources">
            <summary>D'où viennent ces chiffres</summary>
            <ul>
              {data.sources.map((src) => (
                <li key={src.key}>
                  {src.url ? <a href={src.url} target="_blank" rel="noopener noreferrer">{src.title} <Icon name="external" size={12} /></a> : src.title}
                  {src.checked && <span className="faint"> · vérifié le {src.checked}</span>}
                </li>
              ))}
            </ul>
            {data.rules_recheck && <p className="notice warn"><Icon name="alert" /><span>Les règles ARE/ASS ont été vérifiées le {data.rules_checked} : elles changent chaque année.</span></p>}
          </details>
          {data.notes.map((n) => <p key={n} className="faint">{n}</p>)}
          <p className="faint">{data.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

function rank(s: PlanBScenario): number {
  return s.status === "risk" ? 2 : s.status === "tight" ? 1 : 0;
}

function headlineOf(s: PlanBScenario): string {
  if (s.months === null) return "Tu tiens";
  const n = s.months >= 10 ? Math.round(s.months) : s.months;
  return `${s.key === "end_of_rights" ? "Fin de droits" : s.key === "stop" ? "Sans revenu" : "En baisse"} : ${String(n).replace(".", ",")} mois`;
}

function ScenarioView({ s, data, drop, onDrop }: { s: PlanBScenario; data: PlanBData; drop: number; onDrop: (v: number) => void }) {
  const gap = s.gap_cents > 0 ? s.gap_cents : 0;
  return (
    <div className="pb-scenario" data-status={s.status}>
      <p className="pb-lead"><b>{s.title}</b> <span className="faint">{s.lead}</span></p>
      {s.key === "drop" && (
        <div className="pb-drop" role="group" aria-label="Ampleur de la baisse">
          {DROPS.map((d) => (
            <button key={d} type="button" className="pill" aria-pressed={drop === d} onClick={() => onDrop(d)}>−{d} %</button>
          ))}
        </div>
      )}
      <p className="pb-figure" role="status">{monthsText(s)}</p>
      <dl className="pb-stats">
        <div><dt>Revenus après</dt><dd>{money(s.income_after_cents, { whole: true })} / mois</dd></div>
        <div><dt>Il faut sortir</dt><dd>{money(s.needs_cents, { whole: true })} / mois</dd></div>
        <div><dt>{gap > 0 ? "Il manque" : "Marge"}</dt><dd className={gap > 0 ? "is-negative" : "is-positive"}>{money(gap > 0 ? gap : -s.gap_cents, { whole: true })} / mois</dd></div>
        <div><dt>Épargne mobilisable</dt><dd>{money(s.cash_cents, { whole: true })}</dd></div>
      </dl>
      {s.are && (
        <p className="pb-are">
          <b>Allocation chômage :</b> entre {money(s.are.low_cents, { whole: true })} et {money(s.are.high_cents, { whole: true })} brut par mois si tu y as droit
          {" "}(règle officielle : 57 % à 75 % du salaire journalier de référence).{" "}
          <a href={s.are.simulator.url ?? "#"} target="_blank" rel="noopener noreferrer">Le simulateur de France Travail fait foi <Icon name="external" size={12} /></a>
        </p>
      )}
      <div className="pb-aids">
        <h3>Les aides, recalculées</h3>
        {s.aids.length === 0 ? (
          <p className="muted">{s.aids_note}</p>
        ) : (
          <>
            <ul>
              {s.aids.map((a) => (
                <li key={a.variable}>
                  <span>{a.label}</span>
                  <span className="pb-delta">
                    {money(a.before_cents, { whole: true })} → <b>{money(a.after_cents, { whole: true })}</b>
                    {a.delta_cents !== 0 && <small className={a.delta_cents > 0 ? "is-positive" : "is-negative"}> ({money(a.delta_cents, { whole: true, signed: true })})</small>}
                  </span>
                </li>
              ))}
            </ul>
            <p className="faint">{s.aids_note}{s.css === "gratuite" ? " Complémentaire santé solidaire : gratuite." : s.css === "participation" ? " Complémentaire santé solidaire : avec participation." : ""}</p>
          </>
        )}
      </div>
      <div className="pb-actions">
        <h3>La première semaine</h3>
        {s.actions.length === 0 ? <p className="muted">Rien de particulier : ton revenu restant couvre tes charges.</p> : (
          <ol>
            {s.actions.map((a) => <ActionRow key={a.key} a={a} />)}
          </ol>
        )}
      </div>
      {s.notes.map((n) => <p key={n} className="faint">{n}</p>)}
      <p className="faint">Charges : {money(data.needs.fixed_cents, { whole: true })} fixes, {money(data.needs.variable_cents, { whole: true })} de dépenses habituelles{data.needs.provision_cents > 0 ? `, ${money(data.needs.provision_cents, { whole: true })} de pics lissés` : ""} par mois.</p>
    </div>
  );
}

function ActionRow({ a }: { a: PlanBAction }) {
  const [busy, error, run] = useAsyncAction();
  const [done, setDone] = useState(false);
  const toast = useToast();
  const prepare = () => run(async () => {
    const proc = await api<ProcedureView>("/api/procedures", { method: "POST", body: { template: a.template } });
    toast("Démarche préparée : à toi de vérifier, puis d'envoyer.");
    window.location.hash = `#/demarches/${proc.id}`;
  });
  return (
    <li className={`pb-action${done ? " is-done" : ""}`}>
      <div className="pb-action-text">
        <b>{a.title}</b> <span className="tag">{WHEN_LABEL[a.when] ?? a.when}</span>
        <p className="muted">{a.why}</p>
        {a.rule?.url && <a className="faint" href={a.rule.url} target="_blank" rel="noopener noreferrer">{a.rule.title} <Icon name="external" size={12} /></a>}
      </div>
      <div className="pb-action-do">
        {a.kind === "procedure" && <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => void prepare()}>Préparer la démarche</button>}
        {a.kind === "link" && a.url && <a className="btn sm secondary" href={a.url} target="_blank" rel="noopener noreferrer">Ouvrir le site officiel <Icon name="external" size={14} /></a>}
        {a.kind === "check" && <button type="button" className="btn sm quiet" aria-pressed={done} onClick={() => setDone((d) => !d)}><Icon name="check" /> {done ? "Fait" : "À faire"}</button>}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </li>
  );
}
