import react from "@vitejs/plugin-react";

// Même politique que nginx en production : `npm run build && npx vite preview` permet de vérifier
// qu'aucune régression n'introduit de script ou de style inline.
const CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; " +
  "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; " +
  "form-action 'self'; object-src 'none'";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // En dev, le backend tourne sur :8000 ; les cookies restent same-origin grâce au proxy.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  preview: {
    port: 4173,
    headers: { "Content-Security-Policy": CSP },
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    sourcemap: false, // pas de code source exposé en production
    assetsInlineLimit: 0, // aucune ressource en data: URI -> CSP plus stricte possible
    target: "es2022",
  },
  test: {
    environment: "jsdom",
  },
});
