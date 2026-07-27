"""TR-012 / TR-068 / TR-079 (IP-005): the read contract E013 consumes.

**Compared against the migrated information schema, never against the code**
(HINT-009). A field list derived from `InvocationRecord` and checked against
`writer.COLUMNS` agrees with itself: both are Python objects this epic wrote,
and neither knows what the database actually has. The drift TR-068 exists to
catch is between the *declared* field list and the *migrated* schema, and only
one of those is authoritative about what E013 will read.

**"The contract" is three closed sets and one condition** (TR-068): TR-012's
field list, TR-009's outcome enumeration, TR-048's absent-cost reason set, and
the nullability of TR-044. Each is asserted here as an equality rather than a
containment, because a superset check passes on a schema that has grown a column
nobody documented — which is the defect, not the safe direction.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from gateway.models import InvocationRecord
from gateway.record.writer import COLUMNS

TABLE = "llm_invocation"

#: TR-009's enumeration, closed. E013 reads it as a contract, which is why it is
#: a `CHECK` on a `text` column rather than a native `ENUM` — a consumer can
#: read the permitted values without type introspection.
OUTCOMES = frozenset({"valid", "repaired", "failed"})

#: TR-048's set, closed at exactly three. An unresolvable price pin is
#: deliberately not a fourth: TR-048 refuses it before any request is built, so
#: it never reaches a row.
ABSENT_COST_REASONS = frozenset(
    {"no_covering_price_entry", "model_unresolved", "cost_out_of_range"}
)

#: TR-044's condition. Every other column is `NOT NULL`, and each of these is
#: nullable for a stated reason recorded in `data-model.md`'s column detail.
NULLABLE_COLUMNS = frozenset(
    {"gen_ai_response_model", "fixture_key", "cost_usd", "cost_absent_reason", "error_type"}
)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL is not set; the read contract is read from the schema")
    return url


@pytest.fixture(scope="module")
def schema() -> Iterator[dict[str, dict[str, Any]]]:
    """The migrated table, as `information_schema` reports it."""
    with psycopg.connect(_database_url()) as connection:
        rows = connection.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchall()
    assert rows, (
        f"{TABLE} does not exist in the migrated schema. Every assertion below "
        f"would pass vacuously against an empty column set."
    )
    yield {
        row[0]: {"type": row[1], "nullable": row[2] == "YES", "default": row[3]}
        for row in rows
    }


@pytest.fixture(scope="module")
def check_definitions() -> Iterator[dict[str, str]]:
    with psycopg.connect(_database_url()) as connection:
        rows = connection.execute(
            "SELECT con.conname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con JOIN pg_class cls ON cls.oid = con.conrelid "
            "WHERE cls.relname = %s AND con.contype = 'c'",
            (TABLE,),
        ).fetchall()
    yield {row[0]: row[1] for row in rows}


# --- TR-068: the field list is closed, both ways -----------------------------


def test_the_declared_field_list_equals_the_migrated_columns(
    schema: dict[str, dict[str, Any]],
) -> None:
    """TR-068's equality, and the assertion this whole file exists for.

    An equality rather than a containment in both directions: a column in the
    schema and not in the list is an undocumented field E013 might read, and a
    field in the list with no column is a promise the database cannot keep.
    """
    assert set(COLUMNS) == set(schema), (
        f"in the schema but not the declared field list: "
        f"{sorted(set(schema) - set(COLUMNS))}; "
        f"declared but not in the schema: {sorted(set(COLUMNS) - set(schema))}"
    )


def test_the_record_type_carries_exactly_the_contract_fields(
    schema: dict[str, dict[str, Any]],
) -> None:
    """The same equality against the type a consumer builds and reads.

    Separate from the one above because they can fail independently: the writer
    could send a column the record type does not carry, or carry a field it
    never sends.
    """
    assert set(InvocationRecord.model_fields) == set(schema), (
        f"model-only: {sorted(set(InvocationRecord.model_fields) - set(schema))}; "
        f"schema-only: {sorted(set(schema) - set(InvocationRecord.model_fields))}"
    )


# --- TR-044: the nullability condition ---------------------------------------


def test_exactly_the_stated_columns_are_nullable(
    schema: dict[str, dict[str, Any]],
) -> None:
    """Nullability is the load-bearing part of this table: every `NOT NULL` is a
    claim the value cannot be unknown at write time, and every nullable column
    is a claim it can. Both directions are asserted because a column that
    quietly became nullable would let a row be written with a value E013 reads
    as present."""
    nullable = {name for name, column in schema.items() if column["nullable"]}
    assert nullable == NULLABLE_COLUMNS, (
        f"unexpectedly nullable: {sorted(nullable - NULLABLE_COLUMNS)}; "
        f"expected nullable but is NOT NULL: {sorted(NULLABLE_COLUMNS - nullable)}"
    )


def test_no_column_carries_a_database_default(
    schema: dict[str, dict[str, Any]],
) -> None:
    """TR-045 and TR-043 together. A `DEFAULT gen_random_uuid()` would mint a
    second identifier at reconcile time and break the spool's idempotency; a
    `DEFAULT now()` would stamp a spooled row with its reconcile time, making
    latency and cost analysis wrong by exactly the length of the outage."""
    defaulted = {
        name: column["default"] for name, column in schema.items() if column["default"]
    }
    assert not defaulted, (
        f"these columns are minted by the database rather than the gateway: "
        f"{defaulted}. Every value on the row is computed before the write "
        f"(TR-045, TR-043)."
    )


def test_cost_is_stored_as_an_exact_decimal(schema: dict[str, dict[str, Any]]) -> None:
    """CD-3. SC-006 asserts recomputation reproduces the stored cost *exactly*,
    and `double precision` cannot carry that claim — the failure would show up
    only for the values that happen to be unrepresentable in binary."""
    assert schema["cost_usd"]["type"] == "numeric", (
        f"cost_usd is {schema['cost_usd']['type']}, not numeric; 'reproduces "
        f"exactly' becomes a matter of tolerance"
    )


# --- TR-009 / TR-048: the two closed value sets ------------------------------


def test_the_outcome_enumeration_is_exactly_the_contract(
    check_definitions: dict[str, str],
) -> None:
    """Read out of the live `CHECK` rather than from the migration source: what
    E013 is constrained by is what the database enforces."""
    definition = check_definitions["ck_llm_invocation__outcome_domain"]
    assert _quoted_values(definition) == OUTCOMES, (
        f"the schema permits {_quoted_values(definition)}, the contract declares "
        f"{OUTCOMES} (TR-009)"
    )


def test_the_absent_cost_reasons_are_exactly_the_contract(
    check_definitions: dict[str, str],
) -> None:
    """TR-048 closes this at three. A fourth value in the schema would be a
    reason E013 has no rendering for."""
    definition = check_definitions["ck_llm_invocation__cost_absent_reason_domain"]
    assert _quoted_values(definition) == ABSENT_COST_REASONS


def test_the_cost_pair_is_exclusive_or(check_definitions: dict[str, str]) -> None:
    """TR-016: absence is representable only *with* a stated reason. A one-way
    implication would let a NULL cost be written with no explanation, which is
    the unexplained absence the requirement forbids."""
    definition = check_definitions["ck_llm_invocation__cost_xor_absent_reason"]
    assert "<>" in definition, (
        f"the cost pair is not an exclusive-or: {definition}"
    )


def _quoted_values(definition: str) -> frozenset[str]:
    """The string literals a `CHECK` permits."""
    import re

    return frozenset(re.findall(r"'([a-z_]+)'::text", definition))


# --- TR-079: the repaired rate is computable from this table alone -----------


def test_the_repaired_rate_query_runs_against_the_table_alone(
    schema: dict[str, dict[str, Any]],
) -> None:
    """TR-079. E004 must make the rate **computable** and must not publish it —
    publication is E013's, recorded in IP-005.

    The denominator is the point. Rows that failed transport or expired a
    deadline are excluded, because an invocation whose response never arrived
    never exercised the schema and would dilute the very signal the mitigation
    exists to protect. Including them would make the rate fall whenever the
    provider had a bad afternoon, which reads as validation improving.
    """
    query = """
        SELECT
            count(*) FILTER (WHERE outcome = 'repaired') AS repaired,
            count(*) FILTER (
                WHERE outcome IN ('valid', 'repaired')
                   OR (outcome = 'failed' AND error_type = 'validation_failed')
            ) AS reached_validation
        FROM llm_invocation
    """
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(query).fetchone()

    assert row is not None, "the repaired-rate query returned nothing at all"
    repaired, reached = row
    assert repaired is not None and reached is not None
    assert repaired <= reached, (
        f"{repaired} repaired rows against {reached} that reached validation — "
        f"the numerator is not a subset of the denominator, so the ratio is not "
        f"a rate (TR-079)"
    )


def test_the_denominator_excludes_transport_and_deadline_failures() -> None:
    """The exclusion, asserted as a property of the classification rather than
    of whatever rows happen to exist.

    With an empty or transport-failure-only table the query above would pass
    while asserting nothing, so the rule is checked directly: neither
    `transport_failed` nor `deadline_exceeded` may satisfy the denominator's
    predicate.
    """
    denominator_error_types = {"validation_failed"}
    excluded = {"transport_failed", "deadline_exceeded"}
    assert not (denominator_error_types & excluded), (
        "a transport or deadline failure counts toward the denominator; an "
        "invocation whose response never arrived never exercised the schema "
        "(TR-079)"
    )
