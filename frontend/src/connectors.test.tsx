import { describe, expect, it } from "vitest";
import { BANK_BRANDS, brandOf, initials, matchBrand, normalizeName, searchAspsps } from "./banks";
import { monthsLabel } from "./components/Decide";
import { formatField } from "./pages/Connecteurs";

const ASPSPS = [
  "Crédit Agricole Centre Loire",
  "Crédit Agricole Val de France",
  "BNP Paribas",
  "Société Générale",
  "Caisse d'Epargne Loire Centre",
  "Banque Populaire Val de France",
  "BRED",
  "Boursorama",
  "La Banque Postale",
  "Fortuneo",
  "Revolut",
  "Qonto",
];

const clean = (s: string) => s.replace(/[  ]/g, " ");

describe("connexion rapide aux banques", () => {
  it("groups regional banks under one tile and keeps single banks direct", () => {
    const ca = BANK_BRANDS.find((b) => b.id === "ca")!;
    expect(matchBrand(ca, ASPSPS)).toEqual(["Crédit Agricole Centre Loire", "Crédit Agricole Val de France"]);
    expect(matchBrand(BANK_BRANDS.find((b) => b.id === "bp")!, ASPSPS)).toEqual(["Banque Populaire Val de France", "BRED"]);
    expect(matchBrand(BANK_BRANDS.find((b) => b.id === "bnp")!, ASPSPS)).toEqual(["BNP Paribas"]);
    expect(matchBrand(BANK_BRANDS.find((b) => b.id === "n26")!, ASPSPS)).toEqual([]);
  });

  it("normalizes accents and apostrophes", () => {
    expect(normalizeName("Caisse d’Épargne  Loire")).toBe("caisse d'epargne loire");
    expect(brandOf("Caisse d'Epargne Loire Centre")?.id).toBe("ce");
    expect(brandOf("Société Générale")?.id).toBe("sg");
    expect(brandOf("Ma Petite Banque")).toBeUndefined();
  });

  it("searches by substring, best match first", () => {
    expect(searchAspsps("agricole", ASPSPS)).toEqual(["Crédit Agricole Centre Loire", "Crédit Agricole Val de France"]);
    expect(searchAspsps("postale", ASPSPS)).toEqual(["La Banque Postale"]);
    expect(searchAspsps("x", ASPSPS)).toEqual([]);
  });

  it("builds logo initials", () => {
    expect(initials("BNP Paribas")).toBe("BP");
    expect(initials("Revolut")).toBe("RE");
    expect(initials("Caisse d'Épargne")).toBe("CD");
    expect(initials("")).toBe("?");
  });
});

describe("décisions", () => {
  it("labels durations in months and years", () => {
    expect(monthsLabel(5)).toBe("5 mois");
    expect(monthsLabel(12)).toBe("1 an");
    expect(monthsLabel(26)).toBe("2 ans et 2 mois");
  });
});

describe("documents officiels", () => {
  it("formats fields for display", () => {
    expect(clean(formatField("rfr_cents", 2345600))).toBe("23 456,00 €");
    expect(formatField("withholding_rate_percent", "5.6")).toBe("5,6 %");
    expect(formatField("end_of_rights", "2027-03-15")).toMatch(/15 mars/);
    expect(clean(formatField("prestations", [{ label: "Prime d'activité", monthly_cents: 25829 }]))).toBe("Prime d'activité 258,29 €");
    expect(formatField("prestations", [])).toBe("aucune");
    expect(formatField("parts", null)).toBe("—");
  });
});
