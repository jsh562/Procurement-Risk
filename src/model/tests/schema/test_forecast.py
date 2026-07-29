"""The versioned forecast artifact: `forecast_run`, `line_posterior`, and the active-run view.

Everything here is migration `0008`. Four groups, one per task:

* **T040 -- reproducibility and the active pointer (TR-026, TR-027).** Each of
  the nine reproducibility fields refused when null; a second *active* run
  refused both by `INSERT` and by `UPDATE`; and `v_active_forecast_run` returning
  **zero rows** when nothing is active, with no recency fallback anywhere in the
  revision or in any view.
* **T041 -- array shape (TR-028, TR-069, TR-070, TR-072, TR-073).** Unsorted
  draws, a wrong-length array, a wrong *declared* length, the empty array, a
  lower-bound-0 array, and a null element inside either array -- each refused,
  each attributed to the constraint that actually fired.
* **T042 -- the residual agrees with the grid tail, to a tolerance (TR-029,
  TR-030, TR-055).** Exact agreement accepted, agreement within `1e-9` accepted,
  disagreement at `1e-8` refused -- and the tolerance proven to be a tolerance
  rather than an equality, which SC-015 forbids.
* **T043 -- one row, one digest, one canonical array (TR-031, TR-040, TR-068).**
  Neither array insertable without the other; the digest taken over bytes in a
  named serialization; and the survival curve and every percentile derived from
  the draws.
* **T036 / T037 -- two deliberate absences (TR-062, TR-080).** No per-line link
  from a posterior row back to the extracted values or lifecycle events its fit
  consumed, and no maximum permitted age on a run. Both are design decisions
  stating that something is *not* here, so neither has a rejection to exercise;
  each is read out of the catalogue with its requirement's positive half asserted
  alongside, so an absence over an empty table cannot pass for evidence.

**Two mechanisms enforce array length, and they are not interchangeable.** A row
declares `draw_count` and `horizon_days`, and both are checked twice over:

* `fk_line_posterior__run_shape` proves the *declared* pair is the run's own,
  against `uq_forecast_run__shape`. A row declaring a count its run does not have
  is a `ForeignKeyViolation`, whatever its arrays look like.
* `ck_line_posterior__draws_length` / `__survival_length` then prove each *array*
  matches the declared -- and therefore proven -- number. A row whose array
  disagrees with a correctly declared count is a `CheckViolation`.

So "wrong length" is two different rejections depending on which half is wrong,
and both are asserted separately below. Asserting only one would leave the other
half of the chain unexercised while the test read as though it covered length.

**Three null traps in this table, all closed, none by the same mechanism.**

1. `array_length('{}', 1)` is **NULL, not 0**, so the bare
   `array_length(draws, 1) = draw_count` that `data-model.md` originally declared
   evaluates to NULL on the empty array -- and a `CHECK` rejects only on *false*,
   so it **accepts a posterior with no draws at all**. Closed by the delivered
   `coalesce(array_length(...), 0) = ...`. Asserted here by inserting the row the
   declared form would have taken.
2. PostgreSQL array subscripts need not start at 1. `'[0:4]={...}'` is a legal
   one-dimensional array of the declared length whose last element sits one
   subscript short of where the read conventions look for it -- so
   `survival[horizon_days]` is NULL, the residual check is NULL, and the row is
   accepted with a curve nobody can index. Closed by the `array_lower(..., 1) = 1`
   conjunct in `ck_line_posterior__draws_1d` / `__survival_1d`.
3. A NULL *element* inside a non-null array. `STRICT` says nothing about it; the
   three helpers in `model.schema.helpers` return false for it, which is what lets
   `ck_line_posterior__draws_non_negative` and
   `ck_line_posterior__residual_matches_grid_tail` stay the plain comparisons
   `data-model.md` declares instead of carrying a `coalesce` each.

**Never on message text.** Every rejection names the psycopg subclass and the
constraint that must have produced it, through `conftest.assert_rejects`; naming
the class is naming the SQLSTATE, since psycopg derives one class per state. The
exception is `NOT NULL`, which on PostgreSQL 16 carries `column_name` and **no
`constraint_name` at all** -- catalogued, nameable `NOT NULL` constraints arrive
in 17 -- so those assert the column, which is every bit as specific. See
`assert_not_null_violation`.

**Two tests alter the schema, on purpose.** DDL is transactional in PostgreSQL
and `conftest.db_session` rolls everything back, so a test may drop a constraint,
observe what the remaining ones do, and leave the schema untouched. That is the
only way to attribute a rejection when *two* constraints are false on one row
(the null survival element), and the only way to prove the residual check is
tolerance-based rather than merely to read that it is: swap it for the equality
`data-model.md` forbids and watch a row the delivered schema accepts get refused.

**Isolation.** Every test runs on `db_session` and commits nothing. `SET LOCAL`
is used for the one session setting a test changes, so it is unwound with the
transaction rather than left on a pooled connection.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from model.schema import helpers

#: `conftest.assert_rejects` as seen through its fixture. Requested rather than
#: imported for the reason that fixture's docstring gives: the import form relies
#: on pytest having put this directory on `sys.path`.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

# --------------------------------------------------------------------------- #
# Constraint names, spelled once
# --------------------------------------------------------------------------- #

SINGLE_ACTIVE_INDEX = "ix_forecast_run__single_active"
RUN_SHAPE_FOREIGN_KEY = "fk_line_posterior__run_shape"
DRAWS_1D = "ck_line_posterior__draws_1d"
DRAWS_LENGTH = "ck_line_posterior__draws_length"
DRAWS_SORTED = "ck_line_posterior__draws_sorted"
SURVIVAL_1D = "ck_line_posterior__survival_1d"
SURVIVAL_LENGTH = "ck_line_posterior__survival_length"
SURVIVAL_MONOTONE = "ck_line_posterior__survival_monotone"
SURVIVAL_UNIT_INTERVAL = "ck_line_posterior__survival_unit_interval"
RESIDUAL_MATCHES_GRID_TAIL = "ck_line_posterior__residual_matches_grid_tail"
DRAW_DIGEST_LENGTH = "ck_line_posterior__draw_digest_length"
ARTIFACT_HASH_LENGTH = "ck_forecast_run__artifact_hash_length"
DRAW_SERIALIZATION = "ck_forecast_run__draw_serialization"

#: The name the exact-equality mutation in T042 adds. Deliberately *not* one of
#: the delivered names: the rejection it produces must be attributable to the
#: mutation and to nothing in the shipped schema.
RESIDUAL_EXACT_EQUALITY = "ck_line_posterior__residual_exact_equality"

#: `PROB_SUM_TOLERANCE` (data-model.md §Declared Constants). Read here as the
#: number the test reasons about; T050 owns asserting the DDL literal against the
#: published `schema_constants` row, and per TR-076 the literal governs.
PROB_SUM_TOLERANCE = 1e-9

# --------------------------------------------------------------------------- #
# The nine reproducibility fields (TR-026)
# --------------------------------------------------------------------------- #

#: Named, in `0008`'s own order, because "nine reproducibility fields" is
#: otherwise a number nobody can check: which run, which code, which inputs,
#: which random stream, which numerical stack, which bytes came out, how to read
#: them, which model produced them, and when.
REPRODUCIBILITY_FIELDS: tuple[str, ...] = (
    "run_id",
    "code_commit",
    "input_data_hash",
    "seed_entropy",
    "library_versions",
    "artifact_hash",
    "artifact_schema_version",
    "model_version",
    "created_at",
)

#: `as_of_date` is `NOT NULL` on the same table and is deliberately **not** in the
#: tuple above. It is not one of TR-026's nine -- OBJ5 VC1 and SC-012 enumerate
#: those nine and it is not among them -- it is TR-049's grid anchor, and it earns
#: its own null-rejection test below so that a regression names TR-049 and OBJ5
#: VC9 rather than a requirement the column has nothing to do with. Adding it here
#: would have been the cheaper fix and would have made "nine reproducibility
#: fields" a claim this file's own parametrisation contradicts.
ANCHOR_DATE_FIELD = "as_of_date"

#: The array offset OBJ5 VC9's second half is asserted at. `2`, not `1`: with an
#: offset of 1 a schema that resolved offsets from the wrong base -- or ignored
#: the offset entirely -- would still land on `as_of_date + 1` often enough to
#: look right. It must be within `FIXTURE_HORIZON_DAYS`, since an offset past the
#: end of the grid denotes no day at all.
COMPARED_DAY_OFFSET = 2

# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

#: `sha256:` plus 64 lowercase hex digits -- the format E001 froze and `document`,
#: `purchase_order_line`, and `forecast_run` all share (TR-024). Every valid row
#: below carries a well-formed one, so a test aiming at some other rule cannot
#: trip a format check on the way there.
ROSTER_HASH = "sha256:" + "5c1d" * 16
INPUT_DATA_HASH = "sha256:" + "9ab3" * 16

#: 40 lowercase hex digits, which is the whole of a git object name.
CODE_COMMIT = "4f" * 20

#: The 128-bit root entropy recorded verbatim as decimal digits (TR-063).
SEED_ENTROPY = "273419827364981273649812736498127364"

#: The six keys `ck_forecast_run__library_versions_shape` requires present. The
#: values are version strings the schema has no business parsing.
LIBRARY_VERSIONS = (
    '{"pymc": "6.2.0", "arviz": "1.2.0", "numpy": "2.4.6", '
    '"pandas": "3.0.5", "pytensor": "2.40.1", "blas": "openblas-0.3.30"}'
)

#: 32 raw bytes each -- SHA-256 digests, as `bytea` and never as hex text
#: (TR-040). Distinct values so a test cannot pass by comparing a column with
#: itself.
ARTIFACT_HASH = bytes(range(100, 132))
DRAW_DIGEST = bytes(range(32))

# --------------------------------------------------------------------------- #
# E007's fourteen provenance columns (revision 0300, G-2)
# --------------------------------------------------------------------------- #
#
# **Why these constants are here, in a file E003 owns.** Revision `0300` adds
# fourteen `NOT NULL` columns to `forecast_run` with **no default**, because the
# delivered TR-063 defaults audit admits defaults on an enumerated six columns
# and none of these is one of them. Every `INSERT INTO forecast_run` in this
# repository names its columns explicitly, so all three of this module's run
# builders -- `RUN_INSERT`, `RUN_INSERT_LETTING_DEFAULTS_APPLY` and `run_row`
# -- would fail with a not-null violation the moment `0300` applies. Extending
# them is E007's **G-2** remediation and lands in the same change as the
# migration; deferring it would leave this suite red between two commits.
#
# The values are valid but deliberately *fixture-shaped*, in the same spirit as
# `FIXTURE_DRAW_COUNT = 5` against a declared 4,000: this file asserts what the
# delivered schema accepts and refuses, not what an E007 run looks like. The
# run-shape pin (4,000 draws, a 365-day grid) is asserted by E007 over the runs
# E007 emits (DV-014), never over every row in this table -- which is exactly
# why no `CHECK` binds it.

#: `ck_forecast_run__covariates_non_empty` wants a non-empty `text[]` with no
#: NULL and no all-blank element set. Passed as an array *literal* and cast
#: server-side, following the two posterior arrays above rather than relying on
#: the driver's list adaptation.
COVARIATE_NAMES = "{elapsed_days,vendor_id,material_category}"

#: The two E007 digests, distinct from `INPUT_DATA_HASH` and from each other so
#: a test cannot pass by comparing one column against another. Same `sha256:` +
#: 64 lowercase hex form the delivered columns use.
INPUT_FIXTURE_DIGEST = "sha256:" + "7e02" * 16
SPLIT_ASSIGNMENT_HASH = "sha256:" + "b41f" * 16

#: The split's root entropy, in the same 1-to-39 decimal digit form as
#: `SEED_ENTROPY` and deliberately a different number: the two are separate
#: columns precisely because the split seed is a committed constant while the
#: sampler's entropy is per run.
SPLIT_SEED_ENTROPY = "884120397465120398476512039847651203"

#: `ck_forecast_run__vendor_shrinkage_shape` calls
#: `fn_vendor_shrinkage_wellformed`: an object of `VND-###` keys whose values
#: carry exactly `median`, `hpdi_low` and `hpdi_high`, each in `[0, 1]` and
#: correctly ordered. Two vendors rather than one, so a helper that validated
#: only the first member would be caught here.
VENDOR_SHRINKAGE = (
    '{"VND-117": {"median": 0.62, "hpdi_low": 0.41, "hpdi_high": 0.83}, '
    '"VND-204": {"median": 0.18, "hpdi_low": 0.02, "hpdi_high": 0.47}}'
)

#: The remaining scalars. `held_out_fraction_realized` differs from the declared
#: fraction because a stratified split over a finite line set lands *near* the
#: declared value and not on it -- equal values here would let a builder that
#: wrote one column into both pass unnoticed.
HELD_OUT_FRACTION_DECLARED = 0.25
HELD_OUT_FRACTION_REALIZED = 0.2412060301507538
HELD_OUT_UNCENSORED_EVENT_COUNT = 44
OPEN_LINE_COUNT = 24
TRAINING_LINE_COUNT = 151

#: The run anchor. `date`, not `timestamptz`: day 0 of a delivery-duration grid
#: is a calendar day (TR-049).
AS_OF_DATE = date(2026, 3, 1)
CREATED_AT = datetime(2026, 3, 1, 18, 30, tzinfo=UTC)

#: The fixture run's shape. **Deliberately two different numbers**: with
#: `draw_count == horizon_days` a schema that had the two columns transposed --
#: or a check comparing the wrong array against the wrong count -- would pass
#: every test in this file.
FIXTURE_DRAW_COUNT = 5
FIXTURE_HORIZON_DAYS = 3

#: Delivery durations in days from `as_of_date`, ascending, with a tie -- ties are
#: the common case in 4,000 draws quantised to a day grid, so the fixture has one.
FIXTURE_DRAWS: tuple[float, ...] = (1.0, 2.0, 2.0, 4.0, 9.0)

#: `survival[k] = P(not delivered by day k)`, derived from `FIXTURE_DRAWS` rather
#: than invented: the count of draws beyond day k over the draw count. That is
#: what makes the T043 derivability assertion a real comparison and not a
#: restatement of a literal (TR-068).
FIXTURE_SURVIVAL: tuple[float, ...] = tuple(
    sum(1 for draw in FIXTURE_DRAWS if draw > day) / FIXTURE_DRAW_COUNT
    for day in range(1, FIXTURE_HORIZON_DAYS + 1)
)

#: `P(T > horizon_days)`, computed by the same path -- so it agrees with
#: `survival[horizon_days]` exactly, which is the ordinary case the residual
#: check must accept.
FIXTURE_RESIDUAL = (
    sum(1 for draw in FIXTURE_DRAWS if draw > FIXTURE_HORIZON_DAYS) / FIXTURE_DRAW_COUNT
)

#: The shape `data-model.md` §Declared Constants actually declares. One test
#: inserts it, so "4,000 draws and a 365-day grid" is a demonstrated fact about
#: the delivered table rather than a number in a document.
DECLARED_DRAW_COUNT = 4000
DECLARED_HORIZON_DAYS = 365
DECLARED_DRAWS: tuple[float, ...] = tuple(float(i) for i in range(1, DECLARED_DRAW_COUNT + 1))
DECLARED_SURVIVAL: tuple[float, ...] = tuple(
    (DECLARED_DRAW_COUNT - day) / DECLARED_DRAW_COUNT for day in range(1, DECLARED_HORIZON_DAYS + 1)
)
DECLARED_RESIDUAL = (DECLARED_DRAW_COUNT - DECLARED_HORIZON_DAYS) / DECLARED_DRAW_COUNT

#: A one-dimensional array of the declared length whose subscripts run 0..4
#: instead of 1..5. Legal PostgreSQL, and the reason
#: `ck_line_posterior__draws_1d` pins `array_lower` as well as `array_ndims`.
#: Written as a literal because no Python list can express a lower bound.
LOWER_BOUND_ZERO_DRAWS = "[0:4]={1.0,2.0,2.0,4.0,9.0}"
LOWER_BOUND_ZERO_SURVIVAL = "[0:2]={0.8,0.4,0.4}"

#: Draws whose text rendering depends on `extra_float_digits`, for the T043
#: assertion that the digest does not. The first element needs all 17 significant
#: digits; a 15-digit rendering of it is a *different number*.
RENDERING_SENSITIVE_DRAWS: tuple[float, ...] = (1.2345678901234567, 2.5, 3.0, 4.0, 9.0)

LINE_INSERT = text(
    """
    INSERT INTO purchase_order_line (
        po_line_id, project_id, vendor_id, po_number, line_number,
        material_category, description, manufacturer, part_number,
        quantity, unit_of_measure, order_date, need_by_date, criticality,
        lifecycle_state, is_closed, closing_event_id, roster_hash
    )
    VALUES (
        :po_line_id, :project_id, :vendor_id, :po_number, :line_number,
        :material_category, :description, :manufacturer, :part_number,
        :quantity, :unit_of_measure, :order_date, :need_by_date, :criticality,
        :lifecycle_state, :is_closed, :closing_event_id, :roster_hash
    )
    """
)

#: Names every column, `is_active` and `created_at` included, because a `NOT NULL`
#: test on a column with a `DEFAULT` has to pass an **explicit NULL** to defeat
#: the default -- omitting the column proves nothing.
#:
#: **Extended with E007's fourteen provenance columns (revision `0300`, G-2).**
#: They carry no default and are `NOT NULL`, so an insert that omitted them
#: would be refused by the schema rather than defaulted -- which is the same
#: reason the statement already names `is_active` and `created_at`.
RUN_INSERT = text(
    """
    INSERT INTO forecast_run (
        run_id, code_commit, code_worktree_dirty, input_data_hash, seed_entropy,
        chain_count, draw_count, tuning_count, library_versions, artifact_hash,
        draw_serialization, artifact_schema_version, model_version, as_of_date,
        horizon_days, wall_clock_seconds, roster_hash, is_active, created_at,
        covariate_names, open_line_draw_semantic, input_fixture_digest, input_layer,
        input_datasheet_ref, canonical_serialization, split_seed_entropy,
        split_assignment_hash, held_out_fraction_declared, held_out_fraction_realized,
        held_out_uncensored_event_count, vendor_shrinkage, open_line_count,
        training_line_count
    )
    VALUES (
        :run_id, :code_commit, :code_worktree_dirty, :input_data_hash, :seed_entropy,
        :chain_count, :draw_count, :tuning_count, CAST(:library_versions AS jsonb),
        :artifact_hash, :draw_serialization, :artifact_schema_version, :model_version,
        :as_of_date, :horizon_days, :wall_clock_seconds, :roster_hash, :is_active,
        :created_at,
        CAST(:covariate_names AS text[]), :open_line_draw_semantic, :input_fixture_digest,
        :input_layer, :input_datasheet_ref, :canonical_serialization, :split_seed_entropy,
        :split_assignment_hash, :held_out_fraction_declared, :held_out_fraction_realized,
        :held_out_uncensored_event_count, CAST(:vendor_shrinkage AS jsonb), :open_line_count,
        :training_line_count
    )
    """
)

#: The same insert with `is_active` and `created_at` omitted, so the two declared
#: defaults apply. A separate statement rather than SQL assembled at the call
#: site: SQL built from values is what Ruff S608 exists to catch, and there is no
#: value here worth assembling.
#:
#: E007's fourteen are named here too, and that is not an oversight in a
#: statement whose subject is defaults: **none of the fourteen has one** (TR-063),
#: so omitting them would make this statement fail for a reason that has nothing
#: to do with `is_active` or `created_at`.
RUN_INSERT_LETTING_DEFAULTS_APPLY = text(
    """
    INSERT INTO forecast_run (
        run_id, code_commit, code_worktree_dirty, input_data_hash, seed_entropy,
        chain_count, draw_count, tuning_count, library_versions, artifact_hash,
        draw_serialization, artifact_schema_version, model_version, as_of_date,
        horizon_days, wall_clock_seconds, roster_hash,
        covariate_names, open_line_draw_semantic, input_fixture_digest, input_layer,
        input_datasheet_ref, canonical_serialization, split_seed_entropy,
        split_assignment_hash, held_out_fraction_declared, held_out_fraction_realized,
        held_out_uncensored_event_count, vendor_shrinkage, open_line_count,
        training_line_count
    )
    VALUES (
        :run_id, :code_commit, :code_worktree_dirty, :input_data_hash, :seed_entropy,
        :chain_count, :draw_count, :tuning_count, CAST(:library_versions AS jsonb),
        :artifact_hash, :draw_serialization, :artifact_schema_version, :model_version,
        :as_of_date, :horizon_days, :wall_clock_seconds, :roster_hash,
        CAST(:covariate_names AS text[]), :open_line_draw_semantic, :input_fixture_digest,
        :input_layer, :input_datasheet_ref, :canonical_serialization, :split_seed_entropy,
        :split_assignment_hash, :held_out_fraction_declared, :held_out_fraction_realized,
        :held_out_uncensored_event_count, CAST(:vendor_shrinkage AS jsonb), :open_line_count,
        :training_line_count
    )
    """
)

#: Both arrays go in as **array literal text**, cast server-side. A Python list
#: would be adapted to an array by the driver and would work for the ordinary
#: cases, but it cannot express `'{}'`, a NULL element, or a lower bound of 0 --
#: which are three of the rows this file exists to have refused.
POSTERIOR_INSERT = text(
    """
    INSERT INTO line_posterior (
        run_id, po_line_id, draw_count, horizon_days,
        draws, survival, residual_tail_mass, draw_digest
    )
    VALUES (
        :run_id, :po_line_id, :draw_count, :horizon_days,
        CAST(:draws AS double precision[]), CAST(:survival AS double precision[]),
        :residual_tail_mass, :draw_digest
    )
    """
)


def array_literal(values: Sequence[float | None]) -> str:
    """`values` as a PostgreSQL array literal, `None` rendering as `NULL`.

    `repr` rather than `str` or a format spec: `repr` of a Python float is the
    shortest text that round-trips to the same double, and PostgreSQL's float8
    parser is correctly rounded, so the server stores the bit pattern this
    process meant. A fixed number of digits would silently store a *different*
    number, which would make every tolerance assertion in T042 a test of the
    formatter.

    An empty `values` yields `'{}'` -- the array whose `array_length` is NULL, and
    the reason `ck_line_posterior__draws_length` carries a `coalesce`.
    """
    return "{" + ",".join("NULL" if value is None else repr(value) for value in values) + "}"


def line_row(**overrides: Any) -> dict[str, Any]:
    """A valid open `purchase_order_line` row -- the referent `line_posterior` needs.

    `fk_line_posterior__line` is `ON DELETE RESTRICT`, so a posterior row cannot
    exist without one of these. Kept self-contained rather than imported from
    `test_procurement.py`: these tests must not depend on another module's
    fixtures, and the identifier formats are frozen by TR-025 in any case.
    """
    row: dict[str, Any] = {
        "po_line_id": uuid4(),
        "project_id": "PRJ-042",
        "vendor_id": "VND-117",
        "po_number": "PO-51904",
        "line_number": 2,
        "material_category": "valves",
        "description": '4" gate valve, flanged',
        "manufacturer": "Velan",
        "part_number": "VL-4477-04",
        "quantity": 6,
        "unit_of_measure": "ea",
        "order_date": date(2026, 1, 12),
        "need_by_date": date(2026, 7, 30),
        "criticality": 3,
        "lifecycle_state": "submitted",
        "is_closed": False,
        "closing_event_id": None,
        "roster_hash": ROSTER_HASH,
    }
    row.update(overrides)
    return row


def run_row(**overrides: Any) -> dict[str, Any]:
    """A valid, **inactive** `forecast_run` row with all nine reproducibility fields.

    Inactive by default because that is what the schema defaults to: a run is
    inserted inactive and activation is a deliberate second statement (TR-027).
    Defaulting to active here would make every multi-run test in this file
    collide with `ix_forecast_run__single_active` for a reason unrelated to what
    it was asserting.

    Perturbing exactly one field of an otherwise-valid row is what makes a
    rejection attributable; break two and PostgreSQL reports whichever rule it
    evaluated first.

    **Carries E007's fourteen provenance columns (revision `0300`, G-2)** for
    the same reason it carries the delivered seventeen: the row this function
    returns has to be *valid*, or every test that perturbs one field would be
    measuring a not-null violation somewhere else on the row.
    """
    row: dict[str, Any] = {
        "run_id": uuid4(),
        "code_commit": CODE_COMMIT,
        "code_worktree_dirty": False,
        "input_data_hash": INPUT_DATA_HASH,
        "seed_entropy": SEED_ENTROPY,
        "chain_count": 4,
        "draw_count": FIXTURE_DRAW_COUNT,
        "tuning_count": 1000,
        "library_versions": LIBRARY_VERSIONS,
        "artifact_hash": ARTIFACT_HASH,
        "draw_serialization": "float64-le-c-contiguous",
        "artifact_schema_version": 1,
        "model_version": "lognormal-ar1-2026.03",
        "as_of_date": AS_OF_DATE,
        "horizon_days": FIXTURE_HORIZON_DAYS,
        "wall_clock_seconds": 412.5,
        "roster_hash": ROSTER_HASH,
        "is_active": False,
        "created_at": CREATED_AT,
        # E007's fourteen, revision 0300.
        "covariate_names": COVARIATE_NAMES,
        "open_line_draw_semantic": "conditional_remaining_duration_from_run_as_of_date",
        "input_fixture_digest": INPUT_FIXTURE_DIGEST,
        "input_layer": "SYNTHETIC",
        "input_datasheet_ref": "data/procurement/datasheet.md",
        "canonical_serialization": "canonical-json-sorted-keys-utf8",
        "split_seed_entropy": SPLIT_SEED_ENTROPY,
        "split_assignment_hash": SPLIT_ASSIGNMENT_HASH,
        "held_out_fraction_declared": HELD_OUT_FRACTION_DECLARED,
        "held_out_fraction_realized": HELD_OUT_FRACTION_REALIZED,
        "held_out_uncensored_event_count": HELD_OUT_UNCENSORED_EVENT_COUNT,
        "vendor_shrinkage": VENDOR_SHRINKAGE,
        "open_line_count": OPEN_LINE_COUNT,
        "training_line_count": TRAINING_LINE_COUNT,
    }
    row.update(overrides)
    return row


def posterior_row(
    run: Mapping[str, Any],
    po_line_id: UUID,
    **overrides: Any,
) -> dict[str, Any]:
    """A valid `line_posterior` row for `run` and `po_line_id`.

    `draw_count` and `horizon_days` are copied from the run rather than restated,
    so the default row satisfies `fk_line_posterior__run_shape` by construction
    and a test that means to break the *declared* pair has to say so explicitly.
    """
    row: dict[str, Any] = {
        "run_id": run["run_id"],
        "po_line_id": po_line_id,
        "draw_count": run["draw_count"],
        "horizon_days": run["horizon_days"],
        "draws": array_literal(FIXTURE_DRAWS),
        "survival": array_literal(FIXTURE_SURVIVAL),
        "residual_tail_mass": FIXTURE_RESIDUAL,
        "draw_digest": DRAW_DIGEST,
    }
    row.update(overrides)
    return row


def insert_line(session: Session, **overrides: Any) -> UUID:
    """Insert a purchase-order line and return its id."""
    row = line_row(**overrides)
    session.execute(LINE_INSERT, row)
    return row["po_line_id"]


def insert_run(session: Session, **overrides: Any) -> dict[str, Any]:
    """Insert a forecast run and return the row that was written."""
    row = run_row(**overrides)
    session.execute(RUN_INSERT, row)
    return row


def insert_posterior(session: Session, row: Mapping[str, Any]) -> None:
    """Insert one artifact row."""
    session.execute(POSTERIOR_INSERT, dict(row))


def assert_not_null_violation(
    session: Session,
    statement: TextClause,
    row: Mapping[str, Any],
    column: str,
) -> None:
    """Assert `row` is refused as a `NOT NULL` violation naming `column`.

    Deliberately **not** routed through `conftest.assert_rejects`, for a reason
    that is a property of PostgreSQL 16 rather than a preference: a `NOT NULL`
    violation reports `column_name` and carries **no `constraint_name` at all**,
    because catalogued, nameable `NOT NULL` constraints only arrive in 17.
    Forcing this through a helper that requires a constraint name would prove the
    helper's error path and nothing about the schema.

    Asserting the column is not the weaker claim. It is what distinguishes this
    rejection from a null in any *other* required column of the same row -- and
    with nineteen columns on `forecast_run`, seventeen of them `NOT NULL`, that
    distinction is the whole test.
    """
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as rejection:
        session.execute(statement, dict(row))
    if savepoint.is_active:
        savepoint.rollback()

    original = rejection.value.orig
    assert isinstance(original, psycopg.errors.NotNullViolation), (
        f"a row with no {column} must be refused as a NOT NULL violation "
        f"(SQLSTATE 23502); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.diag.column_name == column, (
        f"the rejection must name {column}, or some other required column was null and this "
        f"test never reached the rule it claims to cover; got "
        f"{original.diag.column_name!r} on {original.diag.table_name!r}"
    )


@contextmanager
def schema_temporarily_altered(session: Session, *statements: TextClause) -> Iterator[None]:
    """Run `statements` as DDL, yield, then undo them by rolling back a savepoint.

    Sound because DDL is transactional in PostgreSQL: a dropped constraint is
    back the moment the savepoint is rolled back, and `conftest.db_session`
    discards the outer transaction regardless. Nothing here can leak into another
    test or into the database the next `alembic upgrade` sees.

    Used for exactly two things, both of which are otherwise unprovable:

    * **Attributing a rejection when two constraints are false on the same row.**
      A NULL element in `survival` violates both `__survival_monotone` and
      `__survival_unit_interval`; which one PostgreSQL reports is an
      implementation detail, so each is isolated by dropping the other.
    * **Showing that the delivered residual check would fail as an equality.**
      Reading `abs(...) <= 1e-9` out of the catalogue shows what is written;
      replacing it with `=` and watching an accepted row get refused shows that
      the difference matters (SC-015).
    """
    savepoint = session.begin_nested()
    for statement in statements:
        session.execute(statement)
    try:
        yield
    finally:
        if savepoint.is_active:
            savepoint.rollback()


# --------------------------------------------------------------------------- #
# Read-back and catalogue probes
# --------------------------------------------------------------------------- #

ACTIVE_VIEW_ROWS = text("SELECT * FROM v_active_forecast_run")
ACTIVE_VIEW_RUN_IDS = text("SELECT run_id FROM v_active_forecast_run")
ACTIVATE_RUN = text("UPDATE forecast_run SET is_active = true WHERE run_id = :run_id")
DEACTIVATE_RUN = text("UPDATE forecast_run SET is_active = false WHERE run_id = :run_id")

REPRODUCIBILITY_READBACK = text(
    """
    SELECT run_id, code_commit, input_data_hash, seed_entropy, library_versions,
           artifact_hash, artifact_schema_version, model_version, created_at
    FROM forecast_run
    WHERE run_id = :run_id
    """
)

DEFAULTED_COLUMNS = text("SELECT is_active, created_at FROM forecast_run WHERE run_id = :run_id")

CONSTRAINT_DEFINITION = text(
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :conname"
)

VIEW_DEFINITIONS = text("SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'")

ARRAY_COLUMNS_IN_SCHEMA = text(
    """
    SELECT table_name, column_name, udt_name, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public' AND column_name IN ('draws', 'survival')
    ORDER BY table_name, column_name
    """
)

DIGEST_COLUMN_TYPES = text(
    """
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND (table_name, column_name) IN (
          ('forecast_run', 'artifact_hash'),
          ('line_posterior', 'draw_digest')
      )
    ORDER BY table_name
    """
)

#: The calendar day an array offset denotes, resolved per line through the run
#: that line's artifact belongs to. Joined against `v_active_forecast_run` and not
#: `forecast_run`, because OBJ5 VC9's second half is stated of an *active* run and
#: the view is the only supported way to find one (TR-027) -- so the query is the
#: read path a consumer of the artifact would actually take.
#:
#: `as_of_date` and the offset are both selected: the sum alone would agree
#: trivially if the anchor were somehow null, since `NULL + 1` is NULL for both
#: lines.
SURVIVAL_OFFSET_CALENDAR_DAY = text(
    """
    SELECT p.po_line_id,
           r.as_of_date AS anchor,
           r.as_of_date + CAST(:day_offset AS integer) AS calendar_day,
           array_length(p.survival, 1) AS survival_length
    FROM line_posterior AS p
    JOIN v_active_forecast_run AS r ON r.run_id = p.run_id
    ORDER BY p.po_line_id
    """
)

#: Every date- or time-typed column on `line_posterior`. Must come back empty:
#: a per-line temporal column is precisely how two lines in one run would come to
#: disagree about which day offset k is, and TR-049 puts the anchor on the run.
LINE_POSTERIOR_TEMPORAL_COLUMNS = text(
    """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'line_posterior'
      AND data_type IN (
          'date',
          'timestamp with time zone',
          'timestamp without time zone',
          'time with time zone',
          'time without time zone'
      )
    ORDER BY column_name
    """
)

#: The two facts about `'{}'` that make the naive length check wrong, read from
#: the server rather than asserted from memory.
EMPTY_ARRAY_FACTS = text(
    """
    SELECT array_length('{}'::double precision[], 1) IS NULL AS length_is_null,
           (array_length('{}'::double precision[], 1) = :draw_count) IS NULL
               AS naive_check_is_null
    """
)

#: The three facts that make a lower-bound-0 array dangerous: it is
#: one-dimensional, it is of the declared length, and the subscript the residual
#: check reads is out of range and therefore NULL -- which a `CHECK` accepts.
LOWER_BOUND_ZERO_FACTS = text(
    """
    SELECT array_ndims(subject.values) AS ndims,
           array_length(subject.values, 1) AS length,
           array_lower(subject.values, 1) AS lower_bound,
           subject.values[CAST(:subscript AS integer)] IS NULL AS tail_is_null
    FROM (SELECT CAST(:literal AS double precision[]) AS values) AS subject
    """
)

HELPERS_ON_A_NULL_ELEMENT = text(
    """
    SELECT fn_is_sorted_ascending(CAST(:draws AS double precision[])) AS sorted,
           fn_is_non_increasing(CAST(:survival AS double precision[])) AS non_increasing,
           fn_all_within_unit_interval(CAST(:survival AS double precision[])) AS within_unit
    """
)

RESIDUAL_COMPARISON = text(
    """
    SELECT survival[horizon_days] = residual_tail_mass AS exactly_equal,
           abs(survival[horizon_days] - residual_tail_mass) <= 1e-9 AS within_tolerance
    FROM line_posterior
    WHERE run_id = :run_id AND po_line_id = :po_line_id
    """
)

SURVIVAL_DERIVED_FROM_DRAWS = text(
    """
    SELECT grid.k AS k,
           artifact.survival[grid.k] AS stored,
           (
               SELECT count(*)
               FROM unnest(artifact.draws) AS d (value)
               WHERE d.value > grid.k
           )::double precision / artifact.draw_count AS derived
    FROM line_posterior AS artifact,
         generate_series(1, artifact.horizon_days) AS grid (k)
    WHERE artifact.run_id = :run_id AND artifact.po_line_id = :po_line_id
    ORDER BY grid.k
    """
)

RESIDUAL_DERIVED_FROM_DRAWS = text(
    """
    SELECT artifact.residual_tail_mass AS stored,
           (
               SELECT count(*)
               FROM unnest(artifact.draws) AS d (value)
               WHERE d.value > artifact.horizon_days
           )::double precision / artifact.draw_count AS derived
    FROM line_posterior AS artifact
    WHERE artifact.run_id = :run_id AND artifact.po_line_id = :po_line_id
    """
)

#: The percentile convention, as a subscript: `draws[ceil(p * draw_count)]`,
#: one-based, no interpolation (TR-033, TR-068).
PERCENTILE_BY_SUBSCRIPT = text(
    """
    SELECT draws[ceil(:p * draw_count)::integer]
    FROM line_posterior
    WHERE run_id = :run_id AND po_line_id = :po_line_id
    """
)

DRAWS_AS_TEXT = text(
    """
    SELECT draws::text FROM line_posterior WHERE run_id = :run_id AND po_line_id = :po_line_id
    """
)

DIGEST_BYTES = text(
    """
    SELECT draw_digest FROM line_posterior WHERE run_id = :run_id AND po_line_id = :po_line_id
    """
)

#: `SET LOCAL`, never a bare `SET`: the connection is pooled by
#: `conftest.engine`, so a session setting left behind would follow the socket
#: into the next test. `SET LOCAL` is unwound with the transaction.
SET_SHORT_FLOAT_RENDERING = text("SET LOCAL extra_float_digits = -3")
SET_ROUND_TRIP_FLOAT_RENDERING = text("SET LOCAL extra_float_digits = 1")

DROP_SURVIVAL_MONOTONE = text(
    "ALTER TABLE line_posterior DROP CONSTRAINT ck_line_posterior__survival_monotone"
)
DROP_SURVIVAL_UNIT_INTERVAL = text(
    "ALTER TABLE line_posterior DROP CONSTRAINT ck_line_posterior__survival_unit_interval"
)
DROP_RESIDUAL_TOLERANCE_CHECK = text(
    "ALTER TABLE line_posterior DROP CONSTRAINT ck_line_posterior__residual_matches_grid_tail"
)

#: The check `data-model.md` forbids, added under its own name so the rejection it
#: causes cannot be mistaken for one of the delivered rules.
ADD_RESIDUAL_EQUALITY_CHECK = text(
    """
    ALTER TABLE line_posterior
    ADD CONSTRAINT ck_line_posterior__residual_exact_equality
    CHECK (survival[horizon_days] = residual_tail_mass)
    """
)


# --------------------------------------------------------------------------- #
# T040 -- reproducibility fields and the active pointer (TR-026, TR-027)
# --------------------------------------------------------------------------- #


def test_a_run_records_all_nine_reproducibility_fields(db_session: Session) -> None:
    """TR-026: a run row carries every field needed to reproduce it, and stores them.

    The read-back matters as much as the insert. A migration that silently
    truncated `code_commit` to `char(40)`'s padding, or stored the artifact digest
    as text, would satisfy an insert-only test while making the run
    irreproducible -- which is the failure Principle I exists to prevent.
    """
    run = insert_run(db_session)

    stored = db_session.execute(REPRODUCIBILITY_READBACK, {"run_id": run["run_id"]}).one()

    assert stored.run_id == run["run_id"]
    assert stored.code_commit == CODE_COMMIT, "char(40) blank-padding must not alter the commit"
    assert stored.input_data_hash == INPUT_DATA_HASH
    assert stored.seed_entropy == SEED_ENTROPY, "the 128-bit entropy is recorded verbatim (TR-063)"
    assert stored.library_versions["pymc"] == "6.2.0"
    assert stored.artifact_hash == ARTIFACT_HASH, "the digest is 32 raw bytes, not a rendering"
    assert stored.artifact_schema_version == 1
    assert stored.model_version == "lognormal-ar1-2026.03"
    assert stored.created_at == CREATED_AT


def test_the_two_declared_defaults_apply_when_their_columns_are_omitted(
    db_session: Session,
) -> None:
    """`is_active` defaults false and `created_at` defaults `now()` (TR-027, TR-063).

    Asserted for its own sake -- a run must be inserted *inactive* so activation
    is a deliberate second statement -- and as the premise of the test below it:
    because `created_at` has a `DEFAULT`, its `NOT NULL` can only be exercised by
    passing an **explicit NULL**. Omitting the column proves the default works,
    not that the constraint does.
    """
    row = run_row()
    del row["is_active"]
    del row["created_at"]
    db_session.execute(RUN_INSERT_LETTING_DEFAULTS_APPLY, row)

    defaulted = db_session.execute(DEFAULTED_COLUMNS, {"run_id": row["run_id"]}).one()

    assert defaulted.is_active is False, "a run is inserted inactive; activation is deliberate"
    assert defaulted.created_at is not None, "DEFAULT now() must fill an omitted created_at"


@pytest.mark.parametrize("column", REPRODUCIBILITY_FIELDS)
def test_each_reproducibility_field_is_rejected_when_null(
    db_session: Session,
    column: str,
) -> None:
    """TR-026: all nine reproducibility fields are `NOT NULL`, one test per field.

    Parametrised rather than looped so a regression names the field it broke.
    Each case passes an explicit NULL for exactly one column of an otherwise
    valid row -- which is also what defeats `created_at`'s `DEFAULT now()`; the
    test above proves that default is really there, so this one is not quietly
    asserting against an absent column.

    A reproducibility field that can be absent is a reproducibility field that
    will be absent on the one run anybody needs to reproduce.
    """
    assert_not_null_violation(db_session, RUN_INSERT, run_row(**{column: None}), column)


def test_a_run_with_no_as_of_anchor_date_is_rejected(db_session: Session) -> None:
    """TR-049, OBJ5 VC9 (first half), SC-022: `as_of_date` cannot be absent.

    Its own test rather than a tenth entry in `REPRODUCIBILITY_FIELDS`, for the
    reason recorded on `ANCHOR_DATE_FIELD`: this column is not one of TR-026's
    nine, and a failure here means something different from a failure there. A
    missing reproducibility field makes a run unreproducible; a missing anchor
    makes every *survival array in the run* unreadable, because `survival[k]` is
    defined only relative to `as_of_date` and there is nowhere else to recover it
    from -- the array carries offsets, not dates.

    That is also why it must be `NOT NULL` rather than merely conventional. A run
    with a null anchor would still satisfy every array-shape check in this file
    and would still be selectable as the active run; the curves would simply
    denote nothing, and no read path could detect it.
    """
    assert_not_null_violation(
        db_session,
        RUN_INSERT,
        run_row(**{ANCHOR_DATE_FIELD: None}),
        ANCHOR_DATE_FIELD,
    )


def test_one_array_offset_denotes_the_same_calendar_day_for_every_line_in_a_run(
    db_session: Session,
) -> None:
    """TR-049, OBJ5 VC9 (second half), SC-022: the anchor is per run, not per line.

    Two lines, two artifact rows, one active run. Offset
    `COMPARED_DAY_OFFSET` is resolved for each line by the read path a consumer
    takes -- join the artifact to `v_active_forecast_run` and add the offset to
    that run's anchor -- and both must land on the same calendar day.

    **Why this is not circular.** Resolving both offsets through one run row
    obviously yields one date; the substance is that there is no *other* way to
    resolve them. So the test asserts the structural fact alongside the
    arithmetic one: `line_posterior` carries no date or timestamp column at all,
    which is what makes the run's anchor the only anchor in existence and two
    lines' grids incapable of disagreeing. Were a per-line `as_of_date` added
    later, the arithmetic assertion would keep passing while the guarantee
    quietly became a convention the schema no longer enforced -- so the
    catalogue check is the half that would catch it.

    The comparison is also made against `AS_OF_DATE + COMPARED_DAY_OFFSET`
    computed in Python, not only line-against-line. Two lines agreeing on the
    wrong day is still agreement, and a schema that dropped the offset would
    produce it.

    Both arrays are asserted to be the same length as well. "The same offset" is
    only a meaningful phrase where the offset is in range for both lines, which
    `fk_line_posterior__run_shape` guarantees through the run's `horizon_days` --
    this is the observable consequence of that constraint.
    """
    run = insert_run(db_session, is_active=True)
    first_line = insert_line(db_session, po_number="PO-51904", line_number=1)
    second_line = insert_line(db_session, po_number="PO-77310", line_number=4)
    insert_posterior(db_session, posterior_row(run, first_line))
    insert_posterior(db_session, posterior_row(run, second_line))

    resolved = (
        db_session.execute(SURVIVAL_OFFSET_CALENDAR_DAY, {"day_offset": COMPARED_DAY_OFFSET})
        .mappings()
        .all()
    )

    assert len(resolved) == 2, (
        f"expected the two artifact rows just written to resolve through the active run; "
        f"got {len(resolved)}. If this is 0 the join found no active run and nothing below "
        f"is being compared."
    )
    assert {row["po_line_id"] for row in resolved} == {first_line, second_line}

    expected_day = AS_OF_DATE + timedelta(days=COMPARED_DAY_OFFSET)
    for row in resolved:
        assert row["anchor"] == AS_OF_DATE, (
            f"line {row['po_line_id']} resolved to anchor {row['anchor']}, not the run's "
            f"{AS_OF_DATE}. The anchor a line's grid is read against must be its run's own."
        )
        assert row["calendar_day"] == expected_day, (
            f"offset {COMPARED_DAY_OFFSET} on line {row['po_line_id']} denotes "
            f"{row['calendar_day']}, not {expected_day}. `survival[k]` is defined as "
            f"P(not delivered by end of day as_of_date + k), so a line whose offsets "
            f"resolve elsewhere is being read against the wrong days."
        )
        assert row["survival_length"] == FIXTURE_HORIZON_DAYS, (
            f"line {row['po_line_id']} has a survival array of "
            f"{row['survival_length']} elements against the run's "
            f"{FIXTURE_HORIZON_DAYS}-day horizon, so offset "
            f"{COMPARED_DAY_OFFSET} is not the same position in both grids."
        )

    first_day, second_day = (row["calendar_day"] for row in resolved)

    assert first_day == second_day, (
        f"the two lines resolve offset {COMPARED_DAY_OFFSET} to {first_day} and "
        f"{second_day}. One offset must denote one calendar day across a whole run "
        f"(TR-049, SC-022) -- otherwise a worklist comparing two lines' probability of "
        f"lateness at the same index is comparing different days."
    )

    temporal = db_session.execute(LINE_POSTERIOR_TEMPORAL_COLUMNS).fetchall()

    assert temporal == [], (
        f"`line_posterior` carries temporal columns "
        f"{[(row.column_name, row.data_type) for row in temporal]}. TR-049 puts the anchor "
        f"on the run so that one offset means one day for every line in it; a per-line date "
        f"reintroduces exactly the disagreement that design removes, and the arithmetic "
        f"above would go on passing while it did."
    )


def test_a_second_active_run_is_rejected_on_insert(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-027, SC-013: at most one active run, enforced on the way in.

    A partial unique index on a boolean column: the index holds only the rows
    where `is_active` is true, and being unique on a column that is the constant
    `true` for every row it contains, it can hold at most one. A plain
    `UNIQUE (is_active)` would also permit only one *inactive* run, which would
    break the ordinary case of many superseded runs -- asserted separately below.
    """
    insert_run(db_session, is_active=True)

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, SINGLE_ACTIVE_INDEX):
        insert_run(db_session, is_active=True)


def test_activating_a_second_run_by_update_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-027: the same rule on the `UPDATE` path, which is the path that is used.

    Runs are inserted inactive and promoted later, so activation in practice is
    an `UPDATE` and not an `INSERT`. An index enforces both, but a rule carried by
    a trigger or by application code could easily cover one and miss the other,
    and "the second insert is refused" would read as full coverage while
    promotion swapped the live run silently.
    """
    insert_run(db_session, is_active=True)
    superseded = insert_run(db_session, is_active=False)

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, SINGLE_ACTIVE_INDEX):
        db_session.execute(ACTIVATE_RUN, {"run_id": superseded["run_id"]})


def test_many_inactive_runs_coexist(db_session: Session) -> None:
    """TR-027: the partial index constrains only the active row.

    Three inactive runs and one active one is the ordinary state of a project
    that has refitted twice. Without this, `test_a_second_active_run_is_rejected_on_insert`
    would pass just as well against a plain `UNIQUE (is_active)` that had broken
    superseded runs entirely.
    """
    insert_run(db_session, is_active=False)
    insert_run(db_session, is_active=False)
    insert_run(db_session, is_active=False)
    active = insert_run(db_session, is_active=True)

    visible = db_session.execute(ACTIVE_VIEW_RUN_IDS).scalars().all()

    assert visible == [active["run_id"]], "three superseded runs must not collide with each other"


def test_the_active_view_returns_the_one_active_run(db_session: Session) -> None:
    """TR-027, OBJ5 VC3: with a run active, the view names that run and no other.

    The positive half of the requirement, and the reason the zero-rows test below
    is not vacuous: a view that returned nothing under every condition would
    satisfy "zero rows when nothing is active" perfectly.
    """
    insert_run(db_session, is_active=False)
    active = insert_run(db_session, is_active=True)

    rows = db_session.execute(ACTIVE_VIEW_ROWS).fetchall()

    assert len(rows) == 1
    assert rows[0].run_id == active["run_id"]
    assert rows[0].is_active is True


def test_the_active_view_returns_zero_rows_when_no_run_is_active(db_session: Session) -> None:
    """TR-027, OBJ5 VC3: **no active run means no row** -- never the newest run.

    Two runs exist, both superseded, so emptiness is a statement about
    `is_active` and not about an empty table. "No current forecast" and "current
    forecast, possibly stale" are different answers and a consumer must be able
    to tell them apart; a recency fallback would serve a superseded run as
    current and there would be nothing in the response to say so.

    The second half deactivates a genuinely active run and shows the view empties
    again, which is the transition an operator actually performs when retiring a
    forecast.
    """
    insert_run(db_session, is_active=False)
    insert_run(db_session, is_active=False)

    assert db_session.execute(ACTIVE_VIEW_ROWS).fetchall() == [], (
        "with no active run the view must return zero rows, not the most recent run"
    )

    active = insert_run(db_session, is_active=True)
    assert len(db_session.execute(ACTIVE_VIEW_ROWS).fetchall()) == 1

    db_session.execute(DEACTIVATE_RUN, {"run_id": active["run_id"]})
    assert db_session.execute(ACTIVE_VIEW_ROWS).fetchall() == [], (
        "deactivating the last active run must empty the view, not fall back to it"
    )


def test_the_revision_contains_no_recency_fallback() -> None:
    """TR-027: no `ORDER BY created_at DESC LIMIT 1` in any SQL `0008` executes.

    A behavioural test cannot reach this. The view is empty when nothing is
    active *today*, and it would also be empty today if a fallback existed but
    the table happened to be empty -- so the absence of the fallback has to be
    read off the revision itself.

    Read off the **executed SQL**, not the file text, and that distinction is the
    whole test: `0008`'s comments discuss the forbidden pattern by name, twice, so
    a `grep` over the source would match its own documentation and this test would
    fail against a correct schema. The SQL is recovered by parsing the module and
    taking the string arguments of every `execute(...)` call, then stripping SQL
    line comments; the three helper functions arrive as `Name` arguments from
    `model.schema.helpers`, so their DDL is added from the same constants the
    migration passes.

    Two guards keep the assertion from passing vacuously: the extracted SQL must
    contain the view's own `CREATE VIEW`, and it must contain the
    `(created_at DESC)` index -- so the extractor is proven to see both the object
    under test and the one construct that could be mistaken for a fallback.
    """
    revision = Path(helpers.__file__).parent / "versions" / "0008_forecast.py"
    executed_sql = _executed_sql_of(revision)

    assert "create view v_active_forecast_run" in executed_sql, (
        "the SQL extractor found no CREATE VIEW, so the absence assertions below "
        "would be about an empty string"
    )
    assert "created_at desc" in executed_sql, (
        "the SQL extractor found no (created_at DESC) index, so it is not seeing "
        "the construct a fallback would hide behind"
    )
    assert re.search(r"order\s+by\s+created_at\s+desc", executed_sql) is None, (
        "an ORDER BY created_at DESC in this revision would make 'no current forecast' "
        "indistinguishable from 'stale forecast' (OBJ5 VC3)"
    )
    assert re.search(r"\blimit\b", executed_sql) is None, (
        "the active-run view deliberately has no LIMIT: a LIMIT would hide a second "
        "active row rather than ix_forecast_run__single_active preventing it"
    )


def test_no_view_in_the_schema_selects_the_newest_run_as_a_fallback(
    db_session: Session,
) -> None:
    """TR-027: the same claim against the live catalogue, over every view.

    Stronger than the source scan in one way and weaker in another, so both are
    kept. Stronger: it reads what PostgreSQL actually stored, across every view in
    the schema rather than one revision's text. Weaker: it can only look for the
    *pattern*, so it is expressed as the conjunction that would have to hold --
    a recency fallback needs both a `created_at` ordering and a `LIMIT`.

    `v_purchase_order_line_current_state` does carry a `LIMIT`, legitimately: it
    picks a line's highest `sequence_no` event, which is position and not
    recency. That is why the assertion is the conjunction and not "no view has a
    LIMIT".
    """
    views = db_session.execute(VIEW_DEFINITIONS).fetchall()

    assert views, "the catalogue query returned no views at all, so this asserts nothing"
    for view in views:
        definition = view.definition.lower()
        recency_ordered = "created_at" in definition
        truncated = re.search(r"\blimit\b", definition) is not None
        assert not (recency_ordered and truncated), (
            f"{view.viewname} orders on created_at and truncates, which is the "
            f"most-recent-run fallback the schema forbids"
        )

    active_view = next(view for view in views if view.viewname == "v_active_forecast_run")
    definition = active_view.definition.lower()
    assert "where is_active" in definition
    assert "order by" not in definition
    assert re.search(r"\blimit\b", definition) is None


def _executed_sql_of(revision: Path) -> str:
    """The SQL a revision module executes, lower-cased and stripped of comments.

    Collects the string arguments of every `execute(...)` call in the module, adds
    the three helper-function definitions the migration passes by name, and
    removes `--` line comments from the result. Comments are removed because the
    SQL is documented in place and a search for a forbidden construct would
    otherwise match the sentence explaining that it is forbidden.
    """
    module = ast.parse(revision.read_text(encoding="utf-8"))
    fragments = [
        argument.value
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]
    fragments.extend(
        [
            helpers.FN_IS_SORTED_ASCENDING,
            helpers.FN_IS_NON_INCREASING,
            helpers.FN_ALL_WITHIN_UNIT_INTERVAL,
        ]
    )
    return re.sub(r"--[^\n]*", "", "\n".join(fragments)).lower()


# --------------------------------------------------------------------------- #
# T041 -- array shape (TR-028, TR-069, TR-070, TR-072, TR-073)
# --------------------------------------------------------------------------- #


def test_a_well_formed_posterior_row_is_accepted(db_session: Session) -> None:
    """The baseline every rejection below is a one-field perturbation of.

    Without it, a fixture broken in some unrelated way would make every test in
    this group pass for the wrong reason: each would see *a* rejection, and
    `assert_rejects` would only catch it if the constraint name happened to
    differ.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    insert_posterior(db_session, posterior_row(run, po_line_id))

    stored = db_session.execute(
        RESIDUAL_COMPARISON, {"run_id": run["run_id"], "po_line_id": po_line_id}
    ).one()
    assert stored.within_tolerance is True


def test_unsorted_draws_are_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-028, TR-070: the draw array is stored sorted, or not stored.

    Sortedness is what makes a percentile a subscript. `draws[ceil(p * n)]`
    against an unsorted array returns a real number from the right distribution
    at the wrong rank -- a plausible wrong answer with nothing to distinguish it
    from a right one, which is exactly the silent failure Principle III says to
    convert into a visible one. Here it is a write failure.

    Enforced through an `IMMUTABLE` helper because a `CHECK` admits no subquery,
    so element-wise array validation needs a callable.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    unsorted = array_literal((2.0, 1.0, 2.0, 4.0, 9.0))

    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAWS_SORTED):
        insert_posterior(db_session, posterior_row(run, po_line_id, draws=unsorted))


def test_a_draw_array_disagreeing_with_the_declared_count_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-069, invariant 18: the array is as long as the count on its own row.

    This is the `CHECK` half of the length chain. The row's `draw_count` is the
    run's own value -- `fk_line_posterior__run_shape` has already proven that --
    so comparing the array against it is not a comparison against a number the
    writer chose.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    one_too_many = array_literal((*FIXTURE_DRAWS, 11.0))

    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAWS_LENGTH):
        insert_posterior(db_session, posterior_row(run, po_line_id, draws=one_too_many))


def test_a_declared_draw_count_that_is_not_the_runs_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-073, invariant 18: a writer cannot choose its own `draw_count`.

    **A different mechanism from the test above it, and the distinction is the
    point.** Here the array agrees with the *declared* count perfectly -- six
    draws, `draw_count = 6` -- so `ck_line_posterior__draws_length` is satisfied
    and reports nothing. What fails is `fk_line_posterior__run_shape`, because
    `(run_id, 6, horizon_days)` is not a row of `uq_forecast_run__shape`.

    Without this, a writer could declare any count it liked and pad its array to
    match, and every length check in the schema would agree with it.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    row = posterior_row(
        run,
        po_line_id,
        draw_count=FIXTURE_DRAW_COUNT + 1,
        draws=array_literal((*FIXTURE_DRAWS, 11.0)),
    )

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, RUN_SHAPE_FOREIGN_KEY):
        insert_posterior(db_session, row)


def test_a_survival_array_disagreeing_with_the_declared_horizon_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-072, invariant 19: the day grid is exactly as long as the run's horizon.

    A short grid is the dangerous direction: `survival[k]` for a `k` past the end
    is NULL, and every derived probability computed from it silently becomes NULL
    rather than wrong-and-visible.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    one_too_few = array_literal(FIXTURE_SURVIVAL[:-1])

    with assert_rejects(db_session, psycopg.errors.CheckViolation, SURVIVAL_LENGTH):
        insert_posterior(db_session, posterior_row(run, po_line_id, survival=one_too_few))


def test_a_declared_horizon_that_is_not_the_runs_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-073, invariant 19: the horizon half of the same referential proof.

    As with `draw_count`, the array and the declaration agree with each other and
    disagree with the run, so the `CHECK` passes and the composite foreign key is
    what refuses the row. The residual is set to the new tail so that
    `ck_line_posterior__residual_matches_grid_tail` is satisfied too -- otherwise
    two constraints would be false and PostgreSQL's choice between them would
    decide whether this test passed.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    longer_grid = (0.8, 0.4, 0.4, 0.2)
    row = posterior_row(
        run,
        po_line_id,
        horizon_days=FIXTURE_HORIZON_DAYS + 1,
        survival=array_literal(longer_grid),
        residual_tail_mass=longer_grid[-1],
    )

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, RUN_SHAPE_FOREIGN_KEY):
        insert_posterior(db_session, row)


def test_an_empty_draw_array_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-069: `'{}'` is refused -- the case the declared check would have accepted.

    **The recorded deviation from `data-model.md`, verified rather than argued.**
    `array_length('{}', 1)` is NULL, not 0: an empty array has no dimensions, so
    there is no length to report. The declared
    `array_length(draws, 1) = draw_count` therefore evaluates to NULL, and a
    `CHECK` rejects only on *false* -- so as written it **accepts an artifact row
    with no draws at all**, passing every other constraint on the table. The
    delivered `coalesce(array_length(draws, 1), 0)` makes the comparison
    definite, and `ck_forecast_run__draw_count_positive` guarantees the target is
    positive, so the substituted 0 can never match it.

    Both halves are asserted: the server confirms the naive expression really is
    NULL on this input, and the row really is refused.
    """
    facts = db_session.execute(EMPTY_ARRAY_FACTS, {"draw_count": FIXTURE_DRAW_COUNT}).one()
    assert facts.length_is_null is True, "array_length('{}', 1) must be NULL, not 0"
    assert facts.naive_check_is_null is True, (
        "the declared bare array_length comparison must evaluate to NULL on '{}', "
        "which is what a CHECK accepts -- if this is false the deviation is moot"
    )

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAWS_LENGTH):
        insert_posterior(db_session, posterior_row(run, po_line_id, draws=array_literal(())))


def test_an_empty_survival_array_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-072: the same `coalesce` deviation on the survival grid.

    Exactly one constraint is false on this row, deliberately.
    `array_ndims('{}')` is also NULL, so `ck_line_posterior__survival_1d` accepts
    the empty array and the empty case is owned by the length check alone -- which
    is what makes the server's report deterministic and this assertion able to
    name one constraint.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, SURVIVAL_LENGTH):
        insert_posterior(db_session, posterior_row(run, po_line_id, survival=array_literal(())))


def test_a_lower_bound_zero_draw_array_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-069, TR-033: subscripts must start at 1, because the read conventions do.

    **The second recorded deviation from `data-model.md`, verified.**
    `'[0:4]={...}'` is a legal one-dimensional `double precision[]` of length 5
    whose last element sits at subscript 4. Every declared length and dimension
    check passes on it. But the percentile convention is
    `draws[ceil(p * draw_count)]` -- one-based, a direct subscript -- so on this
    array the top rank is out of reach and `draws[draw_count]` is NULL. The
    delivered `array_lower(draws, 1) = 1` conjunct refuses it.

    The server is asked to confirm the premise first: one dimension, the declared
    length, lower bound 0, and the top subscript NULL.
    """
    facts = db_session.execute(
        LOWER_BOUND_ZERO_FACTS,
        {"literal": LOWER_BOUND_ZERO_DRAWS, "subscript": FIXTURE_DRAW_COUNT},
    ).one()
    assert facts.ndims == 1, "a lower-bound-0 array is still one-dimensional"
    assert facts.length == FIXTURE_DRAW_COUNT, "and still of the declared length"
    assert facts.lower_bound == 0
    assert facts.tail_is_null is True, (
        "draws[draw_count] must be NULL on this array -- that is the silent wrong "
        "answer the array_lower conjunct exists to prevent"
    )

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAWS_1D):
        insert_posterior(db_session, posterior_row(run, po_line_id, draws=LOWER_BOUND_ZERO_DRAWS))


def test_a_lower_bound_zero_survival_array_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-072, TR-030: and here the lower bound is load-bearing twice over.

    `ck_line_posterior__residual_matches_grid_tail` reads
    `survival[horizon_days]` directly. On a lower-bound-0 grid of length
    `horizon_days` that subscript is past the end, so the operand is NULL, so the
    whole residual comparison is NULL -- and a `CHECK` **accepts** NULL. A grid
    that no reader can index would therefore have satisfied the one constraint
    whose job is to prove the distribution adds up.

    The premise is confirmed against the server before the rejection is asserted,
    because the argument depends entirely on that subscript being NULL.
    """
    facts = db_session.execute(
        LOWER_BOUND_ZERO_FACTS,
        {"literal": LOWER_BOUND_ZERO_SURVIVAL, "subscript": FIXTURE_HORIZON_DAYS},
    ).one()
    assert facts.ndims == 1
    assert facts.length == FIXTURE_HORIZON_DAYS
    assert facts.lower_bound == 0
    assert facts.tail_is_null is True, (
        "survival[horizon_days] must be NULL here, which is what would make the "
        "residual check NULL and therefore satisfied"
    )

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, SURVIVAL_1D):
        insert_posterior(
            db_session, posterior_row(run, po_line_id, survival=LOWER_BOUND_ZERO_SURVIVAL)
        )


def test_a_null_element_in_the_draw_array_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-070: a hole in the draws is refused by the sortedness helper.

    `STRICT` covers a null *array* and nothing else. `'{1.0, NULL, 3.0}'` under a
    bare adjacent-pair comparison yields NULL on both pairs touching the null,
    `NOT EXISTS` swallows that, and the `CHECK` accepts an array with a hole in
    it. `fn_is_sorted_ascending` tests each element for null explicitly, which is
    also what lets `ck_line_posterior__draws_non_negative` stay the plain
    `draws[1] >= 0.0` that `data-model.md` declares -- a null first element is
    refused here instead, including in the single-element case no pair comparison
    can reach.
    """
    holed = array_literal((1.0, None, 2.0, 4.0, 9.0))
    helper_values = db_session.execute(
        HELPERS_ON_A_NULL_ELEMENT, {"draws": holed, "survival": array_literal(())}
    ).one()
    assert helper_values.sorted is False, (
        "fn_is_sorted_ascending must return false, not NULL, for a null element"
    )

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAWS_SORTED):
        insert_posterior(db_session, posterior_row(run, po_line_id, draws=holed))


def test_a_null_element_in_the_survival_array_is_rejected_by_both_shape_checks(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-029: a hole in the survival grid violates monotonicity *and* the unit interval.

    **Two constraints are false on this row, so neither can be named while both
    are present.** PostgreSQL reports whichever it evaluated first, and that
    order is an implementation detail no test should encode. So each is isolated
    by dropping the other inside a savepoint -- proving that both genuinely
    refuse the row, rather than that one of them happens to be reported today.

    Both helpers are written to return `false` and not NULL for a null element:
    `fn_is_non_increasing` tests for null explicitly, and
    `fn_all_within_unit_interval` is spelled `(...) IS NOT TRUE` rather than
    `NOT (...)` precisely so NULL collapses to a violation. That is what makes
    every element of a stored `survival` a definite number, which is in turn what
    lets the residual check below be the plain comparison it is.
    """
    holed = array_literal((0.8, None, 0.4))
    helper_values = db_session.execute(
        HELPERS_ON_A_NULL_ELEMENT, {"draws": array_literal(()), "survival": holed}
    ).one()
    assert helper_values.non_increasing is False
    assert helper_values.within_unit is False

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    row = posterior_row(run, po_line_id, survival=holed)

    with (
        schema_temporarily_altered(db_session, DROP_SURVIVAL_UNIT_INTERVAL),
        assert_rejects(db_session, psycopg.errors.CheckViolation, SURVIVAL_MONOTONE),
    ):
        insert_posterior(db_session, row)

    with (
        schema_temporarily_altered(db_session, DROP_SURVIVAL_MONOTONE),
        assert_rejects(db_session, psycopg.errors.CheckViolation, SURVIVAL_UNIT_INTERVAL),
    ):
        insert_posterior(db_session, row)

    # And with the schema back as delivered, the row is still refused -- by one of
    # the two, which is all that can be asserted without isolating them.
    savepoint = db_session.begin_nested()
    with pytest.raises(DBAPIError) as rejection:
        insert_posterior(db_session, row)
    if savepoint.is_active:
        savepoint.rollback()
    assert rejection.value.orig.diag.constraint_name in {
        SURVIVAL_MONOTONE,
        SURVIVAL_UNIT_INTERVAL,
    }


# --------------------------------------------------------------------------- #
# T042 -- the residual agrees with the grid tail, to a tolerance
#         (TR-029, TR-030, TR-055, SC-015)
# --------------------------------------------------------------------------- #

#: A disagreement an order of magnitude *inside* `PROB_SUM_TOLERANCE`, and four
#: orders of magnitude above the ulp of the values involved -- so the two stored
#: doubles are provably different bit patterns, not the same number twice.
WITHIN_TOLERANCE_OFFSET = 5e-10

#: A disagreement an order of magnitude *outside* it. `1e-8` rather than
#: `1.1e-9`: the boundary itself is not a number a test should assert on, because
#: `abs(a - (a + 1e-9))` need not be exactly `1e-9` in binary floating point, so a
#: boundary test would be measuring the subtraction and not the rule.
OUTSIDE_TOLERANCE_OFFSET = 1e-8


def test_a_residual_agreeing_exactly_with_the_grid_tail_is_accepted(
    db_session: Session,
) -> None:
    """TR-030, invariant 22: the ordinary case, where the two computations coincide.

    `residual_tail_mass` is `P(T > horizon_days)` and `survival[horizon_days]` is
    `P(not delivered by day horizon_days)`; they are definitionally the same
    quantity, computed by the producer along two different paths. When both paths
    land on the same double, the row must be accepted -- and `exactly_equal` being
    true here is what shows the tolerance is not hiding a systematic offset.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    insert_posterior(db_session, posterior_row(run, po_line_id))

    comparison = db_session.execute(
        RESIDUAL_COMPARISON, {"run_id": run["run_id"], "po_line_id": po_line_id}
    ).one()
    assert comparison.exactly_equal is True
    assert comparison.within_tolerance is True


def test_a_residual_within_the_tolerance_is_accepted_and_is_not_exactly_equal(
    db_session: Session,
) -> None:
    """TR-055, SC-015: the comparison is a **tolerance**, and this is the proof.

    The row inserted here disagrees with the grid tail by `5e-10`. Two facts are
    then read back from the server about the values as stored:

    * `survival[horizon_days] = residual_tail_mass` is **false** -- the two are
      genuinely different doubles, so this row would be rejected by an equality;
    * `abs(survival[horizon_days] - residual_tail_mass) <= 1e-9` is **true** -- so
      the delivered check accepts it.

    Without both halves this file would pass unchanged against a schema that
    compared the two operands with `=`, which SC-015 explicitly forbids. The
    reason it forbids it: the producer computes the residual from the draws by its
    own path rather than copying `survival[horizon_days]`, which is what makes
    this a genuine agreement test between two computations rather than a
    tautology -- and is exactly why the last bit cannot be relied on. Written as
    an equality the check would reject correct data intermittently, in a way no
    test would reliably reproduce.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    nudged = FIXTURE_RESIDUAL + WITHIN_TOLERANCE_OFFSET
    assert nudged != FIXTURE_RESIDUAL, "the offset must survive rounding to be worth inserting"

    insert_posterior(db_session, posterior_row(run, po_line_id, residual_tail_mass=nudged))

    comparison = db_session.execute(
        RESIDUAL_COMPARISON, {"run_id": run["run_id"], "po_line_id": po_line_id}
    ).one()
    assert comparison.exactly_equal is False, (
        "if the stored operands are bit-identical this row would also satisfy an "
        "equality check, and the test proves nothing about the tolerance"
    )
    assert comparison.within_tolerance is True


def test_a_residual_disagreeing_by_more_than_the_tolerance_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-030, TR-055: `1e-8` of disagreement is a wrong computation, not float noise.

    Both compared quantities are `count / draw_count` ratios, so realised error is
    around `1e-16` and `1e-9` is roughly seven orders of magnitude of slack --
    deliberately, so that a failure here means the producer got the arithmetic
    wrong and not that two doubles disagreed in their last bits.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    wrong = FIXTURE_RESIDUAL + OUTSIDE_TOLERANCE_OFFSET

    with assert_rejects(db_session, psycopg.errors.CheckViolation, RESIDUAL_MATCHES_GRID_TAIL):
        insert_posterior(db_session, posterior_row(run, po_line_id, residual_tail_mass=wrong))


def test_the_delivered_residual_check_is_a_tolerance_and_would_fail_as_an_equality(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """SC-015: exact equality is not merely absent from the DDL -- it would break data.

    Two assertions, and the second is the one that could not be replaced by
    reading the migration.

    1. The catalogued definition is `abs(...) <= 0.000000001` and contains no
       equality between the two operands. `pg_get_constraintdef` renders `1e-9` in
       decimal, so the literal is matched in the form PostgreSQL stores it.
    2. The tolerance check is **replaced** by the forbidden equality inside a
       savepoint, and the row from
       `test_a_residual_within_the_tolerance_is_accepted_and_is_not_exactly_equal`
       -- correct data the delivered schema accepts -- is then **refused**. The
       mutation is named `ck_line_posterior__residual_exact_equality`, so the
       rejection is attributable to it and not to anything shipped.

    DDL is transactional, so the mutation is gone when the savepoint rolls back
    and the delivered constraint is untouched.
    """
    definition = db_session.execute(
        CONSTRAINT_DEFINITION, {"conname": RESIDUAL_MATCHES_GRID_TAIL}
    ).scalar_one()
    assert "abs(" in definition, f"the residual check must be a tolerance; got {definition}"
    assert "<=" in definition
    assert "0.000000001" in definition, (
        f"the tolerance literal must be PROB_SUM_TOLERANCE ({PROB_SUM_TOLERANCE}); got {definition}"
    )
    assert "survival[horizon_days] = residual_tail_mass" not in definition

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    accepted_row = posterior_row(
        run, po_line_id, residual_tail_mass=FIXTURE_RESIDUAL + WITHIN_TOLERANCE_OFFSET
    )

    with (
        schema_temporarily_altered(
            db_session, DROP_RESIDUAL_TOLERANCE_CHECK, ADD_RESIDUAL_EQUALITY_CHECK
        ),
        assert_rejects(db_session, psycopg.errors.CheckViolation, RESIDUAL_EXACT_EQUALITY),
    ):
        insert_posterior(db_session, accepted_row)

    # Delivered schema restored: the same row goes in.
    insert_posterior(db_session, accepted_row)


# --------------------------------------------------------------------------- #
# T043 -- one row, one digest, one canonical array (TR-031, TR-040, TR-068)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("column", ["draws", "survival"])
def test_neither_array_is_insertable_without_the_other(
    db_session: Session,
    column: str,
) -> None:
    """TR-031, SC-014, invariant 21: the two arrays cannot half-exist.

    **Why this is a `NOT NULL` violation and not a cross-row rule.** The spec's
    `PosteriorDraws` and `SurvivalArray` are two *column groups of one row* here,
    not two tables. So "the draws were written but the survival curve was not" is
    not a state the database can be in: there is no second row to be missing, and
    nothing to check across rows. The rejection is `NotNullViolation` naming the
    absent column, because the only way to express the half-written state at all
    is to leave one column null -- and the column refuses.

    That single-row design is what removes the need for a mechanism. Two tables
    plus a both-or-neither rule would need either a deferred constraint pair or a
    trigger; this schema has exactly one deferrable constraint, in `0007`, and
    **zero** triggers. The invariant is carried by table design, which is why
    `data-model.md`'s invariant map records invariant 21's mechanism as "they are
    two NOT NULL columns of one row" rather than naming a constraint.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)

    assert_not_null_violation(
        db_session,
        POSTERIOR_INSERT,
        posterior_row(run, po_line_id, **{column: None}),
        column,
    )


def test_neither_array_is_ever_separated_from_the_other(db_session: Session) -> None:
    """TR-031: the structural claim above, read from the catalogue.

    If a migration split the arrays into two tables, every `NOT NULL` test above
    would keep passing while the invariant quietly became a cross-row rule with
    nothing enforcing it. So the claim is asserted directly, over the whole
    schema: wherever either array appears, both appear, on one table, as
    `NOT NULL` float8 arrays.

    **Rescoped 2026-07-27, and the reason is the one that rescoped
    `test_table_ownership.py` and both `DECLARED_BLOCKS` tables before it.** This
    read "both arrays live on `line_posterior` and nowhere else", which was a
    faithful reading of TR-031 while there was one artifact store. E007's `0302`
    adds a second, `held_out_prediction`, because the delivered table admits no
    order-date-anchored row: `line_posterior.survival` is NOT NULL and
    `ck_line_posterior__draws_non_negative` rejects the negative duration a
    pre-as-of delivery carries under the run anchor. Two stores is the
    arrangement working, not the invariant breaking.

    The claim is unchanged and no weaker. TR-031 is *both-or-neither on one
    row*, and the single-table list was a proxy that a second population
    invalidated — exactly as the single-epic block list was a proxy that a second
    authoring epic invalidated. Asserted as a **pairing** now, so a third store
    is covered the day it lands and a store carrying only one of the two arrays
    fails, which the old form could not distinguish from a store carrying both.

    Still no mechanism, on either table. Two tables plus a both-or-neither rule
    would need a deferred constraint pair or a trigger; this schema has one
    deferrable constraint, in `0007`, and **zero** triggers. Each store carries
    the invariant by table design, which is why `data-model.md`'s invariant map
    records invariant 21's mechanism as "they are two NOT NULL columns of one
    row" rather than naming a constraint.
    """
    columns = db_session.execute(ARRAY_COLUMNS_IN_SCHEMA).fetchall()

    assert columns, (
        "neither `draws` nor `survival` exists anywhere in the schema, so this test is "
        "asserting a pairing over an empty set. The artifact stores are gone or the "
        "database is unmigrated."
    )

    carried_by: dict[str, set[str]] = {}
    for column in columns:
        carried_by.setdefault(column.table_name, set()).add(column.column_name)

    unpaired = sorted(
        (table, sorted(present)) for table, present in carried_by.items() if len(present) != 2
    )
    assert not unpaired, (
        f"{unpaired} carry one of the two arrays and not the other. TR-031's invariant is "
        f"that the draws and the survival curve cannot half-exist, and it is carried by "
        f"their being two NOT NULL columns of one row -- a table holding one of them "
        f"alone makes 'the other was never written' a representable state, with no "
        f"constraint able to see it and no trigger available to."
    )

    for column in columns:
        assert column.udt_name == "_float8", "double precision[], per §Conventions"
        assert column.is_nullable == "NO", (
            f"{column.table_name}.{column.column_name} is nullable, so the half-written "
            f"state this invariant forbids is expressible on that table by leaving one "
            f"column null."
        )


def test_the_digest_is_bytes_taken_over_a_named_serialization(db_session: Session) -> None:
    """TR-040, OBJ5 VC8: the digest covers a defined byte layout, recorded by name.

    Three separate claims, because a digest is only reproducible if all three
    hold:

    * both digest columns are `bytea` -- 32 raw bytes, not 64 hex characters. A
      text rendering would invite exactly the question of which rendering.
    * `draw_serialization` names the scheme the digest was taken over, and admits
      one value today: little-endian IEEE-754 doubles, C order, no padding. A
      reader never has to guess endianness or stride, and widening the set is a
      forward migration that must also say what the new layout digests to.
    * the length is checked in **bytes** by `octet_length`, so it is exact rather
      than a proxy -- asserted on both tables by inserting a 31-byte digest.
    """
    types = db_session.execute(DIGEST_COLUMN_TYPES).fetchall()
    assert [(row.table_name, row.data_type) for row in types] == [
        ("forecast_run", "bytea"),
        ("line_posterior", "bytea"),
    ]

    definition = db_session.execute(
        CONSTRAINT_DEFINITION, {"conname": DRAW_SERIALIZATION}
    ).scalar_one()
    assert "'float64-le-c-contiguous'" in definition, (
        f"the serialization the digest is defined over must be named in the DDL; got {definition}"
    )


def test_an_unnamed_serialization_scheme_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-040: only the one declared byte layout is storable.

    A run claiming some other layout would make its `artifact_hash` unverifiable:
    the digest is defined over bytes, and nothing else in the row says which
    bytes.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAW_SERIALIZATION):
        insert_run(db_session, draw_serialization="float64-be-fortran")


def test_a_digest_that_is_not_thirty_two_bytes_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-040: 32 bytes is SHA-256, counted in bytes on both tables."""
    short = bytes(range(31))

    with assert_rejects(db_session, psycopg.errors.CheckViolation, ARTIFACT_HASH_LENGTH):
        insert_run(db_session, artifact_hash=short)

    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    with assert_rejects(db_session, psycopg.errors.CheckViolation, DRAW_DIGEST_LENGTH):
        insert_posterior(db_session, posterior_row(run, po_line_id, draw_digest=short))


def test_the_digest_is_unaffected_by_the_sessions_float_text_rendering(
    db_session: Session,
) -> None:
    """TR-040: why the digest is `bytea` -- the text rendering of the same draws varies.

    `extra_float_digits` changes how PostgreSQL renders `double precision` as
    text: at `-3` the fixture's first draw comes back with thirteen significant
    digits, at `1` with all seventeen. Those are two different numbers, so a
    digest taken over a text rendering would depend on a session setting and the
    same draws would hash differently in two sessions -- silently, and only for
    values needing more than fifteen digits.

    The stored `draw_digest` is bytes and is identical under both settings, which
    is the property that makes `artifact_hash` reproducible at all.

    `SET LOCAL` on a pooled connection, so the setting leaves with the
    transaction.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    insert_posterior(
        db_session,
        posterior_row(run, po_line_id, draws=array_literal(RENDERING_SENSITIVE_DRAWS)),
    )
    identity = {"run_id": run["run_id"], "po_line_id": po_line_id}

    db_session.execute(SET_SHORT_FLOAT_RENDERING)
    short_rendering = db_session.execute(DRAWS_AS_TEXT, identity).scalar_one()
    short_digest = db_session.execute(DIGEST_BYTES, identity).scalar_one()

    db_session.execute(SET_ROUND_TRIP_FLOAT_RENDERING)
    full_rendering = db_session.execute(DRAWS_AS_TEXT, identity).scalar_one()
    full_digest = db_session.execute(DIGEST_BYTES, identity).scalar_one()

    assert short_rendering != full_rendering, (
        "the premise of this test is that the text rendering is session-dependent; "
        "if both renderings agree, pick draws that need all 17 digits"
    )
    assert repr(RENDERING_SENSITIVE_DRAWS[0]) in full_rendering
    assert repr(RENDERING_SENSITIVE_DRAWS[0]) not in short_rendering
    assert bytes(short_digest) == bytes(full_digest) == DRAW_DIGEST, (
        "the digest is taken over bytes, so no session setting can move it"
    )


def test_the_survival_curve_is_derivable_from_the_canonical_draws(
    db_session: Session,
) -> None:
    """TR-068: the draws are canonical; the survival curve is a function of them.

    `survival[k]` is recomputed from the stored `draws` in SQL -- the share of
    draws beyond day k -- and compared with the stored curve, element by element,
    within `PROB_SUM_TOLERANCE`. Then the same for `residual_tail_mass`, the
    share beyond the horizon.

    This is what "canonical" means operationally: the curve is stored for the
    reader's convenience, at one array access per line instead of a scan over
    4,000 draws, and it carries no information the draws do not already have. A
    curve that could not be recovered from the draws would mean one of the two
    was wrong, with nothing in the row to say which.

    Note the direction of the check. The schema does **not** enforce this -- a
    `CHECK` could, in principle, but the fixture is what is under test here, and
    the constraint that does exist covers the tail (`residual_tail_mass` against
    `survival[horizon_days]`) rather than the whole curve.
    """
    run = insert_run(db_session)
    po_line_id = insert_line(db_session)
    insert_posterior(db_session, posterior_row(run, po_line_id))
    identity = {"run_id": run["run_id"], "po_line_id": po_line_id}

    grid = db_session.execute(SURVIVAL_DERIVED_FROM_DRAWS, identity).fetchall()

    assert len(grid) == FIXTURE_HORIZON_DAYS
    for day in grid:
        assert abs(day.stored - day.derived) <= PROB_SUM_TOLERANCE, (
            f"survival[{day.k}] is {day.stored} but the draws give {day.derived}"
        )

    tail = db_session.execute(RESIDUAL_DERIVED_FROM_DRAWS, identity).one()
    assert abs(tail.stored - tail.derived) <= PROB_SUM_TOLERANCE


def test_a_percentile_is_a_subscript_into_the_canonical_draws(db_session: Session) -> None:
    """TR-033, TR-068, OBJ5 VC10: `draws[ceil(p * draw_count)]`, one-based, no interpolation.

    Run at the shape `data-model.md` declares -- 4,000 draws and a 365-day grid --
    so this also demonstrates that the real artifact shape is representable and
    passes every constraint on the table, not just the five-draw fixture the rest
    of the file uses.

    The convention is evaluated in SQL, as E010 will evaluate it, and compared
    with the same arithmetic in Python. Both sides use IEEE doubles, so they agree
    by construction on any `p`; the `p` values chosen are exactly representable
    binary fractions, which keeps the assertion about the *subscript rule* rather
    than about how `ceil` rounds a product that landed a bit below an integer.
    Nearest rank means no interpolation and no averaging: the answer is always an
    element of the array, which is why `draws[2000]` is exactly `2000.0` and not
    a value between two draws.
    """
    run = insert_run(db_session, draw_count=DECLARED_DRAW_COUNT, horizon_days=DECLARED_HORIZON_DAYS)
    po_line_id = insert_line(db_session)
    insert_posterior(
        db_session,
        posterior_row(
            run,
            po_line_id,
            draws=array_literal(DECLARED_DRAWS),
            survival=array_literal(DECLARED_SURVIVAL),
            residual_tail_mass=DECLARED_RESIDUAL,
        ),
    )
    identity = {"run_id": run["run_id"], "po_line_id": po_line_id}

    for probability in (0.25, 0.5, 0.75, 1.0):
        expected = DECLARED_DRAWS[math.ceil(probability * DECLARED_DRAW_COUNT) - 1]
        actual = db_session.execute(
            PERCENTILE_BY_SUBSCRIPT, {**identity, "p": probability}
        ).scalar_one()
        assert actual == expected, (
            f"the p={probability} percentile must be draws"
            f"[{math.ceil(probability * DECLARED_DRAW_COUNT)}] = {expected}, got {actual}"
        )


# --------------------------------------------------------------------------- #
# T036 / T037 -- two deliberate absences (TR-062, TR-080)
# --------------------------------------------------------------------------- #
#
# Both requirements state that something is *not* in the schema, and `tasks.md`
# assigns each to its migration task as "column presence and deliberate absence".
# Neither can be exercised by a rejection: there is no row to refuse and no error
# to catch, so the catalogue is the only witness. The precedent is
# `test_extraction.py`'s TR-082 assertion, which reads `pg_attribute` to show
# `extracted_value` carries no agent column.
#
# The reason to assert an absence at all is that "by design" and "not yet" look
# identical in a schema. E010 owns read-time risk and E006 owns ingestion runs;
# each will arrive holding a reason to add exactly the object forbidden here. A
# failing test is how that epic finds out it is changing a decision rather than
# filling a hole -- which is the whole difference between propagating a design and
# quietly reversing one.
#
# Each test asserts the requirement's *positive* half in the same breath, so
# neither can pass against a table that had lost the provenance or the anchor
# altogether -- an absence read off a catalogue that returned nothing is vacuous,
# and vacuously true is exactly how an absence assertion fails silently. Both
# tests were verified by adding the forbidden object with real DDL inside the
# rolled-back session and watching them fail, and by dropping the positive half
# and watching them fail for the other reason.

#: `line_posterior`'s only outbound references: the run whose shape it declares,
#: and the line it forecasts. A closed set rather than a blocklist, following
#: `test_extraction.py`'s TR-045 assertion -- naming what is permitted keeps
#: holding as later migrations add tables, where a list of forbidden targets has
#: to be extended each time one appears.
PERMITTED_POSTERIOR_FOREIGN_KEY_TARGETS = frozenset({"forecast_run", "purchase_order_line"})

#: The two relations that hold a forecast artifact. A lineage table is a relation
#: referencing one of these *and* one of the inputs below.
FORECAST_RELATIONS = frozenset({"forecast_run", "line_posterior"})

#: The inputs TR-062 names -- "the extracted values or lifecycle events the fit
#: consumed" -- as the relations that hold them. Deliberately not widened to
#: `chunk` or `document`: those are the citation chain behind an extracted value,
#: and forbidding a reference to them would be a stronger rule than TR-062 states.
CONSUMED_INPUT_RELATIONS = frozenset(
    {
        "extracted_value",
        "extracted_value_contributing_chunk",
        "extraction_failure",
        "lifecycle_event",
    }
)

#: TR-062's run-granularity provenance, which is what stands in for row-level
#: lineage: the code revision, the input data hash, and the sampling seeds.
#: Asserted present alongside the absence, so the test cannot pass against a run
#: table that records no provenance at all -- at which point "no per-line link"
#: would be true and worthless.
RUN_GRANULARITY_PROVENANCE: tuple[str, ...] = ("code_commit", "input_data_hash", "seed_entropy")

FOREIGN_KEY_EDGES = text(
    """
    SELECT con.conrelid::regclass::text AS referencing,
           con.confrelid::regclass::text AS referenced
    FROM pg_constraint AS con
    JOIN pg_namespace AS ns ON ns.oid = con.connamespace
    WHERE con.contype = 'f' AND ns.nspname = 'public'
    """
)

COLUMNS_OF = text(
    """
    SELECT attname
    FROM pg_attribute
    WHERE attrelid = to_regclass('public.' || :table_name)
      AND attnum > 0
      AND NOT attisdropped
    ORDER BY attnum
    """
)


def test_tr062_carries_provenance_per_run_with_no_per_line_link_to_the_inputs(
    db_session: Session,
) -> None:
    """TR-062: run-granularity provenance, and **no** row-level lineage -- by design.

    The deliberate absence, verified: nothing in the schema links a `line_posterior`
    row back to the extracted values or lifecycle events its fit consumed. Two
    assertions, because there are two shapes the link could take -- a foreign key
    out of `line_posterior` itself, and a separate lineage table joining the two
    sides -- and closing only the first would leave the second the obvious way to
    add it.

    Reproduction is therefore **by re-running the recorded run**, which is what the
    three run-level columns asserted present here make possible: the code revision,
    the input data hash, and the seed entropy pin the inputs collectively rather
    than per row. That is a weaker claim than row-level lineage and it is the one
    the epic chose; `data-model.md` records the reversal trigger as "a published
    figure that cannot be resolved to its inputs from the run row alone adds a
    per-line input manifest".

    **What a later epic would break by adding one.** A per-line manifest is a
    second, finer answer to "where did this number come from", and the run row
    would stop being the whole answer while still looking like it. Two granularities
    of the same fact is the failure Principle I is about -- a reader cannot tell
    which one a given row was written under, and a row present in one and absent
    from the other is unattributable in both directions. It would also make the
    artifact's reproducibility depend on rows the fit does not read, so a lineage
    row lost or never written would leave a forecast that still validates and no
    longer reproduces. If that manifest is genuinely needed, this test fails and
    TR-062 is amended first -- that failure is the notification, not an obstacle.
    """
    edges = db_session.execute(FOREIGN_KEY_EDGES).all()

    assert edges, (
        "pg_constraint reported no foreign keys in the public schema at all, so both "
        "absence assertions below would be about an empty set"
    )

    columns = [
        row.attname for row in db_session.execute(COLUMNS_OF, {"table_name": "forecast_run"})
    ]
    missing = [column for column in RUN_GRANULARITY_PROVENANCE if column not in columns]
    assert not missing, (
        f"TR-062 carries provenance at run granularity, so `forecast_run` must record the "
        f"code revision, the input data hash, and the sampling seeds. Absent: {missing}. "
        f"With these gone the absence of per-line lineage would be true and useless -- "
        f"nothing would pin the inputs at any granularity"
    )

    posterior_targets = {row.referenced for row in edges if row.referencing == "line_posterior"}
    assert posterior_targets == set(PERMITTED_POSTERIOR_FOREIGN_KEY_TARGETS), (
        f"`line_posterior`'s only outbound references are its run and its line. Expected "
        f"{sorted(PERMITTED_POSTERIOR_FOREIGN_KEY_TARGETS)}, got {sorted(posterior_targets)}. "
        f"A reference to an extraction or lifecycle relation would be the per-line link "
        f"TR-062 declares absent"
    )

    referenced_by: dict[str, set[str]] = {}
    for row in edges:
        referenced_by.setdefault(row.referencing, set()).add(row.referenced)
    lineage_tables = sorted(
        relation
        for relation, targets in referenced_by.items()
        if targets & FORECAST_RELATIONS and targets & CONSUMED_INPUT_RELATIONS
    )
    assert not lineage_tables, (
        f"{lineage_tables} reference both a forecast relation "
        f"({sorted(FORECAST_RELATIONS)}) and one of the inputs a fit consumed "
        f"({sorted(CONSUMED_INPUT_RELATIONS)}), which is what a per-line lineage table is. "
        f"TR-062 records reproduction as re-running the recorded run instead; adding this "
        f"means amending the requirement, not extending this list"
    )


#: Column-name fragments that would mean a maximum permitted age had been recorded
#: on the run, matched against each underscore-separated *word* of a column name
#: rather than against the name as a whole, so `max_age_days`, `stale_after`,
#: `expires_at`, and `freshness_ttl` are all caught rather than only the one
#: spelling someone thought of.
#:
#: **Narrowed 2026-07-27 from bare substring matching to word-prefix matching,
#: and the marker `max_` to `max` with it.** The scan is a heuristic over names,
#: and a bare substring makes it a heuristic over English: E007's
#: `vendor_shrinkage` (revision `0300`, FR-019) contains the letters `age` and
#: was reported here as a staleness threshold. The original comment recorded
#: that "none of `forecast_run`'s nineteen delivered columns contains any of
#: them", which was a true observation about nineteen columns and not a property
#: of the rule.
#:
#: The claim is unweakened and the four spellings it was written for are still
#: caught -- each marker is a *prefix of a word*, so `expires_at` matches
#: `expir`, `maximum_age_days` matches `max`, and a column genuinely named for
#: an age still fails. What no longer matches is a marker buried inside an
#: unrelated word, which is a false positive rather than a lesser version of the
#: finding.
MAXIMUM_AGE_COLUMN_MARKERS = ("age", "stale", "expir", "ttl", "freshness", "max", "deadline")

#: The seventh column would be the seventh constant. `schema_constants` publishes
#: six values plus its `singleton` key and nothing else, and TR-080's clarification
#: names precisely this as where a staleness threshold *would* go: "if a second
#: consumer needs the same threshold, it becomes a seventh published schema
#: constant rather than two copies". So a closed set is the honest assertion here
#: rather than a marker scan -- the table is small, fixed, and normative.
PUBLISHED_CONSTANT_COLUMNS = frozenset(
    {
        "singleton",
        "vector_dimension",
        "survival_horizon_days",
        "draw_count",
        "probability_sum_tolerance",
        "anchor_date_convention",
        "percentile_convention",
    }
)


def test_tr080_imposes_no_maximum_age_on_a_run_and_exposes_the_anchor_instead(
    db_session: Session,
) -> None:
    """TR-080: no maximum permitted age exists -- by design -- and the anchor is exposed.

    The deliberate absence, verified in the two places the threshold could live: as
    a seventh published constant, and as a column on the run. Both come back
    clean, and the *positive* half is asserted with them -- `as_of_date` is
    present, which is what lets a reader compute the artifact's age itself.
    Together those are the whole requirement: the age is computable and no
    threshold on it is imposed here.

    Not an oversight, and the spec says so in the clarification TR-080 came from:
    "No maximum age is imposed by this epic. The run's as-of anchor date is exposed
    so a reader computes the artifact's age itself; the threshold and its interface
    treatment belong to E010, which owns read-time risk." The alternative
    considered and rejected was a `max_forecast_age_days` constant now, which would
    fix a product threshold before the read surface that uses it exists.

    **What a later epic would break by adding one.** A threshold in the schema is a
    threshold every consumer inherits silently. Two things go wrong at once. The
    schema would begin refusing or relabelling artifacts on a product judgement --
    "too old to use" is a decision about what a reader should be shown, and
    Principle II keeps that at the interface where the interval is published, not
    at the storage boundary where it becomes invisible. And the number would be
    fixed before E010 exists to say what it should be, so the first genuine
    requirement for it would arrive against a value already in production and
    already relied on. Note what is *not* claimed: the schema does not stop a
    consumer treating an old run as unusable. It declines to decide for them, and
    `v_active_forecast_run` returning zero rows -- never the newest run -- is what
    keeps "no current forecast" distinguishable from "current but old" (TR-027).
    """
    constant_columns = {
        row.attname for row in db_session.execute(COLUMNS_OF, {"table_name": "schema_constants"})
    }

    assert constant_columns == set(PUBLISHED_CONSTANT_COLUMNS), (
        f"`schema_constants` publishes six constants and its singleton key. Expected "
        f"{sorted(PUBLISHED_CONSTANT_COLUMNS)}, got {sorted(constant_columns)}. A seventh "
        f"column is where TR-080's staleness threshold would appear, and TR-080 imposes no "
        f"maximum age -- E010 owns that decision (data-model.md §Declared Constants is "
        f"normative for this list, so a legitimate seventh constant is recorded there first)"
    )

    run_columns = [
        row.attname for row in db_session.execute(COLUMNS_OF, {"table_name": "forecast_run"})
    ]

    assert run_columns, (
        "pg_attribute reported no columns for forecast_run, so the marker scan below would "
        "pass over nothing"
    )
    offenders = sorted(
        column
        for column in run_columns
        for word in column.lower().split("_")
        for marker in MAXIMUM_AGE_COLUMN_MARKERS
        if word.startswith(marker)
    )
    assert not offenders, (
        f"TR-080 imposes no maximum permitted age on a forecast run, so `forecast_run` "
        f"carries no age, staleness, or expiry column by design. Found {offenders}. The "
        f"threshold and its interface treatment belong to E010; putting it here would fix a "
        f"product judgement at the storage boundary, where no reader can see or override it"
    )

    assert ANCHOR_DATE_FIELD in run_columns, (
        f"TR-080's positive half is that the run's as-of anchor date is *exposed*, so a "
        f"reader computes the artifact's age itself. Without `{ANCHOR_DATE_FIELD}` the "
        f"absence of a maximum age would leave the age uncomputable rather than the reader's "
        f"to decide. Columns are {run_columns}"
    )
