// Partager un papier : un lien qui ne sert qu'une fois, 48 h au plus, avec ton mot de passe. Le destinataire reçoit
// un PDF marqué à son nom ; le lien ne dit rien du document. Le journal montre qui l'a reçu, et révoque.

import { useRef, useState, type FormEvent } from "react";
import { api, useApi, type VaultShare, type VaultShareState } from "../api";
import { Icon } from "./Icon";
import { useAsyncAction, useToast } from "./ui";

const DURATIONS: { hours: number; label: string }[] = [
  { hours: 1, label: "1 heure" },
  { hours: 24, label: "24 heures" },
  { hours: 48, label: "48 heures" },
];
const STATE: Record<VaultShareState, string> = {
  active: "En attente",
  used: "Téléchargé",
  revoked: "Révoqué",
  expired: "Expiré",
};

/** « 15 oct. à 14:05 » : l'heure compte pour un lien qui vit quelques heures. */
export function when(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }).replace(/\.$/, "");
  return `${day} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
}

export function SharePaper({ docId, onClose }: { docId: number; onClose: () => void }) {
  const journal = useApi<{ shares: VaultShare[] }>(`/api/vault/${docId}/shares`);
  const [recipient, setRecipient] = useState("");
  const [hours, setHours] = useState(48);
  const [password, setPassword] = useState("");
  const [created, setCreated] = useState<{ link: string; share: VaultShare } | null>(null);
  const [busy, error, run] = useAsyncAction();
  const linkRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<{ link: string; share: VaultShare }>(`/api/vault/${docId}/shares`, {
        method: "POST",
        body: { recipient, hours, password },
      });
      setCreated(r);
      setPassword("");
      setRecipient("");
      journal.reload();
    });
  };
  const copy = () => {
    if (!created) return;
    void navigator.clipboard?.writeText(created.link).then(
      () => toast("Lien copié."),
      () => { linkRef.current?.select(); toast("Copie impossible : sélectionne le lien à la main."); },
    );
  };
  const revoke = (s: VaultShare) =>
    void run(async () => {
      await api(`/api/vault/${docId}/shares/${s.id}/revoke`, { method: "POST" });
      if (created?.share.id === s.id) setCreated(null);
      toast(`Lien de ${s.recipient} révoqué.`);
      journal.reload();
    });

  const shares = journal.data?.shares ?? [];
  return (
    <section className="card section vault-share" aria-labelledby="share-title">
      <div className="card-head">
        <h2 id="share-title"><Icon name="send" /> Partager ce papier</h2>
        <button type="button" className="btn ghost xs" aria-label="Fermer le partage" onClick={onClose}><Icon name="close" /></button>
      </div>
      <p className="muted">
        Un lien qui ne sert qu'une fois, puis plus rien. Le destinataire reçoit un PDF marqué « Transmis à … par Bank —
        usage unique » ; le lien ne dit rien du document.
      </p>

      {created && (
        <div className="notice ok vault-share-link" role="status">
          <Icon name="check" />
          <div>
            <p><b>Lien pour {created.share.recipient}</b> — affiché une seule fois, valable jusqu'au {when(created.share.expires_at)}.</p>
            <div className="input-with-action">
              <input ref={linkRef} readOnly value={created.link} aria-label="Lien de partage" onFocus={(e) => e.target.select()} />
              <button type="button" className="btn sm" onClick={copy}><Icon name="copy" /> Copier</button>
            </div>
            <p className="faint">Envoie-le par le canal de ton choix. Bank n'en garde qu'une empreinte : impossible de le réafficher.</p>
          </div>
        </div>
      )}

      <form className="form-grid vault-share-form" onSubmit={submit} aria-label="Créer un lien de partage">
        <label className="field">
          Destinataire
          <input value={recipient} onChange={(e) => setRecipient(e.target.value)} required minLength={2} maxLength={60}
                 placeholder="Agence Dupont, Mme Martin (CAF)…" autoComplete="off" />
        </label>
        <label className="field">
          Valable
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {DURATIONS.map((d) => <option key={d.hours} value={d.hours}>{d.label}</option>)}
          </select>
        </label>
        <label className="field">
          Ton mot de passe
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || recipient.trim().length < 2 || !password}>
          <Icon name="send" /> Créer le lien
        </button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}

      {shares.length > 0 && (
        <>
          <h3 className="vault-share-title">Déjà transmis</h3>
          <ul className="vault-share-journal" aria-label="Journal des partages">
            {shares.map((s) => (
              <li key={s.id} className={`is-${s.state}`}>
                <span><b>{s.recipient}</b> <span className="faint">· créé le {when(s.created_at)}</span></span>
                <span className={`vault-share-state is-${s.state}`}>
                  {STATE[s.state]}
                  {s.state === "used" && s.used_at ? ` le ${when(s.used_at)}` : ""}
                  {s.state === "active" ? ` jusqu'au ${when(s.expires_at)}` : ""}
                </span>
                {s.state === "active" && (
                  <button type="button" className="btn link" disabled={busy} onClick={() => revoke(s)}>Révoquer</button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
