// Un nombre qui se compte jusqu'à sa valeur : une fois, à l'arrivée, et pas du tout si l'utilisateur a demandé
// moins d'animations. La valeur finale est toujours dans le DOM (lecteurs d'écran, tests).

import { useEffect, useState } from "react";

const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function CountUp({ value, duration = 900, format }: { value: number; duration?: number; format?: (n: number) => string }) {
  const [shown, setShown] = useState(() => (reduced() ? value : 0));
  useEffect(() => {
    if (reduced()) {
      setShown(value);
      return undefined;
    }
    const start = performance.now();
    const from = 0;
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(from + (value - from) * eased));
      if (t < 1) frame = window.requestAnimationFrame(step);
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
  }, [value, duration]);
  return <span aria-label={format ? format(value) : String(value)}>{format ? format(shown) : shown.toLocaleString("fr-FR")}</span>;
}
