"""Reading the worklist's inputs from the datastore.

FR-002, FR-019, FR-020, FR-022, FR-029, FR-038, FR-052.

Everything the worklist shows comes from artifacts a previous forecast run
already stored. This module fetches them and computes nothing about risk — the
arithmetic lives in ``api.compute``, which the import contract keeps separate
from anything model-facing.

Two properties are load-bearing here rather than incidental:

**``today`` is supplied, never read from a clock.** FR-038. Three separate
behaviours depend on it — the run's age, whether a need-by date has passed on
the coordinator's calendar, and the stale banner — and a module that consulted
the system clock would make every fixture rot as wall-clock time advanced, and
would let two coordinators in different zones see different state labels for
the same line.

**The active run is a single row by construction.** ``forecast_run`` carries a
partial unique index on ``is_active``, so at most one row can be active. This
module still asserts what it found rather than trusting the index, because the
difference between "no active run" and "the query was wrong" is the difference
between an honest empty state and a silent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from uuid import UUID

#: FR-029. Seven days, and the basis travels with it because the requirement
#: says an unexplained number is not enough: a run whose anchor predates the
#: current week has missed at least a week of lifecycle events. Measured from
#: ``as_of_date`` rather than ``created_at`` — FR-019 already puts the as-of
#: date on every row, so a coordinator can check the staleness claim against a
#: figure the screen is showing them, and a backfilled run's creation time
#: would diverge from what it was actually fitted against.
STALENESS_THRESHOLD_DAYS: Final[int] = 7
STALENESS_BASIS: Final[str] = (
    "One refit cadence. A run whose as-of date predates the current week has missed at "
    "least a week of lifecycle events, so its figures describe a world that has moved on."
)

#: FR-022. ``ck_pol__closed_iff_delivered`` pins ``is_closed`` to
#: ``lifecycle_state = 'delivered'``, so filtering on the flag is the same
#: statement as filtering on the state — without restating E003's terminal-state
#: list here and letting the two drift. It is also the column
#: ``ix_purchase_order_line__open`` is partial on, so this is the indexed read.
OPEN_LINE_PREDICATE: Final[str] = "NOT is_closed"

__all__ = [
    "STALENESS_BASIS",
    "STALENESS_THRESHOLD_DAYS",
    "Conventions",
    "ForecastRunRef",
    "OpenLine",
    "ProjectSummary",
    "WorklistInputs",
    "load_worklist",
    "run_age_days",
]


@dataclass(frozen=True)
class ForecastRunRef:
    """The active run, and the provenance every figure it produced inherits."""

    run_id: UUID
    as_of_date: date
    horizon_days: int
    draw_count: int
    roster_hash: str
    model_version: str
    artifact_schema_version: int

    def age_days(self, today: date) -> int:
        return run_age_days(self.as_of_date, today)

    def is_stale(self, today: date) -> bool:
        return self.age_days(today) > STALENESS_THRESHOLD_DAYS


@dataclass(frozen=True)
class OpenLine:
    """One open purchase-order line, with its posterior when the run covers it.

    ``draws`` and ``survival`` are held here and never leave the serving
    boundary: FR-053 keeps them off the wire, because a client holding several
    thousand draws is one aggregation away from the point estimate this product
    exists to refuse.
    """

    po_line_id: UUID
    project_id: str
    vendor_id: str
    po_number: str
    line_number: int
    description: str
    quantity: float
    unit_of_measure: str
    need_by_date: date
    criticality: int
    lifecycle_state: str
    roster_hash: str
    draws: tuple[float, ...] | None
    survival: tuple[float, ...] | None
    residual_tail_mass: float | None

    @property
    def identifier(self) -> str:
        """The human-readable identifier, not the generated key.

        A coordinator reads `PRJ-002 · PO-4417-3`, not a UUID, and FR-032 puts
        this first in the primary region.
        """
        return f"{self.project_id} · {self.po_number}-{self.line_number}"

    @property
    def has_posterior(self) -> bool:
        return self.draws is not None and self.survival is not None


@dataclass(frozen=True)
class Conventions:
    """The read conventions the stored figures were produced under.

    Published from ``schema_constants`` rather than compiled into either tier,
    so a displayed figure resolves to the artifact *and* the rule it was
    computed by. A constant duplicated here would be a second source of truth
    for a number the database already enforces with a check constraint.
    """

    draw_count: int
    percentile_convention: str
    anchor_date_convention: str


@dataclass(frozen=True)
class ProjectSummary:
    """One project the scoping control may select, and its open-line count.

    The count travels because a project holding no open line and a project the
    coordinator has simply not scoped to are otherwise identical from inside a
    filtered list (FR-025).
    """

    project_id: str
    open_line_count: int


@dataclass(frozen=True)
class WorklistInputs:
    """Everything one worklist response is computed from.

    ``run is None`` is the no-active-run state and is a successful outcome
    rather than a failure — the lines are still listed, carrying no figures.
    """

    run: ForecastRunRef | None
    lines: tuple[OpenLine, ...]
    today: date
    conventions: Conventions
    available_projects: tuple[ProjectSummary, ...]


def run_age_days(as_of_date: date, today: date) -> int:
    """Whole days from a run's anchor to today. Negative if the anchor is ahead."""
    return (today - as_of_date).days


_ACTIVE_RUN_SQL: Final[str] = """
    SELECT run_id, as_of_date, horizon_days, draw_count, roster_hash,
           model_version, artifact_schema_version
      FROM forecast_run
     WHERE is_active
"""

_OPEN_LINES_SQL: Final[str] = f"""
    SELECT pol.po_line_id, pol.project_id, pol.vendor_id, pol.po_number,
           pol.line_number, pol.description, pol.quantity, pol.unit_of_measure,
           pol.need_by_date, pol.criticality, pol.lifecycle_state, pol.roster_hash,
           lp.draws, lp.survival, lp.residual_tail_mass
      FROM purchase_order_line AS pol
      LEFT JOIN line_posterior AS lp
             ON lp.po_line_id = pol.po_line_id
            AND lp.run_id = %(run_id)s::uuid
     WHERE {OPEN_LINE_PREDICATE}
       AND (%(project_id)s::text IS NULL OR pol.project_id = %(project_id)s::text)
"""
# The casts are required, not stylistic. A bare `$1 IS NULL` gives the planner
# nothing to infer a type from and PostgreSQL raises `AmbiguousParameter`; the
# same is true of the join's `run_id` when no run is active and the value sent
# is NULL. Casting is also what makes the no-active-run join behave: `lp.run_id
# = NULL` matches nothing, so every line arrives with its posterior columns
# null — the LEFT JOIN keeps the line, and `has_posterior` reports the truth.

_CONVENTIONS_SQL: Final[str] = """
    SELECT draw_count, percentile_convention, anchor_date_convention
      FROM schema_constants
"""

#: FR-025. Always the full set, including while a filter is active, so a
#: coordinator can leave a scope without a second request. Deliberately not
#: filtered by ``project_id``: the domain of the control is not a function of
#: the control's current value.
_AVAILABLE_PROJECTS_SQL: Final[str] = f"""
    SELECT project_id, count(*) AS open_line_count
      FROM purchase_order_line
     WHERE {OPEN_LINE_PREDICATE}
     GROUP BY project_id
     ORDER BY project_id
"""


def load_worklist(
    connection: Any,
    *,
    today: date,
    project_id: str | None = None,
) -> WorklistInputs:
    """Fetch the active run and every open line, with posteriors where they exist.

    Args:
        connection: An open psycopg connection. Taken rather than created so a
            test can hand in a transaction it will roll back.
        today: The date every date-dependent state is resolved against. Supplied
            rather than read from a clock (FR-038).
        project_id: Optional scope. FR-025's P1 half — the parameter and its
            WHERE clause ship here so the empty-filter state is reachable; the
            on-screen control is US4.

    Returns:
        The inputs for one response. A ``run`` of ``None`` means no run is
        active, which is a state to report rather than an error to raise.

    The join is LEFT, deliberately. An inner join would silently drop every
    line the active run does not cover — which is exactly the line most likely
    to be new, and the one FR-016 says must stay visible.
    """
    with connection.cursor() as cursor:
        cursor.execute(_ACTIVE_RUN_SQL)
        active = cursor.fetchall()

    if len(active) > 1:  # pragma: no cover - the partial unique index forbids it
        raise RuntimeError(
            f"{len(active)} forecast runs are marked active; the partial unique index on "
            "forecast_run(is_active) should make this unreachable, and continuing would "
            "mean choosing one arbitrarily and presenting its figures as though canonical"
        )

    run = (
        ForecastRunRef(
            run_id=active[0][0],
            as_of_date=active[0][1],
            horizon_days=active[0][2],
            draw_count=active[0][3],
            roster_hash=active[0][4],
            model_version=active[0][5],
            artifact_schema_version=active[0][6],
        )
        if active
        else None
    )

    with connection.cursor() as cursor:
        cursor.execute(
            _OPEN_LINES_SQL,
            {"run_id": run.run_id if run else None, "project_id": project_id},
        )
        rows = cursor.fetchall()

    with connection.cursor() as cursor:
        cursor.execute(_CONVENTIONS_SQL)
        constants = cursor.fetchone()
        if constants is None:
            raise RuntimeError(
                "schema_constants holds no row. Its primary key is a singleton boolean pinned "
                "true by a check constraint, so the table is either seeded or the migration did "
                "not complete — and publishing figures without the conventions they were computed "
                "under is what FR-003 exists to prevent"
            )
        cursor.execute(_AVAILABLE_PROJECTS_SQL)
        projects = cursor.fetchall()

    lines = tuple(
        OpenLine(
            po_line_id=row[0],
            project_id=row[1],
            vendor_id=row[2],
            po_number=row[3],
            line_number=row[4],
            description=row[5],
            quantity=float(row[6]),
            unit_of_measure=row[7],
            need_by_date=row[8],
            criticality=row[9],
            lifecycle_state=row[10],
            roster_hash=row[11],
            draws=tuple(row[12]) if row[12] is not None else None,
            survival=tuple(row[13]) if row[13] is not None else None,
            residual_tail_mass=row[14],
        )
        for row in rows
    )

    return WorklistInputs(
        run=run,
        lines=lines,
        today=today,
        conventions=Conventions(
            draw_count=constants[0],
            percentile_convention=constants[1],
            anchor_date_convention=constants[2],
        ),
        available_projects=tuple(
            ProjectSummary(project_id=row[0], open_line_count=row[1]) for row in projects
        ),
    )
