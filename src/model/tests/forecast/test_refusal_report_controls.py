"""T093 — NC-19: the refusal report's failing directions, in two forms.

T092 asserts the report is emitted and complete. Without this file that proves
only that a green suite is compatible with a complete report — not that an
incomplete one, or an absent one, would be caught. Both plants run the delivered
predicate `check_refusal_report`, imported from T092's module rather than
re-authored, because a control that writes its own copy demonstrates the copy is
falsifiable and says nothing about the original.

**Direction one: no report file at all.** G-8 records that the emitted file and
the stderr text are the only surviving record of why a run refused, so a refusal
that emitted nothing leaves the disclosure resting on nothing. The plant is the
report removed, and the absence must fail the same presence check T092 makes.

**Direction two: a report short of a field.** The interesting omission is not a
missing section — that fails the schema check loudly — but a breach block that
kept its threshold and dropped the direction, which is the exact loss this
epic's own history records the refusal message suffering, and which leaves a
document that reads complete to a human skimming it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forecast.conftest import RefusedInvocation
from forecast.test_refusal_report import check_refusal_report
from model.forecast.paths import REFUSAL_REPORT_PREFIX, REPORT_SUFFIX
from model.forecast.report import REFUSAL_SECTION_TITLES

#: The two fields whose loss the plants below simulate, as the renderer spells
#: them. Both are the *fifth* field of FR-017's set from a skimming reader's
#: point of view: the document still names the metric, the parameter and the
#: realized value, so nothing about it looks truncated.
THRESHOLD_LINE = re.compile(r"^- \*\*Threshold\*\*: .*$\n", re.MULTILINE)
DIRECTION_LINE = re.compile(r"^- \*\*Threshold direction\*\*: .*$\n", re.MULTILINE)

#: A section heading, removed to show the schema check is load-bearing too.
SECTION_LINE = re.compile(r"^## \d+\. Unmet Preconditions$\n", re.MULTILINE)


def emitted_report(invocation: RefusedInvocation) -> Path:
    """The attempt's single report file, located the way T092 locates it."""
    emitted = invocation.emitted_reports

    assert len(emitted) == 1
    return emitted[0]


def assert_a_report_was_emitted(report_root: Path) -> Path:
    """T092's presence check, extracted so a control can fail it.

    Written as a function rather than inline in a test for exactly one reason:
    the first direction of NC-19 is "no report file at all fails", and a check
    that only ever runs where a file exists cannot be shown to notice one that
    does not.
    """
    candidates = sorted(
        path
        for path in report_root.iterdir()
        if path.name.startswith(f"{REFUSAL_REPORT_PREFIX}-") and path.suffix == REPORT_SUFFIX
    )

    assert candidates, (
        f"no refusal report was emitted under {report_root}. A refusal writes no row in "
        f"any store, so this file and the job's standard error are the only surviving "
        f"record of why the run refused (FR-037, G-8)"
    )
    return candidates[0]


def test_the_delivered_report_passes_both_checks(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The setup assertion, without which every plant below could pass vacuously.

    If the real report already failed either check, the two controls would be
    demonstrating that a broken predicate rejects everything rather than that a
    working one rejects the plant.
    """
    emitted = assert_a_report_was_emitted(refused_after_sampling.report_root)

    check_refusal_report(emitted.read_text(encoding="utf-8"))


def test_a_refusal_that_emitted_no_report_fails_the_presence_check(
    tmp_path: Path,
) -> None:
    """NC-19's first direction: an empty report root must not pass.

    The plant is a directory with nothing in it, which is precisely the state a
    refusal that skipped its report would leave — and the state a reader
    investigating a refused run would find. It has to be an assertion failure
    rather than a quiet pass, because there is no row anywhere to notice the
    absence from.
    """
    root = tmp_path / "no-report-was-emitted"
    root.mkdir()

    with pytest.raises(AssertionError, match="no refusal report was emitted"):
        assert_a_report_was_emitted(root)


def test_a_report_carrying_a_file_of_another_kind_still_fails_the_presence_check(
    tmp_path: Path,
) -> None:
    """The near miss: a directory that is not empty, but holds no refusal report.

    A run report sitting beside a refused attempt would satisfy any "some file
    was written" test, and FR-040 closes the emitted set to three *kinds* for
    this reason — the check is for a file of the refusal kind, identified by the
    filename form `paths.py` declares, not for output in general.
    """
    root = tmp_path / "wrong-kind"
    root.mkdir()
    (root / f"run-report-not-a-refusal{REPORT_SUFFIX}").write_text("# not this", encoding="utf-8")

    with pytest.raises(AssertionError, match="no refusal report was emitted"):
        assert_a_report_was_emitted(root)


def test_a_report_that_drops_the_threshold_direction_fails(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """NC-19's second direction, at the field this epic actually lost once.

    The doctored document keeps every section, every breach block, the metric,
    the parameter and the realized value, and drops one line per block. A reader
    is left with a number and a bar and no way to turn them into a verdict
    unless they already know which metrics are floors and which are ceilings —
    which is the whole reason FR-017 states the obligation as a field set.
    """
    original = emitted_report(refused_after_sampling).read_text(encoding="utf-8")
    doctored = DIRECTION_LINE.sub("", original)

    assert doctored != original, "the plant removed nothing, so it demonstrates nothing"

    with pytest.raises(AssertionError, match="Threshold direction"):
        check_refusal_report(doctored)


def test_a_report_that_drops_the_threshold_fails(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The same plant at the other half of the pair.

    A realized value with no bar beside it is a measurement reported without its
    decision criterion, which FR-038 names as the failure mode the whole unit
    exists to prevent. Removed as its own case because the direction line and
    the threshold line are two separate renderer statements and either could go
    missing alone.
    """
    original = emitted_report(refused_after_sampling).read_text(encoding="utf-8")
    doctored = THRESHOLD_LINE.sub("", original)

    assert doctored != original

    with pytest.raises(AssertionError, match="Threshold"):
        check_refusal_report(doctored)


def test_a_report_missing_a_declared_section_fails(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The schema half of the predicate, shown to be load-bearing.

    A refusal report whose preconditions section is absent and one whose
    preconditions were all met read identically to anyone parsing for content,
    which is why all four sections are rendered on every refusal — including the
    one that says "None".
    """
    original = emitted_report(refused_after_sampling).read_text(encoding="utf-8")
    doctored = SECTION_LINE.sub("", original)

    assert doctored != original
    assert len(REFUSAL_SECTION_TITLES) == 4

    with pytest.raises(AssertionError, match="declares sections"):
        check_refusal_report(doctored)
