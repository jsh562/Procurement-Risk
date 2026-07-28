"""T079 / T080 — `WITHDRAWN` retired unused, and no stale `BLOCKED` survives.

AD-008 gave FR-034 three exits: satisfied once E002 published the fields, blocked
while it had not, or withdrawn if the resolution had been to change E009's
blocking key instead. E002 took the first, so `WITHDRAWN` was never printed.

Asserting it matters because a discharged gate can be re-asserted by a stale
string. An artifact still saying `BLOCKED` reads exactly like a gate that is
still closed, and nothing else in the suite would notice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from model.procurement import paths

WORKSPACE = paths.REPO_ROOT / "specs" / "00005-synthetic-procurement-history"

#: Lines that legitimately contain the word while *recording* the discharge
#: rather than asserting the state. Kept deliberately broad — the failure this
#: guards against is a forgotten status line, not a careful historical note.
_HISTORICAL = re.compile(
    r"discharged|retired|no artifact still reports|reversed|while the gate|"
    r"superseded|withdrawn|struck|corrected|historical|assert|inconsistency|"
    r"was excluded|never printed|third state|honest third",
    re.IGNORECASE,
)


def _artifacts() -> list[Path]:
    return sorted(p for p in WORKSPACE.glob("*.md"))


def test_the_workspace_has_artifacts_to_check() -> None:
    """A glob matching nothing would make every assertion below vacuous."""
    assert len(_artifacts()) >= 4


@pytest.mark.parametrize("state", ["BLOCKED", "WITHDRAWN"])
def test_no_artifact_asserts_the_state_as_current(state: str) -> None:
    offenders: list[str] = []
    for path in _artifacts():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if state in line and not _HISTORICAL.search(line):
                offenders.append(f"{path.name}:{number}  {line.strip()[:110]}")
    assert not offenders, f"{state} asserted as current:\n" + "\n".join(offenders)


def test_the_requirement_and_criterion_are_live() -> None:
    spec = (WORKSPACE / "spec.md").read_text(encoding="utf-8")
    fr034 = next(line for line in spec.splitlines() if line.startswith("- **FR-034**"))
    sc026 = next(line for line in spec.splitlines() if line.startswith("- **SC-026**"))
    assert "Unblocked" in fr034
    assert "No longer pending" in sc026


def test_the_denominators_count_definitions_within_their_sections() -> None:
    """The rule T078 enforces, asserted on the spec itself.

    A whole-file prefix count returns more than the real number, because the
    audit-history section restates amended IDs in the same bullet form. The
    overcount is asserted to still exist: if it silently disappeared, the rule
    would look satisfied for the wrong reason.
    """
    spec = (WORKSPACE / "spec.md").read_text(encoding="utf-8")

    def scoped(heading: str, prefix: str) -> list[int]:
        start = spec.index(heading)
        end = spec.find("\n## ", start + 1)
        return sorted(
            {int(m) for m in re.findall(rf"^- \*\*{prefix}-(\d{{3}})\*\*", spec[start:end], re.M)}
        )

    requirements = scoped("## Functional Requirements", "FR")
    criteria = scoped("## Success Criteria", "SC")

    assert requirements == list(range(1, len(requirements) + 1))
    assert criteria == list(range(1, len(criteria) + 1))
    assert len(requirements) == 37
    assert len(criteria) == 33

    whole_file = len(re.findall(r"^- \*\*FR-\d{3}\*\*", spec, re.M))
    assert whole_file > len(requirements)


def test_no_artifact_publishes_a_coverage_level_for_the_reassigned_convention() -> None:
    """T080: the +/-4pp coverage convention is reassigned to E014.

    Publishing a level here would be this epic making a claim it explicitly
    handed off — which is a quieter failure than dropping the convention, because
    the number would look like ordinary reporting.
    """
    for path in _artifacts():
        for line in path.read_text(encoding="utf-8").splitlines():
            lowered = line.lower()
            for phrase in ("coverage level", "reference proportion"):
                if phrase in lowered:
                    assert "e014" in lowered or "reassign" in lowered, (
                        f"{path.name} publishes a {phrase} without naming the reassignment: "
                        f"{line.strip()[:120]}"
                    )
