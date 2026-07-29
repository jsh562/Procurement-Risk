import { describe, expect, it } from "vitest";
import { STATE_COPY, STATE_KEYS, type StateKey } from "./stateCopy";

/**
 * FR-044's distinctness obligation, asserted as a comparison over the table.
 *
 * SC-009 requires each of FR-018's eight states to be distinguishable from the
 * others by its wording alone. That is only decidable because the copy is
 * committed data: a screen's rendered text would have to be built first, and
 * the check would then be evaluated against wording invented to pass it.
 */

/** The full text a coordinator meets for one state. */
const textOf = (key: StateKey): string => {
  const copy = STATE_COPY[key];
  return `${copy.label} ${copy.cause} ${copy.remedy}`;
};

/**
 * Words that carry no distinguishing weight. Excluded so "the" occurring in
 * every entry does not count as shared meaning — the obligation is about the
 * phrases that name a cause, not about English function words.
 */
const STOPWORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "been",
  "before",
  "below",
  "by",
  "can",
  "day",
  "for",
  "from",
  "has",
  "have",
  "in",
  "is",
  "it",
  "its",
  "no",
  "not",
  "of",
  "on",
  "one",
  "only",
  "or",
  "run",
  "shown",
  "since",
  "so",
  "still",
  "than",
  "that",
  "the",
  "their",
  "them",
  "then",
  "there",
  "these",
  "they",
  "this",
  "to",
  "up",
  "was",
  "were",
  "what",
  "which",
  "with",
  "you",
]);

const phrasesOf = (key: StateKey): Set<string> => {
  const words = textOf(key)
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .split(/\s+/)
    .filter((word) => word.length > 0 && !STOPWORDS.has(word));
  return new Set(words);
};

describe("degraded-state copy table", () => {
  it("covers exactly FR-018's eight states", () => {
    expect([...STATE_KEYS].sort()).toEqual([
      "already_late",
      "beyond_horizon",
      "calendar_passed",
      "empty_filter",
      "no_active_run",
      "not_covered",
      "roster_mismatch",
      "stale_run",
    ]);
  });

  it("names a cause and a remedy for every state", () => {
    // FR-044. The remedy is what separates a refusal a coordinator can act on
    // from a dead end, so an entry missing one is not a shorter entry — it is
    // a state the screen refuses to explain.
    for (const key of STATE_KEYS) {
      expect(STATE_COPY[key].cause.length, `${key} cause`).toBeGreaterThan(0);
      expect(STATE_COPY[key].remedy.length, `${key} remedy`).toBeGreaterThan(0);
      expect(STATE_COPY[key].label.length, `${key} label`).toBeGreaterThan(0);
    }
  });

  it("holds no entry that is a substring of another", () => {
    // An entry contained in another is not distinguishable from it by wording:
    // a coordinator who read the longer one has already read the shorter.
    for (const outer of STATE_KEYS) {
      for (const inner of STATE_KEYS) {
        if (outer === inner) continue;
        expect(textOf(outer).includes(textOf(inner)), `${inner} is a substring of ${outer}`).toBe(
          false,
        );
      }
    }
  });

  it("gives every entry at least one phrase occurring in no other entry", () => {
    // The stronger half of FR-044. Two entries could each avoid containing the
    // other and still say the same thing in a different order; this is what
    // rules that out.
    for (const key of STATE_KEYS) {
      const others = new Set(
        STATE_KEYS.filter((other) => other !== key).flatMap((other) => [...phrasesOf(other)]),
      );
      const unique = [...phrasesOf(key)].filter((phrase) => !others.has(phrase));
      expect(unique.length, `${key} shares every phrase with another state`).toBeGreaterThan(0);
    }
  });

  it("gives every label its own wording", () => {
    // The label alone is what a coordinator scanning a column of rows reads,
    // so distinctness across the full text is not enough on its own.
    const labels = STATE_KEYS.map((key) => STATE_COPY[key].label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("marks the three page-scope states and the five row-scope ones", () => {
    // FR-018a. Page states render as banners composing with a row's label
    // rather than competing with it, so the scope has to travel with the copy.
    const page = STATE_KEYS.filter((key) => STATE_COPY[key].scope === "page");
    expect([...page].sort()).toEqual(["empty_filter", "no_active_run", "stale_run"]);
    expect(STATE_KEYS.length - page.length).toBe(5);
  });

  it("states no risk figure in the copy for a state that withholds them", () => {
    // FR-015, FR-054. Copy is text a renderer prints verbatim, so a percentage
    // baked into an explanation is a figure on a screen that must show none.
    for (const key of ["no_active_run", "not_covered", "roster_mismatch"] as StateKey[]) {
      expect(textOf(key), `${key} copy carries a figure`).not.toMatch(/\d+\s*%|\bp(50|80)\b/i);
    }
  });
});
