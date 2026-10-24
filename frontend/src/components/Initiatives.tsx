// « Bank te demande » : une fiche à la fois. Elle remarque, elle demande en disant pourquoi, tu réponds d'un geste.
// Les cartes viennent du serveur (clé stable, actions, saisie en ligne) ; ici on ne fait que les montrer et relayer.

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, useApi, type ActResult, type Card, type CardAction, type Initiatives as InitiativesData } from "../api";
import { parseEuros } from "../format";
import { navigate } from "../router";
import { useToast } from "./ui";

const KIND_LABEL: Record<Card["kind"], string> = {
  question: "Bank te demande",
  notice: "Bank remarque",
  celebrate: "Bonne nouvelle",
  tip: "Bank te propose",
};

export function Initiatives({ onChanged, limit = 5, compact = false, title }: { onChanged?: () => void; limit?: number; compact?: boolean; title?: string }) {
  const initial = useApi<InitiativesData>(`/api/initiatives?limit=${limit}`);
  const [data, setData] = useState<InitiativesData | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [leaving, setLeaving] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (initial.data) setData(initial.data);
  }, [initial.data]);

  if (initial.error || !data || data.cards.length === 0) return null;
  const first = data.cards[0]!;
  const rest = data.cards.slice(1);
  const visible = expanded ? data.cards : [first];

  const acted = (key: string, r: ActResult, effectType: string) => {
    toast(r.result);
    setLeaving(key);
    window.setTimeout(() => {
      setData({ cards: r.cards, total: r.total, by_topic: r.by_topic });
      setLeaving(null);
    }, 220);
    if (["answer", "setting", "rule", "ack"].includes(effectType)) onChanged?.();
  };

  return (
    <section className={`asks${compact ? " compact" : ""}`} aria-labelledby="asks-title">
      {/* Sur Aujourd'hui, le titre se voit : une seule chose à faire, dite comme telle. */}
      <h2 id="asks-title" className={title ? "asks-title" : "sr-only"}>{title ?? "Ce que Bank te demande"}</h2>
      <div className="asks-stack">
        {visible.map((card) => (
          <AskCard key={card.key} card={card} leaving={leaving === card.key} limit={limit}
                   onActed={(r, t) => acted(card.key, r, t)} />
        ))}
      </div>
      {rest.length > 0 && (
        <button type="button" className="btn quiet sm asks-more" aria-expanded={expanded} onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Une seule à la fois" : rest.length === 1 && data.total === 2 ? "Voir l'autre" : `Voir les ${data.total - 1} autres`}
        </button>
      )}
    </section>
  );
}

function AskCard({ card, leaving, limit, onActed }: { card: Card; leaving: boolean; limit: number; onActed: (r: ActResult, effectType: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showWhy, setShowWhy] = useState(false);
  const [inputOpen, setInputOpen] = useState(() => card.actions.length > 0 && card.actions[0]!.effect.from_input === true);
  const [value, setValue] = useState<unknown>(
    card.input?.kind === "multi" ? [] : card.input?.kind === "category" && card.input.default ? card.input.default : null,
  );

  const act = async (action: CardAction, v: unknown = null) => {
    if (action.effect.type === "navigate") {
      navigate(String(action.effect.page));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api<ActResult>(`/api/initiatives/${encodeURIComponent(card.key)}/act?limit=${limit}`, {
        method: "POST",
        body: { action: action.id, value: v },
      });
      onActed(r, String(action.effect.type));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const inputAction = card.actions.find((a) => a.effect.from_input === true);
  const primaryIsInput = card.actions[0]?.effect.from_input === true;

  const submitInput = (e: FormEvent) => {
    e.preventDefault();
    if (!inputAction) return;
    const v = normalize(card, value);
    if (v === undefined) {
      setError(card.input?.kind === "money" ? "Indique un montant, par exemple 650 ou 1 200,50." : "Choisis une réponse.");
      return;
    }
    void act(inputAction, v);
  };

  return (
    <article className={`ask ask-${card.kind}${leaving ? " is-leaving" : ""}`} aria-live="polite" data-topic={card.topic}>
      <p className="ask-kind">{KIND_LABEL[card.kind]}</p>
      <h3>{card.title}</h3>
      <p className="ask-body">{card.body}</p>
      {card.evidence && <p className="faint ask-evidence">{card.evidence}</p>}
      {card.why && (
        <p className="ask-why">
          <button type="button" className="btn link" aria-expanded={showWhy} onClick={() => setShowWhy((w) => !w)}>
            {showWhy ? "Pourquoi ça compte" : "Pourquoi ?"}
          </button>
          {showWhy && <span className="faint"> {card.why}</span>}
        </p>
      )}
      {inputOpen && card.input && inputAction ? (
        <form className="ask-input" onSubmit={submitInput}>
          <InputEditor card={card} value={value} onChange={setValue} />
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="btn" disabled={busy}>{inputAction.label}</button>
            {!primaryIsInput && <button type="button" className="btn link" onClick={() => setInputOpen(false)}>Annuler</button>}
            {primaryIsInput && card.actions.slice(1).map((a) => <ActionButton key={a.id} action={a} busy={busy} onClick={() => void act(a)} />)}
          </div>
        </form>
      ) : (
        <div className="actions ask-actions">
          {card.actions.map((a) =>
            a.effect.from_input === true ? (
              <button key={a.id} type="button" className={`btn ${a.style === "primary" ? "" : a.style === "secondary" ? "quiet" : "link"}`}
                      disabled={busy} onClick={() => setInputOpen(true)}>
                {a.label}
              </button>
            ) : (
              <ActionButton key={a.id} action={a} busy={busy} onClick={() => void act(a)} />
            ),
          )}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

function ActionButton({ action, busy, onClick }: { action: CardAction; busy: boolean; onClick: () => void }) {
  const cls = action.style === "primary" ? "btn" : action.style === "secondary" ? "btn quiet" : "btn link";
  return (
    <button type="button" className={cls} disabled={busy} onClick={onClick}>
      {action.label}
    </button>
  );
}

/** Valeur prête pour l'API, ou undefined si la saisie n'est pas exploitable. */
export function normalize(card: Card, value: unknown): unknown {
  const input = card.input;
  if (!input) return value;
  if (input.kind === "money") {
    const cents = typeof value === "string" ? parseEuros(value) : typeof value === "number" ? value : null;
    return cents === null || cents < 0 ? undefined : cents;
  }
  if (input.kind === "int") {
    const n = typeof value === "string" ? Number.parseInt(value, 10) : typeof value === "number" ? value : Number.NaN;
    if (!Number.isFinite(n)) return undefined;
    if (input.minimum !== null && n < input.minimum) return undefined;
    if (input.maximum !== null && n > input.maximum) return undefined;
    return n;
  }
  if (input.kind === "multi") return Array.isArray(value) && value.length > 0 ? value : undefined;
  return typeof value === "string" && value !== "" ? value : undefined;
}

function InputEditor({ card, value, onChange }: { card: Card; value: unknown; onChange: (v: unknown) => void }): ReactNode {
  const input = card.input;
  if (!input) return null;
  if (input.kind === "money") {
    return (
      <span className="euro-field">
        <input inputMode="decimal" autoFocus aria-label={card.title} placeholder={input.placeholder ?? "0"}
               value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value)} />
        <span aria-hidden="true">€</span>
      </span>
    );
  }
  if (input.kind === "int") {
    const small = input.minimum !== null && input.maximum !== null && input.maximum - input.minimum <= 10;
    if (small) {
      const range = Array.from({ length: input.maximum! - input.minimum! + 1 }, (_, i) => input.minimum! + i);
      return (
        <div className="pills" role="radiogroup" aria-label={card.title}>
          {range.map((n) => (
            <button key={n} type="button" role="radio" aria-checked={value === n} className="pill num" onClick={() => onChange(n)}>{n}</button>
          ))}
        </div>
      );
    }
    return <input type="number" inputMode="numeric" autoFocus aria-label={card.title} min={input.minimum ?? undefined} max={input.maximum ?? undefined}
                  value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value)} />;
  }
  if (input.kind === "choice") {
    return (
      <div className="pills" role="radiogroup" aria-label={card.title}>
        {input.options.map((o) => (
          <button key={o.value} type="button" role="radio" aria-checked={value === o.value} className="pill" onClick={() => onChange(o.value)}>{o.label}</button>
        ))}
      </div>
    );
  }
  if (input.kind === "multi") {
    const chosen = Array.isArray(value) ? (value as string[]) : [];
    const toggle = (v: string) => onChange(chosen.includes(v) ? chosen.filter((x) => x !== v) : chosen.length < input.max_items ? [...chosen, v] : chosen);
    return (
      <div className="pills" role="group" aria-label={card.title}>
        {input.options.map((o) => (
          <button key={o.value} type="button" className="pill" aria-pressed={chosen.includes(o.value)} onClick={() => toggle(o.value)}>{o.label}</button>
        ))}
      </div>
    );
  }
  return (
    <select aria-label={card.title} value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choisir une enveloppe…</option>
      {input.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
