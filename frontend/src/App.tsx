import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, ApiError, useApi, type Me } from "./api";
import { Assistant } from "./components/Assistant";
import { Icon, type IconName } from "./components/Icon";
import { Onboarding, onboardingWanted } from "./components/Onboarding";
import { ErrorNote, Loading, ToastProvider } from "./components/ui";
import { Login } from "./pages/Auth";
import { Operations } from "./pages/Operations";
import { Settings } from "./pages/Settings";
import { Today } from "./pages/Today";
import { useRoute } from "./router";

// Pages moins fréquentes : chargées à la première visite (l'écran du jour s'ouvre plus vite sur téléphone).
const Aids = lazy(() => import("./pages/Aids").then((m) => ({ default: m.Aids })));
const Connecteurs = lazy(() => import("./pages/Connecteurs").then((m) => ({ default: m.Connecteurs })));
const Documents = lazy(() => import("./pages/Documents").then((m) => ({ default: m.Documents })));
const Memory = lazy(() => import("./pages/Memory").then((m) => ({ default: m.Memory })));
const Opportunities = lazy(() => import("./pages/Opportunities").then((m) => ({ default: m.Opportunities })));
const Procedures = lazy(() => import("./pages/Procedures").then((m) => ({ default: m.Procedures })));
const Situation = lazy(() => import("./pages/Situation").then((m) => ({ default: m.Situation })));
const Watch = lazy(() => import("./pages/Watch").then((m) => ({ default: m.Watch })));
const Wealth = lazy(() => import("./pages/Wealth").then((m) => ({ default: m.Wealth })));

const NAV: { page: string; label: string; icon: IconName }[] = [
  { page: "aujourdhui", label: "Aujourd'hui", icon: "today" },
  { page: "operations", label: "Opérations", icon: "operations" },
  { page: "opportunites", label: "Opportunités", icon: "target" },
  { page: "demarches", label: "Démarches", icon: "route" },
  { page: "documents", label: "Documents", icon: "vault" },
  { page: "aides", label: "Aides", icon: "aids" },
  { page: "patrimoine", label: "Patrimoine", icon: "wealth" },
  { page: "situation", label: "Ma situation", icon: "situation" },
  { page: "veille", label: "Veille", icon: "news" },
  { page: "connecteurs", label: "Connecteurs", icon: "connectors" },
  { page: "reglages", label: "Réglages", icon: "settings" },
];
// Sur mobile, cinq onglets lisibles ; le reste derrière « Plus ».
const TABS: { page: string; short: string; icon: IconName }[] = [
  { page: "aujourdhui", short: "Jour", icon: "today" },
  { page: "operations", short: "Opérations", icon: "operations" },
  { page: "opportunites", short: "Pistes", icon: "target" },
  { page: "patrimoine", short: "Patrimoine", icon: "wealth" },
  { page: "plus", short: "Plus", icon: "more" },
];
const MORE = new Set(["aides", "documents", "demarches", "veille", "situation", "connecteurs", "reglages", "memoire", "plus"]);
type Badges = { procedures_due: number; procedures_ready: number; confirmations: number; watch_new_relevant: number };

type Phase = "loading" | "setup" | "login" | "ready" | "offline";

export function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const route = useRoute();

  const refresh = useCallback(async () => {
    try {
      const { needs_setup } = await api<{ needs_setup: boolean }>("/api/setup/status");
      if (needs_setup) return setPhase("setup");
      setMe(await api<Me>("/api/auth/me"));
      setPhase("ready");
    } catch (e) {
      setPhase(e instanceof ApiError && e.status === 401 ? "login" : "offline");
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
        <ErrorNote message="Le serveur Bank ne répond pas. Vérifie qu'il est démarré, puis réessaie." onRetry={() => void refresh()} />
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
                    onDone={() => { window.location.hash = "#/aujourdhui"; }} />
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
          <Suspense fallback={<Loading />}>
          {page === "operations" ? <Operations openImport={route.params.get("import") === "1"} />
            : page === "aides" ? <Aids />
            : page === "patrimoine" ? <Wealth />
            : page === "situation" ? <Situation />
            : page === "connecteurs" ? <Connecteurs bankResult={route.page === "banques" ? route.params.get("result") : null} />
            : page === "plus" ? <More username={me.username} onLogout={() => void logout()} />
            : page === "reglages" ? <Settings me={me} onMeChange={() => void refresh()} />
            : page === "memoire" ? <Memory />
            : page === "documents" ? <Documents docId={route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : null} />
            : page === "opportunites" ? <Opportunities />
            : page === "demarches" ? <Procedures id={route.rest[0] && /^\d+$/.test(route.rest[0]) ? Number(route.rest[0]) : null} wantNew={route.params.get("nouvelle") === "1"} />
            : page === "veille" ? <Watch />
            : <Today />}
          </Suspense>
        </main>
      </div>
      <button type="button" className="fab" onClick={() => setAskOpen(true)} aria-label="Demande à Bank"><Icon name="spark" /></button>
      <Assistant open={askOpen} onClose={() => setAskOpen(false)} />
      <nav className="tabbar" aria-label="Navigation">
        {TABS.map((n) => (
          <a key={n.page} href={`#/${n.page}`}
             aria-current={page === n.page || (n.page === "plus" && MORE.has(page)) ? "page" : undefined}>
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
  const b = counts.data;
  const badges: Record<string, { n: number; label: string }> = {
    situation: { n: summary.data?.pending_proposals ?? 0, label: "en attente" },
    opportunites: { n: b?.confirmations ?? 0, label: "gain à confirmer" },
    demarches: { n: (b?.procedures_due ?? 0) + (b?.procedures_ready ?? 0), label: "à traiter" },
    veille: { n: b?.watch_new_relevant ?? 0, label: "nouveautés pour toi" },
  };
  return (
    <nav className="rail" aria-label="Navigation principale">
      <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
      {NAV.map((n) => {
        const badge = badges[n.page];
        return (
          <a key={n.page} className="nav-link" href={`#/${n.page}`} aria-current={page === n.page ? "page" : undefined}>
            <Icon name={n.icon} />
            {n.label}
            {badge && badge.n > 0 ? <span className="nav-badge" aria-label={`${badge.n} ${badge.label}`}>{badge.n}</span> : null}
          </a>
        );
      })}
      <button type="button" className="btn quiet rail-ask" onClick={onAsk}><Icon name="spark" /> Demande à Bank</button>
      <div className="rail-foot">
        <p>{username}</p>
        <button className="btn link" onClick={onLogout}>Se déconnecter</button>
      </div>
    </nav>
  );
}

function More({ username, onLogout }: { username: string; onLogout: () => void }) {
  return (
    <>
      <div className="page-head"><h1>Plus</h1></div>
      <ul className="more">
        <li><a href="#/demarches"><b><Icon name="route" /> Démarches</b><span className="faint">Aides à demander, frais à contester, abonnements à résilier : préparés, suivis</span></a></li>
        <li><a href="#/documents"><b><Icon name="vault" /> Documents</b><span className="faint">Ton coffre-fort : bulletins, avis d'impôt, attestations, RIB</span></a></li>
        <li><a href="#/situation"><b><Icon name="situation" /> Ma situation</b><span className="faint">Ton profil, tes projets, ce que ton agent peut faire</span></a></li>
        <li><a href="#/aides"><b><Icon name="aids" /> Aides</b><span className="faint">CAF, France Travail, simulation de tes droits</span></a></li>
        <li><a href="#/veille"><b><Icon name="news" /> Veille</b><span className="faint">Ce qui change dans les sources officielles, pour toi</span></a></li>
        <li><a href="#/connecteurs"><b><Icon name="connectors" /> Connecteurs</b><span className="faint">Banques, planning de travail, Linky, documents de l'État, agents IA</span></a></li>
        <li><a href="#/memoire"><b><Icon name="memory" /> Mémoire de l'assistant</b><span className="faint">Ce qu'il retient de toi, vos conversations</span></a></li>
        <li><a href="#/reglages"><b><Icon name="settings" /> Réglages</b><span className="faint">Comptes, budget, confidentialité, sécurité, journal</span></a></li>
      </ul>
      <p className="muted" style={{ marginTop: "2rem" }}>{username} · <button className="btn link" onClick={onLogout}>Se déconnecter</button></p>
    </>
  );
}
