"""US1 — the ranked worklist, against the frozen fixture.

The independent test the task list states for this story: load the worklist
against the frozen fixture and confirm the rendered order, figures and per-row
decomposition match expected values exactly.

"Exactly" is the operative word. The expected values are read from the fixture
document, which was computed by the generator and committed — not recomputed
here alongside the implementation, which would assert only that two copies of
the same arithmetic agree.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

#: The run's anchor. Two days after it, so the run is fresh and no line has been
#: passed by the calendar except the one built to be.
TODAY = date(2026, 6, 3)


@pytest.fixture
def worklist(frozen_run: dict[str, Any], client: Any, monkeypatch: Any) -> dict[str, Any]:
    """One response, resolved against a fixed `today`.

    FR-038's injected date is what makes this fixture stable: without it the
    row states would change overnight and this suite would fail on a date
    nobody chose.
    """
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    response = client.get("/api/v1/worklist")
    assert response.status_code == 200, response.text
    return response.json()


def _at(day: date) -> Any:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("UTC"))


def _by_identifier(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    def key(row: dict[str, Any]) -> str:
        identity = row["primary"]["identity"]
        return f"{identity['po_number']}-{identity['line_number']}"

    return {key(row): row for row in rows}


def test_the_rendered_order_matches_the_fixture_exactly(
    worklist: dict[str, Any], frozen_run: dict[str, Any]
) -> None:
    """FR-001, FR-010. Worst first, with the tiebreak resolving the rest.

    The expected order was computed by the generator and committed, so this
    compares the endpoint against a recorded answer rather than against a second
    implementation of the same sort.
    """
    rendered = [row["po_line_id"] for row in worklist["ranked"]]
    expected = [entry["po_line_id"] for entry in frozen_run["ranking"]]
    assert rendered == expected


def test_the_ranks_are_one_based_and_dense(worklist: dict[str, Any]) -> None:
    """FR-048. Position reaches a coordinator who is not reading visually, so it
    has to be a field — and a field with a gap in it is not a position."""
    assert [row["rank"] for row in worklist["ranked"]] == list(
        range(1, len(worklist["ranked"]) + 1)
    )


def test_the_exact_tie_is_resolved_by_the_identifier(
    worklist: dict[str, Any], frozen_run: dict[str, Any]
) -> None:
    """FR-010. The two tied lines share harm, need-by and criticality, so the
    identifier is what actually decides — which is the case that proves the
    tiebreak is total rather than merely long."""
    rows = _by_identifier(worklist["ranked"])
    first, second = rows["PO-4472-1"], rows["PO-4472-2"]

    assert abs(first["rank"] - second["rank"]) == 1, "the tied pair must be adjacent"
    earlier = first if first["rank"] < second["rank"] else second
    later = second if earlier is first else first
    assert earlier["po_line_id"] < later["po_line_id"]


def test_a_nominal_row_carries_exactly_the_four_comparison_quantities(
    worklist: dict[str, Any],
) -> None:
    """FR-027. Four, and no fifth — the cap is what keeps a row scannable, and
    it is only decidable because the shape is closed."""
    row = _by_identifier(worklist["ranked"])["PO-4471-1"]
    assert set(row["primary"]) == {"identity", "need_by", "miss_probability", "duration_pair"}
    assert set(row["secondary"]) == {"as_of_date", "criticality", "calendar_margin_days"}


def test_the_miss_probability_matches_the_stored_survival_value(
    worklist: dict[str, Any], frozen_run: dict[str, Any]
) -> None:
    """FR-008, FR-020, and the inverted-formula guard.

    `survival[k]` is read directly as the probability of *missing*. A row whose
    displayed miss probability equals the complement of the stored value would
    pass every other test here and rank the safest lines first.
    """
    rows = _by_identifier(worklist["ranked"])
    fixture_lines = {
        f"{line['po_number']}-{line['line_number']}": line for line in frozen_run["lines"]
    }

    for identifier, row in rows.items():
        figure = row["primary"]["miss_probability"]
        stored = fixture_lines[identifier].get("expected", {}).get("miss_probability")
        if figure is None or stored is None or figure["measure"] != "point":
            continue
        if figure["bounded"]:
            continue
        # Within half a percent of the stored value, rather than equal to a
        # rounding this test performs itself. Python's `round` is half-to-even —
        # the rule FR-008 explicitly rejects — so an exact comparison here would
        # fail the two fixture lines built to sit on a half-percent boundary and
        # would be asserting the wrong rule while appearing to assert the right
        # one. The half-up rule has its own test; this one is the inversion
        # guard, and a complement would be off by far more than half a percent.
        assert abs(figure["miss"]["percent"] - stored * 100) <= 0.5, (
            f"{identifier} displays {figure['miss']['percent']}% against a stored {stored} — "
            "if these are complements, the worklist is ranking the safest lines first"
        )


def test_the_half_percent_boundary_pair_rounds_half_up_in_both_directions(
    worklist: dict[str, Any],
) -> None:
    """FR-008's worked rule, at the two fixture lines built to sit on it.

    35.5% and 36.5% exactly. Half-up gives 36% and 37%; Python's default
    half-to-even would give 36% and 36% — identical figures for measurably
    different risks, on two rows a coordinator sees side by side.
    """
    rows = _by_identifier(worklist["ranked"])
    assert rows["PO-4477-1"]["primary"]["miss_probability"]["miss"]["percent"] == 36
    assert rows["PO-4477-2"]["primary"]["miss_probability"]["miss"]["percent"] == 37


def test_a_displayed_pair_of_integers_sums_to_one_hundred(worklist: dict[str, Any]) -> None:
    """FR-006, FR-008. The complement is subtracted from the displayed integer.

    Checked over every ranked row rather than one: independent rounding produces
    a pair summing to 101 only at particular values, so a single-row assertion
    would pass on most fixtures.
    """
    for row in worklist["ranked"]:
        figure = row["primary"]["miss_probability"]
        if figure is None or figure["bounded"]:
            continue
        assert figure["miss"]["percent"] + figure["on_time"]["percent"] == 100


def test_a_bounded_figure_pairs_with_a_bound_and_carries_no_numeral(
    worklist: dict[str, Any],
) -> None:
    """FR-008. A bound paired with a flat certainty reintroduces through the
    complement exactly what the bound removes."""
    for row in worklist["ranked"]:
        figure = row["primary"]["miss_probability"]
        if figure is None or not figure["bounded"]:
            continue
        assert figure["miss"]["percent"] is None
        assert figure["on_time"]["percent"] is None
        assert {figure["miss"]["display"], figure["on_time"]["display"]} == {"<1%", ">99%"}


def test_the_quantile_pair_travels_as_one_anchored_unit(worklist: dict[str, Any]) -> None:
    """FR-003, FR-004, FR-049. One object, not two sibling scalars.

    The anchor is required on every pair: an unanchored median of thirty days on
    a ten-day-old run reads ten days more optimistic than it is.
    """
    pair = _by_identifier(worklist["ranked"])["PO-4471-1"]["primary"]["duration_pair"]
    assert set(pair) == {
        "unit",
        "counted_from",
        "as_of_date",
        "median",
        "eightieth",
        "reference_class",
    }
    assert pair["counted_from"] == "run_as_of_date"
    assert pair["as_of_date"] == "2026-06-01"
    assert pair["median"]["quantile_percent"] == 50
    assert pair["eightieth"]["quantile_percent"] == 80


def test_each_quantile_states_its_complementary_frequency(worklist: dict[str, Any]) -> None:
    """FR-005. "Half of comparable orders land by this day" reads as a
    proportion of a population; "the median is 34 days" reads as a commitment
    about this line, which is the misreading the frequency framing removes."""
    pair = _by_identifier(worklist["ranked"])["PO-4471-1"]["primary"]["duration_pair"]
    assert pair["median"]["later_percent"] == 50
    assert pair["eightieth"]["later_percent"] == 20
    assert pair["reference_class"]["basis"] == "posterior_predictive_draws"
    assert pair["reference_class"]["draw_count"] == 4000


def test_the_near_degenerate_pair_is_still_a_pair(worklist: dict[str, Any]) -> None:
    """FR-004. Both members are present even where they are equal.

    The contract offers no way to collapse them to one figure, and this is the
    line that would tempt an implementation to: the two quantiles land on the
    same day, and showing "34 days" once would look like a tidy simplification
    rather than the point estimate it is.
    """
    pair = _by_identifier(worklist["ranked"])["PO-4471-2"]["primary"]["duration_pair"]
    assert pair["median"]["days"] == pair["eightieth"]["days"]
    assert pair["median"]["later_percent"] != pair["eightieth"]["later_percent"]


def test_a_line_beyond_the_horizon_states_a_bound_and_stays_ranked(
    worklist: dict[str, Any],
) -> None:
    """FR-017. Expected harm comes from the draws and does not need the survival
    grid, so the line keeps its place — only the probability degrades to a
    bound, and it says so rather than leaving the interface to infer it."""
    row = _by_identifier(worklist["ranked"])["PO-4476-1"]
    assert row["state"] == "beyond_horizon"
    assert row["primary"]["miss_probability"]["measure"] == "upper_bound"


def test_a_bound_that_would_round_to_zero_degrades_to_the_bounded_wording(
    worklist: dict[str, Any],
) -> None:
    """FR-017's second sentence. "At most 0% late" asserts a certainty, which is
    the one thing a bound must never do."""
    figure = _by_identifier(worklist["ranked"])["PO-4476-2"]["primary"]["miss_probability"]
    assert figure["measure"] == "upper_bound"
    assert figure["bounded"]
    assert figure["miss"]["display"] == "<1%"


def test_an_already_late_line_keeps_its_quantiles_and_loses_only_the_probability(
    worklist: dict[str, Any],
) -> None:
    """FR-030, FR-054. The miss probability is 1 by construction and therefore
    uninformative; how much *further* slip is coming is the open question, and
    the quantile pair is what answers it.

    The withheld figure is an explicit empty, not a structural absence — the
    figure exists in the general case and this row's state removed it.
    """
    for identifier in ("PO-4474-1", "PO-4474-2"):
        row = _by_identifier(worklist["ranked"])[identifier]
        assert row["state"] == "already_late"
        assert row["primary"]["miss_probability"] is None
        assert row["primary"]["duration_pair"]["median"]["days"] >= 0


def test_the_row_decomposes_its_own_rank(worklist: dict[str, Any]) -> None:
    """FR-009, SC-004. The score has exactly three inputs and the row carries
    all three: the distribution (as the quantile pair), the calendar margin the
    draws must exceed before any of it counts as overrun, and the criticality
    the overrun is multiplied by."""
    row = _by_identifier(worklist["ranked"])["PO-4471-1"]
    assert row["primary"]["duration_pair"]["median"]["days"] > 0
    assert 1 <= row["secondary"]["criticality"] <= 5
    assert row["secondary"]["calendar_margin_days"] == 60


def test_the_calendar_margin_goes_negative_and_takes_no_forecast_input(
    worklist: dict[str, Any],
) -> None:
    """FR-009. A margin derived from a predicted delivery date would let a
    reader subtract it from the need-by date and reconstruct that date."""
    assert (
        _by_identifier(worklist["ranked"])["PO-4474-2"]["secondary"]["calendar_margin_days"] == -10
    )


def test_no_row_publishes_the_harm_score(worklist: dict[str, Any]) -> None:
    """FR-041. With criticality displayed beside it, the score would surrender
    the mean overrun to one division — and `need_by + mean_overrun` is a mean
    delivery date, which FR-007 forbids on every surface including this one."""
    serialised = repr(worklist["ranked"]).lower()
    for forbidden in ("expected_harm", "harm", "mean_overrun", "overrun"):
        assert forbidden not in serialised, f"{forbidden!r} reached a row"


def test_no_row_publishes_the_draws(worklist: dict[str, Any]) -> None:
    """FR-053. A client holding four thousand draws is one aggregation away from
    the point estimate this product exists to refuse.

    Checked by walking the payload for a numeric array, not by searching for the
    word: `posterior_predictive_draws` is the reference class's basis and names
    where the figures came from, which is provenance the response is *required*
    to carry (FR-005). A substring check would forbid saying so.
    """

    def arrays(node: Any, path: str = "") -> list[str]:
        if isinstance(node, list):
            if node and all(isinstance(item, int | float) for item in node):
                return [f"{path} ({len(node)} numbers)"]
            return [found for i, item in enumerate(node) for found in arrays(item, f"{path}[{i}]")]
        if isinstance(node, dict):
            return [found for key, item in node.items() for found in arrays(item, f"{path}.{key}")]
        return []

    assert arrays(worklist) == [], "a numeric array reached the payload"


def test_the_excluded_group_holds_its_own_order_and_carries_no_figures(
    worklist: dict[str, Any],
) -> None:
    """FR-016, FR-045. The exclusions travel as a disjoint collection, so no
    consumer can sort an excluded line into the ranking."""
    unranked = _by_identifier(worklist["unranked"])
    assert set(unranked) == {"PO-4473-1", "PO-4473-2"}

    for row in worklist["unranked"]:
        assert set(row["primary"]) == {"identity", "need_by"}

    dates = [row["primary"]["need_by"]["date"] for row in worklist["unranked"]]
    assert dates == sorted(dates), "need-by ascending, under every sort key"


def test_the_terminal_line_appears_in_neither_group(worklist: dict[str, Any]) -> None:
    """FR-022. Delivered lines are not what a coordinator chases."""
    everything = _by_identifier(worklist["ranked"] + worklist["unranked"])
    assert "PO-4479-1" not in everything


def test_the_counts_reconcile_across_both_groups(worklist: dict[str, Any]) -> None:
    """SC-023. A line a defective query dropped shows up here as a shortfall;
    without the reconciliation it renders identically to one correctly excluded."""
    counts = worklist["counts"]
    assert counts["ranked"] == len(worklist["ranked"])
    assert counts["unranked"] == len(worklist["unranked"])
    assert counts["ranked"] + counts["unranked"] == counts["total"]


def test_the_ordering_digest_changes_with_the_order_and_not_otherwise(
    frozen_run: dict[str, Any], client: Any, monkeypatch: Any
) -> None:
    """FR-012. The value the interface compares to answer "did the order change?"

    Two requests a day apart return the same ranking and therefore the same
    digest, even though `meta.today` and the run's age both differ — which is
    what makes the digest a statement about *ordering* rather than about the
    response as a whole.
    """
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    first = client.get("/api/v1/worklist").json()

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY + timedelta(days=1)))
    second = client.get("/api/v1/worklist").json()

    assert first["meta"]["today"] != second["meta"]["today"]
    assert first["ordering_digest"] == second["ordering_digest"]
    assert first["ordering_digest"] != (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ), "a ranking with rows in it must not carry the empty sequence's digest"
