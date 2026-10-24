// Pilote vs consigne : pour chaque consigne, les rendez-vous prévus et ce que l'agent a rapporté — fait à temps,
// en retard, manqué, montant différent — avec le total prévu face au total fait. Un ruban de pastilles pour voir
// le rythme d'un coup d'œil, le tableau pour le détail, puis les mouvements faits hors consigne.

import { useState } from "react";
import { useApi, type PilotRuleTrack, type PilotSlot, type PilotTrack as PilotTrackData } from "../api";
import { money, percent, plural, shortDate } from "../format";
import { ErrorNote } from "./ui";

const STATUS: Record<PilotSlot["status"], { label: string; tone: string }> = {
  on_time: { label: "fait à temps", tone: "ok" },
  late: { label: "en retard", tone: "warn" },
  missed: { label: "manqué", tone: "danger" },
  waiting: { label: "attendu", tone: "" },
  in_progress: { label: "en cours", tone: "" },
  off: { label: "pilote éteint", tone: "" },
};
const SHOWN = 8;

export function slotLabel(rule: Pick<PilotRuleTrack, "every">, s: PilotSlot): string {
  if (rule.every === "day") return shortDate(s.start);
  if (rule.every === "week") return `sem. du ${shortDate(s.start)}`;
  return new Date(`${s.start}T12:00:00`).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
}

export function statusText(s: PilotSlot): string {
  if (s.status === "late") return `en retard de ${plural(s.delay_days, "jour", "jours")}`;
  return STATUS[s.status].label;
}

export function gapText(s: PilotSlot): string | null {
  if (!s.amount_differs) return null;
  return `${money(s.gap_cents, { signed: true })} (${s.gap_percent > 0 ? "+" : ""}${percent(s.gap_percent)})`;
}

export function PilotTrack() {
  const v = useApi<PilotTrackData>("/api/autonomy/track");
  if (v.error) return <ErrorNote message={v.error} onRetry={v.reload} />;
  if (!v.data) return null;
  const { rules, outside } = v.data;
  return (
    <section className="card reveal pl-track" style={{ marginTop: "1rem" }} aria-labelledby="pl-track">
      <div className="card-head"><h2 id="pl-track">Pilote vs consigne</h2><span className="faint">Depuis le {shortDate(v.data.since)}</span></div>
      {rules.length === 0 ? (
        <p className="muted">Aucune consigne suivie pour l'instant. Écris-en une dans le cadre (ex. acheter 50 € de BTC chaque lundi) : Bank comparera chaque rendez-vous à ce que l'agent rapporte.</p>
      ) : (
        rules.map((r) => <RuleTrack key={r.id} rule={r} />)
      )}
      {outside.length > 0 && (
        <div className="pl-outside">
          <h3>Hors consigne</h3>
          <ul>
            {outside.map((m) => (
              <li key={m.move_id}>
                <time dateTime={m.created_at}>{shortDate(m.created_at.slice(0, 10))}</time> <b>{m.text}</b> <span className="faint">— {m.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function RuleTrack({ rule }: { rule: PilotRuleTrack }) {
  const [all, setAll] = useState(false);
  const t = rule.totals;
  const recent = [...rule.slots].reverse();
  const rows = all ? recent : recent.slice(0, SHOWN);
  return (
    <article className="pl-track-rule">
      <header>
        <h3>{rule.label}</h3>
        <p className="pl-track-totals">
          Prévu <b>{money(t.expected_cents)}</b> · fait <b>{money(t.done_cents)}</b>
          {t.gap_cents !== 0 && <span className={t.gap_cents < 0 ? "down" : "up"}> ({money(t.gap_cents, { signed: true })})</span>}
        </p>
        <p className="faint">
          {plural(t.due, "rendez-vous passé", "rendez-vous passés")} : {t.on_time} à temps, {t.late} en retard, {t.missed} {t.missed > 1 ? "manqués" : "manqué"}
          {t.different > 0 ? `, ${plural(t.different, "montant différent", "montants différents")}` : ""}
        </p>
      </header>
      {rule.slots.length > 0 && (
        <ol className="pl-track-strip" aria-label={`Rythme : ${rule.slots.map((s) => `${slotLabel(rule, s)} ${statusText(s)}`).join(", ")}`}>
          {rule.slots.map((s) => <li key={s.period} className={`is-${s.status}${s.amount_differs ? " is-different" : ""}`} title={`${slotLabel(rule, s)} : ${statusText(s)}`} />)}
        </ol>
      )}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="plain">
            <thead><tr><th>Rendez-vous</th><th>Prévu le</th><th>État</th><th className="num">Montant</th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.period}>
                  <td>{slotLabel(rule, s)}</td>
                  <td>{s.status === "off" ? "—" : shortDate(s.due)}</td>
                  <td>
                    <span className={`tag ${STATUS[s.status].tone}`}>{statusText(s)}</span>
                    {s.trial && <span className="tag">essai</span>}
                    {s.amount_differs && <span className="tag warn">montant différent</span>}
                    {s.done_on && s.status === "late" && <span className="faint"> fait le {shortDate(s.done_on)}</span>}
                    {s.note && <span className="faint"> {s.note}</span>}
                    {s.refusals.length > 0 && <span className="pl-refusal"> Refusé par Bank : {s.refusals.join(" ; ")}</span>}
                  </td>
                  <td className="num">
                    {s.amount_cents !== null ? money(s.amount_cents) : "—"}
                    {gapText(s) && <span className="faint"> {gapText(s)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {recent.length > SHOWN && (
        <button type="button" className="btn sm link" onClick={() => setAll(!all)}>{all ? "Moins" : `Tout voir (${recent.length})`}</button>
      )}
    </article>
  );
}
