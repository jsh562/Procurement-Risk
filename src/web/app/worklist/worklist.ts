/**
 * The worklist's response shape, and the one function that fetches it.
 *
 * FR-024. Data reaches this boundary through the request-serving endpoint and
 * no other way — there is no database driver in this package's dependency tree
 * and no connection string in its environment, so "the interface tier opens no
 * datastore connection" is a property of what is installed rather than a rule
 * someone remembers. FR-035's import contract asserts the same boundary from
 * the other side.
 *
 * The types mirror `contracts/openapi.yaml` §WorklistResponse. They are written
 * by hand rather than generated because the generated form would restate the
 * contract's structure without its reasoning, and the reasoning is what tells
 * the next reader why `UnrankedPrimary` has no property a figure could occupy.
 */

/** FR-018's three page-scope states. */
export type PageState = "no_active_run" | "stale_run" | "empty_filter";

/** The row states that exclude a line from the ranking (FR-016, FR-021). */
export type UnrankedRowState = "roster_mismatch" | "not_covered" | "no_active_run";

/** The row states that keep a line ranked, plus `nominal` (FR-017, FR-030). */
export type RankedRowState = "nominal" | "beyond_horizon" | "already_late" | "calendar_passed";

export interface LineIdentity {
  readonly project_id: string;
  readonly po_number: string;
  readonly line_number: number;
  readonly description: string;
}

export interface NeedBy {
  readonly date: string;
  readonly date_of_record: string;
  readonly source: "record" | "session_override";
  readonly unsaved: boolean;
}

/**
 * What an excluded line shows: identity and need-by date, and nothing that
 * could hold a figure (FR-016, FR-054). The absence of a `probability` property
 * is the point — a nullable one renders a dash, and a dash reads as a figure.
 */
export interface UnrankedPrimary {
  readonly identity: LineIdentity;
  readonly need_by: NeedBy;
}

export interface UnrankedRow {
  readonly po_line_id: string;
  readonly state: UnrankedRowState;
  readonly primary: UnrankedPrimary;
}

/**
 * One direction of a probability, already rounded (FR-053).
 *
 * `percent` is `null` exactly in the bounded form, so no numeral survives
 * beside `<1%` or `>99%`. The integer is retained alongside the string for
 * assistive technology and for tests; the string is what renders, because the
 * arithmetic already happened once on the deterministic side of the boundary.
 */
export interface PercentFigure {
  readonly percent: number | null;
  readonly display: string;
}

/**
 * FR-006's dual framing. `measure` travels with the figure rather than being
 * inferred from the row's state — the two are rendered in different words, and
 * inferring it would put two rules that must agree in different tiers (FR-017).
 */
export interface MissProbability {
  readonly measure: "point" | "upper_bound";
  readonly bounded: boolean;
  readonly miss: PercentFigure;
  readonly on_time: PercentFigure;
}

export interface QuantileFigure {
  readonly quantile_percent: number;
  readonly days: number;
  readonly later_percent: number;
}

/**
 * FR-003, FR-004. One labelled pair, nested under a single object rather than
 * carried as two sibling scalars — which is what makes "not independently
 * sortable" structural rather than a rule the interface has to remember.
 */
export interface DurationPair {
  readonly unit: string;
  readonly counted_from: string;
  readonly as_of_date: string;
  readonly median: QuantileFigure;
  readonly eightieth: QuantileFigure;
  readonly reference_class: {
    readonly basis: string;
    readonly draw_count: number;
    readonly percentile_convention: string;
  };
}

/** FR-027's four comparison quantities, in FR-032's reading order. */
export interface PrimaryFigures {
  readonly identity: LineIdentity;
  readonly need_by: NeedBy;
  readonly miss_probability: MissProbability | null;
  readonly duration_pair: DurationPair;
}

/**
 * FR-009's explanatory context — exactly three members. Closed at three so a
 * fifth scannable figure cannot be parked here instead of in `primary`, which
 * is what makes FR-027's cap decidable at all. Expected harm is absent by
 * design: with criticality beside it, the score would surrender the mean
 * overrun to one division (FR-041).
 */
export interface SecondaryContext {
  readonly as_of_date: string;
  readonly criticality: number;
  readonly calendar_margin_days: number;
}

export interface RankedRow {
  readonly po_line_id: string;
  readonly rank: number;
  readonly state: RankedRowState;
  readonly primary: PrimaryFigures;
  readonly secondary: SecondaryContext;
}

export interface ForecastRunMeta {
  readonly run_id: string;
  readonly as_of_date: string;
  readonly horizon_days: number;
  readonly roster_hash: string;
  readonly model_version: string;
  readonly artifact_schema_version: number;
  readonly age_days: number;
  readonly stale: boolean;
  readonly staleness_threshold_days: number;
  readonly staleness_basis: string;
}

export interface WorklistResponse {
  readonly meta: {
    readonly generated_at: string;
    readonly today: string;
    readonly timezone: string;
    readonly forecast_run: ForecastRunMeta | null;
    readonly conventions: {
      readonly draw_count: number;
      readonly percentile_convention: string;
      readonly anchor_date_convention: string;
    };
  };
  readonly scope: {
    readonly project_id: string | null;
    readonly available_projects: ReadonlyArray<{
      readonly project_id: string;
      readonly open_line_count: number;
    }>;
  };
  readonly sort: {
    readonly key: string;
    readonly direction: string;
    readonly tiebreak: readonly string[];
    readonly options: ReadonlyArray<{
      readonly key: string;
      readonly direction: string;
      readonly is_default: boolean;
      readonly is_active: boolean;
    }>;
  };
  readonly page_states: readonly PageState[];
  readonly ranked: readonly RankedRow[];
  readonly unranked: readonly UnrankedRow[];
  readonly counts: {
    readonly ranked: number;
    readonly unranked: number;
    readonly total: number;
  };
  /**
   * FR-055. What the session what-if actually did. An override naming a line
   * this response does not contain is reported with its cause and never
   * silently dropped — the coordinator would otherwise believe an adjustment
   * took effect while reading an ordering computed without it.
   */
  readonly overrides: {
    readonly applied: ReadonlyArray<{
      readonly po_line_id: string;
      readonly need_by_date: string;
    }>;
    readonly unapplied: ReadonlyArray<{
      readonly po_line_id: string;
      readonly need_by_date: string;
      readonly reason: "line_not_found" | "line_terminal" | "line_out_of_scope";
    }>;
  };
  readonly ordering_digest: string;
}

/**
 * Where the serving boundary is. A base URL rather than a database URL: the
 * shape of this constant is itself the FR-024 boundary.
 *
 * **Two variables, deliberately.** This module runs in two places — the server
 * component's first fetch happens in Node, and every adjustment's re-query
 * happens in the browser — and the two do not necessarily reach the boundary at
 * the same address. Next.js only inlines `NEXT_PUBLIC_`-prefixed variables into
 * client bundles, so a single server-side variable silently becomes `undefined`
 * in the browser and every adjustment falls back to a default host. That failure
 * is invisible to a unit test, which stubs `fetch` and never evaluates the URL
 * against a real server; the end-to-end run is what surfaced it.
 *
 * The browser value is read at module scope rather than inside the fetch, so an
 * environment that forgot to set it fails the same way on every request rather
 * than on the first adjustment a coordinator happens to make.
 */
const SERVER_BASE_URL = process.env.WORKLIST_API_BASE_URL ?? "http://localhost:8000";
const BROWSER_BASE_URL =
  process.env.NEXT_PUBLIC_WORKLIST_API_BASE_URL ?? SERVER_BASE_URL ?? "http://localhost:8000";

export const API_BASE_URL = typeof window === "undefined" ? SERVER_BASE_URL : BROWSER_BASE_URL;

/**
 * Raised when the worklist could not be read at all — FR-043's three
 * conditions.
 *
 * `correlationId` is what a coordinator can quote off the screen and an
 * engineer can find in the record. Without it the report is "the worklist was
 * broken this morning" and the matching log line has to be found by timestamp.
 *
 * `title` carries the server's own wording where it supplied one, because the
 * three conditions are not interchangeable: "the datastore is unreachable" and
 * "this run was written by a schema this build does not know" name different
 * things to do about them, and a single generic message names neither.
 */
export class WorklistUnavailableError extends Error {
  constructor(
    readonly cause_detail: string,
    readonly correlationId: string | null = null,
    readonly title: string | null = null,
  ) {
    super(cause_detail);
    this.name = "WorklistUnavailableError";
  }
}

/**
 * Fetch one worklist.
 *
 * `cache: "no-store"` because every figure is anchored to a forecast run and a
 * `today` the server resolved; a cached page would show a state that was true
 * when it was rendered and is silently no longer.
 *
 * A failure to read is deliberately *not* caught here into an empty result.
 * FR-043 makes it a fault rather than a ninth degraded state, and the whole
 * feature turns on an outage being distinguishable from an honest empty state.
 */
export async function fetchWorklist(
  params: {
    readonly projectId?: string;
    readonly sort?: string;
    /** FR-031's session what-ifs, each `<po_line_id>:<YYYY-MM-DD>`. */
    readonly overrides?: readonly string[];
  } = {},
): Promise<WorklistResponse> {
  const query = new URLSearchParams();
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.sort) query.set("sort", params.sort);
  // Appended rather than set: the parameter repeats, because FR-031 admits a
  // set and a coordinator comparing two lines needs both dates moved at once.
  for (const override of params.overrides ?? []) query.append("need_by_override", override);
  const suffix = query.size > 0 ? `?${query}` : "";

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/worklist${suffix}`, { cache: "no-store" });
  } catch (error) {
    throw new WorklistUnavailableError(
      error instanceof Error ? error.message : "the serving boundary could not be reached",
    );
  }

  if (!response.ok) {
    // The problem document, where the server sent one. Read defensively: a
    // proxy returning a 502 with an HTML body is exactly the case where the
    // parse fails, and losing the failure to a parse error would be worse than
    // reporting it without its detail.
    let detail: { title?: string; detail?: string; correlation_id?: string } | undefined;
    try {
      detail = (await response.json())?.detail;
    } catch {
      detail = undefined;
    }

    throw new WorklistUnavailableError(
      detail?.detail ?? `the serving boundary answered ${response.status} ${response.statusText}`,
      detail?.correlation_id ?? null,
      detail?.title ?? null,
    );
  }
  return (await response.json()) as WorklistResponse;
}
