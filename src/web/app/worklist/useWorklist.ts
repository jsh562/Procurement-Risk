"use client";

import { useCallback, useRef, useState } from "react";
import { fetchWorklist, type WorklistResponse } from "./worklist";

/**
 * The session's adjustment set, and what happened when it last changed.
 *
 * FR-011, FR-012, FR-046. Three obligations meet here:
 *
 * **The adjustment goes to the server.** Re-ranking needs the mean over the
 * line's stored draws of the overrun past the new date and a fresh survival
 * lookup at the new offset — arithmetic that would require shipping four
 * thousand draws per line to do here, and would hand the client the arrays from
 * which a single delivery date is one aggregation away.
 *
 * **Every adjustment is acknowledged, including one that changes nothing.** A
 * coordinator unable to tell whether their adjustment applied is the exact
 * failure FR-012 exists to prevent, and an unchanged list is indistinguishable
 * from an ignored input unless something says so.
 *
 * **The two acknowledgements are distinguishable.** FR-046. Which of the two
 * happened must never be left to be inferred from the rows — that is precisely
 * the inference a coordinator cannot make when they do not already know the
 * previous order.
 *
 * Whether the order changed is decided by comparing `ordering_digest`, which the
 * server computes over the ranked group's ordered identifier sequence. Deriving
 * it here would mean re-deriving an equality the server already knows, and two
 * consumers would each derive it slightly differently.
 */

export type AcknowledgementKind = "unchanged" | "reordered" | "refused";

export interface Acknowledgement {
  readonly kind: AcknowledgementKind;
  /** The text placed in the live region. */
  readonly message: string;
  /** The line the adjustment named, so focus can return to its control. */
  readonly poLineId: string | null;
}

export interface UseWorklist {
  readonly worklist: WorklistResponse;
  readonly overrides: ReadonlyMap<string, string>;
  readonly acknowledgement: Acknowledgement | null;
  readonly pending: boolean;
  readonly adjust: (poLineId: string, needByDate: string) => Promise<void>;
  /** FR-025, FR-026. A scope or sort change re-queries under the same overrides. */
  readonly rescope: (projectId: string | null) => Promise<void>;
  readonly resort: (sortKey: string) => Promise<void>;
  readonly clearAcknowledgement: () => void;
}

/** FR-046. The new position, named — "it moved" alone is not actionable. */
const positionOf = (worklist: WorklistResponse, poLineId: string): number | null =>
  worklist.ranked.find((row) => row.po_line_id === poLineId)?.rank ?? null;

export function useWorklist(initial: WorklistResponse): UseWorklist {
  const [worklist, setWorklist] = useState(initial);
  const [overrides, setOverrides] = useState<ReadonlyMap<string, string>>(new Map());
  const [acknowledgement, setAcknowledgement] = useState<Acknowledgement | null>(null);
  const [pending, setPending] = useState(false);
  // The active scope and sort. Held here rather than read from `worklist` on
  // each call so a request in flight cannot be built from a response that has
  // not arrived yet.
  const [projectId, setProjectId] = useState<string | null>(initial.scope.project_id);
  const [sortKey, setSortKey] = useState(initial.sort.key);

  // The digest of the ordering the coordinator is currently looking at. Held in
  // a ref rather than derived from `worklist` at compare time so a re-render
  // between the request and its response cannot change what is compared.
  const previousDigest = useRef(initial.ordering_digest);

  const adjust = useCallback(
    async (poLineId: string, needByDate: string) => {
      const next = new Map(overrides);
      next.set(poLineId, needByDate);
      setPending(true);

      let response: WorklistResponse;
      try {
        response = await fetchWorklist({
          projectId: projectId ?? undefined,
          sort: sortKey,
          overrides: [...next].map(([id, date]) => `${id}:${date}`),
        });
      } catch {
        // FR-055's spirit on the client side: a refusal reaches the coordinator
        // rather than leaving the previous ordering on screen looking like an
        // answer to the new question.
        setPending(false);
        setAcknowledgement({
          kind: "refused",
          message:
            `The adjustment to ${needByDate} could not be applied — the worklist could not be ` +
            "recalculated. The dates and order below are unchanged from before your change.",
          poLineId,
        });
        return;
      }

      const reordered = response.ordering_digest !== previousDigest.current;
      previousDigest.current = response.ordering_digest;

      setWorklist(response);
      setOverrides(next);
      setPending(false);

      const unapplied = response.overrides.unapplied.find((item) => item.po_line_id === poLineId);
      if (unapplied) {
        // FR-055. Reported with its cause, never silently dropped: otherwise
        // the coordinator believes an adjustment took effect while reading an
        // ordering computed without it.
        setAcknowledgement({
          kind: "refused",
          message: `The adjustment was not applied: ${REASON_TEXT[unapplied.reason]}`,
          poLineId,
        });
        return;
      }

      const position = positionOf(response, poLineId);
      setAcknowledgement(
        reordered
          ? {
              kind: "reordered",
              message:
                `Adjustment applied. The order changed — this line is now ` +
                `${position === null ? "not ranked" : `at position ${position}`}.`,
              poLineId,
            }
          : {
              // FR-012. Persistent, and distinguishable from the reordered
              // wording by more than a single word, so which of the two
              // happened is never inferred from the rows.
              kind: "unchanged",
              message:
                "Adjustment applied. The order is unchanged — this line keeps the same " +
                `position${position === null ? "" : `, ${position}`}.`,
              poLineId,
            },
      );
    },
    [overrides, projectId, sortKey],
  );

  /**
   * FR-012's bound on "persistent". The message remains until the next
   * adjustment replaces it, the coordinator dismisses it, or the page reloads —
   * what it must never do is expire on a timer, which is the documented way an
   * acknowledgement is missed.
   */
  const clearAcknowledgement = useCallback(() => setAcknowledgement(null), []);

  /**
   * FR-025, FR-026. Re-query under a new scope or sort key.
   *
   * The adjustment set travels along, because a coordinator who filtered to one
   * project has not withdrawn the question they were asking about a date in it.
   * An override naming a line the new scope excludes comes back in `unapplied`
   * with `line_out_of_scope`, which is the honest answer rather than a silent
   * drop.
   *
   * No acknowledgement is raised: FR-012's message is about an *adjustment*, and
   * a reorder the coordinator asked for by changing the sort key needs no
   * announcement that the order changed — they changed it.
   */
  const requery = useCallback(
    async (next: { projectId?: string | null; sort?: string }) => {
      const scope = next.projectId === undefined ? projectId : next.projectId;
      const key = next.sort ?? sortKey;
      setPending(true);
      try {
        const response = await fetchWorklist({
          projectId: scope ?? undefined,
          sort: key,
          overrides: [...overrides].map(([id, date]) => `${id}:${date}`),
        });
        previousDigest.current = response.ordering_digest;
        setWorklist(response);
        setProjectId(scope);
        setSortKey(key);
      } catch {
        setAcknowledgement({
          kind: "refused",
          message:
            "The worklist could not be reloaded, so the list below still reflects the previous " +
            "scope and sort. Retry in a moment.",
          poLineId: null,
        });
      } finally {
        setPending(false);
      }
    },
    [overrides, projectId, sortKey],
  );

  const rescope = useCallback((next: string | null) => requery({ projectId: next }), [requery]);
  const resort = useCallback((next: string) => requery({ sort: next }), [requery]);

  return {
    worklist,
    overrides,
    acknowledgement,
    pending,
    adjust,
    rescope,
    resort,
    clearAcknowledgement,
  };
}

const REASON_TEXT: Readonly<Record<string, string>> = {
  line_not_found: "that line is not on this worklist.",
  line_terminal: "that line has already been delivered, so there is nothing to chase.",
  line_out_of_scope: "that line is outside the project filter you have applied.",
};
