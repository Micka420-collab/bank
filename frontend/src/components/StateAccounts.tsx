// Connecteurs › Comptes de l'État, connecteur guidé (#30, #32) : un bouton par organisme vers la page d'entrée de son
// espace officiel, avec son état (dernier document lu, sa date, « à renouveler », « à récupérer ») ; les documents
// à récupérer, nés d'un mail d'avis sans pièce jointe ou d'une attente de saison, qui se ferment seuls quand le PDF
// arrive dans Papiers. Bank ne se connecte jamais à ces comptes et n'en garde aucun identifiant.

import { api, useApi } from "../api";
import { dayText } from "./Baremes";
import { Icon } from "./Icon";
import { ErrorNote, useAsyncAction, useToast } from "./ui";

export type StateBody = {
  slug: string;
  name: string;
  entry_url: string;
  path: string;
  expected: string;
  last_document: { title: string; day: string; read_at: string; document_id?: number } | null;
  open_tasks: number;
  status: "to_fetch" | "renew" | "ok" | "none";
};
export type DocTask = {
  id: number;
  body: string;
  body_name: string;
  origin: "mail" | "season";
  expected: string;
  subject: string | null;
  received_at: string | null;
  due: string;
  late: boolean;
  status: "open" | "done" | "dismissed" | "expired";
  entry_url: string | null;
  path: string | null;
  document_id: number | null;
  closed_at: string | null;
  self_closing: boolean;
};
export type StateAccountsView = { bodies: StateBody[]; tasks: DocTask[]; recent: DocTask[]; checked_on: string; note: string };

/** L'état d'un organisme, en clair. */
export function bodyStatus(b: StateBody): { text: string; cls: string } {
  if (b.status === "to_fetch") return { text: `${b.open_tasks} à récupérer`, cls: "late" };
  if (b.status === "renew") return { text: "à renouveler", cls: "warn" };
  if (b.status === "ok") return { text: "à jour", cls: "ok" };
  return { text: "aucun document lu", cls: "" };
}

/** « reçu par mail le 03/08/2026 » ou « attendu chaque été » : d'où vient la tâche. */
export function taskOrigin(t: DocTask): string {
  if (t.origin === "mail") return t.received_at ? `annoncé par mail le ${dayText(t.received_at)}` : "annoncé par mail";
  return "attente de saison";
}

export function StateAccounts() {
  const view = useApi<StateAccountsView>("/api/state-accounts");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const data = view.data;
  const close = (t: DocTask, status: "done" | "dismissed") =>
    void run(async () => {
      await api(`/api/state-accounts/tasks/${t.id}`, { method: "POST", body: { status } });
      toast(status === "done" ? `${t.expected} : noté comme récupéré.` : `${t.expected} : écarté.`);
      view.reload();
    });
  if (view.error) return <ErrorNote message={view.error} onRetry={view.reload} />;
  if (!data) return null;
  return (
    <div className="state-accounts">
      <ul className="state-bodies">
        {data.bodies.map((b) => {
          const st = bodyStatus(b);
          return (
            <li key={b.slug} data-status={b.status}>
              <a className="btn quiet sm" href={b.entry_url} target="_blank" rel="noopener noreferrer" aria-label={`Ouvrir l'espace ${b.name}`}>
                <Icon name="external" /> {b.name}
              </a>
              <span className={`tag ${st.cls}`}>{st.text}</span>
              <div className="faint">
                {b.last_document ? `Dernier document : ${b.last_document.title}, du ${dayText(b.last_document.day)}` : `Attendu : ${b.expected}`}
              </div>
              <div className="faint">Une fois connecté : {b.path}</div>
            </li>
          );
        })}
      </ul>
      {data.tasks.length > 0 && (
        <>
          <h3>Documents à récupérer</h3>
          <ul className="doc-tasks">
            {data.tasks.map((t) => (
              <li key={t.id} data-late={t.late || undefined}>
                <div>
                  <b>{t.expected}</b> <span className="faint">· {t.body_name}</span>{" "}
                  {t.late ? <span className="tag late">en retard</span> : <span className="tag">avant le {dayText(t.due)}</span>}
                  <div className="faint">
                    {taskOrigin(t)}{t.subject && ` : « ${t.subject} »`}
                    {t.self_closing ? " · se ferme seul quand le PDF arrive dans Papiers" : " · à lire en ligne, sans PDF"}
                  </div>
                </div>
                <div className="actions">
                  {t.entry_url && <a className="btn sm" href={t.entry_url} target="_blank" rel="noopener noreferrer">Ouvrir l'espace</a>}
                  <button type="button" className="btn ghost xs" disabled={busy} onClick={() => close(t, "done")}>Je l'ai</button>
                  <button type="button" className="btn ghost xs" disabled={busy} onClick={() => close(t, "dismissed")}>Pas pour moi</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {data.recent.length > 0 && (
        <p className="faint">
          Récemment : {data.recent.slice(0, 3).map((t) => `${t.expected} (${t.status === "done" ? "reçu" : t.status === "expired" ? "saison passée" : "écarté"})`).join(", ")}.
        </p>
      )}
      <p className="faint">{data.note} Pages d'entrée vérifiées le {data.checked_on}.</p>
      {error && <ErrorNote message={error} />}
    </div>
  );
}
