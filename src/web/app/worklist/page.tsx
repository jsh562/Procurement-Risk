import { WorklistBoard } from "./WorklistBoard";
import { STATE_COPY, type PageStateKey } from "./stateCopy";
import { fetchWorklist, WorklistUnavailableError, type WorklistResponse } from "./worklist";
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
 *
 * The banner and the frame are rendered here rather than in the interactive
 * board so they are in the first paint's HTML. An adjustment cannot change
 * either: FR-031's what-if moves a need-by date, and no page-scope state and no
 * run metadata depends on one.
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
    return <CouldNotRead failure={error as WorklistUnavailableError} />;
  }

  return (
    <main className={styles.page}>
      <h1 className={styles.heading}>Delivery risk worklist</h1>

      {/* Before any row content, deliberately — see the file docstring. */}
      {worklist.page_states.map((state) => (
        <Banner key={state} state={state} />
      ))}

      <AsOf meta={worklist.meta} />

      {/*
       * FR-042. The board renders whatever the counts say, because it owns the
       * scoping control and the control must stay on screen when a filter
       * matches nothing — that is the state in which a coordinator most needs
       * it, and it is precisely the state a `counts.total === 0` guard removed
       * it from. `empty_filter` implies an empty result, so the earlier guard
       * meant the control was *never* present exactly when the requirement
       * called for it, and the coordinator's only exit was a reload.
       *
       * The empty message moved inside the board for the same reason: it has to
       * appear beside the control rather than instead of it.
       */}
      <WorklistBoard initial={worklist} />
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
 * FR-043. The system could not look — stated as that, and not as an empty
 * worklist.
 *
 * The wording is deliberately unlike the no-active-run banner's. One means the
 * system looked and there was nothing there; this one means it could not look,
 * and rendering them alike presents an outage as an honest empty state — the
 * same defect in the opposite direction to a missing figure rendered as a zero.
 *
 * No row and no figure appear, because the system holds no knowledge about any
 * line to report. The correlation identifier is shown so the thing a
 * coordinator can quote is the thing an engineer can find.
 */
function CouldNotRead({ failure }: { readonly failure: WorklistUnavailableError }) {
  return (
    <main className={styles.page}>
      <h1 className={styles.heading}>Delivery risk worklist</h1>
      <section className={styles.banner} role="alert" data-state="could-not-read">
        <h2 className={styles.bannerLabel}>
          {failure.title ?? "The forecast data could not be read"}
        </h2>
        <p className={styles.bannerCause}>
          This page could not read the stored forecast, so it cannot say what is outstanding. That
          is not the same as there being nothing outstanding — no lines are shown because none could
          be loaded, not because none exist.
        </p>
        <p className={styles.bannerRemedy}>
          Retry in a moment. If it persists, the serving boundary needs attention: {failure.message}
        </p>
        {failure.correlationId ? (
          <p className={styles.correlation}>
            Quote this reference when reporting it: <code>{failure.correlationId}</code>
          </p>
        ) : null}
      </section>
    </main>
  );
}
