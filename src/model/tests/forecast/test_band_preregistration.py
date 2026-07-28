"""T124 — FR-041: a band that judges a stored quantity is published beforehand.

"Where this epic bounds a stored quantity against an estimated band, the band
MUST be published **before any draw it judges is seen**." The requirement exists
because the held-out band was added carrying that obligation with no requirement
behind it, and the reason is the one FR-028 gives for coverage bands and for the
held-out fraction alike: **a bar fixed after the result is not a bar**.

**DV-040's held-out band is the instance.** `test_held_out_semantic.py` judges
the stored held-out draws' pooled median against a multiple of the training
split's Kaplan–Meier median. Every input to the band's width — the log-scale
spread E005 publishes, the committed split fraction, the delivered-line count —
is fixed before the fit runs, and the band itself is two literals in the source.
That is what "published before any draw is seen" means operationally rather than
as a promise, and it is what this file asserts.

**Asserted as a committed-constant check, and G-11 bounds what that is worth.**
The same gap SC-032 has: a literal in a file proves the value was fixed at some
commit, never that the commit preceded the result. The check closes the run-time
door — a band computed from the draws it judges fails here — and the ordering
half is a diff with an author and a date, read by a human. A planted computed
band is included below, because an AST predicate that accepts everything would
report the same green as one that accepts only literals.

**Scope.** `tasks.md`'s line for this task also names SC-036 and SC-037. Those
are US4's criteria — a run below the published minimum chain count refusing
before sampling, and migration `0300` refusing a populated `forecast_run` — and
they are carried by T089 and T091 in Phase 6, against machinery this phase does
not have. Nothing here asserts them, and this paragraph is why rather than an
omission a reader has to infer.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from forecast import test_held_out_semantic as held_out_semantic
from model.forecast import ablation, config, report

#: Every bound this epic judges a quantity against, as `(module, name)`. FR-041
#: ranges over the estimated bands; the tolerances and thresholds beside them are
#: published under the same discipline and are enumerated here so the claim is
#: "every bound in the epic" rather than "the one this task was about".
PRE_PUBLISHED_BOUNDS: tuple[tuple[ModuleType, str], ...] = (
    # DV-040's held-out band — FR-041's instance.
    (held_out_semantic, "BAND_LOW_MULTIPLE"),
    (held_out_semantic, "BAND_HIGH_MULTIPLE"),
    # The registered coverage band and the event count it was derived at. Both
    # belong to `specs/prd.md` and are restated, never asserted, by this epic.
    (report, "REGISTERED_COVERAGE_BAND"),
    (report, "REGISTERED_UNCENSORED_EVENT_ASSUMPTION"),
    # The vendor-claim rule's threshold: published as a rule before the realized
    # weights were seen, which is the move FR-028 prohibits for bands.
    (report, "SHRINKAGE_SUPPORT_THRESHOLD"),
    # AD-004's reproduction tolerance and the basis condition published beside it.
    (config, "REPRODUCTION_TOLERANCE_DAYS"),
    (config, "REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN"),
    # The mass the ablation floor's interval carries.
    (ablation, "FLOOR_INTERVAL_PROBABILITY"),
)

#: The band DV-040 judges by, named separately because FR-041's instance carries
#: two extra obligations the others do not: it must bracket agreement, and it
#: must be wide enough to separate two duration semantics rather than to grade a
#: forecast.
HELD_OUT_BAND = (held_out_semantic.BAND_LOW_MULTIPLE, held_out_semantic.BAND_HIGH_MULTIPLE)

#: A band computed from the thing it judges. Parsed rather than written, so the
#: planted failure is a real syntax tree and not a hand-made stand-in for one.
PLANTED_COMPUTED_BAND = "BAND_LOW_MULTIPLE = 0.9 * realized_ratio(stored_draws)\n"

#: The same name assigned the way it actually is, as the planting's control.
PLANTED_LITERAL_BAND = "BAND_LOW_MULTIPLE = 0.70\n"


def module_tree(module: ModuleType) -> ast.Module:
    """One module's syntax tree, parsed from its own file on disk.

    From `__file__` rather than from a path literal, so the enumeration above
    cannot drift from the modules it names and a moved file fails at import
    rather than by silently checking nothing.
    """
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def module_level_assignments(tree: ast.Module, name: str) -> list[ast.expr]:
    """Every value assigned to `name` at module level, in source order."""
    return [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]


def is_literal(value: ast.expr) -> bool:
    """Whether an assigned value is fixed in the source rather than computed.

    Constants, and tuples or lists of them — a band is a pair — plus a unary
    minus, so a negative bound is a literal and not an expression. Everything
    else is a value that came from somewhere at import time, and the whole
    question FR-041 asks is whether it could have come from the result.
    """
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        return is_literal(value.operand)
    if isinstance(value, ast.Tuple | ast.List):
        return all(is_literal(element) for element in value.elts)
    return False


def is_pre_published(module: ModuleType, name: str) -> bool:
    """FR-041's predicate: assigned exactly once, at module level, from a literal.

    Once, because two assignments mean a value that is re-bound and a reader
    with no way to tell which one a run used. At module level and from a
    literal, because that is what makes the bound a diff with an author and a
    date rather than an artifact of whatever was in scope when it was evaluated.
    """
    assigned = module_level_assignments(module_tree(module), name)
    return len(assigned) == 1 and is_literal(assigned[0])


# ---------------------------------------------------------------------------
# The predicate itself, planted both ways
# ---------------------------------------------------------------------------


def test_a_band_computed_from_what_it_judges_fails_the_predicate() -> None:
    """The planted positive: without it this file's green means nothing.

    A band derived from the realized ratio is the failure FR-041 names — the bar
    moved to where the result already is — and it is the one form of the mistake
    that reads as perfectly reasonable code.
    """
    computed = module_level_assignments(ast.parse(PLANTED_COMPUTED_BAND), "BAND_LOW_MULTIPLE")
    literal = module_level_assignments(ast.parse(PLANTED_LITERAL_BAND), "BAND_LOW_MULTIPLE")

    assert len(computed) == len(literal) == 1
    assert not is_literal(computed[0])
    assert is_literal(literal[0])


def test_a_bound_assigned_twice_fails_even_when_both_are_literals() -> None:
    """The second half of the predicate: a re-bound constant is not a published one.

    Two literals are two bars, and the run used whichever came last. The failure
    is quiet — every value involved is a number in the source — which is why the
    count is part of the predicate rather than an afterthought.
    """
    rebound = ast.parse("BAND_LOW_MULTIPLE = 0.70\nBAND_LOW_MULTIPLE = 0.95\n")

    assert len(module_level_assignments(rebound, "BAND_LOW_MULTIPLE")) == 2


# ---------------------------------------------------------------------------
# Every bound the epic publishes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "name"),
    PRE_PUBLISHED_BOUNDS,
    ids=[f"{module.__name__.rsplit('.', 1)[-1]}.{name}" for module, name in PRE_PUBLISHED_BOUNDS],
)
def test_every_published_bound_is_a_committed_literal(module: ModuleType, name: str) -> None:
    """FR-041 over the whole enumeration, one case per bound.

    Parametrized rather than looped so a failure names the bound that moved.
    Each of these judges something a run produces, and none of them may be a
    function of it.
    """
    assert is_pre_published(module, name), (
        f"`{name}` in {module.__name__} is not a single module-level literal, so it is a "
        f"bound whose value could have been computed from the run it judges (FR-041)"
    )


# ---------------------------------------------------------------------------
# DV-040's band, which is FR-041's instance
# ---------------------------------------------------------------------------


def test_the_held_out_band_brackets_agreement_rather_than_sitting_beside_it() -> None:
    """A band that excludes 1.0 judges disagreement as the passing case.

    The stored held-out median and the training split's Kaplan–Meier median
    estimate the same quantity by different routes on different rows, so exact
    agreement is the centre of the admissible region and not its edge.
    """
    low, high = HELD_OUT_BAND

    assert 0.0 < low < 1.0 < high
    assert high - low > 0.5, (
        f"the band [{low}, {high}] is narrower than the separation it exists to make; it "
        f"distinguishes a total duration from a remaining one, which differ by a factor near "
        f"two on this input, and a tight band would be a calibration statement FR-026 "
        f"reserves for the evaluation harness"
    )


def test_the_bands_derivation_travels_with_the_band() -> None:
    """The inputs to the width are named where the width is, or it is a guess.

    Every quantity the band was derived from — the published log-scale spread,
    the committed split fraction, the delivered-line count — is fixed before the
    fit runs, and a reader can only check that if the derivation is recorded
    beside the numbers rather than in a review comment.
    """
    documentation = inspect.getdoc(held_out_semantic) or ""
    source = Path(held_out_semantic.__file__).read_text(encoding="utf-8")

    assert "before any draw is seen" in documentation
    assert "standard error" in source
    assert "L-3" in source
