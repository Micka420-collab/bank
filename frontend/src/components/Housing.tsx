// Situation › Ton logement face aux données publiques (#34) : les DPE de ton immeuble relus à l'ADEME (seul
// l'identifiant de ton adresse part ; c'est toi qui dis lequel est ton logement, Bank ne choisit jamais seul), la règle
// F/G de Service-Public, ta commune dans la carte des loyers de l'ANIL avec sa règle de fiabilité. Les constats qui en
// découlent s'affichent dans « Ce qui est lié » ; ici, ce qui manque pour en dire plus est dit tel quel.

import { api, useApi, type ConsentView } from "../api";
import { dayText } from "./Baremes";
import { HousingPublic, type HousingPublicView } from "./HousingPublic";
import { Icon } from "./Icon";
import { ErrorNote, Loading, useAsyncAction, useToast } from "./ui";

export type HousingDpe = {
  number: string;
  letter: string;
  ges: string | null;
  established: string;
  valid_until: string | null;
  building: string;
  address: string;
  where: string;
  floor: number | null;
  surface_m2: number | null;
  depcode: string | null;
};
type Source = { name: string; url: string | null; fetched_at: string | null; last_error: string | null } | null;
export type HousingView = HousingPublicView & {
  consent: boolean;
  dpe: {
    missing: string | null;
    candidates: HousingDpe[];
    total: number | null;
    chosen: HousingDpe | null;
    none_mine: boolean;
    chosen_at: string | null;
    source: Source;
    page: string;
  };
  rule: { text: string; source: string; url: string; checked_on: string };
  home: { kind: string | null; kind_source: string | null; surface_m2: number | null; surface_source: string | null; commune: string | null };
  market: {
    missing: string | null;
    file_label: string | null;
    row: { commune: string; m2: number; low: number; high: number; level: string; obs: number; r2: number; year: number } | null;
    reliable: boolean | null;
    rule: string;
    year: number | null;
    published: string | null;
    page: string | null;
    credit: string;
    source: Source;
  };
  findings: { key: string; kind: string; title: string }[];
  notes: string[];
};
type Refresh = { result: { status: string; message: string }; view: HousingView };

/** « 13,98 » : deux décimales, virgule. */
export function m2Text(x: number): string {
  return x.toFixed(2).replace(".", ",");
}

/** « Etage RDC; Porte 2 · 65,3 m² · appartement » : ce qui aide à reconnaître ton logement. */
export function dpeWhere(d: HousingDpe): string {
  const parts = [d.where || (d.floor !== null ? `étage ${d.floor}` : ""), d.surface_m2 ? `${String(d.surface_m2).replace(".", ",")} m²` : "", d.building];
  return parts.filter(Boolean).join(" · ");
}

function SourceLine({ s }: { s: Source }) {
  if (!s) return null;
  return (
    <p className="faint">
      {s.fetched_at ? `Relu le ${dayText(s.fetched_at)}` : "Jamais relu"} · {s.name}
      {s.last_error && <span className="error"> · dernier essai en échec : {s.last_error}, données précédentes gardées</span>}
    </p>
  );
}

export function Housing() {
  const view = useApi<HousingView>("/api/housing");
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const data = view.data;
  const text = consents.data?.find((c) => c.purpose === "housing")?.text;

  const agree = () =>
    void run(async () => {
      await api("/api/consents/housing", { method: "PUT", body: { granted: true } });
      view.reload();
      consents.reload();
    });
  const refresh = (what: "dpe" | "market" | "control" | "taxes" | "risks") =>
    void run(async () => {
      const r = await api<Refresh>(`/api/housing/${what}/refresh`, { method: "POST" });
      toast(r.result.message);
      view.reload();
    });
  const saveDetails = (epoque: string | null, rooms: number | null) =>
    void run(async () => {
      await api("/api/housing/details", { method: "POST", body: { epoque, rooms } });
      toast("Noté.");
      view.reload();
    });
  const choose = (number: string | null) =>
    void run(async () => {
      await api("/api/housing/dpe/choose", { method: "POST", body: { number } });
      toast(number ? "DPE retenu comme le tien." : "Noté : aucun de ces DPE n'est le tien.");
      view.reload();
    });

  return (
    <section className="card section housing" aria-labelledby="housing-title">
      <div className="card-head">
        <h2 id="housing-title"><Icon name="pin" /> Ton logement face aux données publiques</h2>
      </div>
      <p className="muted">
        Le DPE de ton logement (ADEME) et la règle qui gèle le loyer d'un logement classé F ou G ; ton loyer situé dans
        les annonces de ta commune (ANIL) et face à l'encadrement ; ta taxe foncière prévue ; les risques naturels.
        Les constats s'affichent dans « Ce qui est lié ».
      </p>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {!data && !view.error && <Loading />}
      {error && <ErrorNote message={error} />}
      {data && !data.consent && (
        <div className="panel card-quiet">
          <p className="muted">{text ?? "Bank relit le DPE de ton immeuble et la carte des loyers, avec ton accord."}</p>
          <div className="actions">
            <button type="button" className="btn sm" disabled={busy} onClick={agree}>Oui, comparer mon logement</button>
            <span className="faint">Sans accord, rien ne part.</span>
          </div>
        </div>
      )}
      {data && data.consent && (
        <>
          <h3>Le DPE de ton logement</h3>
          {data.dpe.missing ? (
            <p className="faint">{data.dpe.missing}</p>
          ) : (
            <div className="actions">
              <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => refresh("dpe")}>
                <Icon name="download" /> {data.dpe.source ? "Relire les DPE de mon adresse" : "Chercher les DPE de mon adresse"}
              </button>
            </div>
          )}
          {data.dpe.chosen && (
            <p>
              Ton logement : <b>classe {data.dpe.chosen.letter}</b>, DPE n° {data.dpe.chosen.number} du {dayText(data.dpe.chosen.established)}{" "}
              <span className="faint">({dpeWhere(data.dpe.chosen)})</span>
            </p>
          )}
          {data.dpe.none_mine && !data.dpe.chosen && <p className="faint">Aucun DPE de l'adresse n'est le tien : Bank lit la classe dans ton bail s'il la cite.</p>}
          {data.dpe.candidates.length > 0 && (
            <>
              <p className="faint">
                {data.dpe.candidates.length} DPE à ton adresse{data.dpe.total && data.dpe.total > data.dpe.candidates.length ? ` (sur ${data.dpe.total})` : ""} :
                indique lequel est ton logement. Bank n'en retient aucun seul.
              </p>
              <ul className="housing-dpe">
                {data.dpe.candidates.map((d) => (
                  <li key={d.number}>
                    <span className={`tag${d.letter === "F" || d.letter === "G" ? " danger" : ""}`}>{d.letter}</span>{" "}
                    {dpeWhere(d)} <span className="faint">· n° {d.number} du {dayText(d.established)}</span>
                    {data.dpe.chosen?.number === d.number ? (
                      <span className="tag ok">le tien</span>
                    ) : (
                      <button type="button" className="btn ghost xs" disabled={busy} onClick={() => choose(d.number)}>C'est le mien</button>
                    )}
                  </li>
                ))}
              </ul>
              {!data.dpe.none_mine && (
                <button type="button" className="btn ghost xs" disabled={busy} onClick={() => choose(null)}>Aucun n'est le mien</button>
              )}
            </>
          )}
          <SourceLine s={data.dpe.source} />
          <p className="faint">
            {data.rule.text} Source : <a href={data.rule.url} target="_blank" rel="noreferrer noopener">{data.rule.source}</a>, relue le {data.rule.checked_on}.
          </p>

          <h3>Ton loyer et les annonces de ta commune</h3>
          {data.market.missing ? (
            <p className="faint">{data.market.missing}</p>
          ) : (
            <div className="actions">
              <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => refresh("market")}>
                <Icon name="download" /> Relire la carte des loyers
              </button>
            </div>
          )}
          {data.market.row && (
            <p>
              {data.market.row.commune}, {data.market.file_label} : de <b>{m2Text(data.market.row.low)}</b> à <b>{m2Text(data.market.row.high)} €/m²</b>{" "}
              charges comprises (loyer prédit {m2Text(data.market.row.m2)} €/m², {data.market.row.obs} annonces, R² {m2Text(data.market.row.r2)}
              {data.market.row.level === "maille" ? ", estimé avec des communes voisines" : ""}).{" "}
              {!data.market.reliable && <span className="tag warn">peu fiable</span>}
            </p>
          )}
          <p className="faint">
            {data.market.rule} {data.market.credit}
            {data.market.page && <> · <a href={data.market.page} target="_blank" rel="noreferrer noopener">carte des loyers {data.market.year}</a></>}
            {data.home.surface_m2 && <> · surface prise : {String(data.home.surface_m2).replace(".", ",")} m² ({data.home.surface_source})</>}
          </p>
          <SourceLine s={data.market.source} />

          <HousingPublic data={data} busy={busy} refresh={refresh} saveDetails={saveDetails} />

          {data.notes.length > 0 && (
            <>
              <h3>Ce qui manque pour en dire plus</h3>
              <ul className="housing-notes">{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </>
          )}
          {data.findings.length > 0 && (
            <p className="faint">
              {data.findings.length === 1 ? "Un constat" : `${data.findings.length} constats`} dans « Ce qui est lié » :{" "}
              {data.findings.map((f) => f.title).join(" ; ")}.
            </p>
          )}
        </>
      )}
    </section>
  );
}
