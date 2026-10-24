// Réglages : comptes, budget, apparence, sécurité, journal. Chaque bloc est une carte ; les formulaires d'ajout
// sont repliés tant qu'on n'en a pas besoin.

import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type Account, type AccountKind, type BackupRun, type BackupStatus, type BudgetResponse, type ConsentView, type Me } from "../api";
import { labelAction } from "../audit-labels";
import { Icon } from "../components/Icon";
import { SectionNav } from "../components/SectionNav";
import { Segmented } from "../components/Segmented";
import { EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, shortDate } from "../format";
import { applyTheme, readTheme, type ThemePref } from "../theme";
import { disablePush, enablePush, pushPermission, pushSupported, thisDeviceId, type PushStatus } from "../push";
import { registerPasskey, supportsPasskeys, type PasskeyView } from "../webauthn";

export const KIND_LABELS: Record<AccountKind, string> = {
  checking: "Compte courant",
  savings: "Épargne",
  livret_a: "Livret A",
  ldds: "LDDS",
  lep: "LEP",
  cel: "CEL",
  pel: "PEL",
  pea: "PEA",
  assurance_vie: "Assurance vie",
  per: "PER",
  crypto: "Crypto",
  credit: "Crédit",
  other: "Autre",
};

const SETTINGS_SECTIONS = [
  { id: "acc-title", label: "Comptes" },
  { id: "bud-title", label: "Budget" },
  { id: "look-title", label: "Apparence" },
  { id: "push-title", label: "Notifications" },
  { id: "backup-title", label: "Sauvegardes" },
  { id: "privacy-title", label: "Confidentialité" },
  { id: "sec-title", label: "Sécurité" },
  { id: "audit-title", label: "Journal" },
];

export function Settings({ me, onMeChange }: { me: Me; onMeChange: () => void }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Réglages</h1>
          <p className="muted">Banques, compteur Linky, documents de l'État et agents IA : voir <a href="#/connecteurs">Connecteurs</a>.</p>
        </div>
      </div>
      <SectionNav items={SETTINGS_SECTIONS} />
      <Accounts />
      <BudgetSettings />
      <Appearance />
      <Notifications />
      <Backups />
      <Privacy />
      <Security me={me} onChange={onMeChange} />
      <Audit />
    </>
  );
}

// ------------------------------------------------------------------ sauvegardes
const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const LEVEL_TEXT: Record<BackupStatus["level"], string> = {
  ok: "Sauvegardée et relue : tu peux dormir tranquille.",
  unverified: "Sauvegardée, mais la restauration d'essai n'a pas encore tourné.",
  stale: "Aucune sauvegarde réussie depuis deux jours.",
  none: "Aucune sauvegarde pour l'instant.",
  failed: "Le dernier passage a échoué : regarde le détail.",
};

export function bytesText(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1).replace(".", ",")} Go`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(".", ",")} Mo`;
  if (n >= 1_000) return `${Math.round(n / 1_000)} ko`;
  return `${n} o`;
}

function whenText(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" })} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
}

function RunLine({ run, label }: { run: BackupRun | null; label: string }) {
  if (!run) return <div><dt>{label}</dt><dd className="faint">jamais</dd></div>;
  const d = run.details;
  const what = run.kind === "backup"
    ? run.status === "ok" ? `${d.file ?? ""}${d.cipher_bytes ? ` · ${bytesText(d.cipher_bytes)}` : ""}${d.copies && d.copies.length ? " · copiée sur le second emplacement" : ""}` : d.error ?? "échec"
    : run.status === "ok" ? `${Object.values(d.tables ?? {}).reduce((a, b) => a + b, 0)} lignes relues, tout concorde` : (d.problems ?? [d.error ?? "échec"]).join(" ; ");
  return (
    <div>
      <dt>{label}</dt>
      <dd className={run.status === "ok" ? "is-positive" : "is-negative"}>
        <Icon name={run.status === "ok" ? "check" : "alert"} size={14} /> {whenText(run.started_at)} <span className="faint">({run.trigger === "manual" ? "à la main" : "automatique"})</span>
        <br /><small className="faint">{what}</small>
      </dd>
    </div>
  );
}

export function Backups() {
  const st = useApi<BackupStatus>("/api/system/backups");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const act = (path: string, done: string) =>
    void run(async () => {
      const out = await api<BackupStatus>(path, { method: "POST" });
      toast(out.run?.status === "ok" ? done : "Échec : regarde le détail ci-dessous.");
      st.reload();
    });
  const data = st.data;
  return (
    <section className="card section backups" aria-labelledby="backup-title" data-level={data?.level ?? "none"}>
      <div className="card-head">
        <h2 id="backup-title"><Icon name="vault" /> Sauvegardes</h2>
        {data && (
          <div className="backup-actions">
            <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy || !data.enabled} onClick={() => act("/api/system/backups/run", "Sauvegarde faite et chiffrée.")}>Sauvegarder maintenant</button>
            <button type="button" className="btn sm secondary" disabled={busy || data.files.length === 0} onClick={() => act("/api/system/backups/verify", "Restauration d'essai réussie : la sauvegarde se relit.")}>Tester la restauration</button>
          </div>
        )}
      </div>
      <p className="muted">
        Chaque nuit, Bank fait une copie cohérente de sa base, la chiffre avec une clé dérivée de ta clé maîtresse
        et la range sur le serveur{data?.remote_directory ? " et sur le second emplacement" : ""} ; chaque semaine, il
        la déchiffre dans un dossier jetable, vérifie l'intégrité et recompte les lignes. Un échec te prévient.
      </p>
      {st.error && <p className="error" role="alert">{st.error}</p>}
      {data && (
        <>
          <p className={`notice ${data.level === "ok" ? "ok" : data.level === "failed" || data.level === "stale" ? "danger" : "warn"}`} role="status">
            <Icon name={data.level === "ok" ? "check" : "alert"} /><span>{LEVEL_TEXT[data.level]}{data.enabled ? ` Prochaine sauvegarde automatique : ${whenText(data.next_backup)}.` : " Sauvegardes automatiques désactivées (BANK_BACKUP_ENABLED)."}</span>
          </p>
          <dl className="backup-stats">
            <RunLine run={data.last_backup} label="Dernière sauvegarde" />
            <RunLine run={data.last_verify} label="Dernière restauration d'essai" />
            <div><dt>Où</dt><dd>{data.directory}{data.remote_directory ? <><br /><small className={data.remote_reachable ? "faint" : "is-negative"}>{data.remote_directory}{data.remote_reachable ? "" : " — injoignable"}</small></> : <><br /><small className="faint">Pas de second emplacement : règle BANK_BACKUP_REMOTE_DIR (disque USB, NAS monté).</small></>}</dd></div>
            <div><dt>Rythme</dt><dd>chaque jour à {data.hour} h, {data.keep} copies gardées, restauration d'essai le {WEEKDAYS[data.verify_weekday]}</dd></div>
          </dl>
          {data.files.length > 0 && (
            <details className="backup-files">
              <summary>{data.files.length} {data.files.length > 1 ? "fichiers" : "fichier"} sur le serveur</summary>
              <ul>
                {data.files.map((f) => <li key={f.name}><code>{f.name}</code> <span className="faint">{bytesText(f.bytes)} · {whenText(f.modified_at)}{f.manifest ? "" : " · sans manifeste"}</span></li>)}
              </ul>
            </details>
          )}
          {data.runs.length > 0 && (
            <details className="backup-files">
              <summary>Historique des passages</summary>
              <ul>
                {data.runs.map((r) => <li key={r.id}><Icon name={r.status === "ok" ? "check" : "alert"} size={12} /> {whenText(r.started_at)} · {r.kind === "backup" ? "sauvegarde" : "restauration d'essai"} · {r.status === "ok" ? "réussie" : `échec : ${r.details.error ?? (r.details.problems ?? []).join(" ; ")}`}</li>)}
              </ul>
            </details>
          )}
          <p className="faint">{data.restore_hint}</p>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ notifications sur ce téléphone
export function Notifications() {
  const st = useApi<PushStatus>("/api/push");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const supported = pushSupported();
  const permission = pushPermission();
  const mine = thisDeviceId();
  const here = st.data?.devices.find((d) => d.id === mine) ?? null;

  const activate = () =>
    void run(async () => {
      if (!st.data) return;
      if (!st.data.consent) await api("/api/consents/push", { method: "PUT", body: { granted: true } });
      const device = await enablePush(st.data.public_key);
      toast(`Notifications activées sur « ${device.label} ».`);
      st.reload();
    });
  const remove = (id: number) =>
    void run(async () => {
      await disablePush(id);
      toast("Appareil retiré.");
      st.reload();
    });
  const probe = (id: number) =>
    void run(async () => {
      await api(`/api/push/${id}/test`, { method: "POST" });
      toast("Notification d'essai envoyée : regarde ton écran.");
      st.reload();
    });

  return (
    <section className="card section" aria-labelledby="push-title">
      <div className="card-head">
        <h2 id="push-title"><Icon name="bell" /> Notifications sur ce téléphone</h2>
      </div>
      <p className="muted">
        Bank te prévient ici même, sans messagerie : découvert qui approche, déclaration à faire, gain à confirmer,
        pilote arrêté. Chaque message est chiffré pour ton appareil ; le service de notification du navigateur
        (Google, Apple, Mozilla) ne voit qu'un contenu illisible. Les heures calmes s'appliquent.
      </p>
      {st.error && <p className="error" role="alert">{st.error}</p>}
      {!supported && <p className="notice"><Icon name="alert" /> Ce navigateur ne sait pas recevoir de notifications. Sur iPhone : ajoute Bank à l'écran d'accueil (Partager → Sur l'écran d'accueil), puis reviens ici.</p>}
      {supported && permission === "denied" && <p className="notice"><Icon name="alert" /> Les notifications sont bloquées par le navigateur pour Bank : réautorise-les dans ses réglages de site.</p>}
      {st.data && (
        <>
          {here ? (
            <p><Icon name="check" /> <b>Activées sur cet appareil</b> ({here.label}){here.last_notified_at ? ` · dernière le ${shortDate(here.last_notified_at)}` : ""}</p>
          ) : (
            supported && permission !== "denied" && (
              <div className="actions">
                <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={activate}>
                  <Icon name="bell" /> Activer sur cet appareil
                </button>
              </div>
            )
          )}
          {st.data.devices.length > 0 && (
            <ul className="pk-list" aria-label="Appareils prévenus">
              {st.data.devices.map((d) => (
                <li key={d.id}>
                  <span>
                    <b>{d.label}</b>{d.id === mine ? " (cet appareil)" : ""}
                    <span className="faint"> · depuis le {shortDate(d.linked_at)}{d.last_error ? ` · ${d.last_error}` : ""}</span>
                  </span>
                  <span className="actions" style={{ marginTop: 0 }}>
                    <button type="button" className="btn quiet sm" disabled={busy} onClick={() => probe(d.id)}>Tester</button>
                    <button type="button" className="btn link" disabled={busy} onClick={() => remove(d.id)}>Retirer</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}


// ------------------------------------------------------------------ comptes
function Accounts() {
  const accounts = useApi<Account[]>("/api/accounts");
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AccountKind>("checking");
  const [iban, setIban] = useState("");
  const [balance, setBalance] = useState<number | null>(0);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const showForm = adding || accounts.data?.length === 0;

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/accounts", { method: "POST", body: { name, kind, iban: iban.trim() || null, balance_cents: balance ?? 0 } });
      setName(""); setIban(""); setBalance(0);
      setAdding(false);
      toast("Compte ajouté.");
      accounts.reload();
    });
  };

  const patch = (id: number, body: Record<string, unknown>) =>
    void run(async () => {
      await api(`/api/accounts/${id}`, { method: "PATCH", body });
      accounts.reload();
    });

  return (
    <section className="card section" aria-labelledby="acc-title">
      <div className="card-head">
        <h2 id="acc-title">Comptes</h2>
        {accounts.data && accounts.data.length > 0 && (
          <button type="button" className="btn quiet sm" aria-expanded={showForm} onClick={() => setAdding((v) => !v)}>
            <Icon name={showForm ? "close" : "plus"} /> {showForm ? "Fermer" : "Ajouter un compte"}
          </button>
        )}
      </div>
      {!accounts.data && <Loading />}
      {accounts.data && accounts.data.length > 0 && (
        <ul className="account-list" aria-label="Comptes">
          {accounts.data.map((a) => (
            <li key={a.id} className={a.archived ? "is-archived" : undefined}>
              <div className="account-id">
                <b>{a.name}</b>
                <span className="faint">{KIND_LABELS[a.kind]}{a.iban_masked || a.institution ? ` · ${a.iban_masked ?? a.institution}` : ""}</span>
              </div>
              <div className="account-balance">
                {a.provider === "enablebanking" ? (
                  <b>{money(a.balance_cents)}</b>
                ) : (
                  <EuroInput allowNegative value={a.balance_cents} aria-label={`Solde de ${a.name}`}
                             onChange={(c) => c !== null && c !== a.balance_cents && patch(a.id, { balance_cents: c })} />
                )}
                {a.balance_at && <span className="faint">au {shortDate(a.balance_at)}</span>}
              </div>
              <label className="check account-budget">
                <input type="checkbox" checked={a.include_in_budget}
                       onChange={(e) => patch(a.id, { include_in_budget: e.target.checked })} />
                Dans le budget
              </label>
              <button className="btn link" onClick={() => patch(a.id, { archived: !a.archived })}>
                {a.archived ? "Réactiver" : "Archiver"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {accounts.data?.length === 0 && <p className="muted">Aucun compte pour l'instant. Un compte courant avec son solde suffit pour commencer.</p>}
      {showForm && (
      <form className="panel card-quiet" style={{ marginTop: "1.25rem" }} onSubmit={create} aria-label="Nouveau compte">
        <div className="form-grid">
          <Field label="Nom"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Compte joint" /></Field>
          <Field label="Type">
            <select value={kind} onChange={(e) => setKind(e.target.value as AccountKind)}>
              {Object.entries(KIND_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <Field label="IBAN (facultatif)" hint="Chiffré sur le serveur ; sert à relier le compte à ta banque.">
            <input value={iban} onChange={(e) => setIban(e.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Solde actuel"><EuroInput allowNegative value={balance} onChange={setBalance} /></Field>
        </div>
        {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
        <div className="actions"><button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Ajouter le compte</button></div>
      </form>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ confidentialité
// Un accord par finalité, lisible, daté, révocable : l'effet est immédiat (aucun traitement ne démarre sans).
export function Privacy() {
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const toggle = (c: ConsentView) =>
    void run(async () => {
      await api(`/api/consents/${c.purpose}`, { method: "PUT", body: { granted: !c.granted } });
      consents.reload();
    });
  return (
    <section className="card section" aria-labelledby="privacy-title">
      <div className="card-head">
        <h2 id="privacy-title">Confidentialité</h2>
        <a className="btn link" href="#/memoire">Ce que l'assistant retient</a>
      </div>
      <p className="muted">Ce que Bank fait de plus avec tes données dépend de ton accord, finalité par finalité. Retirer un accord prend effet tout de suite.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!consents.data && <Loading />}
      {consents.data && (
        <ul className="consent-list">
          {consents.data.map((c) => (
            <li key={c.purpose}>
              <div>
                <b>{c.title}</b>
                <p className="muted">{c.text}</p>
                <span className="faint">
                  {c.explicit && c.updated_at ? `${c.granted ? "Accordé" : "Refusé"} le ${shortDate(c.updated_at)}` : c.granted ? "Actif par défaut" : "Pas encore demandé"}
                </span>
              </div>
              <label className="switch">
                <input type="checkbox" role="switch" checked={c.granted} disabled={busy} onChange={() => toggle(c)}
                       aria-label={`${c.title} : ${c.granted ? "accordé" : "refusé"}`} />
                <span aria-hidden="true" />
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ apparence
const THEMES: { value: ThemePref; label: string }[] = [
  { value: "auto", label: "Comme l'appareil" },
  { value: "light", label: "Clair" },
  { value: "dark", label: "Sombre" },
];

function Appearance() {
  const [theme, setTheme] = useState<ThemePref>(readTheme);
  const choose = (t: ThemePref) => {
    setTheme(t);
    applyTheme(t);
  };
  return (
    <section className="card section" aria-labelledby="look-title">
      <div className="card-head"><h2 id="look-title">Apparence</h2></div>
      <p className="muted" style={{ marginBottom: "0.75rem" }}>Le thème vaut pour cet appareil. Les animations suivent le réglage « réduire les animations » de ton système.</p>
      <Segmented value={theme} options={THEMES} onChange={choose} label="Thème" />
    </section>
  );
}

// ------------------------------------------------------------------ budget
function BudgetSettings() {
  const budget = useApi<BudgetResponse>("/api/budget");
  const [buffer, setBuffer] = useState<number | null>(null);
  const [goal, setGoal] = useState<number | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  useEffect(() => {
    if (budget.data) {
      setBuffer(budget.data.settings.buffer_cents);
      setGoal(budget.data.settings.savings_goal_cents);
    }
  }, [budget.data]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/budget/settings", { method: "PUT", body: { buffer_cents: buffer ?? 0, savings_goal_cents: goal ?? 0 } });
      toast("Réglages du budget enregistrés.");
    });
  };

  return (
    <section className="card section" aria-labelledby="bud-title">
      <div className="card-head"><h2 id="bud-title">Budget</h2></div>
      <form className="form-grid" onSubmit={save} style={{ maxWidth: "44rem" }}>
        <Field label="Toujours garder de côté" hint="Retiré du « reste à dépenser » chaque jour.">
          <EuroInput value={buffer} onChange={setBuffer} />
        </Field>
        <Field label="Épargne visée chaque mois" hint="Réservée avant le calcul du reste à dépenser.">
          <EuroInput value={goal} onChange={setGoal} />
        </Field>
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ sécurité
function Security({ me, onChange }: { me: Me; onChange: () => void }) {
  const [setup, setSetup] = useState<{ qr_svg: string; secret: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [exportPw, setExportPw] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const start = () => void run(async () => setSetup(await api("/api/auth/totp/setup", { method: "POST" })));
  const enable = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<{ recovery_codes: string[] }>("/api/auth/totp/enable", { method: "POST", body: { code } });
      setCodes(r.recovery_codes);
      setSetup(null);
      onChange();
    });
  };
  const changePassword = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/auth/password", { method: "POST", body: { current_password: current, new_password: next } });
      setCurrent(""); setNext("");
      toast("Mot de passe changé. Tes autres appareils sont déconnectés.");
    });
  };
  const exportData = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const blob = await api<Blob>("/api/export", { method: "POST", body: { password: exportPw } });
      const url = URL.createObjectURL(blob instanceof Blob ? blob : new Blob([JSON.stringify(blob)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `bank-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setExportPw("");
    });
  };
  // Le SVG du QR code est affiché via <img> : aucun script embarqué ne peut s'exécuter.
  const qrSrc = setup ? `data:image/svg+xml;base64,${toBase64(setup.qr_svg)}` : "";

  return (
    <section className="card section" aria-labelledby="sec-title">
      <div className="card-head"><h2 id="sec-title">Sécurité</h2>{me.totp_enabled && <span className="tag ok">double authentification active</span>}</div>
      <div className="split">
        <div className="stack">
          <h3>Double authentification</h3>
          {me.totp_enabled ? (
            <p>
              <Icon name="shield" /> Active. {me.recovery_codes_left} codes de secours restants.
            </p>
          ) : setup ? (
            <form className="stack" onSubmit={enable}>
              <p>Scanne ce code avec ton application (Aegis, 2FAS, Google Authenticator…), puis saisis le code affiché.</p>
              <img className="qr" src={qrSrc} alt="QR code de configuration" />
              <p className="faint">Clé manuelle : <span style={{ wordBreak: "break-all" }}>{setup.secret}</span></p>
              <Field label="Code à 6 chiffres">
                <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" required />
              </Field>
              <button className="btn" disabled={busy}>Activer</button>
            </form>
          ) : (
            <>
              <p className="muted">Un code de ton téléphone en plus du mot de passe : indispensable pour des données bancaires.</p>
              <button className={`btn${busy ? " is-loading" : ""}`} onClick={start} disabled={busy}><Icon name="shield" /> Configurer</button>
            </>
          )}
          {codes && (
            <div className="notice warn">
              <p><b>Codes de secours</b> : chacun ne sert qu'une fois. Range-les hors de ce téléphone, ils ne seront plus affichés.</p>
              <div className="codes" style={{ marginTop: "0.75rem" }}>{codes.map((c) => <span key={c}>{c}</span>)}</div>
            </div>
          )}
          <Passkeys onChange={onChange} />
        </div>
        <div className="stack">
          <form className="stack" onSubmit={changePassword} aria-label="Changer de mot de passe">
            <h3>Mot de passe</h3>
            <Field label="Actuel"><input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required autoComplete="current-password" /></Field>
            <Field label="Nouveau" hint="12 caractères minimum."><input type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} autoComplete="new-password" /></Field>
            <button className="btn quiet" disabled={busy}>Changer le mot de passe</button>
          </form>
          <form className="stack" onSubmit={exportData} aria-label="Exporter mes données">
            <h3>Exporter mes données</h3>
            <p className="muted">Tout ton historique en JSON. Le fichier contient tes IBAN en clair : garde-le en lieu sûr.</p>
            <Field label="Mot de passe"><input type="password" value={exportPw} onChange={(e) => setExportPw(e.target.value)} required autoComplete="current-password" /></Field>
            <button className="btn quiet" disabled={busy}>Télécharger l'export</button>
          </form>
        </div>
      </div>
      {error && <p className="error" role="alert" style={{ marginTop: "1rem" }}>{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ passkeys
function Passkeys({ onChange }: { onChange: () => void }) {
  const list = useApi<{ passkeys: PasskeyView[]; rp_id: string; max: number }>("/api/auth/passkeys");
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [removing, setRemoving] = useState<number | null>(null);
  const [removePw, setRemovePw] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const supported = supportsPasskeys();
  const add = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await registerPasskey(name, password);
      setAdding(false); setName(""); setPassword("");
      toast("Passkey ajoutée : tu peux te connecter avec, sans mot de passe.");
      list.reload(); onChange();
    });
  };
  const remove = (e: FormEvent) => {
    e.preventDefault();
    if (removing === null) return;
    void run(async () => {
      await api(`/api/auth/passkeys/${removing}/delete`, { method: "POST", body: { password: removePw } });
      setRemoving(null); setRemovePw("");
      toast("Passkey retirée.");
      list.reload(); onChange();
    });
  };
  const keys = list.data?.passkeys ?? [];
  return (
    <div className="stack pk" aria-labelledby="pk-title">
      <h3 id="pk-title">Passkeys</h3>
      <p className="muted">
        Face ID, empreinte ou clé de sécurité : plus simple qu'un mot de passe sur le téléphone, et une fausse page ne
        peut pas la voler — elle ne vaut que pour {list.data?.rp_id ?? "ton adresse Bank"}. Le mot de passe reste en secours et
        pour les gestes sensibles.
      </p>
      {keys.length > 0 && (
        <ul className="pk-list">
          {keys.map((k) => (
            <li key={k.id}>
              <span><Icon name="lock" /> <b>{k.name}</b> <span className="faint">ajoutée le {shortDate(k.created_at)}{k.last_used_at ? `, utilisée le ${shortDate(k.last_used_at)}` : ", jamais utilisée"}{k.backed_up ? ", synchronisée" : ""}</span></span>
              <button type="button" className="btn link" disabled={busy} onClick={() => { setRemoving(k.id); setRemovePw(""); }}>Retirer</button>
            </li>
          ))}
        </ul>
      )}
      {removing !== null && (
        <form className="stack" onSubmit={remove} aria-label="Retirer une passkey">
          <Field label="Ton mot de passe pour retirer cette passkey"><input type="password" value={removePw} onChange={(e) => setRemovePw(e.target.value)} required autoComplete="current-password" /></Field>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="btn quiet sm" disabled={busy}>Retirer</button>
            <button type="button" className="btn link" onClick={() => setRemoving(null)}>Annuler</button>
          </div>
        </form>
      )}
      {!supported ? (
        <p className="faint">Ce navigateur ne propose pas les passkeys.</p>
      ) : adding ? (
        <form className="stack" onSubmit={add} aria-label="Ajouter une passkey">
          <Field label="Nom de l'appareil" hint="Pour la reconnaître : « iPhone », « clé YubiKey »…"><input value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} /></Field>
          <Field label="Ton mot de passe"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" /></Field>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="lock" /> Créer la passkey</button>
            <button type="button" className="btn link" onClick={() => setAdding(false)}>Annuler</button>
          </div>
        </form>
      ) : (
        <button type="button" className="btn quiet" disabled={busy || (list.data ? keys.length >= list.data.max : false)} onClick={() => setAdding(true)}><Icon name="plus" /> Ajouter une passkey</button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ journal
function Audit() {
  const log = useApi<{ chain: { ok: boolean; entries: number; broken_at: number | null; reason: string | null };
    entries: { id: number; ts: string; actor: string; action: string }[] }>("/api/audit?limit=30");
  if (!log.data) return null;
  const { chain, entries } = log.data;
  return (
    <section className="card section" aria-labelledby="audit-title">
      <div className="card-head"><h2 id="audit-title">Journal d'activité</h2><span className="faint">30 derniers événements</span></div>
      <div className={`notice ${chain.ok ? "ok" : "danger"}`}>
        <Icon name={chain.ok ? "shield" : "alert"} />
        <span>
          {chain.ok
            ? `Intègre : ${chain.entries} événements, chaînés et signés. Aucune modification détectée.`
            : `Altération détectée à l'entrée ${chain.broken_at} (${chain.reason}). Quelqu'un a modifié la base hors de l'application.`}
        </span>
      </div>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Journal, défile de côté" style={{ marginTop: "1rem" }}><table className="plain audit-table">
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.ts).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</td>
              <td>{labelAction(e.action)}</td>
              <td className="muted">{e.actor}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}

function toBase64(text: string): string {
  let bin = "";
  for (const b of new TextEncoder().encode(text)) bin += String.fromCharCode(b);
  return btoa(bin);
}
