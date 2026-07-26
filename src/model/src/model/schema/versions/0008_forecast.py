"""forecast

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26 09:35:35.938966

Three helper functions, two tables, and one view: the versioned forecast artifact
contract (OBJ5). `data-model.md` §Migration Sequence assigns
`fn_is_sorted_ascending`, `fn_is_non_increasing`, `fn_all_within_unit_interval`,
`forecast_run`, `line_posterior`, and `v_active_forecast_run` all to `0008`.

Unlike `0007`, nothing here is a cycle -- the order is simply function, run,
view, artifact, because each object references the one before it. What *is*
structural is the shape of the two tables, and it is worth stating once:

**TR-031, invariant 21 -- the two arrays cannot half-exist, and no constraint
enforces that.** `line_posterior` holds `draws` and `survival` as two NOT NULL
columns of *one* row, so "the draws were written but the survival curve was not"
is not a state the database can be in. There is nothing to check, because there
is no second row to be missing. The spec's `PosteriorDraws` and `SurvivalArray`
are two column groups here, not two tables. Two tables plus a
"both-or-neither" rule would need either a deferred constraint pair or a
trigger, and this schema has one deferrable constraint (in `0007`) and zero
triggers.

**TR-028, TR-029, TR-069, TR-072, TR-073 -- array length without a trigger.** A
`CHECK` cannot read another row, so "this array is as long as its run says" looks
like it needs a trigger. It does not. `uq_forecast_run__shape UNIQUE (run_id,
draw_count, horizon_days)` lifts both lengths into a referenceable key;
`fk_line_posterior__run_shape` copies them onto the artifact row and *proves*
they are the run's own values; then two plain single-row `array_length` checks
compare each array against its own row's copy. Neither check reads anything but
the row it sits on, so nothing here depends on a trigger and a dump-and-restore
re-proves every one of them row by row.

**TR-049, TR-033 -- one anchor per run.** `as_of_date` is on `forecast_run` and
nowhere else. Every line's `draws` and `survival` in a run are measured from that
one date, so two lines of the same run cannot disagree about what day 0 is, and
the anchor is a single value to correct if it is ever wrong.

**TR-080 -- there is no maximum-age constant here, deliberately.** The schema
exposes `as_of_date` and `created_at` and takes no position on when a run becomes
stale. A threshold is a read-time policy: E010 decides what "stale" means and
answers it from `as_of_date`. Putting a `ck_forecast_run__not_too_old` here would
be a constraint that turns true rows false as the clock moves, which is not a
constraint at all -- it would break `pg_dump`/restore of a database older than
the threshold.

**TR-027 -- "no active run" is not "the newest run".** The active pointer is
`is_active` with a partial unique index, and `v_active_forecast_run` returns zero
rows when nothing is active. No `ORDER BY created_at DESC LIMIT 1` appears in
this revision or anywhere in the schema; `ix_forecast_run__created_at` exists for
operational listing and is never the selection mechanism. A recency fallback
would make "no current forecast" indistinguishable from "stale forecast", which
is exactly the distinction OBJ5 VC3 requires.

**Recorded deviations from `data-model.md` (TR-083).** Both are null-handling
repairs, both verified by inserting the row the declared form would have
accepted:

1. `ck_line_posterior__draws_length` and `ck_line_posterior__survival_length` are
   written `coalesce(array_length(...), 0) = ...`. `array_length('{}', 1)` is
   **NULL, not 0**, so the declared `array_length(draws, 1) = draw_count` yields
   NULL on an empty array and the `CHECK` *accepts* it -- an artifact row with no
   draws at all, passing every other constraint.
2. `ck_line_posterior__draws_1d` and `ck_line_posterior__survival_1d` also assert
   `array_lower(..., 1) = 1`. PostgreSQL array subscripts need not start at 1
   (`'[0:2]={...}'` is legal), and both the percentile convention
   (`draws[ceil(p * draw_count)]`, one-based) and
   `ck_line_posterior__residual_matches_grid_tail` (`survival[horizon_days]`)
   index these arrays directly. Without the lower bound pinned, a lower-bound-0
   array of the declared length puts the last element out of subscript reach and
   `survival[horizon_days]` is NULL -- which makes the residual check NULL, which
   the `CHECK` accepts.

Neither deviation adds a constraint name: both strengthen a check
`data-model.md` already declares, so the object inventory T052 audits is
unchanged.
"""

from collections.abc import Sequence

from alembic import op

from model.schema.helpers import (
    FN_ALL_WITHIN_UNIT_INTERVAL,
    FN_IS_NON_INCREASING,
    FN_IS_SORTED_ASCENDING,
)

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the array helpers, `forecast_run`, its view, and `line_posterior`.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    `fk_<table>__<purpose>`, `ck_<table>__<rule>`, and `ix_<table>__<purpose>`.
    A server-generated name cannot be relied on by a later forward migration's
    `ALTER TABLE ... DROP CONSTRAINT`, and a test asserting *which* rule rejected
    a row matches on the constraint name -- never on message text, which is
    locale- and version-dependent.

    TR-039: every `CHECK` here that constrains a single column's value domain
    sits on a `NOT NULL` column, so none can pass vacuously. This revision adds
    **no** check on a nullable column -- every column of both tables is NOT NULL
    except `forecast_run.is_active` and `created_at`, which carry defaults and are
    themselves NOT NULL. The null cases that do exist are *element* nulls inside
    the two arrays and out-of-range array subscripts, which is a different trap
    with a different fix; see the array checks below and
    `model.schema.helpers`. Every check was verified by inserting the violating
    row, never by reading the expression.
    """
    # --- the three array helpers (T035) -------------------------------------
    #
    # The DDL lives in `model.schema.helpers` rather than inline; see that
    # module's docstring for why, for the recorded restriction that changing one
    # of these functions is a two-step forward migration under a new name, and
    # for the reason all three treat a NULL element as a violation rather than as
    # an unknown.
    #
    # Created first: a `CHECK` referencing a missing function fails at DDL time.
    op.execute(FN_IS_SORTED_ASCENDING)
    op.execute(FN_IS_NON_INCREASING)
    op.execute(FN_ALL_WITHIN_UNIT_INTERVAL)

    # --- forecast_run (T036, T037) ------------------------------------------
    #
    # TR-026: the nine reproducibility fields, every one NOT NULL. Naming them,
    # because "nine" is otherwise a number nobody can check:
    #
    #   1. run_id                   which run
    #   2. code_commit              which code
    #   3. input_data_hash          which inputs
    #   4. seed_entropy             which random stream
    #   5. library_versions         which numerical stack
    #   6. artifact_hash            which bytes came out
    #   7. artifact_schema_version  how to read those bytes
    #   8. model_version            which model produced them
    #   9. created_at               when
    #
    # NOT NULL rather than "populated by the writer": a reproducibility field that
    # can be absent is a reproducibility field that will be absent on the one run
    # anybody needs to reproduce. TR-062 puts all nine on the run row and adds no
    # lineage table -- run granularity is the granularity the artifact has, and a
    # separate lineage table would let a run exist without its lineage.
    op.execute(
        """
        CREATE TABLE forecast_run (
            run_id uuid NOT NULL,

            -- 40 lowercase hex, so `char(40)` rather than `text`: the width is
            -- part of the format and a git object name is never anything else.
            -- The regex is still required -- `char(40)` blank-pads a short value
            -- rather than refusing it.
            code_commit char(40) NOT NULL,

            -- Not one of the nine, and not decoration either. A run fitted from a
            -- dirty worktree is not reproducible from `code_commit` alone, and
            -- recording that as a boolean is honest where silence would not be
            -- (Principle I). E007 sets it; nothing here can verify it.
            code_worktree_dirty boolean NOT NULL,

            input_data_hash text NOT NULL,

            -- TR-063: the 128-bit root entropy, verbatim, as decimal digits --
            -- not an integer column. `numpy.random.SeedSequence` entropy does not
            -- fit in `bigint`, and per-chain streams are *spawned* from it rather
            -- than derived by arithmetic, so nothing ever adds to this value and
            -- storing it as text loses nothing. 1 to 39 digits covers 0 through
            -- 2^128 - 1.
            seed_entropy text NOT NULL,

            chain_count integer NOT NULL,
            draw_count integer NOT NULL,
            tuning_count integer NOT NULL,

            -- `jsonb`, not a child table: the set of libraries whose version
            -- matters is a property of the fitting code, not of the schema, and a
            -- child table would let a run exist with no versions recorded. The
            -- check pins the six that must always be present and admits more.
            library_versions jsonb NOT NULL,

            -- TR-040: 32 raw bytes, not 64 hex characters. `bytea` because the
            -- digest is taken over *bytes*, and a text rendering would invite
            -- exactly the question of which rendering.
            artifact_hash bytea NOT NULL,

            -- TR-040, OBJ5 VC8. The byte layout the digest was taken over,
            -- recorded as a name so a reader never has to guess endianness or
            -- stride. A single legal value today: widening it is a forward
            -- migration that must also say what the new layout digests to.
            draw_serialization text NOT NULL,

            -- TR-032, OBJ5 VC6, and gap G-10's storage half. The version of the
            -- artifact *format*, so a reader that does not understand a newer
            -- layout can refuse rather than misread it. Distinct from
            -- `model_version`: the format can change with no change to the model,
            -- and the model can change with no change to the format.
            artifact_schema_version integer NOT NULL,

            model_version text NOT NULL,

            -- TR-049, TR-033, OBJ5 VC9. The single anchor. `date`, not
            -- `timestamptz`: day 0 of a delivery-duration grid is a calendar day,
            -- and attaching a time zone would make "how many days until this is
            -- late" depend on where the reader sits.
            as_of_date date NOT NULL,

            -- TR-071, STF-008. The survival grid's length, on the run row rather
            -- than as a schema-wide literal, which is what makes
            -- `ck_line_posterior__survival_length` enforceable without a trigger
            -- and lets a future run change horizon with no migration.
            horizon_days integer NOT NULL,

            wall_clock_seconds double precision NOT NULL,

            -- TR-024: the line roster this run was fitted against, in the format
            -- E001 froze and `document` and `purchase_order_line` already use.
            roster_hash text NOT NULL,

            -- TR-027. DEFAULT false, so a run is inserted *inactive* and
            -- activation is a deliberate second statement. The alternative --
            -- defaulting to true -- would make the first insert of a batch
            -- collide with the live run, or worse, succeed and swap it.
            is_active boolean NOT NULL DEFAULT false,

            created_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_forecast_run PRIMARY KEY (run_id),

            -- Anchored `^`/`$`: without both, 'xdeadbeef...y' would match.
            -- Lowercase hex spelled as a character class rather than relying on a
            -- locale's notion of a hex digit, so the comparison stays byte-wise
            -- across an ICU upgrade.
            CONSTRAINT ck_forecast_run__commit_format
                CHECK (code_commit ~ '^[0-9a-f]{40}$'),

            -- The same literal `0003` uses for `document.roster_hash` and `0007`
            -- for `purchase_order_line.roster_hash`. An unprefixed digest and a
            -- truncated one are both refused.
            CONSTRAINT ck_forecast_run__input_hash_format
                CHECK (input_data_hash ~ '^sha256:[0-9a-f]{64}$'),

            -- Decimal digits only, 1 to 39 of them. `^[0-9]` and not `\\d`:
            -- PostgreSQL's `\\d` is locale-dependent in an ICU build and would
            -- admit non-ASCII digits that no integer parser accepts.
            CONSTRAINT ck_forecast_run__seed_entropy_format
                CHECK (seed_entropy ~ '^[0-9]{1,39}$'),

            -- A run with zero chains or zero retained draws produced no
            -- posterior. `> 0`, so the degenerate run is unrepresentable rather
            -- than merely unusual.
            CONSTRAINT ck_forecast_run__chain_count_positive
                CHECK (chain_count > 0),
            CONSTRAINT ck_forecast_run__draw_count_positive
                CHECK (draw_count > 0),

            -- Tuning draws are discarded, so zero of them is legal -- an already
            -- tuned run reloaded from a trace has none. `>= 0` rather than `> 0`.
            CONSTRAINT ck_forecast_run__tuning_count_non_negative
                CHECK (tuning_count >= 0),

            -- Two assertions in one check, and both matter. `jsonb_typeof(...) =
            -- 'object'` refuses a bare array or the JSON scalar `null` -- note
            -- that a *JSON* null is not a SQL NULL, so the column's NOT NULL does
            -- not catch it and `library_versions = 'null'::jsonb` would otherwise
            -- be a legal "recorded" version set. `?&` then requires all six keys
            -- to be present. Presence only: the *values* are version strings this
            -- schema has no business parsing, and additional keys are welcome.
            CONSTRAINT ck_forecast_run__library_versions_shape
                CHECK (
                    jsonb_typeof(library_versions) = 'object'
                    AND library_versions ?& array['pymc','arviz','numpy','pandas','pytensor','blas']
                ),

            -- 32 bytes = SHA-256. `octet_length` on `bytea` counts bytes, not
            -- characters, so this is exact and not a proxy.
            CONSTRAINT ck_forecast_run__artifact_hash_length
                CHECK (octet_length(artifact_hash) = 32),

            -- TR-040, OBJ5 VC8. One legal value, spelled out. Little-endian
            -- IEEE-754 doubles, C order, no padding -- the only layout the
            -- artifact digest is defined over.
            CONSTRAINT ck_forecast_run__draw_serialization
                CHECK (draw_serialization = 'float64-le-c-contiguous'),

            -- TR-032. Version 1 is the first artifact format; 0 and negative are
            -- not versions.
            CONSTRAINT ck_forecast_run__schema_version_positive
                CHECK (artifact_schema_version >= 1),

            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a `model_version` of one tab would satisfy a bare
            -- `btrim(model_version) <> ''` while naming nothing. No `coalesce`
            -- wrapper and none needed -- the column is NOT NULL, so the
            -- comparison is a definite boolean.
            --
            -- **Deviation from data-model.md, deliberate (TR-083).** That
            -- artifact spells the trim set `E' \\t\\n\\r\\f\\v'`, which PostgreSQL
            -- does not read as whitespace: its escape-string syntax has no `\\v`,
            -- and an unrecognized escape drops the backslash and keeps the
            -- character, so `E'\\v'` is the *letter* `v`. Written as declared this
            -- check would admit a vertical-tab-only value and reject a legitimate
            -- value of `vvv` -- one typo producing both a hole and a false
            -- rejection. `\\u000B` is the character the artifact means. Revisions
            -- `0004`, `0006`, and `0007` record the same deviation.
            CONSTRAINT ck_forecast_run__model_version_present
                CHECK (btrim(model_version, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- TR-071. A zero-day horizon would make the survival array empty,
            -- which `ck_line_posterior__survival_length` would then have to admit.
            CONSTRAINT ck_forecast_run__horizon_positive
                CHECK (horizon_days > 0),

            -- `>= 0`, not `> 0`: a run that finished inside the clock's
            -- resolution is a fast run, not an invalid one.
            CONSTRAINT ck_forecast_run__wall_clock_non_negative
                CHECK (wall_clock_seconds >= 0),

            CONSTRAINT ck_forecast_run__roster_hash_format
                CHECK (roster_hash ~ '^sha256:[0-9a-f]{64}$'),

            -- TR-073, and the object the whole length-enforcement chain hangs
            -- from. Redundant against `pk_forecast_run` by design: a composite
            -- foreign key must reference a unique key carrying *every* column it
            -- compares, and `(run_id)` alone cannot carry `draw_count` or
            -- `horizon_days`. This is what lets an artifact row's declared array
            -- lengths be *proven* to be its run's own values rather than asserted.
            CONSTRAINT uq_forecast_run__shape
                UNIQUE (run_id, draw_count, horizon_days)
        )
        """
    )

    # TR-027, OBJ5 VC2, SC-013, invariant 17. **"At most one active run" as a
    # database fact.** A partial unique index on a boolean column: the index holds
    # only rows where `is_active` is true, and being unique on `(is_active)` --
    # which is the constant `true` for every row it contains -- means it can hold
    # at most one row. Activating a second run fails with a UniqueViolation naming
    # this index.
    #
    # Why this and not the alternatives. A plain `UNIQUE (is_active)` would allow
    # exactly one *inactive* run as well, breaking the ordinary case of many
    # superseded runs. A `CHECK` cannot express it at all -- it cannot see other
    # rows. A trigger could, and would be skippable, disable-able, and invisible to
    # a restore; this schema has zero triggers (invariant map, TR-051).
    #
    # Zero active runs is legal and is a meaningful state, not a gap: it is
    # "no forecast is current", which `v_active_forecast_run` reports as zero rows.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_forecast_run__single_active
        ON forecast_run (is_active)
        WHERE is_active
        """
    )

    # Operational listing only -- "show me the recent runs" for a human. Recorded
    # here in the same terms data-model.md uses: this index is **never** the
    # selection mechanism for the active run. If a query ever reads
    # `ORDER BY created_at DESC LIMIT 1` to decide what is current, that query is
    # the defect, not this index.
    op.execute("CREATE INDEX ix_forecast_run__created_at ON forecast_run (created_at DESC)")

    # --- v_active_forecast_run (T037) ---------------------------------------
    #
    # TR-027, OBJ5 VC3. The read surface for "which forecast is current". Two
    # properties, and the second is the one that carries the requirement:
    #
    #   * At most one row, because `ix_forecast_run__single_active` makes more
    #     than one active run unrepresentable. The view does not need a LIMIT and
    #     deliberately has none -- a LIMIT would *hide* a second active row rather
    #     than the index preventing it.
    #   * **Zero rows when nothing is active.** No fallback, no most-recent-run
    #     default. "No current forecast" and "current forecast, possibly stale"
    #     are different answers and a consumer must be able to tell them apart;
    #     a recency fallback would silently serve a superseded run as current.
    #
    # `SELECT *` is what data-model.md declares and is safe here for a mechanical
    # reason: PostgreSQL expands the star at CREATE VIEW time and stores the
    # expanded column list, so a column added to `forecast_run` by a later
    # revision does not silently appear in this view.
    #
    # `WHERE is_active` rather than `WHERE is_active = true`: the column is a NOT
    # NULL boolean, so the two are identical, and the bare form is the same one the
    # index predicate uses -- written differently, a reader would wonder whether
    # the view and the index were filtering the same set.
    op.execute("CREATE VIEW v_active_forecast_run AS SELECT * FROM forecast_run WHERE is_active")

    # --- line_posterior (T038, T039) ----------------------------------------
    #
    # One row per line per run, holding **both** arrays (TR-031, SC-014) -- see
    # the module docstring for why that is the whole of invariant 21.
    op.execute(
        """
        CREATE TABLE line_posterior (
            run_id uuid NOT NULL,
            po_line_id uuid NOT NULL,

            -- TR-073. Copies of the run's shape, and not redundant storage: they
            -- are the columns `fk_line_posterior__run_shape` compares, which is
            -- what turns "as long as its run says" into a referential fact. A
            -- writer cannot lie about them -- the FK refuses a pair that is not
            -- this run's -- and the two `array_length` checks below then compare
            -- each array against a value already proven correct.
            draw_count integer NOT NULL,
            horizon_days integer NOT NULL,

            -- TR-028, TR-068. The posterior predictive delivery duration in days
            -- measured from `forecast_run.as_of_date`, ascending. `double
            -- precision[]`, per §Conventions: probabilities and draws are float,
            -- money and measured quantities are numeric.
            --
            -- Stored sorted rather than sorted on read, because the percentile
            -- convention is `draws[ceil(p * draw_count)]` -- a direct subscript,
            -- one-based, no interpolation (TR-033, OBJ5 VC10). A read-time sort
            -- would be O(n log n) per line per query inside the request-time
            -- compute envelope, and a *forgotten* read-time sort would return a
            -- plausible wrong number with nothing to distinguish it.
            --
            -- The declared size in `double precision[]` is deliberately absent:
            -- **PostgreSQL ignores declared array dimensions entirely**.
            -- `double precision[4000]` documents an intention and enforces
            -- nothing, which is worse than saying nothing, so cardinality lives in
            -- `ck_line_posterior__draws_length` where it is actually checked.
            draws double precision[] NOT NULL,

            -- TR-029. `survival[k] = P(delivery has not occurred by end of day
            -- as_of_date + k)`, for k = 1..horizon_days. A day grid, not a set of
            -- (day, probability) rows: E010 reads one line's whole curve at once
            -- and subscripts it by `need_by_date - as_of_date`, which is one array
            -- access against ~365 joined rows.
            survival double precision[] NOT NULL,

            -- TR-030, OBJ5 VC5. `P(T > horizon_days)` -- the mass beyond the grid,
            -- stored explicitly rather than truncated. This is the column that
            -- makes the horizon's adequacy *visible*: a line whose residual is
            -- large is a line the grid does not cover, and that is the reversal
            -- trigger recorded for SURVIVAL_HORIZON_DAYS. Truncating instead would
            -- silently renormalise the distribution and the beyond-horizon answer
            -- `1 - residual_tail_mass` (TR-053) would be unavailable.
            residual_tail_mass double precision NOT NULL,

            -- TR-040, TR-068. 32 raw bytes over the serialized draws, in the
            -- layout `forecast_run.draw_serialization` names. `bytea` for the same
            -- reason as `artifact_hash`: the digest covers bytes, and a text
            -- rendering would depend on `extra_float_digits` and the session's
            -- locale -- so the same draws would digest differently in two sessions.
            draw_digest bytea NOT NULL,

            CONSTRAINT pk_line_posterior PRIMARY KEY (run_id, po_line_id),

            -- TR-028, TR-073, invariants 18 and 19. The composite FK that carries
            -- both array lengths. Three columns, so it proves three facts at once:
            -- the run exists, and `draw_count` and `horizon_days` on this row are
            -- that run's own values.
            --
            -- `MATCH FULL` as declared, and here it is equivalent to MATCH SIMPLE
            -- rather than a repair of it: all three referencing columns are NOT
            -- NULL, so the partially-null referencing row that MATCH SIMPLE would
            -- silently skip -- and that MATCH FULL would *refuse* -- is
            -- unrepresentable. That is the null pattern worked out explicitly, and
            -- it is why this FK does not repeat `0007`'s
            -- `fk_lifecycle_event__chain` problem: that FK's referencing triple is
            -- partially null on every line's opening event, so MATCH FULL made the
            -- opening event unrepresentable and it had to be MATCH SIMPLE. Here
            -- there is no boundary row to make unrepresentable, because there is
            -- no legal artifact row with an absent run.
            --
            -- ON DELETE CASCADE: artifacts belong to their run, and discarding a
            -- run discards its posteriors (§Referential Actions). ON UPDATE
            -- CASCADE because the parent key includes mutable columns -- correcting
            -- a run's recorded `draw_count` must propagate rather than deadlock
            -- against its own artifact rows.
            CONSTRAINT fk_line_posterior__run_shape
                FOREIGN KEY (run_id, draw_count, horizon_days)
                REFERENCES forecast_run (run_id, draw_count, horizon_days)
                MATCH FULL
                ON DELETE CASCADE
                ON UPDATE CASCADE,

            -- ON DELETE RESTRICT, unlike the run FK: deleting a purchase-order
            -- line that a forecast was fitted for must be an explicit, ordered
            -- operation. A cascade here would let a roster correction quietly
            -- delete part of a published run.
            CONSTRAINT fk_line_posterior__line
                FOREIGN KEY (po_line_id)
                REFERENCES purchase_order_line (po_line_id)
                ON DELETE RESTRICT,

            -- One dimension, subscripts starting at 1.
            --
            -- **Deviation from data-model.md, deliberate and verified (TR-083).**
            -- The declared form is `array_ndims(draws) = 1` alone. The
            -- `array_lower` conjunct is added because PostgreSQL array subscripts
            -- are not required to start at 1: `'[0:3]={0,1,2,3}'::float8[]` is a
            -- legal one-dimensional array of length 4 whose last element is at
            -- subscript 3. Both this table's read conventions subscript directly
            -- -- `draws[ceil(p * draw_count)]` one-based, and
            -- `survival[horizon_days]` in the residual check -- so a lower bound
            -- of 0 would put the final element out of reach while every declared
            -- length check still passed, and `survival[horizon_days]` would be
            -- NULL. Verified: a lower-bound-0 array of the correct length is
            -- rejected by this constraint.
            --
            -- Note what this check does *not* do: `array_ndims('{}')` is NULL, so
            -- an empty array passes here. That is intentional -- the empty array
            -- is owned by `ck_line_posterior__draws_length` alone, so exactly one
            -- constraint is false on that row and the server's report is
            -- deterministic.
            CONSTRAINT ck_line_posterior__draws_1d
                CHECK (array_ndims(draws) = 1 AND array_lower(draws, 1) = 1),

            -- TR-069, invariant 18. Cardinality as a `CHECK`, because the
            -- declared size in the column type enforces nothing.
            --
            -- **Deviation from data-model.md, deliberate and verified (TR-083).**
            -- The declared form is `array_length(draws, 1) = draw_count`.
            -- `array_length('{}'::float8[], 1)` is **NULL, not 0** -- an empty
            -- array has no dimensions, so there is no length to report. `NULL =
            -- draw_count` is NULL, and a `CHECK` rejects only on *false*, so the
            -- declared form **accepts an artifact row with no draws at all**.
            -- Verified by inserting exactly that row. `coalesce(..., 0)` makes the
            -- comparison definite, and `ck_forecast_run__draw_count_positive`
            -- guarantees `draw_count > 0`, so the substituted 0 can never equal
            -- it. The equivalent `array_length(draws, 1) IS NOT NULL AND
            -- array_length(draws, 1) = draw_count` was rejected only because it
            -- states the length twice.
            CONSTRAINT ck_line_posterior__draws_length
                CHECK (coalesce(array_length(draws, 1), 0) = draw_count),

            -- TR-028, TR-070, invariant 20. Sortedness via the IMMUTABLE helper,
            -- because a `CHECK` admits no subquery and element-wise array
            -- validation needs a callable. The function is genuinely immutable --
            -- it reads no table and does no collation-dependent comparison -- so
            -- the check is emitted as validated and a restore re-proves it row by
            -- row.
            --
            -- Unlike `0007`'s `ck_lifecycle_event__legal_transition`, no
            -- `IS NULL OR ...` guard is needed around this STRICT function: the
            -- argument is the NOT NULL `draws` column, so the function is always
            -- called and never yields NULL through strictness. Its *elements* can
            -- be null, and the function returns false for those -- see
            -- `model.schema.helpers`.
            CONSTRAINT ck_line_posterior__draws_sorted
                CHECK (fn_is_sorted_ascending(draws)),

            -- A delivery duration measured forward from `as_of_date` cannot be
            -- negative. One subscript is enough because the array is sorted: the
            -- smallest element is `draws[1]`, so this rejects any negative draw
            -- anywhere. `draws[1]` is in range because
            -- `ck_line_posterior__draws_length` forces at least one element (with
            -- `draw_count > 0`) and `ck_line_posterior__draws_1d` pins the lower
            -- bound to 1.
            --
            -- Written as declared, with no `coalesce`, and the null branch is
            -- closed elsewhere rather than left open: `draws[1]` could be NULL
            -- only for an array with a null first element, which
            -- `ck_line_posterior__draws_sorted` refuses -- including the
            -- single-element `'{NULL}'` case that no adjacent-pair comparison
            -- reaches, which is precisely why `fn_is_sorted_ascending` tests every
            -- element for null rather than only the pairs. Registered as a
            -- "null branch closed by a sibling check", not as a check that can
            -- pass vacuously.
            CONSTRAINT ck_line_posterior__draws_non_negative
                CHECK (draws[1] >= 0.0),

            -- One dimension, subscripts from 1. Same deviation and the same
            -- reasoning as `ck_line_posterior__draws_1d`, and here the lower bound
            -- is load-bearing twice over: `ck_line_posterior__residual_matches_grid_tail`
            -- reads `survival[horizon_days]` directly, and on a lower-bound-0
            -- array of length `horizon_days` that subscript is past the end and
            -- yields NULL -- which would make the residual agreement check NULL
            -- and therefore accepted.
            CONSTRAINT ck_line_posterior__survival_1d
                CHECK (array_ndims(survival) = 1 AND array_lower(survival, 1) = 1),

            -- TR-072, invariant 19. The day grid is exactly as long as the run's
            -- horizon -- proven against the run row by
            -- `fk_line_posterior__run_shape`, so this is not a comparison against
            -- a number the writer chose. Same `coalesce` deviation as the draws
            -- length check, and the same reason: `array_length('{}', 1)` is NULL,
            -- and `ck_forecast_run__horizon_positive` guarantees the target is
            -- positive so 0 can never match it.
            CONSTRAINT ck_line_posterior__survival_length
                CHECK (coalesce(array_length(survival, 1), 0) = horizon_days),

            -- TR-029. A survival curve cannot rise: a delivery does not un-happen.
            -- Ties allowed, and common -- a day with no probability mass leaves the
            -- curve flat.
            CONSTRAINT ck_line_posterior__survival_monotone
                CHECK (fn_is_non_increasing(survival)),

            -- TR-029. Every element a probability, inclusive at both ends: 1.0 on
            -- day 1 and 0.0 once all mass is consumed are both ordinary. This is
            -- also the check that makes every element of `survival` a definite
            -- number -- the helper refuses a NULL element and, because `NaN <= 1.0`
            -- is false, a NaN one -- which is what lets the residual check below
            -- be the plain comparison data-model.md declares.
            CONSTRAINT ck_line_posterior__survival_unit_interval
                CHECK (fn_all_within_unit_interval(survival)),

            -- TR-030. A probability.
            CONSTRAINT ck_line_posterior__residual_range
                CHECK (residual_tail_mass >= 0.0 AND residual_tail_mass <= 1.0),

            -- TR-030, TR-055, SC-015, invariant 22. **The array and the residual
            -- account for the full distribution, to a tolerance.**
            --
            -- `abs(a - b) <= 1e-9`, never `a = b`. Both operands are `double
            -- precision`, and exact equality between two independently computed
            -- binary floats is a coin flip on the last bit: the producer computes
            -- `residual_tail_mass` from the draws by its own path, not by copying
            -- `survival[horizon_days]`, which is exactly what makes this a genuine
            -- agreement test between two computations rather than a tautology --
            -- and exactly why it cannot be an equality. Written as an equality
            -- this check would reject correct data on the bit, intermittently, in
            -- a way no test would reliably reproduce.
            --
            -- `1e-9` is PROB_SUM_TOLERANCE (§Declared Constants), one of only two
            -- constants duplicated as a DDL literal; T050 asserts this literal
            -- against the published `schema_constants` row, and per TR-076 the
            -- literal governs and a mismatch is repaired in the row. Both compared
            -- quantities are `count / draw_count` ratios, so realised error is
            -- ~1e-16 and the tolerance is about seven orders of magnitude slack --
            -- deliberately, so that a failure here means a wrong computation and
            -- not float noise.
            --
            -- No `coalesce`, and the null branch is closed by construction rather
            -- than left open: `survival[horizon_days]` is in subscript range
            -- because `ck_line_posterior__survival_length` fixes the length and
            -- `ck_line_posterior__survival_1d` fixes the lower bound at 1, and the
            -- element is non-null because `ck_line_posterior__survival_unit_interval`
            -- refuses null elements. Both operands are therefore definite doubles
            -- and this check can never evaluate to NULL. Registered as a "null
            -- branch closed by sibling checks".
            CONSTRAINT ck_line_posterior__residual_matches_grid_tail
                CHECK (abs(survival[horizon_days] - residual_tail_mass) <= 1e-9),

            -- 32 bytes = SHA-256, counted in bytes on a `bytea`.
            CONSTRAINT ck_line_posterior__draw_digest_length
                CHECK (octet_length(draw_digest) = 32)
        )
        """
    )

    # E010 reads one line's posterior across runs, and the primary key is
    # `(run_id, po_line_id)` -- leading with `run_id`, so it cannot serve a
    # `po_line_id` lookup. PostgreSQL indexes neither side of a foreign key
    # automatically, so this also keeps `fk_line_posterior__line`'s RESTRICT check
    # from scanning the table on every line delete.
    op.execute("CREATE INDEX ix_line_posterior__po_line ON line_posterior (po_line_id)")


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only.

    TR-002. Kept as a raising stub rather than deleted, because Alembic calls
    this attribute when a downgrade is requested and a missing one would fail
    with an unexplained AttributeError instead of stating the policy.
    """
    raise NotImplementedError(
        "This migration is forward-only (TR-002) and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
