// Boîte mail en lecture seule : relier une boîte (mot de passe d'application, un dossier, liste d'expéditeurs),
// donner ou retirer l'accord, tester, lire maintenant. Les PDF repérés sont proposés dans Papiers ; rien n'entre
// dans le coffre sans ton geste, Bank ne marque, ne déplace ni n'efface aucun message.

import { useState, type FormEvent } from "react";
import { api, useApi, type MailStatus } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";

export function MailSettings() {
  const st = useApi<MailStatus>("/api/mail");
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  const [busy, error, run] = useAsyncAction();

  const test = () =>
    void run(async () => {
      const r = await api<{ ok: boolean; messages: number }>("/api/mail/test", { method: "POST" });
      toast(`La boîte répond : ${r.messages} message(s) dans le dossier.`);
      st.reload();
    });
  const scan = () =>
    void run(async () => {
      const r = await api<MailStatus & { report: { scanned: number; proposed: number; skipped: number; notes: string[] } }>("/api/mail/scan", { method: "POST" });
      toast(`${r.report.scanned} message(s) lus, ${r.report.proposed} PDF proposé(s) dans Papiers.`);
      st.reload();
    });
  const remove = () =>
    void run(async () => {
      if (!window.confirm("Retirer la boîte mail ? Les propositions en attente disparaissent ; les papiers déjà importés restent.")) return;
      await api("/api/mail", { method: "DELETE" });
      toast("Boîte mail retirée.");
      st.reload();
    });
  const consent = (granted: boolean) =>
    void run(async () => {
      await api("/api/consents/mail", { method: "PUT", body: { granted } });
      st.reload();
    });

  const s = st.data;
  const a = s?.account;
  return (
    <section className="section" aria-labelledby="mail-title" id="mail">
      <div className="section-head">
        <h2 id="mail-title"><Icon name="doc" /> Boîte mail, en lecture seule</h2>
        {a && !editing && (
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={test}>Tester</button>
            <button type="button" className="btn quiet sm" disabled={busy || !s?.consent} onClick={scan}>Lire maintenant</button>
            <button type="button" className="btn quiet sm" onClick={() => setEditing(true)}><Icon name="settings" /> Modifier</button>
          </span>
        )}
      </div>
      <p className="section-note">
        Bank ouvre un seul dossier de ta boîte en lecture seule, chaque heure, et repère les PDF joints qui ressemblent à
        des papiers (facture, attestation, avis, quittance, bulletin). Il te les propose dans Papiers avec l'expéditeur
        et l'objet ; tu importes ou tu écartes. Il ne lit pas le texte de tes mails, n'en marque, n'en déplace ni n'en
        efface aucun. Utilise un <b>mot de passe d'application</b> (jamais ton mot de passe principal) : il est chiffré
        ici et jamais réaffiché.
      </p>
      {st.error && <ErrorNote message={st.error} onRetry={st.reload} />}
      {!s && !st.error && <Loading what="Boîte mail" />}
      {s && !a && !editing && (
        <div className="card">
          <p><b>Aucune boîte reliée.</b> Les factures et attestations qui arrivent par mail restent à déposer à la main.</p>
          <div className="actions"><button type="button" className="btn" onClick={() => setEditing(true)}><Icon name="plus" /> Relier ma boîte mail</button></div>
        </div>
      )}
      {s && a && !editing && (
        <div className="card">
          <dl className="ch-kv">
            <dt>Boîte</dt>
            <dd>{a.username_hint} · {a.host}:{a.port} ({a.security === "ssl" ? "IMAPS" : "STARTTLS"}) · dossier « {a.folder} »</dd>
            <dt>Expéditeurs</dt>
            <dd>{a.senders.length ? a.senders.join(", ") : "tous (les PDF seulement)"}</dd>
            <dt>Accord</dt>
            <dd>
              {s.consent ? "donné" : "pas donné : Bank ne lit rien"}{" "}
              <button type="button" className="btn link" disabled={busy} onClick={() => consent(!s.consent)}>{s.consent ? "Retirer" : "Donner mon accord"}</button>
            </dd>
            <dt>Dernière lecture</dt>
            <dd>{a.last_sync_at ? new Date(a.last_sync_at).toLocaleString("fr-FR") : "jamais"}{a.status === "error" ? " — en erreur" : ""}</dd>
            {a.last_error && <><dt>Dernière erreur</dt><dd className="error">{a.last_error}</dd></>}
            <dt>En attente dans Papiers</dt>
            <dd>{s.proposals.length} PDF proposé(s){s.proposals.length ? <> · <a href="#/documents">voir</a></> : null}</dd>
          </dl>
          <div className="actions">
            <button type="button" className="btn link" disabled={busy} onClick={remove}><Icon name="trash" /> Retirer la boîte</button>
          </div>
        </div>
      )}
      {s && editing && <MailForm status={s} onDone={() => { setEditing(false); st.reload(); }} onCancel={() => setEditing(false)} />}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function MailForm({ status: s, onDone, onCancel }: { status: MailStatus; onDone: () => void; onCancel: () => void }) {
  const a = s.account;
  const [host, setHost] = useState(a?.host ?? "");
  const [port, setPort] = useState(a?.port ?? 993);
  const [security, setSecurity] = useState<"ssl" | "starttls">(a?.security ?? "ssl");
  const [username, setUsername] = useState("");
  const [mailPassword, setMailPassword] = useState("");
  const [folder, setFolder] = useState(a?.folder ?? "INBOX");
  const [senders, setSenders] = useState((a?.senders ?? []).join("\n"));
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/mail", {
        method: "PUT",
        body: {
          password, host, port, security, username, mail_password: mailPassword, folder,
          senders: senders.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean),
        },
      });
      toast("Boîte reliée : l'essai de connexion a réussi.");
      onDone();
    });
  };
  return (
    <form className="card form-grid" onSubmit={submit} aria-label="Relier une boîte mail">
      <Field label="Serveur IMAP" hint="Exemple : imap.free.fr, imap.gmail.com, mail.infomaniak.com — le nom seul.">
        <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="imap.exemple.fr" required maxLength={253} autoComplete="off" />
      </Field>
      <Field label="Port et sécurité" hint="993 avec IMAPS dans la plupart des cas ; 143 avec STARTTLS sinon. Jamais sans chiffrement.">
        <span className="actions" style={{ marginTop: 0 }}>
          <input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(Number(e.target.value))} required style={{ maxWidth: "7rem" }} aria-label="Port" />
          <select value={security} onChange={(e) => { const v = e.target.value as "ssl" | "starttls"; setSecurity(v); setPort(v === "ssl" ? 993 : 143); }} aria-label="Sécurité">
            <option value="ssl">IMAPS (TLS direct)</option>
            <option value="starttls">STARTTLS</option>
          </select>
        </span>
      </Field>
      <Field label="Identifiant" hint="Souvent ton adresse complète.">
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="toi@exemple.fr" required maxLength={200} autoComplete="off" />
      </Field>
      <Field label="Mot de passe d'application" hint="Généré chez ton fournisseur de mail pour Bank seulement ; révocable là-bas. Chiffré ici, jamais réaffiché.">
        <input type="password" value={mailPassword} onChange={(e) => setMailPassword(e.target.value)} required maxLength={500} autoComplete="off" />
      </Field>
      <Field label="Dossier" hint="INBOX, ou un dossier où tu ranges tes factures : Bank ne lira que celui-là.">
        <input value={folder} onChange={(e) => setFolder(e.target.value)} required maxLength={120} autoComplete="off" />
      </Field>
      <Field label="Expéditeurs retenus" hint="Un par ligne : une adresse ou un domaine (caf.fr, francetravail.fr, free-mobile.fr). Vide : tous les PDF.">
        <textarea value={senders} onChange={(e) => setSenders(e.target.value)} rows={3} maxLength={2000} placeholder={"caf.fr\nfrancetravail.fr"} />
      </Field>
      <Field label="Ton mot de passe Bank" hint="Une boîte mail donne accès à beaucoup : on confirme que c'est bien toi.">
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
      </Field>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Essayer et enregistrer</button>
        <button type="button" className="btn quiet" onClick={onCancel}>Annuler</button>
      </div>
    </form>
  );
}
