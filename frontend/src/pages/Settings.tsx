import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type Account, type AccountKind, type BudgetResponse, type Me } from "../api";
import { EuroInput, Field, Loading, useAsyncAction, useToast } from "../components/ui";
import { money, shortDate } from "../format";

export const KIND_LABELS: Record<AccountKind, string> = {
  checking: "Compte courant",
  savings: "Épargne",
  livret_a: "Livret A",
  ldds: "LDDS",
  lep: "LEP",
  cel: "CEL",
  pel: "PEL",
  pea: "PEA",
  assurance_vie: "Assurance vie",
  per: "PER",
  crypto: "Crypto",
  credit: "Crédit",
  other: "Autre",
};

export function Settings({ me, onMeChange }: { me: Me; onMeChange: () => void }) {
  return (
    <>
      <div className="page-head">
        <h1>Réglages</h1>
        <p className="muted">Banques, compteur Linky, documents de l'État et agents IA : voir <a href="#/connecteurs">Connecteurs</a>.</p>
      </div>
      <Accounts />
      <BudgetSettings />
      <Security me={me} onChange={onMeChange} />
      <Audit />
    </>
  );
}

// ------------------------------------------------------------------ comptes
function Accounts() {
  const accounts = useApi<Account[]>("/api/accounts");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AccountKind>("checking");
  const [iban, setIban] = useState("");
  const [balance, setBalance] = useState<number | null>(0);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/accounts", { method: "POST", body: { name, kind, iban: iban.trim() || null, balance_cents: balance ?? 0 } });
      setName(""); setIban(""); setBalance(0);
      toast("Compte ajouté.");
      accounts.reload();
    });
  };

  const patch = (id: number, body: Record<string, unknown>) =>
    void run(async () => {
      await api(`/api/accounts/${id}`, { method: "PATCH", body });
      accounts.reload();
    });

  return (
    <section className="section" aria-labelledby="acc-title">
      <h2 id="acc-title">Comptes</h2>
      {!accounts.data && <Loading />}
      {accounts.data && accounts.data.length > 0 && (
        <table className="plain">
          <thead>
            <tr><th>Compte</th><th>Type</th><th className="num">Solde</th><th>Dans le budget</th><th /></tr>
          </thead>
          <tbody>
            {accounts.data.map((a) => (
              <tr key={a.id} style={a.archived ? { opacity: 0.55 } : undefined}>
                <td><b>{a.name}</b><div className="faint">{a.iban_masked ?? a.institution ?? ""}</div></td>
                <td>{KIND_LABELS[a.kind]}</td>
                <td className="num">
                  {a.provider === "enablebanking" ? money(a.balance_cents) : (
                    <EuroInput allowNegative value={a.balance_cents}
                               onChange={(c) => c !== null && c !== a.balance_cents && patch(a.id, { balance_cents: c })} />
                  )}
                  {a.balance_at && <div className="faint">au {shortDate(a.balance_at)}</div>}
                </td>
                <td>
                  <input type="checkbox" checked={a.include_in_budget} aria-label={`Inclure ${a.name} dans le budget`}
                         onChange={(e) => patch(a.id, { include_in_budget: e.target.checked })} />
                </td>
                <td className="num">
                  <button className="btn link" onClick={() => patch(a.id, { archived: !a.archived })}>
                    {a.archived ? "Réactiver" : "Archiver"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="panel" style={{ marginTop: "1.5rem" }} onSubmit={create} aria-label="Nouveau compte">
        <div className="form-grid">
          <Field label="Nom"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Compte joint" /></Field>
          <Field label="Type">
            <select value={kind} onChange={(e) => setKind(e.target.value as AccountKind)}>
              {Object.entries(KIND_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <Field label="IBAN (facultatif)" hint="Chiffré sur le serveur ; sert à relier le compte à ta banque.">
            <input value={iban} onChange={(e) => setIban(e.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Solde actuel"><EuroInput allowNegative value={balance} onChange={setBalance} /></Field>
        </div>
        {error && <p className="error" role="alert" style={{ marginTop: "0.75rem" }}>{error}</p>}
        <div className="actions"><button className="btn" disabled={busy}>Ajouter le compte</button></div>
      </form>
    </section>
  );
}

// ------------------------------------------------------------------ budget
function BudgetSettings() {
  const budget = useApi<BudgetResponse>("/api/budget");
  const [buffer, setBuffer] = useState<number | null>(null);
  const [goal, setGoal] = useState<number | null>(null);
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  useEffect(() => {
    if (budget.data) {
      setBuffer(budget.data.settings.buffer_cents);
      setGoal(budget.data.settings.savings_goal_cents);
    }
  }, [budget.data]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/budget/settings", { method: "PUT", body: { buffer_cents: buffer ?? 0, savings_goal_cents: goal ?? 0 } });
      toast("Réglages du budget enregistrés.");
    });
  };

  return (
    <section className="section" aria-labelledby="bud-title">
      <h2 id="bud-title">Budget</h2>
      <form className="form-grid" onSubmit={save} style={{ maxWidth: "44rem" }}>
        <Field label="Toujours garder de côté" hint="Retiré du « reste à dépenser » chaque jour.">
          <EuroInput value={buffer} onChange={setBuffer} />
        </Field>
        <Field label="Épargne visée chaque mois" hint="Réservée avant le calcul du reste à dépenser.">
          <EuroInput value={goal} onChange={setGoal} />
        </Field>
        <button className="btn" disabled={busy}>Enregistrer</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ sécurité
function Security({ me, onChange }: { me: Me; onChange: () => void }) {
  const [setup, setSetup] = useState<{ qr_svg: string; secret: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [exportPw, setExportPw] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();

  const start = () => void run(async () => setSetup(await api("/api/auth/totp/setup", { method: "POST" })));
  const enable = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<{ recovery_codes: string[] }>("/api/auth/totp/enable", { method: "POST", body: { code } });
      setCodes(r.recovery_codes);
      setSetup(null);
      onChange();
    });
  };
  const changePassword = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/auth/password", { method: "POST", body: { current_password: current, new_password: next } });
      setCurrent(""); setNext("");
      toast("Mot de passe changé. Tes autres appareils sont déconnectés.");
    });
  };
  const exportData = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const blob = await api<Blob>("/api/export", { method: "POST", body: { password: exportPw } });
      const url = URL.createObjectURL(blob instanceof Blob ? blob : new Blob([JSON.stringify(blob)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `bank-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setExportPw("");
    });
  };
  // Le SVG du QR code est affiché via <img> : aucun script embarqué ne peut s'exécuter.
  const qrSrc = setup ? `data:image/svg+xml;base64,${toBase64(setup.qr_svg)}` : "";

  return (
    <section className="section" aria-labelledby="sec-title">
      <h2 id="sec-title">Sécurité</h2>
      <div className="split">
        <div className="stack">
          <h3>Double authentification</h3>
          {me.totp_enabled ? (
            <p>
              Active. {me.recovery_codes_left} codes de secours restants.
            </p>
          ) : setup ? (
            <form className="stack" onSubmit={enable}>
              <p>Scanne ce code avec ton application (Aegis, 2FAS, Google Authenticator…), puis saisis le code affiché.</p>
              <img className="qr" src={qrSrc} alt="QR code de configuration" />
              <p className="faint">Clé manuelle : <span style={{ wordBreak: "break-all" }}>{setup.secret}</span></p>
              <Field label="Code à 6 chiffres">
                <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" required />
              </Field>
              <button className="btn" disabled={busy}>Activer</button>
            </form>
          ) : (
            <>
              <p className="muted">Un code de ton téléphone en plus du mot de passe : indispensable pour des données bancaires.</p>
              <button className="btn" onClick={start} disabled={busy}>Configurer</button>
            </>
          )}
          {codes && (
            <div className="notice warn">
              <p><b>Codes de secours</b> : chacun ne sert qu'une fois. Range-les hors de ce téléphone, ils ne seront plus affichés.</p>
              <div className="codes" style={{ marginTop: "0.75rem" }}>{codes.map((c) => <span key={c}>{c}</span>)}</div>
            </div>
          )}
        </div>
        <div className="stack">
          <form className="stack" onSubmit={changePassword} aria-label="Changer de mot de passe">
            <h3>Mot de passe</h3>
            <Field label="Actuel"><input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required autoComplete="current-password" /></Field>
            <Field label="Nouveau" hint="12 caractères minimum."><input type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} autoComplete="new-password" /></Field>
            <button className="btn quiet" disabled={busy}>Changer le mot de passe</button>
          </form>
          <form className="stack" onSubmit={exportData} aria-label="Exporter mes données">
            <h3>Exporter mes données</h3>
            <p className="muted">Tout ton historique en JSON. Le fichier contient tes IBAN en clair : garde-le en lieu sûr.</p>
            <Field label="Mot de passe"><input type="password" value={exportPw} onChange={(e) => setExportPw(e.target.value)} required autoComplete="current-password" /></Field>
            <button className="btn quiet" disabled={busy}>Télécharger l'export</button>
          </form>
        </div>
      </div>
      {error && <p className="error" role="alert" style={{ marginTop: "1rem" }}>{error}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ journal
const ACTIONS: Record<string, string> = {
  "setup.done": "Création de l'accès",
  "setup.denied": "Installation refusée (jeton invalide)",
  "login.success": "Connexion",
  "login.failed": "Mot de passe refusé",
  "login.mfa_failed": "Code de vérification refusé",
  "login.locked": "Tentative sur compte verrouillé",
  logout: "Déconnexion",
  "mfa.enabled": "Double authentification activée",
  "password.changed": "Mot de passe changé",
  "password.change_denied": "Changement de mot de passe refusé",
  "account.create": "Compte ajouté",
  "account.update": "Compte modifié",
  "import.file": "Relevé importé",
  "transaction.update": "Opération recatégorisée",
  "category.update": "Enveloppe modifiée",
  "rule.create": "Règle de classement créée",
  "budget.settings": "Réglages du budget modifiés",
  "profile.update": "Profil du foyer modifié",
  "aids.simulate": "Simulation des aides",
  "goal.create": "Objectif ajouté",
  "goal.delete": "Objectif supprimé",
  "bank.connect.start": "Connexion bancaire demandée",
  "bank.connect.done": "Banque connectée",
  "bank.sync": "Synchronisation bancaire",
  "bank.revoke": "Accès bancaire coupé",
  "export.done": "Données exportées",
  "export.denied": "Export refusé (mot de passe)",
  "agent.token.create": "Jeton d'agent IA créé",
  "agent.token.revoke": "Jeton d'agent IA révoqué",
  "onboarding.answer": "Réponse « Ma situation » enregistrée",
  "onboarding.clear": "Réponse « Ma situation » effacée",
  "proposal.create": "Proposition de l'agent reçue",
  "proposal.approve": "Proposition de l'agent appliquée",
  "proposal.reject": "Proposition de l'agent refusée",
  "mcp.denied": "Accès refusé à un agent (portée)",
  "energy.connect": "Compteur Linky connecté",
  "energy.disconnect": "Compteur Linky déconnecté",
  "energy.sync": "Synchronisation Linky",
  "energy.sync_manual": "Synchronisation Linky demandée",
  "energy.sync_failed": "Synchronisation Linky en échec",
  "energy.tariff": "Tarif électricité modifié",
  "document.apply": "Document officiel enregistré",
  "document.delete": "Document officiel supprimé",
  "decision.purchase": "Question « je peux l'acheter ? »",
  "mcp.refused": "Action refusée à un agent",
  "mcp.auth_failed": "Jeton d'agent refusé",
  "agent.token.denied": "Création de jeton refusée (mot de passe)",
  "initiative.act": "Réponse à une carte « Bank te demande »",
  "planning.connect": "Agenda de travail relié",
  "planning.disconnect": "Agenda de travail retiré",
  "planning.model": "Règles de paie modifiées",
  "planning.sync_manual": "Relecture de l'agenda demandée",
  "planning.sync_failed": "Relecture de l'agenda en échec",
};

function Audit() {
  const log = useApi<{ chain: { ok: boolean; entries: number; broken_at: number | null; reason: string | null };
    entries: { id: number; ts: string; actor: string; action: string }[] }>("/api/audit?limit=30");
  if (!log.data) return null;
  const { chain, entries } = log.data;
  return (
    <section className="section" aria-labelledby="audit-title">
      <h2 id="audit-title">Journal d'activité</h2>
      <div className={`notice ${chain.ok ? "ok" : "danger"}`}>
        {chain.ok
          ? `Intègre : ${chain.entries} événements, chaînés et signés. Aucune modification détectée.`
          : `Altération détectée à l'entrée ${chain.broken_at} (${chain.reason}). Quelqu'un a modifié la base hors de l'application.`}
      </div>
      <table className="plain" style={{ marginTop: "1rem" }}>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td style={{ whiteSpace: "nowrap" }}>{new Date(e.ts).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</td>
              <td>{ACTIONS[e.action] ?? (e.action.startsWith("mcp.") ? `Agent : ${e.action.slice(4).replaceAll("_", " ")}` : e.action)}</td>
              <td className="muted">{e.actor}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function toBase64(text: string): string {
  let bin = "";
  for (const b of new TextEncoder().encode(text)) bin += String.fromCharCode(b);
  return btoa(bin);
}
