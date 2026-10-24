// Retraite : à partir de ta génération (âge légal, trimestres requis — règles datées), des trimestres que tu as
// validés (ton relevé, saisi ici, chiffré) et du salaire que Bank connaît (bulletin du coffre ou relevés), trois
// départs comparés en fourchette — âge légal, taux plein, 67 ans — plus un âge au choix, et ce qu'un versement PER
// change à ton impôt. Un ordre de grandeur : le chiffre opposable est celui d'info-retraite.fr. Rien ne part.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, useApi, type Retirement as RetirementData, type RetirementDeparture, type RetirementStatus } from "../api";
import { money, plural, shortDate } from "../format";
import { Icon } from "./Icon";
import { monthYear } from "./Loans";
import { ErrorNote, EuroInput, Field, useAsyncAction, useToast } from "./ui";

type DepartureKey = "legal" | "full_rate" | "age_67" | "custom";
const ORDER: DepartureKey[] = ["legal", "full_rate", "age_67", "custom"];
const TITLES: Record<DepartureKey, string> = { legal: "Âge légal", full_rate: "Taux plein", age_67: "67 ans", custom: "Ton choix" };
const MIN_AGE = 55;
const MAX_AGE = 75;

/** « 1 120 – 1 490 € » ; un seul chiffre quand la fourchette est fermée. */
export function rangeText(low: number, high: number): string {
  const a = money(low, { whole: true });
  const b = money(high, { whole: true });
  return a === b ? a : `${a.replace(/\s?€$/, "")} – ${b}`;
}

export function ageText(years: number, months: number): string {
  return months ? `${years} ans ${months} mois` : `${years} ans`;
}

export function statusText(d: RetirementDeparture): string {
  const s: Record<RetirementStatus, string> = {
    before_legal: "avant l'âge légal",
    decote: `décote · ${plural(d.missing_quarters, "trimestre manquant", "trimestres manquants")}`,
    full: "taux plein",
    surcote: `surcote · +${plural(d.extra_quarters, "trimestre", "trimestres")}`,
  };
  return s[d.status];
}

function headline(d: RetirementDeparture): string {
  return `À ${ageText(d.age_years, d.age_months)} : ${rangeText(d.total_low_cents, d.total_high_cents)} par mois`;
}

export function Retirement({ open = false }: { open?: boolean }) {
  const [age, setAge] = useState<number | null>(null);
  const [committedAge, setCommittedAge] = useState<number | null>(null);
  const [per, setPer] = useState<number | null>(null);
  const [shown, setShown] = useState(open);
  const [selected, setSelected] = useState<DepartureKey>("full_rate");
  const [editing, setEditing] = useState(false);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const t = setTimeout(() => setCommittedAge(age), 250);
    return () => clearTimeout(t);
  }, [age]);
  useEffect(() => {
    if (open) {
      setShown(true);
      ref.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [open]);
  const qs = new URLSearchParams();
  if (committedAge !== null) qs.set("age_years", String(committedAge));
  if (per !== null && per > 0) qs.set("per_cents", String(per));
  const query = qs.toString();
  const view = useApi<RetirementData>(`/api/retirement${query ? `?${query}` : ""}`);
  const data = view.data;
  const career = data?.career ?? null;
  const departures: [DepartureKey, RetirementDeparture][] = [];
  for (const k of ORDER) {
    const d = data?.departures[k];
    if (d) departures.push([k, d]);
  }
  const current = (data?.departures[selected] ?? data?.departures.full_rate) ?? null;
  const reference = data?.departures.full_rate ?? null;
  const maxHigh = Math.max(1, ...departures.map(([, d]) => d.total_high_cents));
  const lastAuto = useRef<number | null>(null);
  useEffect(() => {
    // Un nouvel âge choisi ouvre « Ton choix » ; ensuite la personne reste libre de regarder un autre départ.
    if (committedAge !== null && data?.departures.custom && lastAuto.current !== committedAge) {
      lastAuto.current = committedAge;
      setSelected("custom");
    }
  }, [committedAge, data]);

  return (
    <section ref={ref} className="card section reveal rt" style={{ ["--i" as string]: 5 }} aria-labelledby="rt-title" id="retraite">
      <div className="card-head">
        <div>
          <h2 id="rt-title"><Icon name="clock" /> Ta retraite</h2>
          <p className="muted">À quel âge tu peux partir, avec combien par mois — et ce qu'un trimestre de plus ou de moins change.</p>
        </div>
        <button type="button" className="btn quiet sm" onClick={() => setShown((v) => !v)} aria-expanded={shown} aria-controls="rt-body">
          {shown ? "Replier" : reference ? headline(reference) : "Voir"}
        </button>
      </div>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {shown && data && (
        <div id="rt-body" className="rt-body">
          {data.notes.map((n) => <p key={n} className="notice"><Icon name="alert" /><span>{n}</span></p>)}
          {career ? (
            <p className="rt-career">
              Génération {career.birth_date.slice(0, 4)} : âge légal <b>{ageText(career.legal_age_years, career.legal_age_months)}</b>, <b>{career.quarters_required} trimestres</b> pour le taux plein.
              Bank te compte <b>{plural(career.quarters_acquired, "trimestre validé", "trimestres validés")}</b> au {shortDate(career.as_of)} {career.as_of.slice(0, 4)}
              {career.gross_annual_cents > 0 ? <> et un brut de <b>{money(career.gross_annual_cents, { whole: true })} par an</b> ({career.salary_source}).</> : <>, sans salaire reconnu.</>}
              {" "}
              <button type="button" className="btn link" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>{data.profile.quarters_acquired !== undefined ? "Modifier mon relevé" : "Saisir mon relevé de carrière"}</button>
            </p>
          ) : (
            <button type="button" className="btn sm" onClick={() => setEditing(true)} aria-expanded={editing}>Renseigner ma date de naissance et mon relevé</button>
          )}
          {editing && <CareerForm data={data} onDone={() => { setEditing(false); view.reload(); }} />}

          {career && departures.length > 0 && (
            <>
              <ul className="rt-departures" aria-label="Départs possibles">
                {departures.map(([key, d]) => {
                  const left = (d.total_low_cents / maxHigh) * 100;
                  const width = Math.max(1.5, ((d.total_high_cents - d.total_low_cents) / maxHigh) * 100);
                  const isCurrent = current === d;
                  return (
                    <li key={key} className={`rt-dep${isCurrent ? " is-current" : ""}`} data-status={d.status} data-key={key}>
                      <button type="button" className="rt-dep-row" aria-pressed={isCurrent} onClick={() => setSelected(key)}>
                        <span className="rt-when">
                          <span className="rt-title">{TITLES[key]}</span>
                          <b className="rt-age">{ageText(d.age_years, d.age_months)}</b>
                          <span className="faint">{monthYear(d.at)}</span>
                        </span>
                        <span className="rt-range" aria-hidden="true">
                          <i style={{ left: `${left}%`, width: `${width}%` }} />
                        </span>
                        <span className="rt-amount">
                          <b>{rangeText(d.total_low_cents, d.total_high_cents)}</b>
                          <span className="rt-status">{statusText(d)}</span>
                        </span>
                      </button>
                      {isCurrent && (
                        <div className="rt-detail">
                          <dl className="rt-stats">
                            <div><dt>Pension de base</dt><dd>{rangeText(d.base_monthly_low_cents, d.base_monthly_high_cents)}</dd></div>
                            <div><dt>Complémentaire</dt><dd>{rangeText(d.complementary_monthly_low_cents, d.complementary_monthly_high_cents)}<small className="faint"> · {Number(d.points).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} points</small></dd></div>
                            <div><dt>Taux</dt><dd>{Number(d.rate_percent).toLocaleString("fr-FR", { maximumFractionDigits: 3 })} %</dd></div>
                            <div><dt>Trimestres</dt><dd>{d.quarters_total} / {d.quarters_required}</dd></div>
                            {d.replacement_percent !== null && <div><dt>De ton net actuel</dt><dd>≈ {d.replacement_percent} %</dd></div>}
                          </dl>
                          {d.notes.map((n) => <p key={n} className="faint rt-note">{n}</p>)}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>

              <div className="rt-slider">
                <label htmlFor="rt-age">Et si je partais à <output htmlFor="rt-age">{age ?? career.legal_age_years} ans</output> ?</label>
                <input
                  id="rt-age" type="range" min={MIN_AGE} max={MAX_AGE} step={1}
                  value={age ?? career.legal_age_years}
                  onChange={(e) => setAge(Number(e.target.value))}
                  aria-valuetext={`${age ?? career.legal_age_years} ans`}
                />
                <div className="rt-slider-ends faint" aria-hidden="true"><span>{MIN_AGE} ans</span><span>{MAX_AGE} ans</span></div>
              </div>

              {data.per && (
                <div className="rt-per">
                  <h3>Un versement PER cette année</h3>
                  <div className="rt-per-row">
                    <Field label="Versement">
                      <EuroInput value={per ?? data.per.payment_cents} onChange={(c) => setPer(c)} aria-label="Versement PER" />
                    </Field>
                    <p className="rt-per-lead">
                      Déductible : <b>{money(data.per.deductible_cents, { whole: true })}</b> (plafond {money(data.per.ceiling_cents, { whole: true })}).
                      À {data.per.marginal_rate_percent} % d'impôt, l'État en reprend <b>{money(data.per.tax_saved_cents, { whole: true })}</b> : l'effort réel est de <b>{money(data.per.effort_cents, { whole: true })}</b>.
                    </p>
                  </div>
                  <p className="faint">Tranche : {data.per.tax_detail || data.per.tax_source}.</p>
                  {data.per.notes.map((n) => <p key={n} className="faint rt-note">{n}</p>)}
                </div>
              )}
            </>
          )}

          <details className="rt-sources">
            <summary>Règles appliquées (vérifiées le {shortDate(data.rules_checked)} {data.rules_checked.slice(0, 4)}){data.rules_recheck ? " — à revérifier" : ""}</summary>
            <ul>
              {Object.entries(data.rules).map(([k, r]) => (
                <li key={k}>{r.title}{r.url && <> — <a href={r.url} target="_blank" rel="noreferrer noopener">source <Icon name="external" /></a></>}</li>
              ))}
            </ul>
          </details>
          <p className="faint">{data.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

function CareerForm({ data, onDone }: { data: RetirementData; onDone: () => void }) {
  const p = data.profile;
  const c = data.career;
  const [quarters, setQuarters] = useState<number>(p.quarters_acquired ?? c?.quarters_acquired ?? 0);
  const [asOf, setAsOf] = useState<string>(p.as_of ?? "");
  const [best, setBest] = useState<number | null>(p.best_years_average_cents ?? c?.best_years_average_cents ?? null);
  const [points, setPoints] = useState<string>(p.agirc_arrco_points ?? c?.agirc_arrco_points ?? "");
  const [start, setStart] = useState<string>(p.career_start_year ? String(p.career_start_year) : "");
  const [working, setWorking] = useState<boolean>(p.working ?? c?.working ?? true);
  const [birth, setBirth] = useState<string>(p.birth_date ?? "");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const needsBirth = !c;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (needsBirth && !birth) return;
    void run(async () => {
      await api("/api/retirement/profile", {
        method: "PUT",
        body: {
          quarters_acquired: quarters, as_of: asOf || null, best_years_average_cents: best,
          agirc_arrco_points: points.trim() || null, career_start_year: start ? Number(start) : null,
          working, birth_date: birth || null,
        },
      });
      toast("Relevé enregistré : estimation à jour.");
      onDone();
    });
  };
  const forget = () =>
    void run(async () => {
      await api("/api/retirement/profile", { method: "DELETE" });
      toast("Relevé oublié : Bank estime à nouveau.");
      onDone();
    });
  return (
    <form className="form-grid rt-form" onSubmit={submit} aria-label="Mon relevé de carrière">
      <p className="muted rt-form-lead">
        Les chiffres sont sur ton relevé de carrière (<a href="https://www.info-retraite.fr/" target="_blank" rel="noreferrer noopener">info-retraite.fr</a>, rubrique « Ma carrière »). Ils restent chez toi, chiffrés.
      </p>
      {needsBirth && <Field label="Date de naissance"><input type="date" required value={birth} onChange={(e) => setBirth(e.target.value)} /></Field>}
      <Field label="Trimestres validés"><input type="number" min={0} max={200} required value={quarters} onChange={(e) => setQuarters(Number(e.target.value))} /></Field>
      <Field label="À la date du" hint="la date du relevé ; vide : aujourd'hui"><input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field>
      <Field label="Salaire annuel moyen" hint="les 25 meilleures années, si tu le connais ; sinon Bank estime"><EuroInput value={best} onChange={setBest} aria-label="Salaire annuel moyen" /></Field>
      <Field label="Points Agirc-Arrco" hint="déjà acquis, d'après le relevé"><input inputMode="decimal" value={points} maxLength={20} placeholder="1 250,5" onChange={(e) => setPoints(e.target.value)} /></Field>
      <Field label="Année de début de carrière"><input type="number" min={1950} max={2100} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
      <label className="check"><input type="checkbox" checked={working} onChange={(e) => setWorking(e.target.checked)} /> Je continue de cotiser jusqu'au départ (4 trimestres par an)</label>
      {error && <ErrorNote message={error} />}
      <div className="rt-form-actions">
        <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer</button>
        {p.quarters_acquired !== undefined && <button type="button" className="btn quiet sm" disabled={busy} onClick={forget}>Oublier mon relevé</button>}
      </div>
    </form>
  );
}
