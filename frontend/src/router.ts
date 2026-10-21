// Routage par fragment (#/operations) : aucune réécriture côté serveur, et le retour de la banque
// (/#/banques?result=connected) arrive directement sur la bonne vue.

import { useEffect, useState } from "react";

export type Route = { page: string; params: URLSearchParams; rest: string[] };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path = "", query = ""] = raw.split("?", 2);
  const [page = "", ...rest] = path.split("/");
  return { page: page || "aujourdhui", params: new URLSearchParams(query), rest: rest.filter(Boolean) };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(page: string): void {
  window.location.hash = `#/${page}`;
}
