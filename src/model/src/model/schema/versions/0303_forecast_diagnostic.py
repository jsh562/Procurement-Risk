"""forecast diagnostic

Revision ID: 0303
Revises: 0302
Create Date: 2026-07-27

`forecast_diagnostic` -- convergence evidence, each observed value stored beside
the threshold it was judged against (FR-016, FR-017, FR-018).

**One table rather than two.** Per-parameter and run-level diagnostics answer the
same question -- *what was measured, against what bar, and did it clear* -- and
splitting them would duplicate five columns to avoid one discriminator. The
discriminator is `diagnostic_scope`, and `ck_forecast_diagnostic__metric_matches_scope`
ties it to the metric so the two cannot disagree: a per-parameter divergence
count is not a quantity.

**A stored run breached no blocking threshold, and that is enforced rather than
asserted.** `ck_forecast_diagnostic__blocking_rows_passed` refuses a failing
blocking row outright; combined with `fk_forecast_diagnostic__run`, a
non-converged fit has nowhere to put its evidence and no run row to attach it
to. The cost is that a *refused* run leaves no diagnostic rows at all -- which
SC-015 requires, and which is disclosed as **G-8**: the evidence of *why* a run
refused lives in the job's non-zero exit output and its emitted report file.

**`parameter_name` is the only nullable column this epic declares**, and it
carries **two** checks rather than one, deliberately split:

* `ck_forecast_diagnostic__parameter_iff_parameter_scope` decides *whether* a
  null is permitted -- a biconditional against the NOT NULL closed-set
  `diagnostic_scope`, with the nullable column appearing only inside a null
  *test*, so the expression is definite on every row.
* `ck_forecast_diagnostic__parameter_name_present` owns the *value domain* only,
  and closes its null branch with a leading `IS NULL` that short-circuits before
  the value position is reached.

Folding them into one check would produce a constraint that is either vacuous on
a null or forbids an absence the requirements need, and would lose the ability to
say which of the two rules a row broke. Both are recorded in `data-model.md`'s
**Nullable-column checks** table, which is what the delivered TR-039 audit reads.

**`NULLS NOT DISTINCT` on the natural key is load-bearing.** Under PostgreSQL's
default `NULLS DISTINCT` -- the behaviour E003 deliberately relies on in
`resolved_entity_member` -- two run-scope rows for one metric would both be
accepted, because their `parameter_name` nulls never collide, and a run could
record its divergence count twice with two different values.

**`observed_value` rejects NaN and both infinities at its own check.** A diverged
sampler can produce a NaN R-hat; `NaN <= 1.01` is false in PostgreSQL, so
`passed` would correctly be false and `ck_forecast_diagnostic__blocking_rows_passed`
would refuse the row -- which is right but reports the wrong reason. Refusing the
non-finite value where it happens names the actual defect.

No secondary index: `uq_forecast_diagnostic__run_metric_parameter` leads with
`run_id`, which serves every read this table has. No column carries a `DEFAULT`
(TR-063), no constraint is deferrable (TR-051), and no trigger is declared.
Forward-only: `downgrade()` raises.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0303"
down_revision: str | Sequence[str] | None = "0302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The table, whole, with every constraint declared in the same statement so no
#: row can predate any check.
#:
#: **Recorded deviation from `data-model.md`, following `0004`, `0006`, `0007`,
#: `0008` and `0300`.** The artifact spells the whitespace trim set with a
#: trailing `\\v`. PostgreSQL's escape-string syntax has no `\\v`: an
#: unrecognized escape drops the backslash and keeps the character, so `E'\\v'`
#: is the *letter* `v` -- the check would then admit a vertical-tab-only value
#: and reject a legitimate value of `vvv`. `\\u000B` is the character the
#: artifact means, and it is the spelling every delivered revision uses.
CREATE_FORECAST_DIAGNOSTIC = """
CREATE TABLE forecast_diagnostic (
    -- A surrogate key, because the natural key includes the nullable
    -- `parameter_name` and a primary key admits no null.
    diagnostic_id uuid NOT NULL,

    run_id uuid NOT NULL,

    diagnostic_scope text NOT NULL,

    -- **The only nullable column this epic declares.** Present iff the scope is
    -- `parameter`; see the module docstring for why the two rules governing it
    -- are two constraints.
    parameter_name text,

    metric text NOT NULL,

    observed_value double precision NOT NULL,
    threshold_value double precision NOT NULL,
    threshold_direction text NOT NULL,

    -- Neither of these is a judgement the writer gets to make: both are pinned
    -- to the row's own other columns by the agreement checks below.
    is_blocking boolean NOT NULL,
    passed boolean NOT NULL,

    CONSTRAINT pk_forecast_diagnostic PRIMARY KEY (diagnostic_id),

    -- The natural key. `NULLS NOT DISTINCT` is the whole point -- see the
    -- module docstring. Also the access path for every read this table has
    -- ("the diagnostics of run X", "the blocking set of run X"), which is why
    -- no secondary index is declared: one would be an index nothing uses, and
    -- E003's audit would require it documented for that.
    CONSTRAINT uq_forecast_diagnostic__run_metric_parameter
        UNIQUE NULLS NOT DISTINCT (run_id, metric, parameter_name),

    -- Evidence belongs to its run and dies with it.
    CONSTRAINT fk_forecast_diagnostic__run
        FOREIGN KEY (run_id)
        REFERENCES forecast_run (run_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    CONSTRAINT ck_forecast_diagnostic__scope
        CHECK (diagnostic_scope IN ('parameter', 'run')),

    -- The biconditional. `diagnostic_scope` is NOT NULL and closed-set, and
    -- `parameter_name` appears only inside a null *test*, so this expression is
    -- a definite boolean on every row -- including the rows it refuses.
    CONSTRAINT ck_forecast_diagnostic__parameter_iff_parameter_scope
        CHECK ((diagnostic_scope = 'parameter') = (parameter_name IS NOT NULL)),

    -- The value domain, and nothing else. The `IS NULL` branch short-circuits
    -- before the value position is reached, so the expression is `true` on a
    -- null rather than NULL-valued; permitted absence is owned by the
    -- biconditional above. The trim set is spelled out because single-argument
    -- `btrim` strips spaces only, so a parameter name of one tab would satisfy
    -- a bare `btrim(parameter_name) <> ''` while naming nothing.
    CONSTRAINT ck_forecast_diagnostic__parameter_name_present
        CHECK (
            parameter_name IS NULL
            OR btrim(parameter_name, E' \\t\\n\\r\\f\\u000B') <> ''
        ),

    -- The six metrics, three per-parameter and three run-level. `text` +
    -- `CHECK` rather than a native `ENUM`, per E003's convention.
    CONSTRAINT ck_forecast_diagnostic__metric
        CHECK (
            metric IN (
                'r_hat', 'ess_bulk', 'ess_tail',
                'divergent_transitions', 'ebfmi', 'max_treedepth_hits'
            )
        ),

    -- Not NaN, not either infinity. `observed_value = observed_value` is the
    -- standard NaN test: PostgreSQL's `double precision` sorts NaN as largest
    -- but compares it unequal to itself.
    CONSTRAINT ck_forecast_diagnostic__observed_finite
        CHECK (
            observed_value = observed_value
            AND observed_value <> 'Infinity'::double precision
            AND observed_value <> '-Infinity'::double precision
        ),

    CONSTRAINT ck_forecast_diagnostic__direction
        CHECK (threshold_direction IN ('max', 'min')),

    -- The three per-parameter metrics occur only at parameter scope and the
    -- three run metrics only at run scope. A biconditional rather than two
    -- implications, so neither direction can be satisfied vacuously.
    CONSTRAINT ck_forecast_diagnostic__metric_matches_scope
        CHECK (
            (metric IN ('r_hat', 'ess_bulk', 'ess_tail'))
            = (diagnostic_scope = 'parameter')
        ),

    -- Direction is a function of the metric, so a row cannot record E-BFMI as a
    -- ceiling and thereby make a breach read as a pass.
    CONSTRAINT ck_forecast_diagnostic__direction_matches_metric
        CHECK (
            (threshold_direction = 'min')
            = (metric IN ('ess_bulk', 'ess_tail', 'ebfmi'))
        ),

    -- **FR-018 as a database fact.** Treedepth is reported and never blocking;
    -- the other five always block. Neither classification can be edited row by
    -- row.
    CONSTRAINT ck_forecast_diagnostic__blocking_matches_metric
        CHECK (is_blocking = (metric <> 'max_treedepth_hits')),

    -- `passed` is arithmetic, not an opinion. A row cannot claim a pass its own
    -- two numbers refute.
    CONSTRAINT ck_forecast_diagnostic__passed_matches_threshold
        CHECK (
            passed = CASE
                WHEN threshold_direction = 'max' THEN observed_value <= threshold_value
                ELSE observed_value >= threshold_value
            END
        ),

    -- **A stored run breached no blocking threshold.** See the module
    -- docstring, and G-8 for what this deliberately costs.
    CONSTRAINT ck_forecast_diagnostic__blocking_rows_passed
        CHECK (NOT is_blocking OR passed)
)
"""

#: FR-034, the same grant `0301` and `0302` make and for the same reasons.
GRANT_TO_APPLICATION_ROLE = "GRANT SELECT, INSERT, DELETE ON forecast_diagnostic TO procurement_app"


def upgrade() -> None:
    """Create the table with its natural key, then the application role's grant.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.
    """
    op.execute(CREATE_FORECAST_DIAGNOSTIC)
    op.execute(GRANT_TO_APPLICATION_ROLE)


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup. Dropping `forecast_diagnostic` "
        "discards the only stored evidence that a published run's sampler "
        "converged, which no other column records."
    )
