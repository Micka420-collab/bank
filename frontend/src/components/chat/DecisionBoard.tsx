// Un seul état local pilote le sondage, le graphique et la liste des actions.
// Les trois profils sont des hypothèses explicites ; aucun geste bancaire automatique.
import { useId, useMemo, useState } from "react";
import type { DecisionBoardBlock, ScenarioBlock } from "../../api";
import { money, shortDate } from "../../format";
import { Icon } from "../Icon";
import { previewScenario, ScenarioChart } from "./Scenario";

export type DecisionProfile = "observe" | "progressive" | "focused";

const PROFILES: {
  id: DecisionProfile;
  label: string;
  rate: number;
  intro: string;
  steps: readonly string[];
}[] = [
  {
    id: "observe", label: "Observer", rate: 0,
    intro: "Garder le rythme habituel et identifier les échéances qui fragilisent le solde.",
    steps: ["Relire les échéances prévues", "Repérer les dépenses variables de la semaine", "Vérifier la marge pour les imprévus"],
  },
  {
    id: "progressive", label: "Progressif · 25 %", rate: 0.25,
    intro: "Explorer une baisse de 25 % des dépenses variables quotidiennes estimées.",
    steps: ["Choisir un poste de dépense facultative", "Fixer un repère quotidien réaliste", "Revoir le budget après une semaine"],
  },
  {
    id: "focused", label: "Intensif · 50 %", rate: 0.5,
    intro: "Explorer une baisse importante de 50 % des dépenses variables. À vérifier avant d'en faire un objectif.",
    steps: ["Séparer les achats essentiels des dépenses reportables", "Vérifier si cette baisse est tenable", "Préserver une marge de sécurité"],
  },
];

export function profileSaving(cap: number, profile: DecisionProfile): number {
  const rate = PROFILES.find((p) => p.id === profile)?.rate ?? 0;
  return Number.isFinite(cap) ? Math.round(Math.max(0, cap) * rate) : 0;
}

export function ChatDecisionBoard({ block, onAsk, disabled = false }: {
  block: DecisionBoardBlock;
  onAsk: (prompt: string) => void;
  disabled?: boolean;
}) {
  const uid = useId();
  const [profile, setProfile] = useState<DecisionProfile>("observe");
  const [checked, setChecked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const current = PROFILES.find((p) => p.id === profile) ?? PROFILES[0]!;
  const dailySaving = profileSaving(block.daily_savings_cap_cents, profile);
  const scenario: ScenarioBlock = { ...block, type: "scenario" };
  const preview = useMemo(
    () => previewScenario(scenario, {
      savingsPerDayCents: dailySaving,
      unexpectedExpenseCents: 0,
      expenseDate: block.points[1]?.date ?? "",
    }),
    // Points immuables, recalcul instantané après un choix.
    [block, dailySaving],
  );
  const originalEnd = block.points.at(-1)?.balance_cents ?? 0;
  const commit = () => {
    if (disabled || sent) return;
    const remaining = current.steps.filter((step) => !checked.includes(step));
    const summary = [
      `J'ai exploré le tableau interactif « ${block.title} ».`,
      `Préférence : ${current.label}. Réduction hypothétique : ${money(dailySaving)} par jour.`,
      `Prévision Bank de fin de mois : ${money(originalEnd)} ; hypothèse : ${money(preview.finalBalanceCents)}.`,
      `Point bas estimé de l'hypothèse : ${money(preview.lowestBalanceCents)} le ${shortDate(preview.lowestDate)}.`,
      `Étapes cochées : ${checked.length} sur ${current.steps.length}.`,
      `Étapes restantes : ${remaining.join(" ; ") || "aucune"}.`,
      note.trim() ? `Ma remarque : ${note.trim().slice(0, 200)}.` : "",
      "Explique les limites de cette simulation et aide-moi à choisir une prochaine action ; ne suppose rien d'appliqué.",
    ].filter(Boolean).join("\n");
    onAsk(summary);
    setSent(true);
  };
  const handleChoice = (id: DecisionProfile) => {
    setProfile(id);
    setChecked([]);
    setSent(false);
  };
  const check = (step: string, yes: boolean) => {
    setChecked((prev) => yes ? [...prev.filter((x) => x !== step), step] : prev.filter((x) => x !== step));
    setSent(false);
  };

  return (
    <section className="chat-board" aria-label={block.title}>
      <header className="chat-scenario-head">
        <span className="chat-interactive-icon"><Icon name="target" /></span>
        <div>
          <span className="chat-interactive-type">Tableau interactif lié</span>
          <h3>{block.title}</h3>
          <p className="muted">Un choix actualise immédiatement la courbe et le plan. Rien n'est envoyé avant ta validation.</p>
        </div>
      </header>
      <fieldset className="chat-board-poll" disabled={disabled}>
        <legend>Quel rythme veux-tu explorer ?</legend>
        <div className="chat-board-choices">
          {PROFILES.map((option) => (
            <label key={option.id} className={`chat-board-choice${profile === option.id ? " is-active" : ""}`}>
              <input type="radio" name={`${uid}-profile`} value={option.id}
                     checked={profile === option.id} onChange={() => handleChoice(option.id)} />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <p className="chat-board-description" role="status">{current.intro}</p>
      <ScenarioChart original={block.points} hypothetical={preview.points} />
      <div className="chat-scenario-results chat-board-metrics" aria-live="polite">
        <div><span>Solde hypothétique en fin de mois</span><strong>{money(preview.finalBalanceCents)}</strong></div>
        <div><span>Écart avec la prévision</span><strong>{money(preview.differenceCents, { signed: true })}</strong></div>
        <div><span>Réduction quotidienne envisagée</span><strong>{money(dailySaving)}</strong></div>
      </div>
      {preview.overdraftDate && (
        <p className="chat-scenario-warning" role="status">
          <Icon name="alert" /> Ce scénario passe sous 0 € le {shortDate(preview.overdraftDate)}.
        </p>
      )}
      {block.daily_savings_cap_cents === 0 && (
        <p className="chat-board-muted">Bank n'estime aucune dépense variable quotidienne : les trois courbes sont identiques.</p>
      )}
      <fieldset className="chat-board-plan" disabled={disabled}>
        <legend>Ton plan adapté à ce choix <span>({checked.length}/{current.steps.length})</span></legend>
        <div className="chat-board-progress" role="progressbar" aria-label="Avancement de la checklist"
             aria-valuemin={0} aria-valuemax={current.steps.length} aria-valuenow={checked.length}>
          <span style={{ width: `${checked.length / current.steps.length * 100}%` }} />
        </div>
        {current.steps.map((step, index) => (
          <label key={step} className={`chat-board-step${checked.includes(step) ? " is-checked" : ""}`}>
            <input type="checkbox" checked={checked.includes(step)}
                   onChange={(event) => check(step, event.target.checked)} />
            <span className="chat-board-step-number">{index + 1}</span>
            <span>{step}</span>
          </label>
        ))}
      </fieldset>
      <div className="chat-board-footer">
        <label htmlFor={`${uid}-note`}>Un détail à préciser ? <span className="faint">(facultatif)</span></label>
        <textarea id={`${uid}-note`} rows={2} maxLength={200} disabled={disabled}
                  placeholder="Par exemple : je préfère commencer par mes abonnements…"
                  value={note} onChange={(e) => { setNote(e.target.value); setSent(false); }} />
        <p className="faint">{block.source}. Les montants sont des hypothèses, pas une promesse d'économie. Aucun compte n'est modifié.</p>
        <button type="button" className="btn sm" disabled={disabled || sent} onClick={commit}>
          <Icon name="arrow" /> {sent ? "Choix transmis à Bank" : "En discuter avec Bank"}
        </button>
      </div>
    </section>
  );
}
