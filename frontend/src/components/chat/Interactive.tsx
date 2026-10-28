// Widgets proposés par l'IA, rendus par Bank depuis un schéma strictement validé.
// Aucun HTML externe, aucun envoi automatique : seul le clic de l'utilisateur crée une réponse dans le chat.
import { useId, useState, type FormEvent } from "react";
import type { InteractiveBlock, InteractiveField } from "../../api";
import { Icon } from "../Icon";

type Value = string | string[] | boolean | number;
const TYPE_LABEL = { poll: "Sondage", checklist: "Liste à cocher", form: "Questions" } as const;

function initial(field: InteractiveField): Value {
  if (field.kind === "multiple") return [];
  if (field.kind === "boolean") return false;
  if (field.kind === "scale") return field.min ?? 0;
  return "";
}

function answered(field: InteractiveField, value: Value): boolean {
  if (field.kind === "multiple") return Array.isArray(value) && value.length > 0;
  if (field.kind === "boolean") return value === true;
  if (field.kind === "scale") return typeof value === "number" && Number.isFinite(value);
  return String(value).trim().length > 0;
}

function responseValue(field: InteractiveField, value: Value): string {
  if (field.kind === "multiple") return Array.isArray(value) ? value.join(", ") : "";
  if (field.kind === "boolean") return value ? "Oui" : "Non";
  return String(value).trim();
}

/** Retourne un message explicite et borné, dont les valeurs proviennent UNIQUEMENT de la saisie utilisateur. */
export function serializeWidgetAnswers(block: InteractiveBlock, values: Record<string, Value>): string {
  const lines = block.fields.map((field) => {
    const response = responseValue(field, values[field.id] ?? initial(field));
    return `- ${field.label} : ${response || "Non renseigné"}`;
  });
  return [`Mes réponses à « ${block.title} » :`, ...lines].join("\n").slice(0, 1950);
}

export function ChatInteractive({ block, onAsk }: { block: InteractiveBlock; onAsk: (prompt: string) => void }) {
  const uid = useId();
  const [values, setValues] = useState<Record<string, Value>>(() =>
    Object.fromEntries(block.fields.map((f) => [f.id, initial(f)])),
  );
  const [sent, setSent] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const update = (id: string, v: Value) => { setValues((current) => ({ ...current, [id]: v })); setShowErrors(false); };
  const missing = block.fields.filter((f) => f.required && !answered(f, values[f.id] ?? initial(f)));
  const total = block.fields.filter((f) => f.kind === "multiple").flatMap((f) => f.options ?? []).length;
  const checked = block.fields.filter((f) => f.kind === "multiple")
    .flatMap((f) => Array.isArray(values[f.id]) ? values[f.id] as string[] : []).length;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (sent) return;
    if (missing.length > 0) { setShowErrors(true); return; }
    const content = serializeWidgetAnswers(block, values);
    setSent(true);
    onAsk(content);
  };

  return (
    <section className="chat-interactive" aria-label={block.title} data-kind={block.kind}>
      <header className="chat-interactive-head">
        <span className="chat-interactive-icon"><Icon name={block.kind === "checklist" ? "check" : block.kind === "poll" ? "target" : "edit"} /></span>
        <div>
          <span className="chat-interactive-type">{TYPE_LABEL[block.kind]}</span>
          <h3>{block.title}</h3>
          {block.description && <p className="muted">{block.description}</p>}
        </div>
      </header>
      <form onSubmit={submit} noValidate>
        {block.fields.map((field) => {
          const id = `${uid}-${field.id}`;
          const value = values[field.id] ?? initial(field);
          const options = field.options ?? [];
          return (
            <fieldset key={field.id} className="chat-interactive-field" disabled={sent}>
              <legend>{field.label}{field.required && <span className="chat-required"> · obligatoire</span>}</legend>
              {field.kind === "single" && options.map((option, index) => (
                <label className="chat-answer-choice" key={index} htmlFor={`${id}-${index}`}>
                  <input id={`${id}-${index}`} type="radio" name={id} value={option}
                         checked={value === option} onChange={() => update(field.id, option)} />
                  <span>{option}</span>
                </label>
              ))}
              {field.kind === "multiple" && options.map((option, index) => {
                const selected = Array.isArray(value) && value.includes(option);
                return <label className="chat-answer-choice" key={index} htmlFor={`${id}-${index}`}>
                  <input id={`${id}-${index}`} type="checkbox" checked={selected}
                         onChange={(e) => update(field.id, e.target.checked
                           ? [...(Array.isArray(value) ? value : []), option]
                           : (Array.isArray(value) ? value : []).filter((v) => v !== option))} />
                  <span>{option}</span>
                </label>;
              })}
              {field.kind === "boolean" && (
                <label className="chat-answer-choice">
                  <input type="checkbox" checked={value === true} onChange={(e) => update(field.id, e.target.checked)} />
                  <span>Oui, c'est fait</span>
                </label>
              )}
              {field.kind === "text" && (
                <textarea rows={2} maxLength={180} value={String(value)}
                          aria-label={field.label} placeholder="Ta réponse…"
                          onChange={(e) => update(field.id, e.target.value)} />
              )}
              {field.kind === "number" && (
                <input type="number" inputMode="decimal" value={String(value)} aria-label={field.label}
                       onChange={(e) => update(field.id, e.target.value)} />
              )}
              {field.kind === "scale" && (
                <div className="chat-answer-scale">
                  <input type="range" min={field.min ?? 0} max={field.max ?? 10} step={1}
                         value={Number(value)} aria-label={field.label}
                         onChange={(e) => update(field.id, Number(e.target.value))} />
                  <output aria-live="off">{value}</output>
                </div>
              )}
            </fieldset>
          );
        })}
        {block.kind === "checklist" && (
          <p className="chat-check-progress" aria-live="polite">{checked} sur {total} cases cochées</p>
        )}
        {showErrors && missing.length > 0 && (
          <p className="error" role="alert">
            Réponds à {missing.map((f) => f.label).join(", ")} avant de continuer.
          </p>
        )}
        {sent ? (
          <div className="chat-interactive-sent" role="status">
            <Icon name="check" /><span>Réponses envoyées dans la conversation.</span>
            <button type="button" className="btn link sm" onClick={() => setSent(false)}>Modifier et renvoyer</button>
          </div>
        ) : (
          <div className="chat-interactive-bottom">
            <p className="faint">Rien ne part tant que tu ne valides pas. Ce questionnaire n'agit pas sur tes comptes.</p>
            <button className="btn sm" type="submit">{block.submit_label} <Icon name="arrow" /></button>
          </div>
        )}
      </form>
    </section>
  );
}
