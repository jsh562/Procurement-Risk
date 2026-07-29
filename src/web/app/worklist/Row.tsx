import { STATE_COPY } from "./stateCopy";
import type { PercentFigure, RankedRow, UnrankedRow } from "./worklist";
import styles from "./page.module.css";

/**
 * One row of the worklist.
 *
 * FR-032 makes the presentation a contract rather than a matter of taste, and
 * four of its clauses are load-bearing here:
 *
 * **Reading order** — identity, need-by date, miss probability, quantile pair,
 * in that sequence in the document *and* therefore in the accessibility tree's
 * traversal. A coordinator hearing the row and one seeing it meet it in the
 * same order.
 *
 * **Subordination** — the secondary region is smaller than the primary and never
 * heavier. FR-032 states the direction because "distinct" without one is
 * satisfied by a secondary region set *larger* than the primary, which inverts
 * the subordination FR-009 asks for.
 *
 * **Rank as text** (FR-048) — the ranking quantity is deliberately absent from
 * the row under FR-041, so position read off the screen's geometry would leave
 * the product's entire output conveyed by nothing to a screen reader.
 *
 * **The pair under one accessible name** (FR-049) — a linearised reading of two
 * bare numbers is exactly the independent-quantile reading FR-004 removes, and
 * it reissues the invitation to treat one of them alone as the answer.
 */
export function Row({
  row,
  control,
  runIsStale = false,
}: {
  readonly row: RankedRow;
  /**
   * FR-029's row-level stale mark, composed from `meta.forecast_run.stale`
   * rather than from a per-row field. The requirement states the mark "needs no
   * new figure and no new field; the response already carries the run's
   * staleness and each row's as-of date" — so the page reads the run's flag
   * once and each row qualifies its own as-of date with it.
   */
  readonly runIsStale?: boolean;
  /**
   * FR-031's what-if control, when the page is interactive. Passed in rather
   * than built here so this component stays renderable on the server for the
   * first paint — the ranking is what the coordinator needs immediately, and a
   * row that could only render inside a client component would delay it.
   */
  readonly control?: React.ReactNode;
}) {
  const state = row.state === "nominal" ? null : STATE_COPY[row.state];
  const pair = row.primary.duration_pair;
  const miss = row.primary.miss_probability;

  return (
    <li className={styles.row} data-state={row.state}>
      {/* FR-048. Text within the row, not a visual position. */}
      <span className={styles.rank}>
        <span className={styles.visuallyHidden}>Position </span>
        {row.rank}
      </span>

      <div className={styles.primary}>
        <span className={styles.identity}>
          {row.primary.identity.project_id} · {row.primary.identity.po_number}-
          {row.primary.identity.line_number}
        </span>
        <span className={styles.description}>{row.primary.identity.description}</span>

        <span className={styles.needBy}>
          Need by <time dateTime={row.primary.need_by.date}>{row.primary.need_by.date}</time>
          {/*
           * FR-031, FR-051. The mark sits adjacent to the date it qualifies,
           * inside the same element, so it is read with the date rather than
           * as a separate announcement a coordinator might not reach.
           */}
          {row.primary.need_by.unsaved ? (
            <span className={styles.unsaved}>
              {" "}
              (unsaved what-if — recorded date {row.primary.need_by.date_of_record})
            </span>
          ) : null}
        </span>

        {control}

        <MissProbability figure={miss} />
        <DurationPair pair={pair} />
      </div>

      <Secondary secondary={row.secondary} runIsStale={runIsStale} />

      {/* FR-050. The resolved state as text, never as a tint alone. */}
      {state ? <span className={styles.rowState}>{state.label}</span> : null}
    </li>
  );
}

/**
 * FR-006's dual framing, as one statement.
 *
 * Both directions carry equal prominence — the same type scale and weight,
 * adjacent within one statement — and appear in a fixed order on every row,
 * the chance of missing first. A pair in which one direction is set larger or
 * first on some rows and second on others is not a dual framing; it is a single
 * framing with a footnote.
 *
 * FR-017's `upper_bound` is rendered in different words from a point figure,
 * because the two are different claims and the measure travels with the figure
 * precisely so the interface does not have to infer it from the row's state.
 */
function MissProbability({
  figure,
}: {
  readonly figure: RankedRow["primary"]["miss_probability"];
}) {
  if (figure === null) {
    // FR-030, FR-054. An explicit empty, and the row's state — already
    // rendered as text elsewhere in this row — is what says which state
    // removed it. Never a dash: a dash reads as a figure.
    return (
      <span className={styles.missProbability}>
        No miss probability — this date has already passed the forecast anchor.
      </span>
    );
  }

  const bound = figure.measure === "upper_bound";
  return (
    <span className={styles.missProbability}>
      {bound ? "At most " : ""}
      <Percent figure={figure.miss} /> miss the date
      {bound ? ", at least " : ", "}
      <Percent figure={figure.on_time} /> arrive in time
    </span>
  );
}

/**
 * FR-008's spoken forms. Written out in the accessibility tree, per FR-051.
 *
 * The `<` and `>` glyphs are read inconsistently by screen readers and dropped
 * outright by some — and a dropped `<` turns `<1%` into a flat `1%`, which is a
 * false precision of exactly the kind FR-008 exists to remove. The bound would
 * become the certainty it was introduced to avoid, silently, for the readers
 * least able to notice.
 *
 * `aria-hidden` on the glyph and a visually-hidden span carrying the words:
 * sighted readers see `<1%`, assistive technology hears "less than one
 * percent", and neither gets both.
 */
function Percent({ figure }: { readonly figure: PercentFigure }) {
  const spoken = SPOKEN_BOUND[figure.display];
  if (!spoken) {
    return <strong className={styles.figure}>{figure.display}</strong>;
  }
  return (
    <strong className={styles.figure}>
      <span aria-hidden="true">{figure.display}</span>
      <span className={styles.visuallyHidden}>{spoken}</span>
    </strong>
  );
}

/**
 * FR-051 names both forms verbatim. Keyed by the display string the server
 * already finished, so the interface performs no second rounding and no
 * substitution rule of its own — a figure this map does not know renders
 * unchanged rather than being guessed at.
 */
const SPOKEN_BOUND: Readonly<Record<string, string>> = {
  "<1%": "less than one percent",
  ">99%": "greater than ninety-nine percent",
};

/**
 * FR-004 and FR-049. One labelled unit, not two adjacent numbers.
 *
 * The `<dl>` binds each figure to its own label and the whole to one accessible
 * name via `aria-labelledby`, so a screen reader reaches "likely delivery
 * window" before either number rather than encountering two unrelated integers.
 *
 * The anchor is rendered, not implied: an unanchored median of thirty days on a
 * ten-day-old run reads ten days more optimistic than it is.
 *
 * FR-005 requires the reference class on *each* quantile, and the class is named
 * at every point a frequency appears rather than once per pair. Two reasons, and
 * the second was observed rather than anticipated:
 *
 * The eightieth previously carried no reference class at all — its label read
 * "Four in five land by" and its figure "20 in 100 land later", so it borrowed a
 * population from the *median's* label two lines up, across the very gap the
 * stylesheet inserts to separate the two statements.
 *
 * And a denominator standing without its population takes one from the nearest
 * noun on the row. An open purchase-order line carries a quantity and a unit of
 * measure, so "50 in 100" set beside a part number was read in the field as
 * fifty of the hundred parts on that line rather than as fifty of every hundred
 * comparable orders. That is a third lay misreading beside the two Research
 * records, and it is the one this requirement's own mitigation produces: the
 * frequency framing supplies a denominator, and an absent noun leaves it free to
 * be filled from context.
 */
function DurationPair({ pair }: { readonly pair: RankedRow["primary"]["duration_pair"] }) {
  const labelId = `pair-${pair.as_of_date}-${pair.median.days}-${pair.eightieth.days}`;
  return (
    <section className={styles.durationPair} aria-labelledby={labelId}>
      <h3 id={labelId} className={styles.pairLabel}>
        Likely delivery window, counted from{" "}
        <time dateTime={pair.as_of_date}>{pair.as_of_date}</time>
      </h3>
      <dl className={styles.pairFigures}>
        <dt>Half of comparable orders land by</dt>
        <dd>
          <strong className={styles.figure}>{pair.median.days} days</strong> —{" "}
          {pair.median.later_percent} in 100 comparable orders land later
        </dd>
        <dt>Four in five comparable orders land by</dt>
        <dd>
          <strong className={styles.figure}>{pair.eightieth.days} days</strong> —{" "}
          {pair.eightieth.later_percent} in 100 comparable orders land later
        </dd>
      </dl>
    </section>
  );
}

/**
 * FR-009's explanatory context — the two score inputs that are not the
 * distribution, plus the frame everything is counted from.
 *
 * The score itself is absent under FR-041: with criticality displayed beside
 * it, publishing the score would surrender the mean overrun to one division,
 * and need-by plus mean overrun is a mean delivery date.
 */
function Secondary({
  secondary,
  runIsStale,
}: {
  readonly secondary: RankedRow["secondary"];
  readonly runIsStale: boolean;
}) {
  const margin = secondary.calendar_margin_days;
  return (
    <div className={styles.secondary}>
      <span>Criticality {secondary.criticality} of 5</span>
      <span>
        {margin >= 0 ? `${margin} days of margin` : `${Math.abs(margin)} days past the anchor`}
      </span>
      {/*
       * FR-019, and FR-029's row-level stale mark. Reachable without hover or
       * expansion, and qualified in the row itself when the run is stale — a
       * page banner stops carrying once rows are sorted, filtered, or read one
       * at a time, and a coordinator reading this row alone would otherwise
       * take its figures as current.
       */}
      <span className={runIsStale ? styles.staleAsOf : undefined}>
        Forecast as of <time dateTime={secondary.as_of_date}>{secondary.as_of_date}</time>
        {runIsStale ? " — out of date" : null}
      </span>
    </div>
  );
}

/**
 * An excluded line.
 *
 * FR-016: identity and need-by date, and nothing further. Not criticality, not
 * calendar margin — with the risk figures withheld those would be the only
 * numbers on the row and would be read as risk.
 */
export function ExcludedRow({ row }: { readonly row: UnrankedRow }) {
  return (
    <li className={styles.row} data-state={row.state}>
      <div className={styles.primary}>
        <span className={styles.identity}>
          {row.primary.identity.project_id} · {row.primary.identity.po_number}-
          {row.primary.identity.line_number}
        </span>
        <span className={styles.description}>{row.primary.identity.description}</span>
        <span className={styles.needBy}>
          Need by <time dateTime={row.primary.need_by.date}>{row.primary.need_by.date}</time>
        </span>
      </div>
      <span className={styles.rowState}>{STATE_COPY[row.state].label}</span>
    </li>
  );
}
