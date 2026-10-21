import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Me } from "./api";
import { ErrorNote, Loading, ToastProvider } from "./components/ui";
import { Aids } from "./pages/Aids";
import { Login, Setup } from "./pages/Auth";
import { Operations } from "./pages/Operations";
import { Settings } from "./pages/Settings";
import { Today } from "./pages/Today";
import { Wealth } from "./pages/Wealth";
import { useRoute } from "./router";

const NAV: { page: string; label: string; short: string }[] = [
  { page: "aujourdhui", label: "Aujourd'hui", short: "Jour" },
  { page: "operations", label: "Opérations", short: "Opérations" },
  { page: "aides", label: "Aides", short: "Aides" },
  { page: "patrimoine", label: "Patrimoine", short: "Patrimoine" },
  { page: "reglages", label: "Réglages", short: "Réglages" },
];

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

  const page = route.page === "banques" ? "reglages" : route.page;
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
          {page === "operations" ? <Operations />
            : page === "aides" ? <Aids />
            : page === "patrimoine" ? <Wealth />
            : page === "reglages" ? (
              <Settings me={me} bankResult={route.page === "banques" ? route.params.get("result") : null}
                        onMeChange={() => void refresh()} />
            )
            : <Today totpEnabled={me.totp_enabled} />}
        </main>
      </div>
      <nav className="tabbar" aria-label="Navigation">
        {NAV.map((n) => (
          <a key={n.page} href={`#/${n.page}`} aria-current={page === n.page ? "page" : undefined}>{n.short}</a>
        ))}
      </nav>
    </ToastProvider>
  );
}
