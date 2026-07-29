"""The worklist endpoint, against a real database.

Phase 2 covers FR-015 — the no-active-run state — which is the one state
reachable with an empty `forecast_run` and the one every other state is built
on top of. It is a P1 acceptance scenario in its own right (US3 scenario 1),
not a placeholder.

Building it first is deliberate. If the figures came first, every later state
would be a subtraction from a row that assumes figures are present, and the
absent case would be whatever fell out. Building the absence first makes the
presence the special case, which is the direction Principle III points.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

#: Every need-by here is expressed against the run anchor in conftest, so a
#: fixture's meaning does not drift as the wall clock advances.
AS_OF = date(2026, 6, 1)

#: FR-012's stated domain: the digest of the empty sequence, carried by every
#: response that ranks nothing. Written out rather than imported from the
#: implementation — a test that computes the expected value the same way the
#: code does asserts only that the code is self-consistent.
EMPTY_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_no_active_run_is_reported_as_a_state_not_an_error(
    empty_worklist: Any, client: Any
) -> None:
    """FR-015, US3 scenario 1.

    A 404 or a 500 here would make the honest empty state look like a broken
    page, and send a coordinator to chase an engineer rather than a vendor.
    """
    response = client.get("/api/v1/worklist")
    assert response.status_code == 200, response.text
    assert "no_active_run" in response.json()["page_states"]


def test_every_open_line_is_listed_when_no_run_is_active(
    empty_worklist: Any, client: Any, insert_open_line: Callable[..., Any]
) -> None:
    """FR-015. The lines exist and the coordinator can still see them.

    Hiding them would be the easier implementation and the wrong one: a line
    invisible because no forecast covers it is exactly the line most likely to
    be forgotten.
    """
    for offset, po_number in ((30, "PO-1000"), (60, "PO-1001")):
        insert_open_line(po_number=po_number, need_by_date=AS_OF + timedelta(days=offset))

    body = client.get("/api/v1/worklist").json()
    assert body["counts"]["total"] == 2
    identities = {
        (row["primary"]["identity"]["po_number"], row["primary"]["identity"]["line_number"])
        for row in body["unranked"]
    }
    assert identities == {("PO-1000", 1), ("PO-1001", 1)}


def test_no_risk_figure_appears_anywhere_without_an_active_run(
    empty_worklist: Any, client: Any, insert_open_line: Callable[..., Any]
) -> None:
    """FR-015, SC-007. The load-bearing assertion of the whole feature.

    Asserted over the serialised rows rather than field by field: a future field
    carrying a zero would pass a field-by-field check and fail this one, which
    is the direction the risk actually runs. Scoped to the rows because `meta`
    legitimately publishes `draw_count` and the percentile convention — those
    describe how figures would be read, and name no line's risk.
    """
    insert_open_line(need_by_date=AS_OF + timedelta(days=30))
    body = client.get("/api/v1/worklist").json()

    assert body["ranked"] == [], "a line cannot be ranked when nothing ranked it"
    assert body["counts"]["ranked"] == 0

    serialised = repr(body["unranked"]).lower()
    for forbidden in ("probability", "percent", "p50", "p80", "quantile", "harm", "margin"):
        assert forbidden not in serialised, (
            f"{forbidden!r} reached a row with no active forecast run; "
            "an absent figure rendered as a value is worse than no interface"
        )


def test_unranked_rows_carry_no_property_that_could_hold_a_figure(
    empty_worklist: Any, client: Any, insert_open_line: Callable[..., Any]
) -> None:
    """FR-016, FR-054. Structural absence, not an empty value.

    A row with a nullable `probability` field renders a dash, and a dash reads
    as a figure. The property is absent from the shape entirely.
    """
    insert_open_line(need_by_date=AS_OF + timedelta(days=30))
    row = client.get("/api/v1/worklist").json()["unranked"][0]

    assert set(row) == {"po_line_id", "state", "primary"}
    assert set(row["primary"]) == {"identity", "need_by"}
    assert row["state"] == "no_active_run"


def test_the_response_states_which_day_it_resolved_against(
    empty_worklist: Any, client: Any
) -> None:
    """FR-038. `today` and its zone travel with the response.

    A consumer that used its own clock could disagree with the server about
    which lines have passed, so the response says which day it meant.
    """
    meta = client.get("/api/v1/worklist").json()["meta"]
    assert date.fromisoformat(meta["today"])
    assert meta["timezone"]
    assert meta["forecast_run"] is None


def test_the_response_publishes_the_conventions_its_figures_are_read_under(
    empty_worklist: Any, client: Any
) -> None:
    """FR-003. Published from `schema_constants`, not compiled into either tier.

    Asserted even in the state that shows no figures: the conventions describe
    how a figure would be read, and a page that only learns them once figures
    appear has no way to state them alongside the first one.
    """
    conventions = client.get("/api/v1/worklist").json()["meta"]["conventions"]
    assert conventions["draw_count"] == 4000
    assert conventions["percentile_convention"] == "nearest_rank_one_based_no_interpolation"
    assert conventions["anchor_date_convention"] == "run_as_of_date"


def test_the_offered_sort_keys_are_exactly_the_four(empty_worklist: Any, client: Any) -> None:
    """FR-026, FR-032. Enumerated by the server so the claim is testable here
    rather than against a component's source.

    The absence is the point: no key orders lines by a single delivery date or
    by one quantile alone, which is how the point estimate would re-enter
    through the sort control.
    """
    sort = client.get("/api/v1/worklist").json()["sort"]
    assert [option["key"] for option in sort["options"]] == [
        "expected_harm",
        "need_by_date",
        "criticality",
        "calendar_margin",
    ]
    assert sort["key"] == "expected_harm"
    assert sort["direction"] == "desc"
    assert [option for option in sort["options"] if option["is_active"]][0]["key"] == sort["key"]
    assert sort["tiebreak"][-1] == "po_line_id asc", "the tiebreak must be total"


def test_an_unoffered_sort_key_is_refused(empty_worklist: Any, client: Any) -> None:
    """FR-026. `p50` is exactly the key that would reintroduce the point
    estimate, so it must not be silently ignored and fall back to the default."""
    assert client.get("/api/v1/worklist", params={"sort": "p50"}).status_code == 422


def test_a_response_that_ranks_nothing_carries_the_empty_ordering_digest(
    empty_worklist: Any, client: Any
) -> None:
    """FR-012. Defined on every response, including this one."""
    assert client.get("/api/v1/worklist").json()["ordering_digest"] == EMPTY_DIGEST


def test_a_terminal_line_is_not_on_the_worklist(
    empty_worklist: Any,
    client: Any,
    insert_open_line: Callable[..., Any],
    close_line: Callable[..., None],
) -> None:
    """FR-022. Delivered lines are not what a coordinator chases.

    The line is closed by walking its real event chain, because that is the only
    way E003's schema permits — which also means this asserts against a line
    that is closed the way production lines are closed.
    """
    delivered = insert_open_line(po_number="PO-1000", need_by_date=AS_OF + timedelta(days=30))
    insert_open_line(po_number="PO-1001", need_by_date=AS_OF + timedelta(days=45))
    close_line(delivered)

    body = client.get("/api/v1/worklist").json()
    assert body["counts"]["total"] == 1
    assert body["unranked"][0]["primary"]["identity"]["po_number"] == "PO-1001"


def test_an_empty_scope_is_distinguishable_from_no_run(empty_worklist: Any, client: Any) -> None:
    """FR-042. "Your filter matched nothing" and "there is no forecast" are
    different statements about a coordinator's own data."""
    scoped = client.get("/api/v1/worklist", params={"project_id": "PRJ-004"}).json()
    assert "empty_filter" in scoped["page_states"]

    unscoped = client.get("/api/v1/worklist").json()
    assert "empty_filter" not in unscoped["page_states"], (
        "an unfiltered worklist with no open lines matched no filter, so saying "
        "a filter matched nothing would be false and FR-018's enumeration is canonical"
    )


def test_the_scope_control_can_be_left_without_a_second_request(
    empty_worklist: Any, client: Any, insert_open_line: Callable[..., Any]
) -> None:
    """FR-025. `available_projects` is the full set even while a filter is
    active — the domain of the control is not a function of its current value."""
    insert_open_line(project_id="PRJ-001", po_number="PO-1000", need_by_date=AS_OF)
    insert_open_line(project_id="PRJ-002", po_number="PO-2000", need_by_date=AS_OF)

    scoped = client.get("/api/v1/worklist", params={"project_id": "PRJ-001"}).json()
    assert scoped["scope"]["project_id"] == "PRJ-001"
    assert [item["project_id"] for item in scoped["scope"]["available_projects"]] == [
        "PRJ-001",
        "PRJ-002",
    ]
    assert all(item["open_line_count"] == 1 for item in scoped["scope"]["available_projects"])


def test_a_malformed_scope_is_refused_by_name(empty_worklist: Any, client: Any) -> None:
    """FR-025's P1 half. A typo becomes a 422 naming the field rather than a
    silently empty worklist that reads as good news."""
    response = client.get("/api/v1/worklist", params={"project_id": "nope"})
    assert response.status_code == 422
    assert "project_id" in repr(response.json())


def test_the_counts_reconcile(
    empty_worklist: Any, client: Any, insert_open_line: Callable[..., Any]
) -> None:
    """SC-023. `ranked + unranked == total` separates a line correctly excluded
    from one a defective query dropped — which otherwise render identically."""
    for index in range(3):
        insert_open_line(po_number=f"PO-100{index}", need_by_date=AS_OF + timedelta(days=index))

    counts = client.get("/api/v1/worklist").json()["counts"]
    assert counts["ranked"] + counts["unranked"] == counts["total"] == 3
