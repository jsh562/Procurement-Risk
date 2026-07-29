import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorklistBoard } from "./WorklistBoard";
import type { RankedRow, WorklistResponse } from "./worklist";

/**
 * The adjustment loop.
 *
 * FR-011, FR-012, FR-046, FR-051. What is under test here is not the
 * arithmetic — that lives on the server and is tested there — but the three
 * things a coordinator can only learn from the interface: that their adjustment
 * was applied, whether the order moved, and where they are now.
 *
 * The two acknowledgements are the crux. A reorder announced to nobody is the
 * same failure for a coordinator using assistive technology that an
 * unacknowledged no-op is for one reading the page visually: the list moves and
 * nothing is said.
 */

const row = (id: string, rank: number, needBy: string): RankedRow => ({
  po_line_id: id,
  rank,
  state: "nominal",
  primary: {
    identity: {
      project_id: "PRJ-001",
      po_number: `PO-${id}`,
      line_number: 1,
      description: `Line ${id}`,
    },
    need_by: { date: needBy, date_of_record: needBy, source: "record", unsaved: false },
    miss_probability: {
      measure: "point",
      bounded: false,
      miss: { percent: 40, display: "40%" },
      on_time: { percent: 60, display: "60%" },
    },
    duration_pair: {
      unit: "days",
      counted_from: "run_as_of_date",
      as_of_date: "2026-06-01",
      median: { quantile_percent: 50, days: 30, later_percent: 50 },
      eightieth: { quantile_percent: 80, days: 45, later_percent: 20 },
      reference_class: {
        basis: "posterior_predictive_draws",
        draw_count: 4000,
        percentile_convention: "nearest_rank_one_based_no_interpolation",
      },
    },
  },
  secondary: { as_of_date: "2026-06-01", criticality: 3, calendar_margin_days: 30 },
});

const response = (
  rows: readonly RankedRow[],
  digest: string,
  overrides: WorklistResponse["overrides"] = { applied: [], unapplied: [] },
): WorklistResponse => ({
  meta: {
    generated_at: "2026-06-03T09:00:00Z",
    today: "2026-06-03",
    timezone: "UTC",
    forecast_run: null,
    conventions: {
      draw_count: 4000,
      percentile_convention: "nearest_rank_one_based_no_interpolation",
      anchor_date_convention: "run_as_of_date",
    },
  },
  scope: { project_id: null, available_projects: [] },
  sort: {
    key: "expected_harm",
    direction: "desc",
    tiebreak: ["need_by_date asc", "criticality desc", "po_line_id asc"],
    options: [],
  },
  page_states: [],
  ranked: rows,
  unranked: [],
  counts: { ranked: rows.length, unranked: 0, total: rows.length },
  overrides,
  ordering_digest: digest,
});

const A = "aaaaaaaa-0000-0000-0000-000000000001";
const B = "bbbbbbbb-0000-0000-0000-000000000002";

const INITIAL = response([row(A, 1, "2026-08-10"), row(B, 2, "2026-09-10")], "sha256:first");

const stubNext = (body: WorklistResponse) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, statusText: "OK", json: async () => body })),
  );

const adjust = async (poLineId: string, value: string) => {
  const input = screen.getByLabelText("Try a different need-by date", {
    selector: `#need-by-${poLineId}`,
  });
  await act(async () => {
    fireEvent.change(input, { target: { value } });
  });
  return input;
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("adjusting a need-by date", () => {
  it("acknowledges an adjustment that changes no ordering", async () => {
    // FR-012. An unchanged list is indistinguishable from an ignored input
    // unless something says otherwise, and a coordinator unable to tell whether
    // their adjustment applied is the exact failure this prevents.
    stubNext(response(INITIAL.ranked, "sha256:first"));
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2026-08-05");

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /applied/i,
      );
    });
    expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
      /order is unchanged/i,
    );
  });

  it("acknowledges a reorder and names the new position", async () => {
    // FR-046. "It moved" is not actionable; the coordinator needs to know where
    // to look, especially having just lost their place in a list that shifted.
    stubNext(response([row(B, 1, "2026-09-10"), row(A, 2, "2026-08-10")], "sha256:second"));
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2026-07-01");

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /order changed/i,
      );
    });
    expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
      /position 2/i,
    );
  });

  it("makes the two acknowledgements distinguishable from each other", async () => {
    // FR-046. Which of the two happened must never be left to be inferred from
    // the rows — that is precisely the inference a coordinator cannot make when
    // they do not already know the previous order.
    stubNext(response(INITIAL.ranked, "sha256:first"));
    const view = render(<WorklistBoard initial={INITIAL} />);
    await adjust(A, "2026-08-05");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /unchanged/i,
      ),
    );
    const unchanged = screen.getByRole("status", { name: "Adjustment status" }).textContent;

    view.unmount();
    vi.unstubAllGlobals();
    stubNext(response([row(B, 1, "2026-09-10"), row(A, 2, "2026-08-10")], "sha256:second"));
    render(<WorklistBoard initial={INITIAL} />);
    await adjust(A, "2026-07-01");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /changed/i,
      ),
    );

    expect(screen.getByRole("status", { name: "Adjustment status" }).textContent).not.toBe(
      unchanged,
    );
  });

  it("keeps focus on the adjusted control after the list reorders", async () => {
    // FR-046. Focus returning to the top of a list that has just moved leaves
    // the coordinator to find their place again — in a list whose order is
    // exactly what they just changed.
    stubNext(response([row(B, 1, "2026-09-10"), row(A, 2, "2026-08-10")], "sha256:second"));
    render(<WorklistBoard initial={INITIAL} />);

    const input = await adjust(A, "2026-07-01");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /applied/i,
      ),
    );
    expect(document.activeElement).toBe(input);
  });

  it("does not expire the acknowledgement on a timer", async () => {
    // FR-012. Persistent rather than a timed notification, because a message
    // that disappears is the documented way an acknowledgement is missed.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      stubNext(response(INITIAL.ranked, "sha256:first"));
      render(<WorklistBoard initial={INITIAL} />);
      await adjust(A, "2026-08-05");
      await waitFor(() =>
        expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
          /applied/i,
        ),
      );

      await act(async () => {
        vi.advanceTimersByTime(120_000);
      });
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /applied/i,
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets the coordinator dismiss the acknowledgement", async () => {
    // FR-012's stated bound on "persistent": until the next adjustment replaces
    // it, the coordinator dismisses it, or the page reloads.
    stubNext(response(INITIAL.ranked, "sha256:first"));
    render(<WorklistBoard initial={INITIAL} />);
    await adjust(A, "2026-08-05");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /applied/i,
      ),
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    });
    expect(screen.getByRole("status", { name: "Adjustment status" })).toBeEmptyDOMElement();
  });

  it("reports an adjustment the server did not apply, with its cause", async () => {
    // FR-055. Silently dropping it leaves the coordinator believing an
    // adjustment took effect while reading an ordering computed without it.
    stubNext(
      response(INITIAL.ranked, "sha256:first", {
        applied: [],
        unapplied: [{ po_line_id: A, need_by_date: "2026-08-05", reason: "line_terminal" }],
      }),
    );
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2026-08-05");

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /not applied/i,
      );
    });
    expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
      /already been delivered/i,
    );
  });

  it("says so rather than leaving a stale order on screen when the request fails", async () => {
    // A failed recalculation that changed nothing on screen looks exactly like
    // an adjustment that changed nothing — the one reading a coordinator must
    // not make.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    );
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2026-08-05");

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /could not be applied/i,
      );
    });
  });

  it("sends every adjustment in force, not only the latest", async () => {
    // FR-031 admits a set: a coordinator comparing two lines needs both dates
    // moved at once, so the second adjustment must not silently drop the first.
    const spy = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => response(INITIAL.ranked, "sha256:first"),
      requested: url,
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2026-08-05");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /applied/i,
      ),
    );
    await adjust(B, "2026-09-05");

    await waitFor(() => {
      const url = String(spy.mock.calls.at(-1)?.[0]);
      expect(url).toContain(`need_by_override=${encodeURIComponent(`${A}:2026-08-05`)}`);
      expect(url).toContain(`need_by_override=${encodeURIComponent(`${B}:2026-09-05`)}`);
    });
  });

  it("says the line is no longer ranked when an adjustment excludes it", async () => {
    // A need-by date pushed past the horizon, or one that lands on a state
    // suppressing every figure, moves the line out of the ranking entirely.
    // "Now at position null" would be worse than saying nothing; the
    // acknowledgement has to name the outcome that actually happened.
    stubNext(response([row(B, 1, "2026-09-10")], "sha256:second"));
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "2030-01-01");

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /not ranked/i,
      );
    });
  });

  it("ignores a cleared date rather than sending an empty adjustment", async () => {
    // Clearing the field is how a coordinator backs out of typing a date, not
    // a request to re-rank against nothing — and an empty value would be
    // refused by the server as malformed, turning a keystroke into an error.
    const spy = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => response(INITIAL.ranked, "sha256:first"),
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={INITIAL} />);

    await adjust(A, "");

    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("status", { name: "Adjustment status" })).toBeEmptyDOMElement();
  });

  it("keeps the active scope and sort when re-querying after an adjustment", async () => {
    // A coordinator who filtered to one project has not withdrawn the filter by
    // asking a what-if question inside it — and a request that dropped the
    // scope would answer with a list they did not ask for.
    const scoped: WorklistResponse = {
      ...INITIAL,
      scope: { project_id: "PRJ-002", available_projects: [] },
      sort: { ...INITIAL.sort, key: "criticality" },
    };
    const spy = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => scoped,
      requested: url,
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={scoped} />);

    await adjust(A, "2026-08-05");

    await waitFor(() => {
      const url = String(spy.mock.calls.at(-1)?.[0]);
      expect(url).toContain("project_id=PRJ-002");
      expect(url).toContain("sort=criticality");
    });
  });

  it("offers a keyboard-operable native date control", () => {
    // FR-051. A native input is keyboard-operable, announces itself and accepts
    // typed input without any of that being reimplemented — which is where
    // custom pickers usually lose one of the three.
    render(<WorklistBoard initial={INITIAL} />);
    const input = screen.getByLabelText("Try a different need-by date", {
      selector: `#need-by-${A}`,
    });
    expect(input).toHaveAttribute("type", "date");
    expect(input.tagName).toBe("INPUT");
  });
});

describe("scoping and sorting", () => {
  const WITH_PROJECTS: WorklistResponse = {
    ...INITIAL,
    scope: {
      project_id: null,
      available_projects: [
        { project_id: "PRJ-001", open_line_count: 12 },
        { project_id: "PRJ-002", open_line_count: 0 },
      ],
    },
    sort: {
      ...INITIAL.sort,
      options: [
        { key: "expected_harm", direction: "desc", is_default: true, is_active: true },
        { key: "need_by_date", direction: "asc", is_default: false, is_active: false },
        { key: "criticality", direction: "desc", is_default: false, is_active: false },
        { key: "calendar_margin", direction: "asc", is_default: false, is_active: false },
      ],
    },
  };

  it("offers exactly FR-026's four keys and no delivery-date or single-quantile key", () => {
    // FR-026, FR-032. The absence is the requirement: a key ordering lines by a
    // single delivery date, or by one quantile alone, is the point estimate
    // re-entering through the sort control.
    render(<WorklistBoard initial={WITH_PROJECTS} />);
    const options = [...screen.getByLabelText("Order by").querySelectorAll("option")].map(
      (option) => option.getAttribute("value"),
    );

    expect(options).toEqual(["expected_harm", "need_by_date", "criticality", "calendar_margin"]);
    for (const forbidden of ["p50", "p80", "delivery_date", "median", "eightieth"]) {
      expect(options).not.toContain(forbidden);
    }
  });

  it("takes the offered keys from the response rather than a list of its own", () => {
    // FR-032 makes the claim testable against the response; a hard-coded list
    // here would be a second source of truth that agrees until it does not.
    render(
      <WorklistBoard
        initial={{
          ...WITH_PROJECTS,
          sort: {
            ...WITH_PROJECTS.sort,
            options: [
              { key: "vendor_reliability", direction: "asc", is_default: false, is_active: true },
            ],
          },
        }}
      />,
    );
    const options = [...screen.getByLabelText("Order by").querySelectorAll("option")].map(
      (option) => option.getAttribute("value"),
    );
    expect(options).toEqual(["vendor_reliability"]);
  });

  it("offers every project with its open-line count, plus a way out of the filter", () => {
    // FR-025. The full set even while a filter is active, so the scope can be
    // left without a second request — and the count because a project holding
    // nothing and one simply not selected are otherwise indistinguishable from
    // inside a filtered list.
    render(
      <WorklistBoard
        initial={{ ...WITH_PROJECTS, scope: { ...WITH_PROJECTS.scope, project_id: "PRJ-001" } }}
      />,
    );
    const select = screen.getByLabelText("Project") as HTMLSelectElement;

    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "All projects",
      "PRJ-001 (12 open)",
      "PRJ-002 (0 open)",
    ]);
    expect(select.value).toBe("PRJ-001");
  });

  it("re-queries under a chosen scope", async () => {
    const spy = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => WITH_PROJECTS,
      requested: url,
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={WITH_PROJECTS} />);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Project"), { target: { value: "PRJ-002" } });
    });

    await waitFor(() => {
      expect(String(spy.mock.calls.at(-1)?.[0])).toContain("project_id=PRJ-002");
    });
  });

  it("re-queries under a chosen sort key", async () => {
    const spy = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => WITH_PROJECTS,
      requested: url,
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={WITH_PROJECTS} />);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Order by"), { target: { value: "need_by_date" } });
    });

    await waitFor(() => {
      expect(String(spy.mock.calls.at(-1)?.[0])).toContain("sort=need_by_date");
    });
  });

  it("says so rather than leaving a stale list when a re-query fails", async () => {
    // The list on screen would otherwise answer a question the coordinator did
    // not ask — the filter they chose, applied to rows from the one they left.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    );
    render(<WorklistBoard initial={WITH_PROJECTS} />);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Project"), { target: { value: "PRJ-002" } });
    });

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Adjustment status" })).toHaveTextContent(
        /could not be reloaded/i,
      );
    });
  });

  it("does not raise an adjustment acknowledgement for a sort the coordinator chose", async () => {
    // FR-012's message is about an adjustment. A reorder the coordinator asked
    // for by changing the key needs no announcement that the order changed —
    // they changed it, and saying so would train them to ignore the region.
    const spy = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => response([row(B, 1, "2026-09-10")], "sha256:different"),
    }));
    vi.stubGlobal("fetch", spy);
    render(<WorklistBoard initial={WITH_PROJECTS} />);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Order by"), { target: { value: "criticality" } });
    });

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.getByRole("status", { name: "Adjustment status" })).toBeEmptyDOMElement();
  });
});
