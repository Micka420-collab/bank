// Assistant Enable Banking : six étapes pour relier Bank à tes banques sans toucher au serveur — créer l'application,
// coller l'adresse de retour, donner la clé (.pem), laisser Bank vérifier, activer en liant ses comptes, choisir sa
// banque. La clé part une fois, chiffrée côté serveur ; elle n'est plus jamais affichée (indice et empreinte seulement).

import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent, type ReactNode } from "react";
import { api, type BankSetupIssue, type BankSetupStatus } from "../api";
import { Icon } from "./Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "./ui";

export const SETUP_STEPS = ["Créer l'application", "Adresse de retour", "Ta clé", "Vérification", "Activation", "Ta banque"];
const BLOCKING = new Set<BankSetupIssue["code"]>(["bad_key", "unreachable", "redirect"]);
const MAX_PEM = 16_000;
// Le fichier téléchargé porte l'identifiant de l'application pour nom : « 1a2b…-….pem ».
const PEM_NAME = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?: \(\d+\))?\.pem$/i;

/** Prêt à choisir sa banque : configuré, et la dernière vérification n'a rien trouvé de bloquant. */
export function setupReady(st: BankSetupStatus | null): boolean {
  return !!st && st.configured && st.check?.ok === true;
}

/** L'étape où en est la configuration (0 à 5) ; avant la clé, c'est toi qui avances. */
export function setupStep(st: BankSetupStatus | null): number {
  if (!st || !st.configured) return 0;
  if (!st.check) return 3;
  const codes = st.check.issues.map((i) => i.code);
  if (codes.some((c) => BLOCKING.has(c))) return 3;
  if (codes.includes("inactive")) return 4;
  return 5;
}

export function appIdFromFileName(name: string): string | null {
  return PEM_NAME.exec(name.trim())?.[1] ?? null;
}

export function BankSetup({ chooser, onChange }: {
  chooser?: ReactNode; // l'étape 6 : le choix de la banque (BankConnect)
  onChange?: (st: BankSetupStatus) => void;
}) {
  const [st, setSt] = useState<BankSetupStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [manual, setManual] = useState<number | null>(null); // étape choisie à la main (avant la clé, ou « changer de clé »)
  const [busy, error, run] = useAsyncAction();
  const autoChecked = useRef(false);
  const toast = useToast();

  const load = () => {
    setLoadError(null);
    api<BankSetupStatus>("/api/banks/setup").then(setSt, (e: Error) => setLoadError(e.message));
  };
  useEffect(load, []);
  useEffect(() => {
    if (st) onChange?.(st);
    // seul l'état reçu compte : le rappel du parent peut changer à chaque rendu
  }, [st]);

  const check = () =>
    void run(async () => {
      setSt(await api<BankSetupStatus>("/api/banks/setup/check", { method: "POST" }));
      setManual(null);
    });
  // Configuré par le serveur mais jamais vérifié : Bank vérifie tout seul, une fois.
  useEffect(() => {
    if (st?.configured && st.check === null && !autoChecked.current) {
      autoChecked.current = true;
      check();
    }
  });

  if (loadError) return <ErrorNote message={loadError} onRetry={load} />;
  if (!st) return <Loading what="Assistant de connexion" />;

  const computed = setupStep(st);
  const current = manual ?? computed;
  const copy = () =>
    void navigator.clipboard?.writeText(st.redirect_url).then(
      () => toast("Adresse copiée : colle-la dans « Allowed redirect URLs »."),
      () => toast("Copie impossible : sélectionne l'adresse à la main."),
    );
  const sandbox = st.check?.issues.find((i) => i.code === "sandbox");

  return (
    <div className="wizard" aria-label="Assistant de connexion Enable Banking">
      <ol className="wizard-steps" aria-label={`Étape ${current + 1} sur ${SETUP_STEPS.length}`}>
        {SETUP_STEPS.map((label, i) => (
          <li key={label} className={i < current ? "is-done" : i === current ? "is-active" : ""} aria-current={i === current ? "step" : undefined}>
            <span aria-hidden="true">{i < current ? <Icon name="check" /> : i + 1}</span>
            {label}
          </li>
        ))}
      </ol>

      <div className="wizard-panel" key={current}>
        {current === 0 && (
          <>
            <h3>1. Crée ton application Enable Banking</h3>
            <p className="muted">
              Enable Banking est l'intermédiaire agréé qui lit tes comptes pour Bank. Pour un usage personnel, c'est gratuit :
              ton application ne voit que les comptes que tu lies toi-même (« mode restreint »). Compte cinq minutes.
            </p>
            <ol className="steps">
              <li>
                Ouvre le <a href={st.console_url} target="_blank" rel="noreferrer noopener">Control Panel d'Enable Banking <Icon name="external" /></a> et
                connecte-toi avec ton e-mail (un lien de connexion t'est envoyé).
              </li>
              <li>Va dans <b>Applications</b>, puis <b>Add a new application</b>.</li>
              <li>Environnement : choisis <b>Production</b>. « Sandbox » ne montre que des banques de démonstration.</li>
              <li>Nom : ce que tu veux, par exemple « Bank perso ». Pour l'e-mail et les adresses demandées, les tiens suffisent.</li>
              <li>Clé : laisse <b>Generate in the browser</b>. Un fichier <code>.pem</code> se télécharge : garde-le, Bank en a besoin à l'étape 3.</li>
            </ol>
            <p className="faint">Garde ce formulaire ouvert : l'étape suivante te donne l'adresse à y coller.</p>
            <div className="actions">
              <button type="button" className="btn" onClick={() => setManual(1)}>J'y suis, étape suivante <Icon name="arrow" /></button>
            </div>
          </>
        )}

        {current === 1 && (
          <>
            <h3>2. Colle l'adresse de retour</h3>
            <p className="muted">
              Dans le formulaire, champ <b>Allowed redirect URLs</b>, colle exactement cette adresse. C'est là que ta banque te
              renverra après ton accord.
            </p>
            <div className="copy-field">
              <code>{st.redirect_url}</code>
              <button type="button" className="btn quiet sm" onClick={copy}><Icon name="copy" /> Copier</button>
            </div>
            <p className="faint">Puis valide le formulaire (<b>Register</b>) : le fichier .pem se télécharge à ce moment-là.</p>
            <div className="actions">
              <button type="button" className="btn ghost" onClick={() => setManual(0)}><Icon name="back" /> Retour</button>
              <button type="button" className="btn" onClick={() => setManual(2)}>C'est fait <Icon name="arrow" /></button>
            </div>
          </>
        )}

        {current === 2 && (
          <KeyForm
            source={st.source}
            onBack={() => setManual(st.configured ? null : 1)}
            onSaved={(next) => {
              setSt(next);
              setManual(null);
              toast(next.check?.ok ? "Application vérifiée : choisis ta banque." : "Clé enregistrée. Il reste une chose à régler.");
            }}
          />
        )}

        {current === 3 && (
          <>
            <h3>4. Bank vérifie</h3>
            {!st.check || busy ? (
              <p className="muted" aria-live="polite"><span className="spinner" /> Bank interroge Enable Banking avec ta clé…</p>
            ) : (
              <>
                <p className="muted">Bank a interrogé Enable Banking avec ta clé. Voici ce qu'il faut régler :</p>
                <Issues issues={st.check.issues.filter((i) => BLOCKING.has(i.code))} />
                {st.check.issues.some((i) => i.code === "redirect") && (
                  <div className="copy-field">
                    <code>{st.redirect_url}</code>
                    <button type="button" className="btn quiet sm" onClick={copy}><Icon name="copy" /> Copier</button>
                  </div>
                )}
              </>
            )}
            <div className="actions">
              {st.source === "app" && <button type="button" className="btn ghost" onClick={() => setManual(2)}>Changer de clé</button>}
              <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={check}>Vérifier à nouveau</button>
            </div>
          </>
        )}

        {current === 4 && (
          <>
            <h3>5. Active ton application</h3>
            <p className="muted">
              Ta clé est la bonne. Dernier geste chez Enable Banking : en mode restreint, ton application ne s'ouvre qu'aux comptes
              que tu as liés toi-même.
            </p>
            <ol className="steps">
              <li>Dans le <a href={st.console_url} target="_blank" rel="noreferrer noopener">Control Panel <Icon name="external" /></a>, ouvre ton application.</li>
              <li>Clique <b>Activate by linking accounts</b>.</li>
              <li>Choisis ta banque, connecte-toi chez elle comme d'habitude et coche les comptes à suivre.</li>
              <li>Reviens ici : Bank vérifie que l'application est active.</li>
            </ol>
            <p className="faint"><Icon name="shield" /> Gratuit, réservé à ton usage, et Bank ne voit jamais tes identifiants bancaires.</p>
            <div className="actions">
              <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={check}>J'ai lié mes comptes, vérifier</button>
            </div>
          </>
        )}

        {current === 5 && (
          <>
            <h3>6. Choisis ta banque</h3>
            <p className="muted">
              <Icon name="check" /> Application{st.check?.app_name ? ` « ${st.check.app_name} »` : ""} vérifiée.
              {chooser ? " Choisis ta banque : tu donnes ton accord chez elle, puis tu reviens ici." : " Tu peux connecter tes banques."}
            </p>
            {chooser}
          </>
        )}

        {sandbox && current >= 3 && <p className="notice warn"><Icon name="alert" /><span>{sandbox.message}</span></p>}
        {error && <ErrorNote message={error} />}
      </div>

      {st.configured && <Summary st={st} onChangeKey={() => setManual(2)} onRemoved={(next) => { setSt(next); setManual(null); autoChecked.current = true; }} />}
    </div>
  );
}

function Issues({ issues }: { issues: BankSetupIssue[] }) {
  return (
    <ul className="wizard-issues">
      {issues.map((i) => (
        <li key={i.code} className={`notice ${i.code === "unreachable" ? "warn" : "danger"}`} role="alert">
          <Icon name="alert" />
          <span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

function KeyForm({ source, onBack, onSaved }: {
  source: BankSetupStatus["source"];
  onBack: () => void;
  onSaved: (st: BankSetupStatus) => void;
}) {
  const [appId, setAppId] = useState("");
  const [pem, setPem] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [over, setOver] = useState(false);
  const [local, setLocal] = useState<string | null>(null);
  const [busy, error, run] = useAsyncAction();

  const take = async (file: File | undefined) => {
    if (!file) return;
    setLocal(null);
    if (file.size > MAX_PEM) return setLocal("Ce fichier est trop gros pour une clé : prends le .pem téléchargé à la création.");
    const text = await file.text();
    if (!text.includes("PRIVATE KEY-----")) return setLocal("Ce fichier ne contient pas de clé privée : prends le .pem téléchargé à la création.");
    setPem(text);
    setFileName(file.name);
    const guessed = appIdFromFileName(file.name);
    if (guessed) setAppId((v) => v || guessed);
  };
  const onFile = (e: ChangeEvent<HTMLInputElement>) => void take(e.target.files?.[0]);
  const onDrop = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setOver(false);
    void take(e.dataTransfer.files?.[0]);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!pem.trim()) return setLocal("Il manque la clé : dépose le fichier .pem ou colle son contenu.");
    if (!pem.includes("PRIVATE KEY-----")) return setLocal("Ceci n'est pas une clé privée (« -----BEGIN PRIVATE KEY----- »).");
    setLocal(null);
    void run(async () => {
      const next = await api<BankSetupStatus>("/api/banks/setup", {
        method: "PUT",
        body: { password, app_id: appId.trim(), private_key: pem.trim() },
      });
      setPem(""); setPassword(""); setFileName(null);
      onSaved(next);
    });
  };

  if (source === "server") {
    return (
      <>
        <h3>3. Ta clé</h3>
        <p className="muted">La clé est fournie par le serveur (fichier .env) : il n'y a rien à saisir ici.</p>
        <div className="actions"><button type="button" className="btn ghost" onClick={onBack}><Icon name="back" /> Retour</button></div>
      </>
    );
  }

  return (
    <form className="stack" onSubmit={submit} aria-label="Clé Enable Banking">
      <h3>3. Donne ta clé à Bank</h3>
      <p className="muted">
        La clé permet à Bank de signer ses demandes à Enable Banking. Elle est chiffrée dans Bank et ne sera plus jamais affichée.
      </p>
      <label
        className={`drop${over ? " is-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <Icon name={fileName ? "check" : "upload"} />
        <span>{fileName ? <>Clé chargée : <b>{fileName}</b></> : <>Dépose ici le fichier <b>.pem</b>, ou clique pour le choisir</>}</span>
        <input type="file" accept=".pem,application/x-pem-file,text/plain" onChange={onFile} aria-label="Fichier de clé .pem" className="sr-only" />
      </label>
      <details>
        <summary>Ou colle le contenu du fichier</summary>
        <Field label="Contenu de la clé">
          <textarea value={fileName ? "" : pem} rows={5} maxLength={MAX_PEM} autoComplete="off" spellCheck={false} placeholder="-----BEGIN PRIVATE KEY-----" onChange={(e) => { setFileName(null); setPem(e.target.value); }} />
        </Field>
      </details>
      <Field label="Identifiant de l'application (Application ID)" hint="sur la page de ton application ; c'est aussi le nom du fichier .pem">
        <input value={appId} required maxLength={80} autoComplete="off" spellCheck={false} placeholder="1a2b3c4d-…" onChange={(e) => setAppId(e.target.value)} />
      </Field>
      <Field label="Ton mot de passe Bank" hint="exigé pour garder une clé">
        <input type="password" value={password} required autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {local && <p className="notice warn" role="alert"><Icon name="alert" /><span>{local}</span></p>}
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button type="button" className="btn ghost" onClick={onBack}><Icon name="back" /> Retour</button>
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}><Icon name="shield" /> Enregistrer et vérifier</button>
      </div>
    </form>
  );
}

function Summary({ st, onChangeKey, onRemoved }: {
  st: BankSetupStatus;
  onChangeKey: () => void;
  onRemoved: (st: BankSetupStatus) => void;
}) {
  const [removing, setRemoving] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const remove = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const next = await api<BankSetupStatus>("/api/banks/setup", { method: "DELETE", body: { password } });
      setPassword(""); setRemoving(false);
      toast("Application retirée : Bank ne contacte plus Enable Banking.");
      onRemoved(next);
    });
  };
  return (
    <div className="wizard-summary">
      <p className="faint">
        <Icon name="lock" /> Application <b>{st.app_id_hint ?? "?"}</b>
        {st.key_fingerprint && <> · empreinte de la clé <code>{st.key_fingerprint}</code></>}
        {st.source === "server" && <> · <span className="tag">configurée par le serveur</span></>}
      </p>
      {st.source === "app" && !removing && (
        <span className="actions" style={{ marginTop: 0 }}>
          <button type="button" className="btn quiet sm" onClick={onChangeKey}>Changer de clé</button>
          <button type="button" className="btn ghost sm" onClick={() => setRemoving(true)}>Retirer</button>
        </span>
      )}
      {removing && (
        <form className="stack" onSubmit={remove} aria-label="Retirer l'application">
          <p className="muted">
            Bank oublie la clé et ne synchronise plus tes banques{st.connections ? ` (${st.connections} connectée${st.connections > 1 ? "s" : ""})` : ""}. L'historique déjà importé reste.
          </p>
          <Field label="Ton mot de passe Bank"><input type="password" value={password} required autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <ErrorNote message={error} />}
          <span className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="btn ghost sm" onClick={() => setRemoving(false)}>Annuler</button>
            <button className={`btn danger sm${busy ? " is-loading" : ""}`} disabled={busy}>Retirer l'application</button>
          </span>
        </form>
      )}
    </div>
  );
}
