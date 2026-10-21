import { describe, expect, it } from "vitest";
import { errorMessage, readCookie } from "./api";
import { amountOnly, dayNumber, money, parseEuros, plural, shortDate } from "./format";
import { parseHash } from "./router";

const clean = (s: string) => s.replace(/[  ]/g, " ");

describe("format", () => {
  it("formats euros the French way", () => {
    expect(clean(money(123456))).toBe("1 234,56 €");
    expect(clean(money(-500, { whole: true }))).toBe("-5 €");
    expect(clean(money(2500, { signed: true }))).toBe("+25,00 €");
    expect(clean(amountOnly(3849))).toBe("38");
  });

  it("parses user input into cents", () => {
    expect(parseEuros("12,50")).toBe(1250);
    expect(parseEuros("1 234")).toBe(123400);
    expect(parseEuros("-3.5")).toBe(-350);
    expect(parseEuros("12,345")).toBeNull();
    expect(parseEuros("abc")).toBeNull();
  });

  it("treats ISO dates as civil dates (no UTC shift)", () => {
    expect(dayNumber("2026-10-01")).toBe("1");
    expect(shortDate("2026-10-31")).toMatch(/31/);
  });

  it("pluralizes", () => {
    expect(plural(1, "jour", "jours")).toBe("1 jour");
    expect(plural(0, "jour", "jours")).toBe("0 jour");
    expect(plural(12, "jour", "jours")).toBe("12 jours");
  });
});

describe("api helpers", () => {
  it("reads the CSRF cookie", () => {
    expect(readCookie("bank_csrf", "a=1; bank_csrf=abc%3D; z=2")).toBe("abc=");
    expect(readCookie("missing", "a=1")).toBe("");
  });

  it("turns FastAPI errors into readable messages", () => {
    expect(errorMessage("Compte introuvable", "x")).toBe("Compte introuvable");
    expect(errorMessage([{ loc: ["body", "iban"], msg: "IBAN invalide" }], "x")).toBe("iban : IBAN invalide");
    expect(errorMessage(null, "Erreur 500")).toBe("Erreur 500");
  });
});

describe("router", () => {
  it("parses hash routes with params", () => {
    const r = parseHash("#/banques?result=connected");
    expect(r.page).toBe("banques");
    expect(r.params.get("result")).toBe("connected");
    expect(parseHash("").page).toBe("aujourdhui");
    expect(parseHash("#/").page).toBe("aujourdhui");
  });
});
