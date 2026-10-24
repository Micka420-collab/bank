import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Received, sharedToken } from "./pages/Received";
import { initTheme } from "./theme";
import "./styles.css";

initTheme();
// Lien de partage reçu par un tiers : sa propre page, sans session ni appel à l'application.
const shared = sharedToken(window.location.hash);
// Ni manifeste ni service worker chez un tiers : il ne garde pas la coquille de Bank en cache.
if (shared) document.querySelector('link[rel="manifest"]')?.remove();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {shared ? <Received token={shared} /> : <App />}
  </StrictMode>,
);

if (import.meta.env.PROD && !shared && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" });
  });
}
