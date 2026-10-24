// « Ce qui est lié » : ce que tes contrats (lus dans Papiers), tes relevés et tes aides disent ensemble. Chaque
// constat porte ses pièces ; une piste statistique reste une piste. Bank n'envoie rien : résilier, réclamer,
// contester restent tes gestes — il prépare la démarche quand il y en a une.

import { useState, type FormEvent } from "react";
import { api, useApi, type Commute, type Correlation, type CorrelationsView } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Loading, useAsyncAction, useToast } from "./ui";

const LEVEL: Record<Correlation["level"], { text: string; cls: string }> = {
  danger: { text: "à régler", cls: "danger" },
  warning: { text: "à vérifier", cls: "warn" },
  info: { text: "bon à savoir", cls: "" },
};

export function Correlations() {
  const view = useApi<CorrelationsView>("/api/correlations");
  const [showHidden, setShowHidden] = useState(false);
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!view.data) return <Loading what="Ce qui est lié" />;
  const data = view.data;
  const serious = data.findings.filter((f) => !f.statistical);
  const hints = data.findings.filter((f) => f.statistical);
  return (
    <section className="section corr" aria-labelledby="corr-title">
      <h2 id="corr-title"><Icon name="route" /> Ce qui est lié</h2>
      <p className="section-note">
        Bank croise tes contrats lus dans Papiers (bail, assurances, bulletins), tes relevés et tes aides : un doublon
        d'assurance, un loyer qui dépasse le bail, une prime qui monte, une déclaration que le versement ne reflète
        pas. Chaque constat montre ses pièces.
      </p>
      {data.findings.length === 0 ? (
        <p className="muted">
          Rien de lié à signaler pour l'instant. Dépose ton bail et tes attestations d'assurance dans Papiers : c'est là
          que Bank trouve le plus souvent de l'argent qui dort.
          {data.hidden > 0 && <> {data.hidden} constat{data.hidden > 1 ? "s" : ""} écarté{data.hidden > 1 ? "s" : ""}.</>}
        </p>
      ) : (
        <ul className="corr-list">
          {serious.map((f) => <FindingRow key={f.key} f={f} commute={data.commute ?? null} onChange={view.reload} />)}
        </ul>
      )}
      {hints.length > 0 && (
        <details className="corr-hints" open>
          <summary>Pistes ({hints.length}) : des régularités, pas des causes</summary>
          <ul className="corr-list">
            {hints.map((f) => <FindingRow key={f.key} f={f} onChange={view.reload} />)}
          </ul>
        </details>
      )}
      {data.hidden > 0 && data.findings.length > 0 && (
        <button type="button" className="btn link" onClick={() => setShowHidden((v) => !v)} aria-expanded={showHidden}>
          {data.hidden} constat{data.hidden > 1 ? "s" : ""} écarté{data.hidden > 1 ? "s" : ""}
        </button>
      )}
      <p className="faint" style={{ marginTop: "0.75rem" }}>{data.disclaimer} Indice des loyers : {data.irl_source}.</p>
    </section>
  );
}

/** « loan:3:405 » : le prêt 3, simulé à 4,05 % ; le lien ouvre son simulateur de renégociation avec ce taux. */
export function simulatorHref(ref: string): string | null {
  const m = /^loan:(\d+):(\d+)$/.exec(ref);
  return m ? `#/patrimoine?pret=${m[1]}&taux=${m[2]}` : null;
}

function FindingRow({ f, commute, onChange }: { f: Correlation; commute?: Commute | null; onChange: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const [open, setOpen] = useState(false);
  const [editTrip, setEditTrip] = useState(false);
  const trip = f.kind === "real_expenses_ask" || (f.kind === "real_expenses" && editTrip);
  const toast = useToast();
  const dismiss = () => run(async () => {
    await api("/api/correlations/decide", { method: "POST", body: { key: f.key, decision: "dismissed" } });
    toast("Écarté.");
    onChange();
  });
  const start = () => run(async () => {
    const r = await api<{ procedure_id: number }>("/api/correlations/start", { method: "POST", body: { key: f.key } });
    window.location.hash = `#/demarches/${r.procedure_id}`;
  });
  return (
    <li className={`corr-row is-${f.level}`}>
      <div className="corr-head">
        <span className={`tag ${LEVEL[f.level].cls}`}>{LEVEL[f.level].text}</span>
        <span className="faint">{f.topic_label}</span>
        {f.yearly_cents ? <span className="corr-stake">{money(f.yearly_cents, { whole: true })} par an</span> : null}
      </div>
      <h3>{f.title}</h3>
      <p>{f.body}</p>
      {f.evidence.length > 0 && (
        <>
          <button type="button" className="btn link" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "Masquer les pièces" : `Les pièces (${f.evidence.length})`}
          </button>
          {open && (
            <ul className="corr-evidence">
              {f.evidence.map((e, i) => (
                <li key={i}>
                  <b>{e.label}</b> : {e.value}
                  {e.ref?.startsWith("doc:") && <> · <a href={`#/documents/${e.ref.slice(4)}`}>voir le document</a></>}
                  {e.ref?.startsWith("series:") && <> · <a href="#/operations">voir les opérations</a></>}
                  {e.ref?.startsWith("url:https://") && <> · <a href={e.ref.slice(4)} target="_blank" rel="noreferrer noopener">source</a></>}
                  {e.ref && simulatorHref(e.ref) && <> · <a href={simulatorHref(e.ref)!}>ouvrir le simulateur</a></>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {trip && <CommuteForm commute={commute ?? null} onDone={() => { setEditTrip(false); onChange(); }} />}
      <div className="actions">
        {f.kind === "real_expenses" && !editTrip && (
          <button type="button" className="btn link" onClick={() => setEditTrip(true)}>Modifier mon trajet</button>
        )}
        {f.procedure_id ? (
          <a className="btn sm" href={`#/demarches/${f.procedure_id}`}><Icon name="route" /> Démarche en cours</a>
        ) : f.action?.template ? (
          <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => void start()}>
            <Icon name="route" /> {f.action.label ?? "Préparer la démarche"}
          </button>
        ) : null}
        <button type="button" className="btn link" disabled={busy} onClick={() => void dismiss()}>Pas un problème</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}

/** Le trajet domicile-travail pour les frais réels : distance d'un aller, puissance fiscale, électrique. Chiffré. */
function CommuteForm({ commute, onDone }: { commute: Commute | null; onDone: () => void }) {
  const [km, setKm] = useState(commute ? commute.one_way_km.replace(".", ",") : "");
  const [hp, setHp] = useState(commute ? String(commute.fiscal_hp) : "");
  const [electric, setElectric] = useState(commute?.electric ?? false);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/correlations/commute", {
        method: "PUT",
        body: { one_way_km: km.replace(",", ".").trim(), fiscal_hp: Number(hp), electric },
      });
      toast("Trajet enregistré.");
      onDone();
    });
  };
  return (
    <form className="corr-commute" onSubmit={submit} aria-label="Trajet domicile-travail">
      <label>
        Distance d'un aller (km)
        <input inputMode="decimal" required value={km} placeholder="38" onChange={(e) => setKm(e.target.value)} />
      </label>
      <label>
        Puissance fiscale (CV, carte grise case P.6)
        <input inputMode="numeric" required value={hp} placeholder="5" onChange={(e) => setHp(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={electric} onChange={(e) => setElectric(e.target.checked)} /> Véhicule électrique
      </label>
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !km || !hp}>Comparer</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
