// WhatsApp par QR code, sur ton numéro : la passerelle tourne à côté de Bank, tu scannes le QR depuis WhatsApp ›
// Appareils connectés, puis tu écris dans ta conversation « Moi ». Afficher le QR et délier demandent ton mot de
// passe ; le QR arrive en image (data URI) : le navigateur ne connaît jamais l'adresse de la passerelle.

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type WhatsAppQrCode, type WhatsAppQrStatus } from "../api";
import { Icon } from "./Icon";
import { useAsyncAction, useCelebrate, useToast } from "./ui";

const STATUS_EVERY = 3000;

export function WhatsAppQrRisk() {
  return (
    <p className="notice warn wa-qr-risk">
      <Icon name="alert" />
      <span>
        Ton choix, en connaissance de cause : une passerelle non officielle (WhatsApp peut suspendre le compte) qui
        voit toutes tes conversations. Bank ne lit que ta conversation « Moi » et ignore tout le reste, sans rien garder.
      </span>
    </p>
  );
}

export function WhatsAppQr({ onLinked }: { onLinked: () => void }) {
  const [st, setSt] = useState<WhatsAppQrStatus | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, error, run] = useAsyncAction();
  const toast = useToast();
  const celebrate = useCelebrate();
  const wasLinked = useRef<boolean | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSt(await api<WhatsAppQrStatus>("/api/channels/whatsapp_qr/status"));
    } catch {
      /* passerelle ou réseau momentanément absents : on réessaie au prochain tour */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Tant que ce n'est pas relié, on relit l'état (le scan se fait sur ton téléphone, pas ici).
  const settled = st !== null && st.paired && st.linked;
  useEffect(() => {
    if (settled) return;
    const t = window.setInterval(() => void refresh(), STATUS_EVERY);
    return () => window.clearInterval(t);
  }, [settled, refresh]);

  // Le QR vit 30 s chez la passerelle : Bank le renouvelle, on le relit au rythme qu'elle indique.
  const showQr = st !== null && st.window && !st.paired;
  useEffect(() => {
    if (!showQr) {
      setQr(null);
      return;
    }
    let stop = false;
    let timer = 0;
    const load = async () => {
      let next = 20;
      try {
        const r = await api<WhatsAppQrCode>("/api/channels/whatsapp_qr/qr");
        if (stop) return;
        setQr(r.qr);
        next = r.refresh_in ?? next;
      } catch {
        if (!stop) setQr(null);
      }
      if (!stop) timer = window.setTimeout(() => void load(), next * 1000);
    };
    void load();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [showQr]);

  useEffect(() => {
    if (st === null) return;
    const linked = st.paired && st.linked;
    if (wasLinked.current === false && linked) {
      celebrate("done", "C'est relié ! Écris-toi un message dans ta conversation « Moi » : Bank répond là.");
      onLinked();
    }
    wasLinked.current = linked;
  }, [st, celebrate, onLinked]);

  const openWindow = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api("/api/channels/whatsapp_qr/pairing", { method: "POST", body: { password } });
      setPassword("");
      await refresh();
    });
  };
  const unlink = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      try {
        await api("/api/channels/whatsapp_qr/logout", { method: "POST", body: { password } });
      } finally {
        setPassword("");
        await refresh();
        onLinked();
      }
      toast("WhatsApp délié : Bank ne lit plus rien.");
    });
  };

  if (st === null) return <p className="faint">Lecture de la passerelle…</p>;
  const passwordField = (
    <input className="input" type="password" autoComplete="current-password" aria-label="Mot de passe" placeholder="Ton mot de passe"
           value={password} onChange={(e) => setPassword(e.target.value)} required />
  );
  // Ton compte relié reste supprimable quoi qu'il arrive (passerelle injoignable, téléphone délié de son côté) :
  // sinon les alertes continueraient de viser un WhatsApp qui ne répond plus.
  const unlinkForm = (children: ReactNode, className = "ch-pair") => (
    <form className={className} onSubmit={unlink} aria-label="Délier WhatsApp">
      {children}
      <div className="ch-pair-row">
        {passwordField}
        <button className={`btn ghost${busy ? " is-loading" : ""}`} disabled={busy}>Délier</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );

  if (!st.bridge) {
    const unreachable = (
      <>
        <h3><Icon name="alert" /> Passerelle injoignable</h3>
        <p className="faint">{st.error ?? "Vérifie qu'elle tourne et son adresse."}</p>
      </>
    );
    if (st.linked) {
      return unlinkForm(
        <>
          {unreachable}
          <p className="faint">Tu peux quand même délier : Bank ne t'écrira plus. Retire alors aussi « Bank » dans WhatsApp › Appareils connectés.</p>
        </>,
      );
    }
    return (
      <div className="ch-pair">
        {unreachable}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    );
  }

  if (st.paired && st.linked) {
    return unlinkForm(
      <>
        <h3><Icon name="check" /> Relié : {st.phone ?? "ton numéro"}</h3>
        <p>Écris-toi un message dans ta conversation « Moi » : Bank répond là. Tes autres conversations restent invisibles pour lui.</p>
        <p className="faint">Délier déconnecte Bank de ton WhatsApp (il disparaît de tes appareils connectés).</p>
      </>,
      "ch-pair wa-qr-done",
    );
  }
  if (st.linked && !showQr) {
    // Délié depuis WhatsApp › Appareils connectés : rescanner (même mot de passe) ou délier aussi côté Bank.
    const submit = (e: FormEvent<HTMLFormElement>) => {
      const by = (e.nativeEvent as SubmitEvent).submitter?.getAttribute("name");
      if (by === "unlink") unlink(e);
      else openWindow(e); // Entrée : le premier bouton, rescanner
    };
    return (
      <form className="ch-pair" onSubmit={submit} aria-label="WhatsApp délié depuis ton téléphone">
        <h3><Icon name="alert" /> WhatsApp a été délié depuis ton téléphone</h3>
        <p className="faint">Rescanne le QR code pour reprendre, ou délie ici : Bank ne t'écrira plus.</p>
        <div className="ch-pair-row">
          {passwordField}
          <button name="rescan" className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>Rescanner le QR code</button>
          <button name="unlink" className="btn ghost" disabled={busy}>Délier</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    );
  }
  if (showQr) {
    return (
      <div className="ch-pair is-live wa-qr" role="status" aria-live="polite">
        <h3><Icon name="send" /> Scanne ce QR code</h3>
        <p>Sur ton téléphone : WhatsApp › Appareils connectés › Connecter un appareil.</p>
        <div className="wa-qr-frame">
          {qr ? <img src={qr} alt="QR code à scanner avec WhatsApp" width={256} height={256} /> : <span className="faint">QR code en préparation…</span>}
        </div>
        <p className="ch-wait"><span className="ch-pulse" aria-hidden="true" />En attente du scan… le QR se renouvelle tout seul.</p>
      </div>
    );
  }
  return (
    <form className="ch-pair" onSubmit={openWindow} aria-label="Afficher le QR code WhatsApp">
      <h3><Icon name="send" /> {st.paired ? "Relier ce téléphone à Bank" : "Relier ton WhatsApp"}</h3>
      <p className="faint">
        {st.paired
          ? `La passerelle est reliée à ${st.phone ?? "un numéro"}, mais pas encore à Bank : confirme ton mot de passe.`
          : "Confirme ton mot de passe : le QR code s'affiche ici pendant 10 minutes."}
      </p>
      <div className="ch-pair-row">
        {passwordField}
        <button className={`btn${busy ? " is-loading" : ""}`} disabled={busy}>{st.paired ? "Relier" : "Afficher le QR code"}</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
