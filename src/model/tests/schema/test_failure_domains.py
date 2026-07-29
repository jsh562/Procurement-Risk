"""FR-056 / SC-016 (T078): the five run-level kinds and the seven per-field
outcomes share zero values.

**Both lists are read out of the catalog, and neither is written down here.**
That is the whole design of this module. A test that restates the five kinds and
the seven outcomes as Python literals and intersects those two literals passes
whenever the migrations drift away from it — which is the failure it exists to
catch. The disjointness that matters is between the two `CHECK` constraints that
decide what is *storable*:

* `ck_ingestion_run__failure_kind_domain` on `ingestion_run.run_failure_kind`
* `ck_extraction_failure__outcome` on `extraction_failure.outcome`

so both bodies are fetched with `pg_get_constraintdef` and the string literals
are parsed out of them. What this file states independently is only what the
requirement states independently: **five** and **seven**, and an empty
intersection.

**Why the two domains must not meet.** A run-level failure aborts the run and
explains why no more documents were written; a per-field outcome explains why one
value on one chunk was not stored. They live in different tables against
different populations, and a value in both would make the two indistinguishable
in every published count — a `fixture_missing` recorded as a per-field outcome
would appear in FR-034's breakdown as though one field had failed, while the run
that never finished looked complete.

**And a run-level failure cannot be an `extraction_failure` row at all**, which
is the structural reason the second domain exists. `extraction_failure.
source_chunk_id` is `NOT NULL` with a `RESTRICT` foreign key to a chunk the
document's rollback has just removed, so the row has no referent and cannot be
stored. That is asserted here too, from the catalog rather than from the record
that says so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from model.ingest.failures import FAILURE_OUTCOMES
from model.ingest.runs import RUN_FAILURE_KINDS

#: The two constraints, and the count each requirement fixes. The counts are the
#: only numbers this module states, and they are stated because FR-056 and
#: FR-034 state them: five run-level kinds, seven per-field outcomes.
RUN_LEVEL_CONSTRAINT = "ck_ingestion_run__failure_kind_domain"
PER_FIELD_CONSTRAINT = "ck_extraction_failure__outcome"
RUN_LEVEL_COUNT = 5
PER_FIELD_COUNT = 7

_CONSTRAINT_DEFINITION = text(
    """
    SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
     WHERE c.conname = ANY(:names) AND c.contype = 'c'
    """
)

#: A single-quoted string literal inside a `CHECK` body, as PostgreSQL prints it
#: back. `pg_get_constraintdef` renders the admitted values as
#: `'value'::text` inside an `ARRAY[...]` or a chain of `=` comparisons; either
#: way the literals are the quoted runs, and a doubled quote inside one would be
#: a value containing a quote, which neither vocabulary has and neither should.
_LITERAL = re.compile(r"'([^']*)'")


def literals_in(definition: str) -> frozenset[str]:
    """Every string literal the constraint body admits.

    Deliberately naive about *how* the body is written — an `IN` list, an
    `= ANY (ARRAY[...])`, or a chain of `OR`s all render their vocabulary as
    quoted literals — because the assertion is about the vocabulary and not
    about the shape of the expression. A parser that understood one shape would
    silently return nothing if a future revision used another, and an empty set
    intersects with everything.
    """
    return frozenset(_LITERAL.findall(definition))


@pytest.fixture
def definitions(db_session: Session) -> Mapping[str, str]:
    """Both `CHECK` bodies, straight out of `pg_constraint`."""
    rows = db_session.execute(
        _CONSTRAINT_DEFINITION, {"names": [RUN_LEVEL_CONSTRAINT, PER_FIELD_CONSTRAINT]}
    ).all()
    found = {name: definition for name, definition in rows}
    missing = sorted({RUN_LEVEL_CONSTRAINT, PER_FIELD_CONSTRAINT} - set(found))
    assert not missing, (
        f"{missing} are not in the catalog, so the two domains cannot be compared at all. "
        f"A constraint that is absent admits everything."
    )
    return found


def test_the_two_declared_domains_share_zero_values(definitions: Mapping[str, str]) -> None:
    """SC-016's intersection, over the two constraint bodies themselves.

    Neither vocabulary is written down in this module. Both are parsed from the
    catalog, so this fails when a migration adds a value to either domain that
    the other already holds — which a test comparing two Python lists would
    report as a pass, both lists having been edited to agree with each other and
    with neither constraint.
    """
    run_level = literals_in(definitions[RUN_LEVEL_CONSTRAINT])
    per_field = literals_in(definitions[PER_FIELD_CONSTRAINT])
    assert run_level, f"{RUN_LEVEL_CONSTRAINT} admits no literal; an empty domain intersects "
    assert per_field, f"{PER_FIELD_CONSTRAINT} admits no literal; an empty domain intersects "
    overlap = sorted(run_level & per_field)
    assert not overlap, (
        f"FR-056 / SC-016: {overlap} is admitted by both {RUN_LEVEL_CONSTRAINT} and "
        f"{PER_FIELD_CONSTRAINT}. A run-level failure and a per-field outcome sharing a "
        f"value are indistinguishable in every published count: the aborted run would read "
        f"as one failed field, and the field would read as an aborted run."
    )


def test_the_run_level_domain_is_closed_at_five(definitions: Mapping[str, str]) -> None:
    """FR-056: five kinds, and the count is read rather than restated.

    The number is the requirement's; the membership is the catalog's. A sixth
    kind is an amendment to FR-056 and a new revision, not an addition somebody
    makes to a list.
    """
    admitted = literals_in(definitions[RUN_LEVEL_CONSTRAINT])
    assert len(admitted) == RUN_LEVEL_COUNT, (
        f"FR-056 closes the run-level set at {RUN_LEVEL_COUNT}; {RUN_LEVEL_CONSTRAINT} "
        f"admits {len(admitted)}: {sorted(admitted)}"
    )


def test_the_per_field_domain_is_closed_at_seven(definitions: Mapping[str, str]) -> None:
    """FR-034: seven outcomes, counted from the constraint that enforces them."""
    admitted = literals_in(definitions[PER_FIELD_CONSTRAINT])
    assert len(admitted) == PER_FIELD_COUNT, (
        f"FR-034 closes the per-field set at {PER_FIELD_COUNT}; {PER_FIELD_CONSTRAINT} "
        f"admits {len(admitted)}: {sorted(admitted)}"
    )


def test_the_job_chooses_from_the_domains_the_database_enforces(
    definitions: Mapping[str, str],
) -> None:
    """The second half, and a different claim from disjointness.

    The tests above compare the two *constraints* with each other and never
    consult Python. This one compares each constraint with the tuple the job
    picks its value from, which is the drift that produces a `CHECK` violation
    at the one moment a run has no other way to explain itself — the failure
    write happens after a rollback, and a rejected `UPDATE` there leaves the run
    reading as in flight for ever.
    """
    assert set(RUN_FAILURE_KINDS) == set(literals_in(definitions[RUN_LEVEL_CONSTRAINT])), (
        "`runs.RUN_FAILURE_KINDS` and `ck_ingestion_run__failure_kind_domain` disagree. The "
        "job chooses a kind from the tuple and the database decides what is storable; a "
        "disagreement surfaces as a rejected UPDATE after a rollback, where there is nothing "
        "left to fall back on."
    )
    assert set(FAILURE_OUTCOMES) == set(literals_in(definitions[PER_FIELD_CONSTRAINT])), (
        "`failures.FAILURE_OUTCOMES` and `ck_extraction_failure__outcome` disagree."
    )


def test_a_per_field_row_could_not_carry_a_run_level_failure(db_session: Session) -> None:
    """The structural reason FR-056 needs its own home, read from the catalog.

    `extraction_failure.source_chunk_id` is `NOT NULL` and its foreign key to
    `chunk` is `ON DELETE RESTRICT`, so a per-field row explaining a rolled-back
    document points at a chunk the rollback has just removed and cannot be
    stored at all. This is asserted against `information_schema` and
    `pg_constraint` rather than quoted from the data model, because it is the
    delivered schema that decides.
    """
    nullable = db_session.execute(
        text(
            """
            SELECT is_nullable FROM information_schema.columns
             WHERE table_name = 'extraction_failure' AND column_name = 'source_chunk_id'
            """
        )
    ).scalar_one()
    assert nullable == "NO", (
        "`extraction_failure.source_chunk_id` is nullable, so a per-field row could carry a "
        "run-level failure with no chunk — and FR-056's separate home would be optional "
        "rather than structural"
    )

    delete_action = db_session.execute(
        text(
            """
            SELECT c.confdeltype
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = 'extraction_failure'
               AND c.contype = 'f'
               AND c.conname = 'fk_extraction_failure__chunk_page'
            """
        )
    ).scalar_one()
    assert delete_action == "r", (
        "`fk_extraction_failure__chunk_page` does not RESTRICT on delete, so removing a "
        "chunk would take its failure rows with it and the rollback argument would not hold"
    )
