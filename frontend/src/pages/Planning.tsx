// Planning de travail : ton agenda (iCal) relié à ta paie. Bank connaît déjà l'employeur, le jour et le montant
// habituel ; avec les astreintes, heures et jours planifiés, il prévoit la prochaine paie et apprend tes règles.

import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type PayModel, type PayRule, type PlanningStatus } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, parseEuros, shortDate } from "../format";

const UNIT_LABEL: Record<PayRule["unit"], string> = { per_event: "par événement", per_day: "par jour", per_hour: "par heure" };

export function Planning() {
  const status = useApi<PlanningStatus>("/api/planning");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const sync = () =>
    void run(async () => {
      const r = await api<PlanningStatus>("/api/planning/sync", { method: "POST" });
      toast(`Agenda relu : ${r.imported ?? 0} journées.`);
      status.reload();
    });
  const disconnect = () =>
    void run(async () => {
      await api("/api/planning", { method: "DELETE" });
      toast("Agenda retiré. Les journées enregistrées ont été effacées.");
      status.reload();
    });

  return (
    <section className="section" aria-labelledby="planning-title" id="planning">
      <div className="section-head"><h2 id="planning-title"><Icon name="calendar" /> Planning de travail</h2></div>
      {status.error && <ErrorNote message={status.error} onRetry={status.reload} />}
      {!status.data && !status.error && <Loading what="Planning" />}
      {status.data && (
        <>
          <p className="section-note">
            {status.data.employer && status.data.usual_pay_cents ? (
              <>
                Je sais déjà que <b>{status.data.employer}</b> te verse <b>{money(status.data.usual_pay_cents, { whole: true })}</b>
                {status.data.pay_day ? <> vers le <b>{status.data.pay_day}</b> de chaque mois</> : null}.{" "}
              </>
            ) : (
              <>Dès qu'un salaire apparaît dans tes relevés, je connais ton employeur et ton jour de paie. </>
            )}
            Relie ton agenda : avec tes astreintes, heures ou jours planifiés, je calcule <b>combien</b> tu vas toucher,
            pas seulement quand — et j'apprends ce que chaque événement te rapporte à partir de tes paies.
          </p>
          {status.data.connected && status.data.plan ? (
            <PlanPanel status={status.data} busy={busy} onSync={sync} onDisconnect={disconnect} onChanged={status.reload} />
          ) : (
            <ConnectForms onConnected={status.reload} />
          )}
          {error && <p className="error" role="alert">{error}</p>}
        </>
      )}
    </section>
  );
}

function ConnectForms({ onConnected }: { onConnected: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const connectUrl = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<PlanningStatus>("/api/planning/url", { method: "POST", body: { url: url.trim() } });
      setUrl("");
      toast(`Agenda relié : ${r.imported ?? 0} journées lues. Il sera relu chaque nuit.`);
      onConnected();
    });
  };
  const upload = (file: File | undefined) => {
    if (!file) return;
    void run(async () => {
      const form = new FormData();
      form.append("file", file);
      const r = await api<PlanningStatus>("/api/planning/file", { method: "POST", form });
      toast(`Agenda lu : ${r.imported ?? 0} journées.`);
      onConnected();
    });
  };

  return (
    <div className="split">
      <form className="panel stack" onSubmit={connectUrl} aria-label="Adresse de l'agenda">
        <h3>Adresse privée de l'agenda</h3>
        <p className="muted">
          Google Agenda : Paramètres › ton agenda › « Adresse secrète au format iCal ». Outlook : « Publier un calendrier ».
          Adresse en https, chiffrée chez Bank, relue chaque nuit.
        </p>
        <Field label="Adresse (https://…/basic.ics)">
          <input type="password" value={url} onChange={(e) => setUrl(e.target.value)} required minLength={12} maxLength={2000}
                 autoComplete="off" spellCheck={false} placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" />
        </Field>
        <button className="btn" disabled={busy || url.trim().length < 12}>Relier l'agenda</button>
      </form>
      <div className="panel stack">
        <h3>Ou un fichier .ics</h3>
        <p className="muted">Export de ton agenda ou du planning de ton employeur. Pas de mise à jour automatique : tu le redéposes quand il change.</p>
        <label className="drop">
          <input className="sr-only" type="file" accept=".ics,text/calendar" disabled={busy}
                 onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          <span className="btn quiet" aria-hidden="true">{busy ? "Lecture…" : "Choisir un fichier"}</span>
          <span>Seules les journées et les titres des événements sont gardés, chiffrés.</span>
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function PlanPanel({ status, busy, onSync, onDisconnect, onChanged }: {
  status: PlanningStatus;
  busy: boolean;
  onSync: () => void;
  onDisconnect: () => void;
  onChanged: () => void;
}) {
  const plan = status.plan!;
  const next = status.predictions[0];
  return (
    <div className="stack">
      <div className="connected-head">
        <div>
          <b>{plan.label}</b>{" "}
          <span className="faint">
            {plan.source === "url" ? "relu chaque nuit" : "fichier déposé"} · {plan.shifts} journées
            {plan.coverage_start && plan.coverage_end && ` du ${shortDate(plan.coverage_start)} au ${shortDate(plan.coverage_end)}`}
            {plan.last_sync_at && ` · relu le ${shortDate(plan.last_sync_at)}`}
          </span>
          {plan.status === "error" && <div className="error">{plan.last_error}</div>}
        </div>
        <span className="actions" style={{ marginTop: 0 }}>
          {plan.source === "url" && <button className="btn quiet" disabled={busy} onClick={onSync}>Relire maintenant</button>}
          <button className="btn link" disabled={busy} onClick={onDisconnect}>Retirer</button>
        </span>
      </div>

      {next ? (
        <div className="pay-forecast" aria-live="polite">
          <p className="today-lead">Paie du {shortDate(next.pay_date)}</p>
          <p className="pay-figure">{money(next.amount_cents, { whole: true })}</p>
          <p className="muted">
            Part fixe {money(next.base_cents, { whole: true })}
            {next.extras.filter((e) => e.total_cents).map((e) => (
              <span key={e.pattern}> + {money(e.total_cents, { whole: true })} ({e.count} × {e.pattern}{e.learned ? ", appris" : ""})</span>
            ))}
            . Période du {shortDate(next.period_start)} au {shortDate(next.period_end)} : {next.worked_days} jour{next.worked_days > 1 ? "s" : ""} planifié{next.worked_days > 1 ? "s" : ""}.
          </p>
          {status.predictions.slice(1).map((p) => (
            <p key={p.pay_date} className="faint">Puis {shortDate(p.pay_date)} : {money(p.amount_cents, { whole: true })}.</p>
          ))}
        </div>
      ) : (
        <p className="muted">{status.model?.rules.length ? "Pas encore de période couverte par l'agenda pour la prochaine paie." : "Règle ci-dessous ce que chaque événement te rapporte : la prochaine paie s'affichera ici."}</p>
      )}

      <ModelEditor status={status} onSaved={onChanged} />

      {status.upcoming.length > 0 && (
        <details className="upcoming-shifts">
          <summary>Les 14 prochains jours ({status.upcoming.length} événement{status.upcoming.length > 1 ? "s" : ""})</summary>
          <ul className="plain-list">
            {status.upcoming.map((u, i) => (
              <li key={i}><b>{shortDate(u.day)}</b> {u.title}{!u.all_day && u.hours > 0 ? ` · ${u.hours} h` : ""}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

type RuleDraft = { pattern: string; unit: PayRule["unit"]; amount: string };

function toDraft(model: PayModel | null): RuleDraft[] {
  return (model?.rules ?? []).map((r) => ({ pattern: r.pattern, unit: r.unit, amount: (r.amount_cents / 100).toString().replace(".", ",") }));
}

export function ModelEditor({ status, onSaved }: { status: PlanningStatus; onSaved: () => void }) {
  const [rules, setRules] = useState<RuleDraft[]>(() => toDraft(status.model));
  const [base, setBase] = useState(status.model ? (status.model.base_cents / 100).toString().replace(".", ",") : "");
  const [offset, setOffset] = useState<0 | 1>(status.model?.offset_months ?? 0);
  const [busy, error, run] = useAsyncAction();
  const [invalid, setInvalid] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => {
    setRules(toDraft(status.model));
    setBase(status.model ? (status.model.base_cents / 100).toString().replace(".", ",") : "");
    setOffset(status.model?.offset_months ?? 0);
  }, [status.model]);

  const set = (i: number, patch: Partial<RuleDraft>) => setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = (e: FormEvent) => {
    e.preventDefault();
    const baseCents = base.trim() === "" ? null : parseEuros(base);
    if (baseCents === null && base.trim() !== "") return setInvalid("Part fixe : indique un montant en euros.");
    const clean: PayRule[] = [];
    for (const r of rules) {
      const cents = parseEuros(r.amount);
      if (!r.pattern.trim() || cents === null) return setInvalid("Chaque règle a un mot-clé et un montant (par exemple « Astreinte », 180).");
      clean.push({ pattern: r.pattern.trim(), unit: r.unit, amount_cents: cents });
    }
    setInvalid(null);
    void run(async () => {
      await api("/api/planning/model", { method: "PUT", body: { base_cents: baseCents, offset_months: offset, rules: clean } });
      toast("Règles de paie enregistrées.");
      onSaved();
    });
  };

  const learned = status.learned;
  return (
    <form className="panel stack" onSubmit={save} aria-label="Règles de paie">
      <h3>Comment ta paie varie avec ton planning</h3>
      <p className="muted">
        Une règle = un mot-clé trouvé dans le titre d'un événement, et ce qu'il te rapporte. « * » prend tous les
        événements. Dès quatre paies connues, Bank apprend les vrais montants et les utilise s'ils collent à la réalité.
      </p>
      {learned && (
        <div className="notice ok">
          {learned.note}. Part fixe apprise : {money(learned.base_cents, { whole: true })}
          {learned.rules.filter((r) => r.learned).map((r) => <span key={r.pattern}> · {r.pattern} : {money(r.amount_cents)} {UNIT_LABEL[r.unit]}</span>)}.
        </div>
      )}
      <div className="form-grid">
        <Field label="Part fixe par paie" hint="Vide : la paie habituelle lue dans tes relevés.">
          <span className="euro-field">
            <input inputMode="decimal" value={base} onChange={(e) => setBase(e.target.value)} placeholder={status.usual_pay_cents ? String(Math.round(status.usual_pay_cents / 100)) : "1 900"} />
            <span aria-hidden="true">€</span>
          </span>
        </Field>
        <Field label="La paie du mois couvre">
          <select value={offset} onChange={(e) => setOffset(Number(e.target.value) as 0 | 1)}>
            <option value={0}>le même mois</option>
            <option value={1}>le mois précédent (variable à terme échu)</option>
          </select>
        </Field>
      </div>
      <div className="rules">
        {rules.map((r, i) => (
          <div className="rule" key={i}>
            <input value={r.pattern} onChange={(e) => set(i, { pattern: e.target.value })} placeholder="Astreinte" maxLength={40} aria-label={`Mot-clé de la règle ${i + 1}`} />
            <select value={r.unit} onChange={(e) => set(i, { unit: e.target.value as PayRule["unit"] })} aria-label={`Unité de la règle ${i + 1}`}>
              {(Object.keys(UNIT_LABEL) as PayRule["unit"][]).map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
            </select>
            <span className="euro-field">
              <input inputMode="decimal" value={r.amount} onChange={(e) => set(i, { amount: e.target.value })} placeholder="180" aria-label={`Montant de la règle ${i + 1}`} />
              <span aria-hidden="true">€</span>
            </span>
            <button type="button" className="btn link" onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}>Retirer</button>
          </div>
        ))}
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        {rules.length < 8 && (
          <button type="button" className="btn quiet" onClick={() => setRules((rs) => [...rs, { pattern: rs.length === 0 ? "Astreinte" : "", unit: "per_event", amount: "" }])}>
            Ajouter une règle
          </button>
        )}
        <button className="btn" disabled={busy}>Enregistrer</button>
      </div>
      {invalid && <p className="error" role="alert">{invalid}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
