"""T061 — DV-027: the two stores' array invariants, paired definition by definition.

ADR-0018 § Consequences/Negative states the obligation and supplies no mechanism:
"the array invariants are now asserted in two places. Every future strengthening
— the kind E003 already had to apply twice, where a check evaluated to NULL on
the input it existed to refuse — must be applied to both tables or they diverge."
This file is that mechanism.

**Why pairing definitions rather than re-asserting behaviour.**
`test_stored_arrays.py` already runs every array property over the rows of both
stores, and it would go on passing after one store's check was dropped, because
the writer produces conforming rows either way. What a dropped check changes is
what the *database* refuses, and the only place that is visible is
`pg_constraint`. So this reads both tables' constraint definitions and requires
them equal — not merely both present, since a check weakened from `> 0` to
`>= 0` is present, named the same, and no longer the same rule.

The comparison is exact string equality of `pg_get_constraintdef`, which is
PostgreSQL's own normalisation of the expression rather than the DDL text. Two
checks written differently but meaning the same thing therefore compare equal,
and two written identically against different columns do not. Neither table's
name appears in either definition — a check references bare column names — so
"identical modulo the table name" needs no substitution here, and the test
asserts that too rather than assuming it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: Module-level SQL, never assembled from values (Ruff S608).
CHECK_DEFINITIONS_SQL = text(
    """
    SELECT t.relname AS table_name, c.conname AS constraint_name,
           pg_get_constraintdef(c.oid) AS definition
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE c.contype = 'c' AND t.relname IN ('line_posterior', 'held_out_prediction')
    """
)

#: The delivered store, whose checks E003 owns, and E007's own.
DELIVERED_STORE = "line_posterior"
HELD_OUT_STORE = "held_out_prediction"

#: The seven shared properties DV-027 enumerates, by the constraint suffix each
#: is declared under on both tables. Written out rather than derived by set
#: intersection: an intersection would shrink silently when a constraint is
#: dropped, and report a pass over whatever survived on both sides.
SHARED_ARRAY_INVARIANTS = (
    "draws_1d",
    "draws_length",
    "draws_sorted",
    "draws_non_negative",
    "survival_1d",
    "survival_length",
    "survival_monotone",
    "survival_unit_interval",
    "residual_range",
    "residual_matches_grid_tail",
    "draw_digest_length",
)

#: The three `IMMUTABLE` helpers E003 delivered in `0008`. **Called by both
#: stores' checks and re-declared by neither** — DV-026's other half, and the
#: discipline ADR-0018 § Consequences/Neutral names as what keeps sortedness,
#: monotonicity and unit-interval membership from acquiring two definitions.
SHARED_HELPER_FUNCTIONS = (
    "fn_is_sorted_ascending",
    "fn_is_non_increasing",
    "fn_all_within_unit_interval",
)


def _definitions(db_session: Session) -> dict[str, dict[str, str]]:
    """Both tables' check constraints, keyed by table and then by name."""
    found: dict[str, dict[str, str]] = {DELIVERED_STORE: {}, HELD_OUT_STORE: {}}
    for row in db_session.execute(CHECK_DEFINITIONS_SQL).mappings():
        found[row["table_name"]][row["constraint_name"]] = row["definition"]
    return found


@pytest.mark.parametrize("invariant", SHARED_ARRAY_INVARIANTS)
def test_each_shared_array_invariant_is_declared_on_both_stores(
    db_session: Session, invariant: str
) -> None:
    """Presence, one property at a time, so a failure names which one diverged.

    Parametrized rather than looped, because "the array invariants are paired" is
    eleven separate claims and a single assertion over all of them reports the
    first break and hides the rest.
    """
    found = _definitions(db_session)

    assert f"ck_{DELIVERED_STORE}__{invariant}" in found[DELIVERED_STORE], (
        f"the delivered `line_posterior` no longer declares {invariant!r}; the pairing has "
        f"a side missing, and E003 owns that half"
    )
    assert f"ck_{HELD_OUT_STORE}__{invariant}" in found[HELD_OUT_STORE], (
        f"`held_out_prediction` does not declare {invariant!r}. No delivered constraint "
        f"reaches that table, so the property is now enforced on one population and not the "
        f"other — the divergence ADR-0018 accepted the duplication in order to detect"
    )


@pytest.mark.parametrize("invariant", SHARED_ARRAY_INVARIANTS)
def test_each_paired_definition_is_identical_on_both_stores(
    db_session: Session, invariant: str
) -> None:
    """Equality of the normalised expressions, which is where a weakening shows.

    A check strengthened, weakened or re-scoped on one store alone is present
    under its own name on both sides and fails here. `pg_get_constraintdef`
    renders the parsed expression, so differences in whitespace, casing or
    parenthesisation in the DDL do not register and a genuine change in the rule
    does.
    """
    found = _definitions(db_session)
    delivered = found[DELIVERED_STORE][f"ck_{DELIVERED_STORE}__{invariant}"]
    held_out = found[HELD_OUT_STORE][f"ck_{HELD_OUT_STORE}__{invariant}"]

    assert held_out == delivered, (
        f"{invariant!r} is declared differently on the two stores:\n"
        f"  {DELIVERED_STORE}: {delivered}\n"
        f"  {HELD_OUT_STORE}: {held_out}\n"
        f"A property that holds of one artifact population and not the other is the "
        f"divergence DV-027 exists to fail on"
    )


def test_no_paired_definition_mentions_the_table_it_sits_on(db_session: Session) -> None:
    """"Identical modulo the table name" needs no substitution, and that is checked.

    A check constraint references bare column names, so neither table's name
    appears in either definition — which is what lets the comparison above be
    plain equality. Asserted rather than assumed, because a future check written
    with a qualified column reference would make the equality unsatisfiable for a
    reason that has nothing to do with the rule diverging.
    """
    found = _definitions(db_session)
    for table, definitions in found.items():
        for name, definition in definitions.items():
            assert table not in definition, (
                f"{name} carries its own table name in {definition!r}; the paired comparison "
                f"is equality and a qualified reference would make it fail spuriously"
            )


def test_both_stores_call_the_delivered_helpers_rather_than_a_copy(
    db_session: Session
) -> None:
    """DV-026's consequence, seen from the parity side.

    Three of the shared invariants are enforced by `IMMUTABLE` helper functions,
    and a re-declared copy under an E007 name would leave the two definitions
    textually different — so this would already fail above. It is stated
    separately because the *reason* matters: E003's helpers are called and never
    re-declared, which is what stops sortedness, monotonicity and unit-interval
    membership from acquiring two definitions that agree today.
    """
    found = _definitions(db_session)
    for helper in SHARED_HELPER_FUNCTIONS:
        callers = {
            table
            for table, definitions in found.items()
            if any(helper in definition for definition in definitions.values())
        }

        assert callers == {DELIVERED_STORE, HELD_OUT_STORE}, (
            f"{helper} is called by {sorted(callers) or 'neither store'}; it is E003's "
            f"function and both populations' checks are meant to call it"
        )


def test_the_two_stores_declare_no_unpaired_array_check(db_session: Session) -> None:
    """The set difference, so a check added to one store alone is caught too.

    The parametrized tests above quantify over a written list, which cannot see a
    constraint nobody thought to enumerate. This closes that direction: every
    check on either table has a counterpart of the same suffix on the other, or
    it is an unpaired rule and the stores have diverged in the direction DV-027
    does not otherwise look.
    """
    found = _definitions(db_session)
    delivered = {name.removeprefix(f"ck_{DELIVERED_STORE}__") for name in found[DELIVERED_STORE]}
    held_out = {name.removeprefix(f"ck_{HELD_OUT_STORE}__") for name in found[HELD_OUT_STORE]}
    # `line_delivered`, `anchor_convention` and `duration_semantic` have no
    # counterpart by design: they are what makes the held-out store a *second*
    # population rather than a copy, and `line_posterior` carries no anchor
    # column for them to pair against (G-5).
    held_out_only = {"line_delivered", "anchor_convention", "duration_semantic"}

    assert held_out - delivered == held_out_only, (
        f"`held_out_prediction` carries the unpaired check(s) "
        f"{sorted(held_out - delivered - held_out_only)}; a rule on one store only is a "
        f"divergence whether it was added or dropped"
    )
    assert not delivered - held_out, (
        f"`line_posterior` carries the check(s) {sorted(delivered - held_out)} that "
        f"`held_out_prediction` does not; the delivered store was strengthened and the "
        f"E007-owned one was not"
    )
