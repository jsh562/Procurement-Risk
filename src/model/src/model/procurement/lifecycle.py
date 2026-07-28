"""The legal transition walk: `6 + 3L` events, every pair legal, no position reused.

The transition set is **imported from `model.schema.helpers`-equivalent truth
rather than restated**: it is the same seven edges the delivered
`fn_is_legal_lifecycle_transition` enforces, so a generator that disagreed with
the database would emit an artifact the loader rejects.

Each rework loop repeats three *states* at three *new positions*. Position
reuse is impossible by construction here because `sequence_no` only ever
increments — which matters because `uq_lifecycle_event__line_sequence` would
otherwise reject the load after the artifact was already committed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

__all__ = [
    "INITIAL_STATE",
    "LEGAL_TRANSITIONS",
    "NON_TERMINAL_STATES",
    "STATES",
    "TERMINAL_STATE",
    "LifecycleError",
    "WalkedEvent",
    "state_sequence",
    "validate_walk",
    "walk",
]

INITIAL_STATE = "submitted"
TERMINAL_STATE = "delivered"

#: The seven legal edges, in the order `data-model.md` and the delivered
#: `fn_is_legal_lifecycle_transition` list them.
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("submitted", "under_review"),
        ("under_review", "approved"),
        ("under_review", "revise_and_resubmit"),
        ("revise_and_resubmit", "submitted"),
        ("approved", "released_for_fabrication"),
        ("released_for_fabrication", "shipped"),
        ("shipped", "delivered"),
    }
)

STATES = (
    "submitted",
    "under_review",
    "approved",
    "revise_and_resubmit",
    "released_for_fabrication",
    "shipped",
    "delivered",
)

NON_TERMINAL_STATES = tuple(s for s in STATES if s != TERMINAL_STATE)


class LifecycleError(ValueError):
    """Raised when a walk is not a legal path through the state machine."""


@dataclass(frozen=True, slots=True)
class WalkedEvent:
    sequence_no: int
    to_state: str
    occurred_at: date


def state_sequence(rework_loops: int) -> tuple[str, ...]:
    """The states a line visits, in order, for `rework_loops` loops.

    `6 + 3L` events: the five forward states after `submitted`, plus three
    repeated states per loop — `revise_and_resubmit`, `submitted`,
    `under_review` — inserted before the clean pass to `approved`.
    """
    if rework_loops < 0:
        raise LifecycleError(f"rework loops cannot be negative, found {rework_loops}")
    states = [INITIAL_STATE, "under_review"]
    for _ in range(rework_loops):
        states += ["revise_and_resubmit", "submitted", "under_review"]
    states += ["approved", "released_for_fabrication", "shipped", TERMINAL_STATE]
    return tuple(states)


def walk(
    order_date: date, event_dates: Sequence[date], rework_loops: int
) -> tuple[WalkedEvent, ...]:
    """Pair the truncated date chain with the state sequence it belongs to.

    `event_dates` is already truncated at the as-of date, so this emits only as
    many events as survived censoring — the walk is *prefix-truncated*, never
    re-routed. A censored line is a line partway along the legal path, not a
    line that took a different one.
    """
    states = state_sequence(rework_loops)
    if len(event_dates) > len(states):
        raise LifecycleError(
            f"{len(event_dates)} event dates for a {rework_loops}-loop line, which visits "
            f"only {len(states)} states"
        )
    if event_dates and event_dates[0] != order_date:
        raise LifecycleError(
            f"event 1 is dated {event_dates[0]} but the order date is {order_date}; "
            f"the opening transition *is* the clock start"
        )
    return tuple(
        WalkedEvent(index, state, when)
        for index, (state, when) in enumerate(zip(states, event_dates, strict=False), start=1)
    )


def validate_walk(events: Sequence[WalkedEvent]) -> None:
    """DV-007, asserted as a refusal rather than reported."""
    if not events:
        raise LifecycleError("every line must carry at least one event")

    expected = list(range(1, len(events) + 1))
    if [e.sequence_no for e in events] != expected:
        raise LifecycleError(
            f"sequence numbers {[e.sequence_no for e in events]} are not contiguous from 1"
        )
    if events[0].to_state != INITIAL_STATE:
        raise LifecycleError(
            f"event 1 is {events[0].to_state!r}; every chain opens at {INITIAL_STATE!r}"
        )
    for previous, current in zip(events, events[1:], strict=False):
        edge = (previous.to_state, current.to_state)
        if edge not in LEGAL_TRANSITIONS:
            raise LifecycleError(
                f"{edge[0]!r} -> {edge[1]!r} at sequence {current.sequence_no} is not one of "
                f"the seven legal transitions"
            )
        if current.occurred_at <= previous.occurred_at:
            raise LifecycleError(
                f"event {current.sequence_no} at {current.occurred_at} does not follow "
                f"event {previous.sequence_no} at {previous.occurred_at}; occurred_at must "
                f"strictly increase with sequence_no"
            )

    positions = {(e.sequence_no, e.to_state) for e in events}
    if len(positions) != len(events):
        raise LifecycleError("a state repeats a position; each loop must use new positions")
