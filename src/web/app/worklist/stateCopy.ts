/**
 * The committed wording for each of FR-018's eight degraded states.
 *
 * FR-044. Every entry names **the cause** and **what would change it** — the
 * difference between a refusal a coordinator can act on and a dead end. A
 * screen that says "no data available" has told a coordinator nothing they can
 * do next; one that says the forecast has not run yet, and that figures appear
 * once it does, has.
 *
 * The table is committed and keyed by state so SC-009's distinctness obligation
 * is decidable as a comparison over data rather than a reading of a screen
 * someone has already built. Copy composed at the render site is copy no test
 * can pin: "distinguishable by wording alone" would then be evaluated only
 * after the implementer had invented the wording it judges. `stateCopy.test.ts`
 * asserts the two properties FR-044 states — no entry a substring of another,
 * and every entry holding at least one phrase occurring in no other entry.
 *
 * These strings are also the accessible text FR-050 requires: a state carried
 * only by colour or position is invisible to a coordinator who cannot see it,
 * and invisible to every automated check of the wording obligation.
 */

/** FR-018's canonical enumeration. Three page-scope, five row-scope. */
export type PageStateKey = "no_active_run" | "stale_run" | "empty_filter";

export type RowStateKey =
  "not_covered" | "beyond_horizon" | "roster_mismatch" | "already_late" | "calendar_passed";

export type StateKey = PageStateKey | RowStateKey;

export interface StateCopy {
  /** The short label on the banner or row. */
  readonly label: string;
  /** Why the figures are absent, reduced, or annotated. */
  readonly cause: string;
  /** What would change this state — the actionable half FR-044 requires. */
  readonly remedy: string;
  /** Whether this state renders as a page banner or a row label (FR-018a). */
  readonly scope: "page" | "row";
}

/**
 * Keyed by state. `scope: "page"` entries render as banners that compose with a
 * row's label rather than competing with it; `no_active_run` is the one state
 * that is both, echoed onto every row so a client cannot render a figure from a
 * row it forgot to cross-check against the banner.
 */
export const STATE_COPY: Readonly<Record<StateKey, StateCopy>> = {
  no_active_run: {
    label: "No forecast has been run",
    cause:
      "Nothing has produced delivery-risk figures for these lines yet, so this page can only " +
      "list what is outstanding.",
    remedy:
      "Every open line stays listed below with its need-by date. Risk figures appear once a " +
      "forecast run completes and is marked active.",
    scope: "page",
  },

  stale_run: {
    label: "These figures are from an older forecast",
    cause:
      "The active run was fitted against data as of a date more than a refit cadence ago, so it " +
      "has missed at least a week of lifecycle events.",
    remedy:
      "The figures below are still shown, each labelled with the as-of date they were fitted " +
      "against. A newer run replaces them.",
    scope: "page",
  },

  empty_filter: {
    label: "This project filter matched no open lines",
    cause: "The project you selected has nothing outstanding right now.",
    remedy: "Clear the filter or pick a different project to see lines again.",
    scope: "page",
  },

  not_covered: {
    label: "Not included in the current forecast",
    cause:
      "The active run produced no posterior for this line — usually because the line was added " +
      "after the run was fitted.",
    remedy:
      "It is listed here so it is not overlooked, and it joins the ranking after the next " +
      "forecast run covers it.",
    scope: "row",
  },

  beyond_horizon: {
    label: "Need-by date is past the forecast window",
    cause:
      "This need-by date falls further out than the run models day by day, so the chance of " +
      "missing it is known only as an upper bound.",
    remedy:
      "That bound is shown in place of an exact figure. A run anchored nearer this date models " +
      "it directly.",
    scope: "row",
  },

  roster_mismatch: {
    label: "Vendor records changed since this forecast",
    cause:
      "This line's vendor roster differs from the one the active run was fitted against, so any " +
      "figure would describe a different set of suppliers.",
    remedy:
      "No figure is shown for it rather than a misleading one. The next run refits against the " +
      "current roster.",
    scope: "row",
  },

  already_late: {
    label: "Need-by date precedes the forecast anchor",
    cause:
      "This date sits before the day the run was fitted from, so the line was overdue before " +
      "the forecast began and the chance of missing it is one by definition.",
    remedy:
      "The likely-delivery range is still shown, because how much further slip is coming is the " +
      "question that remains open.",
    scope: "row",
  },

  calendar_passed: {
    label: "Need-by date has gone by",
    cause:
      "Today is past this date, though the forecast still models it — the run was anchored " +
      "before the date and it has since passed.",
    remedy:
      "Every figure on this row remains sound and is shown in full; only the calendar has moved.",
    scope: "row",
  },
} as const;

/** FR-018's enumeration, in the order the precedence resolves them. */
export const STATE_KEYS = Object.keys(STATE_COPY) as readonly StateKey[];
