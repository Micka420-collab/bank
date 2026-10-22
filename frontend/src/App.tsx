import { lazy, Suspense, useCallback, useEffect, useReducer, useState } from "react";
import { api, ApiError, useApi, type Me } from "./api";
import { Assistant } from "./components/Assistant";
import { Icon, type IconName } from "./components/Icon";
import { InstallHint, OfflineStrip, useScrollingDown } from "./components/mobile";
import { Onboarding, onboardingWanted } from "./components/Onboarding";
import { ErrorNote, Loading, ToastProvider } from "./components/ui";
import { Login } from "./pages/Auth";
import { Operations } from "./pages/Operations";
import { Settings } from "./pages/Settings";
import { Today } from "./pages/Today";
import { useRoute } from "./router";

// Pages moins fréquentes : chargées à la première visite (l'écran du jour s'ouvre plus vite sur téléphone).
const Aids = lazy(() => import("./pages/Aids").then((m) => ({ default: m.Aids })));
const Crypto = lazy(() => import("./pages/Crypto").then((m) => ({ default: m.Crypto })));
const Pilot = lazy(() => import("./pages/Pilot").then((m) => ({ default: m.Pilot })));
const Connecteurs = lazy(() => import("./pages/Connecteurs").then((m) => ({ default: m.Connecteurs })));
const Documents = lazy(() => import("./pages/Documents").then((m) => ({ default: m.Documents })));
const Memory = lazy(() => import("./pages/Memory").then((m) => ({ default: m.Memory })));
const Messageries = lazy(() => import("./pages/Messageries").then((m) => ({ default: m.Messageries })));
const Opportunities = lazy(() => import("./pages/Opportunities").then((m) => ({ default: m.Opportunities })));
const Procedures = lazy(() => import("./pages/Procedures").then((m) => ({ default: m.Procedures })));
const Situation = lazy(() => import("./pages/Situation").then((m) => ({ default: m.Situation })));
const Watch = lazy(() => import("./pages/Watch").then((m) => ({ default: m.Watch })));
const Wealth = lazy(() => import("./pages/Wealth").then((m) => ({ default: m.Wealth })));

// Cinq destinations, pas une de plus : le reste est rangé dans « Plus » (ou en bas du rail sur grand écran).
// « Gagner » réunit missions et aides ; « Papiers » réunit documents et démarches ; « Épargne » réunit le
// patrimoine, la crypto et le pilote automatique.
const HUBS: Record<string, string> = {
  opportunites: "gagner",
  aides: "gagner",
  documents: "papiers",
  demarches: "papiers",
  crypto: "patrimoine",
  pilote: "patrimoine",
};
const NAV: { page: string; hub: string; label: string; icon: IconName }[] = [
  { page: "aujourdhui", hub: "aujourdhui", label: "Aujourd'hui", icon: "today" },
  { page: "operations", hub: "operations", label: "Opérations", icon: "operations" },
  { page: "opportunites", hub: "gagner", label: "Gagner", icon: "coin" },
  { page: "documents", hub: "papiers", label: "Papiers", icon: "folder" },
  { page: "patrimoine", hub: "patrimoine", label: "Épargne", icon: "wealth" },
];
const NAV_MORE: { page: string; label: string; icon: IconName }[] = [
  { page: "situation", label: "Ma situation", icon: "situation" },
  { page: "veille", label: "Veille", icon: "news" },
  { page: "messageries", label: "Messageries", icon: "chat" },
  { page: "connecteurs", label: "Connecteurs", icon: "connectors" },
  { page: "reglages", label: "Réglages", icon: "settings" },
];
// Sur téléphone : le jour, l'argent qui passe, ce qu'on peut gagner, les papiers (l'appareil photo), et « Plus ».
const TABS: { page: string; hub: string; short: string; icon: IconName }[] = [
  { page: "aujourdhui", hub: "aujourdhui", short: "Jour", icon: "today" },
  { page: "operations", hub: "operations", short: "Opérations", icon: "operations" },
  { page: "opportunites", hub: "gagner", short: "Gagner", icon: "coin" },
  { page: "documents", hub: "papiers", short: "Papiers", icon: "folder" },
  { page: "plus", hub: "plus", short: "Plus", icon: "more" },
];
const MORE = new Set(["patrimoine", "crypto", "pilote", "veille", "situation", "messageries", "connecteurs", "reglages", "memoire", "plus"]);
type Badges = { procedures_due: number; procedures_ready: number; confirmations: number; watch_new_relevant: number };

export function hubOf(page: string): string {
  return HUBS[page] ?? page;
}

type Phase = "loading" | "setup" | "login" | "ready" | "offline";
const OFFLINE = "Le serveur Bank ne répond pas. Vérifie qu'il est démarré, puis réessaie.";

export function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [offline, setOffline] = useState(OFFLINE);
  const [me, setMe] = useState<Me | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  // Fin de l'accueil déjà sur #/aujourdhui : le hash ne change pas, aucun « hashchange » — on redessine nous-mêmes.
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  const route = useRoute();
  const reading = useScrollingDown(); // on lit vers le bas : le bouton flottant s'efface, les chiffres restent visibles

  const refresh = useCallback(async () => {
    try {
      const { needs_setup } = await api<{ needs_setup: boolean }>("/api/setup/status");
      if (needs_setup) return setPhase("setup");
      setMe(await api<Me>("/api/auth/me"));
      setPhase("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return setPhase("login");
      // Le serveur a répondu « trop de demandes » : il tourne, il faut juste patienter — ne pas le dire en panne.
      setOffline(e instanceof ApiError && e.status === 429 ? e.message : OFFLINE);
      setPhase("offline");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onUnauthorized = () => {
      setMe(null);
      setPhase("login");
    };
    window.addEventListener("bank:unauthorized", onUnauthorized);
    return () => window.removeEventListener("bank:unauthorized", onUnauthorized);
  }, [refresh]);

  if (phase === "loading") return <main className="auth"><Loading what="Ouverture" /></main>;
  if (phase === "offline") {
    return (
      <main className="auth">
        <ErrorNote message={offline} onRetry={() => void refresh()} />
      </main>
    );
  }
  if (phase === "setup") return <Onboarding stage="intro" onAccess={() => void refresh()} />;
  if (phase === "login" || !me) return <Login onDone={() => void refresh()} />;

  // L'onboarding continue après la création de l'accès (et au retour de la banque) tant qu'il n'est pas terminé.
  if (route.page === "bienvenue" || (onboardingWanted() && (route.page === "banques" || route.page === "aujourdhui" || route.page === ""))) {
    return (
      <ToastProvider>
        <Onboarding stage="bank" bankResult={route.page === "banques" ? route.params.get("result") : null}
                    onDone={() => { window.location.hash = "#/aujourdhui"; redraw(); }} />
      </ToastProvider>
    );
  }

  const page = route.page === "banques" ? "connecteurs" : route.page;
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    setMe(null);
    setPhase("login");
  };

  return (
    <ToastProvider>
      <a className="sr-only" href="#contenu">Aller au contenu</a>
      <div className="shell">
        <Rail page={page} username={me.username} onLogout={() => void logout()} onAsk={() => setAskOpen(true)} />
        <main className="sheet" id="contenu" tabIndex={-1} key={page}>
          <HubTabs page={page} />
          <Suspense fallback={<Loading />}>
          {page === "operations" ? <Operations openImport={route.params.get("import") === "1"} />
            : page === "aides" ? <Aids />
            : page === "patrimoine" ? <Wealth />
            : page === "crypto" ? <Crypto />
            : page === "pilote" ? <Pilot />
            : page === "situation" ? <Situation />
            : page === "connecteurs" ? <Connecteurs bankResult={route.page === "banques" ? route.params.get("result") : null} />
            : page === "plus" ? <More username={me.username} onLogout={() => void logout()} />
            : page === "reglages" ? <Settings me={me} onMeChange={() => void refresh()} />
            : page === "memoire" ? <Memory />
            : page === "documents" ? <Documents docId={route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : null} />
            : page === "opportunites" ? <Opportunities />
            : page === "demarches" ? <Procedures id={route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : null} wantNew={route.params.get("nouvelle") === "1"} />
            : page === "veille" ? <Watch />
            : page === "messageries" ? <Messageries />
            : <Today />}
          </Suspense>
        </main>
      </div>
      <OfflineStrip />
      <InstallHint />
      <button type="button" className={`fab${reading ? " is-hidden" : ""}`} onClick={() => setAskOpen(true)} aria-label="Demande à Bank"><Icon name="spark" /></button>
      <Assistant open={askOpen} onClose={() => setAskOpen(false)} />
      <nav className="tabbar" aria-label="Navigation">
        {TABS.map((n) => (
          <a key={n.page} href={`#/${n.page}`}
             aria-current={hubOf(page) === n.hub || (n.page === "plus" && MORE.has(page)) ? "page" : undefined}>
            <Icon name={n.icon} />
            {n.short}
          </a>
        ))}
      </nav>
    </ToastProvider>
  );
}

function Rail({ page, username, onLogout, onAsk }: { page: string; username: string; onLogout: () => void; onAsk: () => void }) {
  const summary = useApi<{ pending_proposals: number }>("/api/agents/summary");
  // Pastilles relues à chaque changement de page (appel léger : sans recalcul des pistes).
  const counts = useApi<Badges>(`/api/copilot/badges?p=${encodeURIComponent(page)}`);
  const reloadCounts = counts.reload;
  useEffect(() => {
    let timer: number | undefined;
    const onChange = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(reloadCounts, 400); // plusieurs écritures d'affilée : une seule relecture
    };
    window.addEventListener("bank:changed", onChange);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("bank:changed", onChange);
    };
  }, [reloadCounts]);
  const b = counts.data;
  const badges: Record<string, { n: number; label: string }> = {
    gagner: { n: b?.confirmations ?? 0, label: "gain à confirmer" },
    papiers: { n: (b?.procedures_due ?? 0) + (b?.procedures_ready ?? 0), label: "démarche à traiter" },
    situation: { n: summary.data?.pending_proposals ?? 0, label: "en attente" },
    veille: { n: b?.watch_new_relevant ?? 0, label: "nouveautés pour toi" },
  };
  const hub = hubOf(page);
  const link = (n: { page: string; label: string; icon: IconName }, key: string, current: boolean, small = false) => {
    const badge = badges[key];
    return (
      <a key={n.page} className={`nav-link${small ? " is-small" : ""}`} href={`#/${n.page}`} aria-current={current ? "page" : undefined}>
        <Icon name={n.icon} />
        {n.label}
        {badge && badge.n > 0 ? <span className="nav-badge" aria-label={`${badge.n} ${badge.label}`}>{badge.n}</span> : null}
      </a>
    );
  };
  return (
    <nav className="rail" aria-label="Navigation principale">
      <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
      {NAV.map((n) => link(n, n.hub, hub === n.hub))}
      <div className="rail-more" role="group" aria-label="Autres pages">
        {NAV_MORE.map((n) => link(n, n.page, page === n.page, true))}
      </div>
      <button type="button" className="btn quiet rail-ask" onClick={onAsk}><Icon name="spark" /> Demande à Bank</button>
      <div className="rail-foot">
        <p>{username}</p>
        <button className="btn link" onClick={onLogout}>Se déconnecter</button>
      </div>
    </nav>
  );
}

/** Les deux pages d'un pôle, comme deux onglets : « Gagner » (missions, aides), « Papiers » (documents, démarches). */
export function HubTabs({ page }: { page: string }) {
  const hub = hubOf(page);
  const tabs =
    hub === "gagner"
      ? [{ page: "opportunites", label: "Missions" }, { page: "aides", label: "Aides" }]
      : hub === "papiers"
        ? [{ page: "documents", label: "Documents" }, { page: "demarches", label: "Démarches" }]
        : hub === "patrimoine"
          ? [{ page: "patrimoine", label: "Épargne" }, { page: "crypto", label: "Crypto" }, { page: "pilote", label: "Pilote" }]
          : [];
  if (tabs.length === 0) return null;
  const names: Record<string, string> = { gagner: "Gagner", papiers: "Papiers", patrimoine: "Épargne" };
  return (
    <nav className="hub-tabs" aria-label={names[hub]}>
      {tabs.map((t) => (
        <a key={t.page} href={`#/${t.page}`} aria-current={page === t.page ? "page" : undefined}>{t.label}</a>
      ))}
    </nav>
  );
}

function More({ username, onLogout }: { username: string; onLogout: () => void }) {
  return (
    <>
      <div className="page-head"><h1>Plus</h1></div>
      <ul className="more">
        <li><a href="#/patrimoine"><b><Icon name="wealth" /> Épargne</b><span className="faint">Ton patrimoine, ton matelas, où placer</span></a></li>
        <li><a href="#/crypto"><b><Icon name="crypto" /> Crypto</b><span className="faint">Tes adresses suivies, leur valeur, l'impôt, les arnaques à éviter</span></a></li>
        <li><a href="#/pilote"><b><Icon name="robot" /> Pilote automatique</b><span className="faint">Ton agent agit seul, dans ton cadre — et tu coupes d'un geste</span></a></li>
        <li><a href="#/situation"><b><Icon name="situation" /> Ma situation</b><span className="faint">Ton profil, tes projets, ce que ton agent peut faire</span></a></li>
        <li><a href="#/veille"><b><Icon name="news" /> Veille</b><span className="faint">Ce qui change dans les sources officielles, pour toi</span></a></li>
        <li><a href="#/messageries"><b><Icon name="chat" /> Messageries</b><span className="faint">Écris à Bank depuis WhatsApp, Telegram, Signal… et reçois ses alertes</span></a></li>
        <li><a href="#/connecteurs"><b><Icon name="connectors" /> Connecteurs</b><span className="faint">Banques, planning de travail, Linky, documents de l'État, agents IA</span></a></li>
        <li><a href="#/memoire"><b><Icon name="memory" /> Mémoire de l'assistant</b><span className="faint">Ce qu'il retient de toi, vos conversations</span></a></li>
        <li><a href="#/reglages"><b><Icon name="settings" /> Réglages</b><span className="faint">Comptes, budget, confidentialité, sécurité, journal</span></a></li>
      </ul>
      <p className="muted" style={{ marginTop: "2rem" }}>{username} · <button className="btn link" onClick={onLogout}>Se déconnecter</button></p>
    </>
  );
}
