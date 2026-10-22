// Connexion rapide : une carte, l'identifiant retenu sur cet appareil (jamais le mot de passe), le code à six chiffres
// validé dès qu'il est complet. La création du premier accès se fait dans l'onboarding.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Field, useAsyncAction } from "../components/ui";
import { loginWithPasskey, supportsPasskeys } from "../webauthn";

const REMEMBER_KEY = "bank:username";

function rememberedUsername(): string {
  try {
    return window.localStorage.getItem(REMEMBER_KEY) ?? "";
  } catch {
    return "";
  }
}

function remember(username: string): void {
  try {
    window.localStorage.setItem(REMEMBER_KEY, username);
  } catch {
    /* stockage indisponible : on n'insiste pas */
  }
}

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState(rememberedUsername);
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, error, run] = useAsyncAction();
  const formRef = useRef<HTMLFormElement>(null);
  const remembered = username.length > 0 && rememberedUsername() === username;
  const passkeys = supportsPasskeys();
  const withPasskey = () =>
    void run(async () => {
      const r = await loginWithPasskey();
      remember(r.username);
      onDone();
    });

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    void run(async () => {
      if (mfaToken) {
        await api("/api/auth/mfa", { method: "POST", body: { mfa_token: mfaToken, code: code.replace(/\s+/g, "") } });
        onDone();
        return;
      }
      const r = await api<{ mfa_required: boolean; mfa_token?: string }>("/api/auth/login", {
        method: "POST",
        body: { username, password },
      });
      remember(username);
      if (r.mfa_required && r.mfa_token) setMfaToken(r.mfa_token);
      else onDone();
    });
  };

  // Le code TOTP fait six chiffres : on valide dès qu'il est complet, sans bouton à chercher — une seule fois par
  // code saisi, pour qu'un code refusé ne reparte pas en boucle.
  const autoSent = useRef("");
  useEffect(() => {
    const digits = code.replace(/\s+/g, "");
    if (mfaToken && /^\d{6}$/.test(digits) && !busy && autoSent.current !== digits) {
      autoSent.current = digits;
      formRef.current?.requestSubmit();
    }
  }, [code, mfaToken, busy]);

  return (
    <main className="auth">
      <form className="auth-card" onSubmit={submit} ref={formRef} aria-labelledby="login-title">
        <div className="auth-brand"><img src="/icon.svg" alt="" width={36} height={36} /><span>Bank</span></div>
        <h1 id="login-title">{mfaToken ? "Code de vérification" : remembered ? `Bonjour ${username}` : "Connexion"}</h1>
        {mfaToken ? (
          <>
            <p className="muted">Le code à six chiffres de ton application d'authentification, ou un code de secours.</p>
            <Field label="Code">
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoFocus
                     autoComplete="one-time-code" inputMode="numeric" className="code-input" aria-describedby="code-help" />
            </Field>
            <p className="faint" id="code-help">Validé automatiquement dès six chiffres.</p>
          </>
        ) : (
          <>
            {remembered ? (
              <p className="muted">Pas toi ? <button type="button" className="btn link" onClick={() => setUsername("")}>Changer d'identifiant</button></p>
            ) : (
              <Field label="Identifiant">
                <input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" autoFocus />
              </Field>
            )}
            <Field label="Mot de passe">
              <span className="input-with-action">
                <input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} required
                       autoComplete="current-password" autoFocus={remembered} />
                <button type="button" className="btn ghost sm" aria-pressed={showPassword} onClick={() => setShowPassword((v) => !v)}
                        aria-label={showPassword ? "Masquer le mot de passe" : "Afficher le mot de passe"}>
                  {showPassword ? "Masquer" : "Afficher"}
                </button>
              </span>
            </Field>
          </>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button className={`btn lg${busy ? " is-loading" : ""}`} disabled={busy}>{mfaToken ? "Valider le code" : "Se connecter"}</button>
        {!mfaToken && passkeys && (
          <button type="button" className="btn quiet lg" disabled={busy} onClick={withPasskey}>
            <Icon name="lock" /> Se connecter avec une passkey
          </button>
        )}
        {mfaToken && (
          <button type="button" className="btn link" onClick={() => { setMfaToken(null); setCode(""); }}>Revenir à la connexion</button>
        )}
        <p className="faint auth-foot"><Icon name="shield" /> Chez toi, sur ton serveur. Rien ne sort de ta maison.</p>
      </form>
    </main>
  );
}
