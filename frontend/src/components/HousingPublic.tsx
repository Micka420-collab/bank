// Situation › Ton logement face aux données publiques, suite (#34) : l'encadrement des loyers (liste datée de la fiche
// F1314, loyers de référence de Paris en open data, simulateur officiel ailleurs ; l'époque de construction se
// demande, jamais devinée), la taxe foncière prévue à partir des taux votés (la revalorisation des bases reste dite
// inconnue), et Géorisques à côté de ton assurance habitation (injoignable depuis ce serveur : c'est dit).

import { useEffect, useState, type ReactNode } from "react";
import { money } from "../format";
import { dayText } from "./Baremes";
import { Icon } from "./Icon";

type Source = { name: string; url: string | null; fetched_at: string | null; last_error: string | null } | null;

export type HousingPublicView = {
  details: { epoque: string | null; rooms: number | null; epoques: { key: string; label: string }[] };
  control: {
    commune: string | null;
    territory: { name: string; start: string; partial: boolean; simulator: string; simulator_label: string; open_data: boolean } | null;
    list: { source: string; url: string; verified: string; checked_on: string; end: string };
    page: { verified: string; missing: string[]; changed: boolean } | null;
    paris: { quartier: string | null; years: number[]; page: string; source: string; arrete_2026: string } | null;
    cdc: { source: string; url: string };
    source: Source;
    paris_source: Source;
  };
  taxes: {
    code: string | null;
    known: { year: number; amount_cents: number; teom_cents: number | null; source: string; monthly: boolean } | null;
    forecast: { target: number; amount_cents: number | null; due: string | null; unknown_rates: number[] } | null;
    rates: { year: number; commune: string; tfb: number; teom: number }[];
    notes: string[];
    page: string;
    bases: { source: string; url: string };
    source: Source;
  };
  risks: {
    reachable: boolean | null;
    checked_at: string | null;
    last_error: string | null;
    page: string;
    insurance: { id: number; insurer: string | null; until: string | null } | null;
    source: Source;
  };
};

/** « 49,67 % » : deux décimales, virgule. */
export function rateText(x: number): string {
  return `${x.toFixed(2).replace(".", ",")} %`;
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
}

function Fetched({ s }: { s: Source }) {
  if (!s) return null;
  return (
    <p className="faint">
      {s.fetched_at ? `Relu le ${dayText(s.fetched_at)}` : "Jamais relu"} · {s.name}
      {s.last_error && <span className="error"> · dernier essai en échec : {s.last_error}</span>}
    </p>
  );
}

type Props = {
  data: HousingPublicView;
  busy: boolean;
  refresh: (what: "control" | "taxes" | "risks") => void;
  saveDetails: (epoque: string | null, rooms: number | null) => void;
};

export function HousingPublic({ data, busy, refresh, saveDetails }: Props) {
  const c = data.control;
  const t = data.taxes;
  const r = data.risks;
  const [epoque, setEpoque] = useState(data.details.epoque ?? "");
  const [rooms, setRooms] = useState(data.details.rooms ? String(data.details.rooms) : "");
  useEffect(() => {
    setEpoque(data.details.epoque ?? "");
    setRooms(data.details.rooms ? String(data.details.rooms) : "");
  }, [data.details.epoque, data.details.rooms]);
  const lastRates = t.rates.slice(-3);

  return (
    <>
      <h3>Encadrement des loyers</h3>
      {!c.territory ? (
        <p className="faint">
          {c.commune ? `${c.commune} n'est pas` : "Ta commune n'est pas"} dans la liste des communes où le loyer est plafonné.
        </p>
      ) : (
        <>
          <p>
            <b>{c.territory.name}</b> : loyer plafonné pour un bail signé ou renouvelé depuis le {dayText(c.territory.start)}
            {c.territory.partial ? " (une partie seulement du territoire de ta commune)" : ""}.{" "}
            {c.territory.open_data ? (
              c.paris?.quartier ? <>Ton quartier : <b>{c.paris.quartier}</b>.</> : null
            ) : (
              <span className="faint">Pas de données ouvertes vérifiées pour ces loyers de référence.</span>
            )}{" "}
            <Ext href={c.territory.simulator}>{c.territory.simulator_label}</Ext>
          </p>
          <div className="actions">
            <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => refresh("control")}>
              <Icon name="download" /> Relire l'encadrement
            </button>
          </div>
          {c.territory.open_data && (
            <div className="housing-details">
              <label>
                Époque de construction de l'immeuble
                <select value={epoque} disabled={busy} onChange={(e) => setEpoque(e.target.value)}>
                  <option value="">Je ne sais pas</option>
                  {data.details.epoques.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
                </select>
              </label>
              <label>
                Pièces principales
                <input type="number" min={1} max={20} inputMode="numeric" value={rooms} disabled={busy} onChange={(e) => setRooms(e.target.value)} />
              </label>
              <button type="button" className="btn ghost sm" disabled={busy} onClick={() => saveDetails(epoque || null, rooms ? Number(rooms) : null)}>
                Enregistrer
              </button>
            </div>
          )}
          {c.paris && (
            <p className="faint">
              Loyers de référence publiés pour {c.paris.years.length ? c.paris.years.join(", ") : "aucune année"} (valeurs indicatives ; l'arrêté préfectoral fait
              foi) · <Ext href={c.paris.page}>{c.paris.source}</Ext> · <Ext href={c.paris.arrete_2026}>arrêté de 2026</Ext>
            </p>
          )}
          <p className="faint">
            Sans accord avec le bailleur, la <Ext href={c.cdc.url}>commission départementale de conciliation</Ext> se saisit gratuitement.
          </p>
          <Fetched s={c.paris_source} />
        </>
      )}
      <p className="faint">
        Liste de la <Ext href={c.list.url}>fiche F1314</Ext> vérifiée le {dayText(c.list.verified)}, relue le {c.list.checked_on} ; fin de
        l'expérimentation écrite sur la fiche : {dayText(c.list.end)}.
        {c.page?.changed && <span className="tag warn">fiche revue le {dayText(c.page.verified)}</span>}
      </p>
      <Fetched s={c.source} />

      <h3>Taxe foncière</h3>
      {t.known ? (
        <p>
          {t.known.year} : <b>{money(t.known.amount_cents)}</b>{t.known.teom_cents !== null ? ` dont ${money(t.known.teom_cents)} de TEOM` : ""}{" "}
          <span className="faint">({t.known.source === "avis" ? "ton avis dans Papiers" : "tes prélèvements"}{t.known.monthly ? ", mensualisée" : ""})</span>
          {t.forecast && (
            <>
              <br />
              {t.forecast.target} :{" "}
              {t.forecast.amount_cents !== null ? <b>{money(t.forecast.amount_cents)} à bases inchangées</b> : "effet des taux non chiffré"}
              {t.forecast.due && <> · échéance vers le {dayText(t.forecast.due)}</>}
              {t.forecast.unknown_rates.length > 0 && <span className="faint"> · taux {t.forecast.unknown_rates.join(", ")} pas encore publiés</span>}
            </>
          )}
        </p>
      ) : null}
      {lastRates.length > 0 && (
        <ul className="housing-rates">
          {lastRates.map((x) => (
            <li key={x.year}>{x.year} : taxe foncière {rateText(x.tfb)}{x.teom ? `, TEOM ${rateText(x.teom)}` : ""}</li>
          ))}
        </ul>
      )}
      {t.code && (
        <div className="actions">
          <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => refresh("taxes")}>
            <Icon name="download" /> Relire les taux de ma commune
          </button>
        </div>
      )}
      {t.notes.map((n) => <p key={n} className="faint">{n}</p>)}
      <p className="faint">
        Taux : <Ext href={t.page}>{t.source?.name ?? "data.economie.gouv.fr"}</Ext> · revalorisation des bases non chiffrée (<Ext href={t.bases.url}>{t.bases.source}</Ext>).
      </p>
      <Fetched s={t.source} />

      <h3>Risques naturels et ton assurance habitation</h3>
      <p>
        {r.insurance ? (
          <>Ton assurance habitation : {r.insurance.insurer ?? "assureur non lu"}{r.insurance.until ? `, valable jusqu'au ${dayText(r.insurance.until)}` : ""}. </>
        ) : (
          <span className="faint">Pas d'assurance habitation dans Papiers. </span>
        )}
        {r.reachable === false && <span className="tag warn">Géorisques injoignable depuis ton serveur</span>}
        {r.reachable === true && <span className="faint">Géorisques répond ; Bank n'en lit pas encore les risques, faute d'en avoir vérifié le format.</span>}
      </p>
      <p className="faint">
        Les risques de ton adresse : <Ext href={r.page}>georisques.gouv.fr</Ext>
        {r.checked_at && <> · dernier essai le {dayText(r.checked_at)}</>}
      </p>
      <div className="actions">
        <button type="button" className={`btn ghost xs${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => refresh("risks")}>
          Réessayer Géorisques
        </button>
      </div>
    </>
  );
}
