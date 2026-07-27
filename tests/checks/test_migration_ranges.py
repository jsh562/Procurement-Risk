"""TR-018 / TR-051 (VR-028): the revision directory is partitioned by prefix.

{SAD:ADR-0013} splits one Alembic directory between two epics: `0001`-`0099` is
E003's, `0100`-`0199` is E004's. **Nothing maps a revision to an owning epic
except its prefix** — there is no manifest, no per-file marker, and no import to
inspect. So the check is a *partition* check over the whole directory rather
than a per-file epic lookup: it asserts the blocks tile the range without
overlap, that every revision falls inside one of them, and that no prefix is
duplicated.

Lives at the repository root rather than inside `/src/model` because it asserts
the boundary *between* two epics' claims. Neither owns the assertion: E004
cannot add a test to E003's suite, and E003 has no reason to assert E004's half.
That is the same narrow `/tests` exception `test_layout.py` and
`test_orchestration.py` sit under.

**A missing directory is reported and skipped, not passed** (TR-051). Before
E003's arrangement landed there was no directory to check, and a check that
silently passes on an absent input is indistinguishable from one that passed on
a correct one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "src" / "model" / "src" / "model" / "schema" / "versions"

#: The blocks {SAD:ADR-0013} assigns, as inclusive `(low, high, owner)` triples.
#: Held here because this file asserts the partition itself — a set of blocks
#: read from the thing being checked would make every property below vacuous.
BLOCKS: tuple[tuple[int, int, str], ...] = (
    (1, 99, "E003"),
    (100, 199, "E004"),
)

#: `NNNN_name.py`. The prefix is the revision id and the filename prefix at
#: once — E003's convention, and what makes a prefix-based partition possible.
REVISION_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.py$")

#: Read out of each file rather than trusted from its name, because the two can
#: disagree and the runner obeys the *contents*.
REVISION_LINE = re.compile(r'^revision:\s*str\s*=\s*"(\d+)"', re.MULTILINE)
DOWN_REVISION_LINE = re.compile(
    r'^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*(?:"(\d+)"|None)',
    re.MULTILINE,
)


def _revision_files() -> list[Path]:
    if not VERSIONS_DIR.is_dir():
        pytest.skip(f"revision directory not present at {VERSIONS_DIR}")
    return sorted(
        path
        for path in VERSIONS_DIR.glob("*.py")
        if path.name != "__init__.py" and REVISION_FILENAME.match(path.name)
    )


def _prefix(path: Path) -> int:
    match = REVISION_FILENAME.match(path.name)
    assert match is not None, f"unreachable: {path.name} passed the filter"
    return int(match.group(1))


def test_the_directory_is_present_and_not_empty() -> None:
    """TR-051's reporting rule, and the positive control for everything below.

    A partition check over zero files passes every property it asserts. This is
    what distinguishes "the blocks are respected" from "there was nothing to
    respect them".
    """
    files = _revision_files()
    assert files, f"no revision files found in {VERSIONS_DIR}"


def test_the_declared_blocks_partition_the_range_without_overlap() -> None:
    """The blocks themselves, before anything is checked against them.

    An overlapping or gapped block table would make the per-file assertion
    below meaningless while still passing it — a revision could sit in two
    epics' claims at once and satisfy "is inside some block".
    """
    ordered = sorted(BLOCKS)
    for (low, high, owner), (next_low, _, next_owner) in zip(ordered, ordered[1:], strict=False):
        assert low <= high, f"{owner}'s block is inverted: {low}-{high}"
        assert high < next_low, (
            f"{owner}'s block ({low}-{high}) overlaps {next_owner}'s (from {next_low})"
        )
        assert next_low == high + 1, (
            f"a gap sits between {owner}'s block (ends {high}) and {next_owner}'s "
            f"(starts {next_low}); a revision numbered in it would belong to no epic"
        )


def test_every_revision_falls_inside_a_declared_block() -> None:
    """TR-018. The assertion the whole file exists for."""
    unclaimed = [
        path.name
        for path in _revision_files()
        if not any(low <= _prefix(path) <= high for low, high, _ in BLOCKS)
    ]
    assert not unclaimed, (
        f"{unclaimed} carry prefixes outside every declared block. Nothing but the "
        f"prefix says which epic owns a revision, so a number outside the blocks "
        f"belongs to no one. Declared: "
        f"{[f'{low:04d}-{high:04d} {owner}' for low, high, owner in BLOCKS]}"
    )


def test_no_prefix_is_duplicated() -> None:
    """Two files claiming one number is a merge artefact between the two epics'
    branches, and it is exactly what the block split exists to prevent. Alembic
    would resolve it as two revisions with the same id — one silently
    unreachable."""
    seen: dict[int, list[str]] = {}
    for path in _revision_files():
        seen.setdefault(_prefix(path), []).append(path.name)
    duplicates = {prefix: names for prefix, names in seen.items() if len(names) > 1}
    assert not duplicates, f"duplicate revision prefixes: {duplicates}"


def test_the_filename_prefix_matches_the_revision_id_inside() -> None:
    """The runner obeys the file's contents; the partition check reads its name.

    If the two disagree, this check is asserting a property of the filenames
    while the chain that actually runs is something else — the check would pass
    on a directory it does not describe.
    """
    mismatched = []
    for path in _revision_files():
        match = REVISION_LINE.search(path.read_text(encoding="utf-8"))
        assert match is not None, f"{path.name} declares no `revision:` line"
        if int(match.group(1)) != _prefix(path):
            mismatched.append(f"{path.name} declares revision {match.group(1)!r}")
    assert not mismatched, f"filename prefix and declared revision id disagree: {mismatched}"


def test_the_revision_graph_resolves_to_a_single_head() -> None:
    """TR-051. Two heads means two chains, and `migrate` applies one of them.

    Derived from the files rather than by invoking Alembic: this check must run
    with no database configured, and `alembic heads` needs a connection. A head
    is a revision no other revision points back to.
    """
    revisions: dict[str, str | None] = {}
    for path in _revision_files():
        text = path.read_text(encoding="utf-8")
        revision = REVISION_LINE.search(text)
        down = DOWN_REVISION_LINE.search(text)
        assert revision is not None, f"{path.name} declares no `revision:` line"
        assert down is not None, f"{path.name} declares no `down_revision:` line"
        revisions[revision.group(1)] = down.group(1)

    pointed_at = {down for down in revisions.values() if down is not None}
    heads = sorted(set(revisions) - pointed_at)
    assert len(heads) == 1, (
        f"the revision graph has {len(heads)} heads: {heads}. Two epics authoring "
        f"into one directory produce this when both chain off the same parent; "
        f"the later branch must chain off the earlier one's head instead."
    )

    roots = sorted(rev for rev, down in revisions.items() if down is None)
    assert len(roots) == 1, f"the revision graph has {len(roots)} roots: {roots}"


def test_every_declared_down_revision_exists() -> None:
    """A dangling parent makes the chain unrunnable from empty — the property
    TR-050 asserts by apply-from-empty, caught here without a database."""
    revisions: dict[str, str | None] = {}
    for path in _revision_files():
        text = path.read_text(encoding="utf-8")
        revision = REVISION_LINE.search(text)
        down = DOWN_REVISION_LINE.search(text)
        assert revision is not None and down is not None
        revisions[revision.group(1)] = down.group(1)

    dangling = {
        revision: down
        for revision, down in revisions.items()
        if down is not None and down not in revisions
    }
    assert not dangling, f"revisions point at parents that do not exist: {dangling}"


def test_the_two_epics_blocks_are_both_populated() -> None:
    """Not a rule either epic states, and deliberately weak: it asserts only
    that the partition is doing work.

    If E004's block were empty, every assertion above would pass while
    describing a single-epic directory — and the split would be evidenced by
    nothing. Weak on purpose: it says a block is used, never how many revisions
    it should hold.
    """
    populated = {
        owner
        for low, high, owner in BLOCKS
        for path in _revision_files()
        if low <= _prefix(path) <= high
    }
    assert populated == {owner for _, _, owner in BLOCKS}, (
        f"only {sorted(populated)} have revisions; the partition is untested for the rest"
    )


@pytest.mark.parametrize("prefix", ["0000", "0200", "9999"])
def test_the_check_reports_a_revision_numbered_outside_the_blocks(prefix: str) -> None:
    """A check that cannot fail proves nothing.

    Three planted numbers, one below every block, one just past the last, and
    one far outside — the just-past case is the one an off-by-one in the block
    table would let through.
    """
    number = int(prefix)
    assert not any(low <= number <= high for low, high, _ in BLOCKS), (
        f"{prefix} was expected to fall outside every declared block, but the block table admits it"
    )
