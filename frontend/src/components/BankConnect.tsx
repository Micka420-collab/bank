// Connexion bancaire : tuiles aux couleurs de chaque banque, recherche instantanée, états visibles (repos, survol,
// connexion en cours, connectée, erreur). La connexion elle-même se fait chez la banque (DSP2, Enable Banking) :
// Bank ne voit ni ne stocke jamais d'identifiants.

import { useMemo, useState } from "react";
import type { Connection } from "../api";
import { BANK_BRANDS, brandOf, caisseLabel, colorFor, initials, matchBrand, searchAspsps, type BankBrand } from "../banks";
import { Icon } from "./Icon";

export type TileState = "idle" | "busy" | "connected" | "error";

export function BankMark({ name, small = false }: { name: string; small?: boolean }) {
  const brand = brandOf(name);
  const [logoFailed, setLogoFailed] = useState(false);
  const color = colorFor(name);
  return (
    <span className={`bank-mark${small ? " small" : ""}`} style={{ ["--brand" as string]: color }} aria-hidden="true">
      {brand && !logoFailed ? (
        <img src={`/banks/${brand.id}.svg`} alt="" onError={() => setLogoFailed(true)} />
      ) : (
        brand?.mark ?? initials(name)
      )}
    </span>
  );
}

export function tileState(name: string, connections: Connection[], busy: string | null, failed: string | null): TileState {
  if (busy === name) return "busy";
  if (failed === name) return "error";
  if (connections.some((c) => c.aspsp_name === name && c.status === "active")) return "connected";
  return "idle";
}

export function BankConnect({ aspsps, connections, busy, failed, onConnect, compact = false }: {
  aspsps: string[] | null;
  connections: Connection[];
  busy: string | null; // nom en cours de connexion
  failed: string | null; // dernier nom en échec
  onConnect: (name: string) => void;
  compact?: boolean;
}) {
  const [picked, setPicked] = useState<BankBrand | null>(null);
  const [query, setQuery] = useState("");
  const names = aspsps ?? [];
  const brands = useMemo(
    () => BANK_BRANDS.map((b) => ({ brand: b, names: matchBrand(b, names) })).filter((x) => x.names.length > 0),
    [names],
  );
  const results = useMemo(() => searchAspsps(query, names), [query, names]);
  const choices = picked ? matchBrand(picked, names) : [];
  const connectedCount = (list: string[]) =>
    list.filter((n) => connections.some((c) => c.aspsp_name === n && c.status === "active")).length;

  if (aspsps === null) {
    return (
      <div className="bank-grid" aria-busy="true" aria-label="Liste des banques en cours de chargement">
        {Array.from({ length: compact ? 4 : 8 }, (_, i) => <div key={i} className="bank-tile" style={{ opacity: 0.5 }}><span className="bank-mark" /><b className="skeleton"><i /></b></div>)}
      </div>
    );
  }

  return (
    <div className="stack">
      <label className="search">
        <Icon name="search" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Trouve ta banque (${names.length} établissements)`} aria-label="Rechercher une banque" autoComplete="off" />
      </label>
      {query.trim().length >= 2 ? (
        results.length === 0 ? (
          <p className="muted">Aucun établissement ne correspond à « {query} ». Tu peux toujours importer tes relevés.</p>
        ) : (
          <ul className="results" aria-label="Résultats">
            {results.map((n) => {
              const st = tileState(n, connections, busy, failed);
              return (
                <li key={n}>
                  <button type="button" className="btn link" disabled={busy !== null} onClick={() => onConnect(n)}>
                    <BankMark name={n} small />
                    {n}
                    {st === "connected" && <span className="tag ok">connectée</span>}
                    {st === "busy" && <span className="spinner" aria-label="Connexion en cours" />}
                  </button>
                </li>
              );
            })}
          </ul>
        )
      ) : picked && choices.length > 1 ? (
        <div className="card bank-choice" aria-live="polite">
          <div className="card-head">
            <h3 style={{ display: "inline-flex", alignItems: "center", gap: "0.6rem" }}><BankMark name={choices[0]!} small /> {picked.name} — laquelle est la tienne ?</h3>
            <button type="button" className="btn ghost sm" onClick={() => setPicked(null)}><Icon name="back" /> Toutes les banques</button>
          </div>
          <ul className="results" aria-label={`Caisses ${picked.name}`}>
            {choices.map((n, i) => {
              const st = tileState(n, connections, busy, failed);
              return (
                <li key={n}>
                  <button type="button" className="btn link" disabled={busy !== null} onClick={() => onConnect(n)} autoFocus={i === 0}>
                    {caisseLabel(picked, n)}
                    {st === "connected" && <span className="tag ok">connectée</span>}
                    {st === "busy" && <span className="spinner" aria-label="Connexion en cours" />}
                    {st === "error" && <span className="tag warn">échec</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div className="bank-grid" role="list" aria-label="Banques courantes">
          {brands.slice(0, compact ? 8 : brands.length).map(({ brand, names: n }) => {
            const single = n.length === 1;
            const st: TileState = single ? tileState(n[0]!, connections, busy, failed) : connectedCount(n) > 0 ? "connected" : n.some((x) => x === busy) ? "busy" : "idle";
            return (
              <button
                key={brand.id}
                type="button"
                role="listitem"
                className={`bank-tile${st === "connected" ? " is-connected" : st === "error" ? " is-error" : ""}`}
                aria-label={`${brand.name}${st === "connected" ? ", connectée" : st === "busy" ? ", connexion en cours" : st === "error" ? ", échec" : ""}`}
                disabled={busy !== null}
                onClick={() => (single ? onConnect(n[0]!) : setPicked(brand))}
              >
                <BankMark name={n[0]!} />
                <b>{brand.name}</b>
                {!single && <small>{n.length} caisses — choisir</small>}
                {st === "connected" && <span className="bank-state ok" aria-hidden="true"><Icon name="check" /></span>}
                {st === "busy" && <span className="bank-state busy" aria-hidden="true" />}
                {st === "error" && <span className="bank-state error" aria-hidden="true"><Icon name="close" /></span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
