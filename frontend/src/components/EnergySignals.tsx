// Signaux d'énergie (#33) : ton contrat (option, kVA, heures creuses), la puissance maximale Linky face aux kVA
// souscrits, la couleur Tempo, la simulation Base / Heures creuses / Tempo, la consommation à climat égal et le prix
// repère du gaz. Chaque chiffre vient du serveur avec sa source ; rien n'est calculé ici.

import { useState } from "react";
import {
  api,
  type ClimateView,
  type CurveView,
  type EnergyContract,
  type EnergyStatus,
  type GasView,
  type PowerCheckView,
  type SimulationView,
  type TempoView,
  type WeatherView,
} from "../api";
import { Field, useAsyncAction, useToast } from "./ui";
import { money, shortDate } from "../format";

const OPTION_LABELS: Record<EnergyContract["option"], string> = { base: "Base", hphc: "Heures creuses", tempo: "Tempo" };

export function EnergySignals({ status, onChanged }: { status: EnergyStatus; onChanged: () => void }) {
  return (
    <div className="stack">
      <ContractForm status={status} onChanged={onChanged} />
      {status.power && <PowerNote power={status.power} />}
      <TempoStrip tempo={status.tempo} onChanged={onChanged} />
      {status.simulation && <SimulationCard sim={status.simulation} curve={status.curve} onChanged={onChanged} />}
      {status.climate && <ClimateNote climate={status.climate} weather={status.weather} onChanged={onChanged} />}
      {status.gas && <GasLine gas={status.gas} />}
    </div>
  );
}

function ContractForm({ status, onChanged }: { status: EnergyStatus; onChanged: () => void }) {
  const [option, setOption] = useState<EnergyContract["option"]>(status.contract?.option ?? "base");
  const [kva, setKva] = useState<number>(status.contract?.kva ?? 6);
  const [hc, setHc] = useState<string>(status.contract?.hc ?? "");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const save = () =>
    void run(async () => {
      await api("/api/energy/contract", { method: "PUT", body: { option, kva, hc: hc.trim() || null } });
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
      <Field label="Heures creuses" hint="Sur ta facture, ex. 22:00-06:00 (facultatif)">
        <input value={hc} maxLength={40} placeholder="22:00-06:00" onChange={(e) => setHc(e.target.value)} />
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

// Accord donné puis relève immédiate : la même mécanique pour la météo et la courbe de charge.
function useConsentThen(purpose: string, then: string | null, onChanged: () => void) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const agree = () =>
    void run(async () => {
      await api(`/api/consents/${purpose}`, { method: "PUT", body: { granted: true } });
      if (then) {
        const r = await api<EnergyStatus>(then, { method: "POST" });
        const problem = r.weather_error ?? r.curve?.last_error;
        if (problem) toast(problem);
      }
      onChanged();
    });
  return [busy, error, agree] as const;
}

function SimulationCard({ sim, curve, onChanged }: { sim: SimulationView; curve?: CurveView; onChanged: () => void }) {
  const [busy, error, agree] = useConsentThen("linky_curve", "/api/energy/sync", onChanged);
  return (
    <section className="energy-sim" aria-label="Base, Heures creuses ou Tempo">
      <b>Base, Heures creuses ou Tempo sur ta consommation</b>
      {sim.mode && <span className={`sim-mode ${sim.mode === "exacte" ? "ok" : ""}`}>{sim.mode === "estimation" ? "Estimation" : `Calcul ${sim.mode}`}</span>}
      <p>{sim.message}</p>
      {sim.options.some((o) => o.total_cents !== null) && (
        <div className="table-scroll">
          <table className="sim-table">
            <thead><tr><th>Option ({sim.kva} kVA)</th><th>Sur l'année</th><th>Abonnement</th><th>Consommation</th></tr></thead>
            <tbody>
              {sim.options.map((o) => (
                <tr key={o.option} className={o.option === sim.current_option ? "sim-current" : undefined}>
                  <th scope="row">{o.label}{o.option === sim.current_option ? " (ton option)" : ""}</th>
                  {o.total_cents === null ? (
                    <td colSpan={3} className="faint">{o.missing}</td>
                  ) : (
                    <>
                      <td><b>{money(o.total_cents, { whole: true })}</b></td>
                      <td>{money(o.subscription_cents ?? 0, { whole: true })}</td>
                      <td>{money(o.energy_cents ?? 0, { whole: true })}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sim.assumptions.length > 0 && (
        <ul className="faint sim-assumptions">
          {sim.assumptions.map((a) => <li key={a}>{a}.</li>)}
          {sim.hc_breakeven_pct !== null && <li>Heures creuses coûte moins que Base dès {sim.hc_breakeven_pct} % de ta consommation en heures creuses.</li>}
        </ul>
      )}
      {curve && !curve.consent && (
        <p className="muted">
          Pour un calcul exact, Bank peut lire ta courbe de charge (toutes les 30 min) avec ton jeton Linky.{" "}
          <button type="button" className="btn quiet" disabled={busy} onClick={agree}>Utiliser la courbe de charge</button>
          {error && <span className="error" role="alert"> {error}</span>}
        </p>
      )}
      {curve?.consent && (
        <p className="faint">
          Courbe de charge : {curve.days ? `${curve.days} jours complets depuis le ${shortDate(curve.since ?? "")}` : "aucun jour pour l'instant"}
          {curve.last_error && <> · {curve.last_error}</>}
        </p>
      )}
      <p className="faint">
        Source : {sim.source}
        {sim.grid_url && <> · <a href={sim.grid_url} target="_blank" rel="noopener noreferrer">grille de la CRE</a></>}
        {" "}· <a href={sim.tempo_hours_url} target="_blank" rel="noopener noreferrer">heures Tempo</a>
      </p>
    </section>
  );
}

function ClimateNote({ climate, weather, onChanged }: { climate: ClimateView; weather: WeatherView; onChanged: () => void }) {
  const [busy, error, agree] = useConsentThen("meteo", "/api/energy/weather/refresh", onChanged);
  if (!weather.consent) {
    return (
      <p className="muted">
        Consommation à climat égal : Bank demande la température de ta commune à Open-Meteo (coordonnées arrondies à 10 km) avec ton accord.{" "}
        <button type="button" className="btn quiet" disabled={busy || !weather.place_known} onClick={agree}>Comparer à climat égal</button>
        {!weather.place_known && <span className="faint"> Enregistre d'abord ton adresse (Réglages › Ton adresse et ton calendrier).</span>}
        {error && <span className="error" role="alert"> {error}</span>}
      </p>
    );
  }
  return (
    <div className={`notice ${climate.status === "drift" ? "warn" : "ok"} climate-note`}>
      <b>Consommation à climat égal</b>
      <p>{climate.message}</p>
      <p className="faint">
        Degrés-jours de chauffe base {weather.base_c} °C (18 °C moins la température moyenne du jour).
        {climate.wh_per_hdd ? ` Ta consommation monte de ${climate.wh_per_hdd} Wh par degré-jour (mesuré sur l'année).` : ""}
        {` Bank signale au-delà de +${climate.threshold_pct} % à climat égal sur trois mois.`}
        {" "}Source : <a href={weather.source_url} target="_blank" rel="noopener noreferrer">{weather.source}</a>
        {weather.last_error && <> · {weather.last_error}</>}
      </p>
    </div>
  );
}

function GasLine({ gas }: { gas: GasView }) {
  return (
    <p className="faint gas-line">
      {gas.message} Source : <a href={gas.source_url} target="_blank" rel="noopener noreferrer">{gas.source}</a>
    </p>
  );
}
