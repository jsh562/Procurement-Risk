"""The response validator's input set.

FR-020a, SC-027. The requirement's claim is over unobserved time, so it names
the verification means instead: a validator over *exactly* the admitted inputs.
These tests are that verification — each admitted input is shown to move it, and
each excluded one is shown not to.

Both halves matter. A validator sensitive to too much returns `200` for an
identical response and proves nothing about which inputs are permitted to move
a figure. One sensitive to too little returns `304` for a response that
genuinely differs, and withholds a changed worklist from a coordinator.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from api.risk_read.validator import compute_validator

TODAY = date(2026, 6, 3)
RUN_ID = "3f7c2b90-5a44-4e11-8b0a-6d9e1c33a201"


def _line(**overrides: Any) -> Any:
    """One line, with the stored fields the response derives from."""
    from uuid import UUID

    from api.risk_read.query import OpenLine

    defaults: dict[str, Any] = {
        "po_line_id": UUID(int=1),
        "project_id": "PRJ-001",
        "vendor_id": "VND-001",
        "po_number": "PO-4471",
        "line_number": 3,
        "description": "Air handling unit AHU-3",
        "quantity": 1.0,
        "unit_of_measure": "EA",
        "need_by_date": date(2026, 8, 10),
        "criticality": 4,
        "lifecycle_state": "submitted",
        "roster_hash": "sha256:" + "a" * 64,
        "draws": (1.0, 2.0),
        "survival": (0.5,),
        "residual_tail_mass": 0.5,
    }
    return OpenLine(**{**defaults, **overrides})


def _validator(**overrides: Any) -> str:
    defaults: dict[str, Any] = {
        "run_id": RUN_ID,
        "today": TODAY,
        "project_id": None,
        "sort_key": "expected_harm",
        "overrides": None,
        "lines": [_line()],
    }
    return compute_validator(**{**defaults, **overrides})


def test_identical_inputs_give_an_identical_validator() -> None:
    """The baseline. Without this every other test here passes trivially."""
    assert _validator() == _validator()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "00000000-0000-0000-0000-000000000000"),
        ("run_id", None),
        ("today", TODAY + timedelta(days=1)),
        ("project_id", "PRJ-002"),
        ("sort_key", "need_by_date"),
        ("overrides", {"1": "2026-09-01"}),
    ],
)
def test_every_admitted_input_moves_the_validator(field: str, value: Any) -> None:
    """FR-020a's admitted set, one at a time.

    An input that did not move the validator would let a `304` withhold a
    response that legitimately differs — and `run_id: None` is in the list
    because the *absence* of an active run is itself one of the admitted
    inputs, not merely a missing value.
    """
    assert _validator(**{field: value}) != _validator()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("need_by_date", date(2026, 9, 1)),
        ("criticality", 5),
        ("lifecycle_state", "shipped"),
        ("roster_hash", "sha256:" + "b" * 64),
        ("project_id", "PRJ-002"),
        ("po_number", "PO-9999"),
        ("line_number", 4),
    ],
)
def test_every_stored_field_the_response_derives_from_moves_the_validator(
    field: str, value: Any
) -> None:
    """FR-020a names these individually, and each is load-bearing.

    `lifecycle_state` is the one worth pausing on: a line FR-022 has since made
    terminal leaves the worklist entirely while every surviving row's figure
    stands still. Without it in the validator, a `304` would hand back a
    worklist still listing a delivered line.
    """
    assert _validator(lines=[_line(**{field: value})]) != _validator()


def test_a_line_appearing_or_disappearing_moves_the_validator() -> None:
    """FR-020a. The subject of a *response* is a set of rows, not one row.

    A line opened since the last request changes the worklist without changing
    any surviving figure, so the set has to participate — otherwise an unchanged
    validator would be a statement about figures only, and SC-027 asks it to be
    a statement about the response.
    """
    from uuid import UUID

    two = [_line(), _line(po_line_id=UUID(int=2), po_number="PO-4472")]
    assert _validator(lines=two) != _validator()
    assert _validator(lines=[]) != _validator()


def test_the_order_lines_arrive_in_does_not_move_the_validator() -> None:
    """The validator is a function of the line *set*.

    A query returning the same rows in a different order has changed nothing a
    coordinator could observe, and a validator that moved with it would report a
    change on every other request and stop meaning anything.
    """
    from uuid import UUID

    first = _line()
    second = _line(po_line_id=UUID(int=2), po_number="PO-4472")
    assert _validator(lines=[first, second]) == _validator(lines=[second, first])


def test_the_posterior_does_not_move_the_validator() -> None:
    """The draws cannot change without the run changing, and the run's identity
    is already in the validator. Including them would make it expensive to
    compute and no more sensitive."""
    assert _validator(lines=[_line(draws=(9.0, 9.5), survival=(0.9,))]) == _validator()


def test_the_validator_is_weak() -> None:
    """Two responses with identical inputs differ only in `meta.generated_at`,
    which records when the response was produced rather than what it contains.
    That is exactly the semantic difference a weak validator expresses."""
    assert _validator().startswith('W/"sha256:')


def test_a_matching_validator_is_answered_with_304(frozen_run: dict[str, Any], client: Any) -> None:
    """The end-to-end form, through the real endpoint."""
    first = client.get("/api/v1/worklist")
    etag = first.headers["ETag"]
    assert etag.startswith('W/"sha256:')

    second = client.get("/api/v1/worklist", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_a_stale_validator_is_answered_with_the_full_response(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """A validator from a different state must not suppress the body."""
    response = client.get("/api/v1/worklist", headers={"If-None-Match": 'W/"sha256:stale"'})
    assert response.status_code == 200
    assert response.json()["counts"]["total"] > 0


def test_the_response_forbids_a_shared_cache(frozen_run: dict[str, Any], client: Any) -> None:
    """FR-031. A response computed under one adjustment set must not be served
    to a request carrying a different one, and the payload is time-dependent
    through `today` — so a stored copy could show a run as current after the day
    boundary made it stale."""
    assert client.get("/api/v1/worklist").headers["Cache-Control"] == "private, no-cache"
