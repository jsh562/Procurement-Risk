import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import WorklistPage from "./page";
import { STATE_COPY } from "./stateCopy";
import type { WorklistResponse } from "./worklist";

/**
 * The worklist page in the no-active-run state (FR-015, US3 scenario 1).
 *
 * Rendered to markup and asserted over the document order, because the two
 * properties that matter here are both properties of *order*: the banner
 * precedes any row content, and no figure appears anywhere. A query-by-role
 * assertion would confirm the banner exists and say nothing about where.
 */

const RESPONSE: WorklistResponse = {
  meta: {
    generated_at: "2026-06-03T09:00:00Z",
    today: "2026-06-03",
    timezone: "America/New_York",
    forecast_run: null,
    conventions: {
      draw_count: 4000,
      percentile_convention: "nearest_rank_one_based_no_interpolation",
      anchor_date_convention: "run_as_of_date",
    },
  },
  scope: { project_id: null, available_projects: [{ project_id: "PRJ-001", open_line_count: 2 }] },
  sort: {
    key: "expected_harm",
    direction: "desc",
    tiebreak: ["need_by_date asc", "criticality desc", "po_line_id asc"],
    options: [
      { key: "expected_harm", direction: "desc", is_default: true, is_active: true },
      { key: "need_by_date", direction: "asc", is_default: false, is_active: false },
      { key: "criticality", direction: "desc", is_default: false, is_active: false },
      { key: "calendar_margin", direction: "asc", is_default: false, is_active: false },
    ],
  },
  page_states: ["no_active_run"],
  ranked: [],
  unranked: [
    {
      po_line_id: "1a5d3e70-c2b8-4f6a-9d21-0e77b4c81f55",
      state: "no_active_run",
      primary: {
        identity: {
          project_id: "PRJ-001",
          po_number: "PO-4471",
          line_number: 3,
          description: "Air handling unit AHU-3, 12000 CFM",
        },
        need_by: {
          date: "2026-08-10",
          date_of_record: "2026-08-10",
          source: "record",
          unsaved: false,
        },
      },
    },
  ],
  counts: { ranked: 0, unranked: 1, total: 1 },
  ordering_digest: "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
};

const renderWith = async (body: unknown, ok = true): Promise<string> => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok,
      status: ok ? 200 : 503,
      statusText: ok ? "OK" : "Service Unavailable",
      json: async () => body,
    })),
  );
  return renderToStaticMarkup(await WorklistPage());
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("worklist page, no active run", () => {
  it("puts the banner before any row content", async () => {
    // FR-015. A banner after the list is one a coordinator reads only after
    // they have already drawn a conclusion from what is missing.
    const markup = await renderWith(RESPONSE);
    const bannerAt = markup.indexOf(STATE_COPY.no_active_run.label);
    const firstRowAt = markup.indexOf("PO-4471");

    expect(bannerAt).toBeGreaterThanOrEqual(0);
    expect(firstRowAt).toBeGreaterThanOrEqual(0);
    expect(bannerAt).toBeLessThan(firstRowAt);
  });

  it("states the cause and the remedy, not just the label", async () => {
    // FR-044. The remedy is the difference between a refusal a coordinator can
    // act on and a dead end.
    const markup = await renderWith(RESPONSE);
    expect(markup).toContain(STATE_COPY.no_active_run.cause);
    expect(markup).toContain(STATE_COPY.no_active_run.remedy);
  });

  it("still lists every open line with its identity and need-by date", async () => {
    // FR-015. The coordinator's inventory of what is outstanding does not
    // vanish with the forecast.
    const markup = await renderWith(RESPONSE);
    expect(markup).toContain("PRJ-001 · PO-4471-3");
    expect(markup).toContain("2026-08-10");
  });

  it("renders no risk figure anywhere", async () => {
    // SC-007. Asserted over the whole document rather than per element: a
    // placeholder that slipped into a region this test did not name is exactly
    // the failure mode.
    const markup = await renderWith(RESPONSE);
    expect(markup).not.toMatch(/\d+(\.\d+)?\s*%/);
    expect(markup).not.toMatch(/\bp(50|80)\b/i);
  });

  it("carries the row's state as text, not as a tint", async () => {
    // FR-050. A state carried only by colour is invisible to a coordinator who
    // cannot see it, and to every automated check of SC-009's wording rule.
    const markup = await renderWith(RESPONSE);
    expect(markup).toContain(STATE_COPY.no_active_run.label);
  });

  it("says which day and zone the states were resolved against", async () => {
    // FR-038. A coordinator elsewhere would otherwise assume their own clock.
    const markup = await renderWith(RESPONSE);
    expect(markup).toContain("2026-06-03");
    expect(markup).toContain("America/New_York");
  });

  it("reports an unreachable endpoint as a fault, never as an empty worklist", async () => {
    // FR-043. "Nothing is outstanding" is the most damaging thing this page
    // could say incorrectly, so an outage must not render as one.
    const markup = await renderWith(null, false);
    expect(markup).toContain("could not be loaded");
    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain("No open purchase-order lines are in scope.");
  });

  it("distinguishes an empty worklist from a failed one", async () => {
    // The other half of the pair above. With nothing outstanding the page says
    // so plainly — and that statement is only safe to make because the failure
    // path above never reaches it.
    const markup = await renderWith({
      ...RESPONSE,
      unranked: [],
      counts: { ranked: 0, unranked: 0, total: 0 },
    });
    expect(markup).toContain("No open purchase-order lines are in scope.");
    expect(markup).not.toContain('role="alert"');
  });

  it("names the run and its as-of date once one is active", async () => {
    // FR-019, FR-052. The as-of date is row text reachable without hover, and
    // the model version is what resolves a figure to the artifact behind it.
    const markup = await renderWith({
      ...RESPONSE,
      meta: {
        ...RESPONSE.meta,
        forecast_run: {
          run_id: "3f7c2b90-5a44-4e11-8b0a-6d9e1c33a201",
          as_of_date: "2026-06-01",
          horizon_days: 365,
          roster_hash: "sha256:" + "a".repeat(64),
          model_version: "lognormal-hierarchical-v3",
          artifact_schema_version: 1,
          age_days: 2,
          stale: false,
          staleness_threshold_days: 7,
          staleness_basis: "One refit cadence.",
        },
      },
      page_states: [],
    });

    expect(markup).toContain("lognormal-hierarchical-v3");
    expect(markup).toContain("2026-06-01");
    expect(markup).not.toContain("No forecast run is active.");
  });

  it("renders one banner per page state in force", async () => {
    // FR-018a. Page states compose rather than compete: a stale run and an empty
    // filter are both true at once and each has its own copy to show.
    const markup = await renderWith({
      ...RESPONSE,
      page_states: ["stale_run", "empty_filter"],
      unranked: [],
      counts: { ranked: 0, unranked: 0, total: 0 },
    });

    expect(markup).toContain(STATE_COPY.stale_run.label);
    expect(markup).toContain(STATE_COPY.empty_filter.label);
  });
});
