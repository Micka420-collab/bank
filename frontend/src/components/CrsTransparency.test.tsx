import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CrsTransparency, CRS_SOURCES, explainCRS } from "./CrsTransparency";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("CRS et banques : information transparente, sans fausse classification", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it("makes no promise of anonymity or of a non-reporting bank", () => {
    expect(explainCRS("FR", "CH")).toContain("relation bilatérale");
    expect(explainCRS("FR", "US")).toContain("FATCA");
    expect(explainCRS("FR", "FR")).toContain("Même juridiction");
    expect(explainCRS("FR", "OTHER")).toContain("vérifie");
    expect(CRS_SOURCES.relations).toMatch(/^https:\/\/www\.oecd\.org\//);
    expect(CRS_SOURCES.declaration).toMatch(/^https:\/\/www\.impots\.gouv\.fr\//);
  });

  it("lets the reader explore two tax jurisdictions without exposing their account data", async () => {
    await act(async () => root.render(<CrsTransparency />));
    const selects = [...host.querySelectorAll<HTMLSelectElement>("select")];
    expect(selects).toHaveLength(2);
    expect(host.textContent).toContain("non-CRS");
    expect(host.textContent).toContain("ne signifie pas");
    expect(host.textContent).toContain("relations CRS activées");
    expect(host.querySelectorAll('a[target="_blank"][rel="noopener noreferrer"]').length).toBeGreaterThan(2);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(selects[1], "US");
      selects[1]!.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(host.querySelector(".crs-result")?.textContent).toContain("FATCA");
    expect(host.textContent).toContain("3916");
    expect(host.textContent).toContain("2027");
  });
});
