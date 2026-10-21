// Missions : des pistes concrètes pour récupérer, payer moins, gagner ou mettre de côté — tirées de tes comptes, de
// tes documents et de règles datées, la plus rentable d'abord. La tirelire ne se remplit qu'avec ce qui est
// constaté sur tes comptes et confirmé par toi ; l'« en route » (estimé) reste à côté, jamais additionné.

import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type Confirmation,
  type Opportunity,
  type OpportunityCategory,
  type OpportunityCenter,
  type SourceRef,
} from "../api";
import { Icon, type IconName } from "../components/Icon";
import { ErrorNote, EuroInput, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, perPeriod, plural, safeHref, shortDate } from "../format";
import { fullDate } from "./Documents";

const CATEGORY_ICON: Record<OpportunityCategory, IconName> = {
  recuperer: "undo",
  reduire: "trendDown",
  revenus: "trendUp",
  epargne: "wealth",
  anticiper: "clock",
};
// Paliers de la tirelire (en centimes, sur un an) : de quoi fêter le premier gain sans attendre des mois.
const MILESTONES = [2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000];

export function nextMilestone(cents: number): number {
  return MILESTONES.find((m) => m > cents) ?? Math.ceil((cents + 1) / 1_000_000) * 1_000_000;
}

export function chanceLevel(p: number): { dots: number; label: string } {
  if (p >= 0.7) return { dots: 3, label: "Chance forte" };
  if (p >= 0.4) return { dots: 2, label: "Chance moyenne" };
  return { dots: 1, label: "Chance faible" };
}

function effort(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m}` : `${h} h`;
}

export function Opportunities() {
  const center = useApi<OpportunityCenter>("/api/opportunities");
  const [filter, setFilter] = useState<OpportunityCategory | "all" | "mine">("all");
  const [coin, setCoin] = useState(0); // une pièce tombe dans la tirelire quand tu confirmes un gain
  if (center.error) return <ErrorNote message={center.error} onRetry={center.reload} />;
  if (!center.data) return <Loading what="Recherche de missions" />;
  const c = center.data;
  const counts = c.opportunities.reduce<Record<string, number>>((acc, o) => ({ ...acc, [o.category]: (acc[o.category] ?? 0) + 1 }), {});
  const shown = c.opportunities.filter((o) =>
    filter === "all" ? true : filter === "mine" ? c.priorities.includes(o.category) : o.category === filter,
  );
  const [featured, ...rest] = shown;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tes missions</h1>
          <p className="muted">Des pistes concrètes pour récupérer ou économiser de l'argent, la plus rentable d'abord. Tu choisis, Bank prépare.</p>
        </div>
      </div>

      <PiggyBank value={c.value} coin={coin} />

      {c.confirmations.length > 0 && (
        <section className="section" aria-labelledby="confirm-title">
          <h2 id="confirm-title">De l'argent est arrivé</h2>
          <div className="stack">
            {c.confirmations.map((x) => <ConfirmGain key={x.key} item={x} onDone={() => { setCoin((n) => n + 1); center.reload(); }} />)}
          </div>
        </section>
      )}

      {c.opportunities.length > 1 && (
        <div className="chips opp-filters" role="group" aria-label="Filtrer les missions">
          <button type="button" className="chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            Tout <span className="chip-count">{c.opportunities.length}</span>
          </button>
          {c.priorities.length > 0 && (
            <button type="button" className="chip" aria-pressed={filter === "mine"} onClick={() => setFilter("mine")}>Tes priorités</button>
          )}
          {(Object.keys(c.categories) as OpportunityCategory[]).filter((k) => counts[k]).map((k) => (
            <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>
              {c.categories[k]} <span className="chip-count">{counts[k]}</span>
            </button>
          ))}
        </div>
      )}

      {shown.length === 0 && (
        <section className="card card-quiet opp-empty">
          <h2>Pas de mission pour l'instant</h2>
          <p className="muted">Bank regarde à nouveau à chaque synchronisation : nouveaux frais, abonnements, droits aux aides, papiers qui expirent.</p>
        </section>
      )}
      {featured && (
        <section className="section opp-group" aria-labelledby="featured-title">
          <h2 id="featured-title">{featured.deadline ? "À faire en premier" : "La mission du moment"}</h2>
          <ol className="opp-list"><MissionCard opp={featured} tv={c.time_value_cents_per_hour} onChange={center.reload} featured /></ol>
        </section>
      )}
      {rest.length > 0 && (
        <section className="section opp-group" aria-labelledby="leads-title">
          <h2 id="leads-title">Les autres missions</h2>
          <ol className="opp-list">{rest.map((o) => <MissionCard key={o.key} opp={o} tv={c.time_value_cents_per_hour} onChange={center.reload} />)}</ol>
        </section>
      )}

      <footer className="opp-foot">
        <details className="opp-how">
          <summary>Comment Bank classe les missions ?</summary>
          <p className="faint">
            Pour chacune : ce qu'elle peut rapporter sur un an, multiplié par la chance que ça marche, moins le temps
            qu'elle te prend. Les échéances proches passent devant. Rien n'est promis : un droit se confirme auprès de
            l'organisme, un gain se constate sur tes comptes.
          </p>
          <TimeValue cents={c.time_value_cents_per_hour} onSaved={center.reload} />
        </details>
        {c.hidden > 0 && <p className="faint">{plural(c.hidden, "mission écartée ou remise à plus tard", "missions écartées ou remises à plus tard")} : elles reviennent d'elles-mêmes à la date choisie.</p>}
        <p className="faint">{c.disclaimer}</p>
      </footer>
    </>
  );
}

// ------------------------------------------------------------------ la tirelire
function PiggyBank({ value, coin }: { value: OpportunityCenter["value"]; coin: number }) {
  const [open, setOpen] = useState(false);
  const got = value.confirmed_yearly_cents;
  const target = nextMilestone(got);
  const fill = Math.min(1, got / target);
  const reached = MILESTONES.filter((m) => m <= got);
  return (
    <section className="card piggy" aria-labelledby="piggy-title">
      <Jar fill={fill} coin={coin} />
      <div className="piggy-body">
        <h2 id="piggy-title" className="piggy-title">Ta tirelire</h2>
        <p className={`piggy-figure${got === 0 ? " is-zero" : ""}`}>{money(got, { whole: got === 0 || got >= 10_000 })}</p>
        <p className="muted">récupérés grâce à Bank, sur un an · constaté sur tes comptes et confirmé par toi</p>
        <div className="piggy-next">
          <span className="piggy-bar" aria-hidden="true"><i style={{ width: `${Math.round(fill * 100)}%` }} /></span>
          <span className="faint">Prochain palier : {money(target, { whole: true })}</span>
        </div>
        {reached.length > 0 && (
          <ul className="piggy-badges" aria-label="Paliers atteints">
            {reached.map((m) => <li key={m}><Icon name="coin" size={14} /> {money(m, { whole: true })}</li>)}
          </ul>
        )}
        <p className="piggy-road">
          <span className="piggy-road-label">En route</span>{" "}
          {value.estimated_yearly_cents ? (
            <>≈ {money(value.estimated_yearly_cents, { whole: true })} <span className="faint">si tes démarches en cours aboutissent (selon leurs chances)</span></>
          ) : (
            <span className="faint">rien pour l'instant : lance une mission</span>
          )}
        </p>
        {(value.history.length > 0 || value.pending.length > 0) && (
          <>
            <button type="button" className="btn link" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Masquer le détail" : "Voir le détail"}
            </button>
            {open && (
              <ul className="plain-list value-events">
                {value.history.map((v, i) => (
                  <li key={`c${i}`}><span className="tag ok">Obtenu</span> {v.label} — <b>{perPeriod(v.amount_cents, v.period)}</b> <span className="faint">le {shortDate(v.on)} · {v.evidence}</span></li>
                ))}
                {value.pending.map((v, i) => (
                  <li key={`p${i}`}><span className="tag">En route</span> {v.label} — ≈ {perPeriod(v.amount_cents, v.period)} <span className="faint">{v.evidence}</span></li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/** Le bocal : se remplit à l'ouverture (un seul mouvement), une pièce y tombe quand tu confirmes un gain. */
export function Jar({ fill, coin = 0 }: { fill: number; coin?: number }) {
  const ref = useRef<SVGGElement>(null);
  const level = 126 - Math.round(fill * 96); // niveau du liquide : y=126 (vide, rien ne dépasse) à y=30 (plein)
  useEffect(() => {
    const el = ref.current;
    if (!coin || !el) return;
    el.classList.remove("is-dropping");
    void el.getBoundingClientRect(); // relance l'animation
    el.classList.add("is-dropping");
  }, [coin]);
  return (
    <svg className="jar" viewBox="0 0 120 140" role="img" aria-label={`Tirelire remplie à ${Math.round(fill * 100)} % du prochain palier`}>
      <defs>
        <clipPath id="jar-inside">
          <path d="M32 36 Q32 30 40 30 H80 Q88 30 88 36 V114 Q88 124 78 124 H42 Q32 124 32 114 Z" />
        </clipPath>
      </defs>
      <g clipPath="url(#jar-inside)">
        <g className="jar-fill" style={{ ["--level" as string]: `${level}px` }}>
          <path className="jar-liquid" d="M20 0 Q35 -5 50 0 T80 0 T110 0 V140 H20 Z" />
          <circle className="jar-bubble" cx="48" cy="14" r="3" />
          <circle className="jar-bubble" cx="70" cy="26" r="2" />
        </g>
      </g>
      <path className="jar-glass" d="M32 36 Q32 30 40 30 H80 Q88 30 88 36 V114 Q88 124 78 124 H42 Q32 124 32 114 Z" />
      <path className="jar-shine" d="M40 44 V100" />
      <rect className="jar-lid" x="36" y="16" width="48" height="12" rx="4" />
      <rect className="jar-slot" x="51" y="20.5" width="18" height="3" rx="1.5" />
      <g ref={ref} className="jar-coin" aria-hidden="true">
        <circle cx="60" cy="2" r="7" />
        <path d="M62.5 -1.2 a3.2 3.2 0 1 0 0 6.4 M56.6 1.2 h4 M56.6 3 h4" />
      </g>
    </svg>
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
      toast(`+${perPeriod(item.amount_cents, item.period)} dans ta tirelire !`);
      onDone();
    });
  return (
    <div className="notice ok opp-confirm" role="group" aria-label={item.title}>
      <Icon name="coin" />
      <div>
        <p><b>{item.title}</b> : {item.label}.</p>
        <p className="muted">C'est bien le résultat de ta démarche ? Confirme-le : {perPeriod(item.amount_cents, item.period)} passent dans ta tirelire et la démarche est close.</p>
        <div className="actions">
          <button type="button" className={`btn sm${busy ? " is-loading" : ""}`} disabled={busy} onClick={confirm}>Confirmer ce gain</button>
          <a className="btn quiet sm" href={`#/demarches/${item.procedure_id}`}>Ouvrir la démarche</a>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ une mission
function MissionCard({ opp, tv, onChange, featured = false }: { opp: Opportunity; tv: number; onChange: () => void; featured?: boolean }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const noMoney = opp.amount_cents === 0;
  const chance = chanceLevel(opp.probability);
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
    <li className={`opp mission is-${opp.category}${featured ? " is-featured" : ""}${opp.procedure ? " is-started" : ""}`}>
      <div className="mission-top">
        <span className="mission-kind"><Icon name={CATEGORY_ICON[opp.category]} size={16} /> {opp.category_label}</span>
        {noMoney ? <span className="reward is-none">À vérifier</span> : <span className="reward">+{perPeriod(opp.amount_cents, opp.period)}</span>}
      </div>
      <h3>{opp.title}</h3>
      <p className="muted">{opp.summary}</p>
      <div className="mission-meta">
        <span><Icon name="clock" size={15} /> {effort(opp.effort_minutes)}</span>
        <span className="chance" aria-label={`${chance.label} (${Math.round(opp.probability * 100)} %)`} title={`${Math.round(opp.probability * 100)} %`}>
          {[1, 2, 3].map((d) => <i key={d} className={d <= chance.dots ? "is-on" : ""} aria-hidden="true" />)} {chance.label}
        </span>
        {opp.deadline && <span className="tag warn"><Icon name="clock" size={12} /> avant le {fullDate(opp.deadline)}</span>}
        {opp.confidence === "faible" && <span className="tag">Incertain</span>}
      </div>
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
            <button type="button" className="btn quiet sm" disabled={busy} onClick={() => decide("snoozed", "C'est noté : on en reparle dans 30 jours.")}>Plus tard</button>
            <button type="button" className="btn ghost sm" disabled={busy} onClick={() => decide("dismissed", "Mission écartée.")}>Pas pour moi</button>
          </>
        )}
      </div>
      <details className="opp-why">
        <summary>Pourquoi ?</summary>
        {!noMoney && (
          <p className="faint">
            Gain net attendu sur un an : <b>{money(opp.expected_net_cents, { whole: true })}</b> ({money(opp.yearly_cents, { whole: true })} × {Math.round(opp.probability * 100)} % − {effort(opp.effort_minutes)} à {money(tv, { whole: true })} de l'heure).
          </p>
        )}
        <Evidence title="Constaté dans tes données" items={opp.facts} tone="fact" />
        <Evidence title="Estimé par Bank" items={opp.estimates} tone="estimate" />
        <Evidence title="Supposé (à vérifier)" items={opp.hypotheses} tone="hypothesis" />
        {opp.sources.length > 0 && <Sources sources={opp.sources} />}
      </details>
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
        Une heure de ton temps compte pour <b>{money(cents, { whole: true })}</b> (une hypothèse, à toi de la régler).{" "}
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
