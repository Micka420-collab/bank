// Opportunités : ce que tu pourrais récupérer, payer en moins, gagner, mieux placer ou anticiper — tiré de tes
// comptes, de tes documents et de règles datées, classé par gain net attendu. Chaque piste dit ce qui est
// constaté, ce qui est estimé, ce qui est supposé, et d'où viennent les règles. Rien n'est promis : un gain
// ne passe dans « obtenu » que quand il est constaté sur tes comptes et que tu le confirmes.

import { useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type Confirmation,
  type Opportunity,
  type OpportunityCategory,
  type OpportunityCenter,
  type SourceRef,
} from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, EuroInput, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, perPeriod, plural, safeHref, shortDate } from "../format";
import { fullDate } from "./Documents";

const CONFIDENCE: Record<string, string> = { "élevée": "Fiable", moyenne: "Plausible", faible: "Incertain" };
const CONFIDENCE_TONE: Record<string, string> = { "élevée": "ok", moyenne: "", faible: "warn" };

function effort(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m}` : `${h} h`;
}

export function Opportunities() {
  const center = useApi<OpportunityCenter>("/api/opportunities");
  const [filter, setFilter] = useState<OpportunityCategory | "all" | "mine">("all");
  if (center.error) return <ErrorNote message={center.error} onRetry={center.reload} />;
  if (!center.data) return <Loading what="Recherche des pistes" />;
  const c = center.data;
  const counts = c.opportunities.reduce<Record<string, number>>((acc, o) => ({ ...acc, [o.category]: (acc[o.category] ?? 0) + 1 }), {});
  const shown = c.opportunities.filter((o) =>
    filter === "all" ? true : filter === "mine" ? c.priorities.includes(o.category) : o.category === filter,
  );
  const urgent = shown.filter((o) => o.deadline);
  const rest = shown.filter((o) => !o.deadline);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Opportunités</h1>
          <p className="muted">
            Ce que tu pourrais récupérer, payer en moins, gagner ou anticiper, tiré de tes comptes, de tes documents et
            de règles officielles datées. Classé par gain net attendu : ce que ça peut rapporter, multiplié par la
            chance que ça marche, moins le temps que ça te prend.
          </p>
        </div>
      </div>

      <ValueLedger value={c.value} />

      {c.confirmations.length > 0 && (
        <section className="section" aria-labelledby="confirm-title">
          <h2 id="confirm-title">Bank a vu passer un gain</h2>
          <div className="stack">
            {c.confirmations.map((x) => <ConfirmGain key={x.key} item={x} onDone={center.reload} />)}
          </div>
        </section>
      )}

      <div className="chips opp-filters" role="group" aria-label="Filtrer les pistes">
        <button type="button" className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
          Tout <span className="chip-count">{c.opportunities.length}</span>
        </button>
        {c.priorities.length > 0 && (
          <button type="button" className="chip" aria-pressed={filter === "mine"} onClick={() => setFilter("mine")}>
            Tes priorités
          </button>
        )}
        {(Object.keys(c.categories) as OpportunityCategory[]).filter((k) => counts[k]).map((k) => (
          <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>
            {c.categories[k]} <span className="chip-count">{counts[k]}</span>
          </button>
        ))}
      </div>

      {shown.length === 0 && (
        <section className="card card-quiet opp-empty">
          <h2>Rien à signaler ici pour l'instant</h2>
          <p className="muted">Bank regarde à nouveau à chaque synchronisation : nouveaux frais, abonnements, droits aux aides, documents qui expirent.</p>
        </section>
      )}

      {urgent.length > 0 && (
        <section className="section opp-group" aria-labelledby="urgent-title">
          <h2 id="urgent-title"><Icon name="clock" /> Avec une échéance</h2>
          <ol className="opp-list">{urgent.map((o) => <OpportunityCard key={o.key} opp={o} tv={c.time_value_cents_per_hour} onChange={center.reload} />)}</ol>
        </section>
      )}
      {rest.length > 0 && (
        <section className="section opp-group" aria-labelledby="leads-title">
          <h2 id="leads-title"><Icon name="target" /> Les pistes, de la plus rentable à la moins rentable</h2>
          <ol className="opp-list">{rest.map((o) => <OpportunityCard key={o.key} opp={o} tv={c.time_value_cents_per_hour} onChange={center.reload} />)}</ol>
        </section>
      )}

      <footer className="opp-foot">
        <TimeValue cents={c.time_value_cents_per_hour} onSaved={center.reload} />
        {c.hidden > 0 && <p className="faint">{plural(c.hidden, "piste écartée ou remise à plus tard", "pistes écartées ou remises à plus tard")} : elles reviennent d'elles-mêmes à la date choisie.</p>}
        <p className="faint">{c.disclaimer}</p>
      </footer>
    </>
  );
}

// ------------------------------------------------------------------ obtenu / en cours
function ValueLedger({ value }: { value: OpportunityCenter["value"] }) {
  const [open, setOpen] = useState(false);
  const nothing = value.confirmed_yearly_cents === 0 && value.estimated_yearly_cents === 0;
  return (
    <section className="card value-ledger" aria-labelledby="ledger-title">
      <h2 id="ledger-title" className="sr-only">Ce que Bank t'a fait gagner</h2>
      <div className="value-col is-confirmed">
        <span className="value-label">Obtenu</span>
        <span className="value-figure">{money(value.confirmed_yearly_cents, { whole: value.confirmed_yearly_cents === 0 || value.confirmed_yearly_cents >= 10_000 })}</span>
        <span className="faint">sur un an · constaté sur tes comptes et confirmé par toi</span>
      </div>
      <div className="value-col is-estimated">
        <span className="value-label">En cours</span>
        <span className="value-figure">{value.estimated_yearly_cents ? `≈ ${money(value.estimated_yearly_cents, { whole: true })}` : "—"}</span>
        <span className="faint">estimé, selon les chances de tes démarches · jamais ajouté à l'obtenu</span>
      </div>
      {nothing ? (
        <p className="faint value-note">Lance une piste ci-dessous : quand le gain arrive sur ton compte, Bank te le propose ici, et c'est toi qui confirmes.</p>
      ) : (
        <div className="value-note">
          <button type="button" className="btn link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Masquer le détail" : "Voir le détail"}
          </button>
          {open && (
            <ul className="plain-list value-events">
              {value.history.map((v, i) => (
                <li key={`c${i}`}><span className="tag ok">Obtenu</span> {v.label} — <b>{perPeriod(v.amount_cents, v.period)}</b> <span className="faint">le {shortDate(v.on)} · {v.evidence}</span></li>
              ))}
              {value.pending.map((v, i) => (
                <li key={`p${i}`}><span className="tag">Estimé</span> {v.label} — ≈ {perPeriod(v.amount_cents, v.period)} <span className="faint">{v.evidence}</span></li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function ConfirmGain({ item, onDone }: { item: Confirmation; onDone: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const confirm = () =>
    void run(async () => {
      await api("/api/opportunities/confirm", {
        method: "POST",
        body: { key: item.key, amount_cents: item.amount_cents, period: item.period, label: item.label, procedure_id: item.procedure_id },
      });
      toast(`Gain confirmé : ${perPeriod(item.amount_cents, item.period)}.`);
      onDone();
    });
  return (
    <div className="notice ok opp-confirm" role="group" aria-label={item.title}>
      <Icon name="check" />
      <div>
        <p><b>{item.title}</b> : {item.label}.</p>
        <p className="muted">Si c'est bien le résultat de ta démarche, confirme-le : il sera compté comme obtenu ({perPeriod(item.amount_cents, item.period)}) et la démarche sera close.</p>
        <div className="actions">
          <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={confirm}>Confirmer ce gain</button>
          <a className="btn quiet sm" href={`#/demarches/${item.procedure_id}`}>Ouvrir la démarche</a>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ une piste
function OpportunityCard({ opp, tv, onChange }: { opp: Opportunity; tv: number; onChange: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const money0 = opp.amount_cents === 0;
  const decide = (decision: "dismissed" | "snoozed", label: string) =>
    void run(async () => {
      await api("/api/opportunities/decide", { method: "POST", body: { key: opp.key, decision, days: 30 } });
      toast(label);
      onChange();
    });
  const start = () =>
    void run(async () => {
      const r = await api<{ procedure_id: number }>("/api/opportunities/start", { method: "POST", body: { key: opp.key } });
      window.location.hash = `#/demarches/${r.procedure_id}`;
    });
  return (
    <li className={`opp is-${opp.category}`}>
      <div className="opp-tags">
        <span className="tag">{opp.category_label}</span>
        <span className={`tag ${CONFIDENCE_TONE[opp.confidence] ?? ""}`} title={`Confiance ${opp.confidence}`}>{CONFIDENCE[opp.confidence] ?? opp.confidence}</span>
        {opp.deadline && <span className="tag warn"><Icon name="clock" size={12} /> avant le {fullDate(opp.deadline)}</span>}
      </div>
      <h3>{opp.title}</h3>
      <p className="muted">{opp.summary}</p>
      <dl className="opp-figures">
        <div>
          <dt>En jeu</dt>
          <dd>{money0 ? <span className="faint">Pas de montant</span> : perPeriod(opp.amount_cents, opp.period)}</dd>
        </div>
        <div>
          <dt>Chance</dt>
          <dd>{Math.round(opp.probability * 100)} %</dd>
        </div>
        <div>
          <dt>Ton temps</dt>
          <dd>{effort(opp.effort_minutes)}</dd>
        </div>
        <div title={`Sur un an : ${money(opp.yearly_cents, { whole: true })} × ${Math.round(opp.probability * 100)} % − ${effort(opp.effort_minutes)} à ${money(tv, { whole: true })} de l'heure`}>
          <dt>Gain net attendu</dt>
          <dd className={opp.expected_net_cents > 0 ? "is-gain" : ""}>{money0 ? "—" : `${money(opp.expected_net_cents, { whole: true })} / an`}</dd>
        </div>
      </dl>
      <details className="opp-why">
        <summary>Pourquoi Bank te le propose</summary>
        <Evidence title="Constaté dans tes données" items={opp.facts} tone="fact" />
        <Evidence title="Estimé par Bank" items={opp.estimates} tone="estimate" />
        <Evidence title="Supposé (à vérifier)" items={opp.hypotheses} tone="hypothesis" />
        {opp.sources.length > 0 && <Sources sources={opp.sources} />}
      </details>
      <div className="actions">
        {opp.procedure ? (
          <a className="btn sm" href={`#/demarches/${opp.procedure.id}`}><Icon name="route" /> Démarche en cours : {opp.procedure.stage_label.toLowerCase()}</a>
        ) : opp.action.template ? (
          <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={start}><Icon name="route" /> Préparer la démarche</button>
        ) : opp.action.link?.startsWith("#/") ? (
          <a className="btn sm" href={opp.action.link}>{opp.action.label ?? "Voir"}</a>
        ) : safeHref(opp.action.link) ? (
          <a className="btn sm" href={safeHref(opp.action.link)!} target="_blank" rel="noopener noreferrer">{opp.action.label ?? "Ouvrir"} <Icon name="external" /></a>
        ) : null}
        {!opp.procedure && (
          <>
            <button type="button" className="btn quiet sm" disabled={busy} onClick={() => decide("snoozed", "Remise à dans 30 jours.")}>Plus tard</button>
            <button type="button" className="btn ghost sm" disabled={busy} onClick={() => decide("dismissed", "Piste écartée.")}>Pas pour moi</button>
          </>
        )}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}

function Evidence({ title, items, tone }: { title: string; items: string[]; tone: "fact" | "estimate" | "hypothesis" }) {
  if (items.length === 0) return null;
  return (
    <div className={`opp-evidence is-${tone}`}>
      <h4>{title}</h4>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  );
}

export function Sources({ sources }: { sources: SourceRef[] }) {
  return (
    <div className="opp-evidence is-source">
      <h4>Sources</h4>
      <ul>
        {sources.map((s, i) => (
          <li key={i}>
            {safeHref(s.url) ? <a href={safeHref(s.url)!} target="_blank" rel="noopener noreferrer">{s.title}</a> : s.title}
            {s.verified_on && <span className="faint"> · vérifiée le {fullDate(s.verified_on)}</span>}
            {s.kind === "texte" && <span className="faint"> · texte de loi</span>}
            {s.kind === "secondaire" && <span className="faint"> · source secondaire</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TimeValue({ cents, onSaved }: { cents: number; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<number | null>(cents);
  const [busy, error, run] = useAsyncAction();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/opportunities/settings", { method: "PUT", body: { time_value_cents_per_hour: value ?? 0 } });
      setEditing(false);
      onSaved();
    });
  };
  if (!editing) {
    return (
      <p className="faint">
        Une heure de ton temps compte pour <b>{money(cents, { whole: true })}</b> dans le classement (une hypothèse, à toi de la régler).{" "}
        <button type="button" className="btn link" onClick={() => setEditing(true)}>Modifier</button>
      </p>
    );
  }
  return (
    <form className="opp-time" onSubmit={submit}>
      <label htmlFor="tv">Une heure de ton temps vaut</label>
      <EuroInput id="tv" value={value} onChange={setValue} aria-label="Valeur d'une heure de ton temps" />
      <button className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy}>Enregistrer</button>
      <button type="button" className="btn quiet sm" onClick={() => setEditing(false)}>Annuler</button>
      {error && <span className="error" role="alert">{error}</span>}
    </form>
  );
}
