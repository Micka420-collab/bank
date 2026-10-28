// Transparence CRS, DAC2, DAC8 et FATCA : informations officielles, jamais une liste
// de comptes « cachés ». La présence de la relation bilatérale se vérifie à l'OCDE.
import { useId, useState } from "react";
import { Icon } from "./Icon";

export const CRS_SOURCES = {
  relations: "https://www.oecd.org/en/topics/sub-issues/international-standards-on-tax-transparency/automatic-exchange-of-information-exchange-relationships.html",
  dac2: "https://taxation-customs.ec.europa.eu/taxation/tax-transparency-cooperation/administrative-co-operation-and-mutual-assistance/directive-administrative-cooperation-dac/dac2_en",
  dac8: "https://taxation-customs.ec.europa.eu/taxation/tax-transparency-cooperation/administrative-co-operation-and-mutual-assistance/directive-administrative-cooperation-dac/dac8_en",
  declaration: "https://www.impots.gouv.fr/formulaire/3916/declaration-par-un-resident-dun-compte-letranger-ou-dun-contrat-de-capitalisation-o",
} as const;

export const COUNTRIES = [
  ["FR", "France"], ["BE", "Belgique"], ["CH", "Suisse"], ["LU", "Luxembourg"],
  ["DE", "Allemagne"], ["ES", "Espagne"], ["GB", "Royaume-Uni"], ["US", "États-Unis"],
  ["SG", "Singapour"], ["AE", "Émirats arabes unis"], ["OTHER", "Autre juridiction"],
] as const;

export function explainCRS(taxResidence: string, bankCountry: string): string {
  if (!taxResidence || !bankCountry) return "Indique les deux juridictions pour commencer.";
  if (taxResidence === "OTHER" || bankCountry === "OTHER") {
    return "La juridiction n'est pas dans cette sélection indicative : vérifie directement les relations activées à l'OCDE et les règles nationales.";
  }
  if (taxResidence === bankCountry) {
    return "Même juridiction choisie : le CRS vise surtout l'échange international. Les obligations fiscales et déclaratives locales restent applicables.";
  }
  if (taxResidence === "US" || bankCountry === "US") {
    return "Les États-Unis utilisent notamment FATCA, distinct du CRS. Ne classe pas ce cas comme « non-déclaré » : vérifie aussi les obligations locales.";
  }
  return "Deux juridictions différentes : l'adhésion au CRS ne suffit pas. Vérifie le sens de l'échange et la relation bilatérale effectivement activée dans l'annuaire de l'OCDE.";
}

export function CrsTransparency() {
  const [taxResidence, setTaxResidence] = useState("FR");
  const [bankCountry, setBankCountry] = useState("CH");
  const taxId = useId();
  const bankId = useId();

  return (
    <section className="section crs-transparency" aria-labelledby="crs-title" id="crs">
      <div className="section-head"><h2 id="crs-title"><Icon name="shield" /> CRS / non-CRS des banques</h2><span className="tag">Information réglementaire</span></div>
      <p className="section-note">
        Le <b>Common Reporting Standard</b> (CRS) organise l'échange automatique de certaines informations sur
        les comptes financiers entre administrations fiscales. <b>Non-CRS</b> n'est pas un label fiable de banque,
        et ne signifie pas « anonyme », « non déclaré » ou « exonéré d'impôts ».
      </p>
      <div className="crs-selectors">
        <div><label htmlFor={taxId}>Ta résidence fiscale</label>
          <select id={taxId} value={taxResidence} onChange={(e) => setTaxResidence(e.target.value)}>
            {COUNTRIES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </div>
        <div><label htmlFor={bankId}>Juridiction de l'établissement financier</label>
          <select id={bankId} value={bankCountry} onChange={(e) => setBankCountry(e.target.value)}>
            {COUNTRIES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </div>
      </div>
      <div className="crs-result" role="status">
        <strong>Relation de déclaration : à vérifier officiellement</strong>
        <p>{explainCRS(taxResidence, bankCountry)}</p>
        <p className="faint">La banque, sa filiale, le type de compte et la résidence fiscale peuvent modifier les obligations. Cette page ne prétend pas fournir un statut CRS actualisé banque par banque.</p>
        <a href={CRS_SOURCES.relations} target="_blank" rel="noopener noreferrer">
          Vérifier les relations CRS activées auprès de l'OCDE <Icon name="external" size={14} />
        </a>
      </div>
      <div className="crs-guide">
        <div><h3>CRS / DAC2</h3><p>Échanges automatiques de comptes financiers dans le cadre de l'OCDE et de la coopération fiscale européenne.</p><a href={CRS_SOURCES.dac2} target="_blank" rel="noopener noreferrer">Source : Commission européenne</a></div>
        <div><h3>Crypto / DAC8</h3><p>Depuis le 1er janvier 2026, le cadre DAC8 étend la collecte de données sur les cryptoactifs dans l'UE. Premier échange prévu au plus tard le 30 septembre 2027.</p><a href={CRS_SOURCES.dac8} target="_blank" rel="noopener noreferrer">Source : Commission européenne</a></div>
        <div><h3>Déclaration en France</h3><p>Si tu es résident fiscal français, certains comptes bancaires et d'actifs numériques à l'étranger peuvent nécessiter le formulaire 3916–3916 bis, indépendamment du seul statut CRS.</p><a href={CRS_SOURCES.declaration} target="_blank" rel="noopener noreferrer">Source : impots.gouv.fr</a></div>
      </div>
      <p className="faint">Cette vérification est pédagogique : elle ne garantit ni l'application d'une convention à un établissement précis ni une dispense déclarative. Les deux juridictions sélectionnées ne sont pas transmises à un service tiers par Bank.</p>
    </section>
  );
}
