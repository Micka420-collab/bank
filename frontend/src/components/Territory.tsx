// Réglages › Ton adresse : l'adresse située sur la carte (code INSEE, coordonnées ; seule l'adresse tapée part, vers
// la Géoplateforme de l'IGN, avec ton accord), puis les jours fériés de ta zone et les vacances de ton académie,
// relus aux sources publiques (autre accord). Chaque donnée dit d'où elle vient et quand ; une relève ratée garde la
// précédente et le dit. La prévision du mois et l'année qui vient s'en servent.

import { useState, type FormEvent } from "react";
import { api, useApi, type ConsentView } from "../api";
import { dayText } from "./Baremes";
import { Icon } from "./Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "./ui";

export type TerritoryPlace = {
  query: string | null;
  label: string | null;
  city: string | null;
  citycode: string | null;
  postcode: string | null;
  depcode: string | null;
  lat: number | null;
  lon: number | null;
  score: number | null;
  precision: string | null;
  academy: string | null;
  geocoded_at: string | null;
  source: string | null;
  source_url: string | null;
};
export type TerritoryView = {
  consents: { geocoding: boolean; calendar: boolean };
  place: TerritoryPlace | null;
  zone: string;
  zone_label: string;
  holidays: { day: string; name: string }[];
  school: { academy: string; zone: string | null; breaks: { label: string; start: string; end: string }[] } | null;
  sources: { name: string; url: string | null; fetched_at: string | null; last_error: string | null }[];
  school_page: string;
};

const PRECISION: Record<string, string> = {
  housenumber: "au numéro près",
  street: "à la rue près",
  locality: "au lieu-dit près",
  municipality: "à la commune près",
};

/** « au numéro près », sinon le terme du géocodeur tel quel. */
export function precisionText(p: string | null): string {
  return p ? PRECISION[p] ?? p : "précision inconnue";
}

/** « Vacances de Noël : du 19/12/2026 au 03/01/2027 » ; un seul jour : « le 07/05/2027 ». */
export function breakText(b: { label: string; start: string; end: string }): string {
  return b.start === b.end ? `${b.label} : le ${dayText(b.start)}` : `${b.label} : du ${dayText(b.start)} au ${dayText(b.end)}`;
}

export function Territory() {
  const view = useApi<TerritoryView>("/api/territory");
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const [query, setQuery] = useState("");
  const toast = useToast();
  const data = view.data;
  const text = (purpose: string) => consents.data?.find((c) => c.purpose === purpose)?.text;

  const agree = (purpose: "geocoding" | "calendar") =>
    void run(async () => {
      await api(`/api/consents/${purpose}`, { method: "PUT", body: { granted: true } });
      if (purpose === "calendar") await api("/api/territory/calendar/refresh", { method: "POST" });
      view.reload();
      consents.reload();
    });
  const locate = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/territory/geocode", { method: "POST", body: { query } });
      setQuery("");
      toast("Adresse située : la commune sert maintenant de point de départ.");
      view.reload();
    });
  };
  const forget = () =>
    void run(async () => {
      await api("/api/territory/place", { method: "DELETE" });
      toast("Adresse effacée, avec les vacances de ton académie.");
      view.reload();
    });
  const refresh = () =>
    void run(async () => {
      const r = await api<{ result: { errors: string[] } }>("/api/territory/calendar/refresh", { method: "POST" });
      toast(r.result.errors.length ? r.result.errors.join(" ") : "Jours fériés et vacances relus.");
      view.reload();
    });

  return (
    <section className="card section territory" aria-labelledby="territory-title">
      <div className="card-head">
        <h2 id="territory-title"><Icon name="pin" /> Ton adresse et ton calendrier</h2>
        {data?.consents.calendar && (
          <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={refresh}>
            <Icon name="download" /> Relire maintenant
          </button>
        )}
      </div>
      <p className="muted">
        Ta commune et ses coordonnées servent de point de départ aux comparaisons locales : jours fériés de ta zone,
        vacances de ton académie, commune de ton logement. Tout est gardé chiffré ici.
      </p>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {!data && !view.error && <Loading />}
      {error && <ErrorNote message={error} />}
      {data && (
        <>
          <h3>Ton adresse</h3>
          {!data.consents.geocoding ? (
            <div className="panel card-quiet">
              <p className="muted">{text("geocoding") ?? "Ton adresse, telle que tu la tapes, est envoyée au géocodeur public de l'IGN, avec ton accord."}</p>
              <div className="actions">
                <button type="button" className="btn sm" disabled={busy} onClick={() => agree("geocoding")}>Oui, situer mon adresse</button>
                <span className="faint">Sans accord, rien ne part ; tu peux choisir ta commune à la main dans Patrimoine.</span>
              </div>
            </div>
          ) : (
            <form className="form-grid territory-form" onSubmit={locate} aria-label="Situer ton adresse">
              <Field label={data.place ? "Changer d'adresse" : "Ton adresse"} hint="seul ce que tu tapes part (data.geopf.fr) ; la commune seule suffit">
                <input value={query} required minLength={3} maxLength={200} placeholder="12 rue des Lilas 45000 Orléans"
                       autoComplete="street-address" onChange={(e) => setQuery(e.target.value)} />
              </Field>
              <div className="actions">
                <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || query.trim().length < 3}>Situer</button>
              </div>
            </form>
          )}
          {data.place && (
            <div className="panel card-quiet territory-place">
              <p><b>{data.place.label}</b> <span className="faint">({precisionText(data.place.precision)})</span></p>
              <dl className="baremes-items">
                <div><dt>Commune</dt><dd>{data.place.city} · code INSEE {data.place.citycode}</dd></div>
                <div><dt>Coordonnées</dt><dd>{data.place.lat?.toFixed(5).replace(".", ",")} ; {data.place.lon?.toFixed(5).replace(".", ",")}</dd></div>
                {data.place.academy && <div><dt>Académie</dt><dd>{data.place.academy}</dd></div>}
              </dl>
              <p className="faint">
                {data.place.geocoded_at && <>Situé le {dayText(data.place.geocoded_at)} · </>}
                Source : {data.place.source_url ? <a href={data.place.source_url} target="_blank" rel="noreferrer noopener">{data.place.source}</a> : data.place.source}
              </p>
              <button type="button" className="btn ghost xs" disabled={busy} onClick={forget}><Icon name="trash" /> Effacer l'adresse</button>
            </div>
          )}

          <h3>Jours fériés et vacances scolaires</h3>
          {!data.consents.calendar ? (
            <div className="panel card-quiet">
              <p className="muted">{text("calendar") ?? "Bank relit les jours fériés et les vacances scolaires aux sources publiques, avec ton accord."}</p>
              <div className="actions">
                <button type="button" className="btn sm" disabled={busy} onClick={() => agree("calendar")}>Oui, relire le calendrier</button>
                <span className="faint">Sans accord, la prévision ne décale aucun prélèvement.</span>
              </div>
            </div>
          ) : (
            <>
              <p className="faint">
                Zone des jours fériés : {data.zone_label}. Un prélèvement qui tombe un dimanche ou un jour férié est placé au
                jour ouvré suivant dans la prévision.
              </p>
              {data.holidays.length > 0 && (
                <ul className="territory-days">
                  {data.holidays.slice(0, 6).map((h) => <li key={h.day}><b>{dayText(h.day)}</b> {h.name}</li>)}
                </ul>
              )}
              {data.school ? (
                <>
                  <p>Vacances de l'académie de {data.school.academy}{data.school.zone ? ` (${data.school.zone})` : ""} :</p>
                  <ul className="territory-days">{data.school.breaks.map((b) => <li key={b.label + b.start}>{breakText(b)}</li>)}</ul>
                </>
              ) : (
                <p className="faint">
                  {data.place ? "Vacances scolaires pas encore relues." : "Situe ton adresse pour connaître les vacances de ton académie."}{" "}
                  <a href={data.school_page} target="_blank" rel="noreferrer noopener">Calendrier officiel</a>
                </p>
              )}
              {data.sources.length > 0 && (
                <ul className="baremes-sources">
                  {data.sources.map((s) => (
                    <li key={s.name}>
                      {s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.name}</a> : s.name}{" "}
                      <span className="faint">{s.fetched_at ? `· relu le ${dayText(s.fetched_at)}` : "· jamais relu"}</span>
                      {s.last_error && <span className="error"> · dernier essai en échec : {s.last_error}, données précédentes gardées</span>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
