import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Me } from "./api";
import { ErrorNote, Loading, ToastProvider } from "./components/ui";
import { Aids } from "./pages/Aids";
import { Connecteurs } from "./pages/Connecteurs";
import { Login, Setup } from "./pages/Auth";
import { Operations } from "./pages/Operations";
import { Settings } from "./pages/Settings";
import { Situation } from "./pages/Situation";
import { Today } from "./pages/Today";
import { Wealth } from "./pages/Wealth";
import { useRoute } from "./router";

const NAV: { page: string; label: string }[] = [
  { page: "aujourdhui", label: "Aujourd'hui" },
  { page: "operations", label: "Opérations" },
  { page: "aides", label: "Aides" },
  { page: "patrimoine", label: "Patrimoine" },
  { page: "situation", label: "Ma situation" },
  { page: "connecteurs", label: "Connecteurs" },
  { page: "reglages", label: "Réglages" },
];
// Sur mobile, cinq onglets lisibles ; le reste derrière « Plus ».
const TABS: { page: string; short: string }[] = [
  { page: "aujourdhui", short: "Jour" },
  { page: "operations", short: "Opérations" },
  { page: "situation", short: "Situation" },
  { page: "patrimoine", short: "Patrimoine" },
  { page: "plus", short: "Plus" },
];
const MORE = new Set(["aides", "connecteurs", "reglages", "plus"]);

type Phase = "loading" | "setup" | "login" | "ready" | "offline";

export function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [me, setMe] = useState<Me | null>(null);
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
        <ErrorNote message="Le serveur Bank ne répond pas." onRetry={() => void refresh()} />
      </main>
    );
  }
  if (phase === "setup") return <Setup onDone={() => setPhase("login")} />;
  if (phase === "login" || !me) return <Login onDone={() => void refresh()} />;

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
        <nav className="rail" aria-label="Navigation principale">
          <div className="brand"><img src="/icon.svg" alt="" />Bank</div>
          {NAV.map((n) => (
            <a key={n.page} className="nav-link" href={`#/${n.page}`} aria-current={page === n.page ? "page" : undefined}>
              {n.label}
            </a>
          ))}
          <div className="rail-foot">
            <p>{me.username}</p>
            <button className="btn link" onClick={() => void logout()}>Se déconnecter</button>
          </div>
        </nav>
        <main className="sheet" id="contenu" tabIndex={-1}>
          {page === "operations" ? <Operations openImport={route.params.get("import") === "1"} />
            : page === "aides" ? <Aids />
            : page === "patrimoine" ? <Wealth />
            : page === "situation" ? <Situation />
            : page === "connecteurs" ? <Connecteurs bankResult={route.page === "banques" ? route.params.get("result") : null} />
            : page === "plus" ? <More username={me.username} onLogout={() => void logout()} />
            : page === "reglages" ? <Settings me={me} onMeChange={() => void refresh()} />
            : <Today />}
        </main>
      </div>
      <nav className="tabbar" aria-label="Navigation">
        {TABS.map((n) => (
          <a key={n.page} href={`#/${n.page}`}
             aria-current={page === n.page || (n.page === "plus" && MORE.has(page)) ? "page" : undefined}>
            {n.short}
          </a>
        ))}
      </nav>
    </ToastProvider>
  );
}

function More({ username, onLogout }: { username: string; onLogout: () => void }) {
  return (
    <>
      <div className="page-head"><h1>Plus</h1></div>
      <ul className="more">
        <li><a href="#/aides"><b>Aides</b><span className="faint">CAF, France Travail, simulation de tes droits</span></a></li>
        <li><a href="#/connecteurs"><b>Connecteurs</b><span className="faint">Banques, planning de travail, Linky, documents de l'État, agents IA</span></a></li>
        <li><a href="#/reglages"><b>Réglages</b><span className="faint">Comptes, budget, sécurité, journal</span></a></li>
      </ul>
      <p className="muted" style={{ marginTop: "2rem" }}>{username} · <button className="btn link" onClick={onLogout}>Se déconnecter</button></p>
    </>
  );
}
