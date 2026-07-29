"""forecast run provenance

Revision ID: 0300
Revises: 0103
Create Date: 2026-07-27

E007's first revision, and the first in the `0300`-`0399` block {SAD:ADR-0013}
reserves for it, claimed at epic start per Governance. E003 owns this directory,
the Alembic configuration and the runner; E007 authors revisions into it and
builds no tooling of its own.

**`down_revision` is `0103`, not `0010`.** The prefix block protects *filenames*;
it says nothing about Alembic's revision graph, which is ordered by
`down_revision` and only by `down_revision`. Chaining off E003's `0010` because
`0300` "comes after" it numerically would give the directory two heads --
`0103` and `0300` -- and `alembic upgrade head` would then refuse to choose.
E004's `0103` is the head at the moment this revision lands; if a sibling
Wave-4 epic lands first, this revision is **re-parented** onto that epic's head
rather than renumbered, because the block is E007's regardless of chain
position.

**What this revision adds.** One immutable helper, `fn_vendor_shrinkage_wellformed`,
and the fourteen columns `data-model.md` § Additions to `forecast_run` declares.
Every one is a single-valued fact about one fit, which is why they ride on the
run row rather than on a 1:1 side table: a side table would put a run's identity
in two places and make every consumer join to read a manifest.

**Why this revision requires an empty `forecast_run`, and why it says so itself.**
`ALTER TABLE ... ADD COLUMN ... NOT NULL` with no default is refused by
PostgreSQL on a populated table, and a default is not available: the delivered
TR-063 audit (`test_no_column_outside_the_enumerated_set_carries_a_default`)
admits defaults on an enumerated six columns and none of the fourteen is one of
them. So the precondition is real and unavoidable, and it is checked here rather
than left to the server -- a not-null violation on a column that has existed for
three milliseconds names the *symptom*, and an operator reading it has to
reconstruct which of the fourteen statements failed and why a NULL appeared in a
column nobody wrote to. `ForecastRunNotEmptyError` names the actual condition and
carries the remediation. This is **FR-036**; the disclosed gap is **G-2**.

**The remediation, when the precondition eventually fails.** A database holding
runs takes the three-step route instead: add each column nullable, backfill it,
then `SET NOT NULL`. That is a *different* forward revision in E007's block, not
an edit to this one -- a revision that has been applied anywhere is history.
Nothing here attempts it, because attempting it would mean inventing values for
fourteen provenance fields of a fit nobody recorded, and a fabricated provenance
field is precisely what Principle I forbids.

Forward-only: `downgrade()` raises, and dropping these columns would in any case
discard every manifest field E007 adds, which a run written under them cannot be
reconstructed without.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0300"
down_revision: str | Sequence[str] | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class ForecastRunNotEmptyError(RuntimeError):
    """`forecast_run` holds rows, so the fourteen NOT NULL columns cannot be added.

    A named type rather than a bare `RuntimeError` so the condition is
    matchable: the test that asserts this guard (FR-036's failing direction)
    identifies the refusal by its type, never by its message text, and a caller
    that wants to distinguish "this migration refused a precondition" from "the
    server refused a statement" can.
    """


#: The precondition, read before any DDL is issued. `count(*)` rather than
#: `EXISTS`: the message states how many rows stand in the way, which is the
#: difference between an operator deleting one stray fixture row and one
#: discovering the database is in production use.
EXISTING_RUN_COUNT = sa.text("SELECT count(*) FROM forecast_run")


#: Creates `fn_vendor_shrinkage_wellformed(jsonb) -> boolean`, called by
#: `ck_forecast_run__vendor_shrinkage_shape` (FR-019, SC-004).
#:
#: Declared inline here rather than added to `model.schema.helpers`, following
#: `0003`'s `fn_all_sha256_prefixed`: that module is E003's, its docstring
#: enumerates E003's helper set, and a sixth constant in it would make an E003
#: module the declaration site for an E007 object. **DV-026** counts
#: `CREATE FUNCTION` occurrences across E007's revision sources and requires
#: exactly one; the three array helpers this epic's other revisions call are
#: *called*, never re-declared.
#:
#: **Why a function at all.** A `CHECK` admits no subquery, so member-wise
#: validation of a container needs an `IMMUTABLE` callable -- the same reason
#: `fn_all_sha256_prefixed` and the array helpers exist. `IMMUTABLE STRICT
#: PARALLEL SAFE`, arguments only: no lookup, no `current_setting`, no
#: collation-dependent comparison. Every function it calls is itself immutable
#: (`jsonb_each`, `jsonb_typeof`, `jsonb_object_keys`, `->`), which is what makes
#: the declared volatility true rather than merely asserted -- `jsonb_build_object`
#: would have been the natural way to spell the "exactly these three keys" test
#: and is deliberately avoided, because it is `STABLE`.
#:
#: **Why the outer `CASE`.** `jsonb_each` *raises* on a scalar or an array rather
#: than returning no rows, and `AND` in PostgreSQL is not a short-circuit
#: guarantee. Settling the container's own type in a `CASE` arm is what turns
#: `fn_vendor_shrinkage_wellformed('5'::jsonb)` into `false` instead of
#: `cannot deconstruct a scalar`. A JSON `null` is likewise refused here and not
#: by the column's `NOT NULL` -- a JSON null is not a SQL NULL, so
#: `vendor_shrinkage = 'null'::jsonb` would otherwise be a legal "recorded"
#: shrinkage set, exactly as `ck_forecast_run__library_versions_shape` records
#: for its own column.
#:
#: **Why `IS NOT TRUE` and not `NOT (...)`.** The inner predicate can evaluate to
#: NULL -- `jsonb_typeof(weight -> 'median')` is NULL when the key is absent --
#: and `NOT NULL` is NULL, which a `WHERE` treats as no row. The member would
#: then be *accepted* by the very expression written to refuse it. `IS NOT TRUE`
#: collapses NULL and false together, which is the same idiom
#: `fn_all_within_unit_interval` uses for the same trap.
#:
#: **Why the members are compared as `jsonb` and never cast.** `weight ->>
#: 'median'` on a JSON string yields text that `::double precision` would refuse
#: with a hard error rather than a false, and the type test guarding the cast
#: would again be an `AND` that is not guaranteed to run first. `jsonb`'s own
#: ordering compares two JSON numbers numerically, so `weight -> 'hpdi_low' <=
#: weight -> 'median'` is the comparison meant, and it raises on nothing.
#:
#: **What it cannot do**: shape, never membership. A `CHECK` cannot read
#: `purchase_order_line`, and E007 may not hard-code E001's twelve vendor
#: identifiers into DDL, so "all twelve vendors are present" is **DV-009** and
#: **G-9** rather than a constraint.
FN_VENDOR_SHRINKAGE_WELLFORMED = """
CREATE FUNCTION fn_vendor_shrinkage_wellformed(p_weights jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN jsonb_typeof(p_weights) <> 'object' THEN false
        -- At least one member. An empty object records nothing and would
        -- satisfy every member-wise test below vacuously.
        WHEN p_weights = '{}'::jsonb THEN false
        ELSE NOT EXISTS (
            SELECT 1
            FROM jsonb_each(p_weights) AS member (vendor_id, weight)
            WHERE CASE
                WHEN member.vendor_id !~ '^VND-[0-9]{3}$' THEN true
                WHEN jsonb_typeof(member.weight) <> 'object' THEN true
                ELSE (
                    -- Exactly `median`, `hpdi_low`, `hpdi_high`: three keys in
                    -- total, and all three named ones present as numbers. The
                    -- count is what forbids a fourth key; without it a member
                    -- could carry an unvalidated extra field.
                    (SELECT count(*) FROM jsonb_object_keys(member.weight) AS present (key)) = 3
                    AND jsonb_typeof(member.weight -> 'median') = 'number'
                    AND jsonb_typeof(member.weight -> 'hpdi_low') = 'number'
                    AND jsonb_typeof(member.weight -> 'hpdi_high') = 'number'
                    -- Each in [0, 1]. Stated for all three rather than left to
                    -- follow from the ordering below, because the requirement
                    -- states it of each and a reader should not have to derive
                    -- two of them.
                    AND member.weight -> 'median' >= '0'::jsonb
                    AND member.weight -> 'median' <= '1'::jsonb
                    AND member.weight -> 'hpdi_low' >= '0'::jsonb
                    AND member.weight -> 'hpdi_low' <= '1'::jsonb
                    AND member.weight -> 'hpdi_high' >= '0'::jsonb
                    AND member.weight -> 'hpdi_high' <= '1'::jsonb
                    -- The interval contains its own point estimate. An HPDI
                    -- reported the wrong way round is the failure a bare range
                    -- check cannot see.
                    AND member.weight -> 'hpdi_low' <= member.weight -> 'median'
                    AND member.weight -> 'median' <= member.weight -> 'hpdi_high'
                ) IS NOT TRUE
            END
        )
    END
$$
"""


#: The fourteen columns, in `data-model.md`'s declared order, as one statement.
#:
#: One `ALTER TABLE` with fourteen `ADD COLUMN` clauses rather than fourteen
#: statements: PostgreSQL applies them in a single pass over the table's
#: metadata, and -- more to the point -- the fourteen are one indivisible
#: addition. A chain that could stop between the seventh and the eighth would
#: describe a manifest half of whose fields exist, which no reader and no
#: constraint could interpret.
#:
#: Every constraint is named, following E003's `ck_<table>__<rule>`. A
#: server-generated name cannot be relied on by a later forward migration's
#: `DROP CONSTRAINT`, and a test asserting *which* rule rejected a row matches on
#: the constraint name -- never on message text, which is locale- and
#: version-dependent.
#:
#: TR-039: every check below sits on a `NOT NULL` column, so none can pass
#: vacuously on a NULL. TR-063: not one of the fourteen carries a `DEFAULT`,
#: which is what makes the empty-table guard above load-bearing rather than
#: precautionary.
#:
#: **Recorded deviation from `data-model.md`, following `0004`, `0006`, `0007`
#: and `0008`.** The artifact spells the whitespace trim set with a trailing `\\v`
#: (rendered there as a line break inside the literal). PostgreSQL's
#: escape-string syntax has no `\\v`: an unrecognized escape drops the backslash
#: and keeps the character, so `E'\\v'` is the *letter* `v` -- the check would
#: then admit a vertical-tab-only value and reject a legitimate value of `vvv`.
#: `\\u000B` is the character the artifact means, and it is the spelling every
#: delivered revision already uses.
ADD_PROVENANCE_COLUMNS = """
ALTER TABLE forecast_run
    -- FR-002, SC-006. The covariate set the fit's design matrix carried, as a
    -- list rather than a count: SC-006 is about *which* covariates, and a
    -- number answers a different question. Shape only -- three plausible
    -- strings satisfy this check whatever the fit used, which is why DV-036
    -- compares the recorded list against the design matrix itself.
    --
    -- Three failures, one check: an empty list records nothing; a NULL element
    -- is a covariate whose name is missing, which `text[] NOT NULL` does not
    -- refuse on its own; an all-blank list satisfies both of those and still
    -- names nothing.
    ADD COLUMN covariate_names text[] NOT NULL
        CONSTRAINT ck_forecast_run__covariates_non_empty
        CHECK (
            cardinality(covariate_names) >= 1
            AND array_position(covariate_names, NULL) IS NULL
            AND btrim(array_to_string(covariate_names, ''), E' \\t\\n\\r\\f\\u000B') <> ''
        ),

    -- FR-029, SC-013. What an open line's draw *means*, recorded on the run
    -- because the open population lives in the delivered `line_posterior`,
    -- which E007 may not alter. Exact rather than approximate: the semantic is
    -- a property of the run's whole open-line set, not of any one row. The
    -- held-out counterpart is per row, on `held_out_prediction`.
    ADD COLUMN open_line_draw_semantic text NOT NULL
        CONSTRAINT ck_forecast_run__open_line_semantic
        CHECK (
            open_line_draw_semantic = 'conditional_remaining_duration_from_run_as_of_date'
        ),

    -- FR-042, FR-023, SC-020. E005's published `dataset_content_hash` for the
    -- committed fixture file, observed at run time. A *second* digest and not a
    -- second use of `input_data_hash`: that column holds the hash of the rows
    -- the fit read, and the distinction is what lets FR-023 separate a refusal
    -- (the rows moved) from a provenance warning (only the chain back to the
    -- upstream artifact broke). The same `sha256:` + 64 lowercase hex literal
    -- `ck_forecast_run__input_hash_format` already pins.
    ADD COLUMN input_fixture_digest text NOT NULL
        CONSTRAINT ck_forecast_run__fixture_digest_format
        CHECK (input_fixture_digest ~ '^sha256:[0-9a-f]{64}$'),

    -- FR-045, SC-020. REAL or SYNTHETIC, the two layers the corpus manifest
    -- declares. `text` + `CHECK` rather than a native `ENUM`, per E003's
    -- convention for every closed value set in this schema.
    ADD COLUMN input_layer text NOT NULL
        CONSTRAINT ck_forecast_run__input_layer
        CHECK (input_layer IN ('REAL', 'SYNTHETIC')),

    -- FR-045, SC-020. The datasheet disclosing the input's generative
    -- assumptions. Blank is refused rather than tolerated: a datasheet
    -- reference that names nothing is the unattributable figure Principle I
    -- exists to prevent, one hop up.
    ADD COLUMN input_datasheet_ref text NOT NULL
        CONSTRAINT ck_forecast_run__datasheet_ref_present
        CHECK (btrim(input_datasheet_ref, E' \\t\\n\\r\\f\\u000B') <> ''),

    -- FR-014, FR-005, SC-020. The byte-level serialization the two recomputable
    -- digests are defined over -- sorted keys, no whitespace, UTF-8. Named on
    -- the row for the same reason `draw_serialization` is: a digest whose input
    -- encoding is unrecorded is a number nobody can reproduce.
    ADD COLUMN canonical_serialization text NOT NULL
        CONSTRAINT ck_forecast_run__canonical_serialization
        CHECK (canonical_serialization = 'canonical-json-sorted-keys-utf8'),

    -- FR-043, SC-009. The split's root entropy, verbatim, as decimal digits --
    -- the same form and the same 1-to-39-digit range `seed_entropy` uses, and
    -- for the same reason: 128 bits does not fit in `bigint` and nothing ever
    -- does arithmetic on it. A separate column from `seed_entropy` because the
    -- split seed is a committed constant (AD-005) while the sampler's entropy
    -- is per run; conflating them would make a re-fit reshuffle the split.
    ADD COLUMN split_seed_entropy text NOT NULL
        CONSTRAINT ck_forecast_run__split_seed_format
        CHECK (split_seed_entropy ~ '^[0-9]{1,39}$'),

    -- FR-005, FR-006, FR-023, SC-012. The digest over the serialized split
    -- assignment, recomputable from `forecast_split_assignment` alone (DV-017).
    ADD COLUMN split_assignment_hash text NOT NULL
        CONSTRAINT ck_forecast_run__split_hash_format
        CHECK (split_assignment_hash ~ '^sha256:[0-9a-f]{64}$'),

    -- FR-005, FR-028. The fraction the run *declared* before the split was
    -- drawn, from the committed constant. Strictly inside (0, 1): a run that
    -- held nothing out has no held-out population, and one that held everything
    -- out has no training set.
    ADD COLUMN held_out_fraction_declared double precision NOT NULL
        CONSTRAINT ck_forecast_run__declared_fraction_range
        CHECK (held_out_fraction_declared > 0.0 AND held_out_fraction_declared < 1.0),

    -- FR-006, SC-012. What the split actually realized, which is a measurement
    -- and not a restatement of the line above -- stratified assignment over a
    -- finite line set lands near the declared fraction, not on it. The bounds
    -- are inclusive here precisely because the realized value is measured:
    -- refusing 0.0 would turn a reportable degenerate split into an
    -- unrepresentable one, and DV-007 is where the two are compared.
    ADD COLUMN held_out_fraction_realized double precision NOT NULL
        CONSTRAINT ck_forecast_run__realized_fraction_range
        CHECK (held_out_fraction_realized >= 0.0 AND held_out_fraction_realized <= 1.0),

    -- FR-006, FR-028, SC-012, SC-025. The count of *uncensored* held-out
    -- events -- the quantity that governs how precisely anything can be
    -- calibrated, published so a reader can judge the claim rather than take
    -- it. Zero is legal and is the case worth reporting, so `>= 0`.
    ADD COLUMN held_out_uncensored_event_count integer NOT NULL
        CONSTRAINT ck_forecast_run__held_out_events_non_negative
        CHECK (held_out_uncensored_event_count >= 0),

    -- FR-019, SC-004. Per-vendor shrinkage, each weight a `{median, hpdi_low,
    -- hpdi_high}` object and never a bare number: rho_j is a plug-in of two
    -- fitted parameters, so it has a posterior of its own, and Principle II
    -- refuses a point estimate of a quantity that is itself uncertain --
    -- reported at exactly the sparse-vendor end where the uncertainty is
    -- largest.
    --
    -- One `jsonb` and not twelve columns: E001 owns the vendor roster, and a
    -- column list would make a thirteenth vendor a migration. Not a fourth
    -- table either -- the value is read whole, once per run, by a report, and
    -- is never filtered, joined or aggregated.
    ADD COLUMN vendor_shrinkage jsonb NOT NULL
        CONSTRAINT ck_forecast_run__vendor_shrinkage_shape
        CHECK (fn_vendor_shrinkage_wellformed(vendor_shrinkage)),

    -- FR-021, SC-017. **The one refusal that does not depend on the job
    -- behaving correctly**: a run whose forecast set is empty cannot be
    -- represented at all. Every other refusal in FR-017 is carried by ordering
    -- and by transaction 1.
    ADD COLUMN open_line_count integer NOT NULL
        CONSTRAINT ck_forecast_run__open_line_count_positive
        CHECK (open_line_count > 0),

    -- FR-007. A fit with no training line is not a fit.
    ADD COLUMN training_line_count integer NOT NULL
        CONSTRAINT ck_forecast_run__training_line_count_positive
        CHECK (training_line_count > 0)
"""


def upgrade() -> None:
    """Refuse on a populated table, then create the helper and the columns.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. The count below is **not** a "have I already run?" guard and
    must not be read as one -- it is a precondition on the data, and it would
    refuse just as loudly on a first run against a database holding runs.

    Order is forced twice over. The guard runs before any DDL, so a refusal
    leaves the schema exactly as it found it rather than relying on the
    transaction to undo half of it. The function is created before the columns,
    because a `CHECK` referencing a missing function fails at DDL time.
    """
    connection = op.get_bind()

    existing_runs = connection.execute(EXISTING_RUN_COUNT).scalar_one()
    if existing_runs:
        raise ForecastRunNotEmptyError(
            f"revision 0300 adds fourteen NOT NULL columns to `forecast_run` and "
            f"the table holds {existing_runs} row(s). None of the fourteen may carry a "
            f"DEFAULT -- the delivered TR-063 audit admits defaults on an enumerated six "
            f"columns and none of these is one of them -- so PostgreSQL has no value to "
            f"give the existing rows and this revision cannot be applied as written. "
            f"Either empty `forecast_run` (every row is a forecast artifact and deleting "
            f"one cascades to its posteriors), or author a new forward revision in the "
            f"0300-0399 block that adds each column nullable, backfills it, and then "
            f"issues SET NOT NULL. Do not edit this file: a revision that has been "
            f"applied anywhere is history. See data-model.md gap G-2."
        )

    op.execute(FN_VENDOR_SHRINKAGE_WELLFORMED)
    op.execute(ADD_PROVENANCE_COLUMNS)


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup. Dropping these fourteen columns "
        "discards every manifest field E007 records, and a run written under "
        "them cannot be reconstructed from the columns that remain."
    )
