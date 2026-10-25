// Sommaire d'une page longue : n'apparaît qu'avec au moins trois sections présentes, chaque puce fait défiler jusqu'à
// sa section et lui donne le focus, la section passée sous la barre devient « en cours ».

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentSection, SectionNav } from "./components/SectionNav";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLDivElement;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 450)); });

function page(ids: string[], tops: Record<string, number>) {
  for (const id of ids) {
    const section = document.createElement("section");
    const h2 = document.createElement("h2");
    h2.id = id;
    h2.textContent = id;
    section.appendChild(h2);
    section.getBoundingClientRect = () => ({ top: tops[id] ?? 1000, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
    section.scrollIntoView = vi.fn();
    document.body.appendChild(section);
  }
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

describe("sommaire de page", () => {
  it("lists the sections that exist and highlights the one under the bar", async () => {
    page(["a", "b", "c"], { a: -500, b: 20, c: 900 });
    const items = [{ id: "a", label: "Alpha" }, { id: "b", label: "Bravo" }, { id: "c", label: "Charlie" }, { id: "zz", label: "Absente" }];
    await act(async () => root.render(<SectionNav items={items} />));
    await flush();
    const buttons = [...host.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["Alpha", "Bravo", "Charlie"]);
    expect(buttons.find((b) => b.getAttribute("aria-current") === "true")?.textContent).toBe("Bravo");
    expect(currentSection(["a", "b", "c"])).toBe("b");
    await act(async () => { buttons[2]!.click(); });
    const charlie = document.getElementById("c")!;
    expect((charlie.closest("section") as HTMLElement & { scrollIntoView: ReturnType<typeof vi.fn> }).scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(charlie);
    expect(charlie.getAttribute("tabindex")).toBe("-1");
  });

  it("keeps the current chip visible by scrolling the row only, never the page", async () => {
    // scrollIntoView sur la barre collante ramenait la page en haut à chaque changement de section (défilement bloqué).
    const intoView = vi.fn();
    Element.prototype.scrollIntoView = intoView; // absent de jsdom
    page(["a", "b", "c"], { a: -500, b: 20, c: 900 });
    await act(async () => root.render(<SectionNav items={[{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }]} />));
    await flush();
    expect(host.querySelector('button[aria-current="true"]')?.textContent).toBe("B");
    expect(intoView.mock.contexts.filter((el) => (el as Element).closest(".secnav"))).toHaveLength(0);
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  it("stays hidden with fewer than three sections", async () => {
    page(["a", "b"], {});
    await act(async () => root.render(<SectionNav items={[{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }]} />));
    await flush();
    expect(host.querySelector(".secnav")).toBeNull();
  });
});
