// « Je peux m'acheter… ? » : la question qu'on se pose devant une vitrine, répondue avec la vraie prévision.

import { useState, type FormEvent } from "react";
import { api, useApi, type Category, type PurchasePlan } from "../api";
import { money, parseEuros, shortDate } from "../format";
import { useAsyncAction } from "./ui";

export const ANSWER_LABEL: Record<PurchasePlan["answer"], string> = {
  oui: "Oui",
  oui_mais: "Oui, mais",
  attendre: "Pas tout de suite",
  plus_tard: "Plus tard",
  hors_de_portee: "Pas raisonnable",
};

export function monthsLabel(months: number): string {
  if (months < 12) return `${months} mois`;
  const y = Math.floor(months / 12);
  const m = months % 12;
  return m ? `${y} an${y > 1 ? "s" : ""} et ${m} mois` : `${y} an${y > 1 ? "s" : ""}`;
}

export function Decide() {
  const cats = useApi<Category[]>("/api/categories");
  const [amountText, setAmountText] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState("");
  const [plan, setPlan] = useState<PurchasePlan | null>(null);
  const [busy, error, run] = useAsyncAction();

  const ask = (e: FormEvent) => {
    e.preventDefault();
    const amount = parseEuros(amountText);
    if (!amount || amount <= 0) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    void run(async () => {
      setPlan(await api<PurchasePlan>("/api/decisions/purchase", {
        method: "POST",
        body: { amount_cents: amount, label: label.trim() || "Achat", category: category || null },
      }));
    });
  };

  return (
    <section className="section decide" aria-labelledby="decide-title">
      <h2 id="decide-title">Je peux m'acheter… ?</h2>
      <form className="decide-form" onSubmit={ask}>
        <span className="euro-field">
          <input inputMode="decimal" value={amountText} onChange={(e) => setAmountText(e.target.value)} required
                 placeholder="250" aria-label="Montant" aria-invalid={invalid || undefined} />
          <span aria-hidden="true">€</span>
        </span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Quoi ? (facultatif)" maxLength={80} aria-label="Libellé" />
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Catégorie">
          <option value="">Catégorie…</option>
          {cats.data?.filter((c) => c.kind === "variable" || c.kind === "fixed").map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
        </select>
        <button className="btn" disabled={busy}>Réponse</button>
      </form>
      {invalid && <p className="error" role="alert">Indique un montant, par exemple 250 ou 1 200,50.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {plan && (
        <div className={`answer answer-${plan.answer}`} aria-live="polite">
          <p className="answer-head"><b>{ANSWER_LABEL[plan.answer]}.</b> {plan.headline}</p>
          <p className="muted">{plan.explanation}</p>
          {plan.paths.length > 0 && (
            <ul className="paths">
              {plan.paths.map((p) => (
                <li key={p.label}>
                  <b>{p.label}</b> —{" "}
                  {p.months === null
                    ? p.note
                    : <>{monthsLabel(p.months)}, soit vers {p.affordable_on ? shortDate(p.affordable_on).replace(/^\d+ /, "") : "?"} en mettant {money(p.monthly_cents, { whole: true })} de côté par mois. <span className="faint">{p.note}</span></>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
