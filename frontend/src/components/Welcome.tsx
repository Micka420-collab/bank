// Première ouverture : trois façons de commencer, chacune mène à un écran utile en moins de trois minutes.

import { useState, type FormEvent } from "react";
import { api, useApi } from "../api";
import { parseEuros } from "../format";
import { navigate } from "../router";
import { useAsyncAction } from "./ui";

export type StartPath = "bank" | "import" | "figures";

/** Où en est la connexion bancaire : prête (Enable Banking déjà configuré), à configurer, ou pas encore lu. */
export type BankAvailability = "ready" | "setup" | null;

/** Téléphone ou tablette (pointeur grossier) : télécharger puis redéposer un fichier de clé y est pénible. */
export function isPhone(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches ?? false;
}

/** Enable Banking configuré sur ce serveur ? Lu à /api/banks (aucun secret : un booléen). */
export function useBankAvailability(): BankAvailability {
  const banks = useApi<{ enabled: boolean }>("/api/banks");
  if (banks.data) return banks.data.enabled ? "ready" : "setup";
  return banks.error ? "setup" : null;
}

/** Les trois chemins, en cartes : Aujourd'hui (budget vide) et l'étape « Ta banque » du parcours les partagent.
 *  La carte banque dit ce qu'elle demande vraiment : sans Enable Banking configuré, un compte gratuit à créer chez
 *  eux et un fichier de clé, une quinzaine de minutes, plus simple sur ordinateur. Sur téléphone, dans ce cas, les
 *  trois chiffres passent en premier. */
export function StartPaths({ chosen, onChoose, bank = null, phone = false }: {
  chosen: StartPath | null;
  onChoose: (p: StartPath) => void;
  bank?: BankAvailability;
  phone?: boolean;
}) {
  const figuresFirst = bank === "setup" && phone;
  const bankCard = (
    <button key="bank" type="button" className="path" aria-pressed={chosen === "bank"} onClick={() => onChoose("bank")}>
      <b>Connecter ma banque</b>
      {bank === "setup" ? (
        <>
          <span>Mise à jour automatique ensuite. Demande d'abord de créer un compte gratuit chez Enable Banking et d'y déposer un fichier de clé.</span>
          <small>Environ 15 minutes, une seule fois · plus simple sur ordinateur</small>
        </>
      ) : (
        <>
          <span>Mise à jour automatique, sans rien saisir. Bank reconnaît ton salaire, ton loyer, tes abonnements.</span>
          <small>{bank === "ready" ? "Recommandé · 2 minutes, chez ta banque" : "Mise à jour automatique"}</small>
        </>
      )}
    </button>
  );
  const importCard = (
    <button key="import" type="button" className="path" aria-pressed={chosen === "import"} onClick={() => onChoose("import")}>
      <b>Importer un relevé</b>
      <span>CSV ou OFX depuis ton espace bancaire, toutes banques. Rien ne sort de chez toi.</span>
      <small>3 minutes</small>
    </button>
  );
  const figuresCard = (
    <button key="figures" type="button" className="path" aria-pressed={chosen === "figures"} onClick={() => onChoose("figures")}>
      <b>Juste trois chiffres</b>
      <span>Ton solde, ton revenu, ton loyer : un premier budget tout de suite, le reste plus tard.</span>
      <small>{figuresFirst ? "Recommandé sur téléphone · 30 secondes" : "30 secondes"}</small>
    </button>
  );
  return (
    <div className="paths-grid" role="group" aria-label="Par quoi commencer">
      {figuresFirst ? [figuresCard, bankCard, importCard] : [bankCard, importCard, figuresCard]}
    </div>
  );
}

export function Welcome({ onStarted }: { onStarted: () => void }) {
  const [path, setPath] = useState<StartPath | null>(null);
  const bank = useBankAvailability();
  const choose = (p: StartPath) => {
    if (p === "bank") navigate("bienvenue/banque");
    else if (p === "import") navigate("operations?import=1");
    else setPath(p);
  };
  return (
    <div className="welcome">
      <p className="welcome-kicker">Bienvenue dans ton cahier de comptes</p>
      <h1>Par quoi on commence ?</h1>
      <p className="muted">
        Bank calcule ce que tu peux dépenser chaque jour, prévoit tes échéances, suit tes aides et ton patrimoine.
        Plus il te connaît, plus il est utile — et il te demandera une chose à la fois, en disant pourquoi.
      </p>
      <StartPaths chosen={path} onChoose={choose} bank={bank} phone={isPhone()} />
      {path === "figures" && <ThreeFigures onDone={onStarted} />}
    </div>
  );
}

export function ThreeFigures({ onDone, submitLabel = "Voir mon budget" }: { onDone: () => void; submitLabel?: string }) {
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
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>{submitLabel}</button>
        <span className="faint">Tu pourras connecter ta banque ensuite : Bank affinera tout seul.</span>
      </div>
    </form>
  );
}
