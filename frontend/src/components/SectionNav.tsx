// Sommaire d'une page longue, pensé pour le téléphone : une rangée de puces qui colle en haut de l'écran, chacune
// fait défiler jusqu'à une section (le titre visé doit porter l'id donné) et la section en cours se distingue.
// Des boutons, pas des liens : la route de Bank vit dans le fragment d'adresse, qu'un « #id » casserait.

import { useEffect, useState } from "react";

export type SectionLink = { id: string; label: string };

export function targetOf(id: string): HTMLElement | null {
  const el = document.getElementById(id);
  if (!el) return null;
  return (el.closest("section, details, .card") as HTMLElement | null) ?? el;
}

/** La section dont le haut est passé sous la barre : la dernière, dans l'ordre de la page. */
export function currentSection(ids: string[], offset = 72): string | null {
  let current: string | null = null;
  for (const id of ids) {
    const el = targetOf(id);
    if (!el) continue;
    if (el.getBoundingClientRect().top <= offset) current = id;
  }
  return current;
}

export function SectionNav({ items, label = "Sections de la page" }: { items: SectionLink[]; label?: string }) {
  const [current, setCurrent] = useState<string | null>(null);
  const [present, setPresent] = useState<string[]>([]);
  useEffect(() => {
    const ids = items.map((i) => i.id);
    let frame = 0;
    const update = () => {
      frame = 0;
      const here = ids.filter((id) => document.getElementById(id));
      setPresent((prev) => (prev.length === here.length && prev.every((v, i) => v === here[i]) ? prev : here));
      setCurrent(currentSection(here));
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    update();
    const late = window.setTimeout(update, 400); // sections qui arrivent avec leurs données
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      window.clearTimeout(late);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [items]);
  const go = (id: string) => {
    const el = targetOf(id);
    if (!el) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    const heading = document.getElementById(id);
    if (heading && !heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
  };
  useEffect(() => {
    // La puce en cours reste visible dans la rangée (qui défile de côté sur un téléphone), sans bouger la page.
    const active = document.querySelector<HTMLElement>('.secnav button[aria-current="true"]');
    active?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
  }, [current]);
  const shown = items.filter((i) => present.includes(i.id));
  if (shown.length < 3) return null;
  return (
    <nav className="secnav" aria-label={label}>
      <ul>
        {shown.map((i) => (
          <li key={i.id}>
            <button type="button" aria-current={current === i.id ? "true" : undefined} onClick={() => go(i.id)}>{i.label}</button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
