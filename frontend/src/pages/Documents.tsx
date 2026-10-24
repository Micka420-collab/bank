// Documents : le coffre-fort. Tu déposes une photo ou un PDF (plusieurs d'un coup, ou l'appareil photo du
// téléphone) ; Bank le chiffre, le lit chez toi (OCR compris) et en garde les valeurs utiles, chacune avec la
// ligne où il l'a lue. Rien n'est deviné : ce qui manque s'affiche « non trouvé », tu corriges d'un clic.

import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  api,
  useApi,
  type VaultDetail,
  type VaultField,
  type VaultFieldType,
  type VaultListing,
  type VaultStatus,
  type VaultSummary,
  type VaultUpload,
} from "../api";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { MailProposals } from "../components/MailProposals";
import { Ring } from "../components/Ring";
import { SharePaper } from "../components/SharePaper";
import { ErrorNote, Loading, useAsyncAction, useCelebrate, useToast } from "../components/ui";
import { money, plural } from "../format";

const STATUS: Record<VaultStatus, string> = {
  processing: "Lecture…",
  to_review: "À vérifier",
  verified: "Vérifié",
  failed: "Illisible",
  refused: "Non gardé",
};
const METHOD: Record<string, string> = { pdf_text: "texte du PDF", ocr: "lecture de l'image (OCR)", none: "aucune lecture" };
const ACCEPT = "application/pdf,image/jpeg,image/png,image/webp";
const MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];

export function fullDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

export function monthName(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return `${MONTHS[(m ?? 1) - 1]} ${y}`;
}

/** Valeur affichée selon son type (les montants circulent en centimes). */
export function showValue(type: VaultFieldType, value: number | string): string {
  if (type === "amount") return money(Number(value));
  if (type === "date") return fullDate(String(value));
  if (type === "month") return monthName(String(value));
  if (type === "percent") return `${String(value).replace(".", ",")} %`;
  if (type === "number") return Number.isFinite(Number(value)) ? Number(value).toLocaleString("fr-FR") : String(value);
  return String(value);
}

/** Valeur pré-remplie dans le champ de correction, dans la forme qu'on écrirait à la main. */
export function editValue(type: VaultFieldType, value: number | string): string {
  if (type === "amount") return (Number(value) / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (type === "date") return String(value).split("-").reverse().join("/");
  if (type === "month") return String(value).split("-").reverse().join("/");
  if (type === "percent") return String(value).replace(".", ",");
  return String(value);
}

function bytes(n: number): string {
  if (n < 1_000_000) return `${Math.max(1, Math.round(n / 1000))} Ko`;
  return `${(n / 1_000_000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Mo`;
}

function daysUntil(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  const target = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1).getTime();
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target - today.getTime()) / 86_400_000);
}

export function Documents({ docId }: { docId: number | null }) {
  return docId === null ? <Library /> : <DocumentDetail id={docId} />;
}

// ------------------------------------------------------------------ bibliothèque
function Library() {
  const [kind, setKind] = useState<string>("");
  const listing = useApi<VaultListing>(`/api/vault${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
  const [busy, error, run] = useAsyncAction();
  const [skipped, setSkipped] = useState<VaultUpload["skipped"]>([]);
  const [dragging, setDragging] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const reload = listing.reload;

  // Lecture en tâche de fond : on regarde où elle en est toutes les deux secondes, tant qu'il y en a une.
  useEffect(() => {
    if (!listing.data?.processing) return undefined;
    const t = window.setTimeout(reload, 2000);
    return () => window.clearTimeout(t);
  }, [listing.data, reload]);

  const send = (list: FileList | File[] | null, source: "upload" | "camera") => {
    const chosen = Array.from(list ?? []);
    if (chosen.length === 0) return;
    void run(async () => {
      const form = new FormData();
      chosen.slice(0, 10).forEach((f) => form.append("files", f, f.name));
      form.append("source", source);
      const r = await api<VaultUpload>("/api/vault/upload", { method: "POST", form });
      setSkipped(r.skipped);
      if (r.documents.length) toast(`${plural(r.documents.length, "document déposé", "documents déposés")} : lecture en cours.`);
      if (chosen.length > 10) setSkipped((s) => [...s, { filename: `${chosen.length - 10} autres`, reason: "10 fichiers au plus par envoi" }]);
      listing.reload();
    });
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    send(e.dataTransfer.files, "upload");
  };

  const docs = listing.data?.documents ?? [];
  const toReview = docs.filter((d) => d.status === "to_review").length;
  const expiring = docs.filter((d) => d.expires_on && daysUntil(d.expires_on) <= 60).sort((a, b) => (a.expires_on! < b.expires_on! ? -1 : 1));
  const total = Object.values(listing.data?.counts ?? {}).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p className="muted">Ton coffre-fort, chiffré sur ton serveur. Dépose une photo ou un PDF : Bank le lit et garde les chiffres utiles, avec la ligne où il les a trouvés.</p>
        </div>
        <div className="vault-actions">
          <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => camera.current?.click()}>
            <Icon name="camera" /> Prendre une photo
          </button>
          <button type="button" className="btn quiet" disabled={busy} onClick={() => files.current?.click()}>
            <Icon name="upload" /> Ajouter des fichiers
          </button>
          <input ref={camera} type="file" accept="image/*" capture="environment" hidden aria-label="Photo d'un document"
                 onChange={(e) => { send(e.target.files, "camera"); e.target.value = ""; }} />
          <input ref={files} type="file" accept={ACCEPT} multiple hidden aria-label="Fichiers à déposer"
                 onChange={(e) => { send(e.target.files, "upload"); e.target.value = ""; }} />
        </div>
      </div>

      <div className={`vault-drop${dragging ? " is-over" : ""}`}
           onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
           onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <Icon name="vault" />
        <p><b>Glisse tes documents ici</b> <span className="faint">— PDF, JPEG, PNG ou WebP, 10 à la fois, 12 Mo chacun.</span></p>
        {listing.data && !listing.data.ocr_available && (
          <p className="faint">Les photos sont gardées mais pas lues sur ce serveur (Tesseract absent) : tu peux saisir les valeurs toi-même.</p>
        )}
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      {skipped.length > 0 && (
        <div className="notice warn" role="status">
          <Icon name="alert" />
          <ul className="vault-skipped">
            {skipped.map((s, i) => (
              <li key={i}>
                <b>{s.filename}</b> : {s.reason}
                {s.document_id !== undefined && <> · <a href={`#/documents/${s.document_id}`}>voir</a></>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(toReview > 0 || expiring.length > 0) && (
        <div className="vault-alerts">
          {toReview > 0 && (
            <p className="notice"><Icon name="check" /><span>{plural(toReview, "document attend", "documents attendent")} ta vérification : un coup d'œil aux valeurs lues, puis « C'est bon ».</span></p>
          )}
          {expiring.map((d) => (
            <p key={d.id} className={`notice ${daysUntil(d.expires_on!) < 0 ? "danger" : "warn"}`}>
              <Icon name="calendar" />
              <span>
                <a href={`#/documents/${d.id}`}>{d.title}</a>{" "}
                {daysUntil(d.expires_on!) < 0 ? `a expiré le ${fullDate(d.expires_on!)}` : `expire le ${fullDate(d.expires_on!)} (dans ${plural(daysUntil(d.expires_on!), "jour", "jours")})`}
              </span>
            </p>
          ))}
        </div>
      )}

      {listing.data && total > 0 && (
        <div className="chips vault-filters" role="group" aria-label="Filtrer par type">
          <button type="button" className={`chip${kind === "" ? " is-on" : ""}`} aria-pressed={kind === ""} onClick={() => setKind("")}>
            Tout <span className="faint">{total}</span>
          </button>
          {listing.data.kinds.filter((k) => listing.data!.counts[k.key]).map((k) => (
            <button key={k.key} type="button" className={`chip${kind === k.key ? " is-on" : ""}`} aria-pressed={kind === k.key} onClick={() => setKind(k.key)}>
              {k.label} <span className="faint">{listing.data!.counts[k.key]}</span>
            </button>
          ))}
        </div>
      )}

      <MailProposals onImported={reload} />
      {listing.error && <ErrorNote message={listing.error} onRetry={listing.reload} />}
      {!listing.data && !listing.error && <Loading what="Ouverture du coffre" />}
      {listing.data && docs.length === 0 && (
        <EmptyState art="folder" title="Ton coffre est vide" className="card section vault-empty">
          <p className="muted">Commence par ce qui sert le plus : ton dernier bulletin de paie, ton avis d'impôt, une attestation CAF, un RIB. Bank en tire les chiffres pour tes aides et tes démarches.</p>
          <p className="faint">Prends un papier en photo : Bank le lit pour toi.</p>
        </EmptyState>
      )}
      {kind === "" && docs.length > 0 && <Collection docs={docs} />}
      {docs.length > 0 && (
        <ul className="vault-grid" aria-label="Documents">
          {docs.map((d) => <DocumentTile key={d.id} doc={d} />)}
        </ul>
      )}
      {listing.data && docs.length > 0 && (
        <p className="faint vault-foot"><Icon name="lock" /> {bytes(listing.data.total_bytes)} chiffrés · <ExportVault /></p>
      )}
    </>
  );
}

/** « N papiers rangés » : la collection, et l'anneau des papiers déjà vérifiés par toi. */
function Collection({ docs }: { docs: VaultSummary[] }) {
  const verified = docs.filter((d) => d.status === "verified").length;
  const left = docs.filter((d) => d.status === "to_review").length;
  const reading = docs.filter((d) => d.status === "processing").length;
  return (
    <section className="card vault-collection" aria-labelledby="collection-title">
      <Ring value={verified} max={docs.length} size="md"
            label={`${plural(verified, "papier vérifié", "papiers vérifiés")} sur ${docs.length}`}>
        {verified}<small>/{docs.length}</small>
      </Ring>
      <div>
        <h2 id="collection-title">{plural(docs.length, "papier rangé", "papiers rangés")}</h2>
        <p className="muted">
          {left > 0 ? `Encore ${plural(left, "papier", "papiers")} à vérifier d'un coup d'œil.`
            : reading > 0 ? "Bank finit de lire les derniers."
            : verified === docs.length ? "Tout est vérifié. Bravo !" : "Rien à vérifier pour l'instant."}
        </p>
      </div>
    </section>
  );
}

function DocumentTile({ doc }: { doc: VaultSummary }) {
  const [thumbOk, setThumbOk] = useState(true);
  const showThumb = thumbOk && doc.has_thumb;
  return (
    <li className={`vault-tile is-${doc.status}`}>
      <a href={`#/documents/${doc.id}`} aria-label={`${doc.title}, ${STATUS[doc.status]}`}>
        <span className="vault-thumb" aria-hidden="true">
          {showThumb && doc.status !== "processing" ? (
            <img src={`/api/vault/${doc.id}/thumb`} alt="" loading="lazy" onError={() => setThumbOk(false)} />
          ) : (
            <Icon name={doc.status === "processing" ? "spark" : "doc"} />
          )}
          {doc.sensitive && <span className="vault-lock"><Icon name="lock" /></span>}
        </span>
        <span className="vault-tile-body">
          <b>{doc.title}</b>
          <span className="faint">{doc.kind_label}{doc.document_date ? ` · ${fullDate(doc.document_date)}` : ""}</span>
          <span className={`vault-status is-${doc.status}`}>
            {doc.status === "processing" && <span className="spinner" aria-hidden="true" />}
            {STATUS[doc.status]}
            {doc.status === "to_review" && doc.warnings_count > 0 && " · à revoir"}
          </span>
        </span>
      </a>
    </li>
  );
}

function ExportVault() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const blob = await api<Blob>("/api/vault/export", { method: "POST", body: { password } });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `bank-coffre-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setPassword("");
      setOpen(false);
    });
  };
  if (!open) return <button type="button" className="btn link" onClick={() => setOpen(true)}>Exporter tout le coffre</button>;
  return (
    <form className="vault-export" onSubmit={submit}>
      <input type="password" autoComplete="current-password" placeholder="Ton mot de passe" value={password}
             onChange={(e) => setPassword(e.target.value)} aria-label="Mot de passe pour exporter" required />
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !password}><Icon name="download" /> Télécharger (.zip)</button>
      <button type="button" className="btn quiet sm" onClick={() => setOpen(false)}>Annuler</button>
      {error && <span className="error" role="alert">{error}</span>}
    </form>
  );
}

// ------------------------------------------------------------------ détail
function DocumentDetail({ id }: { id: number }) {
  const doc = useApi<VaultDetail>(`/api/vault/${id}`);
  const [busy, error, run] = useAsyncAction();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [assistNote, setAssistNote] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const toast = useToast();
  const celebrate = useCelebrate();
  const reload = doc.reload;

  useEffect(() => {
    if (doc.data?.status !== "processing") return undefined;
    const t = window.setTimeout(reload, 1500);
    return () => window.clearTimeout(t);
  }, [doc.data, reload]);

  if (doc.error) return <><BackLink /><ErrorNote message={doc.error} onRetry={doc.reload} /></>;
  if (!doc.data) return <><BackLink /><Loading what="Ouverture du document" /></>;
  const d = doc.data;

  const act = (fn: () => Promise<unknown>, done?: string, party = false) =>
    void run(async () => {
      await fn();
      if (done && party) celebrate("done", done);
      else if (done) toast(done);
      doc.reload();
    });
  const save = (key: string) =>
    act(async () => {
      await api(`/api/vault/${d.id}/fields/${key}`, { method: "PUT", body: { value: draft } });
      setEditing(null);
    });
  const assist = () =>
    void run(async () => {
      const r = await api<VaultDetail>(`/api/vault/${d.id}/assist`, { method: "POST" });
      const added = r.assist?.added.length ?? 0;
      setAssistNote(
        `${added ? plural(added, "valeur trouvée", "valeurs trouvées") : "Aucune valeur de plus"}` +
          (r.assist?.rejected ? ` · ${plural(r.assist.rejected, "proposition écartée", "propositions écartées")} (sans citation exacte du document)` : ""),
      );
      doc.reload();
    });
  const remove = () =>
    void run(async () => {
      await api(`/api/vault/${d.id}`, { method: "DELETE" });
      toast("Document supprimé.");
      window.location.hash = "#/documents";
    });

  const readable = d.status === "to_review" || d.status === "verified";
  return (
    <>
      <BackLink />
      <div className="page-head vault-head">
        <div>
          <h1>{d.title}</h1>
          <p className="muted">
            {d.kind_label}
            {d.issuer ? ` · ${d.issuer}` : ""}
            {d.document_date ? ` · ${fullDate(d.document_date)}` : ""}
          </p>
        </div>
        <span className={`vault-status is-${d.status}`}>
          {d.status === "processing" && <span className="spinner" aria-hidden="true" />}
          {STATUS[d.status]}
        </span>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {d.error && <div className={`notice ${d.status === "refused" || d.status === "failed" ? "danger" : "warn"}`} role="status"><Icon name="alert" /><span>{d.error}</span></div>}
      {d.warnings.map((w) => <div key={w} className="notice warn" role="status"><Icon name="alert" /><span>{w}</span></div>)}
      {assistNote && <div className="notice ok" role="status"><Icon name="spark" /><span>{assistNote}</span></div>}

      <div className="vault-detail">
        <aside className="vault-preview">
          {d.mime.startsWith("image/") && d.status !== "refused" ? (
            <a href={`/api/vault/${d.id}/file`} target="_blank" rel="noopener noreferrer" title="Ouvrir l'image en grand">
              <img src={`/api/vault/${d.id}/file`} alt={`Aperçu : ${d.title}`} />
            </a>
          ) : d.has_thumb ? (
            <img src={`/api/vault/${d.id}/thumb`} alt={`Aperçu : ${d.title}`} />
          ) : (
            <div className="vault-preview-empty"><Icon name="doc" size={40} /><span>{d.mime === "application/pdf" ? `PDF · ${plural(d.pages, "page", "pages")}` : "Pas d'aperçu"}</span></div>
          )}
          {d.status !== "refused" && (
            <a className="btn quiet sm" href={`/api/vault/${d.id}/file?download=1`}><Icon name="download" /> Télécharger</a>
          )}
          {d.status !== "refused" && d.status !== "processing" && (
            <button type="button" className="btn quiet sm" aria-expanded={sharing} aria-controls="vault-share" onClick={() => setSharing(true)}><Icon name="send" /> Partager</button>
          )}
          <dl className="vault-facts">
            <div><dt>Lu par</dt><dd>{METHOD[d.method] ?? d.method}{d.method !== "none" ? ` · confiance ${Math.round(d.confidence * 100)} %` : ""}</dd></div>
            <div><dt>À garder</dt><dd>{d.retention.label}{d.retention_until ? `, au moins jusqu'au ${fullDate(d.retention_until)}` : ""}. <a href={d.retention.url} target="_blank" rel="noopener noreferrer">Service-Public.fr</a> <span className="faint">(vérifié le {fullDate(d.retention.verified_on)})</span></dd></div>
            {d.expires_on && <div><dt>Expire</dt><dd>{fullDate(d.expires_on)}</dd></div>}
            <div><dt>Fichier</dt><dd>{bytes(d.size)} · déposé le {fullDate(d.created_at)}{d.source === "camera" ? " (appareil photo)" : ""}</dd></div>
          </dl>
        </aside>

        <section className="card section vault-values" aria-labelledby="values-title">
          <div className="card-head">
            <h2 id="values-title">Ce que Bank a lu</h2>
            {readable && (
              <label className="vault-kind">
                <span className="faint">Type</span>
                <select value={d.kind} disabled={busy} onChange={(e) => act(() => api(`/api/vault/${d.id}`, { method: "PATCH", body: { kind: e.target.value } }), "Type changé : document relu.")}>
                  {d.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}{k.sensitive ? " (sensible)" : ""}</option>)}
                </select>
              </label>
            )}
          </div>
          {d.fields.length === 0 && d.missing.length === 0 && <p className="muted">Pas de valeur à extraire pour ce type de document.</p>}
          <ul className="vault-fields">
            {d.fields.map((f) => (
              <FieldRow key={f.key} field={f} editing={editing === f.key} draft={draft} busy={busy}
                        onEdit={() => { setEditing(f.key); setDraft(editValue(f.type, f.value)); }}
                        onDraft={setDraft} onSave={() => save(f.key)} onCancel={() => setEditing(null)}
                        onRemove={() => act(() => api(`/api/vault/${d.id}/fields/${f.key}`, { method: "DELETE" }))} />
            ))}
            {d.missing.map((m) => (
              <li key={m.key} className="vault-field is-missing">
                <span className="vault-field-label">{m.label}</span>
                {editing === m.key ? (
                  <form className="vault-field-edit" onSubmit={(e) => { e.preventDefault(); save(m.key); }}>
                    <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus aria-label={m.label} placeholder={placeholder(m.type)} />
                    <button className="btn sm" disabled={busy || !draft.trim()}>Enregistrer</button>
                    <button type="button" className="btn quiet sm" onClick={() => setEditing(null)}>Annuler</button>
                  </form>
                ) : (
                  <>
                    <span className="vault-field-value faint">Non trouvé</span>
                    {readable && <button type="button" className="btn link" onClick={() => { setEditing(m.key); setDraft(""); }}>Ajouter</button>}
                  </>
                )}
              </li>
            ))}
          </ul>
          {readable && (
            <div className="actions vault-buttons">
              {d.status === "to_review" && (
                <button type="button" className="btn" disabled={busy} onClick={() => act(() => api(`/api/vault/${d.id}/verify`, { method: "POST" }), "Document vérifié.", true)}>
                  <Icon name="check" /> C'est bon
                </button>
              )}
              {d.official && (
                <button type="button" className="btn quiet" disabled={busy} onClick={() => act(() => api(`/api/vault/${d.id}/apply`, { method: "POST" }), "Chiffres repris dans Bank (aides, impôt).")}>
                  Utiliser ces chiffres dans Bank
                </button>
              )}
              {!d.sensitive && d.text_available && d.missing.length > 0 && (
                <button type="button" className="btn quiet" disabled={busy} onClick={assist} title="Envoie le texte (identifiants masqués) à ton modèle OpenRouter, avec ton accord">
                  <Icon name="spark" /> Lecture assistée
                </button>
              )}
            </div>
          )}
        </section>
      </div>

      {sharing && <SharePaper docId={d.id} onClose={() => setSharing(false)} />}

      <section className="card section vault-manage" aria-labelledby="manage-title">
        <h2 id="manage-title">Gérer</h2>
        <div className="vault-manage-row">
          {readable && (
            <label className="check">
              <input type="checkbox" checked={d.sensitive} disabled={busy || (d.sensitive && d.kinds.find((k) => k.key === d.kind)?.sensitive)}
                     onChange={(e) => act(() => api(`/api/vault/${d.id}`, { method: "PATCH", body: { sensitive: e.target.checked } }))} />
              Pièce sensible (jamais envoyée à un modèle)
            </label>
          )}
          {(d.status === "failed" || readable) && d.status !== "refused" && (
            <button type="button" className="btn link" disabled={busy} onClick={() => act(() => api(`/api/vault/${d.id}/reprocess`, { method: "POST" }), "Relecture lancée.")}>Relire le document</button>
          )}
          <button type="button" className="btn danger sm" disabled={busy} onClick={remove}><Icon name="trash" /> Supprimer</button>
        </div>
      </section>
    </>
  );
}

function placeholder(type: VaultFieldType): string {
  return { amount: "2 064,60", date: "30/09/2026", month: "09/2026", percent: "4,5", number: "12", text: "" }[type];
}

function FieldRow({ field, editing, draft, busy, onEdit, onDraft, onSave, onCancel, onRemove }: {
  field: VaultField;
  editing: boolean;
  draft: string;
  busy: boolean;
  onEdit: () => void;
  onDraft: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onRemove: () => void;
}) {
  const low = field.confidence < 0.7 && field.source !== "user";
  return (
    <li className={`vault-field${low ? " is-low" : ""}`}>
      <span className="vault-field-label">{field.label}</span>
      {editing ? (
        <form className="vault-field-edit" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
          <input value={draft} onChange={(e) => onDraft(e.target.value)} autoFocus aria-label={field.label} placeholder={placeholder(field.type)} />
          <button className="btn sm" disabled={busy || !draft.trim()}>Enregistrer</button>
          <button type="button" className="btn quiet sm" onClick={onCancel}>Annuler</button>
        </form>
      ) : (
        <>
          <span className="vault-field-value">{showValue(field.type, field.value)}</span>
          <span className="vault-field-tools">
            <button type="button" className="btn ghost xs" aria-label={`Corriger : ${field.label}`} title="Corriger" onClick={onEdit}><Icon name="edit" /></button>
            <button type="button" className="btn ghost xs" aria-label={`Retirer : ${field.label}`} title="Retirer (valeur fausse)" onClick={onRemove}><Icon name="trash" /></button>
          </span>
        </>
      )}
      <span className="vault-field-proof">
        {field.source === "user" ? (
          field.original !== null ? `Corrigé par toi · lu : ${showValue(field.type, field.original)}` : "Saisi par toi"
        ) : (
          <>
            {field.source === "assist" && <span className="vault-proof-tag">Lecture assistée</span>}
            {low && <span className="vault-proof-tag is-low">À vérifier</span>}
            {field.evidence ? <q>{field.evidence}</q> : <span>Lu dans le document</span>}
          </>
        )}
      </span>
    </li>
  );
}

function BackLink() {
  return <a className="back-link" href="#/documents"><Icon name="back" /> Documents</a>;
}
