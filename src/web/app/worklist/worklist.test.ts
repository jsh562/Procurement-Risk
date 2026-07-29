import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE_URL, fetchWorklist, WorklistUnavailableError } from "./worklist";

/**
 * The one path data takes into this boundary.
 *
 * FR-024 makes "the interface tier opens no datastore connection" a property to
 * hold rather than a habit, so these assert what the fetch actually does — which
 * URL, with which cache policy, and what happens when it fails.
 */

const stubFetch = (impl: (url: string, init?: RequestInit) => unknown) => {
  const spy = vi.fn(async (url: string, init?: RequestInit) => impl(url, init));
  vi.stubGlobal("fetch", spy);
  return spy;
};

const ok = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: "OK",
  json: async () => body,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchWorklist", () => {
  it("reads the serving boundary and nothing else", async () => {
    const spy = stubFetch(() => ok({ counts: { total: 0 } }));
    await fetchWorklist();

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}/api/v1/worklist`);
    // Every figure is anchored to a run and to a `today` the server resolved,
    // so a cached page shows a state that was true when it was rendered and is
    // silently no longer.
    expect(init).toMatchObject({ cache: "no-store" });
  });

  it("passes a scope and a sort key through as query parameters", async () => {
    const spy = stubFetch(() => ok({}));
    await fetchWorklist({ projectId: "PRJ-002", sort: "need_by_date" });

    expect(spy.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/v1/worklist?project_id=PRJ-002&sort=need_by_date`,
    );
  });

  it("omits the query string entirely when nothing is scoped or sorted", async () => {
    const spy = stubFetch(() => ok({}));
    await fetchWorklist({});
    expect(spy.mock.calls[0][0]).not.toContain("?");
  });

  it("raises rather than returning an empty result when the endpoint is unreachable", async () => {
    // FR-043. An outage is a fault, never a ninth degraded state. Swallowing it
    // into an empty worklist would say "nothing is outstanding", which is the
    // most damaging thing this surface could say incorrectly.
    stubFetch(() => {
      throw new Error("ECONNREFUSED");
    });

    await expect(fetchWorklist()).rejects.toBeInstanceOf(WorklistUnavailableError);
    await expect(fetchWorklist()).rejects.toThrow("ECONNREFUSED");
  });

  it("raises on a non-OK response and names the status", async () => {
    stubFetch(() => ({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({}),
    }));

    await expect(fetchWorklist()).rejects.toThrow("503");
  });

  it("reports a thrown non-Error without pretending to know its message", async () => {
    stubFetch(() => {
      throw "not an Error";
    });

    await expect(fetchWorklist()).rejects.toThrow("could not be reached");
  });
});
