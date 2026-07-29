"""Resolving which degraded state governs a line, and what the page reports.

FR-015, FR-016, FR-017, FR-018, FR-018a, FR-021, FR-029, FR-030, FR-033, FR-042.

The spec enumerates eight degraded states and FR-018a resolves them to exactly
one per line. That resolution is not a labelling convenience: the winning
state's requirement governs the row *in full* — its label, whether the line is
ranked, and which figures are suppressed. An earlier draft resolved only the
label and left the behaviours to the individual requirements, which meant a
roster-mismatched, already-late line was required by one requirement to be
excluded and by another to be ranked. That was STF-005, a CRITICAL finding, and
this module is where it stays closed.

The order runs from states that make a figure untrustworthy, through states
that make one only partially available, to states that merely annotate a figure
that is sound. It is derivable from that principle rather than asserted:

1. ``roster_mismatch`` — the run was fitted against a different population, so
   the figure is about some other line. Untrustworthy.
2. ``not_covered`` — no posterior exists for this line at all. Absent.
3. ``beyond_horizon`` — the survival grid does not reach the need-by date, so
   only a bound is available. Partially available.
4. ``already_late`` — the miss probability is 1 by construction and therefore
   uninformative, but the quantile pair is sound and says how much more slip is
   coming. Annotating.
5. ``calendar_passed`` — every figure is sound; the date has simply passed on
   the coordinator's calendar. Annotating.

Page-scope states compose with a row label rather than competing with it: a
stale run does not stop a row from being beyond its horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from api.risk_read.query import OpenLine, WorklistInputs

__all__ = [
    "PAGE_STATE_ORDER",
    "ROW_STATE_PRECEDENCE",
    "PageState",
    "ResolvedLine",
    "RowState",
    "resolve_states",
]


class RowState(StrEnum):
    """The state governing one line. Exactly one applies after resolution.

    ``NOMINAL`` is not the absence of a state — it is the state of a populated
    row to which no degraded state applies, and it is named so a row always
    carries a state value rather than a null a consumer has to interpret.
    """

    NOMINAL = "nominal"
    NO_ACTIVE_RUN = "no_active_run"
    ROSTER_MISMATCH = "roster_mismatch"
    NOT_COVERED = "not_covered"
    BEYOND_HORIZON = "beyond_horizon"
    ALREADY_LATE = "already_late"
    CALENDAR_PASSED = "calendar_passed"


class PageState(StrEnum):
    """A state of the whole response. Composes with a row label."""

    NO_ACTIVE_RUN = "no_active_run"
    STALE_RUN = "stale_run"
    EMPTY_FILTER = "empty_filter"


#: FR-018a. First match wins. Order is the requirement, not an implementation
#: detail — reordering it changes which label a coordinator sees and which
#: figures a row is allowed to carry.
ROW_STATE_PRECEDENCE: Final[tuple[RowState, ...]] = (
    RowState.NO_ACTIVE_RUN,
    RowState.ROSTER_MISMATCH,
    RowState.NOT_COVERED,
    RowState.BEYOND_HORIZON,
    RowState.ALREADY_LATE,
    RowState.CALENDAR_PASSED,
)

PAGE_STATE_ORDER: Final[tuple[PageState, ...]] = (
    PageState.NO_ACTIVE_RUN,
    PageState.STALE_RUN,
    PageState.EMPTY_FILTER,
)

#: FR-016, FR-021. States whose lines carry no risk figures and take no place
#: in the ranking. Derived from the precedence rather than listed twice: every
#: state at or above ``BEYOND_HORIZON``'s position that is not itself
#: partially-available suppresses. Written explicitly because "derived" here
#: would be cleverness standing in for a rule the spec states directly.
_SUPPRESSING: Final[frozenset[RowState]] = frozenset(
    {RowState.NO_ACTIVE_RUN, RowState.ROSTER_MISMATCH, RowState.NOT_COVERED}
)


@dataclass(frozen=True)
class ResolvedLine:
    """A line and the single state that governs it."""

    line: OpenLine
    state: RowState

    @property
    def suppresses_figures(self) -> bool:
        """Whether this line carries no risk figures at all.

        FR-016 and FR-021 exclude; FR-017 and FR-030 keep the line ranked with
        a reduced figure set. The distinction is the whole point of the
        precedence order.
        """
        return self.state in _SUPPRESSING

    @property
    def is_ranked(self) -> bool:
        return not self.suppresses_figures


def _row_state(line: OpenLine, inputs: WorklistInputs) -> RowState:
    """The one state governing ``line``, by FR-018a's precedence."""
    run = inputs.run
    if run is None:
        # FR-015. Echoed onto every row rather than left to the page banner:
        # a row whose only protection is the reader remembering to look up is
        # one filter or sort away from rendering a figure it does not have.
        return RowState.NO_ACTIVE_RUN

    if line.roster_hash != run.roster_hash:
        # FR-021. The run was fitted against a different population, so any
        # figure would be about a different line. Withheld, not annotated.
        return RowState.ROSTER_MISMATCH

    if not line.has_posterior:
        return RowState.NOT_COVERED

    offset = (line.need_by_date - run.as_of_date).days
    if offset > run.horizon_days:
        # FR-017. The survival grid stops at the horizon; only the residual
        # tail mass is available, and only as a bound.
        return RowState.BEYOND_HORIZON

    if offset <= 0:
        # FR-030, and E003's clamp. `survival` is one-based over
        # `k = 1..horizon_days` and stores no `k = 0`, so a need-by on the
        # as-of date has no offset to read — which is why `<= 0` rather than
        # the spec prose's "earlier than".
        return RowState.ALREADY_LATE

    if line.need_by_date < inputs.today:
        # FR-030's second half. The run still forecasts this date, but the
        # coordinator's calendar has passed it. Both facts are true and the
        # row states them separately.
        return RowState.CALENDAR_PASSED

    return RowState.NOMINAL


def resolve_states(
    inputs: WorklistInputs,
    *,
    scoped: bool = False,
) -> tuple[tuple[ResolvedLine, ...], tuple[PageState, ...]]:
    """Resolve every line to one state, and the page to its composing states.

    Args:
        inputs: The rows and run this response is computed from.
        scoped: Whether a project scope was requested. Needed because
            ``empty_filter`` (FR-042) means "a scope matched nothing", which is
            a different statement from "there are no open lines at all" — and
            reporting the wrong one tells a coordinator something false about
            their own data.

    Returns:
        The resolved lines in input order, and the page states that apply.
    """
    resolved = tuple(
        ResolvedLine(line=line, state=_row_state(line, inputs)) for line in inputs.lines
    )

    page: list[PageState] = []
    if inputs.run is None:
        page.append(PageState.NO_ACTIVE_RUN)
    elif inputs.run.is_stale(inputs.today):
        # FR-033. Never both: `no_active_run` requires no run and `stale_run`
        # requires one, so the pair is unsatisfiable and admitting it in the
        # response shape would describe a state the data cannot produce.
        page.append(PageState.STALE_RUN)

    if scoped and not resolved:
        page.append(PageState.EMPTY_FILTER)

    return resolved, tuple(page)
