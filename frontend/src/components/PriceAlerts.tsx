// Alertes de cours : un seuil haut, un seuil bas ou une variation sur 24 h, sur un actif dont Bank suit le cours.
// Un constat envoyé dans tes messageries une fois par franchissement, jamais un conseil. La liste dit l'état de
// chaque alerte (armée, déclenchée le …) ; réarmer et supprimer sont à un geste.

import { useEffect, useState, type FormEvent } from "react";
import { api, useApi, type PriceAlert, type PriceAlertKind, type PriceAlertsView } from "../api";
import { shortDate } from "../format";
import { Icon } from "./Icon";
import { Segmented } from "./Segmented";
import { ErrorNote, Field, useAsyncAction, useToast } from "./ui";

const KIND_TEXT: Record<PriceAlertKind, string> = { above: "au-dessus de", below: "sous", change: "varie de" };

export function eurText(eur: string | null): string {
  if (eur === null) return "—";
  const n = Number(eur);
  return n.toLocaleString("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: n >= 1000 ? 0 : n >= 1 ? 2 : 6 });
}

export function firedText(iso: string): string {
  const d = new Date(iso);
  // Le jour et l'heure dans ton fuseau, tous deux : entre minuit et 2 h, la date UTC serait celle de la veille.
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `le ${shortDate(day)} à ${d.getHours()} h ${String(d.getMinutes()).padStart(2, "0")}`;
}

export function alertText(a: PriceAlert): string {
  return a.kind === "change" ? `${a.asset} ${KIND_TEXT.change} ${a.threshold_text} sur 24 h` : `${a.asset} ${KIND_TEXT[a.kind]} ${a.threshold_text}`;
}

export function changeText(change: string | null): string {
  if (change === null) return "—";
  const n = Number(change);
  return `${n > 0 ? "+" : ""}${n.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} %`;
}

export function PriceAlerts({ refresh = 0 }: { refresh?: number }) {
  const v = useApi<PriceAlertsView>("/api/crypto/alerts");
  const [view, setView] = useState<PriceAlertsView | null>(null);
  // Un relevé peut déclencher une alerte : la liste se relit, sans effacer le formulaire en cours de saisie.
  const { reload } = v;
  useEffect(() => {
    if (!refresh) return;
    setView(null);
    reload();
  }, [refresh]); // eslint-disable-line -- reload change à chaque rendu, seul le relevé compte
  const data = view ?? v.data;
  const [asset, setAsset] = useState("");
  const [kind, setKind] = useState<PriceAlertKind>("below");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  if (v.error) return <ErrorNote message={v.error} onRetry={v.reload} />;
  if (!data) return null;
  const chosen = asset || data.assets[0] || "";
  const full = data.alerts.length >= data.max;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      setView(await api<PriceAlertsView>("/api/crypto/alerts", { method: "POST", body: { asset: chosen, kind, value: value.trim(), label: label.trim() || null } }));
      setValue("");
      setLabel("");
      toast("Alerte armée. Bank te prévient au franchissement, une fois.");
    });
  };
  const rearm = (id: number) => run(async () => {
    setView(await api<PriceAlertsView>(`/api/crypto/alerts/${id}/rearm`, { method: "POST" }));
    toast("Alerte réarmée.");
  });
  const remove = (id: number) => run(async () => {
    await api(`/api/crypto/alerts/${id}`, { method: "DELETE" });
    setView(await api<PriceAlertsView>("/api/crypto/alerts"));
  });

  return (
    <section className="card reveal cx-alerts" style={{ marginTop: "1rem" }} aria-labelledby="cx-alerts">
      <div className="card-head"><h2 id="cx-alerts">Alertes de cours</h2><span className="faint">{data.alerts.length} / {data.max}</span></div>
      <p className="faint">Un constat dans tes messageries quand le cours franchit le seuil que tu fixes, une fois par franchissement. Jamais un conseil.</p>
      {data.alerts.length === 0 ? (
        <p className="muted">Aucune alerte. Exemple : « BTC sous 50 000 € », ou « ETH varie de 8 % sur 24 h ».</p>
      ) : (
        <ul className="cx-wallets cx-alert-list">
          {data.alerts.map((a) => (
            <li key={a.id} className={`is-${a.state}`}>
              <div>
                <b>{alertText(a)}</b>
                {a.state === "armed" ? <span className="tag ok">armée</span> : <span className="tag warn">déclenchée {a.triggered_at ? firedText(a.triggered_at) : ""}</span>}
                {a.label && <span className="faint">« {a.label} »</span>}
                <span className="faint">
                  {a.kind === "change" ? `Variation sur 24 h : ${changeText(a.change_24h)}` : `Cours : ${eurText(a.price_eur)}`}
                  {a.state === "triggered" && a.triggered_price ? ` · au déclenchement : ${eurText(a.triggered_price)}` : ""}
                </span>
              </div>
              <div className="cx-alert-actions">
                {a.state === "triggered" && (
                  <button type="button" className="btn sm quiet" disabled={busy} onClick={() => void rearm(a.id)}><Icon name="undo" /> Réarmer</button>
                )}
                <button type="button" className="btn link" disabled={busy} onClick={() => void remove(a.id)} aria-label={`Supprimer l'alerte ${alertText(a)}`}><Icon name="trash" /></button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {data.assets.length === 0 ? (
        <p className="muted" style={{ marginTop: "1rem" }}>Une alerte porte sur un actif dont Bank suit le cours : ajoute d'abord un avoir ou une adresse.</p>
      ) : (
        <form className="form-grid" style={{ marginTop: "1rem" }} onSubmit={submit}>
          <Field label="Actif">
            <select value={chosen} onChange={(e) => setAsset(e.target.value)}>
              {data.assets.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </Field>
          <Segmented value={kind} onChange={setKind} label="Type"
                     options={(["below", "above", "change"] as PriceAlertKind[]).map((k) => ({ value: k, label: data.kinds[k] }))} />
          <Field label={kind === "change" ? "Variation (en %)" : "Seuil (en €)"}
                 hint={kind === "change" ? `De ${data.bounds.change_min} à ${data.bounds.change_max} %, à la hausse comme à la baisse` : `Réarmée seule quand le cours revient ${data.bounds.margin_percent} % de l'autre côté`}>
            <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" placeholder={kind === "change" ? "8" : "50 000"} maxLength={32} required />
          </Field>
          <Field label="Repère (facultatif)"><input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} placeholder="Mon prix d'achat" /></Field>
          {error && <ErrorNote message={error} />}
          <button className={`btn quiet${busy ? " is-loading" : ""}`} disabled={busy || full || !value.trim()}><Icon name="bell" /> Armer l'alerte</button>
        </form>
      )}
    </section>
  );
}
