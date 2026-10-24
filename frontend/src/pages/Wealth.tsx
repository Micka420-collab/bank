// Patrimoine : la valeur nette et son évolution, la répartition, le matelas de sécurité, les livrets, les objectifs,
// la projection « et si j'épargnais » et le plan d'investissement. Les chiffres d'abord, les formulaires repliés.

import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type HistoryRange, type ProjectionYear, type WealthResponse } from "../api";
import { Donut, type Slice } from "../components/charts/Donut";
import { TrendChart } from "../components/charts/TrendChart";
import { CountUp } from "../components/CountUp";
import { Icon } from "../components/Icon";
import { InvestPlan } from "../components/InvestPlan";
import { Loans } from "../components/Loans";
import { Retirement } from "../components/Retirement";
import { SectionNav } from "../components/SectionNav";
import { Segmented } from "../components/Segmented";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { amountOnly, money, percent, plural, shortDate } from "../format";
import { Stat } from "./Today";

const RANGES: { value: HistoryRange; label: string }[] = [
  { value: "7d", label: "7 j" },
  { value: "30d", label: "30 j" },
  { value: "3m", label: "3 mois" },
  { value: "1y", label: "1 an" },
  { value: "all", label: "Tout" },
];
const RANGE_DAYS: Record<HistoryRange, number> = { "7d": 7, "30d": 30, "3m": 92, "1y": 365, all: Infinity };

/** Les derniers jours d'une série quotidienne, selon la période choisie. */
export function sliceRange<T>(series: T[], range: HistoryRange): T[] {
  const n = RANGE_DAYS[range];
  return Number.isFinite(n) ? series.slice(-n) : series;
}

const BUCKETS: Record<string, { label: string; color: string }> = {
  liquidites: { label: "Comptes courants", color: "var(--ink-3)" },
  epargne_disponible: { label: "Épargne disponible", color: "var(--accent)" },
  epargne_long_terme: { label: "Épargne long terme", color: "var(--positive)" },
  autres_actifs: { label: "Autres actifs", color: "var(--warning)" },
};

export function bucketSlices(byBucket: Record<string, number>): Slice[] {
  const positive = Object.entries(byBucket).filter(([k, v]) => k !== "dettes" && v > 0);
  const total = positive.reduce((a, [, v]) => a + v, 0) || 1;
  return positive
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ slug: k, name: BUCKETS[k]?.label ?? k, color: BUCKETS[k]?.color ?? "var(--ink-3)", cents: v, share: v / total }));
}

const WEALTH_SECTIONS = [
  { id: "nw", label: "Valeur nette" },
  { id: "reg-title", label: "Livrets" },
  { id: "goals-title", label: "Objectifs" },
  { id: "loans-title", label: "Crédits" },
  { id: "rt-title", label: "Retraite" },
  { id: "proj-title", label: "Simulation" },
  { id: "plan-title", label: "Où placer" },
];

export function Wealth({ retraite = false }: { retraite?: boolean }) {
  const w = useApi<WealthResponse>("/api/wealth");
  const [range, setRange] = useState<HistoryRange>("3m");
  if (w.error) return <ErrorNote message={w.error} onRetry={w.reload} />;
  if (!w.data) return <Loading />;
  const { net_worth: nw, emergency_fund: ef } = w.data;
  const slices = bucketSlices(nw.by_bucket);
  const history = w.data.history;
  const shown = sliceRange(history, range);
  const first = history[0];
  const monthAgo = history.length > 30 ? history[history.length - 31] : undefined;
  const deltaMonth = monthAgo ? nw.net_cents - monthAgo.net_cents : undefined;
  const savings = (nw.by_bucket.epargne_disponible ?? 0) + (nw.by_bucket.epargne_long_terme ?? 0);
  const yearlyInterest = w.data.regulated.reduce((a, r) => a + r.yearly_interest_cents, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Patrimoine</h1>
          <p className="muted">Ce que tu possèdes, moins ce que tu dois — et comment le faire grandir.</p>
        </div>
      </div>

      <SectionNav items={WEALTH_SECTIONS} />

      <section className="card card-hero reveal" style={{ ["--i" as string]: 0 }} aria-labelledby="nw">
        <p className="today-lead" id="nw">Valeur nette</p>
        <p className={`today-figure${nw.net_cents < 0 ? " is-zero" : ""}`}>
          <CountUp value={Math.round(nw.net_cents / 100)} format={(n) => amountOnly(n * 100)} /> €
        </p>
        <p className="muted">
          {money(nw.assets_cents, { whole: true })} d'actifs{nw.liabilities_cents > 0 ? `, ${money(nw.liabilities_cents, { whole: true })} de dettes` : ", aucune dette"}.
          {history.length > 1 && first && ` Suivi depuis le ${shortDate(first.day)}.`}
        </p>
        {history.length > 1 ? (
          <div style={{ marginTop: "1rem" }}>
            <div className="card-head" style={{ marginBottom: "0.5rem" }}>
              <span className="faint">Évolution</span>
              <Segmented value={range} options={RANGES} onChange={setRange} label="Période" />
            </div>
            <TrendChart points={shown.map((p) => ({ date: p.day, value: p.net_cents }))} label="Valeur nette" baseline="auto" />
          </div>
        ) : (
          <p className="faint" style={{ marginTop: "1rem" }}>La courbe se dessine à partir de tes opérations : importe un relevé ou connecte ta banque.</p>
        )}
      </section>

      <dl className="stats reveal" style={{ ["--i" as string]: 1 }}>
        <Stat label="Il y a 30 jours" value={monthAgo ? money(monthAgo.net_cents, { whole: true }) : "—"} delta={deltaMonth} deltaLabel="depuis" />
        <Stat label="Épargne totale" value={money(savings, { whole: true })} />
        <Stat label="Matelas de sécurité" value={`${ef.months_covered.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} mois`} />
        <Stat label="Intérêts des livrets / an" value={money(yearlyInterest, { whole: true })} />
      </dl>

      <div className="split reveal" style={{ marginTop: "1rem", ["--i" as string]: 2 }}>
        <section className="card" aria-labelledby="split-title">
          <div className="card-head"><h2 id="split-title">Répartition</h2></div>
          {slices.length === 0 ? (
            <p className="muted">Ajoute tes comptes d'épargne dans Réglages pour voir la répartition.</p>
          ) : (
            <Donut slices={slices} total={nw.assets_cents} label="Répartition des actifs" centerLabel="actifs" />
          )}
        </section>

        <section className="card" aria-labelledby="ef-title">
          <div className="card-head"><h2 id="ef-title">Matelas de sécurité</h2><span className={`tag ${ef.level === "insuffisant" ? "warn" : "ok"}`}>{ef.level}</span></div>
          <p>
            Ton épargne disponible couvre <b>{plural(ef.months_covered, "mois", "mois")}</b> de dépenses essentielles
            ({money(ef.monthly_essentials_cents, { whole: true })} par mois en moyenne).
          </p>
          <div className="meter" style={{ marginTop: "1rem", height: "0.9rem" }} role="meter" aria-valuemin={0}
               aria-valuemax={ef.reference_max_months * 1.5} aria-valuenow={ef.months_covered} aria-label="Mois couverts">
            <i style={{ width: `${Math.min(100, (ef.months_covered / (ef.reference_max_months * 1.5)) * 100)}%`,
                        background: ef.level === "insuffisant" ? "var(--warning)" : "var(--positive)" }} />
            <em style={{ left: `${(ef.reference_min_months / (ef.reference_max_months * 1.5)) * 100}%` }} />
            <em style={{ left: `${(ef.reference_max_months / (ef.reference_max_months * 1.5)) * 100}%` }} />
          </div>
          <p className="faint" style={{ marginTop: "0.5rem" }}>
            Repère couramment cité : {ef.reference_min_months} à {ef.reference_max_months} mois. Les traits marquent ces deux bornes.
          </p>
        </section>
      </div>

      <section className="card reveal" style={{ marginTop: "1rem", ["--i" as string]: 3 }} aria-labelledby="reg-title">
        <div className="card-head">
          <h2 id="reg-title">Livrets réglementés</h2>
          {w.data.rates_as_of && <span className="faint">Taux au {shortDate(w.data.rates_as_of)} ({w.data.rates_source})</span>}
        </div>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Tableau, défile de côté"><table className="plain">
          <thead>
            <tr><th>Livret</th><th className="num">Solde</th><th className="num">Place restante</th><th className="num">Intérêts / an</th></tr>
          </thead>
          <tbody>
            {w.data.regulated.map((r) => (
              <tr key={r.kind}>
                <td>{r.name} <span className="faint">{percent(Number(r.rate_percent), 2)}</span></td>
                <td className="num">{money(r.balance_cents, { whole: true })}</td>
                <td className="num">{r.at_ceiling ? <span className="tag">plafond atteint</span> : money(r.room_cents, { whole: true })}</td>
                <td className="num">{money(r.yearly_interest_cents, { whole: true })}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </section>

      <Goals goals={w.data.goals} accounts={w.data.accounts} onChange={w.reload} />
      <Loans onChange={w.reload} />
      <Retirement open={retraite} />
      <Projection initial={Math.max(0, savings)} />
      <InvestPlan />
      <p className="faint section">{w.data.disclaimer}</p>
    </>
  );
}

function Goals({ goals, accounts, onChange }: { goals: WealthResponse["goals"]; accounts: WealthResponse["accounts"]; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [monthly, setMonthly] = useState<number | null>(null);
  const [date, setDate] = useState("");
  const [accountId, setAccountId] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/goals", {
        method: "POST",
        body: {
          name, target_cents: target, monthly_contribution_cents: monthly ?? 0,
          target_date: date || null, account_id: accountId ? Number(accountId) : null,
        },
      });
      setName(""); setTarget(null); setMonthly(null); setDate("");
      setOpen(false);
      toast("Objectif ajouté.");
      onChange();
    });
  };

  const remove = (id: number) =>
    void run(async () => {
      await api(`/api/goals/${id}`, { method: "DELETE" });
      onChange();
    });

  return (
    <section className="card section reveal" style={{ ["--i" as string]: 4 }} aria-labelledby="goals-title">
      <div className="card-head">
        <h2 id="goals-title">Objectifs</h2>
        <button type="button" className="btn quiet sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <Icon name={open ? "close" : "plus"} /> {open ? "Fermer" : "Nouvel objectif"}
        </button>
      </div>
      {goals.length === 0 && (
        <p className="muted" style={{ marginBottom: open ? "0.75rem" : 0 }}>
          Un objectif chiffré rend l'épargne concrète : vacances, apport, matelas. Bank suit le compte que tu lui indiques et te dit quand tu y seras.
        </p>
      )}
      <div className="envelopes">
        {goals.map((g) => (
          <div key={g.id}>
            <div className="envelope-head">
              <span><b>{g.name}</b> <span className="muted">{money(g.current_cents, { whole: true })} sur {money(g.target_cents, { whole: true })}</span></span>
              <button className="btn link" onClick={() => remove(g.id)}>Supprimer</button>
            </div>
            <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={g.progress} aria-label={g.name}>
              <i style={{ width: `${g.progress * 100}%` }} />
            </div>
            <p className="faint" style={{ marginTop: "0.3rem" }}>
              {g.months_to_goal === 0 ? "Atteint."
                : g.months_to_goal !== null ? `Atteint dans ${plural(g.months_to_goal, "mois", "mois")} au rythme de ${money(g.monthly_contribution_cents, { whole: true })} par mois.`
                : "Ajoute un versement mensuel pour estimer l'échéance."}
              {g.monthly_needed_cents !== null && g.target_date && ` Pour le ${shortDate(g.target_date)} : ${money(g.monthly_needed_cents, { whole: true })} par mois.`}
            </p>
          </div>
        ))}
      </div>
      {open && (
        <form className="panel card-quiet" style={{ marginTop: goals.length ? "1.25rem" : 0 }} onSubmit={submit} aria-label="Nouvel objectif">
          <div className="form-grid">
            <Field label="Objectif"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Matelas de sécurité" /></Field>
            <Field label="Montant visé"><EuroInput required value={target} onChange={setTarget} /></Field>
            <Field label="Versement mensuel"><EuroInput value={monthly} onChange={setMonthly} /></Field>
            <Field label="Échéance (facultatif)"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
            <Field label="Compte suivi">
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">Aucun</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
          </div>
          {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
          <div className="actions"><button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || !target}>Ajouter l'objectif</button></div>
        </form>
      )}
    </section>
  );
}

function Projection({ initial }: { initial: number }) {
  const [start, setStart] = useState<number | null>(initial);
  const [monthly, setMonthly] = useState<number | null>(20000);
  const [rate, setRate] = useState("3");
  const [years, setYears] = useState(10);
  const [rows, setRows] = useState<ProjectionYear[] | null>(null);
  const [, error, run] = useAsyncAction();

  useEffect(() => {
    const t = window.setTimeout(() => {
      const r = Number(rate.replace(",", "."));
      if (!Number.isFinite(r)) return;
      void run(async () => {
        setRows(await api<ProjectionYear[]>("/api/wealth/projection", {
          method: "POST",
          body: { initial_cents: start ?? 0, monthly_cents: monthly ?? 0, rate_percent: r, years, inflation_percent: 2 },
        }));
      });
    }, 250);
    return () => window.clearTimeout(t);
  }, [start, monthly, rate, years, run]);

  const last = rows?.[rows.length - 1];
  const W = 720;
  const H = 200;
  const base = start ?? 0;
  const series = rows ? [{ year: 0, nominal_cents: base, real_cents: base, contributed_cents: base, interest_cents: 0 }, ...rows] : [];
  const max = Math.max(1, ...series.map((r) => r.nominal_cents));
  const px = (i: number) => 12 + (i / Math.max(1, series.length - 1)) * (W - 24);
  const py = (v: number) => H - 28 - (v / max) * (H - 44);
  const path = (key: "nominal_cents" | "real_cents" | "contributed_cents") =>
    series.map((r, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(r[key]).toFixed(1)}`).join("");

  return (
    <section className="card section reveal" style={{ ["--i" as string]: 5 }} aria-labelledby="proj-title">
      <div className="card-head"><h2 id="proj-title">Et si j'épargnais…</h2><span className="faint">Simulation, inflation 2 %</span></div>
      <div className="form-grid">
        <Field label="Point de départ"><EuroInput value={start} onChange={setStart} /></Field>
        <Field label="Chaque mois"><EuroInput value={monthly} onChange={setMonthly} /></Field>
        <Field label="Rendement annuel (%)"><input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} /></Field>
        <Field label={`Durée : ${years} ans`}>
          <input type="range" min={1} max={40} value={years} onChange={(e) => setYears(Number(e.target.value))} />
        </Field>
      </div>
      {error && <p className="error">{error}</p>}
      {last && (
        <>
          <p style={{ marginTop: "1.25rem", fontSize: "var(--t-lg)" }}>
            Dans {years} ans : <b>{money(last.nominal_cents, { whole: true })}</b>, dont{" "}
            {money(last.interest_cents, { whole: true })} d'intérêts. En euros d'aujourd'hui :{" "}
            {money(last.real_cents, { whole: true })}.
          </p>
          <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ marginTop: "1rem" }} role="img"
               aria-label={`Projection de l'épargne sur ${years} ans : ${money(last.nominal_cents, { whole: true })} dont ${money(last.contributed_cents, { whole: true })} versés`}>
            <line x1="12" x2={W - 12} y1={py(0)} y2={py(0)} className="grid-line" />
            <path d={path("contributed_cents")} className="contrib" />
            <path d={path("real_cents")} className="past" />
            <path d={path("nominal_cents")} className="future" />
            <text x="12" y={H - 8}>aujourd'hui</text>
            <text x={W - 12} y={H - 8} textAnchor="end">dans {years} ans</text>
          </svg>
          <div className="legend">
            <span className="l-future">montant futur</span>
            <span>en euros d'aujourd'hui</span>
            <span className="l-contrib">sommes versées</span>
          </div>
        </>
      )}
    </section>
  );
}
