"""Whatever ruff lints, ruff also format-checks — over the same set of tiers.

**The instance.** `verify.yml`'s "Format check (Python)" step looped the three
entries under `/src` and stopped there, while "Lint (Python)" looped the same
three *and* ran once at the checkout root. So root linting was enforced and root
formatting was not: `ruff format --check .` at the root covers 403 files against
the entries' 243, and everything under `/tests` sits in the difference — the 22
modules of `tests/checks`, including every check in this directory, and the 41
import-linter negative fixtures under `tests/fixtures`. An unformatted check
module passed CI.

**The class.** This is the same root cause as the `--basetemp` finding one
iteration earlier, and as the `TMPDIR` asymmetry one before that: *the root is
not an entry, so it falls out of a `for entry in gateway api model` loop.* Three
findings, one shape. A fix that added `uv run ruff format --check .` to the step
would close the instance and leave the shape, and the next tool wired in as a
per-entry loop would reintroduce it — which is what happened twice already.

So the property asserted here is a **relation between two steps** rather than a
fact about either: the set of tiers ruff lints must equal the set it
format-checks. Adding a fourth tier to one step and not the other fails this,
whatever the tool and whatever the tier, and the failure names both sets. It
does not assert *which* tiers those are beyond a floor — pinning the list here
would be a fourth place to remember the root, which is the thing that keeps
being forgotten.

Sits in `tests/checks` because it reads the workflow, which no entry owns — and,
fittingly, because `tests/checks` is exactly the directory the omission left
unchecked.
"""

from __future__ import annotations

import yaml

from tests.checks.helpers.entries import PYTHON_ENTRIES
from tests.checks.helpers.workflow import ROOT_TIER, load_workflow, tiers_running

#: The two ruff invocations, as the workflow spells them. `ruff format` rather
#: than `ruff format --check`: the relation is about which tiers the formatter
#: is pointed at, and a step that ran a *rewriting* `ruff format` in CI would
#: satisfy this check and still be wrong — which `test_the_format_step_checks`
#: below is what catches.
LINT = "ruff check"
FORMAT = "ruff format"


def test_ruff_lints_and_format_checks_the_same_tiers() -> None:
    document = load_workflow()
    linted = tiers_running(document, LINT)
    formatted = tiers_running(document, FORMAT)

    assert linted == formatted, (
        f"ruff lints {sorted(linted)} and format-checks {sorted(formatted)}. Every tier "
        f"one covers the other must cover: linting enforced where formatting is not "
        f"leaves a tier half-checked, and the tier that keeps falling out is the "
        f"checkout root, which is not one of the {len(PYTHON_ENTRIES)} entries a "
        f"`for entry in …` loop iterates. Unlinted: {sorted(formatted - linted)}; "
        f"unformatted: {sorted(linted - formatted)}"
    )


def test_the_covered_set_includes_the_root_and_every_entry() -> None:
    """The floor, so the relation above cannot hold vacuously.

    Two empty sets are equal, and they are what this file would compare if the
    workflow stopped running ruff at all or if the parser stopped recognising
    the steps. The floor is stated as "at least", not as the exact set: which
    tiers exist is the workflow's business, and duplicating the list here would
    be one more place to forget the root.
    """
    linted = tiers_running(load_workflow(), LINT)
    expected = {ROOT_TIER} | {f"src/{entry}" for entry in PYTHON_ENTRIES}
    assert expected <= linted, f"ruff lints {sorted(linted)}, missing {sorted(expected - linted)}"


def test_the_format_step_checks_rather_than_rewrites() -> None:
    """A `ruff format` with no `--check` in CI rewrites files on the runner and
    exits 0 — a formatting gate that passes on unformatted source. The relation
    above cannot see the difference, because both spellings name the same
    tiers."""
    document = load_workflow()
    formatting = [
        line
        for step in (document["jobs"]["verify"]["steps"])
        for line in str(step.get("run", "")).splitlines()
        if FORMAT in line
    ]
    assert formatting, "no step runs `ruff format` at all"
    rewriting = [line.strip() for line in formatting if "--check" not in line]
    assert not rewriting, f"{rewriting} rewrite rather than check, and exit 0 either way"


def test_the_relation_reports_a_step_that_omits_the_root() -> None:
    """The negative control, planted as the exact shape of the finding: a lint
    step covering the three entries and the root, and a format step covering
    only the three. A check that read the two steps and compared nothing would
    pass here."""
    planted = yaml.safe_load(
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - name: Lint (Python)\n"
        "        run: |\n"
        "          for entry in gateway api model; do "
        'uv run --directory "src/$entry" ruff check .; done\n'
        "          uv run ruff check .\n"
        "      - name: Format check (Python)\n"
        "        run: |\n"
        "          for entry in gateway api model; do\n"
        '            uv run --directory "src/$entry" ruff format --check .\n'
        "          done\n"
    )
    linted = tiers_running(planted, LINT)
    formatted = tiers_running(planted, FORMAT)

    assert linted == {ROOT_TIER, "src/gateway", "src/api", "src/model"}
    assert formatted == {"src/gateway", "src/api", "src/model"}
    assert linted - formatted == {ROOT_TIER}


def test_the_loop_expansion_does_not_leak_past_its_done() -> None:
    """`for entry in …; done` binds `$entry` inside the loop only. A parser that
    kept the binding would resolve a later `--directory "src/$entry"` in an
    unrelated step against a list it has nothing to do with, and would report
    tiers no step runs."""
    planted = yaml.safe_load(
        "jobs:\n"
        "  verify:\n"
        "    steps:\n"
        "      - run: |\n"
        "          for entry in gateway api; do\n"
        '            uv run --directory "src/$entry" ruff check .\n'
        "          done\n"
        '          uv run --directory "src/$entry" ruff check .\n'
    )
    tiers = tiers_running(planted, LINT)
    assert tiers == {"src/gateway", "src/api", "src/$entry"}, tiers
