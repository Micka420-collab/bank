import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Field, useAsyncAction } from "../components/ui";

export function Setup({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, error, run] = useAsyncAction();
  const mismatch = confirm.length > 0 && confirm !== password;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    void run(async () => {
      await api("/api/setup", { method: "POST", body: { setup_token: token.trim(), username, password } });
      onDone();
    });
  };

  return (
    <main className="auth">
      <form className="auth-card" onSubmit={submit}>
        <h1>Créer ton accès</h1>
        <p className="muted">
          Le jeton d'installation est dans la configuration du serveur (BANK_SETUP_TOKEN). Il empêche quiconque
          sur ton réseau de prendre la main avant toi.
        </p>
        <Field label="Jeton d'installation">
          <input value={token} onChange={(e) => setToken(e.target.value)} required autoComplete="off" />
        </Field>
        <Field label="Identifiant">
          <input value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3}
                 pattern="[A-Za-z0-9._\-]+" autoComplete="username" />
        </Field>
        <Field label="Mot de passe" hint="12 caractères minimum ; une phrase de passe est idéale.">
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                 minLength={12} autoComplete="new-password" />
        </Field>
        <Field label="Confirmation">
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required
                 autoComplete="new-password" aria-invalid={mismatch} />
        </Field>
        {mismatch && <p className="error">Les deux mots de passe diffèrent.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn" disabled={busy}>Créer mon accès</button>
      </form>
    </main>
  );
}

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, error, run] = useAsyncAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      if (mfaToken) {
        await api("/api/auth/mfa", { method: "POST", body: { mfa_token: mfaToken, code } });
        onDone();
        return;
      }
      const r = await api<{ mfa_required: boolean; mfa_token?: string }>("/api/auth/login", {
        method: "POST",
        body: { username, password },
      });
      if (r.mfa_required && r.mfa_token) setMfaToken(r.mfa_token);
      else onDone();
    });
  };

  return (
    <main className="auth">
      <form className="auth-card" onSubmit={submit}>
        <h1>{mfaToken ? "Code de vérification" : "Connexion"}</h1>
        {mfaToken ? (
          <>
            <p className="muted">Saisis le code à 6 chiffres de ton application d'authentification, ou un code de secours.</p>
            <Field label="Code">
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoFocus
                     autoComplete="one-time-code" inputMode="numeric" />
            </Field>
          </>
        ) : (
          <>
            <Field label="Identifiant">
              <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
            </Field>
            <Field label="Mot de passe">
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                     autoComplete="current-password" />
            </Field>
          </>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn" disabled={busy}>{mfaToken ? "Valider le code" : "Se connecter"}</button>
      </form>
    </main>
  );
}
