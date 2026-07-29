"use client";

import { useEffect, useRef } from "react";
import { ExcludedRow, Row } from "./Row";
import { useWorklist } from "./useWorklist";
import type { WorklistResponse } from "./worklist";
import styles from "./page.module.css";

/**
 * The interactive worklist.
 *
 * Rendered from a response the server component already fetched, so the first
 * paint carries the ranking rather than a spinner — and every subsequent
 * adjustment goes back to the server, because re-ranking needs arithmetic the
 * client must not be given the inputs for (FR-024, FR-053).
 *
 * FR-046 puts two obligations here that are easy to miss:
 *
 * **Focus stays on the adjusted control.** A list that has just reordered
 * underneath a coordinator, with focus returned to the top, is a list they have
 * to find their place in again — and the row they were working on has moved.
 *
 * **The acknowledgement lives in one live region**, shared by both outcomes, so
 * assistive technology announces "applied, order changed" and "applied, order
 * unchanged" through the same channel. Two regions would let one be configured
 * away and leave half the outcomes silent.
 */
export function WorklistBoard({ initial }: { readonly initial: WorklistResponse }) {
  const { worklist, overrides, acknowledgement, pending, adjust, clearAcknowledgement } =
    useWorklist(initial);
  const controls = useRef(new Map<string, HTMLInputElement>());

  useEffect(() => {
    // FR-046. Focus returns to the control the coordinator was using, not to
    // the top of a list that has just moved under them.
    if (acknowledgement?.poLineId) {
      controls.current.get(acknowledgement.poLineId)?.focus();
    }
  }, [acknowledgement]);

  return (
    <>
      {/*
       * FR-012, FR-046. One region, persistent, never on a timer — a message
       * that disappears is the documented way an acknowledgement is missed.
       * `aria-live="polite"` rather than `assertive`: the coordinator initiated
       * this, so it is confirmation rather than an interruption.
       */}
      <div className={styles.acknowledgement} role="status" aria-live="polite">
        {acknowledgement ? (
          <>
            <span data-kind={acknowledgement.kind}>{acknowledgement.message}</span>
            <button type="button" onClick={clearAcknowledgement} className={styles.dismiss}>
              Dismiss
            </button>
          </>
        ) : null}
      </div>

      {worklist.ranked.length > 0 ? (
        <section className={styles.group} aria-labelledby="ranked-heading">
          <h2 id="ranked-heading" className={styles.groupHeading}>
            Ranked by {SORT_LABELS[worklist.sort.key] ?? worklist.sort.key} (
            {worklist.ranked.length} lines,{" "}
            {worklist.sort.direction === "desc" ? "highest first" : "lowest first"})
          </h2>
          <p className={styles.tiebreak}>
            Ties are broken by {worklist.sort.tiebreak.join(", then ")}.
          </p>
          <ol className={styles.rows} aria-busy={pending}>
            {worklist.ranked.map((row) => (
              <Row
                key={row.po_line_id}
                row={row}
                control={
                  <NeedByControl
                    poLineId={row.po_line_id}
                    value={overrides.get(row.po_line_id) ?? row.primary.need_by.date}
                    onAdjust={adjust}
                    register={(element) => {
                      if (element) controls.current.set(row.po_line_id, element);
                      else controls.current.delete(row.po_line_id);
                    }}
                  />
                }
              />
            ))}
          </ol>
        </section>
      ) : null}

      {worklist.unranked.length > 0 ? (
        <section className={styles.group} aria-labelledby="unranked-heading">
          <h2 id="unranked-heading" className={styles.groupHeading}>
            Not ranked ({worklist.unranked.length} lines)
          </h2>
          <p className={styles.groupNote}>
            These lines are outstanding and are listed so they are not overlooked. No risk figures
            are shown for them.
          </p>
          <ul className={styles.rows}>
            {worklist.unranked.map((row) => (
              <ExcludedRow key={row.po_line_id} row={row} />
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

/**
 * The need-by what-if control.
 *
 * FR-051. A native `<input type="date">` rather than a custom picker: it is
 * keyboard-operable, announces itself, and accepts typed input without any of
 * that having to be reimplemented — and reimplementing it is where custom
 * pickers usually lose one of the three.
 *
 * The label states that the change is a what-if, because the control is the
 * point at which a coordinator forms the expectation of what pressing it does.
 */
function NeedByControl({
  poLineId,
  value,
  onAdjust,
  register,
}: {
  readonly poLineId: string;
  readonly value: string;
  readonly onAdjust: (poLineId: string, needByDate: string) => Promise<void>;
  readonly register: (element: HTMLInputElement | null) => void;
}) {
  const id = `need-by-${poLineId}`;
  return (
    <span className={styles.needByControl}>
      <label htmlFor={id}>Try a different need-by date</label>
      <input
        id={id}
        ref={register}
        type="date"
        defaultValue={value}
        onChange={(event) => {
          if (event.target.value) void onAdjust(poLineId, event.target.value);
        }}
      />
    </span>
  );
}

/**
 * Wording for the four keys FR-026 admits. Read from the server's key rather
 * than enumerated here — the server owns which keys exist (FR-032), and this
 * map only supplies English for the one in force.
 */
const SORT_LABELS: Readonly<Record<string, string>> = {
  expected_harm: "expected schedule harm",
  need_by_date: "need-by date",
  criticality: "criticality",
  calendar_margin: "calendar margin",
};
