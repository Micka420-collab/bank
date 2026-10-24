// Tes accords, groupés par ce qu'ils envoient (DESIGN.md § 3.10) : un titre par groupe, chaque accord en une ligne
// « Envoie : … » et son texte complet replié. Un seul composant : le parcours n'en montre que deux groupes, Réglages
// › Confidentialité les montre tous. Rien n'est coché d'avance : l'interrupteur dit l'état que le serveur a renvoyé.

import type { ReactNode } from "react";
import type { ConsentGroup } from "../api";

export const GROUP_TITLES: Record<ConsentGroup, string> = {
  public: "Rien te concernant",
  zone: "Ta zone, pas ton adresse",
  adresse: "Ton adresse",
  contenus: "Tes contenus, vers un service tiers",
};
const ORDER: ConsentGroup[] = ["public", "zone", "adresse", "contenus"];

export type ConsentRow = { purpose: string; title: string; text: string; granted: boolean; group?: ConsentGroup; sends?: string };

/** Les accords de chaque groupe, dans l'ordre d'affichage ; ceux qui n'ont pas de groupe à la fin, sans titre. */
export function groupConsents<T extends ConsentRow>(rows: T[], groups: ConsentGroup[] = ORDER): { group: ConsentGroup | null; rows: T[] }[] {
  const out: { group: ConsentGroup | null; rows: T[] }[] = groups
    .map((g) => ({ group: g as ConsentGroup | null, rows: rows.filter((r) => r.group === g) }))
    .filter((g) => g.rows.length > 0);
  const loose = rows.filter((r) => !r.group);
  if (loose.length) out.push({ group: null, rows: loose });
  return out;
}

export function ConsentGroups<T extends ConsentRow>({ consents, groups = ORDER, busy, onToggle, meta }: {
  consents: T[];
  groups?: ConsentGroup[];
  busy: boolean;
  onToggle: (c: T) => void;
  /** Une ligne de plus sous l'accord (« Accordé le 21 oct. »). */
  meta?: (c: T) => ReactNode;
}) {
  return (
    <div className="consent-groups">
      {groupConsents(consents, groups).map(({ group, rows }) => (
        <section key={group ?? "autres"} className="consent-group" aria-labelledby={group ? `cg-${group}` : undefined}>
          {group && <h3 id={`cg-${group}`}>{GROUP_TITLES[group]}</h3>}
          <ul className="consent-list">
            {rows.map((c) => (
              <li key={c.purpose}>
                <div>
                  <b id={`consent-${c.purpose}`}>{c.title}</b>
                  {c.sends && <p className="consent-sends" id={`consent-${c.purpose}-sends`}><span>Envoie :</span> {c.sends}</p>}
                  {c.sends ? (
                    <details className="consent-more">
                      <summary>Lire le texte complet</summary>
                      <p className="muted">{c.text}</p>
                    </details>
                  ) : (
                    <p className="muted">{c.text}</p>
                  )}
                  {meta && <span className="faint">{meta(c)}</span>}
                </div>
                <label className="switch">
                  <input type="checkbox" role="switch" checked={c.granted} disabled={busy} onChange={() => onToggle(c)}
                         aria-labelledby={`consent-${c.purpose}`} aria-describedby={c.sends ? `consent-${c.purpose}-sends` : undefined} />
                  <span aria-hidden="true" />
                </label>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
