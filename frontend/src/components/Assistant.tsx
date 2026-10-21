// « Demande à Bank » : un panneau latéral où tu poses une question en français ; le modèle (OpenRouter, ta clé)
// répond à partir de ton dossier. La conversation vit dans la page, jamais sur le serveur. Chaque réponse dit
// quel modèle a répondu, ce que ça a coûté et ce qu'il a vu (anonymisé par défaut).

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, useApi, type AssistantAnswer, type AssistantStatus } from "../api";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { usd } from "../pages/AssistantSettings";

export type Turn = { role: "user" | "assistant"; content: string; meta?: { model: string; cost_usd: number; privacy: string; truncated: boolean } };

export const SUGGESTIONS = [
  "Combien je peux dépenser d'ici ma prochaine paie ?",
  "Où est parti mon argent ce mois-ci ?",
  "Je peux m'offrir 300 € de vacances le mois prochain ?",
  "Qu'est-ce que je devrais faire en priorité pour mon épargne ?",
];

const PRIVACY_SHORT: Record<string, string> = { full: "dossier complet", redacted: "dossier anonymisé", aggregates: "chiffres seuls" };

export function costLabel(cost: number): string {
  if (cost <= 0) return "gratuit";
  if (cost < 0.01) return `${(cost * 100).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} ¢`;
  return usd(Math.round(cost * 100));
}

export function Assistant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const status = useApi<AssistantStatus>(open ? "/api/assistant" : null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    returnTo.current = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => inputRef.current?.focus(), 50);
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Fenêtre modale : la page derrière ne défile plus (surtout sur mobile).
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      returnTo.current?.focus?.();
    };
  }, [open, onClose]);

  useEffect(() => {
    const el = listRef.current;
    if (el && typeof el.scrollTo === "function") el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setError(null);
    setBusy(true);
    const history = turns.slice(-6).map((t) => ({ role: t.role, content: t.content }));
    setTurns((prev) => [...prev, { role: "user", content: q }]);
    setQuestion("");
    try {
      const r = await api<AssistantAnswer>("/api/assistant/ask", { method: "POST", body: { question: q, history } });
      setTurns((prev) => [...prev, { role: "assistant", content: r.answer, meta: { model: r.model, cost_usd: r.cost_usd, privacy: r.privacy, truncated: r.truncated } }]);
    } catch (e) {
      setError((e as Error).message);
      setQuestion(q);
      setTurns((prev) => prev.slice(0, -1));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(question);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(question);
    }
  };

  if (!open) return null;
  const configured = status.data?.configured === true;
  return (
    <div className="assistant-backdrop" onClick={onClose}>
      <aside className="assistant-panel" role="dialog" aria-modal="true" aria-labelledby="assistant-panel-title" onClick={(e) => e.stopPropagation()}>
        <header className="assistant-head">
          <div>
            <h2 id="assistant-panel-title"><Icon name="spark" /> Demande à Bank</h2>
            {configured && status.data?.privacy && <span className="faint">{status.data.model} · {PRIVACY_SHORT[status.data.privacy] ?? status.data.privacy}</span>}
          </div>
          <button type="button" className="btn ghost sm" onClick={onClose} aria-label="Fermer"><Icon name="close" /></button>
        </header>

        <div className="assistant-body" ref={listRef}>
          {status.data && !configured && (
            <div className="assistant-empty">
              <p><b>L'assistant n'est pas encore branché.</b> Ajoute ta clé OpenRouter dans Connecteurs : Bank lui enverra un dossier anonymisé et il répondra à tes questions ici.</p>
              <a className="btn" href="#/connecteurs" onClick={onClose}><Icon name="connectors" /> Ouvrir Connecteurs</a>
            </div>
          )}
          {configured && turns.length === 0 && (
            <div className="assistant-empty">
              <p className="muted">Je réponds à partir de ce que Bank sait de toi : solde, échéances, aides, épargne, objectifs. Par exemple :</p>
              <div className="assistant-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" className="pill" onClick={() => void send(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`assistant-turn is-${t.role}`}>
              {t.role === "user" ? <p>{t.content}</p> : <Markdown source={t.content} />}
              {t.meta && (
                <div className="assistant-meta faint">
                  {t.meta.model} · {costLabel(t.meta.cost_usd)} · {PRIVACY_SHORT[t.meta.privacy] ?? t.meta.privacy}
                  {t.meta.truncated && " · réponse coupée (plafond de longueur)"}
                </div>
              )}
            </div>
          ))}
          {busy && (
            <div className="assistant-turn is-assistant" aria-live="polite">
              <p className="muted"><span className="spinner" aria-hidden="true" /> Bank lit ton dossier…</p>
            </div>
          )}
          {error && <div className="notice danger" role="alert"><Icon name="alert" /><span>{error}</span></div>}
        </div>

        {configured && (
          <form className="assistant-input" onSubmit={submit}>
            <textarea ref={inputRef} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={onKey} rows={2}
                      placeholder="Pose ta question… (Entrée pour envoyer)" aria-label="Ta question" maxLength={2000} disabled={busy} />
            <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || question.trim().length < 2} aria-label="Envoyer"><Icon name="arrow" /></button>
          </form>
        )}
        <p className="assistant-foot faint"><Icon name="shield" /> Rien n'est gardé : la conversation disparaît quand tu fermes le panneau.</p>
      </aside>
    </div>
  );
}
