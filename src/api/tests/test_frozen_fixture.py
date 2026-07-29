"""The frozen fixture's own integrity.

FR-036, FR-037. These run before any test that *reads* the fixture is worth
believing: a fixture missing a boundary case, or carrying a survival curve the
storage layer would reject, produces green tests that prove nothing.

The load itself is the strongest assertion here. Every row goes in through the
migrated schema, so `ck_line_posterior__survival_monotone`,
`ck_line_posterior__draws_sorted`, `ck_line_posterior__residual_matches_grid_tail`
and the rest are enforced by PostgreSQL rather than restated in Python — and a
fixture that violated one could not be seeded at all.
"""

from __future__ import annotations

import json
from typing import Any

from fixtures.frozen_run import FIXTURE_PATH, load_fixture

HORIZON_DAYS = 365
DRAW_COUNT = 4000


def test_the_fixture_loads_through_the_committed_schema(frozen_run: dict[str, Any]) -> None:
    """FR-037. The `frozen_run` fixture seeded it; arriving here means every
    check constraint on every row was satisfied by the real server."""
    assert frozen_run["run"]["horizon_days"] == HORIZON_DAYS
    assert frozen_run["run"]["draw_count"] == DRAW_COUNT


def test_every_boundary_case_has_a_line_behind_it() -> None:
    """FR-036. A boundary case with no fixture line is untested however many
    tests run, so the map from case to line is asserted rather than documented."""
    document = load_fixture()
    lines_by_identifier = {
        f"{line['po_number']}-{line['line_number']}": line for line in document["lines"]
    }

    for case, identifiers in document["cases"].items():
        for identifier in identifiers.split("|"):
            assert identifier in lines_by_identifier, f"{case} names a line that is not present"
            assert lines_by_identifier[identifier]["case"] == case, (
                f"{identifier} is claimed for {case} but carries "
                f"{lines_by_identifier[identifier]['case']!r}"
            )


def test_the_fixture_carries_its_own_provenance() -> None:
    """FR-037. It is synthetic data committed to the repository, so it takes
    generator provenance — and takes no retrieval provenance it does not have."""
    provenance = load_fixture()["provenance"]
    assert provenance["generator"].endswith("generate.py")
    assert isinstance(provenance["seed"], int)
    assert provenance["generated_on"]
    assert provenance["regenerate_command"]
    assert provenance["layer"] == "SYNTHETIC"
    # Asserted over the field names, not the prose. The note *explains* why
    # there is no retrieval provenance, and a substring check over the whole
    # block would forbid saying so.
    assert not {"source_url", "retrieved_at", "retrieval_date", "publisher"} & set(provenance)


def test_the_row_digest_matches_the_rows() -> None:
    """FR-037. The digest is the review surface for a 600 KB generated file:
    nobody reads a draw array by eye, so a silent edit has to be detectable."""
    from hashlib import sha256

    document = load_fixture()
    payload = json.dumps(
        document["lines"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert document["row_digest"] == "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def test_survival_is_the_probability_of_being_late_not_of_being_on_time() -> None:
    """The invariant that would have inverted the whole product.

    `survival[k]` is `count(draws > k) / draw_count` — P(not yet delivered by day
    k), which is P(late) for a need-by at offset k. Taking `1 - survival[k]`
    instead ranks the safest lines first and looks entirely plausible on screen,
    so it is checked here against the draws rather than trusted.
    """
    for line in load_fixture()["lines"]:
        posterior = line["posterior"]
        if posterior is None:
            continue
        draws = posterior["draws"]
        survival = posterior["survival"]
        for day in (1, 30, 200, HORIZON_DAYS):
            expected = sum(1 for draw in draws if draw > day) / len(draws)
            assert abs(survival[day - 1] - expected) < 1e-9, (
                f"{line['po_number']}-{line['line_number']} survival[{day}] disagrees with "
                "its own draws"
            )


def test_the_declared_boundary_values_are_the_ones_actually_realised() -> None:
    """FR-036 names each case by an exact property. Each is checked, because a
    case that only approximately holds does not test the boundary it names."""
    document = load_fixture()
    by_case: dict[str, list[dict[str, Any]]] = {}
    for line in document["lines"]:
        by_case.setdefault(line["case"], []).append(line)

    degenerate = by_case["median_equals_eightieth"][0]["expected"]
    assert degenerate["p50_offset"] == degenerate["p80_offset"], (
        "the near-degenerate posterior must put the median and the eightieth percentile on the "
        "same day exactly — 'close together' does not exercise the equal-quantile display"
    )

    first, second = by_case["exact_harm_tie"]
    assert first["expected"]["expected_harm"] == second["expected"]["expected_harm"]
    assert first["need_by_date"] == second["need_by_date"]
    assert first["criticality"] == second["criticality"], (
        "the tie must exhaust every earlier tiebreak, or po_line_id is never what resolves it "
        "and the tiebreak's totality goes untested"
    )

    assert by_case["no_posterior"][0]["posterior"] is None
    assert by_case["roster_mismatch"][0]["roster_hash"] != document["run"]["roster_hash"]
    assert by_case["roster_mismatch"][0]["roster_hash"].startswith("sha256:")

    assert by_case["need_by_equals_as_of"][0]["need_by_offset"] == 0
    assert by_case["need_by_before_as_of"][0]["need_by_offset"] < 0
    assert 0 < by_case["need_by_between_as_of_and_today"][0]["need_by_offset"] < 3
    assert by_case["need_by_last_in_grid_day"][0]["need_by_offset"] == HORIZON_DAYS
    assert by_case["need_by_day_after_horizon"][0]["need_by_offset"] == HORIZON_DAYS + 1

    tail = by_case["residual_tail_rounds_to_extreme"][0]["posterior"]["residual_tail_mass"]
    assert 0 < tail < 0.005, (
        "the residual tail must be non-zero and round to 0% at whole percent, so the display "
        "has to read '<1%' — a bound of 0% states a certainty the posterior does not carry"
    )

    for case in ("miss_probability_half_percent_up", "miss_probability_half_percent_down"):
        miss = by_case[case][0]["expected"]["miss_probability"]
        half_percents = miss * 200
        assert abs(half_percents - round(half_percents)) < 1e-9, (
            f"{case} must sit exactly on a half-percent boundary, not near one"
        )
        assert abs(miss * 100 - round(miss * 100)) > 1e-9, "…and not on a whole percent"


def test_the_recorded_adjustment_changes_no_ordering() -> None:
    """FR-012. The case needs an adjustment known to change nothing; the
    generator searched for one rather than asserting a hand-picked date."""
    adjustment = load_fixture()["adjustment"]
    assert adjustment["expected_ordering_unchanged"] is True
    assert adjustment["adjusted_need_by_date"] < adjustment["need_by_date_of_record"]
    assert adjustment["expected_harm_after"] > adjustment["expected_harm_before"], (
        "an adjustment that did not move the harm at all would satisfy 'ordering unchanged' "
        "trivially and would not test that an applied change is still acknowledged"
    )


def test_the_ranking_is_totally_ordered() -> None:
    """FR-010, FR-013a. Reproducible across reloads means no two lines may be
    left in an undefined order, tie or no tie."""
    ranking = load_fixture()["ranking"]
    assert [entry["rank"] for entry in ranking] == list(range(1, len(ranking) + 1))
    assert len({entry["po_line_id"] for entry in ranking}) == len(ranking)

    harms = [entry["expected_harm"] for entry in ranking]
    assert harms == sorted(harms, reverse=True)


def test_the_fixture_file_is_committed_where_the_generator_writes_it() -> None:
    """A fixture the generator writes somewhere else is one that silently stops
    being the fixture under test."""
    assert FIXTURE_PATH.exists()
    assert FIXTURE_PATH.name == "fixture.json"
