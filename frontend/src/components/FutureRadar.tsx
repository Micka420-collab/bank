// Radar : prévisions chiffrées et contexte macro sont distincts, sans relation causale inventée.
import { useApi } from "../api";
import { money, shortDate } from "../format";
import { Icon } from "./Icon";
import { ErrorNote, Loading } from "./ui";

export type RadarSignal = {
  key: string;
  label: string;
  on: string;
  days: number;
  amount_cents: number;
  projected_month_end_cents: number;
  level: "risk" | "watch";
  basis: string;
  source: string;
  confidence: "declared" | "estimated";
};
export type RadarArticle = {
  id: number;
  title: string;
  published_at: string;
  url: string | null;
  source: string;
  topics: string[];
};
export type RadarCryptoStress = {
  tracked_count: number;
  priced_count: number;
  stale_count: number;
  assets: { asset: string; value_cents: number; as_of: string }[];
  valued_cents: number;
  shock_10_cents: number;
  shock_25_cents: number;
  note: string;
};
export type RadarView = {
  crypto_stress: RadarCryptoStress;
  horizon_days: number;
  available: boolean;
  news_consent: boolean;
  signals: RadarSignal[];
  macro_context: RadarArticle[];
  disclaimer: string;
};

export function FutureRadar() {
  const result = useApi<RadarView>("/api/watch/radar");
  if (result.error) return <ErrorNote message={result.error} onRetry={result.reload} />;
  if (!result.data) return <Loading what="Lecture du radar de prévision" />;
  const data = result.data;
  return (
    <section className="section future-radar" aria-labelledby="radar-title">
      <div className="section-head">
        <h2 id="radar-title"><Icon name="trendUp" /> Radar · {data.horizon_days} prochains jours</h2>
        <span className="tag">Prévision, pas certitude</span>
      </div>
      <p className="section-note">Bank croise tes échéances connues avec sa prévision. Les informations économiques publiques sont affichées à part, car une nouvelle ne démontre pas une perte future.</p>
      {!data.available ? (
        <p className="notice warn">Aucune prévision personnelle fiable sans compte courant budgétaire. <a href="#/connecteurs">Connecter un compte</a> ou importer un relevé.</p>
      ) : data.signals.length === 0 ? (
        <p className="muted">Aucun gros prélèvement identifié sur cette période. Ce n'est pas une garantie qu'il n'y aura pas d'imprévu.</p>
      ) : (
        <ul className="future-radar-list">
          {data.signals.map((signal) => (
            <li key={signal.key} className={`future-radar-signal is-${signal.level}`}>
              <div className="future-radar-timing">
                <strong>{signal.days === 0 ? "Aujourd'hui" : `J−${signal.days}`}</strong>
                <span>{shortDate(signal.on)}</span>
              </div>
              <div className="future-radar-detail">
                <div className="future-radar-heading">
                  <strong>{signal.label}</strong>
                  <span className={`tag ${signal.level === "risk" ? "warn" : ""}`}>
                    {signal.level === "risk" ? "Marge à surveiller" : "Dépense à prévoir"}
                  </span>
                </div>
                <p>{money(signal.amount_cents)} prévus · fin du mois estimée : <b>{money(signal.projected_month_end_cents)}</b>.</p>
                <p className="faint">{signal.basis} · {signal.confidence === "declared" ? "Saisi par toi" : "Estimation à confirmer"}.</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <section className="future-radar-crypto" aria-labelledby="radar-crypto-title">
        <h3 id="radar-crypto-title"><Icon name="crypto" /> Tes investissements crypto · test de résistance</h3>
        {data.crypto_stress.tracked_count === 0 ? (
          <p className="muted">Aucun avoir crypto renseigné. <a href="#/crypto">Ajouter mes avoirs</a> pour explorer un scénario.</p>
        ) : data.crypto_stress.priced_count === 0 ? (
          <p className="muted">Aucun cours récent pour tes avoirs. Réactualise les cours depuis Crypto avant de calculer une simulation.</p>
        ) : (
          <>
            <p>Valeur des avoirs avec cotations récentes : <strong>{money(data.crypto_stress.valued_cents)}</strong>.</p>
            <div className="future-radar-stress" role="group" aria-label="Scénarios de baisse hypothétique">
              <div><span>Si les cours reculaient de 10 %</span><strong>−{money(data.crypto_stress.shock_10_cents)}</strong></div>
              <div><span>Si les cours reculaient de 25 %</span><strong>−{money(data.crypto_stress.shock_25_cents)}</strong></div>
            </div>
            <p className="faint">Actifs cotés inclus : {data.crypto_stress.assets.map((a) => a.asset).join(", ")}.
              {data.crypto_stress.stale_count > 0 ? ` ${data.crypto_stress.stale_count} actif(s) sans cours récent exclu(s).` : ""}
            </p>
          </>
        )}
        <p className="faint">{data.crypto_stress.note}</p>
      </section>
      <div className="future-radar-context">
        <h3><Icon name="news" /> Actualités financières et internationales vérifiables</h3>
        {!data.news_consent ? <p className="muted">La veille réseau est désactivée. <a href="#/reglages">Donner ton accord dans Confidentialité</a> pour lire des sources publiques.</p> : data.macro_context.length === 0 ? <p className="muted">Aucun titre officiel récent indexé sur ces thèmes. L'absence de titre ne signifie pas que les marchés sont sans risque.</p> : (
          <ul className="future-radar-articles">{data.macro_context.map((article) => (
            <li key={article.id}>
              <b>{article.url ? <a href={article.url} target="_blank" rel="noopener noreferrer">{article.title} <Icon name="external" size={14} /></a> : article.title}</b>
              <p className="faint">{article.source} · {shortDate(article.published_at)} · {article.topics.join(", ")}</p>
            </li>
          ))}</ul>
        )}
        <p className="faint">Ces titres contextualisent les risques. Bank ne connaît pas automatiquement l'exposition de tous tes investissements et ne prédit pas leur performance à partir des seuls titres.</p>
      </div>
      <p className="faint">{data.disclaimer}</p>
      <p className="faint">Pour recevoir les alertes budgétaires déjà calculées : <a href="#/messageries">Messageries, dont WhatsApp</a> (connexion et notifications à activer).</p>
    </section>
  );
}
