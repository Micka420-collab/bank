// « Bien démarrer » (DESIGN.md § 3.10 b) : sur Aujourd'hui, sous le chiffre du jour, ce qui reste à régler — un anneau
// « 5 sur 8 », les trois prochains éléments, chacun avec son lien direct. Tout vient du serveur (/api/start) : la
// liste dit la même chose sur chaque appareil. La fête n'a lieu que si l'anneau se ferme après un geste fait pendant
// que la page est ouverte, jamais au chargement. Terminée, elle se replie en pilule une semaine ; « Masquer » la
// retire d'Aujourd'hui (elle reste dans Réglages). Mouvement (§ 3.7) : l'anneau se remplit et son chiffre se compte,
// les éléments arrivent l'un après l'autre, et celui qui vient d'être fait sous tes yeux se coche en se traçant.

import { useEffect, useRef, useState } from "react";
import { api, useApi, type StartItem, type StartView } from "../api";
import { useCountUp } from "../motion";
import { Icon, type IconName } from "./Icon";
import { Ring } from "./Ring";
import { useAsyncAction, useCelebrate } from "./ui";

const ICONS: Record<string, IconName> = {
  banque: "bank",
  foyer: "situation",
  accords: "shield",
  papier: "folder",
  priorites: "target",
  prevenir: "bell",
  securite: "lock",
  sauvegarde: "vault",
  planning: "calendar",
  assistant: "spark",
  linky: "bolt",
  mail: "doc",
  powens: "bank",
  crypto: "crypto",
  pilote: "robot",
};
const SHOWN = 3;

/** Les éléments comptés : d'abord ce qui reste (dans l'ordre), puis ce qui est fait. */
export function orderItems(items: StartItem[]): { todo: StartItem[]; done: StartItem[]; more: StartItem[] } {
  const core = items.filter((i) => i.group === "essentiel");
  return { todo: core.filter((i) => !i.done), done: core.filter((i) => i.done), more: items.filter((i) => i.group === "plus") };
}

/** Lit /api/start et le relit après chaque écriture ailleurs dans l'application (événement « bank:changed »). */
export function useStart(): { data: StartView | null; reloaded: boolean; set: (v: StartView) => void } {
  const st = useApi<StartView>("/api/start");
  const [data, setData] = useState<StartView | null>(null);
  const [reloaded, setReloaded] = useState(false);
  const reload = st.reload;
  useEffect(() => {
    if (st.data) setData(st.data);
  }, [st.data]);
  useEffect(() => {
    let timer: number | undefined;
    const onChange = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setReloaded(true);
        reload();
      }, 400);
    };
    window.addEventListener("bank:changed", onChange);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("bank:changed", onChange);
    };
  }, [reload]);
  return { data, reloaded, set: setData };
}

export function StartChecklist() {
  const { data, reloaded, set } = useStart();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, error, run] = useAsyncAction();
  const celebrate = useCelebrate();
  const wasComplete = useRef<boolean | null>(null);
  // Ce qui vient d'être fait pendant que la page est ouverte (après un geste, jamais au chargement) : coché en direct.
  const doneBefore = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const counted = useCountUp(data?.checklist.done ?? 0);

  // L'anneau se ferme sous tes yeux (après un geste, pas au chargement) : la fête du § 3.6, une fois.
  useEffect(() => {
    if (!data) return;
    const complete = data.checklist.completed_at !== null;
    if (wasComplete.current === false && complete && reloaded) celebrate("done", "Bank est prêt.");
    wasComplete.current = complete;
    const now = new Set(data.checklist.items.filter((i) => i.done).map((i) => i.key));
    if (doneBefore.current && reloaded) setFresh(new Set([...now].filter((k) => !doneBefore.current!.has(k))));
    doneBefore.current = now;
  }, [data, reloaded, celebrate]);

  if (!data || !data.checklist.visible) return null;
  const cl = data.checklist;
  const { todo, done, more } = orderItems(cl.items);
  const dismiss = () =>
    void run(async () => {
      set(await api<StartView>("/api/start/checklist", { method: "POST", body: { action: "dismiss" } }));
    });

  if (cl.collapsed) {
    return (
      <details className="card start-pill">
        <summary>
          <Icon name="check" /> <span><b>Bien démarrer : terminé</b> · Pour aller plus loin</span>
        </summary>
        <ItemList items={more} />
      </details>
    );
  }

  const shown = todo.slice(0, SHOWN);
  const rest = [...todo.slice(SHOWN), ...done];
  const restLabel = moreLabel(todo.length - shown.length, done.length);
  const journey = data.journey;
  return (
    <section className="card start-card" aria-labelledby="start-title">
      <div className="start-head">
        {/* La clé rejoue le remplissage de l'anneau quand le compte change. */}
        <Ring key={cl.done} value={cl.done} max={cl.total} label={`${cl.done} éléments sur ${cl.total} faits`}>
          {counted}<small>/{cl.total}</small>
        </Ring>
        <div className="start-intro">
          <h2 id="start-title">Bien démarrer</h2>
          <p className="muted">{todo.length === 1 ? "Plus qu'une chose à régler." : `${todo.length} choses à régler, à ton rythme.`}</p>
        </div>
        {journey.status !== "finished" && (
          <a className="btn sm start-resume" href={`#/bienvenue${journey.current ? `/${journey.current}` : ""}`}>Reprendre le parcours <Icon name="arrow" /></a>
        )}
      </div>
      <ItemList items={shown} fresh={fresh} />
      {rest.length > 0 && (
        <>
          {open && <ItemList items={rest} fresh={fresh} />}
          <button type="button" className="btn link start-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Voir moins" : restLabel}
          </button>
        </>
      )}
      {more.length > 0 && (
        <details className="start-more">
          <summary>Pour aller plus loin <span className="faint">(non compté)</span></summary>
          <ItemList items={more} />
        </details>
      )}
      <div className="start-foot">
        {confirm ? (
          <p className="start-confirm" role="group" aria-label="Masquer la liste">
            <span>Tu la retrouveras dans Réglages.</span>
            <button type="button" className={`btn quiet sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={dismiss}>Masquer</button>
            <button type="button" className="btn link" onClick={() => setConfirm(false)}>Annuler</button>
          </p>
        ) : (
          <button type="button" className="btn ghost sm" onClick={() => setConfirm(true)}>Masquer</button>
        )}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

/** « Voir 1 autre à faire · 4 faits » : ce qui reste et ce qui est fait, jamais additionnés en « 5 autres ». */
export function moreLabel(todo: number, done: number): string {
  const parts = [];
  if (todo > 0) parts.push(todo === 1 ? "1 autre à faire" : `${todo} autres à faire`);
  if (done > 0) parts.push(done === 1 ? "1 fait" : `${done} faits`);
  return `Voir ${parts.join(" · ")}`;
}

function ItemList({ items, fresh }: { items: StartItem[]; fresh?: Set<string> }) {
  if (items.length === 0) return null;
  return (
    <ul className="start-items">
      {items.map((i) => (
        <li key={i.key} className={i.done ? `is-done${fresh?.has(i.key) ? " is-fresh" : ""}` : undefined}>
          <a href={i.href}>
            <span className="start-icon" aria-hidden="true"><Icon name={i.done ? "check" : ICONS[i.key] ?? "spark"} /></span>
            <span className="start-text"><b>{i.title}</b><span>{i.why}</span></span>
            <span className="start-time">{i.done ? "fait" : `${i.minutes} min`}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

/** Réglages › « Bien démarrer » : la même liste, toujours accessible, même masquée ou terminée. */
export function StartSettings() {
  const { data, set } = useStart();
  const [busy, error, run] = useAsyncAction();
  if (!data) return null;
  const cl = data.checklist;
  const restore = () =>
    void run(async () => {
      set(await api<StartView>("/api/start/checklist", { method: "POST", body: { action: "restore" } }));
    });
  return (
    <section className="card section start-settings" aria-labelledby="start-settings-title">
      <div className="start-head">
        <Ring value={cl.done} max={cl.total} label={`${cl.done} éléments sur ${cl.total} faits`} size="sm">{cl.done}</Ring>
        <div className="start-intro">
          <h2 id="start-settings-title">Bien démarrer</h2>
          <p className="muted">{cl.done} sur {cl.total} faits{cl.visible ? " · visible sur Aujourd'hui" : ""}.</p>
        </div>
        <div className="actions start-actions">
          <a className="btn quiet sm" href={`#/bienvenue${data.journey.current ? `/${data.journey.current}` : ""}`}>
            {data.journey.status === "finished" ? "Revoir le parcours" : "Reprendre le parcours"}
          </a>
          {!cl.visible && <button type="button" className={`btn ghost sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={restore}>Afficher sur Aujourd'hui</button>}
        </div>
      </div>
      <ItemList items={orderItems(cl.items).todo} />
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
