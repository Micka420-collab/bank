// « Pour ton argent » sur l'écran Aujourd'hui : les meilleures pistes du moment, les démarches à relancer, un gain
// à confirmer, les nouveautés de la veille. Une carte, quelques lignes, chacune mène là où on agit.

import { useApi, type CopilotSummary } from "../api";
import { money, perPeriod, plural } from "../format";
import { Icon } from "./Icon";

export function Copilot() {
  const summary = useApi<CopilotSummary>("/api/copilot/summary");
  const s = summary.data;
  if (!s) return null;
  const { opportunities: opps, procedures: procs, watch, value } = s;
  const nothing =
    opps.length === 0 && procs.due.length === 0 && procs.ready === 0 && s.confirmations === 0 &&
    watch.new_relevant === 0 && !watch.engine_outdated && value.confirmed_yearly_cents === 0;
  if (nothing) return null;
  return (
    <section className="card copilot reveal" style={{ marginTop: "1rem", ["--i" as string]: 1 }} aria-labelledby="copilot-title">
      <div className="card-head">
        <h2 id="copilot-title">Pour ton argent</h2>
        {(value.confirmed_yearly_cents > 0 || value.estimated_yearly_cents > 0) && (
          <a className="copilot-value" href="#/opportunites">
            {value.confirmed_yearly_cents > 0 && <span className="is-confirmed">Obtenu {money(value.confirmed_yearly_cents, { whole: true })} / an</span>}
            {value.estimated_yearly_cents > 0 && <span className="faint">En cours ≈ {money(value.estimated_yearly_cents, { whole: true })}</span>}
          </a>
        )}
      </div>
      <ul className="copilot-list">
        {s.confirmations > 0 && (
          <li className="is-good"><Icon name="check" /><a href="#/opportunites">{plural(s.confirmations, "gain constaté à confirmer", "gains constatés à confirmer")}</a></li>
        )}
        {procs.due.map((p) => (
          <li key={`d${p.id}`} className="is-due"><Icon name="clock" /><a href={`#/demarches/${p.id}`}>{p.title}</a><span className="faint">à relancer</span></li>
        ))}
        {procs.ready > 0 && (
          <li><Icon name="route" /><a href="#/demarches">{plural(procs.ready, "démarche prête à valider", "démarches prêtes à valider")}</a></li>
        )}
        {opps.map((o) => (
          <li key={o.key}>
            <Icon name={o.deadline ? "clock" : "target"} />
            <a href="#/opportunites">{o.title}</a>
            {o.amount_cents > 0 && <span className="copilot-amount">{perPeriod(o.amount_cents, o.period)}</span>}
          </li>
        ))}
        {watch.new_relevant > 0 && (
          <li><Icon name="news" /><a href="#/veille">{plural(watch.new_relevant, "nouveauté officielle te concerne", "nouveautés officielles te concernent")}</a></li>
        )}
        {watch.engine_outdated && (
          <li><Icon name="spark" /><a href="#/veille">Nouvelle version du moteur de calcul des aides</a></li>
        )}
        {watch.stale_references > 0 && (
          <li><Icon name="alert" /><a href="#/veille">{plural(watch.stale_references, "donnée de référence à revérifier", "données de référence à revérifier")}</a></li>
        )}
      </ul>
      <div className="actions" style={{ marginTop: "0.75rem" }}>
        <a className="btn quiet sm" href="#/opportunites"><Icon name="target" /> Toutes les pistes</a>
        {procs.open + procs.waiting > 0 && <a className="btn ghost sm" href="#/demarches">Mes démarches ({procs.open + procs.waiting})</a>}
      </div>
    </section>
  );
}
