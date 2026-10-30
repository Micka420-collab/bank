// « Demande à Bank » : un panneau latéral où tu parles à ton assistant. Il répond à partir de ton dossier et, si tu
// l'as permis, se souvient de toi d'une conversation à l'autre : chaque chose qu'il retient s'affiche sous sa
// réponse et s'annule d'un clic ; tout le reste se voit et se corrige dans « Ce qu'il retient ». Sans ton accord,
// rien n'est appris et la conversation s'efface quand elle se termine.

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  api,
  askStream,
  useApi,
  type AssistantOpening,
  type AssistantStatus,
  type ChatBlock,
  type ConversationDetail,
  type ConversationSummary,
  type MemoryNotice,
} from "../api";
import { navigate } from "../router";
import { ChatBlocks, type BlockHandlers } from "./chat/Blocks";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { usd } from "../pages/AssistantSettings";

type Notice = MemoryNotice & { undone?: boolean; failed?: string };
export type Turn =
  | { role: "user"; content: string }
  | { role: "divider"; content: string }
  | {
      role: "assistant";
      content: string;
      messageId?: number;
      meta?: { model?: string; cost_usd?: number; privacy?: string; truncated?: boolean };
      notices?: Notice[];
      feedback?: "up" | "down" | null;
      blocks?: ChatBlock[];
      streaming?: boolean; // réponse en cours : le texte arrive morceau par morceau
      status?: string; // ce que l'assistant fait en ce moment (lit ton dossier, cherche sur le web…)
    };
type AssistantTurn = Extract<Turn, { role: "assistant" }>;

export const SUGGESTIONS = [
  "Combien je peux dépenser d'ici ma prochaine paie ?",
  "Où est parti mon argent ce mois-ci ?",
  "Je peux m'offrir 300 € de vacances le mois prochain ?",
  "Qu'est-ce que je devrais faire en priorité pour mon épargne ?",
];
export const RESUME_HOURS = 6; // au-delà, la conversation est fermée côté serveur (et relue si la mémoire est active)

const PRIVACY_SHORT: Record<string, string> = { full: "dossier complet", redacted: "dossier anonymisé", aggregates: "chiffres seuls" };
const NOTICE_LABEL: Record<MemoryNotice["action"], string> = { add: "Retenu", patch: "Corrigé", remove: "Oublié" };

export function costLabel(cost: number): string {
  if (cost <= 0) return "gratuit";
  if (cost < 0.01) return `${(cost * 100).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} ¢`;
  return usd(Math.round(cost * 100));
}

function fromServer(detail: ConversationDetail): Turn[] {
  return detail.messages.map((m) =>
    m.role === "user"
      ? { role: "user", content: m.content }
      : {
          role: "assistant",
          content: m.content,
          messageId: m.id,
          feedback: m.feedback,
          meta: { model: m.meta.model, cost_usd: m.meta.cost_usd, privacy: m.meta.privacy, truncated: m.meta.truncated },
          blocks: m.blocks ?? [],
        },
  );
}

export function Assistant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const status = useApi<AssistantStatus>(open ? "/api/assistant" : null);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [resumed, setResumed] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noteFor, setNoteFor] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [opening, setOpening] = useState<AssistantOpening | null | undefined>(undefined);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const triedResume = useRef(false);

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

  const configured = status.data?.configured === true;

  // L'ouverture : ce que Bank propose de relier (son initiative) et des questions prêtes — calculé sans modèle.
  useEffect(() => {
    if (!open || !configured || opening !== undefined) return undefined;
    let cancelled = false;
    void api<AssistantOpening>("/api/assistant/opening")
      .then((o) => { if (!cancelled) setOpening(o && typeof o.text === "string" ? { ...o, blocks: o.blocks ?? [] } : null); })
      .catch(() => { if (!cancelled) setOpening(null); });
    return () => {
      cancelled = true;
    };
  }, [open, configured, opening]);

  // Reprise : la dernière conversation, si elle est encore ouverte et récente (après un rechargement de la page).
  useEffect(() => {
    if (!open || !configured || triedResume.current) return undefined;
    triedResume.current = true;
    if (conversationId !== null || turns.length > 0) return undefined;
    let cancelled = false;
    void (async () => {
      try {
        const [last] = await api<ConversationSummary[]>("/api/assistant/conversations?limit=1");
        if (!last || last.closed || Date.now() - Date.parse(last.last_at) > RESUME_HOURS * 3_600_000) return;
        const detail = await api<ConversationDetail>(`/api/assistant/conversations/${last.id}`);
        if (cancelled || detail.closed || detail.messages.length === 0) return;
        setConversationId(detail.id);
        setTurns(fromServer(detail));
        setResumed(true);
      } catch {
        /* pas de reprise : on repart d'une page blanche */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, configured, conversationId, turns.length]);

  useEffect(() => {
    const el = listRef.current;
    if (el && typeof el.scrollTo === "function") el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  // Clavier virtuel (iOS surtout) : la feuille se raccourcit de la hauteur du clavier, la saisie reste visible.
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!open || !vv) return undefined;
    const apply = () => {
      const offset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      panelRef.current?.style.setProperty("--kb-offset", `${offset}px`);
    };
    apply();
    vv.addEventListener("resize", apply);
    vv.addEventListener("scroll", apply);
    return () => {
      vv.removeEventListener("resize", apply);
      vv.removeEventListener("scroll", apply);
    };
  }, [open]);

  const updateAssistantTurn = useCallback((index: number, change: (t: AssistantTurn) => AssistantTurn) => {
    setTurns((prev) => prev.map((t, i) => (i === index && t.role === "assistant" ? change(t) : t)));
  }, []);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setError(null);
    setBusy(true);
    // La question, puis une réponse « en cours » qui se remplit au fil de l'eau.
    setTurns((prev) => [...prev, { role: "user", content: q }, { role: "assistant", content: "", streaming: true, status: "lit ton dossier" }]);
    setQuestion("");
    const patchLast = (change: (t: AssistantTurn) => AssistantTurn) =>
      setTurns((prev) => {
        const i = prev.length - 1;
        const last = prev[i];
        return last && last.role === "assistant" ? [...prev.slice(0, i), change(last)] : prev;
      });
    try {
      const r = await askStream(
        { question: q, conversation_id: conversationId },
        {
          onStatus: (label) => patchLast((t) => ({ ...t, status: label })),
          onDelta: (piece) => patchLast((t) => ({ ...t, content: t.content + piece, status: undefined })),
          onReset: () => patchLast((t) => ({ ...t, content: "", status: "réfléchit" })),
        },
      );
      setTurns((prev) => {
        const next = prev.slice(0, -1); // la réponse en cours laisse place à la réponse complète
        // La conversation précédente a été fermée entre-temps (longue pause) : on le montre.
        if (conversationId !== null && r.conversation_id !== conversationId) {
          next.splice(next.length - 1, 0, { role: "divider", content: "Nouvelle conversation" });
        }
        next.push({
          role: "assistant",
          content: r.answer,
          messageId: r.message_id,
          meta: { model: r.model, cost_usd: r.cost_usd, privacy: r.privacy, truncated: r.truncated },
          notices: r.memory,
          feedback: null,
          blocks: r.blocks ?? [],
        });
        return next;
      });
      setConversationId(r.conversation_id);
      if (r.memory.length > 0) status.reload();
    } catch (e) {
      setError((e as Error).message);
      setQuestion(q);
      setTurns((prev) => prev.slice(0, -2)); // ni la question ni la réponse amorcée : la question revient dans le champ
    } finally {
      setBusy(false);
    }
  };

  const newConversation = () => {
    if (conversationId !== null) {
      // Fermer, c'est aussi donner le signal de la relecture (ce qui mérite d'être retenu).
      void api(`/api/assistant/conversations/${conversationId}/close`, { method: "POST" }).catch(() => undefined);
    }
    setConversationId(null);
    setTurns([]);
    setResumed(false);
    setError(null);
    setNoteFor(null);
    setOpening(undefined); // l'ouverture se recalcule : ce qui manque a pu changer
    inputRef.current?.focus();
  };

  const undo = async (turnIndex: number, k: number) => {
    const turn = turns[turnIndex];
    const n = turn?.role === "assistant" ? turn.notices?.[k] : undefined;
    if (!n || n.undone) return;
    const mark = (patch: Partial<Notice>) =>
      updateAssistantTurn(turnIndex, (t) => ({ ...t, notices: t.notices?.map((x, j) => (j === k ? { ...x, ...patch } : x)) }));
    try {
      if (n.action === "add") await api(`/api/memory/entries/${n.id}`, { method: "DELETE" });
      else if (n.action === "patch") await api(`/api/memory/entries/${n.id}`, { method: "PATCH", body: { content: n.before } });
      else await api("/api/memory/entries", { method: "POST", body: { store: n.store, content: n.content } });
      mark({ undone: true, failed: undefined });
    } catch (e) {
      mark({ failed: (e as Error).message });
    }
  };

  const sendFeedback = async (turnIndex: number, value: "up" | "down" | null, text?: string) => {
    const turn = turns[turnIndex];
    if (turn?.role !== "assistant" || turn.messageId === undefined) return;
    const previous = turn.feedback ?? null;
    updateAssistantTurn(turnIndex, (t) => ({ ...t, feedback: value }));
    try {
      await api(`/api/assistant/messages/${turn.messageId}/feedback`, { method: "PUT", body: { value, note: text || null } });
    } catch (e) {
      updateAssistantTurn(turnIndex, (t) => ({ ...t, feedback: previous }));
      setError((e as Error).message);
    }
  };

  const decideMemory = async (granted: boolean) => {
    try {
      await api("/api/consents/ai_memory", { method: "PUT", body: { granted } });
      status.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const decideSearch = async (granted: boolean) => {
    try {
      await api("/api/consents/web_search", { method: "PUT", body: { granted } });
      status.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const handlers: BlockHandlers = {
    onAsk: (prompt) => void send(prompt),
    onNavigate: (page) => {
      onClose();
      navigate(page);
    },
    onActed: () => setOpening(undefined),
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
  const name = status.data?.name ?? "Bank";
  const memory = status.data?.memory;
  const memoryOn = Boolean(memory?.consent && memory.available);
  const askConsent = configured && memory?.available === true && !memory.consent && !memory.asked;
  const search = status.data?.search;
  const askSearch = configured && !askConsent && search?.configured === true && !search.consent && !search.asked;
  return (
    <div className="assistant-backdrop" onClick={onClose}>
      <aside ref={panelRef} className="assistant-panel" role="dialog" aria-modal="true" aria-labelledby="assistant-panel-title" onClick={(e) => e.stopPropagation()}>
        <header className="assistant-head">
          <div>
            <h2 id="assistant-panel-title"><Icon name="spark" /> Demande à {name}</h2>
            {configured && status.data?.privacy && (
              <span className="faint">
                {status.data.model} · {PRIVACY_SHORT[status.data.privacy] ?? status.data.privacy}
                {memoryOn ? " · se souvient de toi" : ""}
              </span>
            )}
          </div>
          <div className="assistant-head-actions">
            {configured && turns.length > 0 && (
              <button type="button" className="btn ghost sm" onClick={newConversation} aria-label="Nouvelle conversation" title="Nouvelle conversation">
                <Icon name="newChat" />
              </button>
            )}
            {configured && (
              <a className="btn ghost sm" href="#/memoire" onClick={onClose} aria-label={`Ce que ${name} retient`} title={`Ce que ${name} retient`}>
                <Icon name="memory" />
              </a>
            )}
            <button type="button" className="btn ghost sm" onClick={onClose} aria-label="Fermer"><Icon name="close" /></button>
          </div>
        </header>

        <div className="assistant-body" ref={listRef}>
          {status.data && !configured && (
            <div className="assistant-empty">
              <p><b>L'assistant n'est pas encore branché.</b> Ajoute ta clé OpenRouter dans Connecteurs : Bank lui enverra un dossier anonymisé et il répondra à tes questions ici.</p>
              <a className="btn" href="#/connecteurs" onClick={onClose}><Icon name="connectors" /> Ouvrir Connecteurs</a>
            </div>
          )}
          {askConsent && (
            <section className="memory-consent" aria-labelledby="memory-consent-title">
              <h3 id="memory-consent-title"><span className="memory-mark" aria-hidden="true">§</span> Je peux me souvenir de toi</h3>
              <p>
                Tes projets, tes préférences, ce qui marche pour toi : je les retiens d'une conversation à l'autre, et je
                m'améliore avec tes retours. Tout reste chiffré chez toi, visible et effaçable. Ce que je retiens est envoyé
                à ton modèle avec le dossier ; jamais d'IBAN, de numéro de carte ni de code.
              </p>
              <div className="actions">
                <button type="button" className="btn sm" onClick={() => void decideMemory(true)}>Oui, souviens-toi</button>
                <button type="button" className="btn quiet sm" onClick={() => void decideMemory(false)}>Pas maintenant</button>
              </div>
            </section>
          )}
          {askSearch && (
            <section className="memory-consent" aria-labelledby="search-consent-title">
              <h3 id="search-consent-title"><Icon name="search" /> Je peux chercher sur le web</h3>
              <p>
                Pour une règle, un barème ou une actualité, je cherche avec le moteur que tu as branché et je cite mes
                sources. La requête part nettoyée : jamais d'IBAN, de numéro, d'e-mail, de téléphone ni de nom.
              </p>
              <div className="actions">
                <button type="button" className="btn sm" onClick={() => void decideSearch(true)}>Oui, cherche quand il faut</button>
                <button type="button" className="btn quiet sm" onClick={() => void decideSearch(false)}>Pas maintenant</button>
              </div>
            </section>
          )}
          {configured && turns.length === 0 && opening && (
            <div className="assistant-turn is-assistant assistant-opening">
              <p>{opening.text}</p>
              <ChatBlocks blocks={opening.blocks} handlers={handlers} disabled={busy} />
            </div>
          )}
          {configured && turns.length === 0 && opening === null && (
            <div className="assistant-empty">
              <p className="muted">Je réponds à partir de ce que Bank sait de toi : solde, échéances, aides, épargne, objectifs. Par exemple :</p>
              <div className="assistant-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" className="pill" onClick={() => void send(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {resumed && turns.length > 0 && (
            <p className="assistant-resumed faint">
              Suite de votre dernière conversation · <button type="button" className="btn link" onClick={newConversation}>en commencer une autre</button>
            </p>
          )}
          {turns.map((t, i) =>
            t.role === "divider" ? (
              <p key={i} className="assistant-divider faint" role="separator">{t.content}</p>
            ) : (
              <div key={i} className={`assistant-turn is-${t.role}`}>
                {t.role === "user" ? <p>{t.content}</p> : t.content ? <Markdown source={t.content} /> : null}
                {t.role === "assistant" && t.streaming && (
                  <p className="muted assistant-status" aria-live="polite">
                    <span className="spinner" aria-hidden="true" /> {name} {t.status ?? "écrit"}…
                  </p>
                )}
                {t.role === "assistant" && t.blocks && t.blocks.length > 0 && <ChatBlocks blocks={t.blocks} handlers={handlers} disabled={busy} />}
                {t.role === "assistant" && t.notices && t.notices.length > 0 && (
                  <ul className="memory-notes" aria-label={`Ce que ${name} vient de retenir`}>
                    {t.notices.map((n, k) => (
                      <li key={`${n.id}-${k}`} className={n.undone ? "is-undone" : undefined}>
                        <span className="memory-mark" aria-hidden="true">§</span>
                        <span className="memory-note-text">
                          <b>{n.undone ? "Annulé" : NOTICE_LABEL[n.action]}</b> « {n.content} »
                          {n.failed && <span className="error"> — {n.failed}</span>}
                        </span>
                        {!n.undone && (
                          <button type="button" className="btn link" onClick={() => void undo(i, k)}>
                            <Icon name="undo" /> Annuler
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {t.role === "assistant" && t.meta && (
                  <div className="assistant-meta faint">
                    <span>
                      {t.meta.model} · {costLabel(t.meta.cost_usd ?? 0)} · {PRIVACY_SHORT[t.meta.privacy ?? ""] ?? t.meta.privacy}
                      {t.meta.truncated && " · réponse coupée (plafond de longueur)"}
                    </span>
                    {t.messageId !== undefined && (
                      <span className="assistant-feedback">
                        <button type="button" className={`btn ghost xs${t.feedback === "up" ? " is-on" : ""}`} aria-pressed={t.feedback === "up"}
                                aria-label="Réponse utile" title="Réponse utile"
                                onClick={() => void sendFeedback(i, t.feedback === "up" ? null : "up")}>
                          <Icon name="thumbUp" />
                        </button>
                        <button type="button" className={`btn ghost xs${t.feedback === "down" ? " is-on" : ""}`} aria-pressed={t.feedback === "down"}
                                aria-label="Réponse à revoir" title="Réponse à revoir"
                                onClick={() => {
                                  if (t.feedback === "down") return void sendFeedback(i, null);
                                  setNote("");
                                  setNoteFor(i);
                                  void sendFeedback(i, "down");
                                }}>
                          <Icon name="thumbDown" />
                        </button>
                      </span>
                    )}
                  </div>
                )}
                {noteFor === i && t.role === "assistant" && (
                  <form className="assistant-note" onSubmit={(e) => {
                    e.preventDefault();
                    void sendFeedback(i, "down", note.trim());
                    setNoteFor(null);
                  }}>
                    <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} autoFocus
                           placeholder="Qu'est-ce qui n'allait pas ? (facultatif)" aria-label="Ce qui n'allait pas" />
                    <button className="btn sm">Envoyer</button>
                  </form>
                )}
              </div>
            ),
          )}
          {error && <div className="notice danger" role="alert"><Icon name="alert" /><span>{error}</span></div>}
        </div>

        {configured && (
          <form className="assistant-input" onSubmit={submit}>
            <textarea ref={inputRef} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={onKey} rows={2}
                      placeholder="Pose ta question…" aria-label="Ta question" maxLength={2000} disabled={busy}
                      enterKeyHint="send" autoCapitalize="sentences" />
            <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy || question.trim().length < 2} aria-label="Envoyer"><Icon name="arrow" /></button>
          </form>
        )}
        <p className="assistant-foot faint">
          <Icon name="shield" />
          {memoryOn ? (
            <span>{name} se souvient de toi : tout est chiffré chez toi, <a href="#/memoire" onClick={onClose}>visible et effaçable</a>.</span>
          ) : memory && !memory.available ? (
            <span>Mode « chiffres seuls » : pas de mémoire, rien de ce que tu écris n'est retenu.</span>
          ) : (
            <span>Sans mémoire : rien n'est appris, la conversation s'efface quand elle se termine.</span>
          )}
        </p>
      </aside>
    </div>
  );
}
