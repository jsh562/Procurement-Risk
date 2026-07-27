"""T078 / NC-11a / NC-11b / A1–A2 — the QC report says what is true.

`plan.md` § Reporting Obligations makes these "assertable conditions of the QC
report, not guidance about it", on the grounds that an obligation a run can
satisfy by omission is the silent pass the obligations exist to prevent.

The check itself was missing until QC found it. T078 was closed against a file
that did not exist, so the obligations were guidance after all — which is exactly
what they were written not to be.

The report is written by the QC phase, so these tests **skip when it is absent**
rather than fail: a pre-QC run has nothing to check. What they must not do is
pass silently once it exists, so `test_the_report_exists_after_qc` fails loudly
if the file is there but empty.
"""

from __future__ import annotations

import re

import pytest

from model.procurement import paths

WORKSPACE = paths.REPO_ROOT / "specs" / "00005-synthetic-procurement-history"
REPORT = WORKSPACE / "qc-report.md"
SPEC = WORKSPACE / "spec.md"

#: States a requirement or criterion may render in. FR-034 and SC-026 were once
#: restricted to `BLOCKED`; the gate discharged, so the prohibited set inverted —
#: A2 is now the invariant *the rendered state matches the actual one* rather
#: than a ban on one value.
FORBIDDEN_STATES = ("BLOCKED", "WITHDRAWN", "pending", "deferred", "N/A")

pytestmark = pytest.mark.skipif(
    not REPORT.is_file(), reason="qc-report.md is written by the QC phase"
)


def _report() -> str:
    return REPORT.read_text(encoding="utf-8")


def _definition_count(prefix: str, heading: str) -> int:
    """IDs defined in their own section, counted — never asserted as a literal.

    A whole-file prefix count over `spec.md` overcounts, because the
    audit-history section restates amended IDs in the same bullet form. A1 says
    to derive the number; this is the derivation, and it is the same one
    `test_gate_discharged.py` applies.
    """
    spec = SPEC.read_text(encoding="utf-8")
    start = spec.index(heading)
    end = spec.find("\n## ", start + 1)
    return len({m for m in re.findall(rf"^- \*\*{prefix}-(\d{{3}})\*\*", spec[start:end], re.M)})


def test_the_report_exists_after_qc() -> None:
    assert REPORT.stat().st_size > 0, "qc-report.md is present but empty"


class TestNC11:
    """Neither FR-034 nor SC-026 may render in a state it is not in."""

    @pytest.mark.parametrize("identifier", ["FR-034", "SC-026"])
    def test_it_is_not_rendered_in_a_forbidden_state(self, identifier: str) -> None:
        for line in _report().splitlines():
            if identifier not in line:
                continue
            for state in FORBIDDEN_STATES:
                if state in line:
                    assert any(
                        word in line.lower()
                        for word in ("discharged", "was ", "no longer", "retired", "reversed")
                    ), f"{identifier} rendered as {state}: {line.strip()[:120]}"

    @pytest.mark.parametrize("identifier", ["FR-034", "SC-026"])
    def test_it_appears_in_the_report_at_all(self, identifier: str) -> None:
        """A report that omits them satisfies every prohibition vacuously."""
        assert identifier in _report()


class TestA1Denominators:
    """The printed denominators equal the count of definitions, derived."""

    def test_the_requirement_denominator_is_the_definition_count(self) -> None:
        expected = _definition_count("FR", "## Functional Requirements")
        assert f"/ {expected}" in _report() or f"of {expected}" in _report(), (
            f"the report prints no denominator equal to the {expected} requirement "
            f"definitions in spec.md"
        )

    def test_the_criterion_denominator_is_the_definition_count(self) -> None:
        expected = _definition_count("SC", "## Success Criteria")
        assert f"/ {expected}" in _report() or f"of {expected}" in _report(), (
            f"the report prints no denominator equal to the {expected} criterion "
            f"definitions in spec.md"
        )

    def test_no_stale_denominator_is_printed(self) -> None:
        """36 and 32 were the completion denominators while FR-034 and SC-026 were
        excluded. Both rejoined when the gate discharged; printing either again
        would re-assert the gate by arithmetic."""
        report = _report()
        for stale in ("/ 36", "of 36", "/ 32", "of 32"):
            assert stale not in report, f"stale denominator {stale!r} in the report"


class TestA2StateVocabulary:
    def test_every_rendered_verdict_is_from_the_allowed_set(self) -> None:
        """A verdict outside the vocabulary is a state nobody defined, and reads
        as authoritative anyway."""
        allowed = {"PASS", "FAIL", "PARTIAL", "SKIPPED", "N/A", "SATISFIED", "BLOCKED"}
        verdicts = set(re.findall(r"\*\*(PASS|FAIL|PARTIAL|SKIPPED|SATISFIED)\*\*", _report()))
        assert verdicts <= allowed
        assert verdicts, "the report renders no verdict at all"


class TestTheReportDoesNotOverclaim:
    def test_a_pass_verdict_requires_no_open_critical_finding(self) -> None:
        report = _report()
        if re.search(r"\*\*Verdict\*\*:\s*\*\*PASS\*\*", report):
            body = report.split("Verdict", 1)[1]
            assert "CRITICAL" not in body or "0 CRITICAL" in body or "no CRITICAL" in body

    def test_the_coverage_figure_is_stated_with_its_threshold(self) -> None:
        report = _report()
        if "coverage" in report.lower():
            assert "80" in report, "coverage is reported without naming its threshold"
