import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ExcludedRow, Row } from "./Row";
import type { RankedRow, UnrankedRow } from "./worklist";

/**
 * One row's presentation contract.
 *
 * FR-032 requires these to be asserted by automated tests rather than left to
 * judgment, and several success criteria rest on the wording. Asserted over
 * document order because that is what the obligations are about: the reading
 * order *is* the sequence, and a test that only checked presence would pass on
 * a row whose figures were shuffled.
 */

const NOMINAL: RankedRow = {
  po_line_id: "1a5d3e70-c2b8-4f6a-9d21-0e77b4c81f55",
  rank: 3,
  state: "nominal",
  primary: {
    identity: {
      project_id: "PRJ-001",
      po_number: "PO-4471",
      line_number: 3,
      description: "Air handling unit AHU-3",
    },
    need_by: {
      date: "2026-08-10",
      date_of_record: "2026-08-10",
      source: "record",
      unsaved: false,
    },
    miss_probability: {
      measure: "point",
      bounded: false,
      miss: { percent: 87, display: "87%" },
      on_time: { percent: 13, display: "13%" },
    },
    duration_pair: {
      unit: "days",
      counted_from: "run_as_of_date",
      as_of_date: "2026-07-24",
      median: { quantile_percent: 50, days: 34, later_percent: 50 },
      eightieth: { quantile_percent: 80, days: 51, later_percent: 20 },
      reference_class: {
        basis: "posterior_predictive_draws",
        draw_count: 4000,
        percentile_convention: "nearest_rank_one_based_no_interpolation",
      },
    },
  },
  secondary: {
    as_of_date: "2026-07-24",
    criticality: 5,
    calendar_margin_days: 17,
  },
};

const render = (row: RankedRow, runIsStale = false) =>
  renderToStaticMarkup(<Row row={row} runIsStale={runIsStale} />);

const withMiss = (miss: RankedRow["primary"]["miss_probability"]): RankedRow => ({
  ...NOMINAL,
  primary: { ...NOMINAL.primary, miss_probability: miss },
});

describe("a ranked row", () => {
  it("presents the four quantities in the declared reading order", () => {
    // FR-032. The same order in the document and in the accessibility tree's
    // traversal, so a coordinator hearing the row and one seeing it meet it in
    // the same sequence.
    const markup = render(NOMINAL);
    const at = (needle: string) => markup.indexOf(needle);

    expect(at("PO-4471")).toBeLessThan(at("2026-08-10"));
    expect(at("2026-08-10")).toBeLessThan(at("87%"));
    expect(at("87%")).toBeLessThan(at("Likely delivery window"));
  });

  it("carries its position as text", () => {
    // FR-048. With the harm score absent under FR-041, position read off the
    // screen's geometry would leave the ordering conveyed by nothing at all.
    const markup = render(NOMINAL);
    expect(markup).toContain("Position ");
    expect(markup).toContain(">3<");
  });

  it("states both directions of the probability, missing first", () => {
    // FR-006. A fixed order on every row: positive and negative framings are
    // not behaviourally equivalent, so alternating them is not a dual framing.
    const markup = render(NOMINAL);
    expect(markup.indexOf("87%")).toBeLessThan(markup.indexOf("13%"));
    expect(markup).toContain("miss the date");
    expect(markup).toContain("arrive in time");
  });

  it("binds the quantile pair to one accessible name", () => {
    // FR-049. A linearised reading of two bare numbers is exactly the
    // independent-quantile reading FR-004 removes.
    const markup = render(NOMINAL);
    expect(markup).toMatch(/aria-labelledby="pair-[^"]+"/);
    expect(markup).toContain("Likely delivery window");
    // Both members present, and each with its own label rather than as siblings.
    expect(markup).toContain("34 days");
    expect(markup).toContain("51 days");
  });

  it("anchors the pair to the as-of date rather than leaving it implied", () => {
    // An unanchored median of thirty days on a ten-day-old run reads ten days
    // more optimistic than it is.
    expect(render(NOMINAL)).toContain("2026-07-24");
  });

  it("states each quantile's complementary frequency", () => {
    // FR-005. "Half of comparable orders land by this day" reads as a
    // proportion of a population; a bare quantile reads as a commitment.
    const markup = render(NOMINAL);
    expect(markup).toContain("Half of comparable orders land by");
    expect(markup).toContain("50 in 100 land later");
    expect(markup).toContain("20 in 100 land later");
  });

  it("renders an upper bound in different words from a point figure", () => {
    // FR-017. The measure travels with the figure precisely so the interface
    // does not infer it from the row's state — two rules that must agree would
    // otherwise live in different tiers.
    const markup = render(
      withMiss({
        measure: "upper_bound",
        bounded: false,
        miss: { percent: 4, display: "4%" },
        on_time: { percent: 96, display: "96%" },
      }),
    );
    expect(markup).toContain("At most");
    expect(markup).toContain("at least");
  });

  it("shows an absent probability as words, never as a dash", () => {
    // FR-054. A dash reads as a figure, which is the defect FR-015 exists to
    // prevent appearing one renderer earlier.
    const markup = render(withMiss(null));
    expect(markup).toContain("No miss probability");
    expect(markup).not.toMatch(/>\s*[—–-]\s*</);
  });

  it("carries the criticality and the calendar margin, and never the score", () => {
    // FR-009 names the three inputs; FR-041 keeps the score off the row,
    // because need-by plus mean overrun is a mean delivery date.
    const markup = render(NOMINAL);
    expect(markup).toContain("Criticality 5 of 5");
    expect(markup).toContain("17 days of margin");
    expect(markup.toLowerCase()).not.toContain("harm");
  });

  it("says how far past the anchor a negative margin is", () => {
    // FR-009. The margin renders negative when a need-by date precedes the
    // as-of date, and it takes no forecast input.
    const markup = render({
      ...NOMINAL,
      secondary: { ...NOMINAL.secondary, calendar_margin_days: -10 },
    });
    expect(markup).toContain("10 days past the anchor");
  });

  it("names a degraded state in text on the row", () => {
    // FR-050. A state carried only by colour is invisible to a coordinator who
    // cannot see it and to every check of SC-009's wording obligation.
    const markup = render({ ...NOMINAL, state: "already_late" });
    expect(markup).toContain("Need-by date precedes the forecast anchor");
  });

  it("marks an unsaved need-by change and names the recorded date it replaces", () => {
    // FR-031. A session what-if that looks like the record is the single
    // confusion the mark exists to prevent — and "unsaved" alone leaves the
    // coordinator unable to say what the record actually holds. The mark sits
    // inside the same element as the date, so it is read with the date rather
    // than as a separate announcement.
    const markup = render({
      ...NOMINAL,
      primary: {
        ...NOMINAL.primary,
        need_by: {
          date: "2026-08-01",
          date_of_record: "2026-08-10",
          unsaved: true,
          source: "session_override",
        },
      },
    });
    expect(markup).toContain("unsaved what-if");
    expect(markup).toContain("recorded date 2026-08-10");
    expect(markup).toContain("2026-08-01");
  });
});

describe("an excluded row", () => {
  const EXCLUDED: UnrankedRow = {
    po_line_id: "2b6e4f81-d3c9-5a7b-ae32-1f88c5d92e66",
    state: "not_covered",
    primary: { identity: NOMINAL.primary.identity, need_by: NOMINAL.primary.need_by },
  };

  it("shows identity, need-by date, and the state — and no numbers beyond them", () => {
    // FR-016. With risk figures withheld, criticality and calendar margin would
    // be the only numbers on the row and would be read as risk.
    const markup = renderToStaticMarkup(<ExcludedRow row={EXCLUDED} />);
    expect(markup).toContain("PO-4471");
    expect(markup).toContain("2026-08-10");
    expect(markup).toContain("Not included in the current forecast");
    expect(markup).not.toContain("Criticality");
    expect(markup).not.toContain("margin");
    expect(markup).not.toMatch(/\d+\s*%/);
  });
});

describe("the row-level stale mark (FR-029)", () => {
  it("qualifies the as-of date in the row when the run is stale", () => {
    // FR-029: "Because a page-level banner alone stops carrying once rows are
    // sorted, filtered, or read one at a time, the row MUST carry the signal
    // too ... so a coordinator reading one row in isolation cannot take its
    // figures as current."
    // Driven by the run's staleness from page scope, not by a per-row field:
    // FR-029 says the mark "needs no new figure and no new field".
    expect(render(NOMINAL, true)).toContain("out of date");
  });

  it("carries the mark as words, not as a style alone", () => {
    // FR-050. A mark carried only by italics or a tint is invisible to a
    // coordinator who cannot see it, and to every automated check.
    const withoutMarkup = render(NOMINAL, true).replace(/<[^>]*>/g, "");
    expect(withoutMarkup).toContain("out of date");
  });

  it("composes the mark without any per-row field", () => {
    // The regression this replaced. An `as_of_is_stale` member on `secondary`
    // would violate FR-027's closed three-item set and FR-029's own "no new
    // field" clause — so the row's inputs are asserted to carry exactly the
    // three the contract declares.
    expect(Object.keys(NOMINAL.secondary).sort()).toEqual([
      "as_of_date",
      "calendar_margin_days",
      "criticality",
    ]);
  });

  it("says nothing extra when the run is fresh", () => {
    // The mark must not be permanent furniture — a qualifier present on every
    // row says nothing on the rows that need it.
    expect(render(NOMINAL)).not.toContain("out of date");
  });
});

describe("spoken bounded forms (FR-051)", () => {
  const bounded = (display: string, other: string): RankedRow => ({
    ...NOMINAL,
    primary: {
      ...NOMINAL.primary,
      miss_probability: {
        measure: "point",
        bounded: true,
        miss: { percent: null, display },
        on_time: { percent: null, display: other },
      },
    },
  });

  it("announces <1% as words", () => {
    // FR-051: "the < and > glyphs are read inconsistently or dropped outright,
    // and a dropped < turns <1% into a flat 1%, which is a false precision of
    // exactly the kind FR-008 exists to remove."
    const markup = render(bounded("<1%", ">99%"));
    expect(markup).toContain("less than one percent");
    expect(markup).toContain("greater than ninety-nine percent");
  });

  it("hides the glyph from assistive technology so neither reader gets both", () => {
    const markup = render(bounded("<1%", ">99%"));
    expect(markup).toMatch(/aria-hidden="true"[^>]*>&lt;1%|<span aria-hidden="true">&lt;1%/);
  });

  it("keeps the glyph for sighted readers", () => {
    // The words are an addition, not a replacement: "<1%" is the compact form a
    // coordinator scans a column by.
    expect(render(bounded("<1%", ">99%"))).toContain("&lt;1%");
  });

  it("leaves an ordinary integer figure untouched", () => {
    // A substitution rule that fired on every figure would speak "87%" as
    // something invented. Only the two forms FR-051 names are rewritten.
    const markup = render(NOMINAL);
    expect(markup).toContain("87%");
    expect(markup).not.toContain("less than");
    expect(markup).not.toContain("greater than");
  });
});
