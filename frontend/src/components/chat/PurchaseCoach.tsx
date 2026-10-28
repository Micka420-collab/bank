// Coach d'achat : un diagnostic calculé par Bank, une délibération choisie par la personne.
// Pas d'achat, de paiement ou de transfert, et pas de verdict inventé côté navigateur.
import { useId, useState } from "react";
import type { PurchaseCoachBlock } from "../../api";
import { money, shortDate } from "../../format";
import { Icon } from "../Icon";

type Why = "essential" | "useful" | "pleasure" | "impulse";
type Urgency = "today" | "later" | "unsure";

const VERDICTS: Record<PurchaseCoachBlock["answer"], { title: string; tone: string; advice: string }> = {
  oui: {
    title: "Budget compatible",
    tone: "positive",
    advice: "Le calcul de Bank ne voit pas de difficulté immédiate, mais c'est toi qui décides de son utilité.",
  },
  oui_mais: {
    title: "Possible, mais attention",
    tone: "caution",
    advice: "Cet achat réduit ta marge. Regarde ce que tu acceptes de sacrifier et si tu peux attendre.",
  },
  attendre: {
    title: "Mieux vaut patienter",
    tone: "caution",
    advice: "Le budget est plus serein après la date indiquée par Bank. Acheter tout de suite peut être risqué.",
  },
  plus_tard: {
    title: "Pas maintenant",
    tone: "caution",
    advice: "Bank propose un chemin d'épargne. Tu peux te fixer une date plutôt que de te priver définitivement.",
  },
  hors_de_portee: {
    title: "Déconseillé aujourd'hui",
    tone: "danger",
    advice: "Ta marge actuelle ne permet pas de financer cet achat raisonnablement. Priorité à la sécurité du budget.",
  },
};

export function purchaseReflection(why: Why, urgency: Urgency, canWait: boolean): string {
  if (why === "essential") {
    return "Si c'est nécessaire, vérifie une solution adaptée à ton budget plutôt que de reporter automatiquement.";
  }
  if (why === "impulse") {
    return "Tu l'as repéré sur un coup de tête : essaie de laisser passer 48 heures, puis vois si l'envie reste.";
  }
  if (urgency === "today" && canWait) {
    return "Tu souhaites l'avoir aujourd'hui, mais tu peux attendre. Compare le plaisir immédiat et la tranquillité.";
  }
  if (why === "useful") {
    return "Regarde l'usage réel, le coût total et les alternatives moins chères avant de décider.";
  }
  return "Un achat plaisir peut avoir sa place dans ton budget. L'important est de le choisir consciemment.";
}

export function ChatPurchaseCoach({ block, onAsk, disabled = false }: {
  block: PurchaseCoachBlock;
  onAsk: (text: string) => void;
  disabled?: boolean;
}) {
  const uid = useId();
  const [why, setWhy] = useState<Why>("pleasure");
  const [urgency, setUrgency] = useState<Urgency>("unsure");
  const [canWait, setCanWait] = useState(true);
  const [alternatePrice, setAlternatePrice] = useState("");
  const [sent, setSent] = useState(false);
  const verdict = VERDICTS[block.answer];
  const targetPrice = alternatePrice.trim() ? Number(alternatePrice.replace(",", ".")) : null;
  const validPrice = targetPrice !== null && Number.isFinite(targetPrice)
    && targetPrice > 0 && targetPrice <= 10_000_000;
  const changedPrice = validPrice && Math.round(targetPrice * 100) !== block.amount_cents;
  const invalidPrice = alternatePrice.trim().length > 0 && !validPrice;
  const finalMessage = (recalculate: boolean, challenge = false) => {
    if (disabled || sent || invalidPrice) return;
    const answers = [
      `Je réfléchis à l'achat « ${block.label} ».`,
      recalculate && changedPrice
        ? `Recalcule avec le prix de ${targetPrice!.toFixed(2)} € (à la place de ${money(block.amount_cents)}).`
        : `Prix évalué par Bank : ${money(block.amount_cents)}.`,
      `Nature de l'achat : ${({ essential: "indispensable", useful: "utile", pleasure: "plaisir", impulse: "impulsif" } as const)[why]}.`,
      `Urgence : ${({ today: "j'en ai envie aujourd'hui", later: "ça peut attendre", unsure: "je ne sais pas" } as const)[urgency]}.`,
      `Je peux patienter : ${canWait ? "oui" : "non"}.`,
      challenge
        ? "Je veux que tu me raisonnes si cet achat est peu judicieux : donne-moi des arguments concrets "
          + "fondés sur mon budget, le coût d'opportunité et la possibilité d'attendre. "
          + "Propose une alternative réaliste, sans jugement ni culpabilisation. "
          + "Si mes finances le permettent réellement, dis-le aussi franchement."
        : "Aide-moi à décider raisonnablement, sans culpabiliser, avec les vrais chiffres de Bank.",
      "Si le prix change, recalcule la prévision via show_purchase_coach avant de donner un nouveau verdict. "
      + "Ne considère pas l'achat comme effectué.",
    ];
    onAsk(answers.join("\n"));
    setSent(true);
  };

  return (
    <section className="chat-purchase" aria-label={block.title} data-tone={verdict.tone}>
      <header className="chat-purchase-head">
        <span className="chat-interactive-type">Coach d'achat · Analyse de Bank</span>
        <h3>{block.title}</h3>
        <p>{block.label} <strong>{money(block.amount_cents)}</strong></p>
      </header>
      <div className="chat-purchase-verdict" role="status">
        <Icon name={verdict.tone === "positive" ? "check" : verdict.tone === "danger" ? "alert" : "clock"} />
        <div><strong>{verdict.title}</strong><p>{block.headline}</p></div>
      </div>
      <p className="chat-purchase-advice">{verdict.advice}</p>
      {block.after.overdraft_date && (
        <p className="chat-scenario-warning" role="status">
          <Icon name="alert" /> Cet achat pourrait entraîner un découvert dès le {shortDate(block.after.overdraft_date)}.
        </p>
      )}
      <div className="chat-purchase-metrics">
        <div><span>Point bas sans cet achat</span><strong>{money(block.before.lowest_cents)}</strong></div>
        <div><span>Point bas après cet achat</span><strong>{money(block.after.lowest_cents)}</strong></div>
        <div><span>Budget par jour après</span><strong>{money(block.after.per_day_cents)}</strong></div>
      </div>
      <p className="chat-purchase-source">{block.source}. <span>Calcul prévisionnel, pas une garantie.</span></p>
      {block.best_date && (
        <p className="chat-purchase-path"><Icon name="calendar" /> Date plus favorable estimée : <b>{shortDate(block.best_date)}</b></p>
      )}
      {block.paths.length > 0 && (
        <div className="chat-purchase-paths">
          <strong>Autres chemins possibles</strong>
          {block.paths.map((path, i) => (
            <p key={i}><b>{path.label}</b> — {path.months === null ? "Délai inconnu" : `${path.months} mois`}. {path.note}</p>
          ))}
        </div>
      )}
      <div className="chat-purchase-reflect">
        <fieldset disabled={disabled}>
          <legend>Pourquoi veux-tu l'acheter ?</legend>
          <div className="chat-purchase-choices">
            {([
              ["essential", "C'est nécessaire"],
              ["useful", "Ça me serait utile"],
              ["pleasure", "Pour me faire plaisir"],
              ["impulse", "Sur un coup de tête"],
            ] as const).map(([value, label]) => (
              <label key={value} className={why === value ? "is-active" : ""}>
                <input type="radio" name={`${uid}-why`} value={value}
                       checked={why === value} onChange={() => { setWhy(value); setSent(false); }} />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset disabled={disabled}>
          <legend>Est-ce que ça presse ?</legend>
          <div className="chat-purchase-choices">
            {([
              ["today", "Je le veux maintenant"],
              ["later", "Je peux prendre mon temps"],
              ["unsure", "Je ne sais pas"],
            ] as const).map(([value, label]) => (
              <label key={value} className={urgency === value ? "is-active" : ""}>
                <input type="radio" name={`${uid}-urgency`} value={value}
                       checked={urgency === value} onChange={() => { setUrgency(value); setSent(false); }} />
                {label}
              </label>
            ))}
          </div>
          <label className="chat-purchase-wait">
            <input type="checkbox" checked={canWait}
                   onChange={(e) => { setCanWait(e.target.checked); setSent(false); }} />
            Je pourrais attendre 48 heures avant de décider
          </label>
        </fieldset>
        <p className="chat-purchase-reflection" aria-live="polite"><Icon name="spark" />{purchaseReflection(why, urgency, canWait)}</p>
      </div>
      <div className="chat-purchase-footer">
        <label htmlFor={`${uid}-price`}>Et si tu trouvais moins cher ? <span>(nouveau prix en euros)</span></label>
        <input id={`${uid}-price`} type="number" inputMode="decimal" min="0.01" max="10000000" step="0.01"
               placeholder={(block.amount_cents / 100).toFixed(2)}
               value={alternatePrice} disabled={disabled}
               onChange={(e) => { setAlternatePrice(e.target.value); setSent(false); }} />
        {changedPrice && <p className="chat-purchase-caution">Le verdict affiché concerne toujours {money(block.amount_cents)}. Recalcul nécessaire pour {money(Math.round(targetPrice! * 100))}.</p>}
        {invalidPrice && <p className="chat-purchase-caution" role="alert">Indique un prix valide et positif avant de continuer.</p>}
        <div className="chat-purchase-buttons">
          <button type="button" className="btn sm" disabled={disabled || sent || invalidPrice}
                  onClick={() => finalMessage(Boolean(changedPrice))}>
            <Icon name="arrow" /> {changedPrice ? "Recalculer avec Bank" : "Aide-moi à décider"}
          </button>
          {!changedPrice && (
            <button type="button" className="btn sm secondary" disabled={disabled || sent || invalidPrice}
                    onClick={() => finalMessage(false, true)}>
              <Icon name="shield" /> Raisonne-moi
            </button>
          )}
        </div>
        <p className="faint">Aucune commande, aucun paiement. Les réponses sont envoyées uniquement si tu cliques.</p>
      </div>
    </section>
  );
}
