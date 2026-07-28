"""T102 — NC-4 / DV-016 / SC-039 [COMPLETES FR-023]: the reproduction that warns.

FR-023 has two dispositions and this file owns the one that is **not** a refusal.
A moved fixture digest against an unchanged input row hash is a provenance
warning naming the break: the rows the fit read are unchanged, so the
reproduction is sound and only the chain back to the upstream artifact has
broken. It refuses nothing, writes no artifact of its own, and the run proceeds
to completion with a zero exit.

NC-4 counts **two** cases and they fail in opposite directions — the warning
being named, and the run reaching the far side of the decision. A job that
refused would satisfy the first and fail the second; a job that proceeded in
silence would satisfy the second and fail the first. `test_reproduce_refusals.py`
carries the refusing half, so the two dispositions are separately evidenced,
which is the clause T127 recorded as owed on the reproduction path.

`test_fixture_digest.py` asserts the same rule on the **fit**. This is the same
break observed on the other job, against the same constructed root, because
FR-023's warning reaches the reproduction and nothing in the fit's evidence says
so.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import Engine

from forecast.conftest import EmittedRun, ReproducedRun
from model.forecast.manifest import read_fixture_provenance
from model.forecast.paths import REPRODUCTION_REPORT_PREFIX
from model.forecast.read import read_lines_and_events
from model.forecast.reproduce import OUTCOME_DISAGREES, Reproduction, run_reproduce
from model.forecast.serialize import input_data_hash

#: The field a reader looks for in the emitted report, and the two dispositions
#: it can carry. Named as constants so the warning assertion and its control
#: below read the same field rather than two spellings of it.
AGREEMENT_FIELD = "- **Fixture digest agreement**:"
BREAK_PHRASE = "provenance warning"
AGREEING_PHRASE = "agrees with the digest this run recorded"


@pytest.fixture(scope="module")
def warned_reproduction(
    engine: Engine, emitted_run: EmittedRun, moved_fixture_root: Path, tmp_path_factory
) -> tuple[Reproduction, str, Path]:
    """One reproduction driven against the moved fixture root, with its stream.

    A second re-fit at the committed shape, and the tier pays for it because the
    disposition under assertion is only observable on the far side of one: the
    warning is decided before the sampler starts, but "the run completes" is a
    claim about what happens after it.

    Driven through `run_reproduce` rather than the console script because the
    mutated root is an argument the job takes and the command line does not
    expose — the decision is made in the same function either way.
    """
    log = io.StringIO()
    root = tmp_path_factory.mktemp("warned-reproduction-reports")
    reproduction = run_reproduce(
        engine,
        emitted_run.run_id,
        report_root=root,
        repo_root=moved_fixture_root,
        log=log,
    )
    return reproduction, log.getvalue(), root


def test_the_moved_fixture_is_a_warning_and_the_reproduction_completes(
    engine: Engine,
    emitted_run: EmittedRun,
    moved_fixture_root: Path,
    warned_reproduction: tuple[Reproduction, str, Path],
) -> None:
    """DV-016 on the reproduction path: it does not refuse, and it finishes.

    Four things together, because no three of them exclude the wrong behaviour:
    the fixture digest really moved, the input row hash really did not, the job
    returned a completed comparison rather than raising, and its exit status is
    zero. A refusal would have raised before the sampler and produced no outcome
    at all.
    """
    reproduction, _, _ = warned_reproduction
    observed = read_fixture_provenance(moved_fixture_root)
    with engine.connect() as connection:
        rows_now = input_data_hash(read_lines_and_events(connection))

    assert observed.observed_digest != reproduction.recorded.input_fixture_digest
    assert rows_now == reproduction.recorded.input_data_hash, (
        "the row hash moved as well, so this run does not exercise DV-016's case — the rule "
        "is about a moved fixture digest against an *unchanged* input row hash"
    )
    assert reproduction.outcome.comparisons
    assert reproduction.outcome.exit_status == 0
    assert reproduction.outcome.verdict != OUTCOME_DISAGREES, (
        "the warning changed the verdict; a broken chain back to the upstream artifact is "
        "not a disagreement between two runs, and FR-023 makes the run proceed"
    )


def test_the_warning_names_the_break_on_both_of_its_surfaces(
    warned_reproduction: tuple[Reproduction, str, Path],
) -> None:
    """G-16: no column records that the warning fired, so the pair must.

    The stream and the emitted report are the whole of the record on this path as
    on the fit's, and both are checked with **both digests** present — a warning
    that named the break without the values it compared would leave a reader
    holding a sentence rather than evidence.
    """
    reproduction, diagnostics, _ = warned_reproduction
    fixture = reproduction.outcome.fixture
    body = reproduction.report.read_text(encoding="utf-8")

    assert not fixture.agrees
    assert not fixture.agrees_with_recorded

    assert BREAK_PHRASE in diagnostics
    assert fixture.observed_digest in diagnostics
    assert fixture.recorded_digest in diagnostics

    assert AGREEMENT_FIELD in body
    assert BREAK_PHRASE in body
    assert fixture.observed_digest in body
    assert fixture.recorded_digest in body


def test_the_warning_writes_no_artifact_of_its_own(
    warned_reproduction: tuple[Reproduction, str, Path],
) -> None:
    """FR-023: the warning "refuses nothing, writes no artifact of its own".

    Exactly one file lands, and it is the reproduction report the job emits on
    every completed comparison — not a second one recording the warning. A
    refusal report appearing here would mean the job took the other disposition.
    """
    reproduction, _, root = warned_reproduction
    emitted = sorted(root.iterdir())

    assert emitted == [reproduction.report]
    assert emitted[0].name.startswith(f"{REPRODUCTION_REPORT_PREFIX}-")


def test_the_unmoved_fixture_records_the_agreeing_disposition_instead(
    reproduced_run: ReproducedRun,
) -> None:
    """The other side of the same field, so the two dispositions are separable.

    Without this, the warning assertions above would pass against a report that
    printed the break unconditionally. The tier's shared reproduction runs
    against the committed fixture and records agreement in the same field, which
    is what makes the field a measurement rather than a caption.
    """
    body = reproduced_run.reproduction.report.read_text(encoding="utf-8")

    assert reproduced_run.reproduction.outcome.fixture.agrees
    assert AGREEMENT_FIELD in body
    assert AGREEING_PHRASE in body
    assert BREAK_PHRASE not in body
    assert BREAK_PHRASE not in reproduced_run.diagnostics
