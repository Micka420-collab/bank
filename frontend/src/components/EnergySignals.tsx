// Signaux d'énergie (#33) : ton contrat (option et kVA), la puissance maximale Linky face aux kVA souscrits, et la
// couleur Tempo du jour et du lendemain. Chaque chiffre vient du serveur avec sa source ; rien n'est calculé ici.

import { useState } from "react";
import { api, type EnergyContract, type EnergyStatus, type PowerCheckView, type TempoView } from "../api";
import { Field, useAsyncAction, useToast } from "./ui";
import { money, shortDate } from "../format";

const OPTION_LABELS: Record<EnergyContract["option"], string> = { base: "Base", hphc: "Heures creuses", tempo: "Tempo" };

export function EnergySignals({ status, onChanged }: { status: EnergyStatus; onChanged: () => void }) {
  return (
    <div className="stack">
      <ContractForm status={status} onChanged={onChanged} />
      {status.power && <PowerNote power={status.power} />}
      <TempoStrip tempo={status.tempo} onChanged={onChanged} />
    </div>
  );
}

function ContractForm({ status, onChanged }: { status: EnergyStatus; onChanged: () => void }) {
  const [option, setOption] = useState<EnergyContract["option"]>(status.contract?.option ?? "base");
  const [kva, setKva] = useState<number>(status.contract?.kva ?? 6);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const save = () =>
    void run(async () => {
      await api("/api/energy/contract", { method: "PUT", body: { option, kva } });
      toast("Contrat enregistré.");
      onChanged();
    });
  return (
    <form className="form-grid" aria-label="Ton contrat d'électricité" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <Field label="Option" hint={status.contract ? undefined : "Sur ta facture : Base, Heures creuses ou Tempo."}>
        <select value={option} onChange={(e) => setOption(e.target.value as EnergyContract["option"])}>
          {(Object.keys(OPTION_LABELS) as EnergyContract["option"][]).map((o) => <option key={o} value={o}>{OPTION_LABELS[o]}</option>)}
        </select>
      </Field>
      <Field label="Puissance souscrite">
        <select value={kva} onChange={(e) => setKva(Number(e.target.value))}>
          {status.standard_kva.map((k) => <option key={k} value={k}>{k} kVA</option>)}
        </select>
      </Field>
      <button className="btn quiet" disabled={busy}>Enregistrer le contrat</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

function PowerNote({ power }: { power: PowerCheckView }) {
  const tone = power.status === "too_high" ? "warn" : "ok";
  return (
    <div className={`notice ${tone} power-note`}>
      <b>Puissance souscrite{power.status === "too_high" ? " trop haute" : ""}</b>
      <p>{power.message}</p>
      {power.status === "too_high" && power.yearly_saving_cents !== null && (
        <p className="faint">Écart estimé : {money(power.yearly_saving_cents, { whole: true })} par an.</p>
      )}
      <p className="faint">
        Source : {power.source}
        {power.grid_source && <> · {power.grid_url ? <a href={power.grid_url} target="_blank" rel="noopener noreferrer">{power.grid_source}</a> : power.grid_source}</>}
      </p>
      {power.last_error && <p className="error">{power.last_error}</p>}
    </div>
  );
}

function TempoStrip({ tempo, onChanged }: { tempo: TempoView; onChanged: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const agree = () =>
    void run(async () => {
      await api("/api/consents/tempo", { method: "PUT", body: { granted: true } });
      const r = await api<EnergyStatus>("/api/energy/tempo/refresh", { method: "POST" });
      if (r.tempo_error) toast(r.tempo_error);
      onChanged();
    });
  if (!tempo.consent) {
    return (
      <p className="muted">
        Couleur Tempo du jour et de demain : Bank la demande à api-couleur-tempo.fr (relais de RTE) avec ton accord.{" "}
        <button type="button" className="btn quiet" disabled={busy} onClick={agree}>Afficher la couleur Tempo</button>
        {error && <span className="error" role="alert"> {error}</span>}
      </p>
    );
  }
  return (
    <div className="tempo-strip">
      <span>Tempo :</span>
      {[tempo.today, tempo.tomorrow].map((d, i) => d && (
        <span key={d.day} className={`tempo-day ${d.color ? `tempo-${d.color}` : "tempo-unknown"}`}>
          {i === 0 ? "aujourd'hui" : "demain"} ({shortDate(d.day)}) : {d.label}
        </span>
      ))}
      <span className="faint">
        {tempo.is_tempo ? "Alerte la veille d'un jour rouge, en soirée, hors heures calmes. " : ""}
        Source : <a href="https://www.api-couleur-tempo.fr/" target="_blank" rel="noopener noreferrer">{tempo.source}</a>
        {tempo.last_error && <> · {tempo.last_error}</>}
      </span>
    </div>
  );
}
