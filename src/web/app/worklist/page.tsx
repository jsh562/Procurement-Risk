import { ExcludedRow, Row } from "./Row";
import { STATE_COPY, type PageStateKey } from "./stateCopy";
import {
  fetchWorklist,
  WorklistUnavailableError,
  type UnrankedRow,
  type RankedRow,
  type WorklistResponse,
} from "./worklist";
import styles from "./page.module.css";

/**
 * The coordinator's worklist.
 *
 * A server component: it reaches the request-serving boundary and nothing else,
 * which is FR-024's "the interface tier opens no datastore connection" made
 * structural rather than remembered.
 *
 * FR-015 governs the reading order here. The page-scope banner is emitted
 * **before any row content**, in the document and therefore in the accessibility
 * tree's traversal, so a coordinator meets the explanation before the rows it
 * explains. A banner placed after the list is a banner a coordinator reads only
 * once they have already drawn a conclusion from what is missing — and "figures
 * are absent" without an explanation is indistinguishable from a page that
 * failed to load them.
 */
export const dynamic = "force-dynamic";

export default async function WorklistPage() {
  let worklist: WorklistResponse;
  try {
    worklist = await fetchWorklist();
  } catch (error) {
    // FR-043. An outage is a fault, never a ninth degraded state: rendering it
    // as an empty worklist would tell a coordinator that nothing is outstanding,
    // which is the most damaging thing this page could say incorrectly.
    return <DatastoreUnavailable detail={(error as WorklistUnavailableError).message} />;
  }

  return (
    <main className={styles.page}>
      <h1 className={styles.heading}>Delivery risk worklist</h1>

      {/* Before any row content, deliberately — see the file docstring. */}
      {worklist.page_states.map((state) => (
        <Banner key={state} state={state} />
      ))}

      <AsOf meta={worklist.meta} />

      {worklist.counts.total === 0 ? (
        <p className={styles.empty}>No open purchase-order lines are in scope.</p>
      ) : (
        <>
          {worklist.ranked.length > 0 ? (
            <RankedGroup rows={worklist.ranked} sort={worklist.sort} />
          ) : null}
          {worklist.unranked.length > 0 ? <ExcludedGroup rows={worklist.unranked} /> : null}
        </>
      )}
    </main>
  );
}

/**
 * A page-scope state, rendered as text.
 *
 * FR-050: the state is carried by text present in the accessibility tree, never
 * by colour, position, or an icon with no accessible name. `role="status"` so
 * it is announced rather than merely present.
 */
function Banner({ state }: { readonly state: PageStateKey }) {
  const copy = STATE_COPY[state];
  return (
    <section className={styles.banner} role="status" data-state={state}>
      <h2 className={styles.bannerLabel}>{copy.label}</h2>
      <p className={styles.bannerCause}>{copy.cause}</p>
      <p className={styles.bannerRemedy}>{copy.remedy}</p>
    </section>
  );
}

/**
 * The frame every state label was resolved against.
 *
 * FR-019 and FR-038. The as-of date is text reachable without hover or
 * expansion; `today` is stated because the server resolved it in a configured
 * zone and a coordinator elsewhere would otherwise assume their own.
 */
function AsOf({ meta }: { readonly meta: WorklistResponse["meta"] }) {
  return (
    <p className={styles.provenance}>
      As of <time dateTime={meta.today}>{meta.today}</time> ({meta.timezone}).{" "}
      {meta.forecast_run === null
        ? "No forecast run is active."
        : `Forecast run ${meta.forecast_run.model_version}, fitted against data as of ` +
          `${meta.forecast_run.as_of_date}.`}
    </p>
  );
}

/**
 * The ranked list.
 *
 * FR-047 puts the tiebreak rule on screen beside the active key and direction,
 * and it is not a footnote: expected harm is exactly zero for every line whose
 * draws all land on or before its need-by date, so at a full horizon the
 * tiebreak alone orders that entire block. An order the coordinator cannot
 * account for reads as arbitrary — the failure the per-row decomposition exists
 * to avoid, reappearing at the level of the list.
 *
 * The rule travels in the response rather than being written into this
 * component, so what is stated on screen is the rule the server actually
 * applied. A hard-coded copy would be a second source of truth that drifts
 * silently the moment the server's changes.
 *
 * FR-048 requires the group to state both that it is ordered and how many
 * entries it holds, so position is never carried by vertical placement alone.
 */
function RankedGroup({
  rows,
  sort,
}: {
  readonly rows: readonly RankedRow[];
  readonly sort: WorklistResponse["sort"];
}) {
  return (
    <section className={styles.group} aria-labelledby="ranked-heading">
      <h2 id="ranked-heading" className={styles.groupHeading}>
        Ranked by {SORT_LABELS[sort.key] ?? sort.key} ({rows.length} lines,{" "}
        {sort.direction === "desc" ? "highest first" : "lowest first"})
      </h2>
      <p className={styles.tiebreak}>Ties are broken by {sort.tiebreak.join(", then ")}.</p>
      <ol className={styles.rows}>
        {rows.map((row) => (
          <Row key={row.po_line_id} row={row} />
        ))}
      </ol>
    </section>
  );
}

/**
 * Wording for the four keys FR-026 admits. Read from the server's key rather
 * than enumerated as options here — the server owns which keys exist (FR-032),
 * and this map only supplies English for the one in force.
 */
const SORT_LABELS: Readonly<Record<string, string>> = {
  expected_harm: "expected schedule harm",
  need_by_date: "need-by date",
  criticality: "criticality",
  calendar_margin: "calendar margin",
};

/**
 * The excluded group.
 *
 * FR-016: a separate group carrying identity and need-by date and nothing
 * further, and separate in the document as well as in the payload — a group
 * that existed only in the rendering is one the next consumer re-decides.
 */
function ExcludedGroup({ rows }: { readonly rows: readonly UnrankedRow[] }) {
  return (
    <section className={styles.group} aria-labelledby="unranked-heading">
      <h2 id="unranked-heading" className={styles.groupHeading}>
        Not ranked ({rows.length} lines)
      </h2>
      <p className={styles.groupNote}>
        These lines are outstanding and are listed so they are not overlooked. No risk figures are
        shown for them.
      </p>
      <ul className={styles.rows}>
        {rows.map((row) => (
          <ExcludedRow key={row.po_line_id} row={row} />
        ))}
      </ul>
    </section>
  );
}

/** FR-043. An outage, stated as one. */
function DatastoreUnavailable({ detail }: { readonly detail: string }) {
  return (
    <main className={styles.page}>
      <h1 className={styles.heading}>Delivery risk worklist</h1>
      <section className={styles.banner} role="alert" data-state="unavailable">
        <h2 className={styles.bannerLabel}>The worklist could not be loaded</h2>
        <p className={styles.bannerCause}>
          The stored forecast artifacts could not be read, so this page cannot say what is
          outstanding — not that nothing is.
        </p>
        <p className={styles.bannerRemedy}>
          Retry in a moment. If it persists the serving boundary needs attention: {detail}
        </p>
      </section>
    </main>
  );
}
