// Powens (sur contrat) : même parcours que l'assistant Enable Banking — ton contrat, l'adresse de retour, tes
// identifiants (chiffrés côté serveur, plus jamais affichés), la vérification, puis tes banques via la webview Powens.
// Bank ne voit jamais tes identifiants bancaires : tu les saisis dans la page de Powens.

import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type PowensConnection, type PowensIssue, type PowensStatus } from "../api";
import { shortDate } from "../format";
import { BankMark } from "./BankConnect";
import { Icon } from "./Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useCelebrate, useToast } from "./ui";

export const POWENS_STEPS = ["Ton contrat", "Adresse de retour", "Identifiants", "Vérification", "Tes banques"];
const BLOCKING = new Set<PowensIssue["code"]>(["bad_key", "unreachable"]);
const LABELS: Record<PowensConnection["status"], string> = {
  active: "active", expired: "à reconnecter", revoked: "coupée", error: "erreur", pending: "en attente",
};
export const SANDBOX_NOTE = "Bac à sable : banque de démonstration seulement.";

/** L'étape où en est Powens (0 à 4) ; avant les identifiants, c'est toi qui avances. */
export function powensStep(st: PowensStatus | null): number {
  if (!st || !st.configured) return 0;
  if (!st.check || st.check.issues.some((i) => BLOCKING.has(i.code))) return 3;
  return 4;
}

/** Ouvre une adresse de la webview Powens, et rien d'autre. */
function openWebview(raw: string) {
  const url = new URL(raw);
  if (url.protocol !== "https:" || url.hostname !== "webview.powens.com") throw new Error("Adresse de redirection refusée");
  navigation.go(url.toString());
}

/** La navigation vers la webview (remplaçable en test : jsdom ne navigue pas). */
export const navigation = { go: (url: string) => window.location.assign(url) };

export function PowensSetup({ result = null }: { result?: string | null }) {
  const st = useApi<PowensStatus>("/api/banks/powens");
  const [open, setOpen] = useState(false);
  const [manual, setManual] = useState<number | null>(null);
  const [current, setCurrent] = useState<PowensStatus | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const celebrate = useCelebrate();

  const data = current ?? st.data;
  // Retour de la webview Powens : son message, et la section Powens sous les yeux.
  useEffect(() => {
    if (!result) return;
    if (result === "connected") celebrate("done", "Banque connectée via Powens : clique Synchroniser pour récupérer tes opérations.");
    if (result === "refused") toast("Connexion annulée dans la page de Powens.");
    if (result === "error") toast("La connexion Powens a échoué ou a expiré : recommence.");
  }, [result, toast, celebrate]);
  const loaded = !!data;
  useEffect(() => {
    if (result && loaded) document.getElementById("powens-title")?.scrollIntoView?.({ block: "start" });
  }, [result, loaded]);
  const show = (next: PowensStatus) => { setCurrent(next); setManual(null); };
  const check = () => void run(async () => show(await api<PowensStatus>("/api/banks/powens/setup/check", { method: "POST" })));
  const connect = (path: string) =>
    void run(async () => {
      const r = await api<{ redirect_url: string }>(path, { method: "POST" });
      openWebview(r.redirect_url);
    });
  const sync = (id: number) =>
    void run(async () => {
      const r = await api<{ inserted: number; reconciled: number; errors: string[] }>(`/api/banks/powens/${id}/sync`, { method: "POST" });
      toast(r.errors.length ? r.errors.join(" ") : `${r.inserted} nouvelles opérations, ${r.reconciled} rapprochées.`);
      show(await api<PowensStatus>("/api/banks/powens"));
    });
  const revoke = (id: number) =>
    void run(async () => {
      if (!window.confirm("Couper cette banque ? Powens efface ses données de son côté ; l'historique déjà importé reste dans Bank.")) return;
      await api(`/api/banks/powens/${id}`, { method: "DELETE" });
      show(await api<PowensStatus>("/api/banks/powens"));
    });

  const head = (
    <div className="section-head" style={{ marginTop: "1.5rem" }}>
      <h3 id="powens-title">Powens <span className="tag">sur contrat</span></h3>
      {data && !data.configured && (
        <button type="button" className="btn ghost sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <Icon name={open ? "close" : "lock"} /> {open ? "Fermer" : "Configurer Powens"}
        </button>
      )}
    </div>
  );

  if (st.error) return <div role="group" aria-labelledby="powens-title">{head}<ErrorNote message={st.error} onRetry={st.reload} /></div>;
  if (!data) return <Loading what="Powens" />;

  const note = (
    <p className="section-note">
      Agrégateur professionnel : comptes courants, livrets, assurance-vie, PEA, PER et crédits. Il ne s'active qu'avec un
      contrat Powens ; sans contrat, Enable Banking ci-dessus suffit.
    </p>
  );
  if (!data.configured && !open) return <div role="group" aria-labelledby="powens-title">{head}{note}</div>;

  const step = manual ?? powensStep(data);
  const copy = () =>
    void navigator.clipboard?.writeText(data.redirect_url).then(
      () => toast("Adresse copiée : ajoute-la aux adresses de retour de ton application Powens."),
      () => toast("Copie impossible : sélectionne l'adresse à la main."),
    );
  const issues = data.check?.issues.filter((i) => BLOCKING.has(i.code)) ?? [];

  return (
    <div role="group" aria-labelledby="powens-title">
      {head}
      {note}
      <div className="wizard" aria-label="Assistant de connexion Powens">
        <ol className="wizard-steps" aria-label={`Étape ${step + 1} sur ${POWENS_STEPS.length}`}>
          {POWENS_STEPS.map((label, i) => (
            <li key={label} className={i < step ? "is-done" : i === step ? "is-active" : ""} aria-current={i === step ? "step" : undefined}>
              <span aria-hidden="true">{i < step ? <Icon name="check" /> : i + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        <div className="wizard-panel" key={step}>
          {step === 0 && (
            <>
              <h3>1. Ton contrat Powens</h3>
              <p className="muted">
                Powens ouvre son API de production aux clients sous contrat. Le bac à sable est gratuit, mais ne montre que la
                banque de démonstration : pratique pour essayer le parcours, pas pour tes vrais comptes.
              </p>
              <ol className="steps">
                <li>Ouvre la <a href={data.console_url} target="_blank" rel="noreferrer noopener">console Powens <Icon name="external" /></a>.</li>
                <li>Repère ton <b>domaine</b> (« monappli », ou « monappli-sandbox » pour le bac à sable).</li>
                <li>Dans <b>Client applications</b>, crée une application : note son <b>client_id</b> et son <b>client_secret</b>.</li>
              </ol>
              <div className="actions">
                <button type="button" className="btn" onClick={() => setManual(1)}>J'ai mes identifiants <Icon name="arrow" /></button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h3>2. Ajoute l'adresse de retour</h3>
              <p className="muted">
                Dans ton application Powens, ajoute exactement cette adresse aux adresses de retour autorisées : la page de
                Powens t'y renverra après le choix de ta banque.
              </p>
              <div className="copy-field">
                <code>{data.redirect_url}</code>
                <button type="button" className="btn quiet sm" onClick={copy}><Icon name="copy" /> Copier</button>
              </div>
              <p className="faint">Bank ne peut pas vérifier cette adresse à ta place : en cas d'oubli, Powens refusera le retour.</p>
              <div className="actions">
                <button type="button" className="btn ghost" onClick={() => setManual(0)}><Icon name="back" /> Retour</button>
                <button type="button" className="btn" onClick={() => setManual(2)}>C'est fait <Icon name="arrow" /></button>
              </div>
            </>
          )}

          {step === 2 && (
            <CredentialsForm
              onBack={() => setManual(data.configured ? null : 1)}
              onSaved={(next) => {
                show(next);
                toast(next.check?.ok ? "Powens vérifié : connecte ta banque." : "Identifiants enregistrés. Il reste une chose à régler.");
              }}
            />
          )}

          {step === 3 && (
            <>
              <h3>4. Bank vérifie</h3>
              {!data.check || busy ? (
                <p className="muted" aria-live="polite"><span className="spinner" /> Bank interroge Powens…</p>
              ) : (
                <ul className="wizard-issues">
                  {issues.map((i) => (
                    <li key={i.code} className={`notice ${i.code === "unreachable" ? "warn" : "danger"}`} role="alert">
                      <Icon name="alert" /><span>{i.message}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="actions">
                <button type="button" className="btn ghost" onClick={() => setManual(2)}>Changer d'identifiants</button>
                <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={check}>Vérifier à nouveau</button>
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <h3>5. Tes banques</h3>
              <p className="muted">
                <Icon name="check" /> Powens vérifié. Tu choisis ta banque et t'authentifies dans la page de Powens, puis tu
                reviens ici. Coche les comptes à suivre : Powens laisse les autres de côté.
              </p>
              {data.connections.length > 0 && (
                <ul className="connected" aria-label="Banques connectées par Powens">
                  {data.connections.map((c) => (
                    <li key={c.id}>
                      <BankMark name={c.aspsp_name} />
                      <div>
                        <b>{c.aspsp_name}</b>
                        <div className="faint">
                          {c.accounts.join(", ") || "comptes à venir"} ·{" "}
                          <span className={`tag ${c.status === "active" ? "ok" : c.status === "expired" ? "warn" : ""}`}>{LABELS[c.status] ?? c.status}</span>
                          {c.last_sync_at ? ` · synchro ${shortDate(c.last_sync_at)}` : " · jamais synchronisée"}
                        </div>
                        {c.last_error && <div className="error">{c.last_error}</div>}
                      </div>
                      {(c.status === "active" || c.status === "expired" || c.status === "error") && (
                        <span className="actions" style={{ marginTop: 0 }}>
                          {c.status === "active" ? (
                            <button className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => sync(c.id)}>Synchroniser</button>
                          ) : c.status === "expired" ? (
                            <button className="btn sm" disabled={busy} onClick={() => connect(`/api/banks/powens/${c.id}/reconnect`)}>Reconnecter</button>
                          ) : null}
                          <button className="btn ghost sm" disabled={busy} onClick={() => revoke(c.id)}>Couper</button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <div className="actions">
                <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => connect("/api/banks/powens/connect")}>
                  <Icon name="plus" /> Connecter une banque via Powens
                </button>
              </div>
            </>
          )}

          {data.sandbox && <p className="notice warn"><Icon name="alert" /><span>{SANDBOX_NOTE} Pour tes vrais comptes, il faut le domaine de production.</span></p>}
          {error && <ErrorNote message={error} />}
        </div>

        {data.configured && <Summary st={data} onChange={() => setManual(2)} onRemoved={(next) => { show(next); setOpen(false); }} />}
      </div>
    </div>
  );
}

function CredentialsForm({ onBack, onSaved }: { onBack: () => void; onSaved: (st: PowensStatus) => void }) {
  const [domain, setDomain] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const next = await api<PowensStatus>("/api/banks/powens/setup", {
        method: "PUT",
        body: { password, domain: domain.trim(), client_id: clientId.trim(), client_secret: secret.trim() },
      });
      setSecret(""); setPassword("");
      onSaved(next);
    });
  };
  return (
    <form className="stack" onSubmit={submit} aria-label="Identifiants Powens">
      <h3>3. Donne tes identifiants à Bank</h3>
      <p className="muted">Le secret est chiffré dans Bank et ne sera plus jamais affiché. Bank ne l'envoie qu'à Powens.</p>
      <Field label="Domaine" hint="« monappli » ou « monappli-sandbox », sans .biapi.pro">
        <input value={domain} required maxLength={120} autoComplete="off" spellCheck={false} placeholder="monappli-sandbox" onChange={(e) => setDomain(e.target.value)} />
      </Field>
      <Field label="Client ID">
        <input value={clientId} required maxLength={80} autoComplete="off" spellCheck={false} onChange={(e) => setClientId(e.target.value)} />
      </Field>
      <Field label="Client secret">
        <input type="password" value={secret} required maxLength={300} autoComplete="off" spellCheck={false} onChange={(e) => setSecret(e.target.value)} />
      </Field>
      <Field label="Ton mot de passe Bank" hint="exigé pour garder des identifiants">
        <input type="password" value={password} required autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button type="button" className="btn ghost" onClick={onBack}><Icon name="back" /> Retour</button>
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="shield" /> Enregistrer et vérifier</button>
      </div>
    </form>
  );
}

function Summary({ st, onChange, onRemoved }: { st: PowensStatus; onChange: () => void; onRemoved: (st: PowensStatus) => void }) {
  const [removing, setRemoving] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const remove = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const next = await api<PowensStatus>("/api/banks/powens/setup", { method: "DELETE", body: { password } });
      setPassword(""); setRemoving(false);
      toast("Powens retiré : Bank ne le contacte plus.");
      onRemoved(next);
    });
  };
  const live = st.connections.filter((c) => c.status === "active" || c.status === "expired").length;
  return (
    <div className="wizard-summary">
      <p className="faint">
        <Icon name="lock" /> Domaine <b>{st.domain}</b> · client <b>{st.client_id_hint ?? "?"}</b>
        {st.secret_fingerprint && <> · empreinte du secret <code>{st.secret_fingerprint}</code></>}
      </p>
      {!removing && (
        <span className="actions" style={{ marginTop: 0 }}>
          <button type="button" className="btn quiet sm" onClick={onChange}>Changer d'identifiants</button>
          <button type="button" className="btn ghost sm" onClick={() => setRemoving(true)}>Retirer</button>
        </span>
      )}
      {removing && (
        <form className="stack" onSubmit={remove} aria-label="Retirer Powens">
          <p className="muted">
            Bank efface ton utilisateur chez Powens et oublie les identifiants{live ? ` (${live} banque${live > 1 ? "s" : ""} reliée${live > 1 ? "s" : ""})` : ""}.
            L'historique déjà importé reste.
          </p>
          <Field label="Ton mot de passe Bank"><input type="password" value={password} required autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <ErrorNote message={error} />}
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="btn ghost sm" onClick={() => setRemoving(false)}>Annuler</button>
            <button className={`btn danger sm${busy ? " is-loading" : ""}`} disabled={busy}>Retirer Powens</button>
          </span>
        </form>
      )}
    </div>
  );
}
