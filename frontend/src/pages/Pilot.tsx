// Pilote automatique : laisser ton agent agir seul, dans un cadre que toi seul fixes — et tout couper d'un geste.
// L'état d'abord (en marche, en essai, arrêté), le bouton d'arrêt toujours à portée, ce qui est déjà engagé sur
// tes plafonds, les règles qui ne se règlent pas, puis le cadre (mot de passe exigé) et le journal de chaque
// demande, refus compris.

import { useState, type FormEvent } from "react";
import { api, useApi, type Me, type PilotDestination, type PilotKind, type PilotMove, type PilotRule, type PilotView } from "../api";
import { Icon } from "../components/Icon";
import { PilotTrack } from "../components/PilotTrack";
import { Segmented } from "../components/Segmented";
import { ErrorNote, EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, shortDate } from "../format";

const KINDS: { value: PilotKind; label: string }[] = [
  { value: "buy", label: "Acheter" },
  { value: "sell", label: "Vendre" },
  { value: "swap", label: "Échanger" },
  { value: "transfer", label: "Envoyer" },
];
const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const STATUS: Record<PilotMove["status"], { label: string; tone: string }> = {
  reserved: { label: "autorisé, en cours", tone: "" },
  executed: { label: "exécuté", tone: "ok" },
  failed: { label: "échoué", tone: "warn" },
  unreported: { label: "non rapporté", tone: "danger" },
  revoked: { label: "arrêté avant compte rendu", tone: "warn" },
  simulated: { label: "essai", tone: "" },
  refused: { label: "refusé", tone: "danger" },
};

type Draft = {
  agent_token_id: number | null;
  mode: "rules" | "free";
  trial: boolean;
  scopes: ("crypto_spot" | "transfer")[];
  assets: string[];
  rules: PilotRule[];
  destinations: PilotDestination[];
  per_op_cents: number | null;
  per_day_cents: number | null;
  per_month_cents: number | null;
  max_moves_per_day: number;
  max_loss_cents: number | null;
  days: number;
};

function draftOf(v: PilotView): Draft {
  return {
    agent_token_id: v.agent_token_id ?? v.agents[0]?.id ?? null,
    mode: v.mode,
    trial: v.enabled ? v.trial : true,
    scopes: v.scopes,
    assets: v.assets,
    rules: v.rules.map(({ kind, asset, to_asset, destination, amount_cents, every, on }) => ({ kind, asset, to_asset, destination, amount_cents, every, on: on ?? null })),
    destinations: v.destinations,
    per_op_cents: v.limits.per_op_cents,
    per_day_cents: v.limits.per_day_cents,
    per_month_cents: v.limits.per_month_cents,
    max_moves_per_day: v.limits.max_moves_per_day,
    max_loss_cents: v.limits.max_loss_cents,
    days: 30,
  };
}

export function ruleText(r: PilotRule, every: Record<string, string>): string {
  const verb = KINDS.find((k) => k.value === r.kind)?.label ?? r.kind;
  let t = `${verb} ${money(r.amount_cents)} de ${r.asset}`;
  if (r.kind === "swap" && r.to_asset) t += ` contre ${r.to_asset}`;
  if (r.kind === "transfer" && r.destination) t += ` vers « ${r.destination} »`;
  if (r.on && r.every === "week") return `${t} chaque semaine, prévu le ${WEEKDAYS[r.on - 1] ?? ""}`;
  if (r.on && r.every === "month") return `${t} chaque mois, prévu le ${r.on === 1 ? "1er" : r.on}`;
  return `${t} ${every[r.every] ?? r.every}`;
}

export function Pilot() {
  const v = useApi<PilotView>("/api/autonomy");
  const me = useApi<Me>("/api/auth/me");
  const [view, setView] = useState<PilotView | null>(null);
  const data = view ?? v.data;
  const toast = useToast();
  const [busy, error, run] = useAsyncAction();
  if (v.error) return <ErrorNote message={v.error} onRetry={v.reload} />;
  if (!data) return <Loading />;

  const stop = () =>
    run(async () => {
      setView(await api<PilotView>("/api/autonomy/stop", { method: "POST" }));
      toast("Pilote arrêté : plus rien ne bouge sans toi.");
    });

  const state = data.active ? (data.trial ? "trial" : "on") : "off";
  const expires = data.expires_at ? new Date(data.expires_at) : null;
  const daysLeft = expires ? Math.max(0, Math.ceil((expires.getTime() - Date.now()) / 86_400_000)) : 0;
  const anomaly = !data.active && data.disabled_reason && !/par toi|Arrivé à échéance/.test(data.disabled_reason);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Pilote automatique</h1>
          <p className="muted">Ton agent agit seul, dans ton cadre. Tu coupes quand tu veux, d'ici ou d'un « /pilote stop ».</p>
        </div>
      </div>

      <section className={`card card-hero pl-hero is-${state} reveal`} aria-labelledby="pl-state">
        <div className="pl-state-row">
          <span className="pl-light" aria-hidden="true" />
          <div>
            <p className="pl-state" id="pl-state">{state === "on" ? "En marche" : state === "trial" ? "En essai" : "Arrêté"}</p>
            <p className="muted">
              {state === "off"
                ? data.disabled_reason
                  ? `${data.disabled_reason}${data.disabled_at ? `, le ${shortDate(data.disabled_at.slice(0, 10))}` : ""}. Rien ne bouge sans toi.`
                  : "Rien ne bouge sans toi. Règle un cadre plus bas pour le mettre en route."
                : `Pour « ${data.agent_name} », encore ${daysLeft} jour${daysLeft > 1 ? "s" : ""}. ${state === "trial" ? "Il dit ce qu'il ferait, sans rien exécuter." : "Chaque mouvement est autorisé par Bank avant de partir."}`}
            </p>
          </div>
          {data.enabled && (
            <button type="button" className={`btn danger lg pl-stop${busy ? " is-loading" : ""}`} disabled={busy} onClick={() => void stop()}>
              <Icon name="close" /> Tout arrêter
            </button>
          )}
        </div>
        {anomaly && (
          <p className="notice danger" style={{ marginTop: "1rem" }}><Icon name="alert" /> <span>Arrêt de sécurité : {data.disabled_reason}. Regarde le journal avant de relancer.</span></p>
        )}
        {error && <ErrorNote message={error} />}
        <div className="pl-gauges">
          <Gauge label="Engagé sur 24 h" used={data.usage.day_cents} cap={data.limits.per_day_cents} />
          <Gauge label="Engagé sur 30 jours" used={data.usage.month_cents} cap={data.limits.per_month_cents} />
          <Gauge label="Opérations sur 24 h" used={data.usage.moves_day} cap={data.limits.max_moves_per_day} count />
          <Gauge label="Pertes depuis l'activation" used={data.usage.loss_cents} cap={data.limits.max_loss_cents} danger />
        </div>
      </section>

      <section className="card reveal pl-rules" style={{ marginTop: "1rem" }} aria-labelledby="pl-hard">
        <h2 id="pl-hard">Ce qui ne se règle pas</h2>
        <ul>{data.hard_rules.map((r) => <li key={r}><Icon name="lock" /> {r}</li>)}</ul>
      </section>

      <Frame view={data} totp={me.data?.totp_enabled ?? false} onSaved={(nv) => { setView(nv); toast(nv.active ? "Pilote en route, dans ton cadre." : "Cadre enregistré."); }} />

      <PilotTrack />

      <Journal moves={data.journal} />
    </>
  );
}

function Gauge({ label, used, cap, count = false, danger = false }: { label: string; used: number; cap: number; count?: boolean; danger?: boolean }) {
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const status = pct >= 100 ? "over" : pct >= 75 ? "warning" : undefined;
  return (
    <div className="pl-gauge">
      <p className="faint">{label}</p>
      <p><b>{count ? used : money(used, { whole: true })}</b> <span className="faint">/ {count ? cap : cap > 0 ? money(cap, { whole: true }) : "sans seuil"}</span></p>
      <div className="meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used} data-status={danger && used > 0 ? "warning" : status}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ le cadre
function Frame({ view, totp, onSaved }: { view: PilotView; totp: boolean; onSaved: (v: PilotView) => void }) {
  const [d, setD] = useState<Draft>(() => draftOf(view));
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [assetText, setAssetText] = useState(view.assets.join(", "));
  const [busy, error, run] = useAsyncAction();
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const toggleScope = (s: "crypto_spot" | "transfer") =>
    set({ scopes: d.scopes.includes(s) ? d.scopes.filter((x) => x !== s) : [...d.scopes, s] });

  const save = (enabled: boolean) => (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const assets = assetText.split(/[\s,;]+/).map((a) => a.trim().toUpperCase()).filter(Boolean);
      const nv = await api<PilotView>("/api/autonomy", {
        method: "PUT",
        body: { ...d, assets, enabled, password, code: code || null, days: enabled ? d.days : 0 },
      });
      setPassword("");
      setCode("");
      onSaved(nv);
    });
  };

  if (view.no_crypto) {
    return (
      <section className="card" style={{ marginTop: "1rem" }}>
        <h2>Le cadre</h2>
        <p className="notice warn"><Icon name="alert" /> <span>Tes lignes rouges excluent la crypto : le pilote ne peut pas s'activer. Tu peux les revoir dans Ma situation.</span></p>
      </section>
    );
  }

  return (
    <form className="card reveal pl-frame" style={{ marginTop: "1rem" }} onSubmit={save(true)} aria-labelledby="pl-frame">
      <div className="card-head"><h2 id="pl-frame">Le cadre</h2><span className="faint">Mot de passe exigé pour changer</span></div>

      <fieldset>
        <legend>Qui</legend>
        {view.agents.length === 0 ? (
          <p className="muted">Aucun agent ne peut recevoir le pilote : crée un jeton de rôle Conseiller ou Gestionnaire dans <a href="#/connecteurs">Connecteurs › Agents IA</a>.</p>
        ) : (
          <Field label="Agent autorisé" hint="Le pilote suit ce jeton précis : un nouveau jeton du même nom n'hérite de rien.">
            <select value={d.agent_token_id ?? ""} onChange={(e) => set({ agent_token_id: Number(e.target.value) || null })}>
              {view.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </Field>
        )}
        {view.agents.find((a) => a.id === d.agent_token_id)?.ip_locked === false && (
          <div className="notice warn" role="note">
            <Icon name="alert" />
            <span>
              Ce jeton marche depuis n'importe quelle adresse. Restreins-le à la machine de ton agent dans{" "}
              <a href="#/connecteurs">Connecteurs › Agents IA</a> : un jeton copié ailleurs ne pourra plus rien demander.
            </span>
          </div>
        )}
      </fieldset>

      <fieldset>
        <legend>Quoi</legend>
        <label className="check"><input type="checkbox" checked={d.scopes.includes("crypto_spot")} onChange={() => toggleScope("crypto_spot")} /> {view.scope_labels.crypto_spot}</label>
        <label className="check"><input type="checkbox" checked={d.scopes.includes("transfer")} onChange={() => toggleScope("transfer")} /> {view.scope_labels.transfer}</label>
        <Field label="Actifs permis" hint="Séparés par des virgules. Un équivalent « wrapped » n'est pas l'actif lui-même.">
          <input value={assetText} onChange={(e) => setAssetText(e.target.value)} />
        </Field>
        <Segmented value={d.mode} onChange={(mode) => set({ mode })} label="Façon de faire"
                   options={[{ value: "rules", label: "Seulement mes consignes" }, { value: "free", label: "Libre dans mes plafonds" }]} />
        <p className="faint">{d.mode === "rules" ? "L'agent ne fait que ce que tes consignes disent, à leur rythme." : "L'agent choisit, mais jamais au-delà de tes plafonds ni hors de tes actifs."}</p>
        <Rules draft={d} set={set} every={view.every_labels} />
      </fieldset>

      {d.scopes.includes("transfer") && <Destinations draft={d} set={set} chains={view.chains} />}

      <fieldset>
        <legend>Combien</legend>
        <div className="form-grid pl-limits">
          <Field label="Par opération" hint={`Au plus ${money(view.hard_max.per_op_cents, { whole: true })}`}><EuroInput value={d.per_op_cents} onChange={(v) => set({ per_op_cents: v })} /></Field>
          <Field label="Sur 24 h glissantes"><EuroInput value={d.per_day_cents} onChange={(v) => set({ per_day_cents: v })} /></Field>
          <Field label="Sur 30 jours glissants"><EuroInput value={d.per_month_cents} onChange={(v) => set({ per_month_cents: v })} /></Field>
          <Field label="Opérations par 24 h"><input type="number" min={1} max={20} value={d.max_moves_per_day} onChange={(e) => set({ max_moves_per_day: Number(e.target.value) })} /></Field>
          <Field label="Perte maximale" hint="Atteinte : le pilote s'arrête seul. 0 € : dès la première perte"><EuroInput value={d.max_loss_cents} onChange={(v) => set({ max_loss_cents: v })} /></Field>
          <Field label="Durée" hint={`1 à ${view.hard_max.days} jours, renouvelable`}><input type="number" min={1} max={view.hard_max.days} value={d.days} onChange={(e) => set({ days: Number(e.target.value) })} /></Field>
        </div>
      </fieldset>

      <fieldset>
        <legend>Confirmer</legend>
        <label className="check"><input type="checkbox" checked={d.trial} onChange={(e) => set({ trial: e.target.checked })} /> Mode essai : l'agent dit ce qu'il ferait, sans rien exécuter (conseillé pour commencer)</label>
        <div className="form-grid">
          <Field label="Ton mot de passe"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          {totp && <Field label="Code de double authentification"><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={8} required /></Field>}
        </div>
      </fieldset>
      {error && <ErrorNote message={error} />}
      <div className="actions">
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || view.agents.length === 0}>
          <Icon name="check" /> {view.enabled ? "Renouveler avec ce cadre" : d.trial ? "Lancer l'essai" : "Mettre en route"}
        </button>
        <button type="button" className="btn quiet" disabled={busy || !password} onClick={(e) => save(false)(e as unknown as FormEvent)}>
          Enregistrer sans activer
        </button>
      </div>
    </form>
  );
}

function Rules({ draft, set, every }: { draft: Draft; set: (p: Partial<Draft>) => void; every: Record<string, string> }) {
  const [r, setR] = useState<PilotRule>({ kind: "buy", asset: draft.assets[0] ?? "BTC", to_asset: null, destination: null, amount_cents: 2000, every: "week", on: null });
  const add = () => {
    set({ rules: [...draft.rules, { ...r, asset: r.asset.toUpperCase(), to_asset: r.kind === "swap" ? r.to_asset?.toUpperCase() ?? null : null, destination: r.kind === "transfer" ? r.destination : null }] });
  };
  return (
    <div className="pl-rules-list">
      <p className="faint">Tes consignes {draft.mode === "rules" ? "(au moins une)" : "(facultatives en mode libre)"}</p>
      {draft.rules.length > 0 && (
        <ul>
          {draft.rules.map((x, i) => (
            <li key={`${x.kind}${x.asset}${i}`}>
              <span>{ruleText(x, every)}</span>
              <button type="button" className="btn link" aria-label="Retirer la consigne" onClick={() => set({ rules: draft.rules.filter((_, j) => j !== i) })}><Icon name="trash" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="form-grid pl-rule-form">
        <Field label="Action">
          <select value={r.kind} onChange={(e) => setR({ ...r, kind: e.target.value as PilotKind })}>
            {KINDS.map((k) => <option key={k.value} value={k.value} disabled={k.value === "transfer" && !draft.scopes.includes("transfer")}>{k.label}</option>)}
          </select>
        </Field>
        <Field label="Montant au plus"><EuroInput value={r.amount_cents} onChange={(v) => setR({ ...r, amount_cents: v ?? 0 })} /></Field>
        <Field label="Actif"><input value={r.asset} onChange={(e) => setR({ ...r, asset: e.target.value })} maxLength={12} /></Field>
        {r.kind === "swap" && <Field label="Contre"><input value={r.to_asset ?? ""} onChange={(e) => setR({ ...r, to_asset: e.target.value })} maxLength={12} /></Field>}
        {r.kind === "transfer" && (
          <Field label="Vers">
            <select value={r.destination ?? ""} onChange={(e) => setR({ ...r, destination: e.target.value || null })}>
              <option value="">Choisis une adresse</option>
              {draft.destinations.map((x) => <option key={x.label} value={x.label}>{x.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Rythme">
          <select value={r.every} onChange={(e) => setR({ ...r, every: e.target.value as PilotRule["every"], on: null })}>
            {Object.entries(every).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </Field>
        {r.every !== "day" && (
          <Field label="Jour prévu" hint="Pour le suivi « pilote vs consigne » ; un 31 tombe le dernier jour des mois courts">
            <select value={r.on ?? ""} onChange={(e) => setR({ ...r, on: Number(e.target.value) || null })}>
              <option value="">{r.every === "week" ? "N'importe quel jour" : "N'importe quand dans le mois"}</option>
              {r.every === "week"
                ? WEEKDAYS.map((d, i) => <option key={d} value={i + 1}>{d}</option>)
                : Array.from({ length: 31 }, (_, i) => <option key={i} value={i + 1}>le {i === 0 ? "1er" : i + 1}</option>)}
            </select>
          </Field>
        )}
        <button type="button" className="btn quiet" onClick={add} disabled={!r.amount_cents}><Icon name="plus" /> Ajouter la consigne</button>
      </div>
    </div>
  );
}

function Destinations({ draft, set, chains }: { draft: Draft; set: (p: Partial<Draft>) => void; chains: string[] }) {
  const [x, setX] = useState<PilotDestination>({ label: "", chain: "ethereum", address: "" });
  return (
    <fieldset>
      <legend>Vers où</legend>
      <p className="faint">Seulement des adresses à toi. Une adresse qui ressemble à une autre (mêmes débuts, mêmes fins) est refusée : c'est la signature de l'empoisonnement.</p>
      {draft.destinations.length > 0 && (
        <ul className="pl-dest">
          {draft.destinations.map((dst) => (
            <li key={dst.label}>
              <b>{dst.label}</b> <span className="tag">{dst.chain}</span> <code className="cx-address">{dst.address}</code>
              <button type="button" className="btn link" aria-label={`Retirer ${dst.label}`} onClick={() => set({ destinations: draft.destinations.filter((y) => y.label !== dst.label) })}><Icon name="trash" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="form-grid">
        <Field label="Nom"><input value={x.label} onChange={(e) => setX({ ...x, label: e.target.value })} maxLength={60} placeholder="Mon Ledger" /></Field>
        <Field label="Réseau">
          <select value={x.chain} onChange={(e) => setX({ ...x, chain: e.target.value })}>{chains.map((c) => <option key={c} value={c}>{c}</option>)}</select>
        </Field>
        <Field label="Adresse (en entier)"><input value={x.address} onChange={(e) => setX({ ...x, address: e.target.value.trim() })} spellCheck={false} autoComplete="off" /></Field>
        <button type="button" className="btn quiet" disabled={!x.label || x.address.length < 10}
                onClick={() => { set({ destinations: [...draft.destinations, x] }); setX({ label: "", chain: x.chain, address: "" }); }}>
          <Icon name="plus" /> Ajouter l'adresse
        </button>
      </div>
    </fieldset>
  );
}

// ------------------------------------------------------------------ journal
function Journal({ moves }: { moves: PilotMove[] }) {
  return (
    <section className="card reveal" style={{ marginTop: "1rem" }} aria-labelledby="pl-journal">
      <div className="card-head"><h2 id="pl-journal">Journal</h2><span className="faint">Chaque demande de l'agent, refus compris</span></div>
      {moves.length === 0 ? (
        <p className="muted">Rien encore. Chaque demande apparaîtra ici, avec la raison donnée par l'agent et la décision de Bank.</p>
      ) : (
        <ol className="pl-journal">
          {moves.map((m) => (
            <li key={m.id} className={`is-${m.status}`}>
              <time dateTime={m.created_at}>{shortDate(m.created_at.slice(0, 10))} · {m.created_at.slice(11, 16)}</time>
              <div>
                <p><b>{m.text}</b> <span className={`tag ${STATUS[m.status].tone}`}>{STATUS[m.status].label}</span></p>
                <p className="faint">« {m.reason} »{m.rule ? ` — consigne : ${m.rule}` : ""}</p>
                {m.refusal && m.status !== "executed" && <p className="pl-refusal">{m.refusal}</p>}
                {m.tx_ref && <p className="faint">Réf. <code>{m.tx_ref}</code></p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
