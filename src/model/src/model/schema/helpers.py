"""`CREATE FUNCTION` statements for the schema's immutable SQL helpers.

`data-model.md` §Immutable Helper Functions declares five helpers, each
`IMMUTABLE STRICT PARALLEL SAFE`, each taking arguments only. They exist because
a `CHECK` constraint admits no subquery: element-wise array validation and
"is this ordered pair in the transition table" both need a callable, and the
callable has to be immutable or the check is unsound.

**Why the DDL lives here and not inline in the migration.** Two reasons, both
mechanical:

1. A helper is created by one revision and referenced by checks in others.
   `fn_is_legal_lifecycle_transition` is created by `0007` and referenced by
   `ck_lifecycle_event__legal_transition`; the sortedness family is created by
   `0008`. Keeping the definitions in one module makes the set readable as a
   set, which is what the constraint audit (T051) and the "no undocumented
   object" check (T052) read against `data-model.md`.
2. The recorded restriction on changing one -- `CREATE OR REPLACE FUNCTION`
   does **not** re-validate existing rows, so a change is a two-step forward
   migration under a *new name*, never an in-place replace -- is a property of
   the function set, not of any one migration. Stating it once, here, is the
   only placement where a future author is likely to read it.

Each constant is a complete, parameterless SQL statement, executed as
`op.execute(FN_...)`. Nothing here is formatted, interpolated, or assembled from
values at the call site -- there is no value to assemble, and Ruff S608 exists
because SQL built by concatenation is how injection happens.

`fn_all_sha256_prefixed` is deliberately **not** here: it was created inline by
revision `0003`, before this module existed, and moving its definition now would
change nothing in the database while making `0003` read as though it depended on
a module it does not import. A revision already applied is history.

Extension shape, followed by the three array helpers below (`0008`): add one
module-level constant per function, add its name to `__all__`, and record in the
constant's own docstring comment which revision creates it and which checks call
it.

**One property the three array helpers share, and it is deliberate: a NULL
element makes every one of them return false.** `STRICT` covers a null *array*
and nothing else -- it says nothing about `'{1.0, NULL, 3.0}'`, whose interior
null a naive body would compare with `<` and get NULL, which `NOT EXISTS`
swallows and a `CHECK` then *accepts*. The pattern below (`p_values[i] IS NULL
OR ...`, or `(...) IS NOT TRUE`) turns that into a definite false. Recorded here
rather than per function because it is the reason all three are written in a
shape that looks more defensive than the one-line summary in `data-model.md`
§Immutable Helper Functions suggests, and because two checks on `line_posterior`
(`ck_line_posterior__draws_non_negative` and
`ck_line_posterior__residual_matches_grid_tail`) rely on it to close their own
null branch instead of carrying a `coalesce`.
"""

from __future__ import annotations

__all__ = [
    "FN_ALL_WITHIN_UNIT_INTERVAL",
    "FN_IS_LEGAL_LIFECYCLE_TRANSITION",
    "FN_IS_NON_INCREASING",
    "FN_IS_SORTED_ASCENDING",
]


#: Creates `fn_is_legal_lifecycle_transition(text, text) -> boolean`.
#:
#: Created by revision `0007`. Called by `ck_lifecycle_event__legal_transition`
#: on `lifecycle_event` (TR-022, invariant 12 in `data-model.md`'s
#: invariant->mechanism map).
#:
#: The body is the seven-row transition table from `data-model.md` §State
#: Machines, verbatim and in its declared order, as a literal `VALUES` list. It
#: reads no table, so it is genuinely `IMMUTABLE`: a validated `CHECK` is
#: emitted with the table ahead of the data, so a dump-and-restore re-proves the
#: invariant row by row, and `pg_constraint` records the function's *identity*
#: rather than its text. A version of this that read a `lifecycle_transition`
#: lookup table would be a lie about immutability -- the check would pass or fail
#: depending on rows loaded later in the same restore.
#:
#: `STRICT` matters here rather than being decoration. The opening event of a
#: line has `from_state IS NULL` (`ck_lifecycle_event__first_has_no_predecessor`
#: makes that biconditional on `sequence_no = 1`), and `STRICT` means the
#: function is not called at all on a null argument and yields NULL. A `CHECK`
#: rejects only on *false*, so NULL would be accepted -- which is why the
#: calling check is written `from_state IS NULL OR fn_...(from_state, to_state)`
#: and the null branch is closed by two separate checks rather than left to this
#: function. `(NULL, 'submitted')` is intentionally absent from the list below:
#: the opening transition is not a row in the table, it is the absence of one.
#:
#: Parameters are named `p_from_state` / `p_to_state`, not `from_state` /
#: `to_state`. A SQL-language function body can reference its parameters by
#: name, and PostgreSQL resolves an ambiguity between a parameter name and a
#: column name of the same name in favour of the *column* -- so naming them after
#: the `VALUES` alias's columns would silently compare each column to itself and
#: make the function return true for every pair. Renaming the parameters is not
#: a style choice; it is what keeps the comparison meaningful. The declared
#: signature `(text, text)` is unchanged, and every call site is positional.
#:
#: `cancelled` is deliberately absent from the state set. Adding it is an
#: additive forward migration touching this function, `ck_lifecycle_event__to_state`,
#: `ck_pol__lifecycle_state`, and `ck_lifecycle_event__terminal_iff_delivered`
#: -- and, per the restriction in the module docstring, this function under a
#: new name rather than replaced in place.
FN_IS_LEGAL_LIFECYCLE_TRANSITION = """
CREATE FUNCTION fn_is_legal_lifecycle_transition(p_from_state text, p_to_state text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM (
            VALUES
                -- 1. Reviewer picks it up.
                ('submitted', 'under_review'),
                -- 2. Clean pass.
                ('under_review', 'approved'),
                -- 3. Rejected.
                ('under_review', 'revise_and_resubmit'),
                -- 4. The rework loop. Repeats freely: it returns to a state
                --    that already has an outgoing edge, so nothing bounds the
                --    number of review cycles (OBJ4 VC3).
                ('revise_and_resubmit', 'submitted'),
                -- 5. Released to the vendor.
                ('approved', 'released_for_fabrication'),
                -- 6. Left the plant.
                ('released_for_fabrication', 'shipped'),
                -- 7. Terminal.
                ('shipped', 'delivered')
        ) AS legal (from_state, to_state)
        WHERE legal.from_state = p_from_state
          AND legal.to_state = p_to_state
    )
$$
"""


#: Creates `fn_is_sorted_ascending(double precision[]) -> boolean`.
#:
#: Created by revision `0008`. Called by `ck_line_posterior__draws_sorted` on
#: `line_posterior` (TR-028, TR-070, invariant 20 in `data-model.md`'s
#: invariant->mechanism map).
#:
#: `true` when no element is strictly less than its predecessor -- ties allowed,
#: because 4,000 draws quantised to a day grid repeat constantly and a strict
#: ordering would reject every real posterior. Empty array: vacuously `true`;
#: there is no adjacent pair to violate anything. The empty case is owned by
#: `ck_line_posterior__draws_length`, not by this function, and deliberately so
#: -- if both rejected `'{}'` two constraints would be false on the same row and
#: which one the server reports would be an implementation detail.
#:
#: Two things in the body are load-bearing and neither is obvious from the
#: one-line summary in `data-model.md`:
#:
#: 1. `p_values[i] IS NULL` -- see the module docstring. `'{1.0, NULL, 3.0}'`
#:    under a bare `p_values[i] < p_values[i - 1]` yields NULL on both pairs
#:    touching the null, `NOT EXISTS` reports `true`, and the CHECK accepts a
#:    draw array with a hole in it. Rejecting nulls here is also what lets
#:    `ck_line_posterior__draws_non_negative` stay the plain `draws[1] >= 0.0`
#:    that `data-model.md` declares: a null first element is refused by this
#:    function instead, including in the single-element case that no adjacent-pair
#:    comparison can reach.
#: 2. `i > array_lower(p_values, 1)` rather than `i > 1`. PostgreSQL array
#:    subscripts are not required to start at 1 -- `'[0:2]={0.1,0.2,0.3}'` is a
#:    legal `double precision[]` with lower bound 0 -- and a hardcoded `i > 1`
#:    would skip the 0->1 pair entirely, so `'[0:2]={9,1,2}'` would pass as
#:    sorted. `array_lower` is immutable, so the guard costs nothing. The arrays
#:    on `line_posterior` are additionally pinned to lower bound 1 by
#:    `ck_line_posterior__draws_1d` / `__survival_1d`, because the percentile and
#:    residual conventions index them directly; this function does not rely on
#:    that and is correct without it.
#:
#: NaN is left with PostgreSQL's own float8 ordering, in which NaN sorts above
#: every other value and equals itself. So `'{1.0, NaN}'` is sorted and
#: `'{NaN, 1.0}'` is not. That is the ordering `ORDER BY` and every b-tree in the
#: database use, and disagreeing with it here would make "sorted" mean two
#: different things in one schema. A NaN draw is refused for `survival` by
#: `fn_all_within_unit_interval` (`NaN <= 1.0` is false); on `draws` it is
#: disclosed as out of scope rather than silently handled -- `data-model.md`
#: declares no finiteness constraint on the draw array.
FN_IS_SORTED_ASCENDING = """
CREATE FUNCTION fn_is_sorted_ascending(p_values double precision[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT NOT EXISTS (
        SELECT 1
        FROM generate_subscripts(p_values, 1) AS subscript (i)
        WHERE p_values[subscript.i] IS NULL
           OR (
                subscript.i > array_lower(p_values, 1)
                AND p_values[subscript.i] < p_values[subscript.i - 1]
              )
    )
$$
"""


#: Creates `fn_is_non_increasing(double precision[]) -> boolean`.
#:
#: Created by revision `0008`. Called by `ck_line_posterior__survival_monotone`
#: on `line_posterior` (TR-029).
#:
#: `true` when no element is strictly greater than its predecessor. The mirror of
#: `fn_is_sorted_ascending` with the comparison reversed, and every note on that
#: constant applies unchanged: null elements rejected, `array_lower` rather than
#: a hardcoded 1, empty array vacuously true, PostgreSQL's float8 NaN ordering.
#:
#: This is the survival curve's shape constraint. `survival[k]` is
#: `P(not delivered by day k)`, which cannot rise: a delivery does not un-happen.
#: Ties are allowed and are the common case -- a day with no probability mass
#: leaves the curve flat.
FN_IS_NON_INCREASING = """
CREATE FUNCTION fn_is_non_increasing(p_values double precision[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT NOT EXISTS (
        SELECT 1
        FROM generate_subscripts(p_values, 1) AS subscript (i)
        WHERE p_values[subscript.i] IS NULL
           OR (
                subscript.i > array_lower(p_values, 1)
                AND p_values[subscript.i] > p_values[subscript.i - 1]
              )
    )
$$
"""


#: Creates `fn_all_within_unit_interval(double precision[]) -> boolean`.
#:
#: Created by revision `0008`. Called by
#: `ck_line_posterior__survival_unit_interval` on `line_posterior` (TR-029).
#:
#: `true` when every element is in `[0, 1]`, inclusive at both ends -- a survival
#: curve legitimately holds 1.0 on day 1 and 0.0 once all mass has been
#: consumed.
#:
#: Written `(... ) IS NOT TRUE` rather than `NOT (...)`, and that is the whole
#: subtlety. `NOT (NULL >= 0.0 AND NULL <= 1.0)` is NULL, which a `WHERE` treats
#: as "no row", so `NOT EXISTS` would report `true` and a survival array with a
#: null element would be accepted. `IS NOT TRUE` collapses both NULL and false to
#: true, so a null element and an out-of-range element are refused by the same
#: expression. It also refuses NaN for free -- `NaN <= 1.0` is false -- which is
#: the reading `data-model.md`'s "every element is in [0, 1]" requires, since NaN
#: is in no interval.
#:
#: Because this function is what makes every element of `survival` a definite
#: number in `[0, 1]`, `ck_line_posterior__residual_matches_grid_tail` can be the
#: plain tolerance comparison `data-model.md` declares: its
#: `survival[horizon_days]` operand cannot be NULL, given the length check pins
#: the subscript in range and this check pins the element non-null.
FN_ALL_WITHIN_UNIT_INTERVAL = """
CREATE FUNCTION fn_all_within_unit_interval(p_values double precision[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT NOT EXISTS (
        SELECT 1
        FROM generate_subscripts(p_values, 1) AS subscript (i)
        WHERE (p_values[subscript.i] >= 0.0 AND p_values[subscript.i] <= 1.0) IS NOT TRUE
    )
$$
"""
