// Papiers › « Proposés par ta boîte mail » : les PDF repérés dans la boîte reliée, avec l'expéditeur, l'objet et le
// genre deviné ; tu importes (la pièce est relue dans la boîte, vérifiée, déposée au coffre puis lue) ou tu écartes.
// Rien n'entre dans le coffre sans ce geste. Invisible tant qu'aucune boîte n'est reliée.

import { useState } from "react";
import { api, useApi, type MailProposal, type MailStatus } from "../api";
import { shortDate } from "../format";
import { Icon } from "./Icon";
import { useAsyncAction, useToast } from "./ui";

function sizeText(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1).replace(".", ",")} Mo` : `${Math.max(1, Math.round(n / 1_000))} ko`;
}

export function MailProposals({ onImported }: { onImported?: () => void }) {
  const st = useApi<MailStatus>("/api/mail");
  const [busy, error, run] = useAsyncAction();
  const [working, setWorking] = useState<number | null>(null);
  const toast = useToast();
  const data = st.data;
  if (!data?.configured || data.proposals.length === 0) return null;
  const decide = (p: MailProposal, action: "import" | "dismiss") =>
    void run(async () => {
      setWorking(p.id);
      try {
        const out = await api<MailStatus>(`/api/mail/proposals/${p.id}/${action}`, { method: "POST" });
        toast(action === "import" ? `« ${p.filename} » déposé au coffre : lecture en cours.` : "Écarté.");
        if (action === "import" && out.document_ids?.length) onImported?.();
        st.reload();
      } finally {
        setWorking(null);
      }
    });
  return (
    <section className="card section mail-proposals" aria-labelledby="mail-proposals-title">
      <div className="card-head">
        <h2 id="mail-proposals-title"><Icon name="doc" /> Proposés par ta boîte mail</h2>
        <span className="faint">{data.proposals.length} PDF · <a href="#/connecteurs#mail">réglages</a></span>
      </div>
      <p className="muted">Trouvés dans « {data.account?.folder ?? "INBOX"} ». Importer relit la pièce dans ta boîte et la dépose au coffre ; écarter l'oublie. Bank ne touche pas au mail.</p>
      <ul className="mail-list">
        {data.proposals.map((p) => (
          <li key={p.id} className="mail-item">
            <div className="mail-text">
              <b>{p.filename}</b> <span className="tag">{p.kind_label}</span>
              <p className="muted">{p.sender} · {p.subject}{p.received_at ? ` · ${shortDate(p.received_at)}` : ""} · {sizeText(p.size)}</p>
            </div>
            <div className="mail-do">
              <button type="button" className={`btn sm${working === p.id ? " is-loading" : ""}`} disabled={busy} onClick={() => decide(p, "import")}><Icon name="download" /> Importer</button>
              <button type="button" className="btn sm quiet" disabled={busy} onClick={() => decide(p, "dismiss")}>Écarter</button>
            </div>
          </li>
        ))}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
