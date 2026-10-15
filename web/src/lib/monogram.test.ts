import { describe, expect, test } from "vitest";
import { initials, hueFor } from "./monogram";

describe("initials", () => {
  test.each([
    ["Wealthsimple", "WE"],
    ["Jane Software", "JS"],
    ["1Password", "1P"],
    ["Coinbase", "CO"],
  ])("%s becomes %s", (name, expected) => {
    expect(initials(name)).toBe(expected);
  });

  test("an empty name does not crash the row it labels", () => {
    expect(initials("")).toBe("??");
  });
});

describe("hueFor", () => {
  test("a company is always the same colour", () => {
    expect(hueFor("Stripe")).toBe(hueFor("Stripe"));
  });

  test("different companies generally differ", () => {
    const hues = new Set(["Stripe", "Palantir", "Figma", "Ramp", "Notion"].map(hueFor));

    expect(hues.size).toBeGreaterThan(3);
  });

  test("hue is a usable degree", () => {
    for (const name of ["Stripe", "Palantir", "", "Æ"]) {
      expect(hueFor(name)).toBeGreaterThanOrEqual(0);
      expect(hueFor(name)).toBeLessThan(360);
    }
  });
});
