"""The three extraction tables and the provenance view that migration `0006` creates.

This file is the epic's Principle I test. Everything it asserts is a claim about
what the storage boundary *refuses*, because "an unattributable number is a
defect, not a rough edge" is only true if the database is the thing enforcing it
-- every layer above can be bypassed, and a value written around the constraint
is indistinguishable afterwards from one written through it.

Five groups, one per task, each failing a different way when it is wrong:

* **T024 -- the citation itself (TR-015, TR-016, TR-017).** The three citation
  columns are `NOT NULL`, so an unattributable value is unrepresentable rather
  than merely detectable. Confidence is bounded on the *closed* unit interval, so
  `0.0` and `1.0` are ordinary values and not errors. And the cited page cannot
  disagree with its source chunk's page, which is the one claim in this file that
  a careless test will pass without ever exercising -- see
  `test_a_page_belonging_to_a_different_chunk_is_rejected`. The group closes on
  **TR-078**'s third clause: a stored citation survives a `document_id` key-space
  change, asserted end to end with a citation row actually in place while the
  cascade runs. The *mechanical* half of that requirement -- the chunks follow the
  new key and keep their `chunk_id` -- lives in
  `test_chunk.py::test_updating_a_document_key_cascades_to_every_chunk`, and the
  two halves are deliberately in different files: the insert machinery an
  `extracted_value` row needs is here.
* **T025 -- multi-source provenance (TR-018, TR-059, TR-060).** A three-chunk
  value is recoverable in one read of `v_extracted_value_provenance`, ordinal 1
  denoting the anchor. The two constraints capping the contributor set only work
  as a pair, so both halves are tested. Gaps G-1 and G-2 are asserted as the
  *disclosures they are* -- what a reader sees when the rule is violated -- not as
  guarantees the schema does not make.
* **T026 -- failure as the only representation of a failed extraction (TR-019,
  TR-044, TR-061).** The failure row inserts; the same attempt is structurally
  unrepresentable as a half-filled value row. The vocabulary grows by `INSERT`
  with no DDL, and a retired term still resolves -- gaps G-5 and G-7.
* **T027 -- the value's shape and what it deliberately does not reference
  (TR-045, TR-054, TR-081, TR-082, TR-085).** Canonical text plus an optional
  typed numeric, populated exactly on numeric-kind fields, asserted in both
  directions. No foreign key to any target record, read out of `pg_constraint`.
* **T049 -- append-only as a privilege fact (TR-084, TR-086, SC-028).** Migration
  `0009` revokes `UPDATE` and `DELETE` on all three provenance tables from the
  application role. All six refusals are exercised individually, `INSERT` and
  `SELECT` are shown still to work so the tables are append-only rather than
  read-only, the migration role is shown to retain both verbs, and the revoke is
  read back out of the catalog. What that group can and cannot claim is set out
  in its own section header -- the guarantee is real for the application role and
  **latent** for the deployed connection, which is gap **G-11**.

**Three requirements here are documentation, and are tested as documentation.**
TR-081 (confidence is a computed score, not a calibrated probability),
TR-082 (agent identity is at ingestion-run granularity, not per value), and
TR-085 (provenance rows are retained for the life of the database) constrain what
a *reader* may conclude. No constraint can carry any of them. Inventing a schema
assertion for them would manufacture false coverage, so the tests named
`test_tr08*_is_recorded_in_data_model_md_...` assert exactly what is available to
assert: that `data-model.md`, which TR-083 makes normative for reader-facing
semantics, states them.

**Disclosed gaps this file covers, and what each one actually claims.**

* **G-1** -- a contributor-ordinal *gap* is not enforced. The disclosure records
  that at runtime "the provenance view returns fewer contributors than
  `source_chunk_count` declares, so a reader sees an incomplete citation set
  rather than a wrong one". The test asserts precisely that: the row is accepted,
  and the shortfall is visible to the counting query the gap table names as its
  cover. It does not assert the gap is closed, because it is not.
* **G-2** -- an anchor chunk reappearing as a contributor row is not enforced.
  The disclosure records that the anchor "appears twice in the provenance view,
  overstating the number of distinct sources". The test asserts the row is
  accepted and the overstatement is measurable. It also asserts the contrast that
  makes G-2 a gap rather than an oversight: duplication *within* the contributor
  table is refused by `uq_evcc__value_chunk`; only duplication across the
  anchor/contributor boundary escapes, because no constraint can span it.
* **G-5** -- one attempt can hold both a value row and a failure row. The
  structural half of TR-019 *is* carried (`value_text` NOT NULL and non-blank), so
  the test asserts that half as enforcement and the cross-table half as
  disclosure.
* **G-7** -- a retired term stays insertable. Retirement is advisory; E006 filters
  on `retired_at IS NULL`. The test asserts both the advisory insert and the
  reason retirement cannot simply revoke the term: historical rows must keep
  resolving through it.

**Isolation.** Everything runs on `db_session`, whose outer transaction is rolled
back in teardown, so no test leaves a row behind and no test needs cleanup.
Nothing in `0006` is deferrable -- the schema's one deferrable constraint belongs
to `0007` -- so `force_constraints_immediate` is deliberately unused here.

**Never on message text.** Every rejection names the psycopg subclass and the
constraint that must have produced it, through `conftest.assert_rejects`. The two
exceptions are `NOT NULL` violations, which on PostgreSQL 16 carry `column_name`
and no `constraint_name` at all (catalogued, nameable `NOT NULL` constraints
arrive in 17); those assert the column, which is every bit as specific.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

#: `conftest.assert_rejects` as seen through its fixture. Requested rather than
#: imported for the reason that fixture's docstring gives: the import form relies
#: on pytest having put this directory on `sys.path`.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

#: A `REAL` document for the chunks to resolve through. Retrieval provenance
#: present, generation provenance absent -- `0003` rejects either layer's fields
#: on the other, and a row that trips one of those checks would never reach the
#: extraction constraints this file is about.
SOURCE_DOCUMENT: Mapping[str, Any] = {
    "document_id": "example-piping-submittal-2026",
    "document_type": "submittal",
    "project_id": "PRJ-001",
    "title": "Piping Materials Submittal",
    "source_kind": "REAL",
    "source_ref": "https://standards.example.gov/piping/submittal/2026",
    "issuing_body": "Example Standards Body",
    "retrieval_date": date(2026, 1, 15),
    "generator_id": None,
    "generation_seed": None,
    "generated_at": None,
    "fixture_hashes": None,
    "roster_hash": None,
    "license_basis": "public-domain",
}

DOCUMENT_INSERT = text(
    """
    INSERT INTO document (
        document_id, document_type, project_id, title, source_kind,
        source_ref, issuing_body, retrieval_date,
        generator_id, generation_seed, generated_at, fixture_hashes, roster_hash,
        license_basis
    )
    VALUES (
        :document_id, :document_type, :project_id, :title, :source_kind,
        :source_ref, :issuing_body, :retrieval_date,
        :generator_id, :generation_seed, :generated_at, :fixture_hashes, :roster_hash,
        :license_basis
    )
    """
)

#: The embedding is built server-side from `schema_constants.vector_dimension`
#: -- the published copy every consumer reads under TR-047 -- so this file holds
#: no second opinion about the size of the vector space, and no 384-element
#: literal crosses the driver boundary. A uniform vector is fine here: nothing in
#: this file ranks by distance, it only needs a chunk that exists.
CHUNK_INSERT = text(
    """
    INSERT INTO chunk (
        chunk_id, document_id, document_type, project_id,
        page_number, ordinal, body_text,
        embedding, embedding_model_id, embedding_model_revision
    )
    VALUES (
        :chunk_id, :document_id, :document_type, :project_id,
        :page_number, :ordinal, :body_text,
        (
            SELECT array_agg(1.0::real ORDER BY axis.component)
            FROM generate_series(
                1, (SELECT vector_dimension FROM schema_constants)
            ) AS axis(component)
        )::vector,
        :embedding_model_id, :embedding_model_revision
    )
    """
)

EMBEDDING_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_REVISION = "e4ce9877abf3edfe10b0d82785e83bdcb973e22e"

#: Three pages, three chunks -- TR-058 gives a chunk exactly one page, so a value
#: spanning three pages is three contributing chunks. Non-adjacent on purpose:
#: page numbers that happen to equal an ordinal or a contributor ordinal would
#: let an off-by-one in the DDL or in a test pass unnoticed.
CHUNK_PAGES: tuple[int, ...] = (4, 9, 12)

#: Seeded vocabulary terms (`0005`), one of each `value_kind`, so the
#: numeric-column biconditional can be exercised in every direction.
TEXT_TERM = "manufacturer"
NUMBER_TERM = "quantity"
DATE_TERM = "submittal_date"


class Citation(NamedTuple):
    """A `(chunk, page)` pair -- the unit `uq_chunk__chunk_page` makes referable."""

    chunk_id: UUID
    page_number: int


VALUE_INSERT = text(
    """
    INSERT INTO extracted_value (
        extracted_value_id, source_chunk_id, cited_page, field_name, value_kind,
        value_text, value_number, confidence, provenance_kind, source_chunk_count
    )
    VALUES (
        :extracted_value_id, :source_chunk_id, :cited_page, :field_name, :value_kind,
        :value_text, :value_number, :confidence, :provenance_kind, :source_chunk_count
    )
    """
)

CONTRIBUTOR_INSERT = text(
    """
    INSERT INTO extracted_value_contributing_chunk (
        extracted_value_id, contributor_ordinal, source_chunk_count, chunk_id, page_number
    )
    VALUES (
        :extracted_value_id, :contributor_ordinal, :source_chunk_count, :chunk_id, :page_number
    )
    """
)

FAILURE_INSERT = text(
    """
    INSERT INTO extraction_failure (
        extraction_failure_id, source_chunk_id, attempted_page, field_name,
        outcome, repair_attempt_count, detail
    )
    VALUES (
        :extraction_failure_id, :source_chunk_id, :attempted_page, :field_name,
        :outcome, :repair_attempt_count, :detail
    )
    """
)

VOCABULARY_INSERT = text(
    """
    INSERT INTO field_vocabulary (field_name, value_kind, label, description)
    VALUES (:field_name, :value_kind, :label, :description)
    """
)

RETIRE_TERM = text("UPDATE field_vocabulary SET retired_at = :retired_at WHERE field_name = :name")


def value_row(citation: Citation, **overrides: Any) -> dict[str, Any]:
    """A valid single-chunk `extracted_value` row citing `citation`.

    Perturbing exactly one field of an otherwise-valid row is what makes a
    rejection attributable. Break two at once and PostgreSQL reports whichever
    rule it evaluated first, so the test names one constraint and is satisfied by
    another -- which is the failure `conftest.assert_rejects` exists to catch and
    which this builder exists to avoid producing in the first place.

    The citation columns are copied out of `citation` rather than restated, so a
    test aiming at a `CHECK` cannot trip `fk_extracted_value__chunk_page` on the
    way there.
    """
    row: dict[str, Any] = {
        "extracted_value_id": uuid4(),
        "source_chunk_id": citation.chunk_id,
        "cited_page": citation.page_number,
        "field_name": TEXT_TERM,
        "value_kind": "text",
        "value_text": "Grinnell",
        "value_number": None,
        "confidence": 0.82,
        "provenance_kind": "single_chunk",
        "source_chunk_count": 1,
    }
    row.update(overrides)
    return row


def multi_chunk_value_row(
    citation: Citation, declared_count: int, **overrides: Any
) -> dict[str, Any]:
    """A valid multi-source `extracted_value` row declaring `declared_count` sources.

    `provenance_kind` is set alongside the count because
    `ck_extracted_value__provenance_agrees_with_count` is a biconditional: a
    `single_chunk` row with a count of 3 is refused as firmly as the reverse, and
    a test that forgot to set both would be rejected by that check instead of by
    whatever it meant to exercise.
    """
    return value_row(
        citation,
        provenance_kind="multi_chunk",
        source_chunk_count=declared_count,
        **overrides,
    )


def contributor_row(
    extracted_value_id: UUID,
    contributor_ordinal: int,
    citation: Citation,
    declared_count: int,
) -> dict[str, Any]:
    """A contributor row at `contributor_ordinal`, carrying `declared_count`.

    `declared_count` is a parameter rather than read from the parent, because the
    two constraints capping the contributor set only work as a pair and the
    decisive test is the one where the child carries a count its parent does not
    -- see `test_a_contributor_inflating_its_own_declared_count_trips_the_foreign_key`.
    """
    return {
        "extracted_value_id": extracted_value_id,
        "contributor_ordinal": contributor_ordinal,
        "source_chunk_count": declared_count,
        "chunk_id": citation.chunk_id,
        "page_number": citation.page_number,
    }


def failure_row(citation: Citation, **overrides: Any) -> dict[str, Any]:
    """A valid `extraction_failure` row against `citation`.

    Note what the default carries and what it cannot: an attempted field, the
    chunk and page the attempt was made from, an outcome, a repair count, and a
    non-blank detail. There is no confidence column and no value column of any
    kind, so this table cannot be used to smuggle in a half-extracted value.
    """
    row: dict[str, Any] = {
        "extraction_failure_id": uuid4(),
        "source_chunk_id": citation.chunk_id,
        "attempted_page": citation.page_number,
        "field_name": TEXT_TERM,
        "outcome": "missing_citation",
        "repair_attempt_count": 0,
        "detail": "The manufacturer appears in a table whose page header did not parse.",
    }
    row.update(overrides)
    return row


def insert_chunk(
    session: Session, document: Mapping[str, Any], page_number: int, ordinal: int
) -> UUID:
    """Insert one chunk of `document` on `page_number` and return its id."""
    chunk_id = uuid4()
    session.execute(
        CHUNK_INSERT,
        {
            "chunk_id": chunk_id,
            "document_id": document["document_id"],
            "document_type": document["document_type"],
            "project_id": document["project_id"],
            "page_number": page_number,
            "ordinal": ordinal,
            "body_text": f"Materials schedule continued on page {page_number}.",
            "embedding_model_id": EMBEDDING_MODEL_ID,
            "embedding_model_revision": EMBEDDING_MODEL_REVISION,
        },
    )
    return chunk_id


def insert_value(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `extracted_value` and return its id."""
    session.execute(VALUE_INSERT, dict(row))
    return row["extracted_value_id"]


def insert_contributor(session: Session, row: Mapping[str, Any]) -> None:
    """Insert `row` into `extracted_value_contributing_chunk`."""
    session.execute(CONTRIBUTOR_INSERT, dict(row))


def insert_failure(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `extraction_failure` and return its id."""
    session.execute(FAILURE_INSERT, dict(row))
    return row["extraction_failure_id"]


@pytest.fixture
def source_document(db_session: Session) -> Mapping[str, Any]:
    """A `REAL` document, written inside the test's savepoint."""
    db_session.execute(DOCUMENT_INSERT, dict(SOURCE_DOCUMENT))
    return SOURCE_DOCUMENT


@pytest.fixture
def citations(db_session: Session, source_document: Mapping[str, Any]) -> tuple[Citation, ...]:
    """Three chunks of one document, one page each, on three different pages."""
    return tuple(
        Citation(insert_chunk(db_session, source_document, page, ordinal), page)
        for ordinal, page in enumerate(CHUNK_PAGES)
    )


@pytest.fixture
def anchor(citations: tuple[Citation, ...]) -> Citation:
    """The first chunk -- the anchor citation of every value built below."""
    return citations[0]


# --------------------------------------------------------------------------- #
# Assertion helpers
# --------------------------------------------------------------------------- #


def assert_not_null_violation(
    session: Session, row: Mapping[str, Any], column: str, insert: Any = VALUE_INSERT
) -> None:
    """Assert `row` is refused as a `NOT NULL` violation naming `column`.

    Deliberately not routed through `conftest.assert_rejects`, and the reason is a
    property of PostgreSQL 16 rather than a preference: a `NOT NULL` violation
    reports `column_name` and carries **no `constraint_name` at all**, because
    catalogued, nameable `NOT NULL` constraints only arrive in 17. Forcing this
    through a helper that requires a constraint name would prove the helper's
    error path and nothing about the schema.

    Asserting the column is not a weaker claim. It is what distinguishes this
    rejection from a null in any *other* required column of the same row -- which
    is the exact confusion that would otherwise let a test claim it covered
    TR-015's citation while a typo'd fixture had actually nulled `value_text`.
    """
    savepoint = session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed immediately below
        session.execute(insert, dict(row))
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.NotNullViolation), (
        f"a row with no {column} must be refused as a NOT NULL violation "
        f"(SQLSTATE 23502); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.diag.column_name == column, (
        f"the rejection must name {column}, or some other required column was null and "
        f"this test never reached the rule it claims to cover; got "
        f"{original.diag.column_name!r} on {original.diag.table_name!r}"
    )


PROVENANCE_OF_VALUE = text(
    """
    SELECT contributor_ordinal, chunk_id, page_number
    FROM v_extracted_value_provenance
    WHERE extracted_value_id = :extracted_value_id
    ORDER BY contributor_ordinal
    """
)


def provenance_of(session: Session, extracted_value_id: UUID) -> list[Citation]:
    """Every `(chunk, page)` the view attributes to `extracted_value_id`, by ordinal.

    The `ORDER BY` lives in *this* query, not in the view (TR-060). Ordering here
    is a test's own choice about how to read an unordered set, which is exactly
    the arrangement the view's missing `ORDER BY` is meant to force on every
    consumer.
    """
    rows = session.execute(PROVENANCE_OF_VALUE, {"extracted_value_id": extracted_value_id}).all()
    return [Citation(row.chunk_id, row.page_number) for row in rows]


# --------------------------------------------------------------------------- #
# T024 -- the citation itself (TR-015, TR-016, TR-017)
# --------------------------------------------------------------------------- #

#: TR-015's three columns. Each is `NOT NULL`, so an unattributable value is
#: unrepresentable and not merely detectable -- Principle I enforced at the one
#: layer that cannot be bypassed.
NON_NULLABLE_CITATION_COLUMNS = ("source_chunk_id", "cited_page", "confidence")


@pytest.mark.parametrize("column", NON_NULLABLE_CITATION_COLUMNS)
def test_a_value_missing_any_part_of_its_citation_is_rejected(
    db_session: Session, anchor: Citation, column: str
) -> None:
    """TR-015: source chunk, cited page, and confidence are each `NOT NULL`.

    All three separately, because TR-015 names three columns and a test that only
    nulled one would pass against a schema that had left the other two nullable
    -- and a nullable `cited_page` is precisely the unattributable number
    Principle I calls a defect.

    `MATCH FULL` on `fk_extracted_value__chunk_page` matters for the same reason
    in the other direction: were either citation column ever relaxed to nullable,
    `MATCH SIMPLE` would skip the referential check entirely on a partially-null
    pair. The `NOT NULL` is the primary guard and the match type is the backstop.
    """
    assert_not_null_violation(db_session, value_row(anchor, **{column: None}), column)


#: The rejected side of the closed unit interval. Both ends are overshot, and by
#: a margin large enough that no double-precision rounding could account for it.
CONFIDENCE_OUT_OF_RANGE = (-0.5, -0.000001, 1.000001, 2.0)


@pytest.mark.parametrize("confidence", CONFIDENCE_OUT_OF_RANGE)
def test_confidence_outside_the_closed_unit_interval_is_rejected(
    db_session: Session,
    anchor: Citation,
    assert_rejects: RejectionAsserter,
    confidence: float,
) -> None:
    """TR-016: a confidence outside 0 through 1 is refused by the named check.

    Both sides of the interval, because a check written with one comparison
    instead of two would accept everything below zero -- and a negative
    confidence is not a weak claim, it is a meaningless one.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extracted_value__confidence_range"
    ):
        insert_value(db_session, value_row(anchor, confidence=confidence))


#: The endpoints. TR-054 admits both "as the extremes of the extracting agent's
#: expressed confidence rather than as literal certainty or impossibility".
CONFIDENCE_ENDPOINTS = (0.0, 1.0)


@pytest.mark.parametrize("confidence", CONFIDENCE_ENDPOINTS)
def test_both_endpoints_of_the_confidence_interval_are_accepted(
    db_session: Session, anchor: Citation, confidence: float
) -> None:
    """TR-016, TR-054: the interval is closed -- `0.0` and `1.0` are ordinary values.

    This is the assertion that distinguishes `>= 0.0 AND <= 1.0` from
    `> 0.0 AND < 1.0`, and it is not a formality. Excluding either end would force
    every writer to fabricate an epsilon: an agent that found the value printed
    verbatim on the page reports `1.0`, and one that is reporting a guess it does
    not believe reports `0.0`. A schema that refused those would be silently
    trading an honest extreme for a made-up number just inside the boundary,
    which is the false confidence Principle II exists to remove.

    Read back rather than merely inserted, so a stored `NULL` or a coerced value
    could not pass as acceptance.
    """
    value_id = insert_value(db_session, value_row(anchor, confidence=confidence))

    stored = db_session.execute(
        text("SELECT confidence FROM extracted_value WHERE extracted_value_id = :id"),
        {"id": value_id},
    ).scalar_one()

    assert stored == pytest.approx(confidence), (
        f"confidence {confidence} sits on the boundary of the closed interval TR-016 "
        f"declares and must round-trip unchanged; got {stored!r}"
    )


def test_a_cited_page_below_one_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-015: pages are one-based, stated independently of which chunks exist.

    Reached by the check rather than by `fk_extracted_value__chunk_page`, because
    PostgreSQL evaluates check constraints before firing referential-integrity
    triggers. Both would reject page 0; only one of them states the domain, which
    is why the constraint name is part of the assertion.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extracted_value__cited_page_positive"
    ):
        insert_value(db_session, value_row(anchor, cited_page=0))


def test_a_value_citing_its_source_chunks_own_page_is_accepted(
    db_session: Session, anchor: Citation
) -> None:
    """TR-017, the positive half: an agreeing citation resolves in one join.

    Without this, every rejection below could be satisfied by a table nothing can
    ever be inserted into -- and the composite foreign key would be indisting-
    uishable from a broken one.
    """
    value_id = insert_value(db_session, value_row(anchor))

    resolved = db_session.execute(
        text(
            """
            SELECT chunk.page_number, document.title
            FROM extracted_value
            JOIN chunk ON chunk.chunk_id = extracted_value.source_chunk_id
                      AND chunk.page_number = extracted_value.cited_page
            JOIN document USING (document_id, document_type, project_id)
            WHERE extracted_value.extracted_value_id = :id
            """
        ),
        {"id": value_id},
    ).one()

    assert resolved == (anchor.page_number, SOURCE_DOCUMENT["title"])


def test_a_page_the_source_chunk_does_not_have_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-017: a page absent from the corpus entirely has no referent.

    The weaker of the two TR-017 cases, and the one a single-column foreign key
    would also catch. It is here as the baseline that the next test is measured
    against, not as the evidence.
    """
    absent_page = max(CHUNK_PAGES) + 1

    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__chunk_page"
    ):
        insert_value(db_session, value_row(anchor, cited_page=absent_page))


def test_a_page_belonging_to_a_different_chunk_is_rejected(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-017, the decisive case: the *pair* is what must resolve, not each half.

    This is the only test in the file that a plausible wrong implementation would
    pass. Both halves of the citation exist and are individually valid -- the
    source chunk is a real chunk, and the cited page is a real page of a real
    chunk in the same document. Only the combination is absent. So:

    * a single-column foreign key on `source_chunk_id` accepts this row;
    * a single-column check on `cited_page >= 1` accepts this row;
    * a foreign key on `cited_page` alone, if such a thing were written, accepts
      this row;
    * `fk_extracted_value__chunk_page`, referencing `uq_chunk__chunk_page
      (chunk_id, page_number)`, has no referent for it and refuses.

    That is TR-017's actual claim -- "the page citation must match the page of the
    chunk it was read from" -- and a mismatch here is exactly the silent failure
    Principle III names: a coordinator following the citation lands on a real page
    of a real document that does not contain the value. Refusing at write time
    turns an invisible wrong answer into a visible write failure.

    Enforced by a composite foreign key and **not** a trigger, which is a
    mechanical difference: a trigger can be disabled with `ALTER TABLE ... DISABLE
    TRIGGER`, is skipped by bulk-load paths, and has no referential action -- so a
    chunk's page changing underneath a stored citation would be invisible to it.
    The foreign key propagates that correction through `ON UPDATE CASCADE`
    instead.
    """
    anchor, other = citations[0], citations[1]
    assert anchor.page_number != other.page_number, "the fixture must give two distinct pages"

    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__chunk_page"
    ):
        insert_value(db_session, value_row(anchor, cited_page=other.page_number))


def test_a_value_citing_a_chunk_that_does_not_exist_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-015, TR-017: the other column of the same pair, broken on its own.

    A well-formed page number against an unknown chunk. Tested alongside the page
    cases so both columns of the composite reference are covered, which is what
    keeps the test suite from passing against a foreign key that had lost one of
    them.
    """
    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__chunk_page"
    ):
        insert_value(db_session, value_row(Citation(uuid4(), anchor.page_number)))


def test_deleting_a_chunk_a_value_cites_is_refused(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-015: `ON DELETE RESTRICT` -- a citation is never silently orphaned.

    The same foreign key read in the other direction. Dropping a chunk out from
    under the values that cite it would leave each of them pointing at nothing,
    which is the unattributable number Principle I forbids. `RESTRICT` makes a
    teardown an explicit, ordered operation instead.
    """
    insert_value(db_session, value_row(anchor))

    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__chunk_page"
    ):
        db_session.execute(
            text("DELETE FROM chunk WHERE chunk_id = :chunk_id"), {"chunk_id": anchor.chunk_id}
        )


#: The key `document.document_id` moves to. Well-formed under
#: `ck_document__id_format`, so the `UPDATE` reaches `fk_chunk__document` instead
#: of stopping at the format check, and a *different* string from
#: `SOURCE_DOCUMENT`'s, so a rename that happened and one that did nothing cannot
#: look alike.
RENAMED_SOURCE_DOCUMENT_ID = "example-piping-submittal-2026-r2"

#: The key-space change TR-078 describes, as the one statement a forward migration
#: would issue. It names only `document`: that nothing here touches `chunk` or
#: `extracted_value` is the entire point, since any propagation has to come from
#: the schema rather than from the statement.
RENAME_DOCUMENT_KEY = text(
    "UPDATE document SET document_id = :new_document_id WHERE document_id = :old_document_id"
)

#: The citation as a *consumer* reads it: out of `v_extracted_value_provenance`,
#: resolved through the chunk to the document it was read from. Reading it this
#: way rather than off `extracted_value`'s own columns is what makes the assertion
#: "the citation survives" instead of "the row was not deleted".
#:
#: The chunk join is spelled with an explicit `ON` and not `USING (chunk_id)`,
#: because the view and `chunk` both expose `page_number` and the merged-column
#: form leaves an unqualified reference to it ambiguous. Joining on *both*
#: citation columns is also the stronger form: the query returns a row only while
#: the cited page still agrees with its chunk's page, which is what the citation
#: means (TR-017).
#:
#: The `ORDER BY` lives here and not in the view (TR-060), exactly as it does in
#: `provenance_of`.
CITATION_THROUGH_THE_PROVENANCE_VIEW = text(
    """
    SELECT provenance.contributor_ordinal,
           provenance.chunk_id,
           provenance.page_number,
           document.document_id AS resolved_document_id,
           document.title AS resolved_title
    FROM v_extracted_value_provenance AS provenance
    JOIN chunk ON chunk.chunk_id = provenance.chunk_id
              AND chunk.page_number = provenance.page_number
    JOIN document ON document.document_id = chunk.document_id
                 AND document.document_type = chunk.document_type
                 AND document.project_id = chunk.project_id
    WHERE provenance.extracted_value_id = :extracted_value_id
    ORDER BY provenance.contributor_ordinal
    """
)

#: The citation row's own columns, plus the two facts that reveal a rewrite.
#:
#: `ctid` is the load-bearing one. PostgreSQL implements every `UPDATE` -- one
#: performed by a referential action included -- as a new tuple version at a new
#: physical location, so an unchanged `ctid` says this row was not rewritten at
#: all, which is a stronger claim than "its columns still hold the same values".
#: It is stable for the life of the test because nothing inside a transaction
#: moves a live tuple: plain `VACUUM` never relocates one and `VACUUM FULL` cannot
#: run in a transaction block.
#:
#: `extracted_at` is **corroborating and not the assertion**. No cascade would
#: have rewritten that column even if one had reached this row -- there is no
#: trigger on it -- so on its own it would be evidence of very little.
STORED_CITATION_ROW_IDENTITY = text(
    """
    SELECT extracted_value_id, source_chunk_id, cited_page, extracted_at, ctid::text AS ctid
    FROM extracted_value
    WHERE extracted_value_id = :extracted_value_id
    """
)

#: Unqualified reads, with no `WHERE`. `db_session` commits nothing, so the only
#: rows visible are the calling test's -- and reading the whole table is what makes
#: "one document row afterwards" a claim about every row rather than about the one
#: the test remembered to look at.
ALL_DOCUMENT_KEYS = text("SELECT document_id FROM document ORDER BY document_id")
EXTRACTED_VALUE_ROW_COUNT = text("SELECT count(*) FROM extracted_value")


def test_tr078_a_stored_citation_survives_a_document_key_space_change(
    db_session: Session, source_document: Mapping[str, Any], anchor: Citation
) -> None:
    """TR-078, third clause: a citation is untouched by an in-place key change.

    **This is the end-to-end half of TR-078. The mechanical half is
    `test_chunk.py::test_updating_a_document_key_cascades_to_every_chunk`**, and
    the two are meant to be found from each other. That one covers the first two
    clauses -- the forward migration updates the key in place, and it propagates to
    every chunk by cascade -- and stands in for this third clause by asserting the
    *reason* it should hold: `chunk_id` is unchanged across the cascade, and
    `chunk_id` is half of what `extracted_value.(source_chunk_id, cited_page)`
    references. It never inserts a citation.

    That inference is sound and it is still not the claim. Three things are only
    observable with a citation row actually present while the cascade runs:

    * **The cascade rewrites the referenced rows.** Every `chunk` row the rename
      touches is the referenced side of three foreign keys from `0006` --
      `fk_extracted_value__chunk_page`, `fk_evcc__chunk_page` and
      `fk_extraction_failure__chunk` -- each `MATCH FULL ON DELETE RESTRICT ON
      UPDATE CASCADE`. That the rewrite trips none of those `RESTRICT` edges and
      fires none of those cascades rests on PostgreSQL comparing the *referenced
      key* old-to-new and doing nothing when it is equal. With no citation in the
      table there is nothing for that machinery to consider, so nothing about it is
      tested.
    * **`chunk_id` surviving is weaker than the citation resolving.** A state where
      every `chunk_id` is preserved *and* the citation resolves to the wrong
      document is constructible -- `ON UPDATE SET DEFAULT` on `fk_chunk__document`
      with a decoy document to land on produces exactly it, and the mechanical
      assertion passes throughout. Only the join-through assertion below catches
      that, which is the whole reason this test exists rather than being folded
      into the other one.
    * **The consumer's read path.** The citation is read back through
      `v_extracted_value_provenance` and resolved onward to the document, because
      "the citation survives" is a claim about what a reader can recover, not about
      which bytes are still in the row.

    Also asserted: the row was not *rewritten*, by `ctid`. A cascade that reached
    this row and set its citation columns to the values they already held would
    leave every column assertion above green while having written a new tuple
    version, and `ctid` is what distinguishes those two worlds. `extracted_at` is
    checked beside it as corroboration only -- see
    `STORED_CITATION_ROW_IDENTITY`.

    Why the requirement's last words matter: "so no reload of loaded rows is
    required". If a key-space change forced a delete-and-reload of the chunks, each
    reloaded chunk would take a new `chunk_id`, and every stored citation in the
    corpus would point at a row that no longer exists. That is the unattributable
    number Principle I forbids, arrived at by a migration nobody thought was
    destructive.
    """
    old_document_id = source_document["document_id"]
    value_id = insert_value(db_session, value_row(anchor))

    before_citation = db_session.execute(
        CITATION_THROUGH_THE_PROVENANCE_VIEW, {"extracted_value_id": value_id}
    ).all()
    before_row = db_session.execute(
        STORED_CITATION_ROW_IDENTITY, {"extracted_value_id": value_id}
    ).one()

    assert [row.resolved_document_id for row in before_citation] == [old_document_id], (
        "the citation must resolve to the *old* key before the rename, or the assertions "
        f"after it are vacuous; got {[row.resolved_document_id for row in before_citation]!r}"
    )

    renamed = db_session.execute(
        RENAME_DOCUMENT_KEY,
        {"new_document_id": RENAMED_SOURCE_DOCUMENT_ID, "old_document_id": old_document_id},
    )
    assert renamed.rowcount == 1, (
        f"the key-space change must update exactly one document row; it matched "
        f"{renamed.rowcount}. A refusal here means `fk_chunk__document` no longer carries "
        f"ON UPDATE CASCADE, and TR-078's forward migration is impossible rather than merely "
        f"untested"
    )
    assert db_session.execute(ALL_DOCUMENT_KEYS).scalars().all() == [RENAMED_SOURCE_DOCUMENT_ID], (
        "the key moved *in place*: one document row afterwards, carrying the new key. Two "
        "rows would mean the change had been performed as a copy, which is the reload TR-078 "
        "says is not required"
    )

    after_citation = db_session.execute(
        CITATION_THROUGH_THE_PROVENANCE_VIEW, {"extracted_value_id": value_id}
    ).all()
    after_row = db_session.execute(
        STORED_CITATION_ROW_IDENTITY, {"extracted_value_id": value_id}
    ).one()

    assert len(after_citation) == 1, (
        "the value's one citation must still be recoverable through the provenance view "
        f"after the rename; the view now returns {after_citation!r}. Zero rows means the "
        "citation stopped resolving -- either the row is gone, or its page no longer agrees "
        "with its chunk's, which is what the join in this query refuses to paper over"
    )
    citation = after_citation[0]
    assert (citation.chunk_id, citation.page_number) == (anchor.chunk_id, anchor.page_number), (
        "the citation must still name the same chunk and the same page (TR-078: citations "
        "reference the chunk, not the document, so a document rename cannot move them); got "
        f"{(citation.chunk_id, citation.page_number)!r}, expected "
        f"{(anchor.chunk_id, anchor.page_number)!r}"
    )
    assert citation.resolved_document_id == RENAMED_SOURCE_DOCUMENT_ID, (
        "the citation must resolve onward to the *renamed* document. This is the assertion "
        "the mechanical half in test_chunk.py cannot make: an unchanged chunk_id says the "
        "citation still points at the same chunk, and says nothing about which document that "
        f"chunk now belongs to. Got {citation.resolved_document_id!r}"
    )
    assert citation.resolved_title == source_document["title"], (
        "and it must be the same document under a new key rather than a different document "
        f"the chunk was re-pointed at; got title {citation.resolved_title!r}"
    )

    surviving_rows = db_session.execute(EXTRACTED_VALUE_ROW_COUNT).scalar_one()
    assert surviving_rows == 1, (
        "no citation row may be added or removed by a key-space change; the table holds "
        f"{surviving_rows} rows"
    )

    identity_before = (
        before_row.extracted_value_id,
        before_row.source_chunk_id,
        before_row.cited_page,
    )
    identity_after = (
        after_row.extracted_value_id,
        after_row.source_chunk_id,
        after_row.cited_page,
    )
    assert identity_after == identity_before, (
        "the primary key and both citation columns must be identical across the rename; they "
        f"went from {identity_before!r} to {identity_after!r}"
    )
    assert after_row.ctid == before_row.ctid, (
        "the citation row must not have been *rewritten*, which is stronger than its columns "
        "still holding the same values: a cascade that reached this row and set the citation "
        "to what it already was would leave every assertion above green while writing a new "
        f"tuple version. ctid moved from {before_row.ctid} to {after_row.ctid}, so something "
        "did reach it -- TR-078's 'untouched' is false and one of the three ON UPDATE CASCADE "
        "edges onto `chunk` is firing when the referenced key has not changed"
    )
    assert after_row.extracted_at == before_row.extracted_at, (
        f"corroborating only: extracted_at went from {before_row.extracted_at!r} to "
        f"{after_row.extracted_at!r}. The ctid assertion above is the one that carries the "
        "claim -- a rewrite would not have touched this column either"
    )


# --------------------------------------------------------------------------- #
# T025 -- multi-source provenance (TR-018, TR-059, TR-060)
# --------------------------------------------------------------------------- #

VIEW_DEFINITION = text("SELECT pg_get_viewdef('v_extracted_value_provenance'::regclass, true)")

DECLARED_AND_RECOVERED_COUNTS = text(
    """
    SELECT
        extracted_value.source_chunk_count AS declared,
        (
            SELECT count(*)
            FROM v_extracted_value_provenance
            WHERE v_extracted_value_provenance.extracted_value_id
                  = extracted_value.extracted_value_id
        ) AS recovered
    FROM extracted_value
    WHERE extracted_value.extracted_value_id = :extracted_value_id
    """
)

DISTINCT_SOURCE_CHUNKS = text(
    """
    SELECT count(*) AS rows, count(DISTINCT chunk_id) AS distinct_chunks
    FROM v_extracted_value_provenance
    WHERE extracted_value_id = :extracted_value_id
    """
)


def test_a_three_chunk_value_is_fully_recoverable_through_the_provenance_view(
    db_session: Session, citations: tuple[Citation, ...]
) -> None:
    """TR-018, TR-059: every contributing chunk and page, anchor included, in one read.

    The anchor `(source_chunk_id, cited_page)` **is** contributor 1 and lives on
    the value row; contributors 2 and 3 live in
    `extracted_value_contributing_chunk`. Two things follow, and both are asserted
    here rather than assumed:

    * the view returns exactly three rows -- one per declared source, with the
      anchor present, so "is the anchor also a row in the child table?" has one
      answer instead of a convention;
    * ordinal 1 is the anchor pair, which is what makes TR-015's non-nullable
      citation stay meaningful for a multi-source value: the anchor is a pair of
      columns, so it cannot be absent however many contributors there are.

    `UNION ALL` and not `UNION`: the only way two rows could collide is a defect
    -- `uq_evcc__value_chunk` and `ck_evcc__ordinal_min` already make the anchor
    unrepeatable within the child table -- so `UNION` would hide that defect and
    pay for a sort to do it.
    """
    anchor, second, third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=3))
    insert_contributor(db_session, contributor_row(value_id, 2, second, 3))
    insert_contributor(db_session, contributor_row(value_id, 3, third, 3))

    recovered = provenance_of(db_session, value_id)

    assert len(recovered) == 3, (
        "a three-chunk value must recover exactly three citations -- one per declared "
        f"source, the anchor included; got {recovered!r}"
    )
    assert recovered[0] == anchor, (
        "ordinal 1 denotes the anchor citation carried on the value row itself; "
        f"got {recovered[0]!r}, expected {anchor!r}"
    )
    assert set(recovered) == {anchor, second, third}, (
        "every contributing chunk and its page must be recoverable (TR-018); "
        f"got {set(recovered)!r}"
    )

    declared, recovered_count = db_session.execute(
        DECLARED_AND_RECOVERED_COUNTS, {"extracted_value_id": value_id}
    ).one()
    assert (declared, recovered_count) == (3, 3), (
        "the declared source count and the count the view recovers must agree on a "
        f"correctly written value; got declared={declared}, recovered={recovered_count}"
    )


def test_the_provenance_view_declares_no_ordering(db_session: Session) -> None:
    """TR-060: contributor ordinals carry no precedence, so the view states no order.

    Asserted against the stored view definition rather than against a returned row
    order, which is the only honest form: an unordered query may *happen* to come
    back sorted, so observing sorted output proves nothing either way. The absence
    of `ORDER BY` in the definition is the schema's actual statement.

    Why it matters: ordinals are identity within the contributor set, not rank. An
    ordered view would invite a reader to treat ordinal 2 as "more primary" than
    ordinal 3 and to infer importance, confidence, or document order from a number
    that carries none of them. A consumer that wants an order states one -- as
    `provenance_of` in this file does.
    """
    definition = db_session.execute(VIEW_DEFINITION).scalar_one()

    assert "ORDER BY" not in definition.upper(), (
        "v_extracted_value_provenance must not order its output: ordinal 1 denotes the "
        "anchor and 2..N are a stable but unordered enumeration carrying no precedence "
        f"(TR-060). Definition was:\n{definition}"
    )
    assert "UNION ALL" in definition.upper(), (
        "the view must union the anchor with the contributor rows without deduplicating "
        "-- a collision is a defect and UNION would hide it. Definition was:\n"
        f"{definition}"
    )


def test_gap_g1_a_contributor_ordinal_gap_is_accepted_and_shows_as_a_short_citation_set(
    db_session: Session, citations: tuple[Citation, ...]
) -> None:
    """Gap G-1, asserted as the disclosure it is -- not as a guarantee.

    A contributor-ordinal *gap* -- `source_chunk_count = 3` with only ordinal 2
    present -- is **not enforced**, and `data-model.md` records why: it is a
    cross-row count, a `CHECK` cannot see sibling rows, and a deferred `CHECK` is
    impossible in PostgreSQL. So this test asserts exactly what the gap-disclosure
    record says happens, which is *not* that the row is refused:

        "At runtime the provenance view returns fewer contributors than
        `source_chunk_count` declares, so a reader sees an incomplete citation set
        rather than a wrong one."

    That distinction is the whole reason the gap is acceptable at this scale. An
    incomplete citation set under-claims: a coordinator sees two sources where
    three were used, and every citation they *are* shown resolves correctly. It
    never shows a page the value did not come from. Principle III's bias toward
    the visible failure is satisfied by under-claiming, not by silence.

    The assertion below is also the covering query the gap table names -- "a test
    over `v_extracted_value_provenance` asserting the recovered contributor count
    equals `source_chunk_count`" -- run here in its detecting form, so the same
    query that fails a build on real data is the one exercised.
    """
    anchor, second, _third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=3))

    # Accepted. No constraint spans the parent row and its children, so there is
    # nothing here to reject it; if this raised, the gap would be closed and the
    # disclosure would be the thing that is wrong.
    insert_contributor(db_session, contributor_row(value_id, 2, second, 3))

    declared, recovered = db_session.execute(
        DECLARED_AND_RECOVERED_COUNTS, {"extracted_value_id": value_id}
    ).one()

    assert declared == 3, "the value declares three sources"
    assert recovered == 2, (
        "the anchor plus one contributor is two recoverable citations; if this is 3 the "
        f"fixture wrote a contributor it did not mean to. Got {recovered}"
    )
    assert recovered < declared, (
        "G-1's disclosed runtime outcome is an *incomplete* citation set -- fewer "
        "recovered than declared, never more and never a wrong page. If recovered ever "
        "exceeded declared, the gap would be an over-claim and no longer acceptable"
    )

    ordinals = [citation for citation in provenance_of(db_session, value_id)]
    assert ordinals == [anchor, second], (
        "the citations that are present must still each resolve correctly -- an "
        f"incomplete set, not a corrupted one; got {ordinals!r}"
    )


def test_gap_g2_an_anchor_reappearing_as_a_contributor_is_accepted_and_overstates_sources(
    db_session: Session, anchor: Citation
) -> None:
    """Gap G-2, asserted as the disclosure it is -- not as a guarantee.

    The anchor chunk also appearing as a contributor row is **not enforced**: it is
    a cross-row comparison between parent and child, and no `CHECK` can span two
    tables. The gap-disclosure record states the runtime outcome:

        "At runtime the anchor chunk appears twice in the provenance view,
        overstating the number of distinct sources."

    Note the direction of the error, which is what makes G-2 different from G-1
    and worse: this one *over*-claims. A reader counting rows in the view sees two
    sources where one chunk supplied the value. Every page shown is still a page
    the value genuinely came from -- so no citation resolves wrongly -- but the
    breadth of the evidence is inflated. That is why the recorded production-scale
    alternative is to move the anchor into the contributor table as ordinal 1 and
    drop the anchor columns, which converts the duplication into a primary-key
    collision.

    The second half of this test is the contrast that makes G-2 a disclosed gap
    rather than an oversight: duplication *within* the contributor table is
    refused by `uq_evcc__value_chunk`. Only duplication across the
    anchor/contributor boundary escapes, and it escapes because the boundary is a
    table boundary that no per-row constraint can reach.
    """
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=2))

    # Accepted: `uq_evcc__value_chunk` is UNIQUE (extracted_value_id, chunk_id) on
    # the *child table only*, and the anchor is a pair of columns on the parent.
    insert_contributor(db_session, contributor_row(value_id, 2, anchor, 2))

    rows, distinct_chunks = db_session.execute(
        DISTINCT_SOURCE_CHUNKS, {"extracted_value_id": value_id}
    ).one()

    assert rows == 2, f"the view reports two contributors; got {rows}"
    assert distinct_chunks == 1, (
        "both rows name the same chunk, so exactly one chunk supplied this value; if "
        f"this is 2 the fixture used two chunks and the gap was never exercised. Got "
        f"{distinct_chunks}"
    )
    assert distinct_chunks < rows, (
        "G-2's disclosed runtime outcome is an *overstated* source count -- more rows in "
        "the view than distinct chunks behind them. This is the detection query a build "
        "fails on, since no constraint carries it"
    )


def test_a_contributor_repeating_a_chunk_within_the_child_table_is_rejected(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """The enforced half of G-2's neighbourhood: one contributor row per chunk.

    Two ordinals naming the same chunk would inflate the recovered contributor
    count while the citation set stayed the same size -- the same over-claim G-2
    discloses, but reachable *inside* one table, where a unique constraint can see
    it. So it is enforced, and the boundary between what is carried and what is
    disclosed is drawn exactly where the mechanism runs out.
    """
    anchor, second, _third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=3))
    insert_contributor(db_session, contributor_row(value_id, 2, second, 3))

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "uq_evcc__value_chunk"):
        insert_contributor(db_session, contributor_row(value_id, 3, second, 3))


def test_ordinal_one_is_not_available_to_a_contributor_row(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-059: ordinal 1 denotes the anchor, which is a pair of columns on the parent.

    The floor is 2 and not 1 for a reason that is not stylistic: writing the
    anchor a second time as a contributor row would double-count it against
    `source_chunk_count` and make the provenance view return it twice. That is
    gap G-2's failure mode arrived at by a different route, and this constraint is
    what closes the route that *can* be closed.
    """
    anchor, second, _third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=2))

    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_evcc__ordinal_min"):
        insert_contributor(db_session, contributor_row(value_id, 1, second, 2))


def test_a_contributor_ordinal_beyond_the_declared_count_is_rejected(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-018, first half of the pair: the check, with the child's count honest.

    The value declares two sources, so ordinals run 1 (the anchor) and 2. A
    contributor at ordinal 3 carrying the parent's real count of 2 is refused by
    `ck_evcc__ordinal_within_declared_count`, a per-row check that compares two
    columns of the same row.

    That check is only sound because `source_chunk_count` on the child is pinned
    to the parent's by `fk_evcc__value_count`. On its own it would be comparing
    the ordinal against a number the child invented -- which is the next test.
    """
    anchor, second, _third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=2))

    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_evcc__ordinal_within_declared_count"
    ):
        insert_contributor(db_session, contributor_row(value_id, 3, second, 2))


def test_a_contributor_inflating_its_own_declared_count_trips_the_foreign_key(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-018, second half of the pair: the escape route, and the constraint that closes it.

    Same intent as the previous test -- a third contributor under a value that
    declares two sources -- but the child now claims `source_chunk_count = 3` to
    make its own check pass. And it does pass: `3 <= 3` is true, so
    `ck_evcc__ordinal_within_declared_count` has nothing to say.

    `fk_evcc__value_count` catches it instead. It references
    `uq_extracted_value__id_source_count (extracted_value_id, source_chunk_count)`,
    so `(this value, 3)` has no referent when the parent declares 2 -- the child
    cannot invent a count without inventing a parent to go with it.

    **Both halves are required, and this pair of tests is the evidence.** Neither
    constraint is worth anything alone: the check alone compares the ordinal
    against a number the child chose, and the foreign key alone would happily
    accept ordinal 9 under a truthfully-declared count of 3. Deleting either one
    leaves a schema that still passes one of these two tests, which is exactly why
    there are two.
    """
    anchor, second, _third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=2))

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, "fk_evcc__value_count"):
        insert_contributor(db_session, contributor_row(value_id, 3, second, 3))


def test_a_contributor_citing_a_page_its_chunk_does_not_have_is_rejected(
    db_session: Session,
    citations: tuple[Citation, ...],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-017 for the non-anchor contributors, against the same parent key.

    `fk_evcc__chunk_page` points at `uq_chunk__chunk_page` just as the anchor's
    foreign key does, so a contributor citing a page belonging to a *different*
    chunk has no referent either. Tested with the decisive form -- both halves
    individually valid, only the pair absent -- for the reason given on
    `test_a_page_belonging_to_a_different_chunk_is_rejected`: the weaker form
    would pass against a single-column foreign key.
    """
    anchor, second, third = citations
    value_id = insert_value(db_session, multi_chunk_value_row(anchor, declared_count=2))
    crossed = Citation(second.chunk_id, third.page_number)

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, "fk_evcc__chunk_page"):
        insert_contributor(db_session, contributor_row(value_id, 2, crossed, 2))


def test_provenance_kind_and_source_count_cannot_disagree(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-018: the two provenance facts are a biconditional, not two loose columns.

    `multi_chunk` with a count of 1 is refused as firmly as `single_chunk` with a
    count of 3. Both directions, because a one-sided implication would let one of
    the two states through and a reader filtering on `provenance_kind` would then
    disagree with one counting contributors.
    """
    disagreements = (
        {"provenance_kind": "multi_chunk", "source_chunk_count": 1},
        {"provenance_kind": "single_chunk", "source_chunk_count": 3},
    )
    for overrides in disagreements:
        with assert_rejects(
            db_session,
            psycopg.errors.CheckViolation,
            "ck_extracted_value__provenance_agrees_with_count",
        ):
            insert_value(db_session, value_row(anchor, **overrides))


# --------------------------------------------------------------------------- #
# T026 -- failure is the only representation of a failed extraction
#         (TR-019, TR-044, TR-061)
# --------------------------------------------------------------------------- #

#: The seven-member outcome set `ck_extraction_failure__outcome` declares.
#: `missing_citation` is the member that makes TR-015 coherent: a value whose
#: source page cannot be identified is unstorable as a value, so it has to land
#: somewhere, and without this outcome the only options would be discarding the
#: observation or relaxing TR-015.
DECLARED_OUTCOMES = (
    "no_value_found",
    "unparseable_value",
    "type_coercion_failed",
    "schema_violation",
    "missing_citation",
    "confidence_below_threshold",
    "repair_budget_exhausted",
)

ATTEMPTS_PRESENT_IN_BOTH_TABLES = text(
    """
    SELECT count(*)
    FROM extracted_value
    JOIN extraction_failure
      ON extraction_failure.source_chunk_id = extracted_value.source_chunk_id
     AND extraction_failure.attempted_page = extracted_value.cited_page
     AND extraction_failure.field_name = extracted_value.field_name
    WHERE extracted_value.source_chunk_id = :chunk_id
    """
)

TERM_OF_VALUE = text(
    """
    SELECT field_vocabulary.field_name, field_vocabulary.label, field_vocabulary.retired_at
    FROM extracted_value
    JOIN field_vocabulary USING (field_name, value_kind)
    WHERE extracted_value.extracted_value_id = :extracted_value_id
    """
)


@pytest.mark.parametrize("outcome", DECLARED_OUTCOMES)
def test_every_declared_outcome_records_a_failure(
    db_session: Session, anchor: Citation, outcome: str
) -> None:
    """TR-019: a failed extraction is recordable, with the attempt fully attributed.

    Every member of the set, not only the interesting one, because a check written
    with a member missing is a rejection nobody would notice until an ingestion run
    hit that outcome in production.

    The attempted source is `NOT NULL` even for `missing_citation`: what is missing
    in that case is a citation for the *value*, not knowledge of which chunk was
    read. The attempt is therefore as traceable as a success would have been,
    which is what makes "which fields fail most, on which pages" answerable.
    """
    failure_id = insert_failure(db_session, failure_row(anchor, outcome=outcome))

    stored = db_session.execute(
        text(
            """
            SELECT source_chunk_id, attempted_page, field_name, outcome, repair_attempt_count
            FROM extraction_failure WHERE extraction_failure_id = :id
            """
        ),
        {"id": failure_id},
    ).one()

    assert stored == (anchor.chunk_id, anchor.page_number, TEXT_TERM, outcome, 0)


def test_a_missing_citation_failure_records_what_a_value_row_could_not_hold(
    db_session: Session, anchor: Citation
) -> None:
    """TR-019, TR-061: the two halves of "no partial value row", asserted together.

    This is the pair that makes the failure table necessary rather than merely
    convenient, and it is one test on purpose -- the claim is about the
    *relationship* between the two tables, so splitting it would let either half
    pass while the other went missing.

    Half one: the failure inserts. An extraction whose source page could not be
    identified is recorded, with the chunk it was attempted from, the field, and a
    non-blank detail explaining it.

    Half two: the same observation is **unrepresentable** as a value row. Not
    "discouraged", not "detectable afterwards" -- the citation columns are
    `NOT NULL`, so there is no row shape that expresses "a value, source page
    unknown". That is why `missing_citation` exists as an outcome, and it is
    Principle III at the storage boundary: a value failing validation is recorded
    as absent rather than stored wrong.
    """
    failure_id = insert_failure(
        db_session,
        failure_row(
            anchor,
            outcome="missing_citation",
            detail="Value read from a continuation table whose page header did not parse.",
        ),
    )
    assert failure_id is not None

    stored_outcome = db_session.execute(
        text("SELECT outcome FROM extraction_failure WHERE extraction_failure_id = :id"),
        {"id": failure_id},
    ).scalar_one()
    assert stored_outcome == "missing_citation"

    # The same attempt, tried as a value with no identifiable source page.
    assert_not_null_violation(db_session, value_row(anchor, cited_page=None), "cited_page")
    assert_not_null_violation(
        db_session, value_row(anchor, source_chunk_id=None), "source_chunk_id"
    )


#: `value_text` is `NOT NULL` *and* non-blank, so an empty value row is
#: unrepresentable. The trim set is spelled out in the DDL because
#: single-argument `btrim` strips spaces only -- a value of one tab or one
#: vertical tab would otherwise satisfy a naive check while carrying nothing.
#: The vertical-tab case is the one that catches `E'\\v'`, which PostgreSQL reads
#: as the letter `v` rather than as a control character.
BLANK_VALUE_TEXTS = ("", "   ", "\t", "\n", "\r", "\f", "\v")


@pytest.mark.parametrize("value_text", BLANK_VALUE_TEXTS)
def test_a_value_row_carrying_no_value_is_rejected(
    db_session: Session,
    anchor: Citation,
    assert_rejects: RejectionAsserter,
    value_text: str,
) -> None:
    """TR-019: the structural half of "with no partial value row".

    A value row with nothing in it is not a weak record, it is a record that
    claims an extraction succeeded while carrying no extraction. The failure table
    is the only representation of that state, and this check is what makes it the
    only one.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extracted_value__value_text_present"
    ):
        insert_value(db_session, value_row(anchor, value_text=value_text))


def test_a_failure_record_with_a_blank_detail_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-019: a failure whose detail explains nothing defeats the point of recording it.

    Same widened trim set and the same `\\u000B` reasoning as the value-text
    check, tested with the vertical tab because that is the character a literal
    reading of `data-model.md`'s `E'\\v'` would have let through.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extraction_failure__detail_present"
    ):
        insert_failure(db_session, failure_row(anchor, detail="\v"))


def test_an_undeclared_outcome_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-019: the outcome set is closed, so a reader can enumerate the failure modes.

    An unrecognised outcome would make "which failures need a repair pass" depend
    on a string nobody registered, and a dashboard grouping by outcome would grow
    a category silently.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extraction_failure__outcome"
    ):
        insert_failure(db_session, failure_row(anchor, outcome="page_was_upside_down"))


def test_a_failure_against_an_unknown_field_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-044: a failure draws on the same vocabulary a success does.

    Single-column here, unlike `fk_extracted_value__field`: a failure stores no
    typed value, so it has no numeric-column rule to reduce and no reason to carry
    `value_kind`. What both share is membership -- which is what makes "which
    fields fail most" answerable by joining the two tables against one term list
    rather than reconciling two free-text columns.
    """
    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extraction_failure__field"
    ):
        insert_failure(db_session, failure_row(anchor, field_name="no_such_field"))


def test_gap_g5_one_attempt_can_hold_both_a_value_row_and_a_failure_row(
    db_session: Session, anchor: Citation
) -> None:
    """Gap G-5, asserted as the disclosure it is -- not as a guarantee.

    OBJ3 VC4 has two halves and only one of them is carried by the schema.

    *Carried:* a value row cannot be partial. `value_text` is `NOT NULL` and
    non-blank, the citation is `NOT NULL`, and confidence is `NOT NULL` -- so
    "a value row that is really a failure" has no representation. That half is
    asserted by `test_a_value_row_carrying_no_value_is_rejected` and
    `test_a_missing_citation_failure_records_what_a_value_row_could_not_hold`.

    *Not carried:* that the writer chose exactly **one** of the two tables. This
    is cross-table absence -- no `CHECK` can query another table -- and the
    gap-disclosure record states the runtime outcome plainly:

        "At runtime both a value row and a failure row could exist for one
        attempt, so a field reads as extracted and as failed at once."

    So this test inserts both for the same `(chunk, page, field)` attempt, shows
    neither is refused, and then runs the detection query that makes it a
    build-time failure on real data. The recorded production-scale alternative is
    a single attempt table with a nullable value and a discriminator, which makes
    the two states mutually exclusive by construction.
    """
    insert_value(db_session, value_row(anchor))
    insert_failure(db_session, failure_row(anchor, outcome="confidence_below_threshold"))

    contradictions = db_session.execute(
        ATTEMPTS_PRESENT_IN_BOTH_TABLES, {"chunk_id": anchor.chunk_id}
    ).scalar_one()

    assert contradictions == 1, (
        "G-5 is disclosed as uncarried: one attempt present in both tables is accepted "
        "by the database and detected by this query, not refused by a constraint. If "
        f"this is 0 the two rows did not describe the same attempt. Got {contradictions}"
    )


def test_a_new_vocabulary_term_is_unusable_before_it_exists_and_usable_by_insert_after(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-044: the vocabulary grows by `INSERT`, and no term is usable before it is defined.

    Both directions in one test, because TR-044 is a conjunction and each half
    alone is satisfiable by something wrong. A schema that accepted any string
    would pass "a new term is usable"; a frozen enum would pass "no term can be
    used before it is defined" while failing to grow at all.

    The statement adding the term is a plain `INSERT` -- **no `ALTER TYPE`, no
    `ALTER TABLE`, no DDL of any kind**, which is the property that makes this a
    lookup table rather than an enum. It matters for two mechanical reasons
    recorded in the research: an enum value cannot be retired under a forward-only
    chain, and a value added by `ALTER TYPE` is unusable until the transaction
    commits -- which breaks any migration that adds a term and backfills with it
    in one revision.

    That `INSERT` runs here inside the same savepoint-scoped transaction as the
    value that then uses it, which is the direct demonstration: had a DDL step
    been required, this test could not be written this way at all.
    """
    new_term = {
        "field_name": "lead_time_buffer_days",
        "value_kind": "number",
        "label": "Lead Time Buffer (days)",
        "description": "Buffer a coordinator added on top of the vendor's quoted lead time.",
    }

    # Before: the term does not exist, so the composite field foreign key refuses.
    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__field"
    ):
        insert_value(
            db_session,
            value_row(
                anchor,
                field_name=new_term["field_name"],
                value_kind="number",
                value_text="10",
                value_number=Decimal("10"),
            ),
        )

    # One INSERT. No DDL.
    db_session.execute(VOCABULARY_INSERT, new_term)

    # After: usable immediately, in the same transaction.
    value_id = insert_value(
        db_session,
        value_row(
            anchor,
            field_name=new_term["field_name"],
            value_kind="number",
            value_text="10",
            value_number=Decimal("10"),
        ),
    )

    resolved = db_session.execute(TERM_OF_VALUE, {"extracted_value_id": value_id}).one()
    assert resolved == (new_term["field_name"], new_term["label"], None)


def test_a_value_declaring_a_kind_the_vocabulary_does_not_give_it_is_rejected(
    db_session: Session, anchor: Citation, assert_rejects: RejectionAsserter
) -> None:
    """TR-044, TR-045: `value_kind` is carried in by the foreign key, not asserted by the writer.

    `quantity` is a `number` term. A row claiming it is `text` names a
    `(field_name, value_kind)` pair absent from `uq_field_vocabulary__name_kind`
    and is refused. That is what keeps the denormalized `value_kind` from
    drifting -- unable to, rather than unlikely to -- and it is the whole reason
    `ck_extracted_value__numeric_iff_number_kind` can be a single-row check
    instead of a trigger reading another table.
    """
    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_extracted_value__field"
    ):
        insert_value(
            db_session, value_row(anchor, field_name=NUMBER_TERM, value_kind="text", value_text="4")
        )


def test_gap_g7_a_retired_term_still_resolves_and_stays_insertable(
    db_session: Session, anchor: Citation
) -> None:
    """Gap G-7, asserted as the disclosure it is -- not as a guarantee.

    Retirement is **advisory**. `field_vocabulary.retired_at` carries no
    constraint, and the gap-disclosure record says why the database cannot carry
    one: "a FK checks existence, not a sibling column's value". The recorded
    runtime outcome is that "a retired term stays insertable, so retirement is
    advisory only", and E006 is where the filter on `retired_at IS NULL` lives.

    Two things are asserted, and the first is the reason the second is acceptable:

    * **A historical row still resolves through its term after retirement.** This
      is not incidental -- it is the requirement that rules out the obvious
      alternative. Deleting the term instead of retiring it is already impossible
      (`fk_extracted_value__field ON DELETE RESTRICT`, TR-079), and the recorded
      production-scale alternative -- a partial unique index on
      `(field_name, value_kind) WHERE retired_at IS NULL` -- would remove the
      referent from *every* row, including the ones written before retirement.
      Under Principle I a stored citation whose field no longer resolves is worse
      than a term that stays insertable a while longer.
    * **A new row using the retired term is still accepted.** The gap itself,
      detectable by exactly this insert succeeding, which is what makes it a
      build-time failure once E006's filter is the thing under test.
    """
    historical_id = insert_value(db_session, value_row(anchor))

    db_session.execute(RETIRE_TERM, {"retired_at": date(2026, 6, 30), "name": TEXT_TERM})

    resolved = db_session.execute(TERM_OF_VALUE, {"extracted_value_id": historical_id}).one()
    assert resolved.field_name == TEXT_TERM, (
        "a row written before retirement must keep resolving to its term, or every "
        "citation stored under it becomes unreadable -- the failure Principle I forbids"
    )
    assert resolved.retired_at == date(2026, 6, 30), (
        "and the retirement must be visible to the reader that resolves it, so E006 can "
        f"filter on it; got {resolved.retired_at!r}"
    )

    # The gap: nothing refuses a *new* row against the retired term.
    later_id = insert_value(db_session, value_row(anchor, value_text="Victaulic"))
    still_insertable = db_session.execute(
        text("SELECT count(*) FROM extracted_value WHERE extracted_value_id = :id"),
        {"id": later_id},
    ).scalar_one()
    assert still_insertable == 1, (
        "G-7 is disclosed as uncarried: a retired term stays insertable. If this row had "
        "been refused the gap would be closed and the disclosure would be wrong"
    )


# --------------------------------------------------------------------------- #
# T027 -- the value's shape, and what it deliberately does not reference
#         (TR-045, TR-054, TR-081, TR-082, TR-085)
# --------------------------------------------------------------------------- #

FOREIGN_KEY_TARGETS_OF_EXTRACTED_VALUE = text(
    """
    SELECT DISTINCT confrelid::regclass::text AS target_relation
    FROM pg_constraint
    WHERE conrelid = 'extracted_value'::regclass
      AND contype = 'f'
    """
)

#: The value's only outbound references. A closed set, not a blocklist: naming
#: what *is* permitted keeps holding as later migrations add tables, whereas a
#: list of forbidden targets has to be extended every time one appears.
PERMITTED_FOREIGN_KEY_TARGETS = frozenset({"chunk", "field_vocabulary"})

#: The relation TR-045 and SC-023 forbid a direct reference to. It does not exist
#: yet -- migration `0007` creates it -- which is why the assertion below is
#: phrased as "no foreign key targets a relation of this name" rather than
#: resolving the name through `regclass`. That phrasing holds now and keeps
#: holding after `0007` lands, with no edit.
FORBIDDEN_FOREIGN_KEY_TARGET = "purchase_order_line"

CONFIDENCE_COLUMN_TYPE = text(
    """
    SELECT format_type(atttypid, atttypmod)
    FROM pg_attribute
    WHERE attrelid = 'extracted_value'::regclass
      AND attname = 'confidence'
      AND NOT attisdropped
    """
)

EXTRACTED_VALUE_COLUMNS = text(
    """
    SELECT attname
    FROM pg_attribute
    WHERE attrelid = 'extracted_value'::regclass
      AND attnum > 0
      AND NOT attisdropped
    ORDER BY attnum
    """
)

STORED_VALUE_COLUMNS = text(
    """
    SELECT value_kind, value_text, value_number
    FROM extracted_value WHERE extracted_value_id = :id
    """
)

#: Rejected combinations of `value_kind` and `value_number`, both directions of
#: the biconditional. `data-model.md` fixes the kind of each seeded term, so the
#: kind here is the vocabulary's and not a free choice.
NUMERIC_COLUMN_VIOLATIONS: Mapping[str, tuple[str, str, Any, str]] = {
    "number kind with no numeric": (NUMBER_TERM, "number", None, "48"),
    "text kind with a numeric": (TEXT_TERM, "text", Decimal("48"), "Grinnell"),
    "date kind with a numeric": (DATE_TERM, "date", Decimal("20260314"), "2026-03-14"),
}


@pytest.mark.parametrize(
    ("field_name", "value_kind", "value_number", "value_text"),
    list(NUMERIC_COLUMN_VIOLATIONS.values()),
    ids=list(NUMERIC_COLUMN_VIOLATIONS),
)
def test_the_typed_numeric_column_is_populated_exactly_for_numeric_fields(
    db_session: Session,
    anchor: Citation,
    assert_rejects: RejectionAsserter,
    field_name: str,
    value_kind: str,
    value_number: Any,
    value_text: str,
) -> None:
    """TR-045: `(value_kind = 'number') = (value_number IS NOT NULL)`, both directions.

    A biconditional and not a permission. A numeric-kind field *must* carry the
    typed numeric -- otherwise a consumer computing on it silently falls back to
    parsing `value_text`, which is Principle V's boundary crossed by accident --
    and a text- or date-kind field must *not*, otherwise two columns hold
    overlapping claims about the same value and nothing says which wins.

    The `date` case is the one that shows there is no third typed column: ISO-8601
    goes in `value_text` and `value_number` stays null, because TR-045 permits
    exactly text plus one optional numeric and a third would contradict SC-023.

    This is also the check that could not be written without
    `fk_extracted_value__field` being composite. A `CHECK` cannot query another
    table, so the foreign key carries `field_vocabulary.value_kind` into the row
    and the rule becomes single-row. The alternative -- a trigger, or an
    `IMMUTABLE` function lying about immutability to read the vocabulary -- would
    be either bypassable or wrong after a vocabulary correction.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extracted_value__numeric_iff_number_kind"
    ):
        insert_value(
            db_session,
            value_row(
                anchor,
                field_name=field_name,
                value_kind=value_kind,
                value_text=value_text,
                value_number=value_number,
            ),
        )


#: The accepted side of the same biconditional, one row per `value_kind`.
NUMERIC_COLUMN_AGREEMENTS: Mapping[str, tuple[str, str, str, Any]] = {
    "number kind carries the numeric": (NUMBER_TERM, "number", "48", Decimal("48")),
    "text kind leaves it null": (TEXT_TERM, "text", "Grinnell", None),
    "date kind stores iso 8601 in text": (DATE_TERM, "date", "2026-03-14", None),
}


@pytest.mark.parametrize(
    ("field_name", "value_kind", "value_text", "value_number"),
    list(NUMERIC_COLUMN_AGREEMENTS.values()),
    ids=list(NUMERIC_COLUMN_AGREEMENTS),
)
def test_a_value_is_canonical_text_plus_an_optional_typed_numeric(
    db_session: Session,
    anchor: Citation,
    field_name: str,
    value_kind: str,
    value_text: str,
    value_number: Any,
) -> None:
    """TR-045, the accepted side: canonical text on every row, numeric only where declared.

    Read back rather than merely inserted. Every kind carries `value_text`,
    including `number` -- the canonical text is what the page actually said, and
    the typed numeric is a derived convenience, not a replacement. A schema that
    dropped the text for numeric fields would lose the "as printed" form that
    makes a citation checkable.
    """
    value_id = insert_value(
        db_session,
        value_row(
            anchor,
            field_name=field_name,
            value_kind=value_kind,
            value_text=value_text,
            value_number=value_number,
        ),
    )

    stored = db_session.execute(STORED_VALUE_COLUMNS, {"id": value_id}).one()
    assert stored == (value_kind, value_text, value_number)


def test_no_foreign_key_from_extracted_value_targets_a_purchase_order_line_relation(
    db_session: Session,
) -> None:
    """TR-045, SC-023: the absence of a direct value-to-line reference, read from the catalog.

    Asserted as an absence in `pg_constraint`, because an absence is the only
    thing there is to assert -- there is no row that gets rejected and no error to
    catch, so nothing else about this requirement is observable at runtime.

    **Why the phrasing is "no foreign key targets a relation of that name".**
    `purchase_order_line` does not exist at this point in the chain; migration
    `0007` creates it. Resolving the name through `regclass` would therefore raise
    `UndefinedTable` today and the test would be measuring the migration head
    rather than the constraint set. Comparing target relation *names* holds now,
    holds after `0007` lands, and needs no edit at either point -- which is the
    property that matters, since the requirement is precisely about a table that
    arrives later.

    The primary assertion is the closed set: `extracted_value`'s only outbound
    references are its source chunk and its field name. That is stronger than a
    blocklist and does not rot -- a foreign key to *any* target record added by a
    later migration fails it, without anyone having to remember to extend a list
    of forbidden names.

    Why the requirement exists at all: `resolved_entity_member` (E009, migration
    `0010`) is the single sanctioned join surface between a value and a line. A
    direct foreign key here would make an identity merge representable in two
    places, and Principle III's bias toward refusal depends on there being exactly
    one -- a merge that can be expressed two ways cannot be withheld for review in
    one of them.
    """
    targets = {
        row.target_relation
        for row in db_session.execute(FOREIGN_KEY_TARGETS_OF_EXTRACTED_VALUE).all()
    }

    assert FORBIDDEN_FOREIGN_KEY_TARGET not in targets, (
        f"extracted_value must carry no direct foreign key to {FORBIDDEN_FOREIGN_KEY_TARGET} "
        f"(TR-045, SC-023) -- resolved_entity_member is the only sanctioned join surface. "
        f"Foreign-key targets found: {sorted(targets)}"
    )
    assert targets == set(PERMITTED_FOREIGN_KEY_TARGETS), (
        "extracted_value's only outbound references are its source chunk and its field "
        f"name. Expected {sorted(PERMITTED_FOREIGN_KEY_TARGETS)}, got {sorted(targets)}. A "
        "new target here means a value now references a target record directly, which "
        "TR-045 forbids however the table is named"
    )


def test_confidence_is_stored_as_double_precision_with_no_coarser_scale(
    db_session: Session, anchor: Citation
) -> None:
    """TR-054: a continuous score, with no bucketing or minimum discrimination step.

    Two assertions, and the second is the one with teeth. The column type is
    `double precision` -- not `numeric(3,2)`, not a smallint percentage, not an
    enum of confidence bands -- and that is read out of the catalog rather than
    assumed.

    Then two confidences a nanounit apart are stored and read back distinctly. A
    `numeric(3,2)` column would silently round both to `0.50` and this assertion
    would fail, which is what makes it a test of TR-054's "no coarser scale,
    bucketing, or minimum discrimination step" rather than a restatement of the
    declared type. Coarsening confidence is not a rounding error: it is the
    product quietly deciding which distinctions the extracting agent is allowed to
    express.
    """
    declared_type = db_session.execute(CONFIDENCE_COLUMN_TYPE).scalar_one()
    assert declared_type == "double precision", (
        "TR-054 requires a continuous double-precision score; a fixed-scale numeric or "
        f"an integer band would impose the coarser scale it forbids. Got {declared_type!r}"
    )

    lower, upper = 0.5, 0.5 + 1e-9
    first = insert_value(db_session, value_row(anchor, confidence=lower))
    second = insert_value(db_session, value_row(anchor, confidence=upper))

    stored = (
        db_session.execute(
            text(
                """
            SELECT confidence FROM extracted_value
            WHERE extracted_value_id IN (:first, :second)
            ORDER BY confidence
            """
            ),
            {"first": first, "second": second},
        )
        .scalars()
        .all()
    )

    assert stored == [lower, upper], (
        "two confidences 1e-9 apart must survive the round trip as distinct values -- a "
        "coarser stored scale would collapse them and silently discard a distinction the "
        f"extracting agent expressed. Got {stored!r}"
    )


# --- TR-081, TR-082, TR-085: documentation requirements, tested as documentation ---
#
# These three constrain what a *reader* may conclude from a stored row, not what
# the database will accept. No constraint can carry any of them, and inventing a
# schema assertion would manufacture coverage the schema does not provide. What
# *is* assertable is that `data-model.md` -- which TR-083 makes normative for
# reader-facing semantics -- states them, so that is what these tests assert. The
# names say so plainly, and none of them touches the database.

DATA_MODEL_PATH = (
    Path(__file__).resolve().parents[4] / "specs" / "00003-core-data-schema" / "data-model.md"
)


def _normalized(markdown: str) -> str:
    """Markdown emphasis and code ticks stripped, whitespace collapsed to single spaces.

    So a phrase can be matched across a line wrap and regardless of whether the
    author bolded part of it -- neither of which is a semantic difference, and
    both of which would otherwise make these assertions fail on a reflow.
    """
    return re.sub(r"\s+", " ", markdown.replace("*", "").replace("`", ""))


DATA_MODEL_TEXT = _normalized(DATA_MODEL_PATH.read_text(encoding="utf-8"))

#: The phrases each documentation requirement must be stated by. Chosen to be the
#: load-bearing words rather than whole sentences, so the document can be reworded
#: without breaking the test, but not so loose that a passing mention elsewhere
#: would satisfy it.
DOCUMENTED_SEMANTICS: Mapping[str, tuple[str, ...]] = {
    "TR-081": ("computed score", "calibrated probability"),
    "TR-082": ("ingestion-run granularity", "only per-row temporal fact"),
    "TR-085": ("retained for the life of the database", "regenerable"),
}


@pytest.mark.parametrize(
    ("requirement", "phrases"),
    list(DOCUMENTED_SEMANTICS.items()),
    ids=list(DOCUMENTED_SEMANTICS),
)
def test_tr081_tr082_tr085_semantics_are_recorded_in_data_model_md_not_in_a_constraint(
    requirement: str, phrases: tuple[str, ...]
) -> None:
    """TR-081, TR-082, TR-085 are documentation requirements, and this is a documentation test.

    Each states something a reader must not conclude, and none of them is
    expressible as a constraint:

    * **TR-081** -- confidence is a **computed score**, derived in deterministic
      code from parse signals recorded beside the value, **not a calibrated
      probability**. The schema can carry the type and the closed interval, and it
      does. It cannot carry calibration, and no `CHECK` could tell a
      well-calibrated `0.9` from a badly-calibrated one. Recording the limitation
      is the honest alternative to implying a guarantee, per Principle VII.
      Amended 2026-07-27: the score was originally specified as self-reported by
      the extracting agent. A number a model asserts about its own output is not
      reproducible, so E006 computes it instead; only the source moved, and the
      non-calibration half is unchanged.
    * **TR-082** -- the agent responsible for a citation is identified at
      **ingestion-run granularity** by E006, never on the value row, so
      `extracted_at` is the **only per-row temporal fact**. The schema-side residue
      is an *absence* -- there is no agent column -- which is asserted separately
      below, because an absence cannot be exercised by a rejection either.
    * **TR-085** -- provenance rows are **retained for the life of the database**;
      retention policy is out of scope and the dataset is **regenerable** from the
      repository and its jobs. A schema cannot assert that nothing will ever
      delete a row. Migration `0009` gets as close as a database can by revoking
      `UPDATE` and `DELETE` from the application role (TR-084), and T049 tests
      that; retention itself remains a stated policy.

    So the assertion is that `data-model.md` says these things. That is not a
    formality: TR-083 makes that document normative for reader-facing semantics,
    which means a consumer who reads it and concludes the opposite has been misled
    by the artifact rather than by their own reading. This test is what keeps the
    three statements from being dropped in a later edit.
    """
    assert requirement in DATA_MODEL_TEXT, (
        f"{requirement} must be traceable in data-model.md, which TR-083 makes normative "
        f"for reader-facing semantics. {DATA_MODEL_PATH} names no such requirement"
    )
    missing = [phrase for phrase in phrases if phrase.lower() not in DATA_MODEL_TEXT.lower()]
    assert not missing, (
        f"{requirement} is a semantic requirement no constraint can carry, so data-model.md "
        f"is the only place it can be enforced. These load-bearing phrases are absent: "
        f"{missing}. Add them to {DATA_MODEL_PATH.name} rather than weakening this test -- "
        "an unstated reader-facing limitation is the false guarantee Principle VII forbids"
    )


#: Column names that would mean agent identity had been recorded per value rather
#: than per ingestion run. Substring matching, so `agent`, `agent_id`,
#: `extracting_agent`, and `model_name` are all caught.
AGENT_IDENTITY_COLUMN_MARKERS = ("agent", "model_id", "model_name", "invocation")


def test_tr082_has_no_agent_column_on_the_value_row_which_is_its_only_schema_side_residue(
    db_session: Session,
) -> None:
    """TR-082: the absence of an agent column, which is the requirement's whole schema half.

    Paired with the documentation test above rather than replacing it. TR-082's
    reader-facing claim -- that agent identity lives at ingestion-run granularity
    in E006 -- is documentation. But it has exactly one consequence the catalog can
    show, and `data-model.md` states it in those terms: "`extracted_value.
    extracted_at` as the only per-row temporal fact; no agent column by design".

    Asserting the absence is worth doing because "by design" and "not yet" look
    identical in a schema, and the difference is what a later epic would need to
    know before adding one. `extracted_at` is asserted present in the same breath,
    so the test cannot pass against a table that had lost its temporal fact
    altogether.
    """
    columns = [row.attname for row in db_session.execute(EXTRACTED_VALUE_COLUMNS).all()]

    offenders = [
        column
        for column in columns
        for marker in AGENT_IDENTITY_COLUMN_MARKERS
        if marker in column.lower()
    ]
    assert not offenders, (
        "TR-082 records agent identity at ingestion-run granularity in E006, not on the "
        f"value row, so extracted_value carries no agent column by design. Found "
        f"{offenders}. Adding one here would put the same fact in two places at two "
        "granularities, and a reader could not tell which one a row was written under"
    )
    assert "extracted_at" in columns, (
        "extracted_at is TR-082's one per-row temporal fact and must be present; "
        f"columns are {columns}"
    )


# --------------------------------------------------------------------------- #
# T049 -- append-only as a privilege fact (TR-084, TR-086, SC-028)
# --------------------------------------------------------------------------- #
#
# Migration `0009` revokes `UPDATE` and `DELETE` on `extracted_value`,
# `extracted_value_contributing_chunk` and `extraction_failure` from the
# application role, so append-only stops being a caller convention and becomes
# something the server refuses. These tests are the evidence for SC-028.
#
# **The contributor table is in the list, and it is the one that would have been
# missed.** This group first covered the two tables TR-084 originally named. With
# `extracted_value_contributing_chunk` left mutable, a citation set could be
# silently truncated -- `DELETE` the contributor rows -- without a statement
# touching either named table: the value row is unchanged, its
# `source_chunk_count` still declares three sources, and
# `v_extracted_value_provenance` returns one. That is gap **G-1**'s runtime shape
# reached by privilege rather than by a partial write, and it is exactly the
# unattributable-number failure Principle I is about. TR-084 was amended to name
# all three tables; the parametrized cases below now carry six refusals, not
# four.
#
# **What these tests prove, stated precisely, because the honest claim is
# narrower than "the provenance tables are append-only".** The deployment has one
# role, `procurement`, and it is a SUPERUSER; a superuser bypasses every
# privilege check, so a revoke against it would be catalogued and inert.
# Migration `0009` therefore creates a non-superuser role, `procurement_app`, and
# revokes from that. `DATABASE_URL` is frozen by E001 and still names
# `procurement`, so the guarantee is **latent**: real for the application role,
# asserted here against the catalog and by execution, and operative on the
# connection the day the connection role changes. `data-model.md` records that as
# gap **G-11** with its reversal trigger. Nothing in this file may be read as
# claiming TR-084 is enforced against the process that connects today.
#
# **Why `SET LOCAL ROLE` is a test and not a simulation.** A superuser's
# `SET ROLE` to a non-superuser genuinely drops superuser status -- the privilege
# check that follows is the same one a separate connection would face, run by the
# same code path in the same server. That is a claim about PostgreSQL, so it is
# asserted rather than assumed, by
# `test_set_local_role_genuinely_drops_superuser_status` below, which reads
# `is_superuser` back from the session. If that assumption ever failed, that test
# fails first and every refusal below becomes suspect in the same run -- which is
# the point of asserting it separately instead of trusting it in a comment.
#
# The alternative, a second connection authenticating as `procurement_app`, is
# not available: the role is `NOLOGIN` and has no credential, and inventing one
# would mean the test exercised a role configured by the test rather than the one
# the migration created.

#: The non-superuser role migration `0009` creates and revokes from. Restated
#: here rather than imported: the migration module is named
#: `0009_provenance_privileges`, which is not a Python identifier, so importing it
#: would mean `importlib` machinery in a test whose subject is the database.
#: `test_the_application_role_named_here_is_the_one_the_migration_created` is what
#: keeps the two spellings from drifting apart silently.
APPLICATION_ROLE = "procurement_app"

#: The connection role, which is also the migration role (TR-086) and the owner
#: of every table in the schema.
MIGRATION_ROLE = "procurement"

#: 42501. Named as a constant so the assertion below reads as the SQLSTATE claim
#: it is; psycopg derives one class per SQLSTATE, so naming
#: `InsufficientPrivilege` *is* naming 42501, and this constant cross-checks that.
INSUFFICIENT_PRIVILEGE_SQLSTATE = "42501"

#: Role names cannot be bound as parameters -- `SET ROLE` takes an identifier,
#: not a value -- so these are complete literal statements with nothing to
#: interpolate, which is the form Ruff S608 asks for.
SET_LOCAL_APPLICATION_ROLE = text("SET LOCAL ROLE procurement_app")
RESET_TO_CONNECTION_ROLE = text("RESET ROLE")

#: `current_user` is the role privileges are checked against; `session_user` is
#: the role the connection authenticated as. `SET ROLE` moves the first and
#: leaves the second, and the gap between them is what these tests exploit.
EFFECTIVE_IDENTITY = text(
    """
    SELECT current_user AS effective_role,
           session_user AS connected_role,
           current_setting('is_superuser') AS is_superuser
    """
)

ROLE_ATTRIBUTES = text(
    "SELECT rolsuper, rolcanlogin, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = :rolname"
)

GRANTED_VERBS = text(
    """
    SELECT privilege_type
    FROM information_schema.role_table_grants
    WHERE grantee = :grantee AND table_schema = 'public' AND table_name = :table_name
    """
)

HOLDS_PRIVILEGE = text("SELECT has_table_privilege(:grantee, :table_name, :verb) AS held")

#: Every view in `public` whose rewrite rule reads one of the two provenance
#: tables, with PostgreSQL's own verdict on whether it is auto-updatable.
#: `pg_relation_is_updatable` is consulted rather than reasoned about, because
#: auto-updatability is decided by a list of conditions on the view body that a
#: later edit can satisfy by accident.
DEPENDENT_VIEW_UPDATABILITY = text(
    """
    SELECT DISTINCT v.relname AS view_name,
           pg_relation_is_updatable(v.oid, true) AS updatable_bits
    FROM pg_depend d
    JOIN pg_rewrite r ON r.oid = d.objid
    JOIN pg_class v ON v.oid = r.ev_class
    JOIN pg_class t ON t.oid = d.refobjid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE d.classid = 'pg_rewrite'::regclass
      AND d.refclassid = 'pg_class'::regclass
      AND v.relkind = 'v'
      AND n.nspname = 'public'
      AND t.relname IN (
          'extracted_value',
          'extracted_value_contributing_chunk',
          'extraction_failure'
      )
    """
)

#: `pg_relation_is_updatable` returns `1 << CMD_*`: 4 is UPDATE and 16 is DELETE.
#: Only those two matter here -- a view offering `INSERT` is no bypass of an
#: append-only rule.
AUTO_UPDATABLE_UPDATE_BIT = 4
AUTO_UPDATABLE_DELETE_BIT = 16

#: All three provenance tables, in the order TR-084 names them.
PROVENANCE_TABLES: tuple[str, ...] = (
    "extracted_value",
    "extracted_value_contributing_chunk",
    "extraction_failure",
)

#: The declared source count of the contributor fixture, and the ordinal its one
#: contributor row sits at. Three-and-3 rather than two-and-2 so the `UPDATE`
#: below can renumber the row to a *different* legal ordinal: an edit that is
#: entirely valid, changes which source the citation set claims came second, and
#: would therefore succeed if the privilege were held. A no-op `SET ordinal = 2
#: WHERE ordinal = 2` would exercise the grant just as well and would tell a
#: reader nothing about what the revoke is protecting.
CONTRIBUTOR_DECLARED_COUNT = 3
CONTRIBUTOR_ORDINAL = 3
RENUMBERED_CONTRIBUTOR_ORDINAL = 2

#: The verbs revoked, and the verbs kept.
REVOKED_VERBS: tuple[str, ...] = ("UPDATE", "DELETE")
RETAINED_VERBS: tuple[str, ...] = ("SELECT", "INSERT")


class ProvenanceWrite(NamedTuple):
    """One mutating statement against one provenance table.

    `statement` targets a single existing row by primary key and is otherwise
    entirely valid -- it would succeed if the privilege were held, which is what
    makes its rejection attributable to the grant rather than to a bad row.
    """

    table: str
    verb: str
    statement: Any


#: The six attempts SC-028 is about. Each is reported individually rather than
#: rolled into one loop: "the provenance tables are append-only" is six separate
#: facts, and a single test asserting all six would go red without saying which.
#:
#: Each contributor statement is keyed on the *parent* `extracted_value_id`
#: rather than on a row id of its own, because
#: `pk_extracted_value_contributing_chunk` is the composite
#: `(extracted_value_id, contributor_ordinal)` and there is no single-column
#: identifier to name. The ordinal is pinned in the `WHERE` clause so each
#: statement affects exactly one row, which is what the paired migration-role
#: test asserts.
PROVENANCE_WRITES: tuple[ProvenanceWrite, ...] = (
    ProvenanceWrite(
        "extracted_value",
        "UPDATE",
        text("UPDATE extracted_value SET confidence = 0.5 WHERE extracted_value_id = :row_id"),
    ),
    ProvenanceWrite(
        "extracted_value",
        "DELETE",
        text("DELETE FROM extracted_value WHERE extracted_value_id = :row_id"),
    ),
    ProvenanceWrite(
        "extracted_value_contributing_chunk",
        "UPDATE",
        text(
            "UPDATE extracted_value_contributing_chunk "
            "SET contributor_ordinal = :renumbered_ordinal "
            "WHERE extracted_value_id = :row_id AND contributor_ordinal = :ordinal"
        ),
    ),
    ProvenanceWrite(
        "extracted_value_contributing_chunk",
        "DELETE",
        text(
            "DELETE FROM extracted_value_contributing_chunk "
            "WHERE extracted_value_id = :row_id AND contributor_ordinal = :ordinal"
        ),
    ),
    ProvenanceWrite(
        "extraction_failure",
        "UPDATE",
        text(
            "UPDATE extraction_failure SET repair_attempt_count = repair_attempt_count + 1 "
            "WHERE extraction_failure_id = :row_id"
        ),
    ),
    ProvenanceWrite(
        "extraction_failure",
        "DELETE",
        text("DELETE FROM extraction_failure WHERE extraction_failure_id = :row_id"),
    ),
)

PROVENANCE_WRITE_IDS: tuple[str, ...] = tuple(
    f"{write.verb.lower()}-{write.table}" for write in PROVENANCE_WRITES
)


def write_params(row_id: UUID) -> dict[str, Any]:
    """Bind values for any statement in `PROVENANCE_WRITES`.

    One mapping for all six, carrying the contributor ordinals that only two of
    them name. SQLAlchemy binds what a `text()` construct declares and ignores
    the rest, so a single mapping keeps the two parametrized tests below from
    having to know which table they are looking at -- which is the point of the
    write being a parameter.
    """
    return {
        "row_id": row_id,
        "ordinal": CONTRIBUTOR_ORDINAL,
        "renumbered_ordinal": RENUMBERED_CONTRIBUTOR_ORDINAL,
    }


#: An `INSERT` and a `SELECT` per table, to show the revoke left the tables
#: append-only rather than read-only. Ingestion has to be able to write a
#: citation, or TR-084 would have made the schema unusable instead of honest.
#:
#: The contributor count is keyed on the parent value id, for the same reason its
#: mutating statements are: the table has no single-column identifier.
ROWS_IN_TABLE = {
    "extracted_value": text("SELECT count(*) FROM extracted_value WHERE extracted_value_id = :id"),
    "extracted_value_contributing_chunk": text(
        "SELECT count(*) FROM extracted_value_contributing_chunk WHERE extracted_value_id = :id"
    ),
    "extraction_failure": text(
        "SELECT count(*) FROM extraction_failure WHERE extraction_failure_id = :id"
    ),
}


@contextmanager
def as_application_role(session: Session) -> Iterator[None]:
    """Run the body with `current_user` set to the application role.

    `SET LOCAL`, not `SET`: the setting is scoped to the surrounding transaction,
    so even a body that raises before reaching the `RESET` cannot leak the role
    into the next test through a pooled connection -- `db_session`'s teardown
    rollback discards it.

    The role is set *outside* any savepoint a caller opens inside the body. That
    ordering is not cosmetic: `ROLLBACK TO SAVEPOINT` reverts `SET` statements
    issued after the savepoint, so setting the role inside the savepoint that
    catches the rejection would silently restore superuser privileges partway
    through, and a following assertion would then be testing the wrong role.
    """
    session.execute(SET_LOCAL_APPLICATION_ROLE)
    try:
        yield
    finally:
        session.execute(RESET_TO_CONNECTION_ROLE)


def assert_refused_for_want_of_privilege(
    session: Session, statement: Any, params: Mapping[str, Any], attempt: str
) -> psycopg.errors.InsufficientPrivilege:
    """Assert `statement` is refused as `InsufficientPrivilege` (SQLSTATE 42501).

    Deliberately not routed through `conftest.assert_rejects`, for the same
    reason `assert_not_null_violation` is not: that helper requires a
    `constraint_name`, and a privilege refusal is not attributable to a
    constraint -- it carries none. What it does carry is the SQLSTATE, and the
    SQLSTATE is the whole claim: 42501 is the server saying it declined to run
    the statement, which is categorically different from 23xxx, where it ran the
    statement and a constraint rejected the result.

    Acceptance is a failure, and so is rejection for any other reason -- a
    statement that failed because the row did not exist, or because a check
    fired, would leave the privilege untested while the test went green.

    Returns the psycopg error so a caller can report the exact refusal.
    """
    savepoint = session.begin_nested()
    try:
        session.execute(statement, dict(params))
    except DBAPIError as rejection:
        if savepoint.is_active:
            savepoint.rollback()
        original = rejection.orig
        if not isinstance(original, psycopg.errors.InsufficientPrivilege):
            raise AssertionError(
                f"{attempt} had to be refused for want of privilege (SQLSTATE "
                f"{INSUFFICIENT_PRIVILEGE_SQLSTATE}), but the database raised "
                f"{type(original).__name__} (SQLSTATE {getattr(original, 'sqlstate', None)}). "
                "The statement failed for some reason other than the revoke, so the grant "
                "under test was never exercised"
            ) from rejection
        if original.sqlstate != INSUFFICIENT_PRIVILEGE_SQLSTATE:
            raise AssertionError(
                f"{attempt} was refused as {type(original).__name__} but carried SQLSTATE "
                f"{original.sqlstate!r} rather than {INSUFFICIENT_PRIVILEGE_SQLSTATE!r}"
            ) from rejection
        return original
    else:
        if savepoint.is_active:
            savepoint.rollback()
        raise AssertionError(
            f"{attempt} was ACCEPTED. Migration 0009 takes {list(REVOKED_VERBS)} away from "
            f"{APPLICATION_ROLE} on all three provenance tables, so this statement must fail with "
            f"SQLSTATE {INSUFFICIENT_PRIVILEGE_SQLSTATE}. Either the revoke did not run, or "
            f"the effective role was not {APPLICATION_ROLE} when the statement ran -- check "
            "that SET LOCAL ROLE preceded the savepoint rather than following it"
        )


@pytest.fixture
def provenance_rows(
    db_session: Session, anchor: Citation, citations: tuple[Citation, ...]
) -> Mapping[str, UUID]:
    """One committed-shaped row in each provenance table, written as the migration role.

    Written before any role switch, because the application role is the subject
    of the test and not of the setup: a fixture that could not be written would
    make every refusal below trivially true for the wrong reason.

    The contributor row hangs off its *own* parent value rather than off the one
    keyed `extracted_value`, so the `DELETE` case against that parent cannot
    cascade the contributor row away before its own case runs. The key returned
    for the contributor table is therefore that second parent's id, which is what
    every contributor statement binds to `:row_id`.
    """
    contributor_parent = insert_value(
        db_session, multi_chunk_value_row(anchor, CONTRIBUTOR_DECLARED_COUNT)
    )
    insert_contributor(
        db_session,
        contributor_row(
            contributor_parent, CONTRIBUTOR_ORDINAL, citations[1], CONTRIBUTOR_DECLARED_COUNT
        ),
    )

    return {
        "extracted_value": insert_value(db_session, value_row(anchor)),
        "extracted_value_contributing_chunk": contributor_parent,
        "extraction_failure": insert_failure(db_session, failure_row(anchor)),
    }


def test_set_local_role_genuinely_drops_superuser_status(db_session: Session) -> None:
    """The assumption every refusal in this group rests on, asserted rather than trusted.

    `SET ROLE` from a superuser to a non-superuser drops superuser status for the
    remainder of the transaction: `current_user` becomes the target role and
    `is_superuser` reads `off`. That is what makes the refusals below genuine
    privilege checks rather than a simulation -- the server runs exactly the check
    it would run for a connection authenticated as that role.

    Both halves are asserted, and the first is the one that matters. The
    connection role **is** a superuser, so had `SET ROLE` merely relabelled
    `current_user` while leaving privileges intact, every refusal test in this
    group would fail rather than pass -- but a reader would have no way to tell
    whether the refusal came from the revoke or from something else. Reading
    `is_superuser` back closes that.
    """
    before = db_session.execute(EFFECTIVE_IDENTITY).one()
    assert before.effective_role == before.connected_role, (
        "the session must start unswitched; some earlier statement left a role set"
    )
    assert before.is_superuser == "on", (
        f"this test is only meaningful when the connection role is a superuser -- that is "
        f"the whole reason migration 0009 creates a second role. {before.connected_role!r} "
        f"reports is_superuser={before.is_superuser!r}. If the deployment has gained role "
        "separation, G-11 in data-model.md is resolved and this assertion should be revisited "
        "rather than deleted"
    )

    with as_application_role(db_session):
        during = db_session.execute(EFFECTIVE_IDENTITY).one()

    after = db_session.execute(EFFECTIVE_IDENTITY).one()

    assert during.effective_role == APPLICATION_ROLE, (
        f"SET LOCAL ROLE must move current_user to {APPLICATION_ROLE}; it is "
        f"{during.effective_role!r}"
    )
    assert during.connected_role == before.connected_role, (
        "session_user must be unchanged -- SET ROLE moves the privilege subject, not the "
        f"authenticated identity. It became {during.connected_role!r}"
    )
    assert during.is_superuser == "off", (
        "SET ROLE to a non-superuser must drop superuser status, or every privilege check "
        "in this group is bypassed and the refusals below would prove nothing. The server "
        f"reports is_superuser={during.is_superuser!r} while acting as "
        f"{during.effective_role!r}"
    )
    assert after.effective_role == before.effective_role, (
        f"RESET ROLE must restore {before.effective_role!r}; current_user is "
        f"{after.effective_role!r}"
    )


def test_the_application_role_named_here_is_the_one_the_migration_created(
    db_session: Session,
) -> None:
    """`procurement_app` exists, is not a superuser, cannot log in, and does not bypass RLS.

    The first two attributes are the load-bearing ones and they are the reason
    this role exists at all. A revoke against a superuser is accepted, recorded,
    and inert, so `rolsuper` being false is a precondition of TR-084 meaning
    anything; `rolbypassrls` is asserted alongside it because it is the other
    catalogued way a role sidesteps a restriction.

    `rolcanlogin` being **false** is asserted as the disclosure it is, not as a
    virtue. Nothing connects as this role today -- that is precisely G-11 -- and
    the day something does, this assertion is the one that fails and points a
    reader at the gap record.
    """
    attributes = db_session.execute(ROLE_ATTRIBUTES, {"rolname": APPLICATION_ROLE}).one_or_none()

    assert attributes is not None, (
        f"migration 0009 must create the role {APPLICATION_ROLE!r}; pg_roles has no such row. "
        "Either the migration did not run or the name drifted between the migration and this "
        "test"
    )
    assert not attributes.rolsuper, (
        f"{APPLICATION_ROLE} must not be a superuser. A superuser bypasses every privilege "
        "check, so REVOKE against it is catalogued and inert -- TR-084 would read as "
        "enforcement and enforce nothing"
    )
    assert not attributes.rolbypassrls, (
        f"{APPLICATION_ROLE} must not carry BYPASSRLS; it is the other attribute that lets a "
        "role step around a restriction the catalog records"
    )
    assert not attributes.rolcanlogin, (
        f"{APPLICATION_ROLE} is NOLOGIN because DATABASE_URL is frozen by E001 and names "
        f"{MIGRATION_ROLE}, so no connection is served by it -- gap G-11. If the role has "
        "gained LOGIN, the application's connection role has changed and G-11's reversal "
        "trigger has fired: update data-model.md rather than this assertion"
    )


@pytest.mark.parametrize("write", PROVENANCE_WRITES, ids=PROVENANCE_WRITE_IDS)
def test_the_application_role_cannot_update_or_delete_a_provenance_row(
    db_session: Session, provenance_rows: Mapping[str, UUID], write: ProvenanceWrite
) -> None:
    """TR-084, SC-028: all six mutating attempts are refused, one test each.

    Each statement targets a row that exists and is otherwise valid -- a
    confidence inside the closed unit interval, a repair count incremented by
    one, a contributor renumbered to another legal ordinal -- so the only thing
    standing between it and success is the revoke. Run as the migration role,
    each of these succeeds; that is the paired test below, and the two together
    are what make this a statement about the grant rather than about the row.

    The two contributor cases are the ones that close the quiet path. Neither
    touches `extracted_value` or `extraction_failure`, and the `DELETE` in
    particular would leave a value row whose `source_chunk_count` declares more
    sources than `v_extracted_value_provenance` can return -- an incomplete
    citation set with nothing to indicate it was ever longer.
    """
    refusal = None
    with as_application_role(db_session):
        refusal = assert_refused_for_want_of_privilege(
            db_session,
            write.statement,
            write_params(provenance_rows[write.table]),
            f"{write.verb} on {write.table} as {APPLICATION_ROLE}",
        )

    assert refusal.sqlstate == INSUFFICIENT_PRIVILEGE_SQLSTATE


@pytest.mark.parametrize("write", PROVENANCE_WRITES, ids=PROVENANCE_WRITE_IDS)
def test_the_migration_role_can_still_update_and_delete_a_provenance_row(
    db_session: Session, provenance_rows: Mapping[str, UUID], write: ProvenanceWrite
) -> None:
    """TR-086: the correction path stays open.

    The prescribed correction is a remove-and-reload of the affected chunks in
    the order the `RESTRICT` citation edges permit, and it needs `DELETE`. This
    asserts the migration role still holds both verbs on all three tables -- the second
    half of SC-028, and the half that would silently go missing if a future
    revision revoked from `PUBLIC` or from the owner instead of from the
    application role.

    That `UPDATE` succeeds here is a fact about the grant and not a licence: the
    policy is still remove-and-reload, never an in-place edit of a stored
    citation, page, confidence, or outcome. TR-086 asks that the privilege be
    retained, so retention is what is asserted.
    """
    identity = db_session.execute(EFFECTIVE_IDENTITY).one()
    assert identity.effective_role == MIGRATION_ROLE, (
        f"this test must run as the migration role; current_user is {identity.effective_role!r}"
    )

    savepoint = db_session.begin_nested()
    result = db_session.execute(write.statement, write_params(provenance_rows[write.table]))
    affected = result.rowcount
    savepoint.rollback()

    assert affected == 1, (
        f"{write.verb} on {write.table} as {MIGRATION_ROLE} must affect exactly the one row "
        f"it names, or the correction path TR-086 keeps open is not open. It affected "
        f"{affected}"
    )


def _write_one_provenance_row(
    session: Session, table: str, anchor: Citation, contributor_citation: Citation
) -> UUID:
    """Write one row into `table` and return the id `ROWS_IN_TABLE[table]` reads it back by.

    The contributor case writes *two* rows -- a multi-source parent value and one
    contributor under it -- because a contributor row cannot exist without a
    parent whose `source_chunk_count` it matches (`fk_evcc__value_count`). Both
    are written inside whatever role the caller has set, which is what makes the
    contributor case a real test of that role's `INSERT` on both tables.
    """
    if table == "extracted_value":
        return insert_value(session, value_row(anchor))
    if table == "extraction_failure":
        return insert_failure(session, failure_row(anchor))

    parent = insert_value(session, multi_chunk_value_row(anchor, CONTRIBUTOR_DECLARED_COUNT))
    insert_contributor(
        session,
        contributor_row(
            parent, CONTRIBUTOR_ORDINAL, contributor_citation, CONTRIBUTOR_DECLARED_COUNT
        ),
    )
    return parent


@pytest.mark.parametrize("table", PROVENANCE_TABLES)
def test_the_application_role_can_still_insert_and_select_so_the_tables_are_append_only(
    db_session: Session, anchor: Citation, citations: tuple[Citation, ...], table: str
) -> None:
    """TR-084: append-only, which is not read-only.

    The revoke would be trivial to over-apply -- taking `INSERT` as well would
    make every refusal above pass while making the tables useless to the
    ingestion path that has to write citations in the first place. So the
    positive case is asserted in the same file and against the same role: as
    `procurement_app`, write a row and read it back.

    This matters most for the contributor table. A value with three sources is
    written as one parent row plus two contributor rows, so revoking `INSERT`
    there would leave the application able to store a value declaring three
    sources and unable to store the other two citations -- gap G-1's shape
    produced by the very statement meant to prevent it.

    Written entirely inside the role switch, so the `INSERT` is checked against
    the application role's grant and not the migration role's.
    """
    with as_application_role(db_session):
        acting_as = db_session.execute(EFFECTIVE_IDENTITY).one().effective_role
        row_id = _write_one_provenance_row(db_session, table, anchor, citations[1])
        found = db_session.execute(ROWS_IN_TABLE[table], {"id": row_id}).scalar_one()

    assert acting_as == APPLICATION_ROLE, (
        f"the write must have been attempted as {APPLICATION_ROLE}; it ran as {acting_as!r}"
    )
    assert found == 1, (
        f"{APPLICATION_ROLE} must retain INSERT and SELECT on {table} -- the tables are "
        f"append-only, not read-only. The row it just wrote is not readable back "
        f"(count {found})"
    )


@pytest.mark.parametrize("table", PROVENANCE_TABLES)
def test_the_revoke_is_recorded_in_the_catalog_and_not_merely_observed(
    db_session: Session, table: str
) -> None:
    """SC-028 read out of `information_schema`, independently of any statement being run.

    A behavioural test and a catalog test fail differently and neither subsumes
    the other. The refusals above could in principle come from something other
    than the grant; the catalog says what the grant *is*. Both are asserted.

    **PostgreSQL records no negative grant.** After `REVOKE UPDATE` there is
    simply no `UPDATE` row for this grantee -- there is no catalogued "denied".
    So "the revoke is recorded" means precisely: the two revoked verbs are absent
    while the two retained verbs are present, on this exact table, for this exact
    grantee. Asserting the absence alone would pass just as well against a role
    that had never been granted anything at all, which is why the retained verbs
    are asserted in the same test.

    `has_table_privilege` is consulted as well as `role_table_grants`, because the
    two answer different questions: the view lists grants made directly, while the
    function answers the question the executor actually asks, following role
    membership. A privilege reaching the role through an inherited grant would
    show up here and nowhere else.
    """
    granted = {
        row.privilege_type
        for row in db_session.execute(
            GRANTED_VERBS, {"grantee": APPLICATION_ROLE, "table_name": table}
        ).all()
    }

    assert not granted & set(REVOKED_VERBS), (
        f"migration 0009 revokes {list(REVOKED_VERBS)} on {table} from {APPLICATION_ROLE}, so "
        f"information_schema.role_table_grants must list neither. It lists "
        f"{sorted(granted & set(REVOKED_VERBS))}"
    )
    assert set(RETAINED_VERBS) <= granted, (
        f"{APPLICATION_ROLE} must keep {list(RETAINED_VERBS)} on {table} -- append-only, not "
        f"read-only. Missing {sorted(set(RETAINED_VERBS) - granted)}; the role holds "
        f"{sorted(granted)}"
    )

    for verb in REVOKED_VERBS:
        held = db_session.execute(
            HOLDS_PRIVILEGE, {"grantee": APPLICATION_ROLE, "table_name": table, "verb": verb}
        ).scalar_one()
        assert not held, (
            f"has_table_privilege reports {APPLICATION_ROLE} holds {verb} on {table}. The "
            "direct grant was revoked, so this privilege is arriving through role membership "
            "-- which the executor honours and role_table_grants does not show"
        )

    for verb in (*RETAINED_VERBS, *REVOKED_VERBS):
        held = db_session.execute(
            HOLDS_PRIVILEGE, {"grantee": MIGRATION_ROLE, "table_name": table, "verb": verb}
        ).scalar_one()
        assert held, (
            f"TR-086: {MIGRATION_ROLE} must retain {verb} on {table} so the prescribed "
            "remove-and-reload correction stays possible"
        )


def test_no_view_offers_an_update_or_delete_path_around_the_revoke(db_session: Session) -> None:
    """The one way a table-level revoke can be bypassed, closed by checking for it.

    An auto-updatable view runs its write against the base table with the *view
    owner's* privileges, not the caller's. Since every view here is owned by the
    migration role, an auto-updatable view over `extracted_value` would hand the
    application role exactly the `UPDATE` and `DELETE` the revoke took away, and
    every other test in this group would still pass.

    `v_extracted_value_provenance` is not auto-updatable -- it is a `UNION`, and
    PostgreSQL auto-updates only single-relation views. That is a property of how
    the view happens to be written rather than a decision anyone recorded, so it
    is asserted here: a later simplification of the view body could make it
    auto-updatable without anyone intending to reopen the write path.

    The check is `pg_relation_is_updatable`, PostgreSQL's own verdict, rather
    than an inspection of the view text. `INSERT`-ability is deliberately not
    asserted against -- an insertable view is no bypass of an append-only rule.
    """
    rows = db_session.execute(DEPENDENT_VIEW_UPDATABILITY).all()

    assert rows, (
        "no view was found reading any of the three provenance tables, but "
        "v_extracted_value_provenance does -- the dependency query is wrong and this test is "
        "asserting nothing"
    )

    offenders = {
        row.view_name: row.updatable_bits
        for row in rows
        if row.updatable_bits & (AUTO_UPDATABLE_UPDATE_BIT | AUTO_UPDATABLE_DELETE_BIT)
    }
    assert not offenders, (
        f"these views over the provenance tables are auto-updatable: {offenders}. A write "
        "through an auto-updatable view runs with the view owner's privileges, so each of "
        f"these hands {APPLICATION_ROLE} back the UPDATE or DELETE that migration 0009 "
        "revoked, and TR-084 is enforced only against callers who name the table directly"
    )
