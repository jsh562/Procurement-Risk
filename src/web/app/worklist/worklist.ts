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

export interface RankedRow {
  readonly po_line_id: string;
  readonly rank: number;
  readonly state: RankedRowState;
  readonly primary: UnrankedPrimary & Record<string, unknown>;
  readonly secondary: Record<string, unknown>;
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
  readonly ordering_digest: string;
}

/**
 * Where the serving boundary is. A base URL rather than a database URL: the
 * shape of this constant is itself the FR-024 boundary.
 */
export const API_BASE_URL = process.env.WORKLIST_API_BASE_URL ?? "http://localhost:8000";

/** Raised when the endpoint could not be read at all — FR-043's condition. */
export class WorklistUnavailableError extends Error {
  constructor(readonly cause_detail: string) {
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
  params: { readonly projectId?: string; readonly sort?: string } = {},
): Promise<WorklistResponse> {
  const query = new URLSearchParams();
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.sort) query.set("sort", params.sort);
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
    throw new WorklistUnavailableError(
      `the serving boundary answered ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as WorklistResponse;
}
