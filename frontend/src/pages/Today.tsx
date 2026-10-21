import { api, useApi, type AidsResponse, type BudgetResponse, type Category, type MonthForecast } from "../api";
import { Decide } from "../components/Decide";
import { Initiatives } from "../components/Initiatives";
import { MonthChart } from "../components/MonthChart";
import { Amount, ErrorNote, Loading, useToast } from "../components/ui";
import { Welcome } from "../components/Welcome";
import { amountOnly, dayNumber, longDate, money, monthLabel, plural, shortDate } from "../format";

function verdict(f: MonthForecast): string {
  if (f.status === "risk" && f.overdraft_date) {
    const payday = f.upcoming.find((e) => e.kind === "income" && e.date > (f.overdraft_date ?? ""));
    return (
      `Découvert prévu le ${shortDate(f.overdraft_date)} (point bas ${money(f.lowest_balance_cents, { whole: true })})` +
      (payday ? `, avant ${payday.label} du ${shortDate(payday.date)}.` : ".")
    );
  }
  if (f.status === "tight") {
    return `Ça se resserre : point bas à ${money(f.lowest_balance_cents, { whole: true })} le ${shortDate(f.lowest_balance_date)}.`;
  }
  return `Le mois passe sans découvert. Point bas : ${money(f.lowest_balance_cents, { whole: true })} le ${shortDate(f.lowest_balance_date)}.`;
}

export function Today() {
  const budget = useApi<BudgetResponse>("/api/budget");
  const aids = useApi<AidsResponse>("/api/aids");
  const cats = useApi<Category[]>("/api/categories");
  const agent = useApi<{ pending_proposals: number; onboarding_percent: number; active_agents: number }>("/api/agents/summary");
  const toast = useToast();
  const catName = (slug: string) => cats.data?.find((c) => c.slug === slug)?.name ?? slug;

  if (budget.error) return <ErrorNote message={budget.error} onRetry={budget.reload} />;
  if (!budget.data) return <Loading />;
  const { forecast: f, suggested_budgets: suggested, settings } = budget.data;
  const isEmpty = f.current_balance_cents === 0 && f.history.every((p) => p.balance_cents === 0) && f.upcoming.length === 0;

  const applySuggestion = async (slug: string, cents: number) => {
    await api(`/api/categories/${slug}`, { method: "PATCH", body: { monthly_budget_cents: cents } });
    toast(`Enveloppe fixée à ${money(cents, { whole: true })}.`);
    budget.reload();
  };

  if (isEmpty) return <Welcome onStarted={budget.reload} />;

  const perDay = f.safe_to_spend_per_day_cents;
  const unbudgeted = Object.entries(suggested).filter(([slug]) => !f.budgets.some((b) => b.slug === slug));

  return (
    <>
      <section className="today" aria-labelledby="today-title">
        <p className="today-lead" id="today-title">
          {perDay > 0 ? "Tu peux dépenser" : "Plus de marge ce mois-ci"}
        </p>
        <p className={`today-figure${perDay > 0 ? "" : " is-zero"}`}>
          {amountOnly(perDay)} €<small>par jour</small>
        </p>
        <p className="muted">{allowanceSentence(f, settings.buffer_cents, settings.savings_goal_cents)}</p>
        <p className="verdict" data-status={f.status}>{verdict(f)}</p>
      </section>

      {agent.data && agent.data.pending_proposals > 0 && (
        <div className="notice" style={{ marginBottom: "1.5rem" }}>
          Ton agent a {plural(agent.data.pending_proposals, "proposition", "propositions")} pour toi.{" "}
          <a href="#/situation">Les examiner</a>
        </div>
      )}

      <Initiatives onChanged={budget.reload} />

      <MonthChart
        history={f.history}
        projection={f.projection}
        events={f.upcoming}
        lowestCents={f.lowest_balance_cents}
        lowestDate={f.lowest_balance_date}
        daysInMonth={f.days_in_month}
      />

      <Decide />

      <dl className="figures">
        <div className="figure-item">
          <dt>Solde des comptes courants</dt>
          <dd>{money(f.current_balance_cents, { whole: true })}</dd>
        </div>
        <div className="figure-item">
          <dt>Reçu en {monthLabel(f.month)}</dt>
          <dd>{money(f.income_received_cents, { whole: true })}</dd>
        </div>
        <div className="figure-item">
          <dt>Dépensé en {monthLabel(f.month)}</dt>
          <dd>{money(f.spent_fixed_cents + f.spent_variable_cents, { whole: true })}</dd>
        </div>
        <div className="figure-item">
          <dt>Fin de mois estimée</dt>
          <dd>{money(f.projected_end_balance_cents, { whole: true })}</dd>
        </div>
      </dl>

      <div className="split section">
        <section aria-labelledby="upcoming-title">
          <h2 id="upcoming-title">D'ici la fin du mois</h2>
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
                  {events.map((e, i) => (
                    <div className="ledger-row" key={i}>
                      <div className="ledger-label">
                        <span className="sr-only">{shortDate(date)} : </span>
                        <b>{e.label}</b> {e.late && <span className="tag late">en retard</span>}
                      </div>
                      <Amount cents={e.amount_cents} />
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          )}
          <p className="faint" style={{ marginTop: "0.75rem" }}>
            Dépense courante habituelle : {money(f.daily_variable_rate_cents, { whole: true })} par jour, déjà comptée.
          </p>
        </section>

        <section aria-labelledby="env-title">
          <h2 id="env-title">Enveloppes</h2>
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
            <div className="stack" style={{ marginTop: "1.5rem" }}>
              <p className="muted">Suggestions d'après tes trois derniers mois :</p>
              {unbudgeted.slice(0, 5).map(([slug, cents]) => (
                <div className="envelope-head" key={slug}>
                  <span>{catName(slug)}</span>
                  <button className="btn link" onClick={() => void applySuggestion(slug, cents)}>
                    Fixer à {money(cents, { whole: true })}
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {aids.data && aids.data.summary.alerts.length > 0 && (
        <section className="section" aria-labelledby="aid-alerts">
          <h2 id="aid-alerts">À surveiller</h2>
          <div className="stack">
            {aids.data.summary.alerts.map((a) => (
              <div className="notice warn" key={a}>
                {a} <a href="#/aides">Voir les aides</a>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
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
