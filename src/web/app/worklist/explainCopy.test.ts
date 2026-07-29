import { describe, expect, it } from "vitest";
import { EXPLANATIONS, type ExplanationKey } from "./explainCopy";

/**
 * The provenance table's obligations, asserted over the committed copy.
 *
 * These panels exist so a reader can check the product's arithmetic. That makes
 * the copy itself load-bearing: a panel that named the wrong formula, or quietly
 * implied a synthetic figure was measured, would be worse than no panel at all
 * — it would lend the number a credibility the number does not have.
 *
 * Asserted over the table rather than over rendered output for the same reason
 * `stateCopy.test.ts` is: a check evaluated against a screen would be evaluated
 * against wording invented to pass it.
 */

const KEYS = Object.keys(EXPLANATIONS) as ExplanationKey[];

const textOf = (key: ExplanationKey): string => {
  const entry = EXPLANATIONS[key];
  return [
    entry.title,
    entry.what,
    entry.formula ?? "",
    ...(entry.inputs ?? []),
    entry.source,
    entry.withheld ?? "",
  ].join(" ");
};

describe("provenance copy table", () => {
  it("explains every figure the row renders", () => {
    expect([...KEYS].sort()).toEqual([
      "asOfDate",
      "calendarMargin",
      "criticality",
      "durationPair",
      "identity",
      "missProbability",
      "needBy",
      "rank",
      "state",
    ]);
  });

  it("gives every entry a title, a description and a source", () => {
    // The source is the point of the surface. An entry that describes a figure
    // without saying where it came from is a caption, not provenance.
    for (const key of KEYS) {
      expect(EXPLANATIONS[key].title.length, `${key} title`).toBeGreaterThan(0);
      expect(EXPLANATIONS[key].what.length, `${key} what`).toBeGreaterThan(0);
      expect(EXPLANATIONS[key].source.length, `${key} source`).toBeGreaterThan(0);
    }
  });

  it("gives every toggle its own accessible name", () => {
    // Each panel's title is its toggle's accessible name. Twenty toggles on a
    // row that all announce the same thing are twenty toggles a screen-reader
    // user cannot tell apart.
    const titles = KEYS.map((key) => EXPLANATIONS[key].title);
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("never publishes the ranking score", () => {
    // FR-041, and the reason this whole surface needed care. Expected schedule
    // harm is a mean overrun times criticality, and criticality is on the row —
    // so a panel that printed the score would hand over the mean overrun by
    // division and a mean delivery date by one further addition.
    //
    // The rank entry may name the formula; it may not carry a computed value.
    for (const key of KEYS) {
      const text = textOf(key);
      expect(text, `${key} prints a score`).not.toMatch(/score (?:of|is|=)\s*\d/i);
      expect(text.toLowerCase(), `${key} names the forbidden field`).not.toContain("expected harm");
    }
    expect(EXPLANATIONS.rank.withheld, "the rank entry must explain the refusal").toBeTruthy();
  });

  it("states no predicted delivery date in any form the page forbids", () => {
    // FR-007's enumeration binds every surface, and an explanation is a surface.
    // The e2e tier asserts these same patterns over the rendered body; this
    // catches a violation at the source, where the message names the file.
    for (const key of KEYS) {
      const text = textOf(key);
      expect(text, `${key}`).not.toMatch(/expected (delivery|arrival)/i);
      expect(text, `${key}`).not.toMatch(/\bETA\b/);
      expect(text, `${key}`).not.toMatch(/will arrive|arrives on/i);
    }
  });

  it("declares the synthetic origin of every generated field", () => {
    // Principle I and the dataset's own datasheet, which states that nothing in
    // it is measurement. A provenance panel that described a fabricated figure
    // without saying so would be the exact misrepresentation this surface is
    // supposed to prevent.
    for (const key of ["identity", "needBy", "criticality"] as ExplanationKey[]) {
      expect(EXPLANATIONS[key].source.toLowerCase(), `${key} hides its synthetic origin`).toMatch(
        /synthetic|generated|not measurement|nothing here is measurement/,
      );
    }
  });

  it("records that the slack parameter was back-solved rather than measured", () => {
    // The single most misleading thing this product could imply is that its
    // late-delivery rate was observed. It was not: the declared 0.15 produced
    // 24.6% against a required 25-35% band, and the parameter moved until the
    // band passed. If that sentence ever disappears, this fails.
    expect(EXPLANATIONS.needBy.source).toMatch(/back-solved|0\.13/);
    expect(EXPLANATIONS.needBy.source).toMatch(/24\.6|25 to 35/);
  });

  it("keeps each formula in the terms the code computes it in", () => {
    // A formula rewritten into prose stops being checkable against the source,
    // which is the only property that makes this surface worth having.
    expect(EXPLANATIONS.missProbability.formula).toContain("survival[");
    expect(EXPLANATIONS.missProbability.formula).toContain("count(draws > k)");
    expect(EXPLANATIONS.durationPair.formula).toContain("draws[");
    expect(EXPLANATIONS.calendarMargin.formula).toBe("need_by − as_of");
    expect(EXPLANATIONS.rank.formula).toContain("criticality");
  });

  it("names no complement on the survival read", () => {
    // The stored value already *is* the chance of being late. `origin/main`
    // carried `1 - survival[...]` in nine places before this epic corrected it,
    // so an explanation that described a complement would re-document the bug.
    expect(EXPLANATIONS.missProbability.formula).not.toMatch(/1\s*[-−]\s*survival/);
    expect(EXPLANATIONS.missProbability.source).toMatch(/no complement/i);
  });
});
