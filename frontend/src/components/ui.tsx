import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { money } from "../format";
import { Celebration, type CelebrationKind } from "./Celebration";
import { Icon, type IconName } from "./Icon";

// ------------------------------------------------------------------ toast
/** Neutre par défaut ; « success » (coche qui se trace), « coin » (un gain), « error » (reste plus longtemps). */
export type ToastKind = "info" | "success" | "coin" | "error";
type Toast = { msg: string; kind: ToastKind; n: number; party?: CelebrationKind };
const TOAST_ICON: Record<ToastKind, IconName> = { info: "spark", success: "check", coin: "coin", error: "alert" };
type Show = (msg: string, kind?: ToastKind, party?: CelebrationKind) => void;
const ToastCtx = createContext<Show>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback<Show>((msg, kind = "info", party) => {
    setToast((t) => ({ msg, kind, party, n: (t?.n ?? 0) + 1 }));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), kind === "error" ? 8000 : 4500);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div aria-live="polite" role="status">
        {toast && (
          // La clé relance l'animation (coche, lueur) quand un deuxième message suit le premier.
          <div key={toast.n} className={`toast${toast.kind === "info" ? "" : ` is-${toast.kind}`}`}>
            <Icon name={TOAST_ICON[toast.kind]} />
            {toast.msg}
          </div>
        )}
      </div>
      {/* Hors de la région annoncée : la fête se voit, le texte du toast suffit à la dire. */}
      {toast?.party && <Celebration key={toast.n} kind={toast.party} />}
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/**
 * Un petit moment de fête (§ 3.6 de DESIGN.md) : le toast annonce le texte, quelques pièces ou étincelles
 * jaillissent de lui et un halo s'allume au fond, moins d'une seconde. À n'appeler qu'après une réponse 2xx du
 * serveur à un geste de l'utilisateur — jamais au chargement, jamais pour une estimation.
 */
export function useCelebrate(): (kind: CelebrationKind, msg: string) => void {
  const toast = useToast();
  return useCallback((kind: CelebrationKind, msg: string) => toast(msg, kind === "coin" ? "coin" : "success", kind), [toast]);
}

// ------------------------------------------------------------------ petits composants
export function Amount({ cents, whole = false }: { cents: number; whole?: boolean }) {
  return <span className={`amount${cents > 0 ? " in" : ""}`}>{money(cents, { whole, signed: true })}</span>;
}

export function Loading({ what = "Chargement" }: { what?: string }) {
  // Trois lignes du cahier qui attendent l'encre, plutôt qu'un mot : le texte reste pour les lecteurs d'écran.
  return (
    <div className="skeleton" role="status" aria-live="polite">
      <span className="sr-only">{what}…</span>
      <i aria-hidden="true" /><i aria-hidden="true" /><i aria-hidden="true" />
    </div>
  );
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
