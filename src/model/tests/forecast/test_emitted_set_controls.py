"""T074 — NC-21: a file of an undeclared **kind** must fail the closed-kind check.

FR-040 enumerates three report *kinds*, and `spec.md` records why the earlier
wording — "exactly three files" — was struck on a testable number: FR-037
requires one refusal report **per attempt**, never overwritten, so a second
refusal produces a fourth file and a file-count equality would have failed in
precisely the scenario FR-037 exists to serve. The closed set is over kinds;
instance count within the refusal kind is unbounded by design.

Both halves are planted here, because they fail in opposite directions:

- **a file of an undeclared kind must fail.** Otherwise an artifact can escape
  SC-026's field check simply by not being enumerated — which is the hole DV-041
  calls an equality rather than a containment to close.
- **a third refusal report must pass**, while a file-count equality against
  FR-040's three would reject it. That assertion is what keeps the fix for the
  first half from re-introducing the bug the spec already corrected.

The second planting NC-21 names — an unlisted **field** in the run report — is
asserted through the same closed-schema predicate `test_no_verdict.py` publishes,
imported rather than re-authored. A third copy of a Markdown field parser in this
tier would be a third opinion about what a rendered field is, and DV-041's field
half and DV-021's absence half must agree about that or neither means anything.

**Scope.** `paths.py` resolves a filename form for two of the three kinds; the
reproduction report's form lands with its job (T098), so the classifier below is
closed over the kinds a P1 cut emits and says so rather than pretending to
enumerate a file nothing writes yet. The full-scope **equality** is T073's, which
is ordered after T098 for exactly that reason. This file is the control, and a
control asserts that the predicate refuses — not that the emitted set is complete.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from forecast.conftest import EmittedRun
from forecast.test_no_verdict import report_fields, undeclared_fields
from model.forecast.paths import (
    REFUSAL_REPORT_PREFIX,
    REPORT_SUFFIX,
    RUN_REPORT_PREFIX,
    refusal_report_path,
    run_report_path,
)
from model.forecast.report import EMITTED_REPORT_KINDS, SECTION_FIELDS

#: The kinds a P1 cut emits, keyed by the filename prefix `paths.py` resolves
#: them under. The reproduction report is deliberately absent: its job does not
#: exist yet, and a classifier naming a filename form nothing writes would be a
#: detector that has never detected.
DECLARED_KINDS: dict[str, str] = {
    RUN_REPORT_PREFIX: "run report",
    REFUSAL_REPORT_PREFIX: "refusal report",
}

#: FR-040's count, restated so the assertion below reads against a number rather
#: than against `len` of the thing it is checking.
DECLARED_KIND_COUNT = 3

#: The as-of date and row hash the planted refusal reports are named after. Any
#: well-formed pair does: what is under assertion is the *count* of instances the
#: refusal kind admits, not which input they refused.
REFUSED_AS_OF = date(2026, 4, 1)
REFUSED_ROW_HASH = "sha256:" + "ab" * 32

#: Three attempts a second apart. Three rather than two, so the directory holds
#: four files in total and a file-count equality against FR-040's three fails
#: rather than passing by coincidence.
REFUSED_ATTEMPTS = tuple(datetime(2026, 4, 1, 9, 0, second, tzinfo=UTC) for second in (1, 2, 3))

#: The undeclared kind. Named like something a well-meaning later epic would add
#: — a summary beside the reports — rather than like an obvious stray file.
UNDECLARED_FILE_NAME = f"coverage-summary{REPORT_SUFFIX}"

#: The field planted into a run report, in the form a rendered field takes.
PLANTED_FIELD = "Coverage threshold"


def kind_of(path: Path) -> str | None:
    """Which declared kind a file is an instance of, or `None` for none of them.

    Keyed on the prefix and the suffix `paths.py` publishes, so the classifier
    and the writers cannot disagree about what a report is called. `None` rather
    than a raise, because the caller — not this function — decides whether an
    unclassifiable file is a failure or the thing it went looking for.
    """
    if path.suffix != REPORT_SUFFIX:
        return None
    for prefix, kind in DECLARED_KINDS.items():
        if path.name.startswith(f"{prefix}-"):
            return kind
    return None


def unclassified(root: Path) -> tuple[Path, ...]:
    """Every file under a report root that is an instance of no declared kind.

    The closed-kind predicate, returned as data so a planting can be observed to
    be found. Directories are skipped: a report root holding one would be a
    different question and this rule is about what the jobs *write*.
    """
    return tuple(
        sorted(path for path in root.iterdir() if path.is_file() and kind_of(path) is None)
    )


@pytest.fixture
def report_directory(emitted_run: EmittedRun, tmp_path: Path) -> Path:
    """A copy of the shipped run's report in a directory this test may plant into.

    Copied rather than used in place: the shared run's root is read by every
    other file in this tier, and a control that left an undeclared file behind
    would be planting a failure into its neighbours.
    """
    root = tmp_path / "emitted"
    root.mkdir()
    source = run_report_path(emitted_run.run_id, emitted_run.report_root)
    (root / source.name).write_bytes(source.read_bytes())
    return root


# ---------------------------------------------------------------------------
# The closed-kind predicate
# ---------------------------------------------------------------------------


def test_a_real_emitted_report_root_carries_only_declared_kinds(
    report_directory: Path,
) -> None:
    """The positive control: what the job actually wrote classifies.

    Without it every planting below is satisfied by a classifier that returns
    `None` for everything, which would report the emitted set as entirely
    undeclared and still look green in a controls file.
    """
    assert unclassified(report_directory) == ()
    assert {kind_of(path) for path in report_directory.iterdir()} == {"run report"}


def test_a_planted_file_of_an_undeclared_kind_fails_the_closed_kind_check(
    report_directory: Path,
) -> None:
    """NC-21's first planting: an emitted artifact nobody enumerated.

    This is the file that would otherwise escape SC-026 entirely — not by
    carrying a verdict past the field check, but by never being examined,
    because a containment over the three declared kinds has nothing to say about
    a fourth.
    """
    planted = report_directory / UNDECLARED_FILE_NAME
    planted.write_text("# Coverage summary\n\n- **Coverage**: 0.81\n", encoding="utf-8")

    assert kind_of(planted) is None
    assert unclassified(report_directory) == (planted,)


def test_a_run_report_named_by_something_other_than_a_run_is_still_a_run_report(
    report_directory: Path,
) -> None:
    """The classifier keys on the kind's prefix, not on the identifier after it.

    Stated because the alternative is tempting and wrong: validating the UUID
    would make a malformed run report *undeclared*, which reports a naming
    defect as an unknown artifact and sends the reader looking for a job that
    does not exist.
    """
    plausible = report_directory / f"{RUN_REPORT_PREFIX}-not-a-uuid{REPORT_SUFFIX}"
    plausible.write_text("# Forecast Run Report\n", encoding="utf-8")

    assert kind_of(plausible) == "run report"


def test_three_refusal_reports_are_legal_and_a_file_count_equality_would_reject_them(
    report_directory: Path,
) -> None:
    """The trap FR-037 sets for "exactly three files", planted rather than argued.

    A retry loop refusing three times leaves three refusal reports beside the
    run report, none overwritten. Every one is an instance of a declared kind,
    so the kind-level check passes; a file-count equality against FR-040's three
    fails on the same directory, which is the reason `spec.md` struck that
    wording.
    """
    for attempted_at in REFUSED_ATTEMPTS:
        path = refusal_report_path(REFUSED_AS_OF, REFUSED_ROW_HASH, attempted_at, report_directory)
        path.write_text("# Forecast Refusal Report\n", encoding="utf-8")

    emitted = sorted(report_directory.iterdir())
    kinds = {kind_of(path) for path in emitted}

    assert len(emitted) == len(REFUSED_ATTEMPTS) + 1 > DECLARED_KIND_COUNT
    assert unclassified(report_directory) == ()
    assert kinds == {"run report", "refusal report"}
    assert len({path.name for path in emitted}) == len(emitted), (
        "two refusal reports took the same filename, so the later one overwrote the earlier "
        "and the retry history FR-037 exists to keep is one attempt short"
    )


# ---------------------------------------------------------------------------
# The field half (DV-041's second clause)
# ---------------------------------------------------------------------------


def test_a_planted_unlisted_field_fails_the_run_reports_schema_validation(
    report_directory: Path,
) -> None:
    """NC-21's second planting: a field the run report's closed schema omits.

    The kind check above would pass this file — it is a run report, correctly
    named, in the right directory — so the two halves of DV-041 are genuinely
    independent and each needs its own planting.
    """
    report = next(path for path in report_directory.iterdir() if kind_of(path) == "run report")
    report.write_text(
        report.read_text(encoding="utf-8") + f"\n- **{PLANTED_FIELD}**: 0.80\n",
        encoding="utf-8",
    )
    found = undeclared_fields(report_fields(report), SECTION_FIELDS)

    assert kind_of(report) == "run report"
    assert PLANTED_FIELD in {field for _, field in found}


# ---------------------------------------------------------------------------
# What the report says the set is
# ---------------------------------------------------------------------------


def test_the_report_enumerates_three_kinds_and_this_file_classifies_the_two_p1_emits() -> None:
    """FR-040's count, and an honest statement of what the classifier covers.

    The run report states its own membership, so the three kinds are read from
    the module a reader's document is rendered from. Two of them have a filename
    form today; the reproduction report's arrives with the job that writes it
    (T098), and T073 owns the full-scope equality that ranges over all three.
    Recorded rather than quietly asserted over two, because a closed set checked
    against two thirds of itself is not closed.
    """
    named = {name for name, _ in EMITTED_REPORT_KINDS}

    assert len(EMITTED_REPORT_KINDS) == DECLARED_KIND_COUNT
    assert set(DECLARED_KINDS.values()) < named
    assert named - set(DECLARED_KINDS.values()) == {"reproduction report"}
    assert all(description.strip() for _, description in EMITTED_REPORT_KINDS)
