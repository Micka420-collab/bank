import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type AidStream, type AidsResponse, type Profile, type Simulation } from "../api";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { longDate, money, monthLabel, shortDate } from "../format";

const STATUS: Record<AidStream["status"], { text: string; cls: string }> = {
  ok: { text: "à jour", cls: "ok" },
  late: { text: "en retard", cls: "late" },
  changed: { text: "montant modifié", cls: "warn" },
  ended: { text: "arrêtée", cls: "" },
};

const HOUSING: [string, string][] = [
  ["locataire_vide", "Locataire (logement vide)"],
  ["locataire_meuble", "Locataire (meublé)"],
  ["locataire_hlm", "Locataire HLM"],
  ["locataire_foyer", "Foyer / résidence"],
  ["proprietaire", "Propriétaire"],
  ["primo_accedant", "Accédant (crédit en cours)"],
  ["loge_gratuitement", "Logé gratuitement"],
];
const ACTIVITY: [string, string][] = [
  ["actif", "En emploi"],
  ["chomeur", "Demandeur d'emploi"],
  ["etudiant", "Étudiant"],
  ["retraite", "Retraité"],
  ["inactif", "Sans activité"],
];

export function Aids() {
  const aids = useApi<AidsResponse>("/api/aids");
  if (aids.error) return <ErrorNote message={aids.error} onRetry={aids.reload} />;
  if (!aids.data) return <Loading />;
  const { summary, simulation, gaps, disclaimer } = aids.data;

  return (
    <>
      <div className="page-head">
        <h1>Aides de l'État</h1>
      </div>
      {summary.streams.length === 0 ? (
        <p className="muted">
          Aucun versement d'organisme public reconnu dans tes relevés (CAF, France Travail, CPAM, MSA, impôts…).
          Lance une simulation ci-dessous pour vérifier si tu as des droits non réclamés.
        </p>
      ) : (
        <p className="today-lead">
          {money(summary.total_last_12m_cents, { whole: true })} reçus sur 12 mois, soit{" "}
          {money(summary.monthly_average_cents, { whole: true })} par mois en moyenne.
        </p>
      )}

      {summary.alerts.length > 0 && (
        <div className="stack section">
          {summary.alerts.map((a) => (
            <div className="notice warn" key={a}>{a}</div>
          ))}
        </div>
      )}

      {summary.streams.length > 0 && (
        <section className="section" aria-labelledby="streams-title">
          <h2 id="streams-title">Versements suivis</h2>
          <table className="plain">
            <thead>
              <tr>
                <th>Aide</th>
                <th>12 derniers mois</th>
                <th className="num">Moyenne 3 mois</th>
                <th>Prochain versement</th>
              </tr>
            </thead>
            <tbody>
              {summary.streams.map((s) => (
                <tr key={s.kind}>
                  <td>
                    <b>{s.name}</b> <span className={`tag ${STATUS[s.status].cls}`}>{STATUS[s.status].text}</span>
                    <div className="faint">
                      <a href={s.portal} target="_blank" rel="noopener noreferrer">{s.organism_name}</a>
                      {", dernier versement le "}{shortDate(s.last_date)}
                    </div>
                    {s.message && <div className="faint">{s.message}</div>}
                  </td>
                  <td style={{ minWidth: "9rem" }}><MonthBars totals={s.monthly_totals} /></td>
                  <td className="num">{money(s.average_3m_cents)}</td>
                  <td>{s.next_expected ? `vers le ${shortDate(s.next_expected)}` : "ponctuel"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="section" aria-labelledby="sim-title">
        <h2 id="sim-title">Tes droits, simulés</h2>
        <p className="section-note">
          Calcul par OpenFisca, le moteur open source de l'État qui fait tourner les simulateurs officiels,
          exécuté sur ton serveur : ta situation ne quitte pas ta maison. Les revenus sont repris de tes relevés.
        </p>
        <ProfileAndSimulation simulation={simulation} onSimulated={aids.reload} />
        {gaps.length > 0 && (
          <div className="stack" style={{ marginTop: "1.5rem" }}>
            <h3>Écarts entre droits estimés et versements</h3>
            {gaps.map((g) => (
              <div className={`notice ${g.gap_cents > 0 ? "ok" : "danger"}`} key={g.kind}>
                <b>{g.name}</b> : {g.message}
              </div>
            ))}
          </div>
        )}
        <p className="faint" style={{ marginTop: "1rem" }}>{disclaimer}</p>
      </section>
    </>
  );
}

function lastMonths(n: number): string[] {
  const now = new Date();
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - (n - 1 - i), 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
}

function MonthBars({ totals }: { totals: Record<string, number> }) {
  const keys = lastMonths(12);
  const max = Math.max(1, ...keys.map((k) => totals[k] ?? 0));
  return (
    <div className="bars" aria-label="Versements mensuels">
      {keys.map((k) => (
        <i key={k} className={totals[k] ? undefined : "empty"} style={{ height: `${((totals[k] ?? 0) / max) * 100}%` }}
           title={`${monthLabel(k)} : ${money(totals[k] ?? 0)}`} />
      ))}
    </div>
  );
}

const EMPTY: Profile = {
  birth_date: "",
  postal_insee_code: "",
  housing: "locataire_vide",
  rent_cents: 0,
  activity: "actif",
  couple: false,
  partner_birth_date: null,
  partner_monthly_net_cents: 0,
  children_birth_dates: [],
  annual_taxable_income_n2_cents: null,
};

function ProfileAndSimulation({ simulation, onSimulated }: { simulation: Simulation | null; onSimulated: () => void }) {
  const stored = useApi<Profile | null>("/api/profile");
  const [p, setP] = useState<Profile>(EMPTY);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  useEffect(() => {
    if (stored.data) setP({ ...EMPTY, ...stored.data });
  }, [stored.data]);

  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setP((prev) => ({ ...prev, [k]: v }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const body = {
        ...p,
        partner_birth_date: p.couple ? p.partner_birth_date : null,
        children_birth_dates: p.children_birth_dates.filter(Boolean),
      };
      await api("/api/profile", { method: "PUT", body });
      await api("/api/aids/simulate", { method: "POST" });
      toast("Simulation terminée.");
      onSimulated();
    });
  };

  return (
    <div className="split">
      <form className="panel" onSubmit={submit} aria-label="Profil du foyer">
        <div className="form-grid">
          <Field label="Ta date de naissance">
            <input type="date" required value={p.birth_date} onChange={(e) => set("birth_date", e.target.value)} />
          </Field>
          <Field label="Code INSEE de ta commune" hint="5 caractères, ex. 45234 pour Orléans (différent du code postal).">
            <input required pattern="(\d{5}|2[ABab]\d{3})" value={p.postal_insee_code}
                   onChange={(e) => set("postal_insee_code", e.target.value.toUpperCase())} />
          </Field>
          <Field label="Logement">
            <select value={p.housing} onChange={(e) => set("housing", e.target.value)}>
              {HOUSING.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Loyer mensuel hors charges">
            <EuroInput value={p.rent_cents} onChange={(c) => set("rent_cents", c ?? 0)} />
          </Field>
          <Field label="Situation">
            <select value={p.activity} onChange={(e) => set("activity", e.target.value)}>
              {ACTIVITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Revenu net imposable N-2 (facultatif)" hint="Sur ton avis d'impôt : rend l'APL et le LEP plus justes.">
            <EuroInput value={p.annual_taxable_income_n2_cents} onChange={(c) => set("annual_taxable_income_n2_cents", c)} />
          </Field>
        </div>
        <label className="check" style={{ marginTop: "1rem" }}>
          <input type="checkbox" checked={p.couple} onChange={(e) => set("couple", e.target.checked)} />
          En couple (marié ou pacsé)
        </label>
        {p.couple && (
          <div className="form-grid" style={{ marginTop: "1rem" }}>
            <Field label="Date de naissance du conjoint">
              <input type="date" value={p.partner_birth_date ?? ""} onChange={(e) => set("partner_birth_date", e.target.value || null)} />
            </Field>
            <Field label="Salaire net mensuel du conjoint">
              <EuroInput value={p.partner_monthly_net_cents} onChange={(c) => set("partner_monthly_net_cents", c ?? 0)} />
            </Field>
          </div>
        )}
        <fieldset style={{ border: 0, padding: 0, margin: "1rem 0 0" }}>
          <legend className="muted">Enfants à charge</legend>
          <div className="stack" style={{ marginTop: "0.5rem" }}>
            {p.children_birth_dates.map((d, i) => (
              <div className="actions" style={{ marginTop: 0 }} key={i}>
                <input className="inline" type="date" aria-label={`Naissance de l'enfant ${i + 1}`} value={d}
                       style={{ width: "auto" }}
                       onChange={(e) => set("children_birth_dates", p.children_birth_dates.map((x, j) => (j === i ? e.target.value : x)))} />
                <button type="button" className="btn link"
                        onClick={() => set("children_birth_dates", p.children_birth_dates.filter((_, j) => j !== i))}>
                  Retirer
                </button>
              </div>
            ))}
            <button type="button" className="btn link" style={{ justifySelf: "start" }}
                    onClick={() => set("children_birth_dates", [...p.children_birth_dates, ""])}>
              Ajouter un enfant
            </button>
          </div>
        </fieldset>
        {error && <p className="error" role="alert" style={{ marginTop: "1rem" }}>{error}</p>}
        <div className="actions">
          <button className="btn" disabled={busy}>{busy ? "Calcul en cours…" : "Enregistrer et simuler"}</button>
        </div>
      </form>

      <div>
        {!simulation ? (
          <p className="muted">Renseigne ton foyer pour estimer tes droits ce mois-ci.</p>
        ) : (
          <div className="stack">
            <h3>Estimation pour {monthLabel(simulation.period)} {simulation.period.slice(0, 4)}</h3>
            {simulation.estimates.length === 0 ? (
              <p>Aucune aide estimée avec ces informations.</p>
            ) : (
              <table className="plain">
                <tbody>
                  {simulation.estimates.map((e) => (
                    <tr key={e.variable}>
                      <td>{e.label}</td>
                      <td className="num"><b>{money(e.monthly_cents)}</b> par mois</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p>
              Complémentaire santé solidaire :{" "}
              {simulation.css === "gratuite" ? "gratuite pour toi"
                : simulation.css === "participation" ? `avec participation de ${money(simulation.css_participation_cents)} par mois`
                : "non éligible"}.
            </p>
            <p>Livret d'épargne populaire : {simulation.lep_eligible ? "tu y as droit" : "non éligible"}.</p>
            <p className="faint">
              OpenFisca France {simulation.engine_version}
              {simulation.created_at && `, calculé le ${longDate(simulation.created_at)}`}
              {simulation.inputs && !simulation.inputs.used_declared_taxable_income &&
                ". Revenus N-2 extrapolés de tes relevés : renseigne ton revenu imposable pour plus de précision"}
              .
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
