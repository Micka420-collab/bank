// « Démarrer avec Bank » (DESIGN.md § 3.10) : le parcours plein écran de la première ouverture. Sept étapes, une
// action principale chacune, « Passer » toujours là ; on le quitte (Échap ou « Quitter ») et on le reprend où on l'a
// laissé, sur n'importe quel appareil : fait, passé et l'étape en cours viennent du serveur (/api/start).
// Les étapes réutilisent ce qui existe (chemins de Welcome, assistant Enable Banking, notifications, coffre) ; la
// seule fête est celle de la fin, et seulement si au moins une étape a été faite pendant ce passage.

import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  api,
  useApi,
  type ConsentView,
  type Connection,
  type OnboardingResponse,
  type Opportunity,
  type OpportunityCategory,
  type StartStepKey,
  type StartView,
  type VaultUpload,
} from "../api";
import { money, parseEuros, perPeriod, plural } from "../format";
import { enablePush, pushPermission, pushSupported, type PushStatus } from "../push";
import { BankConnect } from "./BankConnect";
import { BankSetup, setupReady } from "./BankSetup";
import { ConsentGroups } from "./ConsentGroups";
import { Icon } from "./Icon";
import { BankDone, Syncing } from "./Onboarding";
import { Tour } from "./Tour";
import { Loading, useAsyncAction, useCelebrate, useToast } from "./ui";
import { StartPaths, ThreeFigures, type StartPath } from "./Welcome";

const ORDER: StartStepKey[] = ["banque", "foyer", "accords", "papier", "priorites", "prevenir", "visite"];

/** Ce que chaque étape dit d'elle-même : son but en une phrase, sa durée réelle, son « passer ». */
const COPY: Record<StartStepKey, { goal: string; time: string; skip: string }> = {
  banque: { goal: "Que Bank connaisse ton argent sans que tu saisisses rien.", time: "30 s à 5 min", skip: "Plus tard" },
  foyer: { goal: "Que Bank sache qui vit de ce budget, pour simuler tes droits.", time: "1 min", skip: "Plus tard" },
  accords: { goal: "Choisir ce que Bank peut aller chercher pour toi, en sachant ce qui part.", time: "1 min", skip: "Pas maintenant" },
  papier: { goal: "Ranger un premier document que Bank lit pour toi.", time: "1 min", skip: "Plus tard" },
  priorites: { goal: "Dire par quoi Bank commence à chercher.", time: "20 s", skip: "Plus tard" },
  prevenir: { goal: "Recevoir les alertes importantes, découvert ou échéance, là où tu es.", time: "1 min", skip: "Plus tard" },
  visite: { goal: "Savoir où est quoi, en cinq écrans.", time: "40 s", skip: "Passer la visite" },
};

export function isStepKey(k: string | null | undefined): k is StartStepKey {
  return !!k && (ORDER as string[]).includes(k);
}

export function Journey({ stepKey, bankResult = null, onExit }: { stepKey: string | null; bankResult?: string | null; onExit: () => void }) {
  const initial = useApi<StartView>("/api/start");
  const [data, setData] = useState<StartView | null>(null);
  const [announce, setAnnounce] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const didSomething = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const celebrate = useCelebrate();

  useEffect(() => {
    if (initial.data) setData(initial.data);
  }, [initial.data]);

  const key: StartStepKey | null = isStepKey(stepKey) ? stepKey
    : data ? data.journey.current ?? data.journey.steps.find((s) => !s.done && !s.skipped)?.key ?? "banque" : null;
  const go = useCallback((k: StartStepKey) => {
    window.location.hash = `#/bienvenue/${k}`;
  }, []);

  // L'adresse dit toujours l'étape (reprise, lien profond) ; le serveur retient l'étape en cours.
  useEffect(() => {
    if (!key || !data) return;
    if (!isStepKey(stepKey)) window.history.replaceState(null, "", `#/bienvenue/${key}`);
    api<StartView>(`/api/start/steps/${key}`, { method: "POST", body: { action: "current" } }).catch(() => undefined);
  }, [key, !!data]); // eslint-disable-line -- une fois par étape affichée

  const step = data?.journey.steps.find((s) => s.key === key) ?? null;
  // Focus sur le titre de chaque étape, et le titre de l'onglet suit.
  useEffect(() => {
    if (!step) return;
    document.title = `${step.title} · Bank`;
    heading.current?.focus();
  }, [step?.key]); // eslint-disable-line -- à chaque nouvelle étape seulement
  useEffect(() => () => { document.title = "Bank"; }, []);

  const quit = useCallback(() => onExit(), [onExit]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) quit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [quit]);

  const finish = async (tourDone: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/start/finish", { method: "POST", body: { tour_done: tourDone } });
      if (tourDone) didSomething.current = true;
      if (didSomething.current) celebrate("done", "Bank est prêt.");
      onExit();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** Le serveur a confirmé : on relit l'état (coche verte sur le segment), puis l'étape suivante. */
  const completed = async () => {
    if (!key) return;
    setError(null);
    try {
      const next = await api<StartView>("/api/start");
      setData(next);
      const nowDone = next.journey.steps.find((s) => s.key === key)?.done ?? false;
      if (nowDone) {
        didSomething.current = true;
        setAnnounce(`Étape faite : ${step?.title ?? ""}.`);
      }
      const target = next.journey.current && next.journey.current !== key ? next.journey.current : nextAfter(next, key);
      if (target) go(target);
      else void finish(false);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const skip = async () => {
    if (!key) return;
    if (key === "visite") return void finish(false);
    setBusy(true);
    setError(null);
    try {
      const next = await api<StartView>(`/api/start/steps/${key}`, { method: "POST", body: { action: "skip" } });
      setData(next);
      setAnnounce(`Étape passée : ${step?.title ?? ""}.`);
      const target = next.journey.current ?? nextAfter(next, key);
      if (target) go(target);
      else await finish(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (initial.error && !data) {
    return (
      <div className="journey">
        <div className="journey-body"><div className="notice danger" role="alert"><Icon name="alert" /><span>{initial.error}</span></div>
          <div className="actions"><button type="button" className="btn" onClick={initial.reload}>Réessayer</button><button type="button" className="btn ghost" onClick={quit}>Quitter</button></div>
        </div>
      </div>
    );
  }
  if (!data || !key || !step) return <div className="journey"><div className="journey-body"><Loading what="Ton parcours" /></div></div>;

  const index = ORDER.indexOf(key);
  const copy = COPY[key];
  return (
    <div className="journey">
      <header className="journey-top">
        <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
        <p className="journey-count">Étape {index + 1} sur {ORDER.length}</p>
        <button type="button" className="btn ghost sm journey-quit" onClick={quit}>Quitter <Icon name="close" /></button>
      </header>
      <ol className="journey-progress" aria-label="Les étapes du parcours">
        {data.journey.steps.map((s) => (
          <li key={s.key} className={s.done ? "is-done" : s.skipped ? "is-skipped" : ""} aria-current={s.key === key ? "step" : undefined}>
            <a href={`#/bienvenue/${s.key}`}>
              <span className="journey-seg" aria-hidden="true">{s.done && <Icon name="check" size={12} />}</span>
              <span className="sr-only">{s.title}{s.done ? ", faite" : s.skipped ? ", passée" : ""}</span>
            </a>
          </li>
        ))}
      </ol>
      <p className="sr-only" aria-live="polite">{announce}</p>
      <main className="journey-body" key={key}>
        <section className="card journey-card" aria-labelledby="journey-title">
          <p className="journey-kicker"><Icon name="clock" size={14} /> {copy.time}{step.done ? " · déjà fait" : ""}</p>
          <h1 id="journey-title" ref={heading} tabIndex={-1}>{step.title}</h1>
          <p className="journey-goal">{copy.goal}</p>
          {error && <p className="error" role="alert">{error}</p>}
          <StepBody k={key} data={data} done={step.done} bankResult={bankResult} onDone={() => void completed()}
                    onFinishTour={() => void finish(true)} busy={busy} reload={(v) => setData(v)} />
        </section>
        {/* Une étape déjà faite n'a rien à passer : « Continuer » suffit. */}
        {!step.done && (
          <div className="journey-skip">
            <button type="button" className="btn ghost" disabled={busy} onClick={() => void skip()}>{copy.skip}</button>
          </div>
        )}
      </main>
    </div>
  );
}

/** La prochaine étape ni faite ni passée après `key`, sinon la visite si elle reste à faire. */
export function nextAfter(view: StartView, key: StartStepKey): StartStepKey | null {
  const steps = view.journey.steps;
  const from = steps.findIndex((s) => s.key === key);
  const later = steps.slice(from + 1).find((s) => !s.done && !s.skipped);
  if (later) return later.key;
  const visit = steps.find((s) => s.key === "visite");
  return key !== "visite" && visit && !visit.done ? "visite" : null;
}

function StepBody({ k, data, done, bankResult, onDone, onFinishTour, busy, reload }: {
  k: StartStepKey;
  data: StartView;
  done: boolean;
  bankResult: string | null;
  onDone: () => void;
  onFinishTour: () => void;
  busy: boolean;
  reload: (v: StartView) => void;
}) {
  switch (k) {
    case "banque": return <BankStep done={done} result={bankResult} onDone={onDone} />;
    case "foyer": return <HouseholdStep onDone={onDone} />;
    case "accords": return <ConsentsStep data={data} done={done} onDone={onDone} reload={reload} />;
    case "papier": return <PaperStep done={done} onDone={onDone} />;
    case "priorites": return <PrioritiesStep onDone={onDone} />;
    case "prevenir": return <NotifyStep done={done} onDone={onDone} />;
    case "visite": return <Tour onFinish={onFinishTour} busy={busy} />;
  }
}

function Already({ text, onDone }: { text: string; onDone: () => void }) {
  return (
    <>
      <p className="notice ok"><Icon name="check" /><span>{text}</span></p>
      <div className="actions journey-actions"><button type="button" className="btn lg" onClick={onDone}>Continuer <Icon name="arrow" /></button></div>
    </>
  );
}

// ------------------------------------------------------------------ 1. ta banque
function BankStep({ done, result, onDone }: { done: boolean; result: string | null; onDone: () => void }) {
  const [path, setPath] = useState<StartPath | null>(result === "refused" || result === "error" ? "bank" : null);
  const [stage, setStage] = useState<"paths" | "sync" | "linked">(result === "connected" ? "sync" : "paths");
  const backError = result === "refused" ? "Connexion annulée côté banque. Tu peux réessayer, ou choisir une autre banque."
    : result === "error" ? "La connexion a échoué ou a expiré : recommence." : null;

  if (stage === "sync") return <Syncing onDone={() => setStage("linked")} />;
  if (stage === "linked") return <BankDone onAnother={() => { setStage("paths"); setPath("bank"); }} onFinish={onDone} />;
  if (done && path === null) return <Already text="Ton argent est déjà dans Bank : rien à faire ici." onDone={onDone} />;
  return (
    <div className="stack journey-stack">
      {backError && <div className="notice danger" role="alert"><Icon name="alert" /><span>{backError}</span></div>}
      <StartPaths chosen={path} onChoose={setPath} />
      {path === "bank" && <BankLink />}
      {path === "import" && (
        <div className="panel journey-panel">
          <p>Télécharge un relevé CSV ou OFX dans ton espace bancaire, puis dépose-le dans Opérations. Tu reviendras ici depuis « Bien démarrer », sur Aujourd'hui.</p>
          <div className="actions journey-actions"><a className="btn lg" href="#/operations?import=1"><Icon name="upload" /> Importer mon relevé</a></div>
        </div>
      )}
      {path === "figures" && <ThreeFigures onDone={onDone} submitLabel="Enregistrer ces chiffres" />}
      <p className="faint"><Icon name="shield" /> Lecture seule, toujours. Tu coupes l'accès quand tu veux, dans Connecteurs ou chez ta banque.</p>
    </div>
  );
}

function BankLink() {
  const status = useApi<{ enabled: boolean; connections: Connection[] }>("/api/banks");
  const [aspsps, setAspsps] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (status.data?.enabled && ready && aspsps === null) {
      api<{ name: string }[]>("/api/banks/aspsps?country=FR").then((list) => setAspsps(list.map((a) => a.name))).catch((e: Error) => setError(e.message));
    }
  }, [status.data, ready, aspsps]);
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
  if (!status.data) return status.error ? <p className="error" role="alert">{status.error}</p> : <Loading what="Ta banque" />;
  return (
    <div className="journey-bank">
      <p className="muted">Tu t'authentifies chez ta banque (DSP2, via Enable Banking, établissement agréé) : Bank ne voit jamais tes identifiants. Mise à jour automatique ensuite, 90 jours renouvelables.</p>
      {error && <div className="notice danger" role="alert"><Icon name="alert" /><span>{error}</span></div>}
      <BankSetup
        onChange={(s) => {
          setReady(setupReady(s));
          if (s.configured !== status.data?.enabled) status.reload();
        }}
        chooser={<BankConnect aspsps={aspsps} connections={status.data.connections} busy={busy} failed={failed} onConnect={(n) => void connect(n)} />}
      />
    </div>
  );
}

// ------------------------------------------------------------------ 2. ton foyer
const CHIPS = new Set(["household", "housing_status"]);

function HouseholdStep({ onDone }: { onDone: () => void }) {
  const ob = useApi<OnboardingResponse>("/api/onboarding");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, error, run] = useAsyncAction();
  const [invalid, setInvalid] = useState<string | null>(null);
  const loaded = useRef(false);

  // Pré-remplissage : ta réponse si tu en as une, sinon ce que Bank a lu dans tes relevés.
  useEffect(() => {
    if (!ob.data || loaded.current) return;
    loaded.current = true;
    const v: Record<string, string> = {};
    for (const id of FIELDS) {
      const stored = ob.data.answers[id]?.value;
      const guess = ob.data.suggestions.find((s) => s.question_id === id)?.value;
      const raw = stored ?? guess;
      if (raw === undefined || raw === null) continue;
      v[id] = MONEY.has(id) && typeof raw === "number" ? String(raw / 100).replace(".", ",") : String(raw);
    }
    setValues(v);
  }, [ob.data]);

  if (ob.error) return <p className="error" role="alert">{ob.error}</p>;
  if (!ob.data) return <Loading what="Tes réponses" />;
  const questions = Object.fromEntries(ob.data.questions.map((q) => [q.id, q]));
  const guessOf = (id: string) => (ob.data!.answers[id] ? null : ob.data!.suggestions.find((s) => s.question_id === id) ?? null);
  const set = (id: string, v: string) => setValues((cur) => ({ ...cur, [id]: v }));
  const rent = values.housing_status === "locataire" || values.housing_status === "proprietaire_credit";

  const save = (e: FormEvent) => {
    e.preventDefault();
    const out: [string, unknown][] = [];
    for (const id of FIELDS) {
      if (id === "housing_cost" && !rent) continue;
      const raw = (values[id] ?? "").trim();
      if (raw === "") continue;
      if (MONEY.has(id)) {
        const cents = parseEuros(raw);
        if (cents === null || cents < 0) return setInvalid(`Vérifie le montant « ${raw} » : par exemple 1 900 ou 650.`);
        out.push([id, cents]);
      } else if (id === "dependents") {
        const n = Number(raw);
        if (!Number.isInteger(n) || n < 0 || n > 12) return setInvalid("Personnes à charge : un nombre entre 0 et 12.");
        out.push([id, n]);
      } else out.push([id, raw]);
    }
    const missing = REQUIRED.filter((id) => !out.some(([k]) => k === id)).map((id) => SHORT[id]);
    if (missing.length) return setInvalid(`Il manque : ${missing.join(", ")}. Ou passe cette étape, tu pourras finir dans Ma situation.`);
    setInvalid(null);
    void run(async () => {
      for (const [id, value] of out) {
        const stored = ob.data!.answers[id];
        if (stored && JSON.stringify(stored.value) === JSON.stringify(value)) continue;
        await api(`/api/onboarding/${id}`, { method: "PUT", body: { value } });
      }
      onDone();
    });
  };

  return (
    <form className="stack journey-stack journey-form" onSubmit={save} aria-label="Ton foyer" noValidate>
      {FIELDS.filter((id) => id !== "housing_cost" || rent).map((id) => {
        const q = questions[id];
        if (!q) return null;
        const guess = guessOf(id);
        const shown = guess === null ? "" : MONEY.has(id) && typeof guess.value === "number" ? money(guess.value, { whole: true })
          : q.options.find((o) => o.value === String(guess.value))?.label.toLowerCase() ?? String(guess.value);
        const hint = guess ? `Lu dans tes relevés : ${shown} — c'est juste ?` : null;
        if (CHIPS.has(id)) {
          return (
            <fieldset key={id} className="journey-field">
              <legend>{q.question}</legend>
              <div className="chips">
                {q.options.map((o) => (
                  <button key={o.value} type="button" className="chip" aria-pressed={values[id] === o.value} onClick={() => set(id, o.value)}>{o.label}</button>
                ))}
              </div>
              {hint && <p className="faint">{hint}</p>}
            </fieldset>
          );
        }
        return (
          <label key={id} className="field journey-field">
            {q.question}
            {q.kind === "choice" ? (
              <select value={values[id] ?? ""} onChange={(e) => set(id, e.target.value)}>
                <option value="">Choisis…</option>
                {q.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            ) : MONEY.has(id) ? (
              <span className="euro-field"><input inputMode="decimal" value={values[id] ?? ""} onChange={(e) => set(id, e.target.value)} /><span aria-hidden="true">€</span></span>
            ) : (
              <input inputMode="numeric" value={values[id] ?? ""} onChange={(e) => set(id, e.target.value)} />
            )}
            {hint && <span className="faint">{hint}</span>}
          </label>
        );
      })}
      {invalid && <p className="error" role="alert">{invalid}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions journey-actions">
        <button className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer mon foyer</button>
      </div>
      <Address />
    </form>
  );
}
const FIELDS = ["employment", "net_monthly_income", "household", "dependents", "housing_status", "housing_cost"];
const REQUIRED = ["employment", "net_monthly_income", "household", "dependents", "housing_status"];
const MONEY = new Set(["net_monthly_income", "housing_cost"]);
const SHORT: Record<string, string> = {
  employment: "ta situation pro",
  net_monthly_income: "ton revenu",
  household: "ton foyer",
  dependents: "les personnes à charge",
  housing_status: "ton logement",
};

/** En option : ta commune ou ton adresse, avec l'accord « géocodage » juste à côté, décoché. */
function Address() {
  const view = useApi<{ consents: { geocoding: boolean }; place: { label: string | null } | null }>("/api/territory");
  const consents = useApi<ConsentView[]>("/api/consents");
  const [query, setQuery] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const geo = consents.data?.find((c) => c.purpose === "geocoding");
  if (!view.data || !geo) return null;
  const toggle = () =>
    void run(async () => {
      await api("/api/consents/geocoding", { method: "PUT", body: { granted: !view.data!.consents.geocoding } });
      view.reload();
      consents.reload();
    });
  const locate = () =>
    void run(async () => {
      await api("/api/territory/geocode", { method: "POST", body: { query } });
      setQuery("");
      toast("Adresse située : ta commune sert de point de départ.", "success");
      view.reload();
    });
  return (
    <details className="journey-extra">
      <summary>Ta commune ou ton adresse <span className="faint">(facultatif)</span></summary>
      <ConsentGroups consents={[{ ...geo, granted: view.data.consents.geocoding }]} groups={["adresse"]} busy={busy} onToggle={toggle} />
      {view.data.place?.label && <p><Icon name="pin" /> <b>{view.data.place.label}</b></p>}
      {view.data.consents.geocoding && (
        <div className="form-grid">
          <label className="field">
            {view.data.place ? "Changer d'adresse" : "Ton adresse, ou seulement ta commune"}
            <input value={query} minLength={3} maxLength={200} autoComplete="street-address" onChange={(e) => setQuery(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (query.trim().length >= 3) locate(); } }} />
          </label>
          <div className="actions"><button type="button" className="btn quiet" disabled={busy || query.trim().length < 3} onClick={locate}>Situer</button></div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </details>
  );
}

// ------------------------------------------------------------------ 3. tes accords
function ConsentsStep({ data, done, onDone, reload }: { data: StartView; done: boolean; onDone: () => void; reload: (v: StartView) => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const list = data.public_consents;
  const toggle = (purpose: string, granted: boolean) =>
    void run(async () => {
      await api(`/api/consents/${purpose}`, { method: "PUT", body: { granted: !granted } });
      reload(await api<StartView>("/api/start"));
    });
  const grantAll = () =>
    void run(async () => {
      const fresh = list.filter((c) => !(c.granted && c.decided)).length;
      await api("/api/start/public-consents", { method: "POST", body: { purposes: list.map((c) => c.purpose) } });
      toast(`${plural(fresh || list.length, "accord donné", "accords donnés")} — modifiables dans Réglages › Confidentialité.`, "success");
      onDone();
    });
  return (
    <div className="stack journey-stack">
      <ConsentGroups consents={list} groups={["public", "zone"]} busy={busy} onToggle={(c) => toggle(c.purpose, c.granted)} />
      <p className="faint journey-note">
        Ton adresse et tes contenus (messageries, mail, assistant, Linky…) se demandent plus tard, au moment où tu relies le service.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="journey-note">Un clic accorde ces {list.length} accords, et eux seuls. Tu peux les retirer à tout moment dans Réglages › Confidentialité.</p>
      <div className="actions journey-actions">
        {done ? (
          <>
            <button type="button" className="btn lg" disabled={busy} onClick={onDone}>Continuer avec ces choix <Icon name="arrow" /></button>
            {list.some((c) => !c.granted) && <button type="button" className="btn quiet lg" disabled={busy} onClick={grantAll}>Tout activer pour les données publiques</button>}
          </>
        ) : (
          <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy} onClick={grantAll}>Tout activer pour les données publiques</button>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 4. ton premier papier
const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf";

function PaperStep({ done, onDone }: { done: boolean; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [skipped, setSkipped] = useState<VaultUpload["skipped"]>([]);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const send = (list: FileList | null) => {
    const chosen = Array.from(list ?? []).slice(0, 10);
    if (chosen.length === 0) return;
    void run(async () => {
      const form = new FormData();
      chosen.forEach((f) => form.append("files", f, f.name));
      form.append("source", "upload");
      const r = await api<VaultUpload>("/api/vault/upload", { method: "POST", form });
      setSkipped(r.skipped);
      if (r.documents.length) {
        toast(`${plural(r.documents.length, "document déposé", "documents déposés")} : Bank le lit.`, "success");
        onDone();
      }
    });
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    send(e.dataTransfer.files);
  };
  return (
    <div className="stack journey-stack">
      {done && <p className="notice ok"><Icon name="check" /><span>Tu as déjà rangé un papier. Un autre ? Dépose-le ici.</span></p>}
      <div className={`vault-drop journey-drop${dragging ? " is-over" : ""}`}
           onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <Icon name="camera" />
        <p><b>Une photo ou un PDF</b> <span className="faint">— Bank le lit et garde les chiffres utiles, chiffrés chez toi.</span></p>
        <ul className="journey-ideas" aria-label="Par exemple">
          <li className="tag">Avis d'impôt</li>
          <li className="tag">Bulletin de paie</li>
          <li className="tag">Attestation CAF</li>
        </ul>
      </div>
      <input ref={input} type="file" accept={ACCEPT} multiple hidden aria-label="Photo ou fichier à déposer"
             onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      {skipped.length > 0 && (
        <div className="notice warn" role="status"><Icon name="alert" />
          <ul className="vault-skipped">{skipped.map((s, i) => <li key={i}><b>{s.filename}</b> : {s.reason}</li>)}</ul>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions journey-actions">
        <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => input.current?.click()}>
          <Icon name="camera" /> Prendre en photo ou déposer
        </button>
        {done && <button type="button" className="btn quiet lg" onClick={onDone}>Continuer</button>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 5. tes priorités
export const PRIORITIES: { key: OpportunityCategory; label: string; hint: string }[] = [
  { key: "recuperer", label: "Récupérer ce à quoi j'ai droit", hint: "Aides non demandées, frais injustifiés" },
  { key: "reduire", label: "Payer moins", hint: "Abonnements oubliés, frais bancaires" },
  { key: "revenus", label: "Gagner un peu plus", hint: "Prime d'activité, droits liés à ton salaire" },
  { key: "epargne", label: "Mettre de côté", hint: "Le bon livret, l'argent qui dort" },
  { key: "anticiper", label: "Ne plus être pris de court", hint: "Découvert, papiers qui expirent" },
];

function PrioritiesStep({ onDone }: { onDone: () => void }) {
  const top = useApi<{ opportunities: Opportunity[]; priorities?: OpportunityCategory[] }>("/api/opportunities/top");
  const [chosen, setChosen] = useState<Set<OpportunityCategory> | null>(null);
  const [busy, error, run] = useAsyncAction();
  const [invalid, setInvalid] = useState(false);
  const picked = chosen ?? new Set(top.data?.priorities ?? []);
  const toggle = (k: OpportunityCategory) => {
    const next = new Set(picked);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setChosen(next);
    setInvalid(false);
  };
  const save = () => {
    if (picked.size === 0) return setInvalid(true);
    void run(async () => {
      await api("/api/opportunities/settings", { method: "PUT", body: { priorities: [...picked] } });
      onDone();
    });
  };
  const leads = (top.data?.opportunities ?? []).slice(0, 3);
  return (
    <div className="stack journey-stack">
      <div className="goals-grid" role="group" aria-label="Tes priorités">
        {PRIORITIES.map((g) => (
          <button key={g.key} type="button" className="goal" aria-pressed={picked.has(g.key)} onClick={() => toggle(g.key)}>
            <b>{g.label}</b>
            <span className="faint">{g.hint}</span>
          </button>
        ))}
      </div>
      {leads.length > 0 && (
        <section aria-labelledby="journey-leads">
          <h2 id="journey-leads" className="goals-h2">Tes premières pistes</h2>
          <ul className="copilot-list">
            {leads.map((o) => (
              <li key={o.key}><Icon name="target" /><span>{o.title}</span>{o.amount_cents > 0 && <span className="copilot-amount">{perPeriod(o.amount_cents, o.period)}</span>}</li>
            ))}
          </ul>
        </section>
      )}
      {invalid && <p className="error" role="alert">Choisis au moins une priorité, ou passe cette étape.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions journey-actions">
        <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy} onClick={save}>Garder ces priorités</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 6. où te prévenir
function NotifyStep({ done, onDone }: { done: boolean; onDone: () => void }) {
  const st = useApi<PushStatus>("/api/push");
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const supported = pushSupported();
  const permission = pushPermission();
  const push = consents.data?.find((c) => c.purpose === "push");
  const activate = () =>
    void run(async () => {
      if (!st.data) return;
      // Le texte de l'accord est juste au-dessus : ce clic-là est l'accord, explicite.
      if (!st.data.consent) await api("/api/consents/push", { method: "PUT", body: { granted: true } });
      const device = await enablePush(st.data.public_key);
      toast(`Notifications activées sur « ${device.label} ».`, "success");
      onDone();
    });
  return (
    <div className="stack journey-stack">
      {done && <p className="notice ok"><Icon name="check" /><span>Bank sait déjà où te prévenir. Tu peux ajouter un appareil ou continuer.</span></p>}
      <div className="journey-choices">
        <section className="panel journey-choice" aria-labelledby="notify-phone">
          <h2 id="notify-phone"><Icon name="bell" /> Sur ce téléphone</h2>
          {push?.sends && <p className="consent-sends"><span>Envoie :</span> {push.sends}</p>}
          {push && <details className="consent-more"><summary>Lire le texte complet</summary><p className="muted">{push.text}</p></details>}
          {!supported && <p className="notice"><Icon name="alert" /><span>Ce navigateur ne sait pas recevoir de notifications. Sur iPhone : ajoute Bank à l'écran d'accueil, puis reviens ici.</span></p>}
          {supported && permission === "denied" && <p className="notice"><Icon name="alert" /><span>Les notifications sont bloquées pour Bank : réautorise-les dans les réglages du site.</span></p>}
          {supported && permission !== "denied" && (
            <div className="actions journey-actions">
              <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy || !st.data} onClick={activate}>
                <Icon name="bell" /> Activer les notifications
              </button>
            </div>
          )}
        </section>
        <section className="panel journey-choice" aria-labelledby="notify-chat">
          <h2 id="notify-chat"><Icon name="chat" /> Dans ta messagerie</h2>
          <p className="muted">WhatsApp par QR code, Telegram, Signal… Bank t'y écrit ses alertes, et tu lui réponds.</p>
          <div className="actions"><a className="btn quiet" href="#/messageries">Ouvrir Messageries <Icon name="arrow" /></a></div>
        </section>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {done && <div className="actions journey-actions"><button type="button" className="btn quiet lg" onClick={onDone}>Continuer</button></div>}
    </div>
  );
}

