// Tableau de bord : le chiffre du jour, ce que Bank te demande, la tendance, le mois, où part l'argent, les
// dernières opérations. L'essentiel d'abord, le détail en dessous.

import { useState } from "react";
import { api, useApi, type AidsResponse, type BudgetResponse, type Category, type History, type HistoryRange, type MonthForecast } from "../api";
import { Bars } from "../components/charts/Bars";
import { Donut } from "../components/charts/Donut";
import { TrendChart } from "../components/charts/TrendChart";
import { Copilot } from "../components/Copilot";
import { CountUp } from "../components/CountUp";
import { Decide } from "../components/Decide";
import { Icon } from "../components/Icon";
import { Initiatives } from "../components/Initiatives";
import { MonthChart } from "../components/MonthChart";
import { Segmented } from "../components/Segmented";
import { Amount, ErrorNote, Loading, useToast } from "../components/ui";
import { Welcome } from "../components/Welcome";
import { amountOnly, dayNumber, longDate, merchantCase, money, monthLabel, plural, shortDate } from "../format";

const RANGES: { value: HistoryRange; label: string }[] = [
  { value: "7d", label: "7 j" },
  { value: "30d", label: "30 j" },
  { value: "3m", label: "3 mois" },
  { value: "1y", label: "1 an" },
  { value: "all", label: "Tout" },
];

function verdict(f: MonthForecast): string {
  if (f.status === "risk" && f.overdraft_date) {
    const payday = f.upcoming.find((e) => e.kind === "income" && e.date > (f.overdraft_date ?? ""));
    return (
      `Découvert prévu le ${shortDate(f.overdraft_date)} (point bas ${money(f.lowest_balance_cents, { whole: true })})` +
      (payday ? `, avant ${payday.label} du ${shortDate(payday.date)}` : "")
    );
  }
  if (f.status === "tight") return `Ça se resserre : point bas à ${money(f.lowest_balance_cents, { whole: true })} le ${shortDate(f.lowest_balance_date)}`;
  return `Mois sans découvert · point bas ${money(f.lowest_balance_cents, { whole: true })} le ${shortDate(f.lowest_balance_date)}`;
}

export function Today() {
  const budget = useApi<BudgetResponse>("/api/budget");
  const aids = useApi<AidsResponse>("/api/aids");
  const cats = useApi<Category[]>("/api/categories");
  const agent = useApi<{ pending_proposals: number }>("/api/agents/summary");
  const [range, setRange] = useState<HistoryRange>("30d");
  const history = useApi<History>(`/api/history?range=${range}`);
  const toast = useToast();
  const catName = (slug: string) => cats.data?.find((c) => c.slug === slug)?.name ?? slug;

  if (budget.error) return <ErrorNote message={budget.error} onRetry={budget.reload} />;
  if (!budget.data) return <Loading />;
  const { forecast: f, suggested_budgets: suggested, settings } = budget.data;
  const isEmpty = f.current_balance_cents === 0 && f.history.every((p) => p.balance_cents === 0) && f.upcoming.length === 0;
  if (isEmpty) return <Welcome onStarted={budget.reload} />;

  const applySuggestion = async (slug: string, cents: number) => {
    await api(`/api/categories/${slug}`, { method: "PATCH", body: { monthly_budget_cents: cents } });
    toast(`Enveloppe fixée à ${money(cents, { whole: true })}.`);
    budget.reload();
  };
  const perDay = f.safe_to_spend_per_day_cents;
  const unbudgeted = Object.entries(suggested).filter(([slug]) => !f.budgets.some((b) => b.slug === slug));
  const h = history.data;
  const reload = () => {
    budget.reload();
    history.reload();
  };

  return (
    <>
      <section className="card card-hero today reveal" style={{ ["--i" as string]: 0 }} aria-labelledby="today-title">
        <p className="today-lead" id="today-title">{perDay > 0 ? "Tu peux dépenser" : "Plus de marge ce mois-ci"}</p>
        <p className={`today-figure${perDay > 0 ? "" : " is-zero"}`}>
          <CountUp value={Math.round(perDay / 100)} format={(n) => amountOnly(n * 100)} /> €<small>par jour</small>
        </p>
        <p className="muted">{allowanceSentence(f, settings.buffer_cents, settings.savings_goal_cents)}</p>
        <p className="verdict" data-status={f.status}>{verdict(f)}</p>
        <div className="actions quick-actions">
          <a className="btn quiet sm" href="#/connecteurs"><Icon name="bank" /> Connecter une banque</a>
          <a className="btn quiet sm" href="#/operations?import=1"><Icon name="upload" /> Importer un relevé</a>
          <a className="btn quiet sm" href="#/situation"><Icon name="situation" /> Ma situation</a>
          {agent.data && agent.data.pending_proposals > 0 && (
            <a className="btn sm" href="#/situation"><Icon name="robot" /> {plural(agent.data.pending_proposals, "proposition à valider", "propositions à valider")}</a>
          )}
        </div>
      </section>

      <Initiatives onChanged={reload} />

      <Copilot />

      {h && (
        <dl className="stats reveal" style={{ ["--i" as string]: 1 }}>
          <Stat label="Solde des comptes courants" value={money(h.current_balance_cents, { whole: true })} delta={h.balances.length > 1 ? h.current_balance_cents - h.balances[0]!.balance_cents : undefined} deltaLabel={`sur ${rangeLabel(range)}`} />
          <Stat label={`Reçu (${rangeLabel(range)})`} value={money(h.now.income_cents, { whole: true })} delta={h.now.income_cents - h.previous.income_cents} deltaLabel="par rapport à la période précédente" />
          <Stat label={`Dépensé (${rangeLabel(range)})`} value={money(h.now.spent_cents, { whole: true })} delta={h.now.spent_cents - h.previous.spent_cents} goodWhen="down" deltaLabel="par rapport à la période précédente" />
          <Stat label="Fin de mois estimée" value={money(f.projected_end_balance_cents, { whole: true })} delta={f.projected_end_balance_cents - f.current_balance_cents} deltaLabel="d'ici la fin du mois" />
        </dl>
      )}

      <section className="card reveal" style={{ marginTop: "1rem", ["--i" as string]: 2 }} aria-labelledby="trend-title">
        <div className="card-head">
          <h2 id="trend-title">Évolution du solde</h2>
          <Segmented value={range} options={RANGES} onChange={setRange} label="Période" />
        </div>
        {history.error && <ErrorNote message={history.error} onRetry={history.reload} />}
        {!h && !history.error && <Loading what="Historique" />}
        {h && (
          <>
            <TrendChart points={h.balances.map((b) => ({ date: b.date, value: b.balance_cents }))} label="Solde" />
            <p className="faint" style={{ marginTop: "0.5rem" }}>
              Du {shortDate(h.start)} au {shortDate(h.end)} : reçu {money(h.now.income_cents, { whole: true })}, dépensé {money(h.now.spent_cents, { whole: true })}
              {h.previous.income_cents + h.previous.spent_cents > 0 && ` · période précédente : ${money(h.previous.income_cents, { whole: true })} reçus, ${money(h.previous.spent_cents, { whole: true })} dépensés`}.
            </p>
          </>
        )}
      </section>

      <section className="card reveal" style={{ marginTop: "1rem", ["--i" as string]: 3 }} aria-labelledby="month-title">
        <div className="card-head">
          <h2 id="month-title">Ce mois-ci, jour par jour</h2>
          <span className="faint">Projection jusqu'à la fin du mois</span>
        </div>
        <MonthChart history={f.history} projection={f.projection} events={f.upcoming} lowestCents={f.lowest_balance_cents}
                    lowestDate={f.lowest_balance_date} daysInMonth={f.days_in_month} bare />
      </section>

      {h && (
        <div className="split reveal" style={{ marginTop: "1rem", ["--i" as string]: 4 }}>
          <section className="card" aria-labelledby="donut-title">
            <div className="card-head"><h2 id="donut-title">Où part l'argent</h2><span className="faint">{rangeLabel(range)}</span></div>
            <Donut slices={h.categories} total={h.now.spent_cents} label="Dépenses par catégorie" />
            {h.anomalies.length > 0 && (
              <ul className="stack" style={{ marginTop: "1rem", listStyle: "none", padding: 0 }}>
                {h.anomalies.map((a) => (
                  <li key={a.slug} className="notice warn">
                    <Icon name="trendUp" />
                    <span><b>{a.name}</b> : {money(a.cents, { whole: true })}, soit ×{a.ratio.toLocaleString("fr-FR")} par rapport à la période précédente ({money(a.previous_cents, { whole: true })}).</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card" aria-labelledby="bars-title">
            <div className="card-head"><h2 id="bars-title">Reçu et dépensé</h2><span className="faint">{h.range === "7d" ? "par jour" : h.range === "30d" || h.range === "3m" ? "par semaine" : "par mois"}</span></div>
            <Bars periods={h.periods} />
          </section>
        </div>
      )}

      <div className="split reveal" style={{ marginTop: "1rem", ["--i" as string]: 5 }}>
        <section className="card" aria-labelledby="upcoming-title">
          <div className="card-head"><h2 id="upcoming-title">D'ici la fin du mois</h2></div>
          {f.upcoming.length === 0 ? (
            <p className="muted">Aucune échéance récurrente détectée d'ici là.</p>
          ) : (
            <ul className="ledger">
              {groupByDate(f.upcoming).map(([date, events]) => (
                <li className="ledger-day" key={date}>
                  <div className="ledger-date" aria-hidden="true">
                    <strong>{dayNumber(date)}</strong>
                    <span>{monthLabel(date.slice(0, 7))}</span>
                  </div>
                  <div className="ledger-rows">
                    {events.map((e, i) => (
                      <div className="ledger-row" key={i}>
                        <div className="ledger-label">
                          <span className="sr-only">{shortDate(date)} : </span>
                          <b>{e.label}</b> {e.late && <span className="tag late">en retard</span>}
                        </div>
                        <Amount cents={e.amount_cents} />
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="faint" style={{ marginTop: "0.75rem" }}>
            Dépense courante habituelle : {money(f.daily_variable_rate_cents, { whole: true })} par jour, déjà comptée.
          </p>
        </section>

        <section className="card" aria-labelledby="env-title">
          <div className="card-head"><h2 id="env-title">Enveloppes</h2><a className="faint" href="#/reglages">Régler</a></div>
          {f.budgets.length === 0 && unbudgeted.length === 0 && (
            <p className="muted">Pas encore assez d'historique pour proposer des enveloppes.</p>
          )}
          <div className="envelopes">
            {f.budgets.map((b) => {
              const elapsed = Number(dayNumber(f.today)) / f.days_in_month;
              return (
                <div key={b.slug}>
                  <div className="envelope-head">
                    <span>{b.name}</span>
                    <span className={b.status === "over" ? "error" : "muted"}>
                      {money(b.spent_cents, { whole: true })} sur {money(b.budget_cents, { whole: true })}
                    </span>
                  </div>
                  <div className="meter" data-status={b.status} role="meter" aria-valuemin={0}
                       aria-valuemax={b.budget_cents} aria-valuenow={b.spent_cents} aria-label={b.name}>
                    <i style={{ width: `${Math.min(100, (b.spent_cents / b.budget_cents) * 100)}%` }} />
                    <em style={{ left: `${elapsed * 100}%` }} title="Rythme attendu à date" />
                  </div>
                </div>
              );
            })}
          </div>
          {unbudgeted.length > 0 && (
            <div className="stack" style={{ marginTop: "1.25rem" }}>
              <p className="faint">Suggestions d'après tes trois derniers mois :</p>
              {unbudgeted.slice(0, 4).map(([slug, cents]) => (
                <div className="envelope-head" key={slug}>
                  <span>{catName(slug)}</span>
                  <button className="btn link" onClick={() => void applySuggestion(slug, cents)}>Fixer à {money(cents, { whole: true })}</button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {h && h.latest.length > 0 && (
        <section className="card reveal" style={{ marginTop: "1rem", ["--i" as string]: 6 }} aria-labelledby="latest-title">
          <div className="card-head"><h2 id="latest-title">Dernières opérations</h2><a href="#/operations">Tout voir</a></div>
          <ul className="ledger">
            {groupByDate(h.latest).map(([date, items]) => (
              <li className="ledger-day" key={date}>
                <div className="ledger-date" aria-hidden="true"><strong>{dayNumber(date)}</strong><span>{monthLabel(date.slice(0, 7))}</span></div>
                <div className="ledger-rows">
                  {items.map((t) => (
                    <div className="ledger-row" key={t.id}>
                      <div className="ledger-label">
                        <b>{merchantCase(t.merchant)}</b>{" "}
                        {t.is_transfer ? <span className="tag">virement interne</span> : t.category_name ? <span className="tag">{t.category_name}</span> : <span className="tag warn">à classer</span>}
                      </div>
                      <Amount cents={t.amount_cents} />
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card reveal" style={{ marginTop: "1rem", ["--i" as string]: 7 }}>
        <Decide />
      </section>

      {aids.data && aids.data.summary.alerts.length > 0 && (
        <section className="section" aria-labelledby="aid-alerts">
          <h2 id="aid-alerts">À surveiller</h2>
          <div className="stack">
            {aids.data.summary.alerts.map((a) => (
              <div className="notice warn" key={a}><Icon name="alert" /><span>{a} <a href="#/aides">Voir les aides</a></span></div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function rangeLabel(range: HistoryRange): string {
  return { "7d": "7 jours", "30d": "30 jours", "3m": "3 mois", "1y": "1 an", all: "tout l'historique" }[range];
}

/** Variation signée ; la couleur dit si c'est une bonne nouvelle (une dépense qui baisse est en vert). */
export function Stat({ label, value, delta, deltaLabel, goodWhen = "up" }: {
  label: string;
  value: string;
  delta?: number;
  deltaLabel?: string;
  goodWhen?: "up" | "down";
}) {
  const good = delta !== undefined && (goodWhen === "up" ? delta > 0 : delta < 0);
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {delta !== undefined && delta !== 0 && (
        <span className={`trend ${good ? "up" : "down"}`} title={deltaLabel} aria-label={`${delta > 0 ? "plus" : "moins"} ${money(Math.abs(delta), { whole: true })} ${deltaLabel ?? ""}`}>
          <Icon name={delta > 0 ? "trendUp" : "trendDown"} />
          {delta > 0 ? "+" : "−"}{money(Math.abs(delta), { whole: true })}
        </span>
      )}
    </div>
  );
}

function allowanceSentence(f: MonthForecast, buffer: number, goal: number): string {
  const [first, ...rest] = f.allowance;
  const keep = [
    "Échéances prévues déduites",
    buffer > 0 ? `${money(buffer, { whole: true })} gardés de côté` : null,
    goal > 0 ? "épargne du mois réservée" : null,
  ].filter(Boolean).join(", ");
  if (!first || rest.length === 0) {
    return `Jusqu'au ${longDate(`${f.month}-${String(f.days_in_month).padStart(2, "0")}`)} (${plural(f.days_left, "jour", "jours")}). ${keep}.`;
  }
  const next = rest[0]!;
  const until = first.end === first.start ? "aujourd'hui" : `jusqu'au ${shortDate(first.end)}`;
  const why = first.reason ? `l'arrivée de ${first.reason}` : `le ${shortDate(next.start)}`;
  return `${until[0]!.toUpperCase()}${until.slice(1)}, puis ${money(next.per_day_cents, { whole: true })} par jour après ${why}. ${keep}.`;
}

function groupByDate<T extends { date: string }>(items: T[]): [string, T[]][] {
  const map = new Map<string, T[]>();
  for (const it of items) map.set(it.date, [...(map.get(it.date) ?? []), it]);
  return [...map.entries()];
}
