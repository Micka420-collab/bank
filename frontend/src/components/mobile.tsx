// Petits gestes pour le téléphone : un bouton flottant qui s'efface quand on lit (et revient dès qu'on remonte),
// une invitation à installer Bank sur l'écran d'accueil (une fois, sans insister), un bandeau « hors ligne ».

import { useEffect, useState } from "react";
import { Icon } from "./Icon";

/** Vrai quand on fait défiler vers le bas (au-delà d'un seuil) ; faux dès qu'on remonte ou qu'on est en haut. */
export function useScrollingDown(threshold = 24): boolean {
  const [down, setDown] = useState(false);
  useEffect(() => {
    let last = window.scrollY;
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(() => {
        const y = window.scrollY;
        if (y <= 8) setDown(false);
        else if (y - last > threshold) setDown(true);
        else if (last - y > threshold) setDown(false);
        if (Math.abs(y - last) > threshold) last = y;
        ticking = false;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return down;
}

export function useOnline(): boolean {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine !== false);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return online;
}

export function OfflineStrip() {
  const online = useOnline();
  if (online) return null;
  return (
    <div className="offline-strip" role="status">
      <Icon name="alert" /> Hors ligne : tes chiffres datent de la dernière ouverture, rien ne sera envoyé.
    </div>
  );
}

type InstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };
const DISMISS_KEY = "bank:install-dismissed";
const DISMISS_DAYS = 45;

function dismissed(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    return raw !== null && Date.now() - Number(raw) < DISMISS_DAYS * 86_400_000;
  } catch {
    return false;
  }
}

function media(query: string): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

function standalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true || media("(display-mode: standalone)");
}

/** Installer Bank comme une application : Android/Chrome ont un vrai bouton ; iOS Safari a besoin d'un geste
 * (Partager → Sur l'écran d'accueil), qu'on explique une fois. Jamais sur grand écran, jamais une fois installé. */
export function InstallHint() {
  const [prompt, setPrompt] = useState<InstallPromptEvent | null>(null);
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (standalone() || dismissed() || !media("(max-width: 760px)")) return undefined;
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setPrompt(e as InstallPromptEvent);
      setShow(true);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    const ios = /iPhone|iPad|iPod/i.test(navigator.userAgent) && /Safari/i.test(navigator.userAgent) && !/CriOS|FxiOS/i.test(navigator.userAgent);
    const timer = ios ? window.setTimeout(() => setShow(true), 4_000) : undefined;
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.clearTimeout(timer);
    };
  }, []);
  const close = () => {
    setShow(false);
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* stockage indisponible : on ne redemandera qu'à la prochaine session */
    }
  };
  const install = async () => {
    if (!prompt) return;
    await prompt.prompt();
    const { outcome } = await prompt.userChoice;
    if (outcome === "accepted") setShow(false);
    else close();
  };
  if (!show) return null;
  return (
    <div className="install-hint" role="dialog" aria-label="Installer Bank">
      <Icon name="download" />
      <div>
        <b>Bank sur ton écran d'accueil</b>
        <span className="faint">
          {prompt ? "Plein écran, démarrage instantané, comme une application." : "Dans Safari : Partager, puis « Sur l'écran d'accueil »."}
        </span>
      </div>
      {prompt && <button type="button" className="btn sm" onClick={() => void install()}>Installer</button>}
      <button type="button" className="btn ghost sm" onClick={close} aria-label="Plus tard"><Icon name="close" /></button>
    </div>
  );
}
