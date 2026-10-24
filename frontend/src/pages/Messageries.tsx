// Messageries : parler à Bank depuis WhatsApp, Telegram, Signal, Discord ou Slack, et y recevoir ses alertes.
// Une messagerie se configure en trois gestes (jeton, enregistrer, relier ton compte par un code), la confidentialité
// se choisit par messagerie, et rien ne se valide depuis une messagerie : on y lit, on y demande, on revient ici
// pour décider.

import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  useApi,
  type ChannelLinkView,
  type ChannelPlatform,
  type ChannelsView,
  type ConsentView,
  type PairingCode,
  type PrivacyMode,
} from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, Loading, useAsyncAction, useCelebrate, useToast } from "../components/ui";
import { safeHref, shortDate } from "../format";

const BLURB: Record<string, string> = {
  telegram: "Le plus simple : ton robot en deux minutes, rien à exposer sur internet.",
  whatsapp: "L'API officielle de Meta. Demande d'ouvrir une seule adresse au web.",
  signal: "Chiffré de bout en bout : personne d'autre ne lit tes chiffres.",
  discord: "Ton robot en message privé, sans rien à exposer.",
  slack: "Pour qui vit dans Slack : un message privé à l'app Bank.",
  ntfy: "Juste les alertes, en notification sur ton téléphone.",
};

const GUIDE: Record<string, string[]> = {
  telegram: [
    "Dans Telegram, ouvre @BotFather et envoie /newbot.",
    "Donne-lui un nom (« Bank de Micka »), puis un identifiant qui finit par « bot ».",
    "Copie le jeton qu'il te renvoie et colle-le ci-dessous.",
  ],
  whatsapp: [
    "Sur developers.facebook.com, crée une application « Business » et ajoute-lui le produit WhatsApp.",
    "Dans WhatsApp › Configuration de l'API : note l'identifiant du numéro et crée un jeton d'accès permanent (utilisateur système).",
    "Dans Paramètres de l'application › Général : copie la clé secrète.",
    "Enregistre ici, puis dans WhatsApp › Configuration, colle l'URL de rappel et le jeton de vérification affichés plus bas et abonne-toi au champ « messages ».",
  ],
  signal: [
    "Fais tourner signal-cli en service HTTP à côté de Bank, avec un numéro réservé à Bank (le guide CANAUX.md donne la commande).",
    "Indique son adresse (par exemple http://signal:8080) et ce numéro ci-dessous.",
  ],
  discord: [
    "Sur discord.com/developers/applications, crée une application, puis Bot › Reset Token et copie le jeton.",
    "Colle-le ci-dessous et enregistre : un lien d'invitation apparaît.",
    "Invite le robot sur un serveur à toi (même vide) : tu pourras alors lui écrire en message privé.",
  ],
  slack: [
    "Sur api.slack.com/apps, crée une app, active Socket Mode et génère un jeton d'application (portée connections:write).",
    "OAuth & Permissions : ajoute chat:write et im:history, installe l'app et copie le jeton du robot (xoxb-…).",
    "Event Subscriptions : abonne le robot à message.im ; App Home : autorise les messages.",
  ],
  ntfy: [
    "Installe l'application ntfy sur ton téléphone.",
    "Active ntfy ici : Bank choisit un sujet impossible à deviner.",
    "Dans l'application ntfy, abonne-toi à l'adresse affichée.",
  ],
};

const PRIVACY_SHORT: Record<PrivacyMode, string> = {
  redacted: "Anonymisé (conseillé)",
  aggregates: "Chiffres seuls",
  full: "Complet",
};
const PRIVACY: Record<PrivacyMode, string> = {
  redacted: "Anonymisé (conseillé) : marchands et montants, jamais un nom de personne ni un identifiant",
  aggregates: "Chiffres seuls : totaux, aucune opération ni titre de démarche",
  full: "Complet : y compris les titres que tu as écrits — à réserver à Signal",
};

const COMMANDS: [string, string][] = [
  ["/budget", "ce que tu peux dépenser aujourd'hui"],
  ["/missions", "tes meilleures pistes pour gagner ou économiser"],
  ["/demarches", "où en sont tes démarches"],
  ["/nouveau", "repartir d'une conversation vierge"],
  ["/pause", "couper les alertes sur ce compte (/reprendre pour les rallumer)"],
  ["/delier", "ne plus parler à Bank depuis ce compte"],
];

function statusOf(p: ChannelPlatform): { label: string; tone: string } {
  if (!p.configured) return { label: "À configurer", tone: "idle" };
  if (!p.enabled) return { label: "En pause", tone: "idle" };
  if (p.status === "online") return { label: "Connecté", tone: "ok" };
  if (p.status === "error") return { label: "Erreur", tone: "bad" };
  return { label: "Connexion…", tone: "wait" };
}

export function Messageries() {
  const view = useApi<ChannelsView>("/api/channels");
  const [selected, setSelected] = useState<string | null>(null);
  const data = view.data;

  // Tant qu'une messagerie se connecte, on relit l'état (le superviseur travaille en arrière-plan).
  const connecting = data?.platforms.some((p) => p.enabled && p.status === "connecting") ?? false;
  useEffect(() => {
    if (!connecting) return;
    const t = window.setInterval(view.reload, 2000);
    return () => window.clearInterval(t);
  }, [connecting, view.reload]);

  const current = data?.platforms.find((p) => p.key === selected) ?? null;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Messageries</h1>
          <p className="muted">Écris à Bank depuis ta messagerie et reçois ses alertes là où tu es déjà.</p>
        </div>
      </div>
      {view.error && <ErrorNote message={view.error} onRetry={view.reload} />}
      {!data && !view.error && <Loading what="Messageries" />}
      {data && !data.server_enabled && (
        <div className="notice"><Icon name="alert" /><span>Les messageries sont désactivées sur ce serveur (BANK_CHANNELS_ENABLED).</span></div>
      )}
      {data && data.server_enabled && (
        <>
          {!data.consent && <ConsentCard onDone={view.reload} />}
          <section className="section" aria-labelledby="ch-pick">
            <div className="section-head"><h2 id="ch-pick"><Icon name="chat" /> Choisis ta messagerie</h2></div>
            <div className="ch-grid">
              {data.platforms.map((p) => {
                const st = statusOf(p);
                return (
                  <Fragment key={p.key}>
                    <button type="button" className={`ch-tile ch-${p.key}`} aria-pressed={selected === p.key}
                            aria-controls={selected === p.key ? `ch-${p.key}-panel` : undefined}
                            onClick={() => setSelected(selected === p.key ? null : p.key)}>
                      <span className="ch-mark" aria-hidden="true">{p.label.slice(0, 1)}</span>
                      <span className="ch-name">{p.label}</span>
                      <span className="ch-blurb">{BLURB[p.key]}</span>
                      <span className="ch-foot">
                        <span className={`ch-status is-${st.tone}`}>{st.label}</span>
                        {p.links.length > 0 && <span className="ch-count">{p.links.length} relié{p.links.length > 1 ? "s" : ""}</span>}
                        {p.e2e && <span className="ch-badge"><Icon name="lock" /> bout en bout</span>}
                        {!p.inbound && <span className="ch-badge"><Icon name="bell" /> alertes</span>}
                      </span>
                    </button>
                    {/* Le panneau s'ouvre juste sous sa tuile : sur téléphone, pas besoin de chercher plus bas. */}
                    {current?.key === p.key && <PlatformPanel p={current} consent={data.consent} reload={view.reload} />}
                  </Fragment>
                );
              })}
            </div>
          </section>
          <Preferences view={data} reload={view.reload} />
          <section className="section" aria-labelledby="ch-cmd">
            <div className="section-head"><h2 id="ch-cmd"><Icon name="spark" /> Ce que tu peux lui écrire</h2></div>
            <p className="section-note">
              Une question en français (« je peux m'offrir un resto ce soir ? ») ou un raccourci. Bank ne te demandera
              jamais un mot de passe, un code ou un numéro de carte, et rien ne se valide depuis une messagerie.
            </p>
            <ul className="ch-commands">
              {COMMANDS.map(([cmd, text]) => <li key={cmd}><code>{cmd}</code> {text}</li>)}
            </ul>
          </section>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ accord
function ConsentCard({ onDone }: { onDone: () => void }) {
  const consents = useApi<ConsentView[]>("/api/consents");
  const [busy, error, run] = useAsyncAction();
  const spec = consents.data?.find((c) => c.purpose === "channels");
  const accept = () =>
    void run(async () => {
      await api("/api/consents/channels", { method: "PUT", body: { granted: true } });
      onDone();
    });
  return (
    <section className="notice warn ch-consent" aria-labelledby="ch-consent">
      <div>
        <h2 id="ch-consent">{spec?.title ?? "Bank dans tes messageries"}</h2>
        <p>{spec?.text ?? "Ce que Bank t'écrit passe par les serveurs de la messagerie choisie."}</p>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="button" className={`btn${busy ? " is-loading" : ""}`} disabled={busy} onClick={accept}>J'accepte</button>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ une messagerie
function PlatformPanel({ p, consent, reload }: { p: ChannelPlatform; consent: boolean; reload: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [privacy, setPrivacy] = useState<PrivacyMode>(p.privacy);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const st = statusOf(p);

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
      await api(`/api/channels/${p.key}`, { method: "PUT", body: { values: filled, privacy, enabled: true } });
      setValues({});
      toast(`${p.label} enregistré : connexion en cours.`);
      reload();
    });
  };
  const pause = () =>
    void run(async () => {
      await api(`/api/channels/${p.key}`, { method: "PUT", body: { enabled: !p.enabled } });
      reload();
    });
  const test = () =>
    void run(async () => {
      const r = await api<{ name: string | null }>(`/api/channels/${p.key}/test`, { method: "POST" });
      toast(`Ça répond : ${r.name ?? p.label}.`);
      reload();
    });
  const remove = () =>
    void run(async () => {
      if (!window.confirm(`Retirer ${p.label} ? Les jetons sont effacés et les comptes reliés déliés.`)) return;
      await api(`/api/channels/${p.key}`, { method: "DELETE" });
      reload();
    });

  return (
    <section className="ch-panel" id={`ch-${p.key}-panel`} aria-labelledby={`ch-${p.key}-title`}>
      <div className="section-head">
        <h2 id={`ch-${p.key}-title`}>{p.label}</h2>
        <span className={`ch-status is-${st.tone}`}>{st.label}{p.bot_name ? ` · ${p.bot_name}` : ""}</span>
      </div>
      {p.last_error && p.status === "error" && <p className="notice danger" role="alert">{p.last_error}</p>}
      <ol className="steps ch-guide">{GUIDE[p.key]?.map((step) => <li key={step}>{step}</li>)}</ol>
      {p.key === "whatsapp" && <WhatsAppSetup p={p} />}
      <form className="stack" onSubmit={save} aria-label={`Réglages ${p.label}`}>
        <div className="form-grid">
          {p.fields.map((f) => (
            <Field key={f.key} label={f.label}
                   hint={f.secret && f.set ? `Enregistré (${f.hint ?? "•••"}) : laisse vide pour le garder.` : `Exemple : ${f.example}`}>
              <input
                type={f.secret ? "password" : "text"}
                autoComplete="off"
                spellCheck={false}
                value={values[f.key] ?? (f.secret ? "" : (f.value ?? ""))}
                required={f.required && !f.set}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
              />
            </Field>
          ))}
          <Field label="Ce que Bank peut y écrire" hint={PRIVACY[privacy]}>
            <select value={privacy} onChange={(e) => setPrivacy(e.target.value as PrivacyMode)}>
              {(Object.keys(PRIVACY) as PrivacyMode[]).map((m) => <option key={m} value={m}>{PRIVACY_SHORT[m]}</option>)}
            </select>
          </Field>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || !consent}>
            {p.enabled ? "Enregistrer" : "Enregistrer et activer"}
          </button>
          {p.configured && <button type="button" className="btn quiet" disabled={busy} onClick={test}>Tester</button>}
          {p.configured && (
            <button type="button" className="btn quiet" disabled={busy} onClick={pause}>{p.enabled ? "Mettre en pause" : "Réactiver"}</button>
          )}
          {p.configured && <button type="button" className="btn ghost" disabled={busy} onClick={remove}>Retirer</button>}
        </div>
        {!consent && <p className="faint">Donne d'abord ton accord ci-dessus.</p>}
      </form>
      {p.key === "ntfy" && p.extra.subscribe_url && <NtfySubscribe url={p.extra.subscribe_url} />}
      {p.inbound && p.configured && p.enabled && consent && <Pairing p={p} reload={reload} />}
      <Links p={p} reload={reload} />
    </section>
  );
}

function copy(text: string, toast: (m: string) => void, what: string): void {
  void navigator.clipboard.writeText(text).then(
    () => toast(`${what} copié.`),
    () => toast("Copie impossible : sélectionne le texte à la main."),
  );
}

function WhatsAppSetup({ p }: { p: ChannelPlatform }) {
  const toast = useToast();
  const url = p.extra.webhook_url;
  const token = p.extra.verify_token;
  if (!url) return null;
  return (
    <div className="notice ch-webhook">
      <p>
        <b>À coller chez Meta</b> (WhatsApp › Configuration). Meta doit pouvoir joindre cette adresse depuis internet :
        n'expose que ce chemin (le guide CANAUX.md montre comment). Les alertes ne partent que dans les 24 h qui suivent
        ton dernier message au robot — c'est la règle de Meta.
      </p>
      <dl className="ch-kv">
        <dt>URL de rappel</dt>
        <dd><code>{url}</code> <button type="button" className="btn link" onClick={() => copy(url, toast, "URL")}>Copier</button></dd>
        {token && (
          <>
            <dt>Jeton de vérification</dt>
            <dd><code>{token}</code> <button type="button" className="btn link" onClick={() => copy(token, toast, "Jeton")}>Copier</button></dd>
          </>
        )}
      </dl>
    </div>
  );
}

function NtfySubscribe({ url }: { url: string }) {
  const toast = useToast();
  return (
    <div className="notice ch-webhook">
      <p><b>Abonne-toi à cette adresse</b> dans l'application ntfy. Garde-la pour toi : qui la connaît peut lire tes alertes.</p>
      <p><code>{url}</code> <button type="button" className="btn link" onClick={() => copy(url, toast, "Adresse")}>Copier</button></p>
    </div>
  );
}

// ------------------------------------------------------------------ relier ton compte par un code
function deepLink(p: ChannelPlatform, code: PairingCode): { href: string; label: string } | null {
  const e = p.extra;
  if (p.key === "telegram" && e.username) return { href: `https://t.me/${e.username}?start=${code.raw}`, label: "Ouvrir Telegram" };
  if (p.key === "whatsapp" && e.display_phone) {
    return { href: `https://wa.me/${e.display_phone}?text=${encodeURIComponent(code.code)}`, label: "Ouvrir WhatsApp" };
  }
  if (p.key === "signal" && e.account) return { href: `https://signal.me/#p/${e.account}`, label: "Ouvrir Signal" };
  if (p.key === "discord" && e.app_id) {
    return { href: `https://discord.com/oauth2/authorize?client_id=${e.app_id}&scope=bot&permissions=0`, label: "Inviter le robot" };
  }
  return null;
}

// Avec un lien direct, un geste suffit ; sans (robot pas encore joint), on dit où envoyer le code.
const SEND_HOW: Record<string, [string, string]> = {
  telegram: [
    "Touche le bouton : Telegram ouvre ton robot avec le code déjà prêt, il n'y a plus qu'à appuyer sur Démarrer.",
    "Ouvre la conversation avec ton robot dans Telegram et envoie-lui ce code.",
  ],
  whatsapp: [
    "Touche le bouton : WhatsApp ouvre la conversation avec le code déjà écrit, envoie-le.",
    "Envoie ce code au numéro WhatsApp de Bank.",
  ],
  signal: ["Touche le bouton pour ouvrir la conversation avec le numéro de Bank, puis envoie-lui ce code.",
           "Envoie ce code au numéro Signal de Bank."],
  discord: ["Invite le robot sur un serveur à toi, puis écris-lui ce code en message privé.",
            "Écris ce code en message privé à ton robot Discord."],
  slack: ["", "Dans Slack, ouvre l'app Bank › Messages et envoie-lui ce code."],
};

function Pairing({ p, reload }: { p: ChannelPlatform; reload: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState<PairingCode | null>(null);
  const [left, setLeft] = useState(0);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const celebrate = useCelebrate();
  const startCount = useRef(p.links.length);

  const start = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      startCount.current = p.links.length;
      setCode(await api<PairingCode>("/api/channels/pairing", { method: "POST", body: { password } }));
      setPassword("");
    });
  };
  const cancel = () =>
    void run(async () => {
      await api("/api/channels/pairing", { method: "DELETE" });
      setCode(null);
    });

  // Le compte à rebours, et une relecture régulière pour voir arriver ton compte.
  useEffect(() => {
    if (!code) return;
    const tick = () => setLeft(Math.max(0, Math.round((Date.parse(code.expires_at) - Date.now()) / 1000)));
    tick();
    const t = window.setInterval(tick, 1000);
    const poll = window.setInterval(reload, 3000);
    return () => {
      window.clearInterval(t);
      window.clearInterval(poll);
    };
  }, [code, reload]);
  useEffect(() => {
    if (code && p.links.length > startCount.current) {
      setCode(null);
      celebrate("done", `C'est relié ! Écris /budget à ton robot ${p.label} pour essayer.`);
    }
  }, [code, p.links.length, p.label, celebrate]);

  if (!code) {
    return (
      <form className="ch-pair" onSubmit={start} aria-label={`Relier mon compte ${p.label}`}>
        <h3><Icon name="send" /> Relier ton compte {p.label}</h3>
        <p className="faint">Bank n'écoute que les comptes que tu relies. Confirme ton mot de passe : tu reçois un code à envoyer au robot.</p>
        <div className="ch-pair-row">
          <input className="input" type="password" autoComplete="current-password" aria-label="Mot de passe" placeholder="Ton mot de passe"
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
          <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Obtenir un code</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    );
  }
  const link = deepLink(p, code);
  const href = link ? safeHref(link.href) : null;
  const minutes = Math.floor(left / 60);
  return (
    <div className="ch-pair is-live" role="status" aria-live="polite">
      <h3><Icon name="send" /> Envoie ce code à ton robot {p.label}</h3>
      <p className="ch-code" aria-label={`Code ${code.code.split("").join(" ")}`}>{code.code}</p>
      <p className="faint">{SEND_HOW[p.key]?.[href ? 0 : 1]}</p>
      <div className="actions">
        {href && link && <a className="btn" href={href} target="_blank" rel="noopener noreferrer">{link.label} <Icon name="external" /></a>}
        <button type="button" className="btn quiet" onClick={() => copy(code.code, toast, "Code")}><Icon name="copy" /> Copier</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={cancel}>Annuler</button>
      </div>
      <p className="ch-wait">
        <span className="ch-pulse" aria-hidden="true" />
        {left > 0 ? `En attente de ton message… code valable encore ${minutes} min ${String(left % 60).padStart(2, "0")} s.` : "Code expiré : demandes-en un autre."}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ comptes reliés
function Links({ p, reload }: { p: ChannelPlatform; reload: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  if (p.links.length === 0) return null;
  const toggle = (l: ChannelLinkView) =>
    void run(async () => {
      await api(`/api/channels/links/${l.id}`, { method: "PATCH", body: { notify: !l.notify } });
      reload();
    });
  const essai = (l: ChannelLinkView) =>
    void run(async () => {
      await api(`/api/channels/links/${l.id}/test`, { method: "POST" });
      toast("Essai envoyé : regarde ta messagerie.");
    });
  const unlink = (l: ChannelLinkView) =>
    void run(async () => {
      if (!window.confirm(`Délier « ${l.label} » ? Bank ne lui répondra plus et ne lui enverra plus rien.`)) return;
      await api(`/api/channels/links/${l.id}`, { method: "DELETE" });
      reload();
    });
  return (
    <div className="ch-links">
      <h3>Comptes reliés</h3>
      {error && <p className="error" role="alert">{error}</p>}
      <ul>
        {p.links.map((l) => (
          <li key={l.id}>
            <div>
              <b>{l.label}</b>
              <span className="faint">
                relié le {shortDate(l.linked_at)}
                {l.last_seen_at ? ` · dernier message le ${shortDate(l.last_seen_at)}` : ""}
              </span>
              {l.last_error && <span className="ch-link-error">{l.notify ? "" : "Alertes suspendues : "}{l.last_error}</span>}
            </div>
            <div className="actions">
              <label className="ch-switch">
                <input type="checkbox" checked={l.notify} disabled={busy} onChange={() => toggle(l)} /> Alertes
              </label>
              {p.status === "online" && <button type="button" className="btn quiet sm" disabled={busy} onClick={() => essai(l)}>Envoyer un essai</button>}
              {p.inbound && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => unlink(l)}>Délier</button>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ alertes
const ALERTS = [
  "Un risque de découvert dans le mois",
  "Une démarche envoyée restée sans réponse : le moment de relancer",
  "Un gain à confirmer pour remplir ta tirelire",
  "Une nouveauté officielle qui te concerne",
];

function Preferences({ view, reload }: { view: ChannelsView; reload: () => void }) {
  const [busy, error, run] = useAsyncAction();
  const [start, end] = view.quiet_hours;
  const save = (body: { quiet_hours?: number[]; weekly?: boolean }) =>
    void run(async () => {
      await api("/api/channels/preferences", { method: "PUT", body });
      reload();
    });
  const hours = Array.from({ length: 24 }, (_, h) => h);
  return (
    <section className="section" aria-labelledby="ch-alerts">
      <div className="section-head"><h2 id="ch-alerts"><Icon name="bell" /> Tes alertes</h2></div>
      <p className="section-note">
        Bank te prévient sur les comptes reliés (un seul message qui regroupe tout), une seule fois par événement :
      </p>
      <ul className="ch-alert-list">{ALERTS.map((a) => <li key={a}>{a}</li>)}</ul>
      <div className="form-grid">
        <Field label="Heures calmes : à partir de">
          <select value={start} disabled={busy} onChange={(e) => save({ quiet_hours: [Number(e.target.value), end] })}>
            {hours.map((h) => <option key={h} value={h}>{h} h</option>)}
          </select>
        </Field>
        <Field label="jusqu'à">
          <select value={end} disabled={busy} onChange={(e) => save({ quiet_hours: [start, Number(e.target.value)] })}>
            {hours.map((h) => <option key={h} value={h}>{h} h</option>)}
          </select>
        </Field>
      </div>
      <label className="ch-switch">
        <input type="checkbox" checked={view.weekly} disabled={busy} onChange={() => save({ weekly: !view.weekly })} />
        Le point de la semaine, le dimanche soir
      </label>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
