"""The worklist endpoint.

FR-002, FR-018, FR-024, FR-038, FR-042.

One GET, no write path. FR-031 makes an adjusted need-by a session-scoped
what-if that never persists, so there is nothing here to POST to — and that is
a property of the route table rather than a convention someone maintains.

This module composes: it fetches inputs, resolves states, builds rows, orders
them, and serialises. Every computation it composes lives in ``api.compute`` or
``api.risk_read``, which is what keeps the arithmetic out of the request
handler and inside modules the import contracts can reason about.

The response carries all nine members of the committed contract's
``WorklistResponse`` from the first commit, including the ones whose *content*
arrives with later stories. ``sort`` enumerates the offered keys before any
ranking exists, ``scope`` publishes the projects before a control selects among
them, and ``overrides`` reports an empty result before any override can be
made. Shipping a response missing them would break the contract for three
phases — and a contract that consumers cannot rely on until the last phase is
not a contract.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from api.compute.ordering import (
    DEFAULT_SORT_KEY,
    SORT_DIRECTIONS,
    SORT_KEYS,
    TIEBREAK,
    sort_options,
)
from api.compute.ordering import ordering_digest as compute_ordering_digest
from api.compute.ranking import RankableLine, order_lines
from api.risk_read.failures import (
    SUPPORTED_ARTIFACT_SCHEMA_VERSIONS,
    UnsupportedArtifactSchema,
    correlation_id,
    problem,
)
from api.risk_read.overrides import (
    MAX_OVERRIDES,
    OverrideRejection,
    parse_overrides,
    partition_overrides,
)
from api.risk_read.query import (
    STALENESS_BASIS,
    STALENESS_THRESHOLD_DAYS,
    classify_lines,
    load_worklist,
)
from api.risk_read.rows import RowInputs, build_primary, build_secondary, identity, need_by
from api.risk_read.states import ResolvedLine, resolve_states
from api.risk_read.validator import compute_validator

router = APIRouter()

#: FR-038. One configured zone rather than the viewer's device, so the same run
#: is stale for everyone or for no one and a line's state label is reproducible
#: across sessions. A per-viewer clock would make the eight degraded states
#: irreproducible in exactly the way that defeats their acceptance tests.
WORKLIST_TIMEZONE: Final[str] = os.environ.get("WORKLIST_TIMEZONE", "UTC")

#: FR-025's P1 half. `project_id` values are `PRJ-###` by E003's check
#: constraint; validating the shape here turns a typo into a 422 naming the
#: field rather than a silently empty worklist.
PROJECT_ID_PATTERN: Final[str] = r"^PRJ-[0-9]{3}$"

#: FR-026. Anchored, because the constraint is matched with `re.search`: an
#: unanchored alternation would accept `expected_harm_ascending` and any other
#: string merely containing a valid key.
SORT_KEY_PATTERN: Final[str] = rf"^({'|'.join(SORT_KEYS)})$"


def now_in_zone() -> datetime:
    """The request instant, in the configured zone.

    The one place in the feature a clock is read. Everything downstream takes
    the date as an argument (FR-038), which is what lets a frozen fixture stay
    frozen and keeps two coordinators in different zones seeing the same state
    labels for the same line.
    """
    return datetime.now(ZoneInfo(WORKLIST_TIMEZONE))


def get_connection() -> Any:  # pragma: no cover - exercised through the app
    """Open a connection for one request.

    A dependency rather than a module-level pool so a test can override it with
    a transaction it rolls back, and so this module holds no global state that
    a second test would inherit.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is unset. The worklist reads stored artifacts and has no "
            "meaningful behaviour without them; starting without a database would "
            "produce a page that looks like an honest empty state and is not one."
        )
    connection = psycopg.connect(url)
    try:
        yield connection
    finally:
        connection.close()


@router.get("/api/v1/worklist")
def read_worklist(
    response: Response,
    connection: Annotated[Any, Depends(get_connection)],
    project_id: Annotated[str | None, Query(pattern=PROJECT_ID_PATTERN)] = None,
    sort: Annotated[str | None, Query(pattern=SORT_KEY_PATTERN)] = None,
    need_by_override: Annotated[list[str] | None, Query()] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict[str, Any]:
    """Return open lines with their figures, degraded states, and page status.

    Absent data is a state, not a fault: no active run and an empty filter are
    both ``200`` outcomes carrying an explanatory state. Returning ``404`` or
    ``500`` for either would make the honest empty state look like a broken
    page — which is the one reading that would send a coordinator to chase an
    engineer instead of a vendor.
    """
    generated_at = now_in_zone()
    today = generated_at.date()
    sort_key = sort or DEFAULT_SORT_KEY

    try:
        overrides = parse_overrides(need_by_override, today=today)
    except OverrideRejection as exc:
        # FR-055. A refusal reaches the coordinator with its cause, naming the
        # parameter — a silently ignored adjustment leaves them reading an
        # ordering computed without the change they asked for.
        raise HTTPException(
            status_code=422,
            detail={
                "type": "need-by-override-inadmissible",
                "title": "An adjustment could not be applied",
                "detail": str(exc),
                "parameter": "need_by_override",
                "reason": exc.reason,
                "value": exc.value,
                "max_overrides": MAX_OVERRIDES,
            },
        ) from exc

    try:
        inputs = load_worklist(connection, today=today, project_id=project_id, overrides=overrides)
    except psycopg.OperationalError as exc:  # pragma: no cover - needs a downed database
        # FR-043. The system could not look, which is a different statement from
        # "it looked and found nothing" — and rendering them alike presents an
        # outage as an honest empty state.
        raise HTTPException(
            status_code=503,
            detail=problem(
                kind="datastore-unavailable",
                title="Cannot read forecast data",
                detail=str(exc),
                correlation=correlation_id(),
            ),
        ) from exc

    # FR-043, FR-052. A run written by a schema this build does not read is
    # refused rather than read anyway: the array offsets are what a version
    # governs, and a misread offset produces a figure wrong in a way no
    # coordinator could see.
    if (
        inputs.run is not None
        and inputs.run.artifact_schema_version not in SUPPORTED_ARTIFACT_SCHEMA_VERSIONS
    ):
        refusal = UnsupportedArtifactSchema(
            str(inputs.run.run_id), inputs.run.artifact_schema_version
        )
        raise HTTPException(
            status_code=500,
            detail=problem(
                kind="unsupported-artifact-schema",
                title="Forecast artifact schema not recognised",
                detail=str(refusal),
                correlation=correlation_id(),
                artifact_schema_version=inputs.run.artifact_schema_version,
            ),
        )

    reported = {line.po_line_id for line in inputs.lines}
    terminal, out_of_scope = classify_lines(
        connection, set(overrides) - reported, project_id=project_id
    )
    applied, unapplied = partition_overrides(
        overrides, reported=reported, terminal=terminal, out_of_scope=out_of_scope
    )

    resolved, page_states = resolve_states(inputs, scoped=project_id is not None)

    # Query, then states, then rows, then ranking — in that order, and the order
    # is load-bearing. Ranking before resolving states would rank a line whose
    # state excludes it from the ranking; building rows before resolving states
    # would compute a figure the state withholds, and then have to remember to
    # drop it. Each step here can only see what the one before it admitted.
    ranked = [item for item in resolved if item.is_ranked]
    ordered = _order(ranked, inputs, sort_key)
    unranked = _order_excluded([item for item in resolved if not item.is_ranked])

    # FR-020a. Computed over exactly the admitted inputs, so an unchanged value
    # is a positive statement that the whole response is unchanged rather than
    # merely that no figure moved.
    validator = compute_validator(
        run_id=str(inputs.run.run_id) if inputs.run else None,
        today=today,
        project_id=project_id,
        sort_key=sort_key,
        overrides={str(key): value.isoformat() for key, value in applied.items()},
        lines=inputs.lines,
    )
    response.headers["ETag"] = validator
    # FR-031. Revalidate every request and never serve from a shared cache: a
    # response computed under one adjustment set must not reach a request
    # carrying a different one, and the payload is time-dependent through
    # `today`, so a stored copy could show a run as current after the day
    # boundary made it stale.
    response.headers["Cache-Control"] = "private, no-cache"

    if if_none_match is not None and if_none_match.strip() == validator:
        raise HTTPException(status_code=304, headers=dict(response.headers))
    return {
        "meta": _meta(inputs, generated_at=generated_at, today=today),
        "scope": {
            "project_id": project_id,
            "available_projects": [
                {"project_id": item.project_id, "open_line_count": item.open_line_count}
                for item in inputs.available_projects
            ],
        },
        "sort": {
            "key": sort_key,
            "direction": SORT_DIRECTIONS[sort_key],
            "tiebreak": list(TIEBREAK),
            "options": [
                {
                    "key": option.key,
                    "direction": option.direction,
                    "is_default": option.is_default,
                    "is_active": option.is_active,
                }
                for option in sort_options(sort_key)
            ],
        },
        "page_states": [state.value for state in page_states],
        "ranked": [_ranked_row(item, rank, inputs) for rank, item in enumerate(ordered, start=1)],
        "unranked": [_unranked_row(item) for item in unranked],
        "counts": {
            "ranked": len(ordered),
            "unranked": len(unranked),
            "total": len(resolved),
        },
        # FR-055. An override naming a line this response does not contain is
        # reported with which of the three causes applied, never silently
        # dropped — the coordinator would otherwise believe an adjustment took
        # effect while reading an ordering computed without it.
        "overrides": {
            "applied": [
                {"po_line_id": str(key), "need_by_date": value.isoformat()}
                for key, value in sorted(applied.items(), key=lambda item: str(item[0]))
            ],
            "unapplied": sorted(unapplied, key=lambda item: item["po_line_id"]),
        },
        "ordering_digest": compute_ordering_digest(item.line.po_line_id for item in ordered),
    }


def _order_excluded(unranked: list[ResolvedLine]) -> list[ResolvedLine]:
    """FR-045. Need-by ascending, then line identifier ascending.

    Fixed rather than following the active sort key, for two reasons. At least
    one of FR-026's four keys reads a figure an excluded line does not have —
    expected harm needs draws it has none of, and with no active run the
    calendar margin has no as-of date to count from. And a group outside the
    ranking that reordered *with* the ranking would read as part of it, which is
    the whole distinction FR-016 draws.
    """
    return sorted(
        unranked,
        key=lambda item: (item.line.effective_need_by_date, str(item.line.po_line_id)),
    )


def _order(ranked: list[ResolvedLine], inputs: Any, sort_key: str) -> list[ResolvedLine]:
    """Order the ranked group under the active key."""
    if inputs.run is None:
        return ranked

    as_of = inputs.run.as_of_date
    rankable = [
        RankableLine(
            po_line_id=item.line.po_line_id,
            draws=item.line.draws or (),
            need_by_offset=(item.line.effective_need_by_date - as_of).days,
            criticality=item.line.criticality,
        )
        for item in ranked
    ]
    ordering = order_lines(rankable, sort_key=sort_key)
    position = {po_line_id: index for index, po_line_id in enumerate(ordering)}
    return sorted(ranked, key=lambda item: position[item.line.po_line_id])


def _meta(inputs: Any, *, generated_at: datetime, today: date) -> dict[str, Any]:
    """The response envelope: when it was made, for what day, under what run."""
    run = inputs.run
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "today": today.isoformat(),
        "timezone": WORKLIST_TIMEZONE,
        "forecast_run": None
        if run is None
        else {
            "run_id": str(run.run_id),
            "as_of_date": run.as_of_date.isoformat(),
            "horizon_days": run.horizon_days,
            "roster_hash": run.roster_hash,
            "model_version": run.model_version,
            "artifact_schema_version": run.artifact_schema_version,
            "age_days": run.age_days(today),
            "stale": run.is_stale(today),
            "staleness_threshold_days": STALENESS_THRESHOLD_DAYS,
            "staleness_basis": STALENESS_BASIS,
        },
        "conventions": {
            "draw_count": inputs.conventions.draw_count,
            "percentile_convention": inputs.conventions.percentile_convention,
            "anchor_date_convention": inputs.conventions.anchor_date_convention,
        },
    }


def _unranked_row(item: ResolvedLine) -> dict[str, Any]:
    """A line carrying no risk figures.

    FR-016, FR-054. Identity and need-by date only — there is deliberately no
    property here that could hold a zero or a dash, because a renderer given one
    would eventually show it and a coordinator would read it as a figure. This
    is structural absence, the first of FR-054's two encodings.
    """
    return {
        "po_line_id": str(item.line.po_line_id),
        "state": item.state.value,
        "primary": {"identity": identity(item.line), "need_by": need_by(item.line)},
    }


def _ranked_row(item: ResolvedLine, rank: int, inputs: Any) -> dict[str, Any]:
    """A line that takes a place in the ranking.

    `rank` is carried as a field and rendered as text (FR-048), because the
    ranking quantity itself is deliberately absent from the row under FR-041 —
    which would otherwise leave position read off the screen's geometry as the
    only carrier of the product's entire output, and geometry conveys nothing to
    a screen reader. It is the position under the *active* key, never a harm
    rank retained from a different ordering, so a row never asserts two
    orderings at once.
    """
    row_inputs = RowInputs(
        resolved=item,
        as_of_date=inputs.run.as_of_date,
        horizon_days=inputs.run.horizon_days,
        conventions=inputs.conventions,
        today=inputs.today,
        # FR-029. The run's staleness travels onto every populated row, not
        # only into the page banner.
        run_is_stale=inputs.run.is_stale(inputs.today),
        run_draw_count=inputs.run.draw_count,
    )
    return {
        "po_line_id": str(item.line.po_line_id),
        "rank": rank,
        "state": item.state.value,
        "primary": build_primary(row_inputs),
        "secondary": build_secondary(row_inputs),
    }
