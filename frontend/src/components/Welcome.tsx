// Première ouverture : trois façons de commencer, chacune mène à un écran utile en moins de trois minutes.

import { useState, type FormEvent } from "react";
import { api } from "../api";
import { parseEuros } from "../format";
import { navigate } from "../router";
import { useAsyncAction } from "./ui";

export function Welcome({ onStarted }: { onStarted: () => void }) {
  const [path, setPath] = useState<"none" | "figures">("none");
  return (
    <div className="welcome">
      <p className="welcome-kicker">Bienvenue dans ton cahier de comptes</p>
      <h1>Par quoi on commence ?</h1>
      <p className="muted">
        Bank calcule ce que tu peux dépenser chaque jour, prévoit tes échéances, suit tes aides et ton patrimoine.
        Plus il te connaît, plus il est utile — et il te demandera une chose à la fois, en disant pourquoi.
      </p>
      <div className="paths-grid">
        <button type="button" className="path" onClick={() => navigate("connecteurs")}>
          <b>Connecter ma banque</b>
          <span>Mise à jour automatique, sans rien saisir. Bank reconnaît ton salaire, ton loyer, tes abonnements.</span>
          <small>Recommandé · 2 minutes</small>
        </button>
        <button type="button" className="path" onClick={() => navigate("operations?import=1")}>
          <b>Importer un relevé</b>
          <span>CSV ou OFX depuis ton espace bancaire, toutes banques. Rien ne sort de chez toi.</span>
          <small>3 minutes</small>
        </button>
        <button type="button" className="path" aria-pressed={path === "figures"} onClick={() => setPath("figures")}>
          <b>Juste trois chiffres</b>
          <span>Ton solde, ton revenu, ton loyer : un premier budget tout de suite, le reste plus tard.</span>
          <small>30 secondes</small>
        </button>
      </div>
      {path === "figures" && <ThreeFigures onDone={onStarted} />}
    </div>
  );
}

function ThreeFigures({ onDone }: { onDone: () => void }) {
  const [balance, setBalance] = useState("");
  const [income, setIncome] = useState("");
  const [housing, setHousing] = useState("");
  const [busy, error, run] = useAsyncAction();
  const [invalid, setInvalid] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const b = parseEuros(balance);
    const i = parseEuros(income);
    const h = housing.trim() === "" ? 0 : parseEuros(housing);
    if (b === null || i === null || i <= 0 || h === null || h < 0) {
      setInvalid("Vérifie les montants : par exemple 640, 1 900 et 650.");
      return;
    }
    setInvalid(null);
    void run(async () => {
      await api("/api/accounts", { method: "POST", body: { name: "Compte courant", kind: "checking", balance_cents: b } });
      await api("/api/onboarding/net_monthly_income", { method: "PUT", body: { value: i } });
      if (h > 0) await api("/api/onboarding/housing_cost", { method: "PUT", body: { value: h } });
      onDone();
    });
  };

  return (
    <form className="panel figures-form" onSubmit={submit} aria-label="Trois chiffres pour commencer">
      <div className="form-grid">
        <label className="field">
          Solde de ton compte courant
          <span className="euro-field">
            <input inputMode="decimal" required autoFocus value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="640" />
            <span aria-hidden="true">€</span>
          </span>
        </label>
        <label className="field">
          Revenu net par mois
          <span className="euro-field">
            <input inputMode="decimal" required value={income} onChange={(e) => setIncome(e.target.value)} placeholder="1 900" />
            <span aria-hidden="true">€</span>
          </span>
        </label>
        <label className="field">
          Loyer ou crédit logement
          <span className="euro-field">
            <input inputMode="decimal" value={housing} onChange={(e) => setHousing(e.target.value)} placeholder="650" />
            <span aria-hidden="true">€</span>
          </span>
          <span className="faint">Facultatif.</span>
        </label>
      </div>
      {invalid && <p className="error" role="alert">{invalid}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button className="btn" disabled={busy}>Voir mon budget</button>
        <span className="faint">Tu pourras connecter ta banque ensuite : Bank affinera tout seul.</span>
      </div>
    </form>
  );
}
