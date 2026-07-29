"""TR-018 / TR-051 (VR-028): the revision directory is partitioned by prefix.

{SAD:ADR-0013} splits one Alembic directory across the epics that author into
it, each claiming a block at its start: `0001`-`0099` is E003's, `0100`-`0199`
is E004's, `0200`-`0299` is E005's, `0300`-`0399` is E007's, `0400`-`0499` is
E006's. **Nothing maps a revision to an owning epic except its prefix** — there
is no manifest, no per-file marker, and no import to inspect. So the check is a
*partition* check over the whole directory rather than a per-file epic lookup:
it asserts the blocks tile the range without overlap, that every revision falls
inside one of them, and that no prefix is duplicated.

**A claimed block need not be used.** E005 creates no database object at all —
its data model says so in its first line — so its block is declared here and
deliberately holds no revision. That is why the block table below is split in
two: the *declared* blocks are what the partition and membership checks range
over, and only the blocks whose owner actually authored a revision are expected
to be populated. Folding the two back together would force a choice between
leaving a gap at `0200`-`0299` — which the partition check refuses — and
asserting that an epic which writes no DDL nevertheless shipped a migration.

Lives at the repository root rather than inside `/src/model` because it asserts
the boundary *between* the epics' claims. No one of them owns the assertion:
E004 cannot add a test to E003's suite, and E003 has no reason to assert E004's
half. That is the same narrow `/tests` exception `test_layout.py` and
`test_orchestration.py` sit under.

**A declared block need not hold revisions** (E006 AD-013). A block is a
*claim on a number range*, and the claim is what prevents a collision — so it
has to be declarable before the first revision inside it is authored, and it
has to survive an epic that claims a block and never uses it. E005 is exactly
that case: it claimed `0200`-`0299` at epic start expecting it to go unused,
and stated the claim stands regardless so a later need cannot collide. The
distinction the assertions below draw is therefore between *reserved-and-empty*
(permitted, and the point of a reservation) and *populated-but-undeclared*
(forbidden, and what the whole file exists to catch).

**A missing directory is reported and skipped, not passed** (TR-051). Before
E003's arrangement landed there was no directory to check, and a check that
silently passes on an absent input is indistinguishable from one that passed on
a correct one.

**The last section of this file checks the block table rather than the
directory** (NC-12). Extending a block table is a change to a *check*, so
nothing in the suite turns red when it is done and nothing turns red when it is
done wrongly either — every other assertion here reads the table, so a mistake
in it makes the assertions agree with the mistake. Those tests assert the
accepting direction for `0300`-`0399`, and re-derive the finding that produced
the six-part remediation instead of a one-line one.
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
#:
#: **Extended 2026-07-27 (E007 AD-007).** Adding `0300`-`0399` alone would fail
#: the partition check, which requires `next_low == high + 1` and so refuses the
#: gap at `0200`-`0299`; E005's claimed-but-unused block closes it.
#:
#: **Extended again 2026-07-28 (E006 AD-013, FR-040).** E006 and E007 both
#: claimed `0300`-`0399`, against the same baseline and by the same
#: scan-for-the-highest rule, and both were right when they claimed it. E007
#: landed on `main` first, so E006 renumbered its five revisions into
#: `0400`-`0499` and this table records the outcome. The collision was invisible
#: to git — the two sets of `03xx` files carry different names, so the merge
#: raised no conflict — and would have surfaced only as duplicate revision
#: identifiers and two heads, which is what
#: `test_no_prefix_is_duplicated` and
#: `test_the_revision_graph_resolves_to_a_single_head` below exist to catch.
DECLARED_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (1, 99, "E003"),
    (100, 199, "E004"),
    (200, 299, "E005"),
    (300, 399, "E007"),
    (400, 499, "E006"),
)

#: The owners whose block is expected to hold at least one revision — the
#: *populated-expected* half of the table above.
#:
#: Separate from `DECLARED_BLOCKS` because claiming a block and authoring into
#: it are different acts. E005 claims `0200`-`0299` and creates no database
#: object, so requiring every declared block to be populated would fail on the
#: arrangement working as designed, while dropping E005's claim would open a gap
#: the partition check refuses. Membership here is a statement about an epic's
#: scope and is edited when an epic's scope changes — never to make a red run
#: green.
OWNERS_EXPECTED_TO_HAVE_REVISIONS: frozenset[str] = frozenset({"E003", "E004", "E006", "E007"})

#: The width of one block, used to name the block a revision prefix implies.
#: Every block above is a hundreds bucket, and the assertion that every
#: *populated* bucket is declared depends on that being true rather than
#: assumed — `test_every_block_is_one_hundreds_bucket` holds it.
BLOCK_WIDTH = 100

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


Blocks = tuple[tuple[int, int, str], ...]


def _partition_defects(blocks: Blocks) -> list[str]:
    """Every way `blocks` fails to tile a range: inversions, overlaps, gaps.

    Factored out of the test that asserts it is empty so the **same predicate**
    can be pointed at a hypothetical block table. The NC-12 counterfactual at
    the foot of this file depends on that: a partition rule re-implemented
    inside the test that describes it would be evidence about the
    re-implementation and not about this check.

    Returns descriptions rather than raising, because a caller asserting the
    list is *non*-empty needs to see which property broke.
    """
    ordered = sorted(blocks)
    defects = [
        f"{owner}'s block is inverted: {low}-{high}" for low, high, owner in ordered if low > high
    ]
    for (low, high, owner), (next_low, _, next_owner) in zip(ordered, ordered[1:], strict=False):
        if high >= next_low:
            defects.append(
                f"{owner}'s block ({low}-{high}) overlaps {next_owner}'s (from {next_low})"
            )
        elif next_low != high + 1:
            defects.append(
                f"a gap sits between {owner}'s block (ends {high}) and {next_owner}'s "
                f"(starts {next_low}); a revision numbered in it would belong to no epic"
            )
    return defects


def _owners_claiming(number: int, blocks: Blocks) -> list[str]:
    """Which owners' blocks admit `number`. Empty means it belongs to no epic."""
    return [owner for low, high, owner in blocks if low <= number <= high]


def _populated_owners(blocks: Blocks) -> set[str]:
    """Owners whose block holds at least one revision file on disk."""
    return {
        owner
        for low, high, owner in blocks
        for path in _revision_files()
        if low <= _prefix(path) <= high
    }


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
    defects = _partition_defects(DECLARED_BLOCKS)

    assert not defects, f"the declared block table does not tile its range: {defects}"


def test_every_revision_falls_inside_a_declared_block() -> None:
    """TR-018. The assertion the whole file exists for."""
    unclaimed = [
        path.name
        for path in _revision_files()
        if not any(low <= _prefix(path) <= high for low, high, _ in DECLARED_BLOCKS)
    ]
    assert not unclaimed, (
        f"{unclaimed} carry prefixes outside every declared block. Nothing but the "
        f"prefix says which epic owns a revision, so a number outside the blocks "
        f"belongs to no one. Declared: "
        f"{[f'{low:04d}-{high:04d} {owner}' for low, high, owner in DECLARED_BLOCKS]}"
    )


def test_no_prefix_is_duplicated() -> None:
    """Two files claiming one number is a merge artefact between two authoring
    epics' branches, and it is exactly what the block split exists to prevent. Alembic
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


def test_every_block_is_one_hundreds_bucket() -> None:
    """The premise `test_every_populated_block_is_declared` rests on.

    That check names the block a revision implies by dividing its prefix by
    `BLOCK_WIDTH`. If a block were ever declared across a bucket edge — `0350`
    to `0449`, say — the division would name two buckets for one block and the
    check would report a false violation, or worse, silently agree with half of
    one. Asserted rather than assumed, because the block table is the one thing
    in this file nothing else validates.

    E003's block runs `1`-`99` rather than `0`-`99`, and that is not a
    misalignment: `0000` is not a revision number, it is the below-every-block
    negative control at the foot of this file. What matters is that low and
    high land in the same bucket, and that no two blocks share one.
    """
    straddling = [
        f"{low:04d}-{high:04d} {owner}"
        for low, high, owner in DECLARED_BLOCKS
        if low // BLOCK_WIDTH != high // BLOCK_WIDTH
    ]
    assert not straddling, (
        f"{straddling} span more than one hundreds bucket; BLOCK_WIDTH no longer "
        f"describes the table"
    )

    buckets: dict[int, list[str]] = {}
    for low, _, owner in DECLARED_BLOCKS:
        buckets.setdefault(low // BLOCK_WIDTH, []).append(owner)
    shared = {bucket: owners for bucket, owners in buckets.items() if len(owners) > 1}
    assert not shared, (
        f"one hundreds bucket is split between epics: {shared}; a revision inside it "
        f"would imply two owners at once"
    )


def test_every_populated_block_is_declared() -> None:
    """Half of what the old both-populated assertion carried, kept and sharpened.

    Direction matters, and only this direction is a defect: a hundreds bucket
    holding revisions that no block claims means those revisions belong to no
    epic. The converse — a declared block holding nothing — is a *reservation*,
    which is why it is asserted nowhere.

    Distinct from `test_every_revision_falls_inside_a_declared_block` above,
    which reads one file at a time. This one groups first, so the failure names
    the unclaimed *block* and every revision inside it rather than a list of
    filenames a reader has to bucket by hand.
    """
    declared = {low // BLOCK_WIDTH for low, _, _ in DECLARED_BLOCKS}
    by_bucket: dict[int, list[str]] = {}
    for path in _revision_files():
        by_bucket.setdefault(_prefix(path) // BLOCK_WIDTH, []).append(path.name)

    undeclared = {
        f"{bucket * BLOCK_WIDTH:04d}-{bucket * BLOCK_WIDTH + BLOCK_WIDTH - 1:04d}": names
        for bucket, names in by_bucket.items()
        if bucket not in declared
    }
    assert not undeclared, (
        f"revisions sit in hundreds buckets no block claims: {undeclared}. "
        f"Nothing but the prefix says which epic owns a revision, so a bucket "
        f"outside the block table belongs to no one. Declared: "
        f"{[f'{low:04d}-{high:04d} {owner}' for low, high, owner in DECLARED_BLOCKS]}"
    )


def test_at_least_two_blocks_are_populated() -> None:
    """The other half, and the reason the old assertion existed at all.

    Its job was never to police any particular epic's block — it was to stop
    every property above from passing over a directory that only one epic had
    ever written into, where a partition check describes nothing. That job
    needs *some* block populated beyond the first, not *all* of them, and the
    difference is what lets `0200`-`0299` and `0400`-`0499` be claimed before a
    revision is authored inside either.

    Two, not five, and deliberately weak: it says the split is load-bearing,
    never how many revisions a block should hold.

    **Kept alongside E007's stronger equality below rather than folded into
    it**, because the two fail on different things. This one is a floor that
    survives any change to `OWNERS_EXPECTED_TO_HAVE_REVISIONS`; that one is an
    equality against a hand-maintained set, and an equality can be made to pass
    by editing the set. A floor cannot.
    """
    populated = _populated_owners(DECLARED_BLOCKS)
    assert len(populated) >= 2, (
        f"only {sorted(populated)} hold revisions; with fewer than two populated blocks "
        f"every assertion above passes over a single-epic directory and the partition "
        f"is evidenced by nothing. Declared but empty: "
        f"{sorted({owner for _, _, owner in DECLARED_BLOCKS} - populated)}"
    )


def test_every_block_whose_owner_authored_revisions_is_populated() -> None:
    """Not a rule any epic states, and deliberately weak: it asserts only that
    the partition is doing work.

    If E004's block were empty, every assertion above would pass while
    describing a single-epic directory — and the split would be evidenced by
    nothing. Weak on purpose: it says a block is used, never how many revisions
    it should hold.

    **Scoped to `OWNERS_EXPECTED_TO_HAVE_REVISIONS`, not to every declared
    block.** E005 claims `0200`-`0299` and authors nothing into it, so the
    unscoped form would report a correct arrangement as a defect. The assertion
    is an *equality* rather than a containment for the other direction: a
    revision appearing in a block recorded as unused is also a failure, because
    the record and the directory would then disagree about who is authoring.
    """
    declared_owners = {owner for _, _, owner in DECLARED_BLOCKS}
    assert declared_owners >= OWNERS_EXPECTED_TO_HAVE_REVISIONS, (
        f"{sorted(OWNERS_EXPECTED_TO_HAVE_REVISIONS - declared_owners)} are expected to "
        f"have revisions but hold no declared block, so nothing below could find them"
    )

    populated = _populated_owners(DECLARED_BLOCKS)
    assert populated == OWNERS_EXPECTED_TO_HAVE_REVISIONS, (
        f"the populated blocks are {sorted(populated)} but "
        f"{sorted(OWNERS_EXPECTED_TO_HAVE_REVISIONS)} were expected. A missing owner means "
        f"the partition is untested for its block; an extra one means a block recorded as "
        f"claimed-but-unused has acquired revisions"
    )


@pytest.mark.parametrize("prefix", ["0000", "0500", "9999"])
def test_the_check_reports_a_revision_numbered_outside_the_blocks(prefix: str) -> None:
    """A check that cannot fail proves nothing.

    Three planted numbers, one below every block, one just past the last, and
    one far outside — the just-past case is the one an off-by-one in the block
    table would let through.

    **The just-past probe has moved twice.** It was `0200` while E004's
    `0100`-`0199` was the last declared block; it moved to `0400` when E007
    claimed `0300`-`0399` and E005's `0200`-`0299` was declared to close the
    gap; and it moved to `0500` on 2026-07-28 when E006 renumbered into
    `0400`-`0499` and made `0400` a real revision. It has to track the last
    declared block: left behind, it asserts that a now-declared number is
    undeclared, which is the same off-by-one it exists to catch, pointed at the
    test instead of at the table. A control aimed inside a claimed and
    populated block is not a control.
    """
    number = int(prefix)
    claimants = _owners_claiming(number, DECLARED_BLOCKS)

    assert not claimants, (
        f"{prefix} was expected to fall outside every declared block, but {claimants} claim it"
    )


# --------------------------------------------------------------------------- #
# NC-12 — AD-007's remediation, verified in both directions
# --------------------------------------------------------------------------- #
#
# The four tests below are the failing direction for the change this file
# received on 2026-07-27. A remediation is a change to a *check*, so nothing in
# the suite goes red when it is done and nothing goes red when it is done
# wrongly either — the block table is the thing every other assertion here reads,
# so a mistake in it makes the assertions agree with the mistake. That is the
# case NC-12 exists for.
#
# DV-033 and DV-034 need no test of their own and get none:
# `test_the_revision_graph_resolves_to_a_single_head` above and
# `test_chain_resolves_to_exactly_one_head`, `test_chain_is_linear` and
# `test_chain_applies_to_an_empty_database` in
# `src/model/tests/schema/test_migration_chain.py` are the covering assertions,
# and AD-007's constraint was that the remediation leave them untouched. Adding
# a parallel copy here would make "the graph checks are unaltered" harder to
# read, not easier.

#: E007's block, as the numbers a revision in it can carry. Written as its own
#: constant rather than reached through `DECLARED_BLOCKS`, because the
#: acceptance test below must fail if the table's E007 entry is wrong — reading
#: the bounds out of the table under test would assert that a range equals
#: itself.
E007_BLOCK_FIRST = 300
E007_BLOCK_LAST = 399
E007_OWNER = "E007"

#: The block table as it stood before AD-007, plus `0300`-`0399` and nothing
#: else — "part (a)" read as narrowly as the gap analysis reads it.
BLOCKS_WITH_E007_BUT_NO_E005: Blocks = (
    (1, 99, "E003"),
    (100, 199, "E004"),
    (300, 399, "E007"),
)

#: The pre-remediation rule that every *declared* block hold a revision, before
#: part (b) split declared from populated-expected.
OWNERS_BEFORE_THE_POPULATION_SPLIT: frozenset[str] = frozenset({"E003", "E004"})

#: The probe part (c) moved. `0200` was the "just past the last block" case
#: while E004's `0100`-`0199` was last; it stops being outside every block the
#: moment E005's claim is declared.
PROBE_BEFORE_PART_C = 200


@pytest.mark.parametrize("number", [E007_BLOCK_FIRST, 342, E007_BLOCK_LAST])
def test_the_block_table_admits_every_number_in_e007s_reserved_range(number: int) -> None:
    """NC-12, the accepting direction: `0300`-`0399` belongs to E007 and to E007 alone.

    The three probes are the two endpoints and one interior number. Endpoints
    because an off-by-one in the table is the mistake that survives review — a
    block written `301`-`399` admits every revision E007 has authored so far and
    refuses the first one it has not.

    Asserted as *exactly one* claimant with the right name, not merely "inside
    some block". A revision that fell inside two blocks would satisfy
    `test_every_revision_falls_inside_a_declared_block` while belonging to two
    epics, and one that fell inside E005's would be attributed to an epic that
    authors no DDL.
    """
    claimants = _owners_claiming(number, DECLARED_BLOCKS)

    assert claimants == [E007_OWNER], (
        f"{number:04d} is claimed by {claimants}, not by {E007_OWNER} alone. E007 reserved "
        f"{E007_BLOCK_FIRST:04d}-{E007_BLOCK_LAST:04d} at epic start and authored "
        f"`0300`-`0303` into it; a number in that range attributed to nobody, or to two "
        f"owners, means the block table does not say what the claim says."
    )


def test_every_revision_e007_authored_is_attributed_to_e007() -> None:
    """NC-12: the revisions actually on disk, not a range in the abstract.

    The test above reasons about numbers; this one reasons about files. They
    fail on different mistakes: the table could admit `0300`-`0399` correctly
    while E007's revisions were named `03000` or `0300-forecast.py` and matched
    no revision filename at all, in which case every partition assertion in this
    module would pass over a directory that silently excluded four files.

    Also the positive control for the population assertion: E007 appearing in
    `OWNERS_EXPECTED_TO_HAVE_REVISIONS` is a claim that its block holds
    something, and this is where that is observed rather than declared.
    """
    e007_files = sorted(
        path.name
        for path in _revision_files()
        if E007_BLOCK_FIRST <= _prefix(path) <= E007_BLOCK_LAST
    )

    assert e007_files, (
        f"no revision file carries a prefix in {E007_BLOCK_FIRST:04d}-"
        f"{E007_BLOCK_LAST:04d}. Either E007's migrations are not on disk, or they are "
        f"named in a form `REVISION_FILENAME` does not match — in which case every "
        f"assertion in this module is passing over a directory it cannot see them in."
    )
    assert E007_OWNER in _populated_owners(DECLARED_BLOCKS), (
        f"{e007_files} exist but {E007_OWNER} is not among the populated owners "
        f"{sorted(_populated_owners(DECLARED_BLOCKS))}. The block table and the directory "
        f"disagree about who is authoring."
    )


def test_declaring_e007s_block_without_the_rest_of_the_remediation_stays_red() -> None:
    """NC-12: AD-007's "(a) alone turns one red assertion into two", demonstrated.

    Before the remediation exactly one assertion was red —
    `test_every_revision_falls_inside_a_declared_block`, because `0300`-`0303`
    sat in no declared block. The plan's finding is that the obvious minimal fix
    does not clear it, and that finding is the reason four parts landed instead
    of one. It is checked here rather than trusted, because it is a claim about
    what *would* happen and those decay silently: someone loosening the partition
    rule, or dropping the population assertion, would make this file's history
    read as an over-reaction.

    Three counterfactuals, one per way "(a) alone" can be read, each evaluated
    with the **same helper the live assertions use** so the demonstration cannot
    drift from the check it describes:

    1. Declaring only `0300`-`0399` leaves a gap at `0200`-`0299`, which the
       partition rule refuses. So (a) narrowly read does not even compile.
    2. Closing that gap with E005's claim then fails the *pre-part-(b)* rule that
       every declared block be populated, because E005 authors no DDL.
    3. And it makes the *pre-part-(c)* probe at `0200` — whose entire purpose was
       that `0200` sits outside every block — assert something that is no longer
       true.
    """
    gap_defects = _partition_defects(BLOCKS_WITH_E007_BUT_NO_E005)

    assert gap_defects, (
        "declaring E007's block without E005's leaves 0200-0299 claimed by nobody, and "
        "the partition rule is supposed to refuse that. It did not, so either the rule "
        "has been loosened or the gap has been closed some other way — and part (a) of "
        "AD-007 is no longer the incomplete fix this file's history says it was."
    )

    declared_owners = {owner for _, _, owner in DECLARED_BLOCKS}
    populated = _populated_owners(DECLARED_BLOCKS)
    unpopulated_before_part_b = declared_owners - populated

    assert unpopulated_before_part_b == {"E005"}, (
        f"the pre-part-(b) rule was `populated == every declared owner`. Against the "
        f"current table the owners it would report as missing are "
        f"{sorted(unpopulated_before_part_b)}, and it should be exactly E005 — the epic "
        f"that claims 0200-0299 and authors no DDL. An empty set means the rule would "
        f"have passed and part (b) was unnecessary; a larger one means a second block is "
        f"declared and unused, which is a decision belonging in a data model."
    )
    assert populated > OWNERS_BEFORE_THE_POPULATION_SPLIT, (
        f"the populated owners are {sorted(populated)}, which does not strictly extend the "
        f"pre-remediation set {sorted(OWNERS_BEFORE_THE_POPULATION_SPLIT)}. The whole "
        f"reason the population rule had to be rewritten rather than left alone is that a "
        f"third owner started authoring; if that is no longer true, this file is carrying "
        f"a split it does not need."
    )

    assert _owners_claiming(PROBE_BEFORE_PART_C, DECLARED_BLOCKS), (
        f"{PROBE_BEFORE_PART_C:04d} is still outside every declared block, so part (c) "
        f"-- moving the just-past-the-end probe past the last declared block -- fixed "
        f"nothing and the probe could have stayed where it was. Check that E005's block "
        f"is still declared."
    )
