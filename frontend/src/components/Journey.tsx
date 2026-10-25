// « Démarrer avec Bank » (DESIGN.md § 3.10) : le parcours plein écran de la première ouverture. Sept étapes, une
// action principale chacune, « Passer » toujours là ; on le quitte (Échap ou « Quitter ») et on le reprend où on l'a
// laissé, sur n'importe quel appareil : fait, passé et l'étape en cours viennent du serveur (/api/start).
// Les étapes réutilisent ce qui existe (chemins de Welcome, assistant Enable Banking, notifications, coffre) ; la
// seule fête est celle de la fin, et seulement si au moins une étape a été faite pendant ce passage (la visite seule
// ne compte pas) ; elle dit ce qui a été fait et ce qui reste, jamais « tout est prêt » à la place de l'anneau.
// Pas de toast dans le parcours : il couvrirait les boutons de l'étape suivante au téléphone. La confirmation de
// l'étape faite ou passée s'affiche en haut de la suivante, et son titre (qui reçoit le focus) la décrit.
// Mouvement (§ 3.7) : chaque changement d'étape glisse dans le sens du parcours (View Transitions quand le navigateur
// les connaît, entrée CSS sinon), chaque étape a sa petite scène animée, et la fin est un écran de fête dans la carte.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type DragEvent, type FormEvent, type MouseEvent, type ReactNode } from "react";
import {
  api,
  useApi,
  type ConsentView,
  type Connection,
  type OnboardingResponse,
  type Opportunity,
  type OpportunityCategory,
  type PublicConsent,
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
import { useCountUp, viewTransition } from "../motion";
import { Confetti, FinaleBadge, JourneySky, StepArt } from "./JourneyArt";
import { Ring } from "./Ring";
import { Tour } from "./Tour";
import { Loading, useAsyncAction } from "./ui";
import { isPhone, StartPaths, ThreeFigures, useBankAvailability, type StartPath } from "./Welcome";

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
  // Ce que l'étape précédente a donné (« Étape faite : Ton foyer. »), montré en haut de l'étape affichée.
  // Gardé avec l'étape qui doit le montrer : en sautant ailleurs par la barre, il ne traîne pas.
  const [flashed, setFlashed] = useState<{ on: StartStepKey; text: string; skipped?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doneNow = useRef(new Set<StartStepKey>()); // étapes faites pendant ce passage (hors visite)
  const heading = useRef<HTMLHeadingElement>(null);
  // La fin, quand il y a lieu : l'état renvoyé par le serveur et ce qui a été fait pendant ce passage.
  const [finale, setFinale] = useState<{ view: StartView; doneNow: number } | null>(null);
  // Le changement d'étape en cours dans une transition de vue : on la libère quand la nouvelle étape est rendue.
  const transitionReady = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (initial.data) setData(initial.data);
  }, [initial.data]);

  const key: StartStepKey | null = isStepKey(stepKey) ? stepKey
    : data ? data.journey.current ?? data.journey.steps.find((s) => !s.done && !s.skipped)?.key ?? "banque" : null;
  const go = useCallback((k: StartStepKey) => {
    const target = `#/bienvenue/${k}`;
    if (window.location.hash === target) return;
    // Le sens du glissement : en avant vers une étape plus loin dans l'ordre, en arrière sinon.
    const from = key ? ORDER.indexOf(key) : -1;
    document.documentElement.dataset.journeyDir = ORDER.indexOf(k) >= from ? "forward" : "back";
    // On remonte d'abord : l'étape qui part glisse depuis son titre, pas depuis le bas d'un formulaire.
    if (window.scrollY > 0) window.scrollTo?.({ top: 0, behavior: "instant" });
    viewTransition((ready) => {
      transitionReady.current = ready;
      window.location.hash = target;
    });
  }, [key]);
  useLayoutEffect(() => {
    transitionReady.current?.();
    transitionReady.current = null;
  }, [key, !!finale]);
  useEffect(() => () => { delete document.documentElement.dataset.journeyDir; }, []);

  // Le sens de l'entrée CSS (repli sans View Transitions) : on compare l'étape affichée à la précédente.
  const index = key ? ORDER.indexOf(key) : -1;
  const [seen, setSeen] = useState(index);
  const [dir, setDir] = useState<"forward" | "back">("forward");
  if (seen !== index) {
    if (seen !== -1 && index !== -1) setDir(index > seen ? "forward" : "back");
    setSeen(index);
  }

  // L'adresse dit toujours l'étape (reprise, lien profond) ; le serveur retient l'étape en cours.
  useEffect(() => {
    if (!key || !data) return;
    if (!isStepKey(stepKey)) window.history.replaceState(null, "", `#/bienvenue/${key}`);
    api<StartView>(`/api/start/steps/${key}`, { method: "POST", body: { action: "current" } }).catch(() => undefined);
  }, [key, !!data]); // eslint-disable-line -- une fois par étape affichée

  const step = data?.journey.steps.find((s) => s.key === key) ?? null;
  // Focus sur le titre de chaque étape (et de la fin), et le titre de l'onglet suit.
  useEffect(() => {
    if (!step || finale) return;
    document.title = `${step.title} · Bank`;
    window.scrollTo?.({ top: 0, behavior: "instant" }); // la nouvelle étape commence en haut, avec sa confirmation
    heading.current?.focus({ preventScroll: true });
  }, [step?.key, !!finale]); // eslint-disable-line -- à chaque nouvelle étape seulement
  useEffect(() => () => { document.title = "Bank"; }, []);

  const quit = useCallback(() => onExit(), [onExit]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Échap dans un champ, une liste ou un volet déplié sert à ce champ (ou au lecteur d'écran), pas à quitter.
      if (e.target instanceof Element && e.target.closest(FIELD_LIKE)) return;
      quit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [quit]);

  const finish = async (tourDone: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const view = await api<StartView>("/api/start/finish", { method: "POST", body: { tour_done: tourDone } });
      // La fête n'a lieu que si quelque chose a été fait pendant ce passage ; sinon on rentre, simplement.
      if (doneNow.current.size === 0) return onExit();
      const doneCount = doneNow.current.size;
      document.documentElement.dataset.journeyDir = "forward";
      viewTransition((ready) => {
        transitionReady.current = ready;
        setFinale({ view, doneNow: doneCount });
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** Le serveur a confirmé : on relit l'état (coche verte sur le segment), puis l'étape suivante. */
  const completed = async (detail?: string) => {
    if (!key) return;
    setError(null);
    try {
      const next = await api<StartView>("/api/start");
      setData(next);
      const nowDone = next.journey.steps.find((s) => s.key === key)?.done ?? false;
      if (nowDone && key !== "visite") doneNow.current.add(key);
      const text = nowDone ? `Étape faite : ${step?.title ?? ""}.${detail ? ` ${detail}` : ""}` : detail;
      const target = next.journey.current && next.journey.current !== key ? next.journey.current : nextAfter(next, key);
      setFlashed(target && text ? { on: target, text } : null);
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
      const target = next.journey.current ?? nextAfter(next, key);
      setFlashed(target ? { on: target, text: `Étape passée : ${step?.title ?? ""}. Elle t'attend dans « Bien démarrer », sur Aujourd'hui.`, skipped: true } : null);
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
  if (!data || !key || !step) return <div className="journey"><JourneySky /><div className="journey-body"><Loading what="Ton parcours" /></div></div>;
  if (finale) return <Finale view={finale.view} doneNow={finale.doneNow} onExit={quit} />;

  const flash = flashed?.on === key ? flashed.text : null;
  const flashSkipped = flashed?.on === key && !!flashed.skipped;
  const copy = COPY[key];
  return (
    <div className="journey">
      <JourneySky />
      <header className="journey-top">
        <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
        <p className="journey-count">Étape <span className="journey-count-n" key={index}>{index + 1}</span> sur {ORDER.length}</p>
        <button type="button" className="btn ghost sm journey-quit" onClick={quit}>Quitter <Icon name="close" /></button>
      </header>
      <ol className="journey-progress" aria-label="Les étapes du parcours">
        {data.journey.steps.map((s) => (
          <li key={s.key} className={s.done ? "is-done" : s.skipped ? "is-skipped" : ""} aria-current={s.key === key ? "step" : undefined}>
            <a href={`#/bienvenue/${s.key}`} onClick={(e: MouseEvent) => { if (!e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); go(s.key); } }}>
              <span className="journey-seg" aria-hidden="true"><i className="journey-fill" />{s.done && <Icon name="check" size={12} />}</span>
              <span className="sr-only">{s.title}{s.done ? ", faite" : s.skipped ? ", passée" : ""}</span>
            </a>
          </li>
        ))}
      </ol>
      <StepFrame key={key} dir={dir}>
        <section className="card journey-card" aria-labelledby="journey-title">
          {/* Le titre reçoit le focus et se décrit par cette ligne : lue d'un trait, jamais coupée par une annonce. */}
          {flash && <p className={`journey-flash${flashSkipped ? " is-skipped" : ""}`} id="journey-flash"><Icon name={flashSkipped ? "arrow" : "check"} size={14} /> {flash}</p>}
          <div className="journey-head">
            <div className="journey-head-text">
              <p className="journey-kicker"><Icon name="clock" size={14} /> {copy.time}{step.done ? " · déjà fait" : ""}</p>
              <h1 id="journey-title" ref={heading} tabIndex={-1} aria-describedby={flash ? "journey-flash" : undefined}>{step.title}</h1>
            </div>
            <StepArt step={key} />
          </div>
          <p className="journey-goal">{copy.goal}</p>
          {error && <p className="error" role="alert">{error}</p>}
          <StepBody k={key} data={data} done={step.done} bankResult={bankResult} onDone={(d) => void completed(d)}
                    onSkip={() => void skip()} onFinishTour={() => void finish(true)} busy={busy} reload={(v) => setData(v)} />
        </section>
        {/* Une étape déjà faite n'a rien à passer : « Continuer » suffit. La visite porte son propre « Passer ». */}
        {!step.done && !ownSkip(key) && (
          <div className="journey-skip">
            <button type="button" className="btn ghost" disabled={busy} onClick={() => void skip()}>{copy.skip}</button>
          </div>
        )}
      </StepFrame>
    </div>
  );
}

/**
 * Le cadre d'une étape. Son entrée CSS (glissement du côté où l'on va) ne joue que s'il n'est pas né dans une
 * transition de vue — décidé une fois, à sa naissance : la fin de la transition ne la rejoue pas.
 */
function StepFrame({ dir, children }: { dir: "forward" | "back"; children: ReactNode }) {
  const [inTransition] = useState(() => document.documentElement.classList.contains("is-view-transition"));
  return <main className={`journey-body${inTransition ? "" : ` is-${dir}`}`}>{children}</main>;
}

/**
 * La fin du parcours, quand au moins une étape a été faite pendant ce passage : un badge qui « pope », des
 * confettis dans la carte, et ce qui est réglé, compté sous tes yeux. « Bank est prêt » seulement à 8 sur 8.
 */
function Finale({ view, doneNow, onExit }: { view: StartView; doneNow: number; onExit: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const steps = view.journey.steps.filter((s) => s.key !== "visite");
  const cl = view.checklist;
  const ready = cl.total > 0 && cl.done >= cl.total;
  const left = steps.filter((s) => !s.done).length;
  const title = ready ? "Bank est prêt." : left === 0 ? "Parcours terminé !" : "C'est parti !";
  const counted = useCountUp(cl.done, { duration: 1100, delay: 250 });
  useEffect(() => {
    document.title = `${title} · Bank`;
    window.scrollTo?.({ top: 0, behavior: "instant" });
    heading.current?.focus({ preventScroll: true });
  }, [title]);
  return (
    <div className="journey is-finale">
      <JourneySky />
      <header className="journey-top">
        <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
      </header>
      <StepFrame dir="forward">
        <section className="card journey-card journey-finale" aria-labelledby="journey-title">
          <Confetti />
          <FinaleBadge />
          <h1 id="journey-title" ref={heading} tabIndex={-1} aria-describedby="finale-message">{title}</h1>
          <p className="journey-goal" id="finale-message">{finishMessage(view, doneNow)}</p>
          <div className="finale-count">
            <Ring value={cl.done} max={cl.total} label={`${cl.done} éléments sur ${cl.total} faits`} size="lg">
              {counted}<small>/{cl.total}</small>
            </Ring>
            <p><b>{plural(cl.done, "réglage fait", "réglages faits")}</b> sur {cl.total} dans « Bien démarrer ».<br />
              <span className="muted">La liste t'attend sur Aujourd'hui pour la suite.</span></p>
          </div>
          <ul className="finale-list" aria-label="Les étapes du parcours">
            {steps.map((s) => (
              <li key={s.key} className={s.done ? "is-done" : undefined}>
                <span className="finale-tick" aria-hidden="true">{s.done ? <Icon name="check" size={14} /> : <Icon name="clock" size={14} />}</span>
                <span>{s.title}</span>
                <span className="faint">{s.done ? "fait" : "plus tard"}</span>
              </li>
            ))}
          </ul>
          <div className="actions journey-actions">
            <button type="button" className="btn lg" onClick={onExit}>Voir mon tableau de bord</button>
          </div>
        </section>
      </StepFrame>
    </div>
  );
}

/** La visite porte son « Passer » ; « Où te prévenir » aussi quand rien n'y est activable (il devient l'action). */
function ownSkip(k: StartStepKey): boolean {
  return k === "visite" || (k === "prevenir" && !(pushSupported() && pushPermission() !== "denied"));
}

const FIELD_LIKE = "input, select, textarea, [contenteditable='true'], [role='listbox'], details[open]";

/** La fête de fin, s'il y a lieu : ce qui a été fait pendant ce passage, et ce qui reste (sans la visite). */
export function finishMessage(view: StartView, doneNow: number): string | null {
  if (doneNow === 0) return null;
  const left = view.journey.steps.filter((s) => s.key !== "visite" && !s.done).length;
  const made = plural(doneNow, "étape faite", "étapes faites");
  return left === 0 ? `${made} : le parcours est complet.` : `${made} — il en reste ${left}, quand tu veux.`;
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

type OnDone = (detail?: string) => void;

function StepBody({ k, data, done, bankResult, onDone, onSkip, onFinishTour, busy, reload }: {
  k: StartStepKey;
  data: StartView;
  done: boolean;
  bankResult: string | null;
  onDone: OnDone;
  onSkip: () => void;
  onFinishTour: () => void;
  busy: boolean;
  reload: (v: StartView) => void;
}) {
  switch (k) {
    case "banque": return <BankStep done={done} result={bankResult} onDone={onDone} onLater={onSkip} />;
    case "foyer": return <HouseholdStep onDone={onDone} />;
    case "accords": return <ConsentsStep data={data} done={done} onDone={onDone} reload={reload} />;
    case "papier": return <PaperStep done={done} onDone={onDone} />;
    case "priorites": return <PrioritiesStep onDone={onDone} />;
    case "prevenir": return <NotifyStep done={done} onDone={onDone} onLater={onSkip} />;
    case "visite": return <Tour onFinish={onFinishTour} onSkip={onSkip} skipLabel={COPY.visite.skip} busy={busy} />;
  }
}

function Already({ text, onDone }: { text: string; onDone: OnDone }) {
  return (
    <>
      <p className="notice ok"><Icon name="check" /><span>{text}</span></p>
      <div className="actions journey-actions"><button type="button" className="btn lg" onClick={() => onDone()}>Continuer <Icon name="arrow" /></button></div>
    </>
  );
}

// ------------------------------------------------------------------ 1. ta banque
function BankStep({ done, result, onDone, onLater }: { done: boolean; result: string | null; onDone: OnDone; onLater: () => void }) {
  const [path, setPath] = useState<StartPath | null>(result === "refused" || result === "error" ? "bank" : null);
  const [stage, setStage] = useState<"paths" | "sync" | "linked">(result === "connected" ? "sync" : "paths");
  const bank = useBankAvailability();
  const phone = isPhone();
  const panel = useRef<HTMLDivElement>(null);
  const chosenByHand = useRef(false);
  const backError = result === "refused" ? "Connexion annulée côté banque. Tu peux réessayer, ou choisir une autre banque."
    : result === "error" ? "La connexion a échoué ou a expiré : recommence." : null;

  // Au téléphone, ce qu'ouvre la carte s'affiche sous les trois cartes, hors de vue : on y va, et le focus suit
  // (sauf s'il est déjà dans le volet, sur le premier champ des trois chiffres).
  useEffect(() => {
    if (!path || !chosenByHand.current || !panel.current) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    panel.current.scrollIntoView?.({ behavior: reduced ? "auto" : "smooth", block: "start" });
    if (!panel.current.contains(document.activeElement)) panel.current.focus({ preventScroll: true });
  }, [path]);
  const choose = (p: StartPath) => {
    chosenByHand.current = true;
    setPath(p);
  };

  if (stage === "sync") return <Syncing onDone={() => setStage("linked")} />;
  if (stage === "linked") return <BankDone onAnother={() => { setStage("paths"); setPath("bank"); }} onFinish={() => onDone()} />;
  if (done && path === null) return <Already text="Ton argent est déjà dans Bank : rien à faire ici." onDone={onDone} />;
  return (
    <div className="stack journey-stack">
      {backError && <div className="notice danger" role="alert"><Icon name="alert" /><span>{backError}</span></div>}
      <StartPaths chosen={path} onChoose={choose} bank={bank} phone={phone} />
      {path && (
        <div ref={panel} tabIndex={-1} className="journey-reveal" role="region" aria-label={PATH_LABEL[path]}>
          {path === "bank" && <BankLink phone={phone} onLater={onLater} />}
          {path === "import" && (
            <div className="panel journey-panel">
              <p>Télécharge un relevé CSV ou OFX dans ton espace bancaire, puis dépose-le dans Opérations. Un bouton te ramène ensuite ici.</p>
              <div className="actions journey-actions"><a className="btn lg" href={`#/operations?import=1&from=${RETURN}banque`}><Icon name="upload" /> Importer mon relevé</a></div>
            </div>
          )}
          {path === "figures" && <ThreeFigures onDone={() => onDone("Ton premier budget est prêt.")} submitLabel="Enregistrer ces chiffres" />}
        </div>
      )}
      <p className="faint"><Icon name="shield" /> Lecture seule, toujours. Tu coupes l'accès quand tu veux, dans Connecteurs ou chez ta banque.</p>
    </div>
  );
}
const PATH_LABEL: Record<StartPath, string> = { bank: "Connecter ma banque", import: "Importer un relevé", figures: "Juste trois chiffres" };
/** Paramètre des liens qui sortent du parcours : la page d'arrivée montre « Revenir au parcours ». */
const RETURN = "bienvenue/";

function BankLink({ phone, onLater }: { phone: boolean; onLater: () => void }) {
  const status = useApi<{ enabled: boolean; connections: Connection[] }>("/api/banks");
  const [configure, setConfigure] = useState(false);
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
  // Enable Banking pas encore configuré : on dit ce que ça demande avant d'ouvrir l'assistant en six étapes.
  if (!status.data.enabled && !configure) {
    return (
      <div className="panel journey-panel">
        <p><b>Avant ta banque, un compte chez Enable Banking.</b> C'est gratuit, mais il faut y créer une application, télécharger un fichier de clé puis le déposer ici : une quinzaine de minutes, une seule fois.</p>
        <p className="muted">{phone ? "Sur téléphone, c'est pénible : fais-le plutôt sur ordinateur. « Relier ta banque » reste dans « Bien démarrer »." : "L'assistant te guide pas à pas, avec des liens directs."}</p>
        <div className="actions journey-actions">
          {phone ? (
            <>
              <button type="button" className="btn lg" onClick={onLater}>Je le ferai sur ordinateur</button>
              <button type="button" className="btn quiet lg" onClick={() => setConfigure(true)}>Configurer quand même</button>
            </>
          ) : (
            <button type="button" className="btn lg" onClick={() => setConfigure(true)}>Configurer Enable Banking <Icon name="arrow" /></button>
          )}
        </div>
      </div>
    );
  }
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

function HouseholdStep({ onDone }: { onDone: OnDone }) {
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
      onDone("Bank peut simuler tes droits.");
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
          // Un seul choix : de vrais boutons radio (flèches au clavier, « 1 sur 3 » au lecteur d'écran), en pastilles.
          // Une valeur devinée est présélectionnée ; la ligne « Lu dans tes relevés » le dit, liée au groupe.
          return (
            <fieldset key={id} className="journey-field" aria-describedby={hint ? `hint-${id}` : undefined}>
              <legend>{q.question}</legend>
              <div className="chips">
                {q.options.map((o) => (
                  <label key={o.value} className="chip chip-radio">
                    <input type="radio" className="sr-only" name={id} value={o.value} checked={values[id] === o.value} onChange={() => set(id, o.value)} />
                    {o.label}
                  </label>
                ))}
              </div>
              {hint && <p className="faint" id={`hint-${id}`}>{hint}</p>}
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
  const [located, setLocated] = useState(false);
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
      setLocated(true);
      view.reload();
    });
  return (
    <details className="journey-extra">
      <summary>Ta commune ou ton adresse <span className="faint">(facultatif)</span></summary>
      <ConsentGroups consents={[{ ...geo, granted: view.data.consents.geocoding }]} groups={["adresse"]} busy={busy} onToggle={toggle} />
      {view.data.place?.label && <p><Icon name="pin" /> <b>{view.data.place.label}</b></p>}
      <p className="faint" role="status">{located ? "Adresse située : ta commune sert de point de départ." : ""}</p>
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
function ConsentsStep({ data, done, onDone, reload }: { data: StartView; done: boolean; onDone: OnDone; reload: (v: StartView) => void }) {
  const [busy, error, run] = useAsyncAction();
  const list = data.public_consents;
  const off = list.filter((c) => !c.granted).length;
  const toggle = (purpose: string, granted: boolean) =>
    void run(async () => {
      await api(`/api/consents/${purpose}`, { method: "PUT", body: { granted: !granted } });
      reload(await api<StartView>("/api/start"));
    });
  const grantAll = () =>
    void run(async () => {
      const before = new Map(list.map((c) => [c.purpose, c.granted && c.decided]));
      const after = await api<PublicConsent[]>("/api/start/public-consents", { method: "POST", body: { purposes: list.map((c) => c.purpose) } });
      // Le compte vient de la réponse du serveur : ce qui est accordé maintenant et ne l'était pas avant.
      const fresh = after.filter((c) => c.granted && c.decided && !before.get(c.purpose)).length;
      onDone(fresh > 0 ? `${plural(fresh, "accord donné", "accords donnés")}, modifiables dans Réglages › Confidentialité.` : "Ces accords étaient déjà donnés.");
    });
  /** « Continuer avec ces choix » : chaque interrupteur encore sans réponse est noté tel que tu le vois (journalisé). */
  const keep = () =>
    void run(async () => {
      for (const c of list.filter((x) => !x.decided)) {
        await api(`/api/consents/${c.purpose}`, { method: "PUT", body: { granted: c.granted } });
      }
      const yes = list.filter((c) => c.granted).length;
      onDone(`Tes choix sont notés : ${plural(yes, "accord donné", "accords donnés")} sur ${list.length}.`);
    });
  return (
    <div className="stack journey-stack">
      <ConsentGroups consents={list} groups={["public", "zone"]} busy={busy} onToggle={(c) => toggle(c.purpose, c.granted)} />
      <p className="faint journey-note">
        Météo et Valeur du logement attendent ta commune : tant que tu ne l'as pas donnée (étape Ton foyer, facultatif), rien ne part.
        Ton adresse et tes contenus (messageries, mail, assistant, Linky…) se demandent plus tard, au moment où tu relies le service.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="journey-note">
        « Activer » accorde ces {list.length} accords et eux seuls ; « Continuer » note chaque interrupteur tel qu'il est. Tu les changes à tout moment dans Réglages › Confidentialité.
      </p>
      <div className="actions journey-actions">
        {off > 0 && (
          <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy} onClick={grantAll}>Activer ces {list.length} accords</button>
        )}
        <button type="button" className={`btn ${off > 0 ? "quiet " : ""}lg`} disabled={busy} onClick={done ? () => onDone() : keep}>
          Continuer avec ces choix <Icon name="arrow" />
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 4. ton premier papier
const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf";

function PaperStep({ done, onDone }: { done: boolean; onDone: OnDone }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [skipped, setSkipped] = useState<VaultUpload["skipped"]>([]);
  const [busy, error, run] = useAsyncAction();
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
        // La lecture se fait en arrière-plan : on ne promet pas ce qu'elle trouvera.
        onDone(`${plural(r.documents.length, "document déposé", "documents déposés")}. Bank le lit en ce moment ; ce qu'il y trouve s'affiche dans Papiers (un avis d'impôt marche le mieux).`);
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
        {done && <button type="button" className="btn quiet lg" onClick={() => onDone()}>Continuer</button>}
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

function PrioritiesStep({ onDone }: { onDone: OnDone }) {
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
function NotifyStep({ done, onDone, onLater }: { done: boolean; onDone: OnDone; onLater: () => void }) {
  const st = useApi<PushStatus>("/api/push");
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const supported = pushSupported();
  const permission = pushPermission();
  const usable = supported && permission !== "denied";
  const push = consents.data?.find((c) => c.purpose === "push");
  const activate = () =>
    void run(async () => {
      if (!st.data) return;
      // Le texte de l'accord est juste au-dessus et le bouton le dit : ce clic-là est l'accord, explicite. S'il
      // n'aboutit pas (permission refusée, abonnement en échec), l'accord donné par ce clic est retiré.
      const granting = !st.data.consent;
      if (granting) await api("/api/consents/push", { method: "PUT", body: { granted: true } });
      try {
        const device = await enablePush(st.data.public_key);
        onDone(`Notifications activées sur « ${device.label} ».`);
      } catch (e) {
        if (granting) await api("/api/consents/push", { method: "PUT", body: { granted: false } }).catch(() => undefined);
        throw e;
      }
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
          {usable && (
            <div className="actions journey-actions">
              <button type="button" className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy || !st.data} onClick={activate}
                      aria-describedby="notify-consent">
                <Icon name="bell" /> Accepter et activer les notifications
              </button>
              <p className="faint" id="notify-consent">Ce bouton donne aussi l'accord « Notifications », retirable dans Réglages.</p>
            </div>
          )}
        </section>
        <section className="panel journey-choice" aria-labelledby="notify-chat">
          <h2 id="notify-chat"><Icon name="chat" /> Dans ta messagerie</h2>
          <p className="muted">WhatsApp par QR code, Telegram, Signal… Bank t'y écrit ses alertes, et tu lui réponds. Un bouton te ramène ici ensuite.</p>
          <div className="actions"><a className="btn quiet" href={`#/messageries?from=${RETURN}prevenir`}>Ouvrir Messageries <Icon name="arrow" /></a></div>
        </section>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {done && <div className="actions journey-actions"><button type="button" className="btn quiet lg" onClick={() => onDone()}>Continuer</button></div>}
      {/* Rien d'activable ici : « Plus tard » devient l'action claire, pas un lien discret en bas. */}
      {!done && !usable && <div className="actions journey-actions"><button type="button" className="btn lg" onClick={onLater}>Plus tard <Icon name="arrow" /></button></div>}
    </div>
  );
}
