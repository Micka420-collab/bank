// Page du destinataire d'un lien de partage (#/recu/<jeton>) : sans compte, sans session. Le jeton reste dans le
// fragment de l'adresse (jamais envoyé au serveur ni dans un Referer) et n'est soumis qu'au clic : un aperçu de lien
// dans une messagerie ne consomme pas le téléchargement. On vouvoie : c'est un tiers, pas le titulaire de Bank.

import { useState } from "react";
import { Icon } from "../components/Icon";

type Phase = "ready" | "busy" | "done" | "gone" | "error";

/** Jeton d'un lien de partage dans le fragment, ou null pour toute autre adresse. */
export function sharedToken(hash: string): string | null {
  const m = /^#\/recu\/([A-Za-z0-9_-]{20,128})$/.exec(hash);
  return m ? m[1]! : null;
}

export function Received({ token }: { token: string }) {
  const [phase, setPhase] = useState<Phase>("ready");
  const [message, setMessage] = useState("");

  const download = async () => {
    setPhase("busy");
    try {
      const res = await fetch("/api/share/open", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/pdf" },
        body: JSON.stringify({ token }),
        credentials: "omit",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      });
      if (res.status === 404) return setPhase("gone");
      if (!res.ok) {
        setMessage(res.status === 429 ? "Trop de tentatives : réessayez dans quelques minutes." : "Le serveur ne répond pas : réessayez dans un instant.");
        return setPhase("error");
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "document.pdf";
      a.click();
      URL.revokeObjectURL(url);
      window.history.replaceState(null, "", "/#/recu"); // le jeton ne reste pas dans l'historique
      setPhase("done");
    } catch {
      setMessage("Le serveur ne répond pas : réessayez dans un instant.");
      setPhase("error");
    }
  };

  return (
    <main className="auth">
      <section className="auth-card" aria-labelledby="received-title">
        <div className="auth-brand"><img src="/icon.svg" alt="" width={36} height={36} /><span>Bank</span></div>
        <h1 id="received-title">{phase === "done" ? "Document téléchargé" : phase === "gone" ? "Lien plus valable" : "Un document vous a été transmis"}</h1>
        {phase === "done" ? (
          <p className="muted" role="status">Le PDF est dans vos téléchargements. Ce lien ne fonctionne plus : il ne servait qu'une fois.</p>
        ) : phase === "gone" ? (
          <p className="muted" role="status">Ce lien a déjà servi, a expiré ou a été retiré. Demandez-en un nouveau à la personne qui vous l'a envoyé.</p>
        ) : (
          <>
            <p className="muted">
              Le document se télécharge une seule fois, en PDF, marqué à votre nom. Gardez-le : ce lien ne fonctionnera
              plus ensuite.
            </p>
            <button type="button" className={`btn${phase === "busy" ? " is-loading" : ""}`} disabled={phase === "busy"} onClick={() => void download()}>
              <Icon name="download" /> Télécharger le document
            </button>
            {phase === "error" && <p className="error" role="alert">{message}</p>}
          </>
        )}
        <p className="faint auth-foot"><Icon name="lock" /> Lien personnel à usage unique, valable 48 heures au plus.</p>
      </section>
    </main>
  );
}
