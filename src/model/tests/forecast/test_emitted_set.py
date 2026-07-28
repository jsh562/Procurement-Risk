"""T073 — SC-035 / DV-041: the emitted set is a closed-**kind** equality.

FR-040 enumerates three report kinds and this file asserts that every file the
two jobs write is an instance of one of them — as an equality over kinds, not a
containment, so a file of an undeclared kind fails rather than escaping SC-026's
field check by never being looked at.

**Kinds, not files, and the distinction is load-bearing.** An earlier wording of
FR-040 said "exactly three files"; FR-037 requires one refusal report **per
attempt**, never overwritten, so a retry loop produces a fourth file and a
file-count equality would have failed in exactly the scenario FR-037 exists to
serve. Instance count within a kind is unbounded by design, which is why the
equality below ranges over `kind_of` and not over `len`.

**Deferred to here rather than landed with T074.** The third kind is the
reproduction report, and until T098 emitted one the classifier could only be
closed over the two a P1 cut writes — a detector that has never detected. Every
kind now has a job that writes it and a filename form `paths.py` resolves, so the
equality is over the whole set for the first time.

**The parser is `test_no_verdict.py`'s, imported and not re-authored.** Two of
this epic's reports render declared fields as something other than `- **X**: ` —
`Per-Vendor Shrinkage` and `Per-Line Comparison` use Markdown **table columns**,
and both emitted sets sections render `Report kind` with an em dash rather than a
colon. A validator reading only bullet lines reports five fields missing on the
run report and eight on the reproduction report, on every run. There is exactly
one opinion in this tier about what a rendered field is, and DV-041's field half
and DV-021's absence half both rest on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forecast.conftest import EmittedRun, RefusedInvocation, ReproducedRun
from forecast.test_no_verdict import report_fields, undeclared_fields
from model.forecast.paths import (
    REFUSAL_REPORT_PREFIX,
    REPORT_SUFFIX,
    REPRODUCTION_REPORT_PREFIX,
    RUN_REPORT_PREFIX,
)
from model.forecast.report import EMITTED_REPORT_KINDS, SECTION_FIELDS
from model.forecast.reproduce import REPRODUCTION_SECTION_FIELDS, REPRODUCTION_SECTION_TITLES

#: FR-040's three kinds, keyed by the filename prefix `paths.py` resolves them
#: under. **All three**, which is what `test_emitted_set_controls.py` could not
#: carry: it recorded the reproduction report's absence honestly and left the
#: full-scope equality to this file (A-016).
DECLARED_KINDS: dict[str, str] = {
    RUN_REPORT_PREFIX: "run report",
    REPRODUCTION_REPORT_PREFIX: "reproduction report",
    REFUSAL_REPORT_PREFIX: "refusal report",
}

#: The closed schema of each kind that declares one, keyed by kind. The refusal
#: report is deliberately absent: its schema is a set of *sections* whose
#: field set varies with which gate refused — a pre-sampling refusal renders no
#: threshold direction at all — so `test_refusal_report.py` owns its predicate
#: and restating it here would be a second opinion about it.
DECLARED_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "run report": SECTION_FIELDS,
    "reproduction report": REPRODUCTION_SECTION_FIELDS,
}


def kind_of(path: Path) -> str | None:
    """Which declared kind a file is an instance of, or `None` for none of them.

    Keyed on the prefix and the suffix `paths.py` publishes, so the classifier
    and the writers cannot disagree about what a report is called. `None` rather
    than a raise, because the caller decides whether an unclassifiable file is a
    failure or the thing it went looking for.
    """
    if path.suffix != REPORT_SUFFIX:
        return None
    for prefix, kind in DECLARED_KINDS.items():
        if path.name.startswith(f"{prefix}-"):
            return kind
    return None


def emitted_files(*roots: Path) -> tuple[Path, ...]:
    """Every file the jobs wrote across the roots this tier handed them, sorted."""
    return tuple(
        sorted(
            (path for root in roots for path in root.iterdir() if path.is_file()),
            key=lambda path: path.name,
        )
    )


@pytest.fixture
def every_emitted_file(
    emitted_run: EmittedRun,
    reproduced_run: ReproducedRun,
    refused_after_sampling: RefusedInvocation,
    refused_below_the_chain_minimum: RefusedInvocation,
) -> tuple[Path, ...]:
    """What the two jobs actually wrote in this tier, across all four invocations.

    One shipped fit, one reproduction and two refusals — so all three kinds are
    present and the refusal kind is present at a count above one, which is the
    case the equality has to survive. Read off the roots the jobs were given
    rather than off a list this file keeps, because DV-041 is a claim about what
    the jobs *write*.
    """
    return emitted_files(
        emitted_run.report_root,
        reproduced_run.report_root,
        refused_after_sampling.report_root,
        refused_below_the_chain_minimum.report_root,
    )


# ---------------------------------------------------------------------------
# The kind-level equality (SC-035)
# ---------------------------------------------------------------------------


def test_every_emitted_file_is_an_instance_of_one_of_the_three_declared_kinds(
    every_emitted_file: tuple[Path, ...],
) -> None:
    """SC-035, as an equality over kinds rather than a containment.

    The set of kinds the jobs produced is compared against the set FR-040 names,
    in both directions: an undeclared kind fails as an extra member, and a
    declared kind nothing writes fails as a missing one — which is what the
    reproduction report was until T098 and why this assertion waited for it.
    """
    produced = {kind_of(path) for path in every_emitted_file}

    assert None not in produced, (
        f"{[path.name for path in every_emitted_file if kind_of(path) is None]} are "
        f"instances of no declared kind; a file nobody enumerated escapes SC-026's field "
        f"check by never being looked at, which is why DV-041 is an equality"
    )
    assert produced == set(DECLARED_KINDS.values())
    assert produced == {name for name, _ in EMITTED_REPORT_KINDS}


def test_the_classifier_covers_the_kinds_the_reports_themselves_enumerate(
    every_emitted_file: tuple[Path, ...],
) -> None:
    """The two enumerations agree: `paths.py`'s filename forms and `report.py`'s prose.

    Both emitted reports state their own membership, so a reader holding one of
    the three learns what the set is without consulting another document. A kind
    named in the prose with no filename form — the state T074 recorded — would
    show up here as a set difference.
    """
    named = {name for name, _ in EMITTED_REPORT_KINDS}

    assert len(EMITTED_REPORT_KINDS) == len(DECLARED_KINDS)
    assert named == set(DECLARED_KINDS.values())
    assert all(description.strip() for _, description in EMITTED_REPORT_KINDS)
    assert every_emitted_file, "no file was emitted at all, so the equality ranges over nothing"


def test_the_refusal_kind_admits_more_instances_than_the_set_has_kinds(
    every_emitted_file: tuple[Path, ...],
) -> None:
    """FR-037's per-attempt retention, and why the equality is not over files.

    Two refusals were driven in this tier and each kept its own file, so the
    emitted set already holds more files than FR-040 names kinds. Asserted rather
    than argued, because "exactly three files" is the wording `spec.md` struck and
    a later edit could reintroduce it in a form that still passed the kind check.
    """
    refusals = [path for path in every_emitted_file if kind_of(path) == "refusal report"]

    assert len(refusals) >= 2
    assert len(every_emitted_file) > len(DECLARED_KINDS)
    assert len({path.name for path in every_emitted_file}) == len(every_emitted_file), (
        "two emitted files share a name, so one overwrote the other and the per-attempt "
        "history FR-037 exists to keep is short"
    )


# ---------------------------------------------------------------------------
# The field-level half (DV-041's second clause)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(DECLARED_FIELDS))
def test_every_field_each_emitted_report_renders_belongs_to_its_declared_schema(
    kind: str, every_emitted_file: tuple[Path, ...]
) -> None:
    """DV-041's second clause, applied to both kinds that declare a closed schema.

    An equality per section rather than a containment, exactly as
    `test_no_verdict.py` asserts it for the run report: a field outside the
    schema is where an undeclared verdict would appear, and a declared field that
    never renders is a section the schema describes and the document does not
    have.
    """
    schema = DECLARED_FIELDS[kind]
    emitted = [path for path in every_emitted_file if kind_of(path) == kind]

    assert emitted, f"no {kind} was emitted, so its schema is asserted over nothing"
    for path in emitted:
        rendered = report_fields(path)

        assert undeclared_fields(rendered, schema) == (), (
            f"{path.name} renders {undeclared_fields(rendered, schema)}, which its declared "
            f"schema does not name"
        )
        assert set(rendered) == set(schema)
        for title, declared in schema.items():
            assert set(rendered[title]) == set(declared), (
                f"{path.name}'s `## {title}` renders "
                f"{sorted(set(rendered[title]) ^ set(declared))} on one side only"
            )


def test_the_reproduction_report_renders_its_sections_in_the_declared_order(
    every_emitted_file: tuple[Path, ...],
) -> None:
    """Order, so a section cannot be smuggled in between two declared ones.

    The run report's counterpart is `test_no_verdict.py`'s; this is the same
    assertion for the kind that arrived with T098, and it is what turns the set
    comparison above into a claim about the document a reader receives.
    """
    report = next(path for path in every_emitted_file if kind_of(path) == "reproduction report")

    assert tuple(report_fields(report)) == REPRODUCTION_SECTION_TITLES


def test_the_shared_parser_finds_the_fields_a_bullet_only_reader_would_miss(
    every_emitted_file: tuple[Path, ...],
) -> None:
    """Why `report_fields` is imported rather than re-implemented here.

    Both reports render declared fields as table columns and as em-dashed labels,
    and a validator reading only `- **X**: ` lines reports five fields missing on
    the run report and eight on the reproduction report — on every run, forever.
    The three forms are asserted present so a parser narrowed later fails here
    rather than reporting a document as incomplete.
    """
    run_report = next(path for path in every_emitted_file if kind_of(path) == "run report")
    reproduction = next(
        path for path in every_emitted_file if kind_of(path) == "reproduction report"
    )

    assert "Vendor-level claim" in report_fields(run_report)["Per-Vendor Shrinkage"]
    assert "Predictive ESS" in report_fields(reproduction)["Per-Line Comparison"]
    for path in (run_report, reproduction):
        assert "Report kind" in report_fields(path)["Emitted Report Set"], (
            f"{path.name}'s emitted-set section renders `Report kind` with an em dash "
            f"rather than a colon, and a parser that missed it would report the field "
            f"absent from a document that carries it"
        )
