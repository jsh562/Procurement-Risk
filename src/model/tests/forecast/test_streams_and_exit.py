"""T122 — DV-042 / SC-038 / NC-24: two streams and an exit status, as a process.

FR-039's contract has three clauses and this file has three tests, because each
fails on its own. **Standard output** carries exactly one line on a run that
ships — the `run_id` — and nothing at all on a refusal. **Every diagnostic**
reaches standard error. **The exit status** is zero exactly on completion and one
non-zero class otherwise.

Measured over real processes rather than by calling `main()` in-process: an
in-process call produces no exit status, and `run_fit` binds its diagnostic
stream at import, so a redirected `sys.stderr` would leave the notes going
somewhere a test could still capture them and a shell could not.

Two refusals rather than one, and they refuse for unrelated reasons — an anchor
before the cohort exists, and an environment that names no database. That is what
"one non-zero class" means: a consumer tests against zero rather than against a
particular value, so two different categories must not be distinguishable by
status.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable
from datetime import date

import pytest

from forecast.conftest import EmittedRun
from model.forecast.config import CHAINS_MIN

#: An anchor before any line was ordered. Every line's event walk is then empty,
#: the sojourn frame has no row, and the job refuses before it samples — which
#: makes this the cheapest refusal that still runs the whole read-and-hash path.
ANCHOR_BEFORE_THE_COHORT = date(2000, 1, 1)

#: The shape the two refusing invocations are asked for. Small because neither
#: reaches the sampler; named rather than defaulted so the refusal is attributable
#: to the argument under test and not to a shape the job disliked.
#:
#: **At the chain minimum, not below it.** US4's T081 made a chain count below
#: `CHAINS_MIN` a pre-sampling precondition that refuses before the job reads
#: anything, so a two-chain invocation here would refuse for the chain count and
#: this module's anchor refusal would never be reached — the assertions would
#: still pass while measuring a different refusal. The value is read from
#: `config.py` rather than written as a literal, so it tracks the published
#: minimum instead of agreeing with it today.
REFUSAL_CHAINS = CHAINS_MIN
REFUSAL_DRAWS = 50

#: The variable `model.schema.url` reads, removed to produce the second refusal.
DATABASE_URL_ENV_VAR = "DATABASE_URL"

#: Phrases the shipped run's diagnostics must carry. Each names a step the job
#: reports, so "every diagnostic reaches standard error" is checked against the
#: stream that must hold them rather than against the absence of output.
REPORTED_STEPS = ("input row hash", "split assignment hash", "published run", "run report at")


@pytest.fixture(scope="module")
def refused_anchor(
    forecast_fit: Callable[..., subprocess.CompletedProcess[str]], tmp_path_factory
) -> subprocess.CompletedProcess[str]:
    """One refusal: an as-of date before the cohort was ordered."""
    root = tmp_path_factory.mktemp("refusal-anchor")
    return forecast_fit(
        [
            "--as-of-date",
            ANCHOR_BEFORE_THE_COHORT.isoformat(),
            "--chains",
            str(REFUSAL_CHAINS),
            "--draws",
            str(REFUSAL_DRAWS),
            "--tune",
            str(REFUSAL_DRAWS),
            "--report-root",
            str(root),
        ]
    )


@pytest.fixture(scope="module")
def refused_environment(
    forecast_fit: Callable[..., subprocess.CompletedProcess[str]],
    emitted_run: EmittedRun,
    tmp_path_factory,
) -> subprocess.CompletedProcess[str]:
    """The other refusal: an environment naming no database at all.

    A different category from the anchor refusal — the environment rather than
    the argument — and it fails before a single row is read, so the two together
    span the range the "one non-zero class" claim quantifies over.
    """
    root = tmp_path_factory.mktemp("refusal-environment")
    environment = dict(os.environ)
    environment.pop(DATABASE_URL_ENV_VAR, None)
    return forecast_fit(
        [
            "--as-of-date",
            emitted_run.as_of_date.isoformat(),
            "--chains",
            str(REFUSAL_CHAINS),
            "--draws",
            str(REFUSAL_DRAWS),
            "--tune",
            str(REFUSAL_DRAWS),
            "--report-root",
            str(root),
        ],
        environment,
    )


def test_standard_output_carries_the_run_id_and_nothing_else(
    emitted_run: EmittedRun,
    refused_anchor: subprocess.CompletedProcess[str],
    refused_environment: subprocess.CompletedProcess[str],
) -> None:
    """DV-042's first clause, in both of its directions.

    One line on a run that ships and it parses as the identifier the artifacts are
    keyed by, so a consumer can pipe the job into a query. Nothing at all on
    either refusal — not a partial line, not a placeholder — because a refusal
    that printed something a consumer could read as an identifier would be worse
    than one that printed a wrong one.
    """
    lines = emitted_run.stdout.splitlines()

    assert lines == [str(emitted_run.run_id)]
    assert uuid.UUID(lines[0]) == emitted_run.run_id
    assert refused_anchor.stdout == ""
    assert refused_environment.stdout == ""


def test_every_diagnostic_reaches_standard_error(
    emitted_run: EmittedRun,
    refused_anchor: subprocess.CompletedProcess[str],
    refused_environment: subprocess.CompletedProcess[str],
) -> None:
    """The second clause: the diagnostics exist, and they are on the other stream.

    Asserted as presence on standard error *and* absence from standard output,
    because a job that emitted no diagnostics at all would satisfy the absence
    half on its own — and the refusal reason is the only surviving record of why a
    run refused, since a refusal writes no row (G-8).
    """
    for phrase in REPORTED_STEPS:
        assert phrase in emitted_run.stderr, (
            f"the shipped run's standard error does not report {phrase!r}; every step the job "
            f"reports goes to this stream and nowhere else"
        )
        assert phrase not in emitted_run.stdout

    for refusal in (refused_anchor, refused_environment):
        assert refusal.stderr.strip(), "a refusal with no reason on standard error records nothing"
        assert "refused" in refusal.stderr.lower()


def test_the_exit_status_is_zero_exactly_on_completion(
    emitted_run: EmittedRun,
    refused_anchor: subprocess.CompletedProcess[str],
    refused_environment: subprocess.CompletedProcess[str],
) -> None:
    """The third clause, including the "one class" half the other two do not reach.

    Zero on the run that shipped. Non-zero on both refusals, and the *same*
    non-zero value for two refusals of entirely different categories — which is
    what lets a consumer test against zero rather than maintaining a table of
    codes that would have to grow with every new refusal reason.
    """
    assert emitted_run.status == 0
    assert refused_anchor.returncode != 0
    assert refused_environment.returncode != 0
    assert refused_anchor.returncode == refused_environment.returncode
