// Première ouverture : une intro qui montre ce que Bank apporte, la création de l'accès, puis la banque —
// connexion chez l'établissement (DSP2), synchronisation visible, confirmation des comptes. Jamais bloquant :
// « Je connecterai mes comptes plus tard » est toujours là.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, useApi, type Account, type Connection } from "../api";
import { money } from "../format";
import { KIND_LABELS } from "../pages/Settings";
import { BankConnect, BankMark } from "./BankConnect";
import { CountUp } from "./CountUp";
import { Icon } from "./Icon";
import { Field, useAsyncAction, useToast } from "./ui";

const KEY = "bank:onboarding";

export function onboardingWanted(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "pending";
  } catch {
    return false;
  }
}
export function setOnboarding(state: "pending" | "done"): void {
  try {
    window.localStorage.setItem(KEY, state);
  } catch {
    /* stockage indisponible : l'onboarding ne reprendra pas, l'application reste utilisable */
  }
}

type Stage = "intro" | "access" | "bank" | "syncing" | "done";
const STEPS: Stage[] = ["intro", "access", "bank", "done"];

export function Onboarding({ stage, bankResult = null, onAccess, onDone }: {
  stage: "intro" | "bank";
  bankResult?: string | null;
  onAccess?: () => void;
  onDone?: () => void;
}) {
  const [current, setCurrent] = useState<Stage>(() => (stage === "intro" ? "intro" : bankResult === "connected" ? "syncing" : "bank"));
  const finish = () => {
    setOnboarding("done");
    onDone?.();
  };
  const stepIndex = STEPS.indexOf(current === "syncing" ? "bank" : current);
  return (
    <div className="onboarding">
      <div className="onboarding-top">
        <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
        <div className="onboarding-steps" aria-label={`Étape ${stepIndex + 1} sur ${STEPS.length}`}>
          {STEPS.map((s, i) => <i key={s} className={i < stepIndex ? "is-done" : i === stepIndex ? "is-active" : ""} />)}
        </div>
      </div>
      <div className="onboarding-body" key={current}>
        {current === "intro" && <Intro onNext={() => setCurrent("access")} />}
        {current === "access" && <Access onCreated={() => { setOnboarding("pending"); onAccess?.(); }} />}
        {current === "bank" && <Bank result={bankResult} onLater={finish} />}
        {current === "syncing" && <Syncing onDone={() => setCurrent("done")} />}
        {current === "done" && <Done onAnother={() => { window.location.hash = "#/bienvenue"; setCurrent("bank"); }} onFinish={finish} />}
      </div>
      <div className="onboarding-foot">
        {(current === "bank" || current === "intro") && (
          <button type="button" className="btn ghost" onClick={current === "intro" ? () => setCurrent("access") : finish}>
            {current === "intro" ? "Passer l'introduction" : "Je connecterai mes comptes plus tard"}
          </button>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 1. intro animée
const SLIDES = [
  {
    title: "Combien tu peux dépenser aujourd'hui, sans te mettre dans le rouge.",
    text: "Bank déduit tes échéances à venir, garde un coussin de côté et te donne un chiffre par jour, qui change quand ta paie arrive.",
  },
  {
    title: "Tes aides, ta paie, tes abonnements : prévus, pas subis.",
    text: "CAF, France Travail, salaire, loyer, Netflix : Bank reconnaît tout dans tes relevés, prévoit la suite et te prévient avant.",
  },
  {
    title: "Une application qui apprend à te connaître, une question à la fois.",
    text: "Elle remarque, demande en disant pourquoi, et ton agent IA ne fait rien sans ton accord. Tout reste chez toi, chiffré.",
  },
];

function Intro({ onNext }: { onNext: () => void }) {
  const [i, setI] = useState(0);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    timer.current = window.setTimeout(() => (i < SLIDES.length - 1 ? setI(i + 1) : undefined), 3600);
    return () => window.clearTimeout(timer.current);
  }, [i]);
  const slide = SLIDES[i]!;
  return (
    <div className="intro-slide" key={i}>
      <div>
        <h1>{slide.title}</h1>
        <p>{slide.text}</p>
        <div className="actions" style={{ marginTop: "1.5rem" }}>
          {i < SLIDES.length - 1 ? (
            <button type="button" className="btn lg" onClick={() => setI(i + 1)}>Suivant <Icon name="arrow" /></button>
          ) : (
            <button type="button" className="btn lg" onClick={onNext}>Créer mon accès <Icon name="arrow" /></button>
          )}
          <span className="faint" aria-live="polite">{i + 1} / {SLIDES.length}</span>
        </div>
      </div>
      <IntroArt index={i} />
    </div>
  );
}

function IntroArt({ index }: { index: number }) {
  if (index === 0) {
    return (
      <div className="intro-art" aria-hidden="true">
        <p className="today-lead">Tu peux dépenser</p>
        <p className="today-figure"><CountUp value={48} /> €<small>par jour</small></p>
        <p className="muted">Jusqu'au 27, puis 383 € par jour après l'arrivée du salaire.</p>
      </div>
    );
  }
  if (index === 1) {
    return (
      <div className="intro-art" aria-hidden="true">
        {[["Salaire", "+2 150 €", "le 28"], ["Loyer", "−650 €", "le 3"], ["CAF — prime d'activité", "+258 €", "le 5"], ["Netflix", "−13,49 €", "le 25"]].map(([a, b, c], k) => (
          <div className="mini-card" key={a} style={{ ["--i" as string]: k }}>
            <span><b>{a}</b> <span className="faint">{c}</span></span>
            <span className={b!.startsWith("+") ? "amount in" : "amount"}>{b}</span>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="intro-art" aria-hidden="true">
      <div className="ask ask-question" style={{ animationDelay: "200ms" }}>
        <p className="ask-kind">Bank te demande</p>
        <h3>Ton loyer, c'est bien 650 € par mois ?</h3>
        <p className="ask-body">Prélèvement régulier repéré le 3 de chaque mois.</p>
        <div className="actions ask-actions"><span className="btn">C'est juste</span><span className="btn quiet">Corriger</span></div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 2. accès
function Access({ onCreated }: { onCreated: () => void }) {
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, error, run] = useAsyncAction();
  const mismatch = confirm.length > 0 && confirm !== password;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    void run(async () => {
      await api("/api/setup", { method: "POST", body: { setup_token: token.trim(), username, password } });
      await api("/api/auth/login", { method: "POST", body: { username, password } });
      onCreated();
    });
  };
  return (
    <div className="intro-slide">
      <div>
        <h1>Crée ton accès</h1>
        <p>Un identifiant, une phrase de passe, et le jeton d'installation affiché par le serveur (il empêche quiconque sur ton réseau de prendre la main avant toi).</p>
      </div>
      <form className="card stack" onSubmit={submit} aria-label="Créer ton accès">
        <Field label="Jeton d'installation"><input value={token} onChange={(e) => setToken(e.target.value)} required autoComplete="off" /></Field>
        <Field label="Identifiant"><input value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} pattern="[A-Za-z0-9._\-]+" autoComplete="username" /></Field>
        <Field label="Phrase de passe" hint="12 caractères minimum ; quatre mots suffisent."><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={12} autoComplete="new-password" /></Field>
        <Field label="Confirmation"><input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" aria-invalid={mismatch} /></Field>
        {mismatch && <p className="error">Les deux phrases de passe diffèrent.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy}>Créer mon accès</button>
      </form>
    </div>
  );
}

// ------------------------------------------------------------------ 3. banque
function Bank({ result, onLater }: { result: string | null; onLater: () => void }) {
  const status = useApi<{ enabled: boolean; connections: Connection[] }>("/api/banks");
  const [aspsps, setAspsps] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(
    result === "refused" ? "Connexion annulée côté banque. Tu peux réessayer, ou choisir une autre banque."
      : result === "error" ? "La connexion a échoué ou a expiré : recommence."
      : null,
  );

  useEffect(() => {
    if (status.data?.enabled && aspsps === null) {
      api<{ name: string }[]>("/api/banks/aspsps?country=FR").then((list) => setAspsps(list.map((a) => a.name))).catch((e: Error) => setError(e.message));
    }
  }, [status.data, aspsps]);

  const connect = async (name: string) => {
    setBusy(name);
    setFailed(null);
    setError(null);
    try {
      const r = await api<{ redirect_url: string }>("/api/banks/connect", { method: "POST", body: { aspsp_name: name, country: "FR" } });
      const url = new URL(r.redirect_url);
      if (url.protocol !== "https:") throw new Error("Adresse de redirection refusée");
      window.location.assign(url.toString());
    } catch (e) {
      setFailed(name);
      setBusy(null);
      setError((e as Error).message);
    }
  };

  return (
    <div className="stack" style={{ gap: "1.25rem" }}>
      <div>
        <h1>Connecte ta banque</h1>
        <p className="muted" style={{ marginTop: "0.5rem" }}>
          Tu t'authentifies chez ta banque (DSP2, via Enable Banking, établissement agréé) : Bank ne voit jamais tes identifiants et
          ne peut rien faire d'autre que lire. Mise à jour automatique ensuite, pendant 90 jours renouvelables.
        </p>
      </div>
      {status.data && !status.data.enabled && (
        <div className="notice warn">
          <Icon name="alert" />
          <span>La synchronisation bancaire n'est pas configurée sur ce serveur (voir le README : BANK_ENABLEBANKING_APP_ID et clé privée). Tu peux importer tes relevés CSV / OFX ou commencer avec trois chiffres.</span>
        </div>
      )}
      {error && <div className="notice danger" role="alert"><Icon name="alert" /><span>{error}</span></div>}
      {status.data?.enabled && (
        <BankConnect aspsps={aspsps} connections={status.data.connections} busy={busy} failed={failed} onConnect={(n) => void connect(n)} />
      )}
      {status.data && !status.data.enabled && (
        <div className="actions">
          <button type="button" className="btn lg" onClick={onLater}>Continuer sans connexion</button>
        </div>
      )}
      <p className="faint"><Icon name="shield" /> Lecture seule. Tu peux couper l'accès à tout moment dans Connecteurs, ou chez ta banque.</p>
    </div>
  );
}

// ------------------------------------------------------------------ 4. synchronisation visible
const SYNC_STEPS = ["Connexion établie avec ta banque", "Comptes récupérés", "Opérations lues", "Salaire, loyer et abonnements reconnus"];

function Syncing({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [report, setReport] = useState<{ inserted: number; accounts: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    const tick = window.setInterval(() => setStep((s) => Math.min(s + 1, SYNC_STEPS.length - 1)), 900);
    (async () => {
      try {
        const st = await api<{ connections: Connection[] }>("/api/banks");
        const latest = [...st.connections].filter((c) => c.status === "active").sort((a, b) => b.id - a.id)[0];
        let inserted = 0;
        if (latest) {
          const r = await api<{ inserted: number; errors: string[] }>(`/api/banks/${latest.id}/sync`, { method: "POST" });
          inserted = r.inserted;
          if (r.errors.length) toast(r.errors.join(" "));
        }
        const accounts = await api<Account[]>("/api/accounts");
        if (!cancelled) setReport({ inserted, accounts: accounts.filter((a) => a.provider === "enablebanking").length });
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      window.clearInterval(tick);
    };
  }, [toast]);

  useEffect(() => {
    if (report && step >= SYNC_STEPS.length - 1) {
      const t = window.setTimeout(onDone, 700);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [report, step, onDone]);

  return (
    <div className="intro-slide">
      <div>
        <h1>Bank lit tes comptes</h1>
        <p>Quelques secondes : la banque transmet tes comptes et ton historique, Bank reconnaît ce qui revient chaque mois.</p>
        {error && <div className="notice danger" role="alert" style={{ marginTop: "1rem" }}><Icon name="alert" /><span>{error}</span></div>}
        {error && <div className="actions"><button type="button" className="btn" onClick={onDone}>Continuer</button></div>}
      </div>
      <ol className="sync-steps" aria-live="polite">
        {SYNC_STEPS.map((label, i) => (
          <li key={label} className={i < step || (report && i === step) ? "is-done" : i === step ? "is-active" : ""}>
            <span className="dot" aria-hidden="true">{(i < step || (report && i === step)) ? <Icon name="check" /> : null}</span>
            {label}{i === 2 && report ? ` (${report.inserted})` : ""}
          </li>
        ))}
      </ol>
    </div>
  );
}

// ------------------------------------------------------------------ 5. confirmation
function Done({ onAnother, onFinish }: { onAnother: () => void; onFinish: () => void }) {
  const accounts = useApi<Account[]>("/api/accounts");
  const banks = useApi<{ connections: Connection[] }>("/api/banks");
  const connected = (accounts.data ?? []).filter((a) => a.provider === "enablebanking" && !a.archived);
  const total = connected.reduce((s, a) => s + a.balance_cents, 0);
  return (
    <div className="stack" style={{ gap: "1.25rem" }}>
      <div>
        <p className="welcome-kicker"><Icon name="check" /> Banque connectée</p>
        <h1>{connected.length > 0 ? `${connected.length} compte${connected.length > 1 ? "s" : ""} relié${connected.length > 1 ? "s" : ""}` : "Connexion enregistrée"}</h1>
        {connected.length > 0 && <p className="muted" style={{ marginTop: "0.5rem" }}>Solde total : <b>{money(total, { whole: true })}</b>. Bank se met à jour tout seul à partir de maintenant.</p>}
      </div>
      <ul className="connected" aria-label="Comptes reliés">
        {connected.map((a) => (
          <li key={a.id}>
            <span style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <BankMark name={a.institution ?? a.name} small />
              <span><b>{a.name}</b><div className="faint">{KIND_LABELS[a.kind]}{a.iban_masked ? ` · ${a.iban_masked}` : ""}</div></span>
            </span>
            <b>{money(a.balance_cents, { whole: true })}</b>
          </li>
        ))}
        {banks.data?.connections.filter((c) => c.status !== "active").map((c) => (
          <li key={`c${c.id}`}><span><b>{c.aspsp_name}</b><div className="faint">{c.last_error ?? "en attente"}</div></span></li>
        ))}
      </ul>
      <div className="actions">
        <button type="button" className="btn lg" onClick={onFinish}>Voir mon tableau de bord <Icon name="arrow" /></button>
        <button type="button" className="btn quiet lg" onClick={onAnother}><Icon name="plus" /> Ajouter une autre banque</button>
      </div>
    </div>
  );
}
