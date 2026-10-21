import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { money } from "../format";

// ------------------------------------------------------------------ toast
const ToastCtx = createContext<(msg: string) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 4500);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div aria-live="polite" role="status">
        {msg && <div className="toast">{msg}</div>}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// ------------------------------------------------------------------ petits composants
export function Amount({ cents, whole = false }: { cents: number; whole?: boolean }) {
  return <span className={`amount${cents > 0 ? " in" : ""}`}>{money(cents, { whole, signed: true })}</span>;
}

export function Loading({ what = "Chargement" }: { what?: string }) {
  return <p className="muted">{what}…</p>;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="notice danger" role="alert">
      {message}{" "}
      {onRetry && (
        <button type="button" className="btn link" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      {label}
      {children}
      {hint && <span className="faint">{hint}</span>}
    </label>
  );
}

/** Saisie d'un montant en euros, stocké en centimes. */
export function EuroInput({
  value,
  onChange,
  allowNegative = false,
  ...rest
}: {
  value: number | null;
  onChange: (cents: number | null) => void;
  allowNegative?: boolean;
  id?: string;
  required?: boolean;
  placeholder?: string;
  "aria-label"?: string;
}) {
  const [text, setText] = useState(value === null ? "" : (value / 100).toString().replace(".", ","));
  useEffect(() => {
    setText(value === null ? "" : (value / 100).toString().replace(".", ","));
  }, [value]);
  return (
    <input
      {...rest}
      inputMode="decimal"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const cleaned = text.replace(/[\s €]/g, "").replace(",", ".");
        if (cleaned === "") return onChange(null);
        const n = Number(cleaned);
        if (Number.isFinite(n) && (allowNegative || n >= 0)) onChange(Math.round(n * 100));
        else setText(value === null ? "" : (value / 100).toString().replace(".", ","));
      }}
    />
  );
}

export function useAsyncAction(): [boolean, string | null, (fn: () => Promise<void>) => Promise<void>] {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);
  return [busy, error, run];
}
