import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type ProjectionYear, type WealthResponse } from "../api";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, percent, plural, shortDate } from "../format";

const BUCKETS: Record<string, { label: string; color: string }> = {
  liquidites: { label: "Comptes courants", color: "var(--ink-soft)" },
  epargne_disponible: { label: "Épargne disponible", color: "var(--pen)" },
  epargne_long_terme: { label: "Épargne long terme", color: "var(--gain)" },
  autres_actifs: { label: "Autres actifs", color: "var(--warn)" },
};

export function Wealth() {
  const w = useApi<WealthResponse>("/api/wealth");
  if (w.error) return <ErrorNote message={w.error} onRetry={w.reload} />;
  if (!w.data) return <Loading />;
  const { net_worth: nw, emergency_fund: ef } = w.data;
  const positive = Object.entries(nw.by_bucket).filter(([k, v]) => k !== "dettes" && v > 0);
  const totalAssets = Math.max(1, nw.assets_cents);

  return (
    <>
      <div className="page-head">
        <h1>Patrimoine</h1>
      </div>

      <section aria-labelledby="nw">
        <p className="today-lead" id="nw">Valeur nette</p>
        <p className="today-figure">{money(nw.net_cents, { whole: true })}</p>
        <p className="muted">
          {money(nw.assets_cents, { whole: true })} d'actifs, {money(nw.liabilities_cents, { whole: true })} de dettes.
        </p>
        {w.data.history.length > 1 && <NetWorthLine points={w.data.history} />}
        {positive.length > 0 && (
          <div style={{ marginTop: "1.5rem", maxWidth: "40rem" }}>
            <div className="stacked" role="img" aria-label="Répartition des actifs">
              {positive.map(([k, v]) => (
                <i key={k} style={{ width: `${(v / totalAssets) * 100}%`, background: BUCKETS[k]?.color }} />
              ))}
            </div>
            <div className="legend">
              {positive.map(([k, v]) => (
                <span key={k} className="swatch" style={{ ["--c" as string]: BUCKETS[k]?.color }}>
                  {BUCKETS[k]?.label ?? k} : {money(v, { whole: true })}
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      <div className="split section">
        <section aria-labelledby="ef-title">
          <h2 id="ef-title">Matelas de sécurité</h2>
          <p>
            Ton épargne disponible couvre <b>{plural(ef.months_covered, "mois", "mois")}</b> de dépenses essentielles
            ({money(ef.monthly_essentials_cents, { whole: true })} par mois en moyenne).
          </p>
          <div className="meter" style={{ marginTop: "1rem", height: "0.9rem" }} role="meter" aria-valuemin={0}
               aria-valuemax={ef.reference_max_months * 1.5} aria-valuenow={ef.months_covered} aria-label="Mois couverts">
            <i style={{ width: `${Math.min(100, (ef.months_covered / (ef.reference_max_months * 1.5)) * 100)}%`,
                        background: ef.level === "insuffisant" ? "var(--warn)" : "var(--gain)" }} />
            <em style={{ left: `${(ef.reference_min_months / (ef.reference_max_months * 1.5)) * 100}%` }} />
            <em style={{ left: `${(ef.reference_max_months / (ef.reference_max_months * 1.5)) * 100}%` }} />
          </div>
          <p className="faint" style={{ marginTop: "0.5rem" }}>
            Repère couramment cité : {ef.reference_min_months} à {ef.reference_max_months} mois.
          </p>
        </section>

        <section aria-labelledby="reg-title">
          <h2 id="reg-title">Livrets réglementés</h2>
          <table className="plain">
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
          </table>
          {w.data.rates_as_of && (
            <p className="faint" style={{ marginTop: "0.5rem" }}>
              Taux au {shortDate(w.data.rates_as_of)} ({w.data.rates_source}).
            </p>
          )}
        </section>
      </div>

      <Goals goals={w.data.goals} accounts={w.data.accounts} onChange={w.reload} />
      <Projection initial={Math.max(0, (nw.by_bucket.epargne_disponible ?? 0) + (nw.by_bucket.epargne_long_terme ?? 0))} />
      <p className="faint section">{w.data.disclaimer}</p>
    </>
  );
}

function NetWorthLine({ points }: { points: { day: string; net_cents: number }[] }) {
  const W = 600;
  const H = 80;
  const vals = points.map((p) => p.net_cents);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = Math.max(1, max - min);
  const d = points
    .map((p, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * W).toFixed(1)},${(H - 4 - ((p.net_cents - min) / span) * (H - 8)).toFixed(1)}`)
    .join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ maxWidth: "40rem", marginTop: "1rem" }} role="img"
         aria-label={`Évolution depuis le ${shortDate(points[0]!.day)}`}>
      <path d={d} className="past" />
    </svg>
  );
}

function Goals({ goals, accounts, onChange }: { goals: WealthResponse["goals"]; accounts: WealthResponse["accounts"]; onChange: () => void }) {
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
    <section className="section" aria-labelledby="goals-title">
      <h2 id="goals-title">Objectifs</h2>
      <div className="envelopes" style={{ maxWidth: "44rem" }}>
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
      <form className="panel" style={{ marginTop: "1.5rem" }} onSubmit={submit} aria-label="Nouvel objectif">
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
        <div className="actions"><button className="btn" disabled={busy || !target}>Ajouter l'objectif</button></div>
      </form>
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
  const W = 600;
  const H = 180;
  const base = start ?? 0;
  const series = rows ? [{ year: 0, nominal_cents: base, real_cents: base, contributed_cents: base, interest_cents: 0 }, ...rows] : [];
  const max = Math.max(1, ...series.map((r) => r.nominal_cents));
  const px = (i: number) => 4 + (i / Math.max(1, series.length - 1)) * (W - 8);
  const py = (v: number) => H - 18 - (v / max) * (H - 30);
  const path = (key: "nominal_cents" | "real_cents" | "contributed_cents") =>
    series.map((r, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(r[key]).toFixed(1)}`).join("");

  return (
    <section className="section" aria-labelledby="proj-title">
      <h2 id="proj-title">Et si j'épargnais…</h2>
      <div className="form-grid" style={{ maxWidth: "48rem" }}>
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
          <p style={{ marginTop: "1.25rem", fontSize: "var(--step-1)" }}>
            Dans {years} ans : <b>{money(last.nominal_cents, { whole: true })}</b>, dont{" "}
            {money(last.interest_cents, { whole: true })} d'intérêts. En euros d'aujourd'hui (inflation 2 %) :{" "}
            {money(last.real_cents, { whole: true })}.
          </p>
          <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ maxWidth: "48rem", marginTop: "1rem" }} role="img"
               aria-label="Projection de l'épargne">
            <line x1="0" x2={W} y1={py(0)} y2={py(0)} className="seyes-major" />
            <path d={path("contributed_cents")} className="contrib" />
            <path d={path("real_cents")} className="past" />
            <path d={path("nominal_cents")} className="future" />
            <text x="4" y={H - 2}>aujourd'hui</text>
            <text x={W - 4} y={H - 2} textAnchor="end">dans {years} ans</text>
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
