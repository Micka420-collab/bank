// Réglages › Agents IA : un jeton par agent, des droits minimaux, révocable en un clic.

import { useState, type FormEvent } from "react";
import { api, useApi, type AgentCatalog, type AgentToken, type AgentTokenCreated, type PrivacyMode } from "../api";
import { Icon } from "../components/Icon";
import { ErrorNote, Field, useAsyncAction, useToast } from "../components/ui";
import { shortDate } from "../format";

const PRESET_TEXT: Record<string, { title: string; text: string }> = {
  observateur: { title: "Observateur", text: "Lit ta situation et te fait des points. Ne touche à rien." },
  conseiller: {
    title: "Conseiller",
    text: "Lit tes opérations, simule tes droits, mène ton onboarding et te propose des réglages à valider.",
  },
  gestionnaire: {
    title: "Gestionnaire",
    text: "Comme le conseiller, et range seul tes opérations si ton mandat l'y autorise.",
  },
};

const PRIVACY_TEXT: Record<PrivacyMode, string> = {
  redacted: "Anonymisé",
  aggregates: "Agrégats seulement",
  full: "Complet",
};

export function presetOf(scopes: string[], presets: Record<string, string[]>): string | null {
  const key = [...scopes].sort().join(",");
  return Object.entries(presets).find(([, s]) => [...s].sort().join(",") === key)?.[0] ?? null;
}

export function parseIps(text: string): string[] {
  return text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
}

export function AgentTokens() {
  const catalog = useApi<AgentCatalog>("/api/agents/catalog");
  const tokens = useApi<AgentToken[]>("/api/agents/tokens");
  const [name, setName] = useState("hermes");
  const [preset, setPreset] = useState("conseiller");
  const [privacy, setPrivacy] = useState<PrivacyMode>("redacted");
  const [ips, setIps] = useState("192.168.1.112");
  const [ttl, setTtl] = useState(90);
  const [password, setPassword] = useState("");
  const [created, setCreated] = useState<AgentTokenCreated | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<AgentTokenCreated>("/api/agents/tokens", {
        method: "POST",
        body: { name: name.trim(), preset, privacy, allowed_ips: parseIps(ips), ttl_days: ttl, password },
      });
      setPassword("");
      setCreated(r);
      tokens.reload();
    });
  };

  const revoke = (t: AgentToken) =>
    void run(async () => {
      if (!window.confirm(`Révoquer le jeton « ${t.name} » ? L'agent perd l'accès immédiatement.`)) return;
      await api(`/api/agents/tokens/${t.id}`, { method: "DELETE" });
      toast("Jeton révoqué.");
      tokens.reload();
    });

  const copy = (text: string, what: string) =>
    void navigator.clipboard.writeText(text).then(
      () => toast(`${what} copié.`),
      () => toast("Copie impossible : sélectionne le texte à la main."),
    );

  return (
    <section className="section" aria-labelledby="agents-title" id="agents">
      <div className="section-head"><h2 id="agents-title"><Icon name="robot" /> Agents IA</h2></div>
      <p className="section-note">
        Connecte HERMÈS (ou tout agent compatible MCP) pour qu'il devienne ton gestionnaire d'argent. Il lit, calcule
        et propose ; il ne peut ni déplacer d'argent, ni changer son propre mandat, ni appliquer un réglage sans toi.
        Chaque appel est inscrit au journal.
      </p>

      {created && (
        <div className="notice warn token-reveal" role="status">
          <p>
            <b>Jeton de « {created.name} »</b> — affiché une seule fois. Bank n'en garde qu'une empreinte.
          </p>
          <ol className="steps">
            <li>
              Sur la machine d'HERMÈS, ajoute la ligne suivante à <code>~/.hermes/.env</code> :
              <pre className="code">{created.hermes_env}</pre>
              <button className="btn quiet" type="button" onClick={() => copy(created.hermes_env, "Jeton")}>Copier</button>
            </li>
            <li>
              Ajoute le serveur à <code>~/.hermes/config.yaml</code> :
              <pre className="code">{created.hermes_config}</pre>
              <button className="btn quiet" type="button" onClick={() => copy(created.hermes_config, "Configuration")}>Copier</button>
            </li>
            <li>
              Dans HERMÈS : <code>/reload-mcp</code>, puis demande-lui « fais mon onboarding financier ». Le kit complet
              (compétence, routines du matin et du dimanche) est dans <code>integrations/hermes</code>.
            </li>
          </ol>
          <button className="btn link" type="button" onClick={() => setCreated(null)}>J'ai copié, masquer</button>
        </div>
      )}

      <form className="stack agent-form" onSubmit={create} aria-label="Créer un jeton d'agent">
        <fieldset className="presets">
          <legend>Rôle</legend>
          {Object.keys(PRESET_TEXT).map((p) => (
            <label key={p} className="preset" data-checked={preset === p}>
              <input type="radio" name="preset" value={p} checked={preset === p} onChange={() => setPreset(p)} />
              <b>{PRESET_TEXT[p]!.title}</b>
              <span className="faint">{PRESET_TEXT[p]!.text}</span>
              {catalog.data && (
                <span className="scopes">{(catalog.data.presets[p] ?? []).map((s) => <code key={s}>{s}</code>)}</span>
              )}
            </label>
          ))}
        </fieldset>
        <div className="form-grid">
          <Field label="Nom de l'agent">
            <input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={40} pattern="[\p{L}0-9 _.\-]+" />
          </Field>
          <Field label="Confidentialité" hint={catalog.data?.privacy[privacy]}>
            <select value={privacy} onChange={(e) => setPrivacy(e.target.value as PrivacyMode)}>
              {(Object.keys(PRIVACY_TEXT) as PrivacyMode[]).map((m) => <option key={m} value={m}>{PRIVACY_TEXT[m]}</option>)}
            </select>
          </Field>
          <Field label="Adresses autorisées" hint="IP ou réseau (192.168.1.0/24). Vide : toutes.">
            <input value={ips} onChange={(e) => setIps(e.target.value)} />
          </Field>
          <Field label="Validité (jours)">
            <input type="number" min={1} max={catalog.data?.max_ttl_days ?? 365} value={ttl}
                   onChange={(e) => setTtl(Number.parseInt(e.target.value, 10) || 90)} />
          </Field>
        </div>
        <div className="form-grid">
          <Field label="Ton mot de passe" hint="Un jeton ouvre l'accès à toute ta situation : on te le redemande.">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                   autoComplete="current-password" />
          </Field>
          <button className="btn" disabled={busy}>Créer le jeton</button>
        </div>
      </form>
      {error && <p className="error" role="alert">{error}</p>}

      {tokens.error && <ErrorNote message={tokens.error} onRetry={tokens.reload} />}
      {tokens.data && tokens.data.length > 0 && (
        <ul className="connected" style={{ marginTop: "1.5rem" }} aria-label="Jetons d'agents">
          {tokens.data.map((t) => {
            const p = catalog.data ? presetOf(t.scopes, catalog.data.presets) : null;
            return (
              <li key={t.id}>
                <span className="bank-mark" aria-hidden="true"><Icon name="robot" /></span>
                <div style={{ flex: "1 1 14rem", minWidth: 0 }}>
                  <b>{t.name}</b>{" "}
                  <span className={`tag ${t.state === "actif" ? "ok" : "warn"}`}>
                    {{ actif: "actif", expire: "expiré", revoque: "révoqué" }[t.state]}
                  </span>
                  <div className="faint">
                    {p ? PRESET_TEXT[p]?.title : t.scopes.join(", ")} · <code>{t.prefix}…</code> · {PRIVACY_TEXT[t.privacy]}
                  </div>
                  <div className="faint">
                    {t.last_used_at ? `dernier appel le ${shortDate(t.last_used_at)}` : "jamais appelé"} · {t.calls} appel{t.calls > 1 ? "s" : ""}
                    {t.state === "actif" && ` · valable jusqu'au ${shortDate(t.expires_at)}`}
                    {t.allowed_ips.length > 0 && ` · depuis ${t.allowed_ips.map((ip) => ip.replace(/\/(32|128)$/, "")).join(", ")}`}
                  </div>
                </div>
                {t.state === "actif" && (
                  <button className="btn ghost sm" disabled={busy} onClick={() => revoke(t)}>Révoquer</button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
