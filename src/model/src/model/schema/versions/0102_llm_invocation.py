"""llm invocation

Revision ID: 0102
Revises: 0101
Create Date: 2026-07-26

The record every traced model call produces (TR-012). One row per *invocation*,
never one per attempt — the two attempt counts are the only per-attempt
information stored, and no attempt-level outcome value exists anywhere in this
schema (TR-042).

**Column names follow a pinned convention** (TR-013, TR-070). The
`gen_ai_`-prefixed columns take their spellings from the OpenTelemetry
generative-AI semantic conventions at version **1.37.0**, transformed by
lowercasing and replacing `.` with `_`. The pin is mirrored in the
`COMMENT ON TABLE` below so a database inspected without this repository still
states which convention release its column names follow, and
`gateway/tests/test_field_naming.py` asserts that the comment, the gateway
configuration key, and TR-070 all agree.

**The pin was corrected while writing this file, which is why it exists.**
TR-070 originally read `1.36.0`, "selected as the version carrying
`gen_ai.provider.name`". The registry published at tag `v1.36.0` defines
`gen_ai.system`, not `gen_ai.provider.name`; the latter first appears at
`v1.37.0`, which marks `gen_ai.system` deprecated and replaced by it. Reading
the pinned document before writing this table is what caught it — had the
column been named from the requirement's prose rather than from the release it
named, this table would carry a column whose spelling no pinned version
defines.

**A gateway-local column carries no `gen_ai_` prefix** (TR-071), so the prefix
is a reliable signal of which set a column belongs to rather than a decorative
convention. `cache_write_input_tokens` and `cache_read_input_tokens` are
gateway-local: no cached-input-tokens attribute exists at 1.36.0 or 1.37.0,
checked rather than assumed.

**No native `ENUM`** (IP-005). `outcome`, `resolution_mode` and
`cost_absent_reason` are `text` with named `CHECK`s. Two reasons: `CREATE TYPE`
has no `IF NOT EXISTS` form in PostgreSQL 16, making an enum type the one DDL
object that cannot satisfy TR-050's re-runnable-file rule; and E013 reads these
enumerations as a contract, which a `CHECK` exposes without type introspection.

**Nothing here is computed by the database.** No generated column, no default
that mints a value, no view performing arithmetic. `invocation_id` and
`created_at` are gateway-generated and the reasons are recorded at their
columns; cost arrives already computed by a pure Python module behind the
computation-boundary contract (TR-028, TR-032, Principle V).

**Constraint names are unabbreviated**, unlike E003's `pol` and `rem`. The
longest here — `ck_llm_invocation__cache_write_input_tokens_non_negative` at 56
bytes — clears PostgreSQL's 63-byte identifier limit, so no abbreviation is
declared and none is needed. A silently truncated name is one a test can never
match, which is why the margin was checked rather than assumed.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0102"
down_revision: str | Sequence[str] | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: TR-070's pin, mirrored into the table comment. One of exactly three places
#: this value is written — the others are `gateway.config.
#: OTEL_GENAI_SEMCONV_VERSION` and TR-070 itself — and T048 asserts all three
#: agree, so a bump that updates one and forgets another fails the build rather
#: than leaving a database whose comment describes a different convention from
#: the code that wrote its rows.
OTEL_GENAI_SEMCONV_VERSION = "1.37.0"

TABLE_COMMENT = (
    "One row per model invocation, never per attempt (TR-011, TR-042). "
    f"gen_ai_* column names follow OpenTelemetry generative-AI semantic conventions "
    f"{OTEL_GENAI_SEMCONV_VERSION}, lowercased with '.' replaced by '_'. Columns "
    "without that prefix are gateway-local and follow no convention. "
    "Append-only: never updated, never deleted."
)


def upgrade() -> None:
    """Create `llm_invocation`, its two indexes, and the pin mirror.

    Every `CHECK` that constrains a single column's value domain sits on a
    `NOT NULL` column, so none can pass vacuously — a `CHECK` rejects only on
    *false*, and any comparison against NULL is NULL, which a `CHECK` accepts.
    The checks that do span nullable columns are biconditionals or
    implications written to be true on the null branch deliberately, and each
    says so where it appears.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_invocation (
            -- Gateway-generated before the Postgres write is attempted, never
            -- `DEFAULT gen_random_uuid()`. A database default would mint a
            -- *second* identifier at reconcile time, so the spooled copy and
            -- the eventual row would not share one identity and the spool's
            -- `ON CONFLICT DO NOTHING` idempotency would silently break
            -- (TR-041, TR-045).
            invocation_id uuid NOT NULL,

            -- Convention-named. Known from configuration before any request is
            -- built; there is no path on which an invocation has no provider.
            gen_ai_provider_name text NOT NULL,

            -- Convention-named. Deliberately NOT CHECK-constrained: the value
            -- set is the pinned convention's enumeration, and pinning it in
            -- DDL would make a pin bump a migration.
            gen_ai_operation_name text NOT NULL,

            -- Convention-named. The requested name is an input, always known.
            gen_ai_request_model text NOT NULL,

            -- Convention-named, and the one nullable member of the set. The
            -- provider resolves a model only when it answers, so an invocation
            -- that exhausts its transport budget has none. The CHECK below
            -- makes that absence possible ONLY on the failure path.
            gen_ai_response_model text,

            -- Separates replayed rows from live ones with no inference. Chosen
            -- explicitly by configuration with no default (TR-021), so it is
            -- known before the first attempt.
            resolution_mode text NOT NULL,

            -- Conditional: NOT NULL when replaying, by CHECK rather than by
            -- column, since a `record`-mode row legitimately has none until a
            -- fixture is written.
            fixture_key text,

            -- Convention-named. Zero is meaningful (no attempt reported usage);
            -- unknown is not. Summed across every transport and repair attempt,
            -- with a body-less attempt contributing zero rather than leaving
            -- the term undefined (TR-056).
            gen_ai_usage_input_tokens integer NOT NULL,
            gen_ai_usage_output_tokens integer NOT NULL,

            -- Gateway-local: no cached-token attribute exists at the pinned
            -- version, checked at 1.36.0 and 1.37.0. Kept as their own columns
            -- because the provider reports them outside the input count and
            -- bills them at different multipliers -- folding them in silently
            -- corrupts every recomputed cost.
            cache_write_input_tokens integer NOT NULL,
            cache_read_input_tokens integer NOT NULL,

            -- Gateway-local. Total wall clock across every attempt, from a
            -- monotonic clock. Integer milliseconds, not float seconds: the
            -- value feeds property-based tests, and binary float would make
            -- equality assertions platform-sensitive. Named `duration_ms`;
            -- `latency_ms` is not a name in this schema.
            duration_ms integer NOT NULL,

            -- >= 1 because a row exists only if at least one provider request
            -- or fixture lookup happened; <= 3 is TR-010's budget. A fixture
            -- lookup counts as one attempt (TR-056), so the lower bound holds
            -- on replay rows without inference.
            transport_attempt_count smallint NOT NULL,

            -- 0 or 1 -- TR-007 caps repair at one. Zero is the common case,
            -- not an unknown.
            repair_attempt_count smallint NOT NULL,

            -- NUMERIC, never double precision: SC-006 asserts recomputation
            -- reproduces this exactly, and binary floating point cannot carry
            -- that claim. Nullable because TR-016 requires absence to be
            -- representable and forbids substituting zero -- a zero cost and an
            -- unknown cost are different facts.
            cost_usd numeric(18, 10),

            -- The other half of the same pair. Domain closed at three values,
            -- and the set is closed over every path on which a row exists: an
            -- unresolvable price pin is not a fourth value, because TR-048
            -- refuses it before any request is constructed.
            cost_absent_reason text,

            -- Recording the version is what keeps a historical cost
            -- recomputable after rates change; without it the stored figure is
            -- unauditable. NOT NULL even when no entry inside it covers the
            -- model -- the pin is configuration, known before the call.
            price_table_version_id text NOT NULL,

            -- The instant the price entry was resolved against: `created_at` in
            -- record mode, the fixture's recording date widened to midnight UTC
            -- in replay (TR-057). Stored rather than derived, because deriving
            -- it in replay would require reading the fixture file -- which is
            -- not "recoverable from the stored row".
            pricing_timestamp timestamptz NOT NULL,

            -- Exactly one of three values per invocation (TR-009, TR-078).
            outcome text NOT NULL,

            -- Biconditional with outcome, below. Values are the normalized
            -- gateway error classes, never a provider exception name or
            -- message (TR-025, TR-064).
            error_type text,

            -- TR-031: a record must never be untraceable to the request that
            -- caused it. The gateway generates one when the caller supplies
            -- none, so there is no path on which it is unknown. The format
            -- CHECKs are a backstop -- TR-047 validates a caller-supplied
            -- identifier at the boundary *before* any provider request, so a
            -- malformed one is an argument error on an invocation that never
            -- billed, rather than a constraint violation after the provider was
            -- already paid.
            trace_id text NOT NULL,

            -- Gateway-generated, NOT `DEFAULT now()`. A default would stamp a
            -- spooled row with its reconcile time rather than its invocation
            -- time -- making latency and cost analysis wrong by exactly the
            -- length of the outage, and breaking TR-043, since
            -- `pricing_timestamp` equals this in record mode and pricing was
            -- resolved before the write.
            created_at timestamptz NOT NULL,

            CONSTRAINT pk_llm_invocation PRIMARY KEY (invocation_id),

            CONSTRAINT fk_llm_invocation__price_table_version
                FOREIGN KEY (price_table_version_id)
                REFERENCES price_table_version (version_id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            -- OBJ3 VC8. An implication, not a biconditional: a *failed*
            -- invocation may still have resolved a model before failing, so
            -- the reverse direction would reject a legitimate row.
            CONSTRAINT ck_llm_invocation__response_model_unless_failed
                CHECK (outcome = 'failed' OR gen_ai_response_model IS NOT NULL),

            CONSTRAINT ck_llm_invocation__resolution_mode_domain
                CHECK (resolution_mode IN ('record', 'replay')),

            -- Two separate rules, and neither implies the other. The first
            -- constrains the key's *shape* wherever one exists; the second
            -- constrains its *presence* on replay rows. A single combined
            -- check would let a malformed key through on a record-mode row.
            CONSTRAINT ck_llm_invocation__fixture_key_shape
                CHECK (fixture_key IS NULL
                       OR fixture_key ~ '^sha256:[0-9a-f]{64}$'),
            CONSTRAINT ck_llm_invocation__fixture_key_when_replaying
                CHECK (resolution_mode <> 'replay' OR fixture_key IS NOT NULL),

            CONSTRAINT ck_llm_invocation__input_tokens_non_negative
                CHECK (gen_ai_usage_input_tokens >= 0),
            CONSTRAINT ck_llm_invocation__output_tokens_non_negative
                CHECK (gen_ai_usage_output_tokens >= 0),
            CONSTRAINT ck_llm_invocation__cache_write_tokens_non_negative
                CHECK (cache_write_input_tokens >= 0),
            CONSTRAINT ck_llm_invocation__cache_read_tokens_non_negative
                CHECK (cache_read_input_tokens >= 0),
            CONSTRAINT ck_llm_invocation__duration_non_negative
                CHECK (duration_ms >= 0),

            CONSTRAINT ck_llm_invocation__transport_attempts_in_budget
                CHECK (transport_attempt_count BETWEEN 1 AND 3),
            CONSTRAINT ck_llm_invocation__repair_attempts_in_budget
                CHECK (repair_attempt_count BETWEEN 0 AND 1),

            CONSTRAINT ck_llm_invocation__cost_non_negative
                CHECK (cost_usd IS NULL OR cost_usd >= 0),

            CONSTRAINT ck_llm_invocation__cost_absent_reason_domain
                CHECK (cost_absent_reason IS NULL
                       OR cost_absent_reason IN ('no_covering_price_entry',
                                                 'model_unresolved',
                                                 'cost_out_of_range')),

            -- TR-016. Exclusive-or, so "absent with a stated reason" is the
            -- only representable form of absence: a NULL cost with no reason
            -- is rejected by the database rather than caught in review, and a
            -- cost carrying a reason is rejected too.
            CONSTRAINT ck_llm_invocation__cost_xor_absent_reason
                CHECK ((cost_usd IS NULL) <> (cost_absent_reason IS NULL)),

            CONSTRAINT ck_llm_invocation__outcome_domain
                CHECK (outcome IN ('valid', 'repaired', 'failed')),

            -- A biconditional rather than a one-way implication, so E013 can
            -- read "row has an error type" as "invocation failed" with no
            -- further predicate. The cost is that a transient transport error
            -- on a row that ultimately succeeded is not retained -- nothing
            -- asks for it, and retaining it would make this column mean two
            -- different things.
            CONSTRAINT ck_llm_invocation__error_type_iff_failed
                CHECK ((outcome = 'failed') = (error_type IS NOT NULL)),

            CONSTRAINT ck_llm_invocation__error_type_domain
                CHECK (error_type IS NULL
                       OR error_type IN ('validation_failed',
                                         'transport_failed',
                                         'deadline_exceeded')),

            -- W3C Trace Context Level 1. The all-zero value matches the format
            -- pattern and is defined as invalid by that specification, so the
            -- second check is not redundant with the first: a NOT NULL that
            -- admits 32 zeroes enforces presence without enforcing meaning.
            CONSTRAINT ck_llm_invocation__trace_id_format
                CHECK (trace_id ~ '^[0-9a-f]{32}$'),
            CONSTRAINT ck_llm_invocation__trace_id_not_all_zero
                CHECK (trace_id <> repeat('0', 32))
        )
        """
    )

    # E013's panel orders by recency; without this the panel's first query is a
    # sequential scan over every invocation ever made.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_invocation__created_at "
        "ON llm_invocation (created_at DESC)"
    )

    # Trace lookup: "show me everything that happened under this trace" is the
    # question the identifier exists to answer, and it is not answerable at
    # speed from the recency index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_invocation__trace_id "
        "ON llm_invocation (trace_id)"
    )

    # No index on `price_table_version_id`. Its only purpose would be to
    # accelerate the delete-time and update-time FK checks of TR-046, and
    # neither price table is ever deleted from or re-identified -- so it would
    # cost every insert and serve no read.

    # `COMMENT ON TABLE` replaces rather than appends, so this is idempotent
    # under TR-050's re-runnable-file rule without an `IF NOT EXISTS` form.
    op.execute(f"COMMENT ON TABLE llm_invocation IS {_quote(TABLE_COMMENT)}")


def _quote(value: str) -> str:
    """Render a SQL string literal.

    Hand-rolled rather than parameterised because `COMMENT ON` takes no bind
    parameters in PostgreSQL — the comment must be a literal in the statement
    text. The doubling below is the whole of the escaping this needs: the input
    is a module-level constant in this file, not user input, and the function
    exists so that constant can be edited without anyone having to remember the
    rule.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
