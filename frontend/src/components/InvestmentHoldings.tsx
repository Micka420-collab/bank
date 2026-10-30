// Inventaire local : Bank ne confond jamais comptes, titres, documents et cours de marché.
import { useApi } from "../api";
import { money } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Loading } from "./ui";

type Position = {
  key: string;
  label: string;
  kind: string;
  origin: "compte" | "declaration" | "crypto";
  source_label: string;
  currency: string;
  quantity: string | null;
  value_cents: number | null;
  valuation_status: string;
  as_of: string | null;
  rate_pct: number | null;
  rate_as_of: string | null;
  rate_source: string | null;
  document_verified: boolean;
  required_facts: string[];
  potential_overlap: boolean;
  note: string;
};
type Inventory = {
  as_of: string;
  positions: Position[];
  counts: { total: number; unpriced_or_unverified: number; potential_overlaps: number };
  note: string;
};

const STATUS: Record<string, string> = {
  solde_date: "Solde bancaire daté",
  solde_ancien: "Solde ancien",
  date_inconnue: "Date du solde inconnue",
  devise_non_convertie: "Devise non convertie",
  valeur_declaree_non_verifiee: "Valeur déclarée, non vérifiée",
  cours_date: "Cotation récente",
  cours_absent_ou_perime: "Cours absent ou périmé",
};

export function InvestmentHoldings({ onCompare }: { onCompare?: (capitalCents: number) => void }) {
  const inventory = useApi<Inventory>("/api/watch/investments/holdings");
  if (inventory.error) return <ErrorNote message={inventory.error} onRetry={inventory.reload} />;
  if (!inventory.data) return <Loading what="Lecture des placements suivis" />;
  const data = inventory.data;
  if (!Array.isArray(data.positions) || !data.counts) {
    return <ErrorNote message="La réponse de l'inventaire des placements est incomplète." onRetry={inventory.reload} />;
  }
  return (
    <section className="section investment-holdings" aria-labelledby="investments-held-title">
      <div className="section-head">
        <h2 id="investments-held-title"><Icon name="wealth" /> Tes placements suivis</h2>
        <span className="tag">Lecture seule · données privées</span>
      </div>
      <p className="section-note">
        Les vrais soldes de comptes et quantités crypto connus de Bank, séparés des valeurs simplement déclarées.
        Un compte PEA ne permet pas de connaître ses ETF ou leurs frais sans relevé de titres.
      </p>
      {data.positions.length === 0 ? (
        <div className="investment-holdings-empty">
          <p>Pas encore de placement identifié dans les comptes, le profil ou le portefeuille crypto.</p>
          <p><a href="#/connecteurs">Connecter une banque</a> · <a href="#/situation">Déclarer un actif</a> · <a href="#/patrimoine">Voir l'épargne</a></p>
        </div>
      ) : <>
        <p className="investment-holdings-meta">{data.counts.total} lignes recensées · {data.counts.unpriced_or_unverified} valorisations anciennes ou non vérifiées
          {data.counts.potential_overlaps > 0 ? ` · ${data.counts.potential_overlaps} doublons possibles` : ""}</p>
        <div className="investment-holdings-grid">
          {data.positions.map((position) => <article className="investment-holdings-card" key={position.key}>
            <div className="investment-holdings-title">
              <h3>{position.label}</h3>
              <span className="tag">{position.kind.replaceAll("_", " ")}</span>
            </div>
            <p className="faint">{position.source_label} · {STATUS[position.valuation_status] ?? position.valuation_status}
              {position.as_of ? ` · ${position.as_of}` : ""}</p>
            {position.value_cents === null ?
              <p><strong>Valorisation EUR inconnue</strong></p> :
              <p><strong>{money(position.value_cents)}</strong>
                {position.valuation_status !== "solde_date" && position.valuation_status !== "cours_date"
                  ? <span className="tag warn">À vérifier</span> : null}</p>}
            {position.quantity !== null && <p>Quantité : <b>{position.quantity}</b></p>}
            {position.rate_pct !== null && <p className="faint">Taux réglementé : {position.rate_pct.toLocaleString("fr-FR")} %
              {position.rate_as_of ? ` · référence du ${position.rate_as_of}` : ""}. Taux susceptible de changer.</p>}
            {onCompare && position.value_cents !== null
              && position.value_cents >= 10_000 && position.value_cents <= 100_000_000
              && (position.valuation_status === "solde_date" || position.valuation_status === "cours_date") && (
                <button type="button" className="btn quiet sm" onClick={() => onCompare(position.value_cents!)}>
                  <Icon name="arrow" /> Comparer ce montant, sans transaction
                </button>
              )}
            {position.potential_overlap && <p className="notice warn"><Icon name="alert" /> Possiblement déjà compté dans une autre ligne.</p>}
            <details>
              <summary>Frais, risques et documents à vérifier</summary>
              <p className="faint">Aucun DIC ou contrat n'est lié automatiquement à cette position.</p>
              <ul>{position.required_facts.map(fact => <li key={fact}>{fact}</li>)}</ul>
              <a href="#/documents">Ouvrir le coffre-fort documentaire</a>
            </details>
            <p className="faint">{position.note}</p>
          </article>)}
        </div>
      </>}
      <p className="faint">{data.note}</p>
    </section>
  );
}
