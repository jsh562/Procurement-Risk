/**
 * How every figure on the worklist is produced, in one committed table.
 *
 * This is a demonstration surface. Its purpose is that a reader can take any
 * number on the screen and find out what it is, the arithmetic that produced it,
 * and where the data underneath it came from — including where that data is
 * fabricated, which for this product is almost everywhere.
 *
 * Three rules govern what may appear here.
 *
 * **The ranking score is never published**, and this table explains the refusal
 * rather than working around it. Expected schedule harm is a mean overrun
 * multiplied by criticality, and criticality is on the row; publishing the score
 * would surrender the mean overrun to one division and a mean delivery date to
 * one further addition. Naming the formula is safe, printing its value is not.
 *
 * **Provenance is stated honestly.** Most figures here trace to a seeded
 * generator rather than to a measurement, and one of its parameters was chosen
 * by back-solving an acceptance band. Saying so is the point of the surface; a
 * transparency panel that implied measurement would be worse than none.
 *
 * **Nothing here is the only carrier of anything.** FR-019 requires the as-of
 * date reachable without hover or expansion, and FR-050 requires a state to be
 * carried by text in the accessibility tree. Every figure this table explains is
 * already rendered on the row; these panels add reasons, never facts.
 */

export type Explanation = {
  /** The toggle's accessible name. Distinct per entry — asserted. */
  readonly title: string;
  /** What the figure is, in a coordinator's terms rather than the model's. */
  readonly what: string;
  /** The arithmetic, written as it appears in the code that computes it. */
  readonly formula?: string;
  /** The named inputs the formula consumes. */
  readonly inputs?: readonly string[];
  /** Where the underlying data comes from, including whether it is synthetic. */
  readonly source: string;
  /** What is deliberately withheld here, and the reason. */
  readonly withheld?: string;
};

/**
 * Declared rather than inferred. `as const` would narrow each entry to its own
 * literal type, which makes `formula` and `inputs` absent from the union instead
 * of optional on it — so every reader would have to widen the value back before
 * it could ask whether a formula exists.
 */
export type ExplanationKey =
  | "rank"
  | "identity"
  | "needBy"
  | "missProbability"
  | "durationPair"
  | "criticality"
  | "calendarMargin"
  | "asOfDate"
  | "state";

export const EXPLANATIONS: Record<ExplanationKey, Explanation> = {
  rank: {
    title: "How this line's position is decided",
    // Deliberately does not name the ranking quantity in words. `Row.test.tsx`
    // asserts that no row's markup contains "harm", as the row-scoped proxy for
    // FR-041's rule that the score never reaches the screen. The formula below
    // is the traceable part; the label for it is not worth weakening that check.
    what: "The row's place in the ordering. Position 1 is the line where delay is expected to cost the most — read as expected days late, weighted by how much being late costs.",
    formula: "mean(max(0, draw − need_by_offset)) × criticality",
    inputs: [
      "Every posterior draw for this line, as days from the run's anchor",
      "The offset from the anchor to the effective need-by date",
      "Criticality, 1 to 5",
    ],
    source:
      "Computed in the serving boundary at request time from stored forecast artifacts. No model runs when this page loads.",
    withheld:
      "The score itself is never shown. Criticality is on this row, so publishing the score would give the mean overrun by division, and the need-by date plus a mean overrun is a single predicted delivery date — the one thing this product refuses to display. The ordering therefore reaches you as a position rather than a number.",
  },
  identity: {
    title: "Where this line's identity comes from",
    what: "The project, purchase order and line number this row describes.",
    source:
      "Wholly synthetic. Generated from a committed seed (20260416), with per-line values derived from a hash of the project, PO number and line number so the dataset regenerates identically. No real firm, vendor, project or purchase order is represented.",
  },
  needBy: {
    title: "Where the need-by date comes from",
    what: "The date the material is needed on site. This is an input to the forecast, never an output of it.",
    formula: "order_date + round(line_expected_duration_days) + slack_days",
    inputs: [
      "Order date, drawn uniformly over 2025-06-16 to 2026-02-16",
      "Expected duration for the category, adjusted by the vendor's offset",
      "Slack, drawn as a fraction of that duration",
    ],
    source:
      "Synthetic. Slack is drawn from a normal distribution with mean 0.13 truncated at zero — and that 0.13 was back-solved, not measured: the datasheet had declared 0.15, which produced a 24.6% late-delivery share against a required band of 25 to 35 percent, so the parameter moved until the band passed. The dataset's own datasheet states it plainly: nothing here is measurement.",
  },
  missProbability: {
    title: "How the chance of missing the date is computed",
    what: "The probability this line is still undelivered on its need-by date, stated in both directions so neither reading has to be inferred.",
    formula: "survival[(need_by − as_of) − 1], where survival[k] = count(draws > k) / draw_count",
    inputs: [
      "The line's survival array over a 365-day grid",
      "The offset in days from the run's anchor to the effective need-by date",
    ],
    // The bounded forms are described rather than quoted: `Row.test.tsx` asserts
    // that an ordinary figure never renders the words those bounds are spoken
    // as, and copy quoting them verbatim would trip that check without any
    // bounded figure being present.
    source:
      "Read straight off the stored array — the value is already the chance of being late, so no complement is taken. The pair is rounded half-up, and the second direction is 100 minus the first rather than a second rounding, so the two always sum to one hundred. Anything that would round to nothing, or to a certainty, renders as a bound instead — a forecast reported as certain is no longer a forecast. The two flat percentages are not written even here: this panel renders inside the row, and a row showing a bounded figure must not carry the numerals that figure exists to avoid.",
    withheld:
      "Withheld entirely when the need-by date falls on or before the run's anchor. The probability there is one by construction, which says nothing about the line, and the survival grid starts at day one so there is no cell to read.",
  },
  durationPair: {
    title: "How the delivery window is computed",
    what: "Two labelled points on the same distribution, counted in days forward from the run's anchor. They are read together; neither is the answer on its own.",
    formula: "draws[max(1, ceil(p × draw_count / 100)) − 1], for p = 50 and p = 80",
    inputs: [
      "The line's sorted posterior draws — 4,000 of them",
      "Nearest-rank convention, one-based, no interpolation",
    ],
    source:
      "Each draw is a remaining duration for this line, conditioned on it having survived the time already elapsed — not a total duration with elapsed days subtracted. The percentile convention travels with the figure because a median read by a different rule is a different number.",
    withheld:
      "Never converted to a date. The 'N in 100 comparable orders' phrasing is the reference class: it means 50 of every hundred orders like this one, not 50 of the parts on this line.",
  },
  criticality: {
    title: "Where criticality comes from",
    what: "How much it costs to be late on this line, on a 1 to 5 scale where 5 is most critical. It multiplies the ranking score.",
    formula: "table lookup: lead-time tier of the material category × slack tercile",
    inputs: [
      "The category's lead-time tier — long-lead equipment ranks higher",
      "This line's slack relative to its category's expected duration, as a tercile over the whole dataset",
    ],
    source:
      "Synthetic and fully derived — never drawn independently. Nine table cells produce five bands, and generation fails if any band is unreachable. Derivation runs slack to pressure to band in one direction only, so criticality feeds nothing that produces slack.",
  },
  calendarMargin: {
    title: "How the calendar margin is computed",
    what: "Days between the run's anchor and the need-by date. Positive is room; negative means the line was already overdue when the model was fitted.",
    formula: "need_by − as_of",
    inputs: ["The effective need-by date", "The active run's anchor date"],
    source:
      "Pure calendar arithmetic with no forecast input whatever — deliberately. A margin derived from a predicted delivery date could be subtracted back out of the need-by date to reconstruct that prediction, which would defeat through arithmetic what the design refuses through a field.",
  },
  asOfDate: {
    title: "What the as-of date means",
    what: "The date the active forecast run was fitted. Every probability on this page is evaluated against this date, not against today.",
    source:
      // "out of date" is the stale mark's own wording, and `Row.test.tsx`
      // asserts a fresh row never carries it. Described here in different words
      // so the panel cannot counterfeit the mark it explains.
      "Chosen so a row's figures do not change overnight with no model run and no action by anyone. A run is flagged as no longer current once it is more than seven days old — a week being the refit cadence, so an older run has missed at least one cycle of lifecycle events. Staleness suppresses nothing: the figures are the ones that run produced, and they are not less true for the run being old, only less current.",
  },
  state: {
    title: "What this row's status means",
    what: "How much of this row can be trusted. Eight states are possible and exactly one applies, resolved in a fixed precedence.",
    source:
      "Precedence runs from states that make a figure untrustworthy, through states that make one only partially available, to states that merely annotate a figure that is sound: no active run, then vendor records changed, then not covered by the run, then beyond the forecast window, then need-by before the anchor, then need-by simply gone by. A row with no degraded state carries no label.",
    withheld:
      "A line the run does not cover is listed but never ranked. Giving it a score of zero would place it among the safest lines on the strength of having no forecast at all.",
  },
};
