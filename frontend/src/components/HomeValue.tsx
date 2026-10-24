// Ton logement : estimé par les ventes voisines (DVF, données ouvertes) — une fourchette (1er et 3e quartiles) autour
// de la médiane, le nombre de ventes et la période, la source et les dates (extraction, vente la plus récente). La
// médiane entre dans le patrimoine sur une ligne à part, le prêt immobilier en face. Seule la commune part.

import { useState, type FormEvent } from "react";
import { api, useApi, type CommuneHit, type ConsentView, type HomeEstimate, type HomeKind, type HomeRefresh, type HomeView } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Field, useAsyncAction, useToast } from "./ui";

/** « 24 oct. 2026 » : une extraction ou une vente se lit avec son année. */
export function fullDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

/** Où tombe la médiane dans la fourchette, en % (50 si la fourchette est fermée). */
export function medianPosition(e: Pick<HomeEstimate, "low_cents" | "median_cents" | "high_cents">): number {
  const span = e.high_cents - e.low_cents;
  if (span <= 0) return 50;
  return Math.min(100, Math.max(0, ((e.median_cents - e.low_cents) / span) * 100));
}

export function HomeValue({ onChange }: { onChange?: () => void }) {
  const view = useApi<HomeView>("/api/home");
  const [editing, setEditing] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const [note, setNote] = useState<string | null>(null);
  const toast = useToast();
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  const data = view.data;
  const reload = () => { view.reload(); onChange?.(); };
  const refresh = () =>
    void run(async () => {
      const r = await api<{ result: HomeRefresh }>("/api/home/refresh", { method: "POST" });
      setNote(r.result.status === "refreshed" ? null : r.result.message);
      if (r.result.status === "refreshed") toast("Ventes DVF mises à jour.");
      reload();
    });
  const showForm = data !== null && (editing || !data.profile);
  return (
    <section className="card section reveal home" style={{ ["--i" as string]: 4 }} aria-labelledby="home-title">
      <div className="card-head">
        <div>
          <h2 id="home-title">Ton logement</h2>
          <p className="muted">Si tu es propriétaire : sa valeur estimée par les ventes voisines des 24 derniers mois.</p>
        </div>
        {data?.profile && (
          <button type="button" className="btn quiet sm" aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
            <Icon name={editing ? "close" : "edit"} /> {editing ? "Fermer" : "Modifier"}
          </button>
        )}
      </div>
      {data && !data.consent && <HomeConsent onDone={() => { if (data.profile) refresh(); else reload(); }} />}
      {showForm && data && (
        <HomeForm view={data} onDone={(r) => { setEditing(false); setNote(r && r.status !== "refreshed" ? r.message : null); toast("Logement enregistré."); reload(); }} />
      )}
      {data?.profile && !editing && <HomeResult view={data} />}
      {note && <p className="notice" role="status">{note}</p>}
      {error && <ErrorNote message={error} />}
      {data?.profile && data.consent && (
        <div className="home-foot">
          <p className="faint">
            Source : <a href={data.source_url} target="_blank" rel="noreferrer noopener">{data.source}</a>
            {data.data.fetched_at && <> · extraction du {fullDate(data.data.fetched_at)}</>}
            {data.data.latest_sale && <> · vente la plus récente : {fullDate(data.data.latest_sale)}</>}
          </p>
          {data.data.error && !data.data.fresh && <p className="faint">Dernier essai en échec : {data.data.error}</p>}
          <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy || data.data.fresh} onClick={refresh}
                  title={data.data.fresh ? `Une extraction par trimestre : prochaine possible le ${fullDate(data.data.next_refresh)}` : undefined}>
            <Icon name="download" /> {data.data.fresh ? `À jour jusqu'au ${fullDate(data.data.next_refresh)}` : "Mettre à jour les ventes"}
          </button>
        </div>
      )}
      {data && <p className="faint">{data.disclaimer}</p>}
    </section>
  );
}

function HomeConsent({ onDone }: { onDone: () => void }) {
  const consents = useApi<ConsentView[]>("/api/consents");
  const spec = consents.data?.find((c) => c.purpose === "home_value");
  const [busy, error, run] = useAsyncAction();
  return (
    <div className="panel card-quiet home-consent">
      <p className="muted">{spec?.text ?? "Bank télécharge le fichier public des ventes de ta commune (DVF), avec ton accord."}</p>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}
                onClick={() => void run(async () => { await api("/api/consents/home_value", { method: "PUT", body: { granted: true } }); onDone(); })}>
          Oui, estimer avec les ventes DVF
        </button>
        <span className="faint">Sans accord, rien ne part ; ta description reste enregistrée ici.</span>
      </div>
    </div>
  );
}

function HomeForm({ view, onDone }: { view: HomeView; onDone: (r: HomeRefresh | null) => void }) {
  const p = view.profile;
  const [kind, setKind] = useState<HomeKind>(p?.kind ?? "appartement");
  const [surface, setSurface] = useState(p ? String(p.surface) : "");
  const [code, setCode] = useState(p?.code ?? "");
  const [commune, setCommune] = useState(p?.commune ?? "");
  const [section, setSection] = useState(p?.section ?? "");
  const [rooms, setRooms] = useState(p?.rooms ? String(p.rooms) : "");
  const [inWealth, setInWealth] = useState(p?.in_wealth ?? true);
  // Déjà déclaré à l'agent (placements hors Bank) : tu le dis, sinon il compterait deux fois dans son dossier.
  const owned = view.declared_property;
  const [declared, setDeclared] = useState(view.declared_match !== null);
  const [declaredAs, setDeclaredAs] = useState(view.declared_match ?? owned[0]?.label ?? "");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<CommuneHit[] | null>(null);
  const [busy, error, run] = useAsyncAction();
  const [min, max] = view.bounds.surface;
  const search = () =>
    void run(async () => {
      setHits(await api<CommuneHit[]>(`/api/home/communes?q=${encodeURIComponent(query.trim())}`));
    });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<{ result: HomeRefresh | null }>("/api/home/profile", {
        method: "PUT",
        body: {
          kind, surface: Number(surface), code: code.trim().toUpperCase(), commune: commune.trim() || null,
          section: section.trim() || null, rooms: rooms ? Number(rooms) : null, in_wealth: inWealth,
          declared_as: declared && declaredAs ? declaredAs : null,
        },
      });
      onDone(r.result);
    });
  };
  return (
    <form className="form-grid home-form" onSubmit={submit} aria-label="Décrire mon logement">
      <Field label="Type">
        <select value={kind} onChange={(e) => setKind(e.target.value as HomeKind)}>
          {view.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      </Field>
      <Field label="Surface habitable (m²)">
        <input type="number" inputMode="numeric" required min={min} max={max} value={surface} onChange={(e) => setSurface(e.target.value)} />
      </Field>
      {view.consent && (
        <Field label="Chercher ta commune" hint="nom ou code postal ; seul ce que tu tapes part (geo.api.gouv.fr)">
          <span className="home-search">
            <input value={query} maxLength={60} placeholder="Orléans ou 45000" onChange={(e) => setQuery(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (query.trim().length >= 2) search(); } }} />
            <button type="button" className="btn quiet sm" disabled={busy || query.trim().length < 2} onClick={search}>Chercher</button>
          </span>
        </Field>
      )}
      {hits && (
        <ul className="home-hits" aria-label="Communes trouvées">
          {hits.length === 0 && <li className="faint">Aucune commune trouvée.</li>}
          {hits.map((h) => (
            <li key={h.code}>
              <button type="button" className="btn link" onClick={() => { setCode(h.code); setCommune(h.name); setHits(null); }}>
                {h.name} <span className="faint">{h.postcodes.join(", ")} · {h.code}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Field label="Code INSEE de la commune" hint="5 caractères ; à Paris, Lyon, Marseille, celui de l'arrondissement (75115…)">
        <input required pattern="(\d{5}|2[ABab]\d{3})" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
      </Field>
      <Field label="Nom de la commune"><input value={commune} maxLength={80} onChange={(e) => setCommune(e.target.value)} /></Field>
      <Field label="Section cadastrale (facultatif)" hint="2 caractères, sur ton avis de taxe foncière : affine sur ton quartier">
        <input value={section} maxLength={2} pattern="[0-9A-Za-z]{1,2}" placeholder="AB" onChange={(e) => setSection(e.target.value.toUpperCase())} />
      </Field>
      <Field label="Pièces principales (facultatif)">
        <input type="number" inputMode="numeric" min={view.bounds.rooms[0]} max={view.bounds.rooms[1]} value={rooms} onChange={(e) => setRooms(e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={inWealth} onChange={(e) => setInWealth(e.target.checked)} /> Compter dans mon patrimoine
      </label>
      {owned.length > 0 && (
        <label className="check">
          <input type="checkbox" checked={declared} onChange={(e) => setDeclared(e.target.checked)} /> Déjà déclaré à ton agent (placements hors Bank)
        </label>
      )}
      {owned.length > 0 && declared && (
        <Field label="Sous le libellé" hint="cette ligne sort de son dossier : l'estimation la remplace">
          <select value={declaredAs} onChange={(e) => setDeclaredAs(e.target.value)}>
            {owned.map((a, i) => <option key={i} value={a.label}>{a.label} · {money(a.value_cents, { whole: true })}</option>)}
          </select>
        </Field>
      )}
      {error && <ErrorNote message={error} />}
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Estimer mon logement</button>
    </form>
  );
}

function HomeResult({ view }: { view: HomeView }) {
  const p = view.profile!;
  const e = view.estimate;
  const what = `${p.kind === "maison" ? "Maison" : "Appartement"} de ${p.surface} m²${p.rooms ? `, ${p.rooms} pièces` : ""} · ${p.commune ?? p.code}${p.section ? `, section ${p.section}` : ""}`;
  // Accord retiré : l'estimation déjà faite reste (elle est calculée ici, et compte dans le patrimoine) ; seules
  // les nouvelles extractions attendent.
  if (!view.consent && !e?.ok) return <p className="muted">{what}. L'estimation attend ton accord.</p>;
  if (!e) return <p className="muted">{what}. {view.data.error ? "Pas encore de ventes téléchargées." : "Ventes en cours de téléchargement : mets à jour."}</p>;
  if (!e.ok) return <><p className="muted">{what}.</p><p className="notice" role="status">{e.message}</p></>;
  const pos = medianPosition(e);
  return (
    <div className="home-result">
      <p className="muted">{what}.</p>
      {!view.consent && (
        <p className="notice" role="status">
          Accord retiré : l'estimation garde {view.data.fetched_at ? `l'extraction du ${fullDate(view.data.fetched_at)}` : "la dernière extraction"}, sans mise à jour.
        </p>
      )}
      <p className="home-figure">
        <span className="home-median">{money(e.median_cents, { whole: true })}</span>{" "}
        <span className="faint">valeur médiane · fourchette {money(e.low_cents, { whole: true })} – {money(e.high_cents, { whole: true })}</span>
      </p>
      <div className="home-range" role="img" aria-label={`Fourchette de ${money(e.low_cents, { whole: true })} à ${money(e.high_cents, { whole: true })}, médiane ${money(e.median_cents, { whole: true })}`}>
        <i />
        <em style={{ left: `${pos}%` }} />
      </div>
      <div className="home-range-labels faint" aria-hidden="true">
        <span>{money(e.low_m2_cents, { whole: true })}/m²</span>
        <span>{money(e.median_m2_cents, { whole: true })}/m²</span>
        <span>{money(e.high_m2_cents, { whole: true })}/m²</span>
      </div>
      <p className="faint">
        {e.count} ventes comparables ({e.scope === "section" ? `section ${p.section}` : "toute la commune"}, surface ± {e.tolerance_percent} %
        {e.rooms_used ? ", pièces ± 1" : ""}) du {fullDate(e.first_sale!)} au {fullDate(e.last_sale!)} ; fenêtre de 24 mois jusqu'au {fullDate(e.window_end!)}.
      </p>
      {e.notes.length > 0 && <ul className="home-notes">{e.notes.map((n) => <li key={n} className="faint">{n}</li>)}</ul>}
      {view.in_wealth ? (
        <dl className="home-net" aria-label="Dans ton patrimoine">
          <div><dt>Logement (estimation DVF)</dt><dd>{money(e.median_cents, { whole: true })}</dd></div>
          {view.loans.map((l) => <div key={l.id}><dt>{l.name} · restant dû</dt><dd>−{money(l.remaining_cents, { whole: true })}</dd></div>)}
          {view.loans.length > 0 && view.net_cents !== null && <div className="home-net-total"><dt>Net</dt><dd>{money(view.net_cents, { whole: true })}</dd></div>}
        </dl>
      ) : (
        <p className="faint">Pas compté dans ton patrimoine (modifiable).</p>
      )}
    </div>
  );
}
