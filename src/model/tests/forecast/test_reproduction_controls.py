"""T104 — NC-17 [COMPLETES FR-022]: a perturbed artifact fails, and names the line.

`compare.py`'s property tier proves the comparison *function*. What it cannot
reach is the harness's **wiring** — reading both stores, pairing the lines,
applying the tolerance, resolving one of three outcomes and mapping that outcome
onto an exit status — and `test_reproduction.py` exercises every part of that in
the passing direction only. This file plants the failure.

**One stored line's 80th percentile is moved beyond the tolerance, in the
database**, inside this tier's rolled-back transaction, and the recorded run is
then re-read through the job's own reader. The re-fit it is compared against is
the tier's shared one: sampling a third time would measure nothing this file is
about, and the perturbation is deterministic while a re-fit is not — the delta on
the named line is the perturbation itself, so the failure is attributable rather
than merely present.

P80 rather than the median, deliberately. The top of the array is where AD-004's
arithmetic is binding, and a harness comparing only the median — which the
perturbation leaves untouched — passes every assertion in `test_reproduction.py`
and fails here.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import URL, text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, ReproducedRun
from model.forecast import reproduce
from model.forecast.compare import MEDIAN_PROBABILITY, P80_PROBABILITY
from model.forecast.config import REPRODUCTION_TOLERANCE_DAYS
from model.forecast.reproduce import (
    LINE_POSTERIOR_STORE,
    OUTCOME_AGREES,
    OUTCOME_DISAGREES,
    RecordedRun,
    Reproduction,
    ReproductionOutcome,
    compare_reproduction,
    main,
    read_recorded_run,
)

#: Module-level SQL, never assembled from values (Ruff S608).
PERTURB_DRAWS_SQL = text(
    "UPDATE line_posterior SET draws = :draws WHERE run_id = :run_id AND po_line_id = :line"
)

#: How far the perturbed line's upper tail is moved, in days. An order of
#: magnitude past the 5.0-day tolerance, so the breach is unambiguous and the
#: realized delta the harness reports is recognisably this number rather than
#: sampling noise that happened to exceed the bar.
PERTURBATION_DAYS = 50.0


def perturbed_line(recorded: RecordedRun) -> tuple[object, np.ndarray]:
    """The line to move, and the draw vector that moves its P80 and not its median.

    The first line in `line_posterior` by identifier, so the choice is a property
    of the data rather than of which line happened to breach.

    The suffix starts at the 80th percentile's **own** order statistic — the
    element at `ceil(0.8·n)` read as a 1-indexed rank — which is the one place
    this file has to get the convention right for the same reason `compare.py`
    does. Starting one element later leaves the reported P80 untouched and the
    planting invisible, which is precisely the off-by-one `compare.py`'s property
    tier exists to exclude, and is what this control caught when it was written
    with a rounded fraction instead. A suffix rather than a single element,
    because `ck_line_posterior__draws_sorted` requires whatever is stored to
    remain ascending.
    """
    po_line_id = sorted(recorded.artifacts[LINE_POSTERIOR_STORE], key=str)[0]
    draws = np.asarray(recorded.artifacts[LINE_POSTERIOR_STORE][po_line_id].draws, dtype=float)
    rank = math.ceil(P80_PROBABILITY * draws.size)
    moved = draws.copy()
    moved[rank - 1 :] += PERTURBATION_DAYS
    return po_line_id, moved


@pytest.fixture
def perturbed(
    db_session: Session, emitted_run: EmittedRun, reproduced_run: ReproducedRun
) -> tuple[ReproductionOutcome, object]:
    """The shared re-fit compared against a store with one line's tail moved.

    The `UPDATE` is issued against the tier's rolled-back transaction and the run
    is re-read through `read_recorded_run`, so the harness reads the perturbed
    value out of the database rather than being handed one — which is the half of
    the wiring a hand-built record would not exercise.
    """
    recorded = read_recorded_run(db_session, emitted_run.run_id)
    po_line_id, moved = perturbed_line(recorded)
    db_session.execute(
        PERTURB_DRAWS_SQL,
        {
            "draws": [float(value) for value in moved],
            "run_id": emitted_run.run_id,
            "line": po_line_id,
        },
    )
    return (
        compare_reproduction(
            read_recorded_run(db_session, emitted_run.run_id),
            reproduced_run.reproduction.refit,
        ),
        po_line_id,
    )


def test_the_unperturbed_comparison_is_the_one_that_agrees(
    reproduced_run: ReproducedRun,
) -> None:
    """The positive control the planting is measured against.

    Without it a harness that reported `disagrees` unconditionally would pass
    every assertion below, and the control would be evidence about nothing.
    """
    assert reproduced_run.reproduction.outcome.verdict == OUTCOME_AGREES
    assert reproduced_run.reproduction.outcome.exit_status == 0


def test_a_perturbed_p80_makes_the_harness_disagree_and_exit_non_zero(
    perturbed: tuple[ReproductionOutcome, object],
) -> None:
    """NC-17: the verdict flips and the status class goes with it.

    The status is asserted against **zero** rather than against `1`, which is the
    contract FR-017 states on both sides: every refusal and every failing verdict
    in this package shares one non-zero class, and no requirement allocates a
    distinct code to any category.
    """
    outcome, _ = perturbed

    assert outcome.verdict == OUTCOME_DISAGREES
    assert outcome.exit_status != 0


def test_the_failure_names_the_line_and_its_realized_delta(
    perturbed: tuple[ReproductionOutcome, object],
) -> None:
    """ "Naming that line and its realized delta" — as data, not as prose.

    The breach is asserted to be the perturbed line's P80 **and nothing else**,
    which is what makes the failure attributable: a harness that reported every
    line as breached would satisfy "names that line" while telling a reader
    nothing about which artifact moved.
    """
    outcome, po_line_id = perturbed
    breaches = outcome.breaches

    assert len(breaches) == 1
    assert breaches[0].po_line_id == po_line_id
    assert breaches[0].store == LINE_POSTERIOR_STORE
    assert breaches[0].probability == P80_PROBABILITY
    assert breaches[0].delta_days == pytest.approx(-PERTURBATION_DAYS)
    assert abs(breaches[0].delta_days) > REPRODUCTION_TOLERANCE_DAYS


def test_the_same_lines_median_is_untouched_so_a_median_only_harness_would_pass(
    perturbed: tuple[ReproductionOutcome, object],
) -> None:
    """Why the perturbation is placed in the tail rather than across the array.

    The median of the perturbed line is unchanged, so a harness that compared
    only the median — the plausible half-implementation — reports agreement on
    exactly this database. Asserted rather than argued, because it is the reason
    FR-022 names two quantities instead of one.
    """
    outcome, po_line_id = perturbed
    same_line = [row for row in outcome.comparisons if row.po_line_id == po_line_id]
    median = next(row for row in same_line if row.probability == MEDIAN_PROBABILITY)

    assert median.delta_days == pytest.approx(0.0)
    assert median.agrees
    assert len(same_line) == 2


def test_the_console_entry_point_returns_the_outcomes_own_status(
    perturbed: tuple[ReproductionOutcome, object],
    reproduced_run: ReproducedRun,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The last link: the outcome above is what `forecast-reproduce` returns.

    `main` computes nothing of its own — it parses arguments, runs the job and
    returns the outcome's status — so the job is substituted for one returning
    the planted outcome and the entry point's own answer is read back. That is
    what carries "makes `forecast-reproduce` exit non-zero" without sampling a
    third time, and it is a behavioural assertion rather than a scan of the
    function's text: a `main` that started deciding a status of its own fails
    here, and one whose source merely moved does not.

    FR-039 is asserted in the same breath, because this is the only path in the
    tier that observes the entry point completing: standard output carries
    **exactly one** line, the reproduced `run_id`, and nothing else.
    """
    outcome, _ = perturbed
    substituted = Reproduction(
        recorded=reproduced_run.reproduction.recorded,
        refit=reproduced_run.reproduction.refit,
        outcome=outcome,
        report=tmp_path / "unused.md",
    )
    monkeypatch.setattr(reproduce, "run_reproduce", lambda *_, **__: substituted)

    status = main([])
    captured = capsys.readouterr()

    assert outcome.exit_status != 0
    assert status == outcome.exit_status, (
        f"`forecast-reproduce` exited {status} on an outcome whose own status is "
        f"{outcome.exit_status}; the entry point decides nothing and must return it"
    )
    assert captured.out.splitlines() == [str(outcome.run_id)]


def test_the_entry_point_refuses_an_unknown_run_without_sampling(
    database_url: URL, capsys: pytest.CaptureFixture[str]
) -> None:
    """`forecast-reproduce` end to end on a path that costs no fit.

    A run identifier no `forecast_run` row carries: the job refuses before it
    reads an artifact, writes its reason to standard error, puts **nothing** on
    standard output — FR-039's "on any refusal it MUST carry nothing" — and
    returns the same non-zero class a failing verdict does.
    """
    del database_url  # requested so the tier skips consistently without one
    status = main(["--run-id", "00000000-0000-4000-8000-000000000000"])
    captured = capsys.readouterr()

    assert status != 0
    assert captured.out == ""
    assert "forecast-reproduce refused" in captured.err
    assert "00000000-0000-4000-8000-000000000000" in captured.err
