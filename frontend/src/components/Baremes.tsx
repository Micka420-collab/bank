// Réglages › Barèmes : les taux, tarifs et indices que Bank applique, chacun avec sa date et sa source ; les mises à
// jour que Bank a faites seul (ancienne → nouvelle valeur), avec le retour au fichier livré ; les indices relevés
// (inflation, IRL, SMIC) ; « Vérifier maintenant ». Rien ne change en silence : chaque mise à jour est aussi dans la
// Veille et au journal. Sans ton accord, aucune source n'est interrogée.

import { useState } from "react";
import { api, useApi, type ConsentView } from "../api";
import { Icon } from "./Icon";
import { ErrorNote, Loading, useAsyncAction, useToast } from "./ui";

export type BaremeGroup = {
  key: string;
  title: string;
  as_of: string | null;
  source: string | null;
  updated: boolean;
  items: { label: string; value: string }[];
};
export type BaremeUpdate = {
  path: string;
  label: string;
  shown_old: string;
  shown_new: string;
  effective_from: string;
  source: string;
  source_url: string;
  applied_at: string;
  reverted_at: string | null;
  state: "active" | "reverted" | "file_newer";
};
export type BaremeIndex = {
  key: string;
  label: string;
  value: string;
  period: string;
  period_label: string;
  note: string | null;
  source_url: string;
  fetched_at: string;
};
export type BaremeSource = {
  slug: string;
  name: string;
  url: string;
  enabled: boolean;
  last_checked_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  next_check: string | null;
};
export type BaremesView = {
  consent: boolean;
  sources: BaremeSource[];
  in_effect: BaremeGroup[];
  updates: BaremeUpdate[];
  indices: BaremeIndex[];
  irl: { checked: number; gaps: string[]; missing: string[]; applied: boolean } | null;
  notes: { at: string; text: string }[];
};
export type BaremesRefresh = { status: "checked" | "failed" | "fresh"; message: string; changes: unknown[] };

/** « 01/08/2026 » : une date d'effet se lit en entier. */
export function dayText(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

const STATE_TEXT: Record<BaremeUpdate["state"], string> = {
  active: "en vigueur",
  reverted: "écartée",
  file_newer: "remplacée par le fichier, plus récent",
};

export function Baremes() {
  const view = useApi<BaremesView>("/api/baremes");
  const [busy, error, run] = useAsyncAction();
  const [note, setNote] = useState<string | null>(null);
  const toast = useToast();
  const data = view.data;
  const check = () =>
    void run(async () => {
      const r = await api<{ result: BaremesRefresh }>("/api/baremes/refresh", { method: "POST" });
      setNote(r.result.message);
      if (r.result.changes.length) toast("Barèmes mis à jour : le détail est aussi dans la Veille.");
      view.reload();
    });
  const revert = (u: BaremeUpdate) =>
    void run(async () => {
      await api("/api/baremes/revert", { method: "POST", body: { path: u.path } });
      toast(`${u.label} : retour à la valeur du fichier (${u.shown_old}).`);
      view.reload();
    });
  return (
    <section className="card section baremes" aria-labelledby="baremes-title">
      <div className="card-head">
        <h2 id="baremes-title"><Icon name="news" /> Barèmes</h2>
        {data?.consent && (
          <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={check}>
            <Icon name="download" /> Vérifier maintenant
          </button>
        )}
      </div>
      <p className="muted">
        Les taux, tarifs et indices que Bank utilise, avec leur date et leur source. Une fois par mois, Bank les relit
        aux sources officielles et applique ce qui a changé ; le fichier livré avec Bank reste le repli.
      </p>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {!data && !view.error && <Loading />}
      {data && !data.consent && <BaremesConsent onDone={() => { view.reload(); check(); }} />}
      {note && <p className="notice" role="status">{note}</p>}
      {error && <ErrorNote message={error} />}
      {data && (
        <>
          <div className="baremes-groups">
            {data.in_effect.map((g) => (
              <div key={g.key} className="panel card-quiet">
                <h3>{g.title} {g.updated && <span className="tag ok">mis à jour par Bank</span>}</h3>
                <dl className="baremes-items">
                  {g.items.map((i) => <div key={i.label}><dt>{i.label}</dt><dd>{i.value}</dd></div>)}
                </dl>
                <p className="faint">
                  {g.as_of && <>En vigueur depuis le {dayText(g.as_of)} · </>}Source : {g.source ?? "non précisée"}
                </p>
              </div>
            ))}
          </div>
          {data.updates.length > 0 && (
            <>
              <h3>Mises à jour faites par Bank</h3>
              <ul className="baremes-updates">
                {data.updates.map((u) => (
                  <li key={u.path} data-state={u.state}>
                    <div>
                      <b>{u.label}</b> : {u.shown_old} → {u.shown_new}{" "}
                      <span className={`tag${u.state === "active" ? " ok" : ""}`}>{STATE_TEXT[u.state]}</span>
                      <p className="faint">
                        Depuis le {dayText(u.effective_from)} · appliquée le {dayText(u.applied_at)} ·{" "}
                        <a href={u.source_url} target="_blank" rel="noreferrer noopener">{u.source}</a>
                      </p>
                    </div>
                    {u.state === "active" && (
                      <button type="button" className="btn ghost xs" disabled={busy} onClick={() => revert(u)}
                              aria-label={`${u.label} : revenir à ${u.shown_old}`}>
                        <Icon name="undo" /> Revenir au fichier
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {data.indices.length > 0 && (
            <>
              <h3>Indices relevés</h3>
              <dl className="baremes-items">
                {data.indices.map((i) => (
                  <div key={i.key}>
                    <dt>{i.label} <span className="faint">({i.period_label})</span></dt>
                    <dd><a href={i.source_url} target="_blank" rel="noreferrer noopener" title={i.note ?? undefined}>{i.value}</a></dd>
                  </div>
                ))}
              </dl>
              {data.irl && (
                <p className="faint">
                  {data.irl.gaps.length === 0
                    ? `IRL : les ${data.irl.checked} trimestres relus à l'INSEE concordent avec la table livrée avec Bank.`
                    : `IRL : la table livrée avec Bank différait de l'INSEE (${data.irl.gaps.join(" ; ")}).`}
                  {data.irl.missing.length > 0 && ` Nouveaux trimestres publiés : ${data.irl.missing.join(", ")}.`}
                  {(data.irl.gaps.length > 0 || data.irl.missing.length > 0) &&
                    (data.irl.applied
                      ? " Ton loyer est vérifié avec les indices de l'INSEE."
                      : " Ton loyer sera vérifié avec les indices de l'INSEE à la prochaine relève.")}
                </p>
              )}
            </>
          )}
          {data.notes.length > 0 && (
            <>
              <h3>Ce que la dernière vérification n'a pas changé</h3>
              <ul className="baremes-notes">{data.notes.map((n) => <li key={n.text} className="faint">{n.text}</li>)}</ul>
            </>
          )}
          <h3>Sources</h3>
          <ul className="baremes-sources">
            {data.sources.map((s) => (
              <li key={s.slug}>
                <a href={s.url} target="_blank" rel="noreferrer noopener">{s.name}</a>{" "}
                <span className="faint">
                  {!s.enabled ? "· en pause (Veille › Sources)" : s.last_ok_at ? `· relue le ${dayText(s.last_ok_at)}` : "· jamais relue"}
                  {s.next_check && s.enabled && ` · prochaine le ${dayText(s.next_check)}`}
                </span>
                {s.last_error && <span className="error"> · dernier essai en échec : {s.last_error}, valeurs précédentes gardées</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function BaremesConsent({ onDone }: { onDone: () => void }) {
  const consents = useApi<ConsentView[]>("/api/consents");
  const spec = consents.data?.find((c) => c.purpose === "baremes");
  const [busy, error, run] = useAsyncAction();
  return (
    <div className="panel card-quiet">
      <p className="muted">{spec?.text ?? "Bank relit chaque mois des données publiques (CRE, Caisse des Dépôts, INSEE, OpenFisca), avec ton accord."}</p>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}
                onClick={() => void run(async () => { await api("/api/consents/baremes", { method: "PUT", body: { granted: true } }); onDone(); })}>
          Oui, tenir les barèmes à jour
        </button>
        <span className="faint">Sans accord, rien ne part : Bank garde les valeurs du fichier livré.</span>
      </div>
    </div>
  );
}
