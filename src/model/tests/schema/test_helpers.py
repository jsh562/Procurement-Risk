"""Property-based tests for the three immutable array helpers created by `0008`.

`fn_is_sorted_ascending`, `fn_is_non_increasing`, and `fn_all_within_unit_interval`
are the only pure scoring-style functions this epic introduces, and
`project-instructions.md` §Testing & Quality Policy requires property-based tests
over pure functions. Hypothesis, per plan.md's Testing Strategy table.

**Why these tests are here, in the schema tier, rather than in a pure-Python unit
tier.** These functions are written in SQL, so PostgreSQL is their only
interpreter. Reimplementing them in Python and property-testing the
reimplementation would test a copy, not the constraint that actually guards
`line_posterior`. So the property under test is a *differential* one: for every
array Hypothesis can build, the server's answer equals an independently written
Python oracle's. That catches the class of bug this task exists to prevent --
NULL swallowed by three-valued logic, a hardcoded lower bound of 1, an off-by-one
in the adjacent-pair comparison -- none of which a hand-picked example list is
likely to contain, and none of which is visible by reading the SQL.

The oracles below deliberately do not use `sorted()` or `min()`/`max()`. They
mirror PostgreSQL's own float8 total order, in which NaN sorts *above* every
other value and equals itself, because that is the order the database's b-trees
and `ORDER BY` use and the helpers must agree with it or "sorted" would mean two
things in one schema. Python's own comparison operators disagree (every NaN
comparison is false), which is exactly why the key function is explicit.

These tests read; they never write. So they take a module-scoped connection
rather than the function-scoped `db_session`, which also keeps Hypothesis's
`function_scoped_fixture` health check quiet -- that check fires precisely because
a per-example fixture is *not* reset between examples, and there is no state here
to reset.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine, TextClause, text

#: Every helper takes one `double precision[]` and returns `boolean`. Each is
#: called with the array cast explicitly rather than left to parameter type
#: inference: an empty list and an all-NULL list give psycopg no element to infer
#: an element type from, and those are two of the cases most worth testing.
SORTED_ASCENDING = text("SELECT fn_is_sorted_ascending(CAST(:values AS double precision[]))")
NON_INCREASING = text("SELECT fn_is_non_increasing(CAST(:values AS double precision[]))")
WITHIN_UNIT_INTERVAL = text(
    "SELECT fn_all_within_unit_interval(CAST(:values AS double precision[]))"
)

#: Bounded to 64 bits because the column type is `double precision`; a Python
#: float wider than that would be rounded on the way in and the oracle would then
#: be comparing a different number than the server saw.
#:
#: NaN and both infinities are generated on purpose. They are the values whose
#: comparison semantics differ between Python and PostgreSQL, so excluding them
#: would remove the only examples that can catch a disagreement.
ELEMENTS = st.floats(width=64, allow_nan=True, allow_infinity=True)

#: `st.none()` for NULL elements -- the trap the helpers exist to close, and the
#: one `STRICT` does not cover: strictness applies to a null *array*, not to a
#: null element inside a non-null one.
ARRAYS = st.lists(st.one_of(st.none(), ELEMENTS), max_size=16)

#: A survival-curve-shaped generator, so the unit-interval property is exercised
#: on values that mostly *are* probabilities rather than almost never being any.
PROBABILITY_ARRAYS = st.lists(
    st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=1.0, width=64),
        ELEMENTS,
    ),
    max_size=16,
)

#: Hypothesis runs each example as one database round trip. `deadline=None`
#: because a round trip's latency is not a property of the function under test --
#: a slow example would otherwise fail the suite for a reason unrelated to
#: correctness.
HELPER_SETTINGS = settings(
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)


@pytest.fixture(scope="module", name="helpers")
def helpers_connection(engine: Engine) -> Iterator[Connection]:
    """A read-only connection for the whole module.

    Module-scoped deliberately; see the module docstring. Nothing here writes, so
    there is no isolation requirement and no teardown beyond closing.
    """
    with engine.connect() as connection:
        yield connection


def _float8_key(value: float) -> tuple[int, float]:
    """PostgreSQL's float8 total order as a Python sort key.

    `-inf < finite < +inf < NaN`, and NaN equals itself. Tuples compare
    lexicographically, so tagging NaN with a leading 1 puts it above every real
    value and equal to every other NaN -- which is what `float8lt` does and what
    every b-tree on a `double precision` column is built on.

    Python's bare `<` cannot express this: `float('nan') < 1.0` and
    `1.0 < float('nan')` are both false, so a Python-native pairwise comparison
    would call `{NaN, 1.0}` sorted *and* `{1.0, NaN}` sorted, and would agree with
    the server on one of the two by luck.
    """
    return (1, 0.0) if math.isnan(value) else (0, value)


def _oracle_is_sorted_ascending(values: list[float | None]) -> bool:
    """True when no element is strictly less than its predecessor.

    A NULL element makes this false -- the documented behaviour of the SQL
    function, and the reason `ck_line_posterior__draws_non_negative` can stay the
    plain `draws[1] >= 0.0` that `data-model.md` declares. The empty list is
    vacuously true: there is no adjacent pair to violate anything.
    """
    if any(value is None for value in values):
        return False
    keys = [_float8_key(value) for value in values if value is not None]
    return all(keys[i] >= keys[i - 1] for i in range(1, len(keys)))


def _oracle_is_non_increasing(values: list[float | None]) -> bool:
    """True when no element is strictly greater than its predecessor."""
    if any(value is None for value in values):
        return False
    keys = [_float8_key(value) for value in values if value is not None]
    return all(keys[i] <= keys[i - 1] for i in range(1, len(keys)))


def _oracle_all_within_unit_interval(values: list[float | None]) -> bool:
    """True when every element is a number in `[0, 1]`, inclusive.

    NULL is not in the interval and neither is NaN, and Python agrees with
    PostgreSQL on both without any key function: `None` is filtered explicitly,
    and `0.0 <= nan <= 1.0` is false in both languages.
    """
    return all(value is not None and 0.0 <= value <= 1.0 for value in values)


def _evaluate(
    connection: Connection, statement: TextClause, values: list[float | None]
) -> bool | None:
    """Run one helper against one array and return the server's answer."""
    return connection.execute(statement, {"values": values}).scalar()


@given(values=ARRAYS)
@HELPER_SETTINGS
def test_fn_is_sorted_ascending_agrees_with_the_oracle(
    helpers: Connection, values: list[float | None]
) -> None:
    """TR-070: the server's notion of ascending is the one `data-model.md` declares.

    Ties allowed, NULL elements refused, PostgreSQL's NaN ordering respected. Any
    disagreement here is a disagreement between the constraint guarding
    `line_posterior.draws` and the documented rule, which is the only thing a
    reader of `data-model.md` can rely on.
    """
    assert _evaluate(helpers, SORTED_ASCENDING, values) is _oracle_is_sorted_ascending(values)


@given(values=ARRAYS)
@HELPER_SETTINGS
def test_fn_is_non_increasing_agrees_with_the_oracle(
    helpers: Connection, values: list[float | None]
) -> None:
    """TR-029: the survival curve's shape rule, checked the same way."""
    assert _evaluate(helpers, NON_INCREASING, values) is _oracle_is_non_increasing(values)


@given(values=PROBABILITY_ARRAYS)
@HELPER_SETTINGS
def test_fn_all_within_unit_interval_agrees_with_the_oracle(
    helpers: Connection, values: list[float | None]
) -> None:
    """TR-029: every survival element a probability, inclusive at both ends."""
    assert _evaluate(helpers, WITHIN_UNIT_INTERVAL, values) is _oracle_all_within_unit_interval(
        values
    )


@given(values=st.lists(st.floats(width=64, allow_nan=False), min_size=1, max_size=16))
@HELPER_SETTINGS
def test_sortedness_and_monotonicity_are_mirror_images(
    helpers: Connection, values: list[float]
) -> None:
    """A property neither oracle can accidentally satisfy: the two are reverses.

    Differential testing against an oracle catches a wrong function; it cannot
    catch two functions written wrong the *same* way, because the oracles are
    written by the same hand on the same day. This property is independent of both
    oracles: whatever `fn_is_sorted_ascending` says about a list,
    `fn_is_non_increasing` must say about its reverse. NaN is excluded here only
    because it makes the relation uninteresting rather than untrue -- a NaN is
    both `>=` and `<=` itself, so a list of NaNs is trivially both.
    """
    ascending = _evaluate(helpers, SORTED_ASCENDING, list(values))
    reversed_non_increasing = _evaluate(helpers, NON_INCREASING, list(reversed(values)))
    assert ascending is reversed_non_increasing


@given(values=st.lists(st.floats(min_value=0.0, max_value=1.0, width=64), max_size=16))
@HELPER_SETTINGS
def test_every_probability_array_is_within_the_unit_interval(
    helpers: Connection, values: list[float]
) -> None:
    """The oracle-independent half of the unit-interval property.

    Anything Hypothesis draws from `[0.0, 1.0]` must be accepted, with no
    reference to the Python oracle at all. This is what would catch an inverted
    comparison or an exclusive bound that the differential test could only catch
    if the oracle happened to be right.
    """
    assert _evaluate(helpers, WITHIN_UNIT_INTERVAL, list(values)) is True


@pytest.mark.parametrize(
    ("values", "sorted_ascending", "non_increasing", "within_unit_interval"),
    [
        pytest.param([], True, True, True, id="empty-is-vacuously-true"),
        pytest.param([0.5], True, True, True, id="single-element"),
        pytest.param([1.0, 1.0, 1.0], True, True, True, id="all-ties"),
        pytest.param([0.0, 1.0], True, False, True, id="strictly-ascending"),
        pytest.param([1.0, 0.0], False, True, True, id="strictly-descending"),
        pytest.param([None], False, False, False, id="single-null-element"),
        pytest.param([0.5, None], False, False, False, id="trailing-null-element"),
        pytest.param([None, 0.5], False, False, False, id="leading-null-element"),
        pytest.param([0.0, 1.0000000001], True, False, False, id="just-above-one"),
        pytest.param([-0.0], True, True, True, id="negative-zero-is-in-range"),
    ],
)
def test_declared_boundary_cases(
    helpers: Connection,
    values: list[float | None],
    sorted_ascending: bool,
    non_increasing: bool,
    within_unit_interval: bool,
) -> None:
    """The cases worth pinning by name rather than trusting a generator to find.

    `[None]` is the one that matters most: a single NULL element has no adjacent
    pair, so a helper written as "no pair is out of order" returns true for it,
    `ck_line_posterior__draws_sorted` accepts it, and
    `ck_line_posterior__draws_non_negative` then evaluates `NULL >= 0.0` -- which
    is NULL, which a `CHECK` accepts. `'{NULL}'` with `draw_count = 1` would be a
    storable posterior with no draws in it. This row is why all three helpers test
    every element for null rather than only the pairs.
    """
    assert _evaluate(helpers, SORTED_ASCENDING, values) is sorted_ascending
    assert _evaluate(helpers, NON_INCREASING, values) is non_increasing
    assert _evaluate(helpers, WITHIN_UNIT_INTERVAL, values) is within_unit_interval


def test_a_null_array_yields_null_through_strictness(helpers: Connection) -> None:
    """`STRICT` means a null *array* is never passed to the body at all.

    Worth asserting rather than assuming, because it is the difference between the
    three helpers and `fn_is_legal_lifecycle_transition`: that one is called from a
    check on a nullable column and needs an `IS NULL OR ...` guard around it, while
    these three are called only on NOT NULL array columns and need none. If any of
    them ever returned `false` here instead of NULL, that guard would become
    load-bearing and its absence a defect.
    """
    for statement in (SORTED_ASCENDING, NON_INCREASING, WITHIN_UNIT_INTERVAL):
        assert _evaluate(helpers, statement, None) is None  # type: ignore[arg-type]


def test_a_lower_bound_zero_array_is_compared_from_its_own_first_element(
    helpers: Connection,
) -> None:
    """PostgreSQL array subscripts need not start at 1, and the helpers must not assume so.

    `'[0:2]={9,1,2}'` is a legal one-dimensional `double precision[]` whose
    elements sit at subscripts 0, 1, 2. A body written `WHERE i > 1` skips the
    0->1 pair entirely and reports this array as sorted. `array_lower(p_values, 1)`
    is what makes it report false.

    The arrays on `line_posterior` are separately pinned to lower bound 1 by
    `ck_line_posterior__draws_1d` and `__survival_1d`, because the percentile and
    residual conventions subscript them directly. The helpers do not rely on that
    and are asserted here to be correct without it.
    """
    unsorted_from_zero = helpers.execute(
        text("SELECT fn_is_sorted_ascending('[0:2]={9,1,2}'::double precision[])")
    ).scalar()
    assert unsorted_from_zero is False

    sorted_from_zero = helpers.execute(
        text("SELECT fn_is_sorted_ascending('[0:2]={1,2,9}'::double precision[])")
    ).scalar()
    assert sorted_from_zero is True

    rising_from_zero = helpers.execute(
        text("SELECT fn_is_non_increasing('[0:2]={0.1,0.9,0.2}'::double precision[])")
    ).scalar()
    assert rising_from_zero is False


def test_the_helpers_are_immutable_strict_and_parallel_safe(helpers: Connection) -> None:
    """The three properties that make a function sound inside a `CHECK`.

    Not decoration, and not implied by the DDL having been accepted: PostgreSQL
    takes `IMMUTABLE` on trust. A validated check constraint is emitted with the
    table ahead of the data in a dump, and `pg_constraint` records the function's
    *identity* rather than its text -- so a restore re-proves the invariant row by
    row by calling this function. If one of these were `STABLE` or `VOLATILE`,
    PostgreSQL would refuse it in a `CHECK` outright; if one silently read a table,
    it would pass the declaration and produce a check whose result depended on
    rows loaded later in the same restore. Asserting the catalogue's record is the
    part a test can do.
    """
    rows = helpers.execute(
        text(
            """
            SELECT proname, provolatile, proisstrict, proparallel
            FROM pg_proc
            WHERE pronamespace = 'public'::regnamespace
              AND proname IN (
                  'fn_is_sorted_ascending',
                  'fn_is_non_increasing',
                  'fn_all_within_unit_interval'
              )
            ORDER BY proname
            """
        )
    ).all()

    assert [row.proname for row in rows] == [
        "fn_all_within_unit_interval",
        "fn_is_non_increasing",
        "fn_is_sorted_ascending",
    ], "revision 0008 did not create all three array helpers in the public schema"

    for row in rows:
        assert row.provolatile == "i", f"{row.proname} is not IMMUTABLE"
        assert row.proisstrict is True, f"{row.proname} is not STRICT"
        assert row.proparallel == "s", f"{row.proname} is not PARALLEL SAFE"
