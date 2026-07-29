"""US2 — asking what happens if a date moves.

The independent test the task list states: change one line's need-by date and
confirm the list reorders, the probability moves in the expected direction, and
the invocation record gains no row.

Two things are being proved here at once, and they pull in opposite directions.
An adjustment must genuinely re-rank — otherwise the question goes unanswered.
And it must write nothing — no row on the line, no row in the invocation record,
nothing that survives a reload. FR-031 makes the query parameter the whole
mechanism precisely so those two are not in tension: there is no write path to
forget to guard.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from api.risk_read.overrides import MAX_OVERRIDES

TODAY = date(2026, 6, 3)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: Any) -> None:
    """FR-038's injected date, so a fixture does not change state overnight."""
    from api.routes import worklist as route

    monkeypatch.setattr(
        route,
        "now_in_zone",
        lambda: datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=ZoneInfo("UTC")),
    )


def _get(client: Any, **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/worklist", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _row(body: dict[str, Any], po_line_id: str) -> dict[str, Any]:
    for row in body["ranked"] + body["unranked"]:
        if row["po_line_id"] == po_line_id:
            return row
    raise AssertionError(f"{po_line_id} is in neither group")


def test_pulling_a_need_by_date_in_raises_the_miss_probability(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-013, FR-020. Moving a need-by date earlier never *decreases* the
    displayed probability of missing it — the survival array is non-increasing,
    so reading it at a smaller offset can only give a value at least as large.

    This is the property that makes the what-if worth asking. If the figure did
    not move, the coordinator learns nothing from moving the date.
    """
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    before = _row(_get(client), line["po_line_id"])
    pulled = date.fromisoformat(line["need_by_date"]) - timedelta(days=20)
    after = _row(
        _get(client, need_by_override=[f"{line['po_line_id']}:{pulled.isoformat()}"]),
        line["po_line_id"],
    )

    assert (
        after["primary"]["miss_probability"]["miss"]["percent"]
        > before["primary"]["miss_probability"]["miss"]["percent"]
    )


def test_pushing_a_need_by_date_out_lowers_the_miss_probability(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """The converse. Both directions are checked because a figure that moved
    the same way whichever way the date went would satisfy a one-sided test."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    before = _row(_get(client), line["po_line_id"])
    # Ten days, not forty. Forty pushes the date past every draw, so the
    # probability rounds to zero and takes the bounded form — which is correct
    # behaviour with no integer to compare, and is asserted separately below.
    pushed = date.fromisoformat(line["need_by_date"]) + timedelta(days=10)
    after = _row(
        _get(client, need_by_override=[f"{line['po_line_id']}:{pushed.isoformat()}"]),
        line["po_line_id"],
    )

    assert not after["primary"]["miss_probability"]["bounded"]
    assert (
        after["primary"]["miss_probability"]["miss"]["percent"]
        < before["primary"]["miss_probability"]["miss"]["percent"]
    )


def test_pushing_a_date_past_every_draw_gives_a_bound_and_not_a_zero(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-008. Pushed far enough, the stored probability *is* exactly zero — and
    `0%` on a screen is a promise the posterior is in no position to make.

    This is the case a reader expects to be exempt from the bounded form, and it
    is the one that most needs it: four thousand draws cannot evidence a
    certainty, so an exact zero in the array is itself an estimate at the
    resolution the draw count supports.
    """
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    pushed = date.fromisoformat(line["need_by_date"]) + timedelta(days=60)
    figure = _row(
        _get(client, need_by_override=[f"{line['po_line_id']}:{pushed.isoformat()}"]),
        line["po_line_id"],
    )["primary"]["miss_probability"]

    assert figure["bounded"]
    assert figure["miss"]["display"] == "<1%"
    assert figure["miss"]["percent"] is None
    assert figure["on_time"]["display"] == ">99%"


def test_an_adjustment_reorders_the_list(frozen_run: dict[str, Any], client: Any) -> None:
    """FR-011. The whole point: the ranking answers the new question."""
    line = next(
        item for item in frozen_run["lines"] if item["case"] == "adjustment_changes_no_ordering"
    )
    before = [row["po_line_id"] for row in _get(client)["ranked"]]

    # Pulled far enough to matter — this line is near the bottom, so a large
    # pull is what moves it and demonstrates the reorder.
    pulled = date.fromisoformat(line["need_by_date"]) - timedelta(days=85)
    after = [
        row["po_line_id"]
        for row in _get(client, need_by_override=[f"{line['po_line_id']}:{pulled.isoformat()}"])[
            "ranked"
        ]
    ]

    assert after != before
    assert after.index(line["po_line_id"]) < before.index(line["po_line_id"])
    assert sorted(after) == sorted(before), "reordering must not add or drop a line"


def test_the_recorded_no_op_adjustment_changes_nothing_and_is_still_applied(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-012. The case the fixture searched for at generation time.

    The adjustment takes effect — the harm rises — and the order does not move.
    Both facts must be observable, because "applied" and "changed the order" are
    what the two acknowledgements distinguish, and a coordinator who cannot tell
    them apart cannot tell an applied no-op from an ignored adjustment.
    """
    adjustment = frozen_run["adjustment"]
    before = _get(client)
    after = _get(
        client,
        need_by_override=[f"{adjustment['po_line_id']}:{adjustment['adjusted_need_by_date']}"],
    )

    assert after["ordering_digest"] == before["ordering_digest"], "the order must not move"
    assert after["overrides"]["applied"] == [
        {
            "po_line_id": adjustment["po_line_id"],
            "need_by_date": adjustment["adjusted_need_by_date"],
        }
    ], "an applied no-op must still be reported as applied"


def test_an_adjusted_row_is_marked_unsaved_and_shows_both_dates(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-031. A session what-if a coordinator cannot distinguish from the
    record is the single confusion the unsaved mark exists to prevent."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    adjusted = date.fromisoformat(line["need_by_date"]) - timedelta(days=5)
    row = _row(
        _get(client, need_by_override=[f"{line['po_line_id']}:{adjusted.isoformat()}"]),
        line["po_line_id"],
    )

    assert row["primary"]["need_by"]["date"] == adjusted.isoformat()
    assert row["primary"]["need_by"]["date_of_record"] == line["need_by_date"]
    assert row["primary"]["need_by"]["source"] == "session_override"
    assert row["primary"]["need_by"]["unsaved"] is True


def test_an_adjustment_writes_nothing_to_the_line(
    frozen_run: dict[str, Any], client: Any, connection: Any
) -> None:
    """FR-031. It resets on reload because it was never stored — asserted
    against the table rather than against a second request, which would pass
    even if the write happened and the read happened to be cached."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    adjusted = date.fromisoformat(line["need_by_date"]) - timedelta(days=5)
    _get(client, need_by_override=[f"{line['po_line_id']}:{adjusted.isoformat()}"])

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT need_by_date FROM purchase_order_line WHERE po_line_id = %s",
            (line["po_line_id"],),
        )
        assert cursor.fetchone()[0].isoformat() == line["need_by_date"]

    # And a request without the parameter shows the record again.
    assert (
        _row(_get(client), line["po_line_id"])["primary"]["need_by"]["date"] == line["need_by_date"]
    )


def test_an_adjustment_can_push_a_line_past_the_horizon(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """The state resolves against the *effective* date, not the recorded one.

    A row whose state was resolved from the record while its figures used the
    adjusted date would carry a label and a number that disagree — and the label
    is what tells the coordinator how to read the number.
    """
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    far = date.fromisoformat(frozen_run["run"]["as_of_date"]) + timedelta(days=400)
    row = _row(
        _get(client, need_by_override=[f"{line['po_line_id']}:{far.isoformat()}"]),
        line["po_line_id"],
    )

    assert row["state"] == "beyond_horizon"
    assert row["primary"]["miss_probability"]["measure"] == "upper_bound"


def test_more_than_one_line_may_be_adjusted_at_once(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-031 admits a set, which is why the parameter repeats: a coordinator
    comparing two lines needs both dates moved together."""
    lines = [item for item in frozen_run["lines"] if item["case"] in {"nominal", "exact_harm_tie"}]
    def pulled(item: dict[str, Any]) -> str:
        adjusted = date.fromisoformat(item["need_by_date"]) - timedelta(days=3)
        return f"{item['po_line_id']}:{adjusted.isoformat()}"

    params = [pulled(item) for item in lines]
    body = _get(client, need_by_override=params)
    assert len(body["overrides"]["applied"]) == len(lines)


def test_an_override_naming_an_absent_line_is_reported_not_dropped(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-055. Silently dropping it leaves the coordinator believing an
    adjustment took effect while reading an ordering computed without it."""
    missing = uuid4()
    body = _get(client, need_by_override=[f"{missing}:2026-09-01"])

    assert body["overrides"]["applied"] == []
    assert body["overrides"]["unapplied"] == [
        {"po_line_id": str(missing), "need_by_date": "2026-09-01", "reason": "line_not_found"}
    ]


def test_an_override_naming_a_terminal_line_says_so(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-055's second cause. Distinguished from "not found" because it calls
    for a different action: a delivered line needs no chasing at all."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "terminal_line")
    body = _get(client, need_by_override=[f"{line['po_line_id']}:2026-09-01"])

    assert body["overrides"]["unapplied"] == [
        {
            "po_line_id": line["po_line_id"],
            "need_by_date": "2026-09-01",
            "reason": "line_terminal",
        }
    ]


def test_an_override_naming_an_out_of_scope_line_says_so(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-055's third cause. The remedy is to clear the filter, which is only
    discoverable if the cause is named."""
    line = next(item for item in frozen_run["lines"] if item["project_id"] == "PRJ-002")
    body = _get(
        client,
        project_id="PRJ-001",
        need_by_override=[f"{line['po_line_id']}:2026-09-01"],
    )

    assert body["overrides"]["unapplied"][0]["reason"] == "line_out_of_scope"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("not-a-uuid:2026-09-01", "not a UUID"),
        ("11111111-1111-1111-1111-111111111111:2026-13-01", "valid calendar date"),
        ("11111111-1111-1111-1111-111111111111:2026-02-30", "valid calendar date"),
        ("11111111-1111-1111-1111-111111111111", "expected"),
        ("11111111-1111-1111-1111-111111111111:2099-01-01", "years from today"),
    ],
)
def test_a_malformed_or_implausible_adjustment_is_refused_with_its_cause(
    frozen_run: dict[str, Any], client: Any, value: str, reason: str
) -> None:
    """FR-055. A refusal, distinct from a non-application: there is nothing here
    to compute an answer from, so it is a 422 rather than a report.

    `2026-02-30` is in the list because it passes a regex and fails a calendar —
    a validator matching only the shape would accept it and then be asked what
    the thirtieth of February means.
    """
    response = client.get("/api/v1/worklist", params={"need_by_override": [value]})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["parameter"] == "need_by_override"
    assert reason in detail["reason"] or reason in detail["detail"]


def test_a_duplicate_adjustment_is_refused(frozen_run: dict[str, Any], client: Any) -> None:
    """Two dates for one line, and no rule for which wins. Picking one silently
    would answer a question the coordinator did not ask."""
    identifier = "11111111-1111-1111-1111-111111111111"
    response = client.get(
        "/api/v1/worklist",
        params={"need_by_override": [f"{identifier}:2026-09-01", f"{identifier}:2026-10-01"]},
    )
    assert response.status_code == 422
    assert "twice" in response.json()["detail"]["reason"]


def test_a_set_over_the_cap_is_refused_with_the_cap_stated(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-055. Refused rather than truncated: a truncated set re-ranks the list
    against dates the coordinator did not ask for, and the result looks exactly
    like a correct answer."""
    params = [f"{uuid4()}:2026-09-01" for _ in range(MAX_OVERRIDES + 1)]
    response = client.get("/api/v1/worklist", params={"need_by_override": params})

    assert response.status_code == 422
    assert response.json()["detail"]["max_overrides"] == MAX_OVERRIDES


def test_an_adjustment_before_the_order_date_is_admitted(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """The stored `need_by_date >= order_date` constraint is deliberately not
    applied here. It guards the stored record; this value is never stored, and
    enforcing it would refuse a question the ranking answers correctly."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    before_order = date.fromisoformat(frozen_run["run"]["as_of_date"]) - timedelta(days=60)

    body = _get(client, need_by_override=[f"{line['po_line_id']}:{before_order.isoformat()}"])
    assert len(body["overrides"]["applied"]) == 1
    assert _row(body, line["po_line_id"])["state"] == "already_late"


def test_an_adjustment_moves_the_validator(frozen_run: dict[str, Any], client: Any) -> None:
    """FR-020a admits the applied override set, so a response under one
    adjustment must not be served to a request carrying another."""
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    plain = client.get("/api/v1/worklist").headers["ETag"]
    adjusted = client.get(
        "/api/v1/worklist",
        params={"need_by_override": [f"{line['po_line_id']}:2026-09-01"]},
    ).headers["ETag"]

    assert plain != adjusted
