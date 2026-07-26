"""`resolved_entity` and `resolved_entity_member` -- migration `0010` (T047).

OBJ6, TR-034, TR-035, TR-045. The P2 half of the schema: a confirmed
cross-document identity, and the membership table that is the **only sanctioned
join** between an extracted value and a purchase-order line.

**What this file is really about.** `extracted_value` carries no foreign key to
`purchase_order_line` -- `test_extraction.py` asserts that absence continuously
-- so nothing in the schema relates a submittal's manufacturer to a line on a
purchase order *except* both being members of the same `resolved_entity`. That
makes every such link an explicit, dated row naming the identity it was merged
under, which is what Principle III asks for: a wrong merge is a row somebody can
find and delete, not an invisible edge on a value row.

**Three rules carry the weight, and each is tested by the row that would break
it.**

1. **The XOR.** A member points at an `extracted_value` **or** a
   `purchase_order_line`, never both, never neither. Both failure modes are
   refused by `ck_rem__exactly_one_target`, and both are exercised -- a check of
   this shape written as `(a IS NULL) <> (b IS NULL)` would behave identically
   today and is not what the schema has.
2. **A record cannot belong to two entities** (TR-035, OBJ6 VC2), by
   `uq_rem__extracted_value` and `uq_rem__po_line`. These are plain `UNIQUE`, so
   PostgreSQL's default **`NULLS DISTINCT`** applies, and that default is the
   entire mechanism: every line member holds a NULL `extracted_value_id`, NULLs
   never collide, and so the many members of one kind coexist while two rows
   naming the same record do not. `NULLS NOT DISTINCT` -- one keyword away, legal
   syntax since PostgreSQL 15 -- would cap the whole database at one member of
   each kind. Both the behaviour and the catalog flag are asserted, because the
   behaviour alone would also pass against a partial unique index, and the flag
   alone would pass against an index nobody ever inserted through.
3. **A single-member entity is ordinary** (OBJ6 VC3). A material appearing in
   only one document is still a resolved entity, and nothing in the schema
   requires a second member. Asserted, because a "membership" table invites a
   minimum-cardinality rule that would make the common case unrepresentable.

**Gap G-6 is asserted as what it discloses, not as a guarantee.**
`agreement_attribute_names` elements are `field_vocabulary` terms and PostgreSQL
has no array-element foreign key, so an entity naming a term the vocabulary does
not hold is **accepted**. The test below writes exactly that row, shows it
stored, and shows what `data-model.md`'s gap record says a reader then sees --
"a reader resolving it finds nothing" -- by resolving it and finding nothing.
Asserting a rejection there would be asserting a guarantee the schema does not
make.

**What is *not* covered here.** `data-model.md` §Disclosed Gaps records no gap
that this table alone could close, and this file adds none. The one adjacent
question -- whether a member's `resolved_entity` and its target agree about
anything semantic, such as the entity's normalized manufacturer matching the
value's text -- is E009's to decide when it populates the table, is cross-table,
and is deliberately not asserted here as though the schema carried it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Any
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
# Source rows -- two documents, their chunks, two values, one purchase-order line
# --------------------------------------------------------------------------- #
#
# The three sources OBJ6 VC1 names are a specification, a submittal, and a
# purchase-order line, so the fixtures below build exactly those and nothing
# else. Both documents are `REAL`: `0003` refuses either provenance layer's
# fields on the other, and a document row tripping one of those checks would
# never reach the membership constraints this file is about.

SPECIFICATION_DOCUMENT: Mapping[str, Any] = {
    "document_id": "example-piping-specification-2026",
    "document_type": "specification",
    "project_id": "PRJ-001",
    "title": "Section 22 11 00 -- Facility Water Distribution Piping",
    "source_kind": "REAL",
    "source_ref": "https://standards.example.gov/piping/specification/2026",
    "issuing_body": "Example Standards Body",
    "retrieval_date": date(2026, 1, 15),
    "generator_id": None,
    "generation_seed": None,
    "generated_at": None,
    "fixture_hashes": None,
    "roster_hash": None,
    "license_basis": "public-domain",
}

SUBMITTAL_DOCUMENT: Mapping[str, Any] = {
    **SPECIFICATION_DOCUMENT,
    "document_id": "example-piping-submittal-2026",
    "document_type": "submittal",
    "title": "Piping Materials Submittal",
    "source_ref": "https://standards.example.gov/piping/submittal/2026",
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

#: The embedding is built server-side from `schema_constants.vector_dimension`,
#: the published copy every consumer reads under TR-047, so this file holds no
#: second opinion about the size of the vector space. Nothing here ranks by
#: distance; it only needs a chunk that exists.
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

#: A well-formed roster hash in the format E001 froze -- `sha256:` plus 64
#: lowercase hex digits (TR-024).
ROSTER_HASH = "sha256:" + "3f2a" * 16

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

ENTITY_INSERT = text(
    """
    INSERT INTO resolved_entity (
        resolved_entity_id, normalized_manufacturer, normalized_part_number,
        agreement_attribute_names
    )
    VALUES (
        :resolved_entity_id, :normalized_manufacturer, :normalized_part_number,
        CAST(:agreement_attribute_names AS text[])
    )
    """
)

MEMBER_INSERT = text(
    """
    INSERT INTO resolved_entity_member (
        member_id, resolved_entity_id, member_kind, extracted_value_id, po_line_id
    )
    VALUES (
        :member_id, :resolved_entity_id, :member_kind, :extracted_value_id, :po_line_id
    )
    """
)

#: The membership read OBJ6 VC1 is about: every member of one entity, traced back
#: to the artifact it came from. The two `extracted_value` members resolve
#: through their anchor chunk to a document and its type; the line member
#: resolves to a purchase order. `LEFT JOIN` throughout, because exactly one of
#: the two paths is populated on any given row -- that is the XOR, seen from the
#: read side.
RECOVER_MEMBERS = text(
    """
    SELECT m.member_kind,
           d.document_type AS document_type,
           l.po_number AS po_number,
           coalesce(v.value_text, l.manufacturer) AS source_text
    FROM resolved_entity_member m
    LEFT JOIN extracted_value v ON v.extracted_value_id = m.extracted_value_id
    LEFT JOIN chunk c ON c.chunk_id = v.source_chunk_id
    LEFT JOIN document d ON d.document_id = c.document_id
    LEFT JOIN purchase_order_line l ON l.po_line_id = m.po_line_id
    WHERE m.resolved_entity_id = :resolved_entity_id
    """
)

COUNT_MEMBERS = text(
    "SELECT count(*) FROM resolved_entity_member WHERE resolved_entity_id = :resolved_entity_id"
)

COUNT_ENTITIES = text(
    "SELECT count(*) FROM resolved_entity WHERE resolved_entity_id = :resolved_entity_id"
)

DELETE_ENTITY = text("DELETE FROM resolved_entity WHERE resolved_entity_id = :resolved_entity_id")

#: The reader's resolution of an agreement attribute, and the whole of gap G-6's
#: runtime story: an element that names no vocabulary term simply returns no row.
RESOLVE_AGREEMENT_ATTRIBUTES = text(
    """
    SELECT term.name, vocabulary.label
    FROM resolved_entity e
    CROSS JOIN LATERAL unnest(e.agreement_attribute_names) AS term(name)
    LEFT JOIN field_vocabulary vocabulary ON vocabulary.field_name = term.name
    WHERE e.resolved_entity_id = :resolved_entity_id
    ORDER BY term.name
    """
)

#: `pg_index.indnullsnotdistinct` is PostgreSQL 15+'s record of whether a unique
#: index treats NULLs as equal. `false` is the default and is what TR-035 needs;
#: `indpred IS NULL` additionally asserts the index is not partial, since a
#: partial index would reach the same behaviour by a mechanism `data-model.md`
#: does not declare.
UNIQUE_INDEX_NULL_HANDLING = text(
    """
    SELECT i.indnullsnotdistinct, (i.indpred IS NULL) AS is_total
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE c.relname = :index_name
    """
)

#: `confdeltype`: `c` is CASCADE, `r` is RESTRICT.
CASCADE = "c"
RESTRICT = "r"

FOREIGN_KEY_DELETE_ACTIONS = text(
    """
    SELECT conname, confdeltype
    FROM pg_constraint
    WHERE conrelid = 'resolved_entity_member'::regclass AND contype = 'f'
    ORDER BY conname
    """
)

#: Every relation carrying a foreign key to `extracted_value` *and* one to
#: `purchase_order_line`. TR-045 says there is exactly one, and names it.
RELATIONS_JOINING_VALUES_TO_LINES = text(
    """
    SELECT joined.relname
    FROM (
        SELECT c.conrelid::regclass::text AS relname,
               bool_or(c.confrelid = 'extracted_value'::regclass) AS references_value,
               bool_or(c.confrelid = 'purchase_order_line'::regclass) AS references_line
        FROM pg_constraint c
        WHERE c.contype = 'f'
        GROUP BY c.conrelid
    ) AS joined
    WHERE joined.references_value AND joined.references_line
    """
)

#: `SET LOCAL ROLE` takes an identifier, not a bindable value, so these are
#: complete literal statements with nothing to interpolate -- the form Ruff S608
#: asks for. The role is the one migration `0009` creates; see
#: `test_extraction.py` for why it is `NOLOGIN` and what that means (gap G-11).
APPLICATION_ROLE = "procurement_app"
SET_LOCAL_APPLICATION_ROLE = text("SET LOCAL ROLE procurement_app")
RESET_TO_CONNECTION_ROLE = text("RESET ROLE")
EFFECTIVE_ROLE = text("SELECT current_user AS effective_role")

# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #
#
# Perturbing exactly one field of an otherwise-valid row is what makes a
# rejection attributable. Break two at once and PostgreSQL reports whichever rule
# it evaluated first, so the test names one constraint and is satisfied by
# another -- the failure `conftest.assert_rejects` exists to catch, and which
# these builders exist to avoid producing in the first place.


def entity_row(**overrides: Any) -> dict[str, Any]:
    """A valid `resolved_entity`: normalized on both columns, two agreement terms.

    Both agreement terms are real `field_vocabulary` entries, so a test aiming at
    some other rule cannot be quietly relying on G-6's hole to get its fixture
    stored.
    """
    row: dict[str, Any] = {
        "resolved_entity_id": uuid4(),
        "normalized_manufacturer": "grinnell",
        "normalized_part_number": "gr-2001-06",
        "agreement_attribute_names": ["manufacturer", "part_number"],
    }
    row.update(overrides)
    return row


def value_member_row(entity_id: UUID, extracted_value_id: UUID, **overrides: Any) -> dict[str, Any]:
    """A valid member pointing at an `extracted_value`, with `po_line_id` null."""
    row: dict[str, Any] = {
        "member_id": uuid4(),
        "resolved_entity_id": entity_id,
        "member_kind": "extracted_value",
        "extracted_value_id": extracted_value_id,
        "po_line_id": None,
    }
    row.update(overrides)
    return row


def line_member_row(entity_id: UUID, po_line_id: UUID, **overrides: Any) -> dict[str, Any]:
    """A valid member pointing at a `purchase_order_line`, with `extracted_value_id` null."""
    row: dict[str, Any] = {
        "member_id": uuid4(),
        "resolved_entity_id": entity_id,
        "member_kind": "purchase_order_line",
        "extracted_value_id": None,
        "po_line_id": po_line_id,
    }
    row.update(overrides)
    return row


def line_row(*, line_number: int = 7, **overrides: Any) -> dict[str, Any]:
    """A valid **open** `purchase_order_line` -- no closing pointer, not closed.

    Open is the ordinary state and is the one that needs no `lifecycle_event`
    chain behind it, so a membership test does not have to build a six-event
    history to get a line it can point at.
    """
    row: dict[str, Any] = {
        "po_line_id": uuid4(),
        "project_id": "PRJ-001",
        "vendor_id": "VND-014",
        "po_number": "PO-88213",
        "line_number": line_number,
        "material_category": "piping",
        "description": '6" carbon steel pipe, seamless',
        "manufacturer": "Grinnell",
        "part_number": "GR-2001-06",
        "quantity": 12.5,
        "unit_of_measure": "m",
        "order_date": date(2026, 3, 2),
        "need_by_date": date(2026, 6, 1),
        "criticality": 4,
        "lifecycle_state": "submitted",
        "is_closed": False,
        "closing_event_id": None,
        "roster_hash": ROSTER_HASH,
    }
    row.update(overrides)
    return row


def insert_entity(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `resolved_entity` and return its id."""
    session.execute(ENTITY_INSERT, dict(row))
    return row["resolved_entity_id"]


def insert_member(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `resolved_entity_member` and return its id."""
    session.execute(MEMBER_INSERT, dict(row))
    return row["member_id"]


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


def insert_manufacturer_value(session: Session, chunk_id: UUID, page_number: int) -> UUID:
    """Insert a single-chunk `manufacturer` value citing `(chunk_id, page_number)`."""
    row = {
        "extracted_value_id": uuid4(),
        "source_chunk_id": chunk_id,
        "cited_page": page_number,
        "field_name": "manufacturer",
        "value_kind": "text",
        "value_text": "Grinnell",
        "value_number": None,
        "confidence": 0.82,
        "provenance_kind": "single_chunk",
        "source_chunk_count": 1,
    }
    session.execute(VALUE_INSERT, row)
    return row["extracted_value_id"]


@contextmanager
def as_application_role(session: Session) -> Iterator[None]:
    """Run the body with `current_user` set to the application role.

    `SET LOCAL`, not `SET`: the setting is scoped to the surrounding transaction,
    so a body that raises before reaching the `RESET` cannot leak the role into
    the next test through a pooled connection.

    Restated here rather than imported from `test_extraction.py`: importing one
    test module from another makes the two collectable only together and would
    tie this file's fate to an unrelated one.

    The guarded `RESET` is not tidiness. If a statement in the body is refused,
    PostgreSQL aborts the transaction and every later statement -- including this
    `RESET ROLE` -- fails with `InFailedSqlTransaction`. Raising that from a
    `finally` would *replace* the error that actually failed the test with an
    unrelated one, which is the hazard `conftest._rollback` documents. Nothing is
    lost by swallowing it: the role was set with `SET LOCAL`, so `db_session`'s
    teardown rollback discards it either way.
    """
    session.execute(SET_LOCAL_APPLICATION_ROLE)
    try:
        yield
    finally:
        with suppress(DBAPIError):
            session.execute(RESET_TO_CONNECTION_ROLE)


# --------------------------------------------------------------------------- #
# Fixtures -- the three sources, built once
# --------------------------------------------------------------------------- #


@pytest.fixture
def specification_value(db_session: Session) -> UUID:
    """A `manufacturer` value extracted from a **specification**."""
    db_session.execute(DOCUMENT_INSERT, dict(SPECIFICATION_DOCUMENT))
    chunk_id = insert_chunk(db_session, SPECIFICATION_DOCUMENT, page_number=4, ordinal=0)
    return insert_manufacturer_value(db_session, chunk_id, page_number=4)


@pytest.fixture
def submittal_value(db_session: Session) -> UUID:
    """A `manufacturer` value extracted from a **submittal** -- a second document."""
    db_session.execute(DOCUMENT_INSERT, dict(SUBMITTAL_DOCUMENT))
    chunk_id = insert_chunk(db_session, SUBMITTAL_DOCUMENT, page_number=9, ordinal=0)
    return insert_manufacturer_value(db_session, chunk_id, page_number=9)


@pytest.fixture
def po_line(db_session: Session) -> UUID:
    """One open purchase-order line -- the third source."""
    row = line_row()
    db_session.execute(LINE_INSERT, row)
    return row["po_line_id"]


@pytest.fixture
def entity(db_session: Session) -> UUID:
    """One valid `resolved_entity` for members to hang off."""
    return insert_entity(db_session, entity_row())


@pytest.fixture
def second_entity(db_session: Session) -> UUID:
    """A second entity with a different normalized identity.

    Different on both key columns, so an attempt to add a record to it cannot be
    rejected by `uq_resolved_entity__normalized_identity` on the way to the
    membership uniqueness rule under test.
    """
    return insert_entity(
        db_session,
        entity_row(normalized_manufacturer="victaulic", normalized_part_number="vic-77-06"),
    )


# --------------------------------------------------------------------------- #
# TR-034, OBJ6 VC1 -- a three-source entity is fully recoverable
# --------------------------------------------------------------------------- #


def test_a_three_source_entity_is_fully_recoverable_from_the_entity(
    db_session: Session,
    entity: UUID,
    specification_value: UUID,
    submittal_value: UUID,
    po_line: UUID,
) -> None:
    """SC-016, OBJ6 VC1: specification, submittal, and purchase-order line, all recovered.

    This is the read the table exists to serve, and it is asserted end to end
    rather than by counting rows: each member is traced back through the schema
    to the artifact it came from -- the two values through their anchor chunk to
    a document and its type, the line to its purchase order. A membership table
    that stored three rows but could not tell a reader which document each came
    from would satisfy a count and fail the requirement.

    Note what makes this recovery possible at all: the value members and the line
    member sit in **one** table keyed by `resolved_entity_id`, so "everything
    known about this material" is a single-key lookup and not a union the caller
    has to assemble. `ix_rem__entity` is the index for it.
    """
    insert_member(db_session, value_member_row(entity, specification_value))
    insert_member(db_session, value_member_row(entity, submittal_value))
    insert_member(db_session, line_member_row(entity, po_line))

    recovered = {
        (row.member_kind, row.document_type, row.po_number, row.source_text)
        for row in db_session.execute(RECOVER_MEMBERS, {"resolved_entity_id": entity}).all()
    }

    assert recovered == {
        ("extracted_value", "specification", None, "Grinnell"),
        ("extracted_value", "submittal", None, "Grinnell"),
        ("purchase_order_line", None, "PO-88213", "Grinnell"),
    }, (
        "all three members of the entity must be recoverable from the entity id alone, each "
        f"resolving to the artifact it came from. Recovered {sorted(map(str, recovered))}"
    )


def test_a_single_member_entity_persists_without_error(
    db_session: Session, entity: UUID, submittal_value: UUID
) -> None:
    """OBJ6 VC3: a material appearing in only one document is still a resolved entity.

    Worth its own test rather than being implied by the three-member case,
    because "membership" invites a minimum-cardinality rule -- a `CHECK` that an
    entity has at least two members, or a discriminator requiring one of each
    kind. Neither is expressible as a single-row constraint, and neither is
    wanted: a submittal naming a manufacturer no purchase order has yet been cut
    for is exactly the state E009 must be able to record, and refusing it would
    force the resolver to either invent a second member or discard the
    observation.
    """
    member_id = insert_member(db_session, value_member_row(entity, submittal_value))

    assert db_session.execute(COUNT_MEMBERS, {"resolved_entity_id": entity}).scalar_one() == 1, (
        "a one-member entity must persist; the row was accepted but is not readable back"
    )
    assert member_id is not None


# --------------------------------------------------------------------------- #
# TR-035 -- the XOR
# --------------------------------------------------------------------------- #


def test_a_member_naming_both_targets_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    entity: UUID,
    submittal_value: UUID,
    po_line: UUID,
) -> None:
    """`ck_rem__exactly_one_target`: two targets is not a member, it is two members.

    `member_kind` is left at `extracted_value` so the row satisfies
    `ck_rem__kind_agrees` -- the populated `extracted_value_id` is exactly what
    that kind claims -- and the *only* rule this row breaks is the cardinality
    one. Set the kind the other way and both checks would be violated at once,
    PostgreSQL would report whichever it evaluated first, and this test would be
    asserting an evaluation order the documentation does not promise.

    A row with both set is not a harmless over-specification: it would make a
    single member a bridge between one value and one line *without* the entity
    mediating, which is precisely the direct value-to-line link TR-045 forbids,
    smuggled into the join table.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_rem__exactly_one_target"):
        db_session.execute(
            MEMBER_INSERT,
            value_member_row(entity, submittal_value, po_line_id=po_line),
        )


def test_a_member_naming_neither_target_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter, entity: UUID
) -> None:
    """`ck_rem__exactly_one_target`: a member of nothing is not a member.

    The kind is `purchase_order_line` for the reason the both-set case sets it to
    `extracted_value`: with both target columns null, `ck_rem__kind_agrees` reads
    `false = false`, which is true, so this row breaks one rule and not two.

    This half is the one a naive implementation misses. `num_nonnulls(...) = 1`
    is *false* on an all-null row, so the `CHECK` refuses it -- but a presence
    rule written on nullable columns as, say, `extracted_value_id IS NOT NULL OR
    po_line_id IS NOT NULL` alongside a separate "not both" rule would work too,
    while the single expression it invites -- `extracted_value_id <>
    po_line_id`-style comparisons -- evaluates to NULL here, and a `CHECK`
    accepts NULL.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_rem__exactly_one_target"):
        db_session.execute(
            MEMBER_INSERT,
            line_member_row(entity, po_line_id=None),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("member_kind", "populated"),
    [
        pytest.param("purchase_order_line", "extracted_value", id="line-kind-value-target"),
        pytest.param("extracted_value", "purchase_order_line", id="value-kind-line-target"),
    ],
)
def test_the_member_kind_cannot_disagree_with_the_populated_target(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    entity: UUID,
    submittal_value: UUID,
    po_line: UUID,
    member_kind: str,
    populated: str,
) -> None:
    """`ck_rem__kind_agrees`, a biconditional, so both directions are refused.

    The discriminator is redundant with "which column is populated" and is kept
    anyway, because a reader filtering members by kind should not have to write
    `WHERE extracted_value_id IS NOT NULL` and be silently wrong the day a third
    member kind appears. Redundancy is only safe when it cannot disagree, which
    is what this constraint buys and what this test checks.

    Each row here populates exactly one target, so `ck_rem__exactly_one_target`
    is satisfied and the rejection is attributable to the kind rule alone.
    """
    row = (
        value_member_row(entity, submittal_value, member_kind=member_kind)
        if populated == "extracted_value"
        else line_member_row(entity, po_line, member_kind=member_kind)
    )

    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_rem__kind_agrees"):
        db_session.execute(MEMBER_INSERT, row)


def test_an_undeclared_member_kind_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter, entity: UUID, po_line: UUID
) -> None:
    """`ck_rem__member_kind`: the set of member kinds is closed.

    `specification` is the plausible wrong answer -- a reader who thinks of a
    member as "where the record came from" rather than "which table it lives in"
    would write exactly this. The kind names a *relation*, and a third relation
    means a third nullable column and a third foreign key in the same migration
    that widens this list.

    The line target is the populated one, so `ck_rem__kind_agrees` reads
    `false = false` and holds: an undeclared kind is never `'extracted_value'`,
    so agreeing with a null `extracted_value_id` is the only thing it can do.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_rem__member_kind"):
        db_session.execute(
            MEMBER_INSERT, line_member_row(entity, po_line, member_kind="specification")
        )


# --------------------------------------------------------------------------- #
# TR-035, OBJ6 VC2 -- a record cannot belong to two entities
# --------------------------------------------------------------------------- #


def test_an_extracted_value_already_a_member_cannot_join_a_second_entity(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    entity: UUID,
    second_entity: UUID,
    submittal_value: UUID,
) -> None:
    """TR-035, OBJ6 VC2 on the value side: `uq_rem__extracted_value`.

    A value belonging to two entities would mean the same observed manufacturer
    was simultaneously two different materials, and every downstream count that
    groups by entity would double-count it. The rejection is a `UniqueViolation`
    (SQLSTATE 23505) naming the constraint, so a caller can distinguish "this
    record is already resolved elsewhere" from any other failure and route the
    pair to review -- which is what Principle III asks for in place of a silent
    merge.
    """
    insert_member(db_session, value_member_row(entity, submittal_value))

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "uq_rem__extracted_value"):
        db_session.execute(MEMBER_INSERT, value_member_row(second_entity, submittal_value))


def test_a_purchase_order_line_already_a_member_cannot_join_a_second_entity(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    entity: UUID,
    second_entity: UUID,
    po_line: UUID,
) -> None:
    """TR-035, OBJ6 VC2 on the line side: `uq_rem__po_line`.

    Asserted separately from the value side rather than parametrized with it,
    because they are two constraints on two columns and a schema could easily
    carry one and not the other -- the omission would be invisible in any test
    that only exercised values.
    """
    insert_member(db_session, line_member_row(entity, po_line))

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "uq_rem__po_line"):
        db_session.execute(MEMBER_INSERT, line_member_row(second_entity, po_line))


def test_the_same_record_cannot_be_added_to_the_same_entity_twice(
    db_session: Session, assert_rejects: RejectionAsserter, entity: UUID, submittal_value: UUID
) -> None:
    """The degenerate case of the same rule, and the more likely one in practice.

    "A second entity" is the requirement's wording, but a re-run of a resolver
    that is not idempotent produces a duplicate against the *same* entity, and
    the same `UNIQUE` catches it. Worth pinning: a constraint written as
    `UNIQUE (resolved_entity_id, extracted_value_id)` would satisfy the
    requirement's wording, pass the two tests above, and let this row through --
    the entity would then report two members for one record.
    """
    insert_member(db_session, value_member_row(entity, submittal_value))

    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "uq_rem__extracted_value"):
        db_session.execute(MEMBER_INSERT, value_member_row(entity, submittal_value))


def test_many_members_of_one_kind_coexist_because_the_uniques_are_nulls_distinct(
    db_session: Session, entity: UUID, po_line: UUID
) -> None:
    """The behavioural half of TR-035's `NULLS DISTINCT` dependency.

    Three line members of one entity, each holding a NULL `extracted_value_id`.
    Under PostgreSQL's default `NULLS DISTINCT` those three NULLs do not collide
    and all three rows are stored; under `NULLS NOT DISTINCT` the second would be
    rejected by `uq_rem__extracted_value`, and the table would hold at most one
    member of each kind **in the entire database**. That failure is not
    hypothetical or exotic -- it is one keyword in the DDL, and every other test
    in this file would still pass.
    """
    second_line = line_row(line_number=8)
    third_line = line_row(line_number=9)
    db_session.execute(LINE_INSERT, second_line)
    db_session.execute(LINE_INSERT, third_line)

    insert_member(db_session, line_member_row(entity, po_line))
    insert_member(db_session, line_member_row(entity, second_line["po_line_id"]))
    insert_member(db_session, line_member_row(entity, third_line["po_line_id"]))

    assert db_session.execute(COUNT_MEMBERS, {"resolved_entity_id": entity}).scalar_one() == 3, (
        "three line members, each with a NULL extracted_value_id, must coexist. If only one "
        "was stored, uq_rem__extracted_value is NULLS NOT DISTINCT and the schema can hold "
        "one member of each kind in total"
    )


@pytest.mark.parametrize("index_name", ["uq_rem__extracted_value", "uq_rem__po_line"])
def test_both_uniques_are_total_and_nulls_distinct_in_the_catalog(
    db_session: Session, index_name: str
) -> None:
    """The catalog half of the same claim, which the behavioural half cannot give.

    Two properties, and neither is implied by the test above passing:

    * **`indnullsnotdistinct` is false.** Behaviour proves the default is in
      force *today*; the flag is what a reviewer reads, and it is the single
      keyword that would change it.
    * **The index is total, not partial.** A partial unique index
      (`... WHERE extracted_value_id IS NOT NULL`) reaches the same behaviour by
      a different mechanism -- one `data-model.md` explicitly does not declare,
      that does not appear in `information_schema.table_constraints`, and that a
      later reader would have to reverse-engineer. `data-model.md` records the
      choice of a plain `UNIQUE` as deliberate; this asserts the choice held.
    """
    index = db_session.execute(UNIQUE_INDEX_NULL_HANDLING, {"index_name": index_name}).one_or_none()

    assert index is not None, f"migration 0010 must create {index_name}; pg_index has no such index"
    assert index.indnullsnotdistinct is False, (
        f"{index_name} must use PostgreSQL's default NULLS DISTINCT. With NULLS NOT DISTINCT "
        "the many members whose target column is null all collide, and the table can hold one "
        "member of each kind for the whole database"
    )
    assert index.is_total, (
        f"{index_name} must be a plain UNIQUE constraint, not a partial index. data-model.md "
        "declares plain UNIQUE and relies on NULLS DISTINCT; a WHERE clause here would be a "
        "second mechanism for the same rule and would not appear as a table constraint"
    )


# --------------------------------------------------------------------------- #
# Referential actions -- CASCADE to the entity, RESTRICT to the targets
# --------------------------------------------------------------------------- #


def test_deleting_the_entity_removes_its_members_and_leaves_the_records(
    db_session: Session,
    entity: UUID,
    submittal_value: UUID,
    po_line: UUID,
) -> None:
    """`fk_rem__entity ON DELETE CASCADE` -- membership has no meaning without the entity.

    Both halves matter and the second is the point. Discarding a merge that
    turned out to be wrong must remove the *claim* -- the membership rows -- and
    must not remove the evidence: the extracted value and the purchase-order line
    are records of what the documents said, and they survive the entity that was
    built out of them. A cascade reaching them would make withdrawing a bad merge
    destructive, which would push a resolver toward leaving bad merges in place.
    """
    insert_member(db_session, value_member_row(entity, submittal_value))
    insert_member(db_session, line_member_row(entity, po_line))

    db_session.execute(DELETE_ENTITY, {"resolved_entity_id": entity})

    assert db_session.execute(COUNT_ENTITIES, {"resolved_entity_id": entity}).scalar_one() == 0
    assert db_session.execute(COUNT_MEMBERS, {"resolved_entity_id": entity}).scalar_one() == 0, (
        "deleting the entity must cascade to its members; membership rows naming a "
        "non-existent entity would be unreachable and would still hold the record hostage "
        "through uq_rem__extracted_value"
    )

    surviving_values = db_session.execute(
        text("SELECT count(*) FROM extracted_value WHERE extracted_value_id = :id"),
        {"id": submittal_value},
    ).scalar_one()
    surviving_lines = db_session.execute(
        text("SELECT count(*) FROM purchase_order_line WHERE po_line_id = :id"),
        {"id": po_line},
    ).scalar_one()

    assert (surviving_values, surviving_lines) == (1, 1), (
        "the cascade must stop at the membership rows. The value and the line are the "
        "evidence the entity was built from and must outlive it"
    )


@pytest.mark.parametrize(
    ("target", "statement", "constraint"),
    [
        pytest.param(
            "extracted_value",
            text("DELETE FROM extracted_value WHERE extracted_value_id = :id"),
            "fk_rem__extracted_value",
            id="extracted-value",
        ),
        pytest.param(
            "purchase_order_line",
            text("DELETE FROM purchase_order_line WHERE po_line_id = :id"),
            "fk_rem__po_line",
            id="purchase-order-line",
        ),
    ],
)
def test_deleting_a_record_that_is_a_member_is_refused(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    entity: UUID,
    submittal_value: UUID,
    po_line: UUID,
    target: str,
    statement: Any,
    constraint: str,
) -> None:
    """`ON DELETE RESTRICT` on both targets, unlike the entity edge above.

    Deleting a value or a line that a merge depends on must be an explicit,
    ordered operation -- drop the membership first, then the record. A cascade
    here would let a reload of one document silently shrink an entity's
    membership while the entity went on asserting an identity it could no longer
    evidence, which is the invisible corruption Principle III exists to prevent.
    """
    insert_member(db_session, value_member_row(entity, submittal_value))
    insert_member(db_session, line_member_row(entity, po_line))

    row_id = submittal_value if target == "extracted_value" else po_line
    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, constraint):
        db_session.execute(statement, {"id": row_id})


def test_the_declared_delete_actions_are_what_the_catalog_holds(db_session: Session) -> None:
    """§Referential Actions, read back from `pg_constraint` rather than argued.

    One CASCADE and two RESTRICTs, and the asymmetry is the design: the child is
    definitionally owned by the entity and definitionally *not* owned by the
    records it names. Asserting the catalog catches the case the behavioural
    tests cannot -- a fourth foreign key added later with the wrong action, on a
    path no existing test exercises.
    """
    actions = {
        row.conname: row.confdeltype for row in db_session.execute(FOREIGN_KEY_DELETE_ACTIONS).all()
    }

    assert actions == {
        "fk_rem__entity": CASCADE,
        "fk_rem__extracted_value": RESTRICT,
        "fk_rem__po_line": RESTRICT,
    }, (
        "resolved_entity_member must carry exactly three foreign keys: CASCADE to the entity, "
        f"RESTRICT to each record it names. The catalog holds {actions}"
    )


# --------------------------------------------------------------------------- #
# TR-034 -- the entity's own columns
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        pytest.param(
            "normalized_manufacturer",
            "Grinnell",
            "ck_resolved_entity__manufacturer_normalized",
            id="manufacturer-not-case-folded",
        ),
        pytest.param(
            "normalized_manufacturer",
            "\t",
            "ck_resolved_entity__manufacturer_normalized",
            id="manufacturer-tab-only",
        ),
        pytest.param(
            "normalized_manufacturer",
            "\v",
            "ck_resolved_entity__manufacturer_normalized",
            id="manufacturer-vertical-tab-only",
        ),
        pytest.param(
            "normalized_part_number",
            "GR-2001-06",
            "ck_resolved_entity__part_number_normalized",
            id="part-number-not-case-folded",
        ),
        pytest.param(
            "normalized_part_number",
            "",
            "ck_resolved_entity__part_number_normalized",
            id="part-number-empty",
        ),
    ],
)
def test_an_unnormalized_identity_column_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    column: str,
    value: str,
    constraint: str,
) -> None:
    """TR-034: normalization is asserted at the storage boundary, not assumed of the writer.

    Two claims per constraint and both are exercised. `= lower(...)` says the
    value has been case-folded -- without it, `Grinnell` and `grinnell` would be
    two entities for one material and `uq_resolved_entity__normalized_identity`
    would happily hold both. The `btrim` says the value is not blank.

    The **vertical-tab** case is the one that catches the `E'\\v'` trap:
    PostgreSQL's escape-string syntax has no `\\v`, so a trim set written that way
    would silently contain the *letter* `v` and not U+000B, admitting this row
    while rejecting a legitimate part number of `vvv`. Both halves of that trap
    are covered -- here, and by `test_a_part_number_of_only_v_characters_is_accepted`.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, constraint):
        db_session.execute(ENTITY_INSERT, entity_row(**{column: value}))


def test_a_part_number_of_only_v_characters_is_accepted(db_session: Session) -> None:
    """The other half of the `E'\\v'` trap: a legitimate value must not be rejected.

    A trim set written `E' \\t\\n\\r\\f\\v'` contains the letter `v`, so
    `btrim('vvv', ...)` returns the empty string and this perfectly ordinary part
    number is refused. A typo that only ever *opened* a hole would be caught by
    the vertical-tab case above; this one is caught by nothing else, and it is
    the half that would fail loudly in production against real data.
    """
    entity_id = insert_entity(db_session, entity_row(normalized_part_number="vvv"))

    assert db_session.execute(COUNT_ENTITIES, {"resolved_entity_id": entity_id}).scalar_one() == 1


@pytest.mark.parametrize(
    ("agreement", "description"),
    [
        pytest.param([], "empty array", id="empty"),
        pytest.param([None], "single NULL element", id="single-null-element"),
        pytest.param([""], "single empty-string element", id="single-empty-element"),
        pytest.param(["  "], "single blank element", id="single-blank-element"),
        pytest.param([None, None], "all-NULL array", id="all-null"),
    ],
)
def test_an_entity_agreeing_on_nothing_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    agreement: list[str | None],
    description: str,
) -> None:
    """`ck_resolved_entity__agreement_non_empty`, including the cases below the surface.

    TR-034 makes agreement attributes part of what an entity *is*: an entity is a
    claim that these records describe one material, and the attributes are the
    evidence for that claim. An entity agreeing on nothing is a merge with no
    stated basis.

    The empty array is the case `data-model.md` declares and the reason the check
    is written with `cardinality` -- `array_length('{}', 1)` is **NULL**, a
    `CHECK` rejects only on *false*, so the natural-looking
    `array_length(agreement_attribute_names, 1) >= 1` would have **accepted** the
    empty array. `cardinality('{}')` is `0`.

    The remaining cases are the recorded deviation (TR-083, see the migration
    docstring): `cardinality(ARRAY[NULL])` is `1`, so the declared form alone
    accepts an array whose one element names nothing -- the same defect the empty
    array is refused for, one subscript deeper. `array_position(arr, NULL) IS
    NULL` and the `array_to_string` presence check close it.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_resolved_entity__agreement_non_empty"
    ):
        db_session.execute(ENTITY_INSERT, entity_row(agreement_attribute_names=agreement))

    assert description  # the parametrize label is the failure message's readable half


def test_one_agreement_attribute_is_enough(db_session: Session) -> None:
    """The accepted side of the same rule: the floor is one, not two.

    Asserted because the check could be strengthened by accident -- `>= 2` reads
    as "they agreed on more than one thing" and would make the ordinary
    single-attribute merge unrepresentable.
    """
    entity_id = insert_entity(db_session, entity_row(agreement_attribute_names=["manufacturer"]))

    assert db_session.execute(COUNT_ENTITIES, {"resolved_entity_id": entity_id}).scalar_one() == 1


def test_one_normalized_identity_admits_only_one_entity(
    db_session: Session, assert_rejects: RejectionAsserter, entity: UUID
) -> None:
    """`uq_resolved_entity__normalized_identity` -- the identity, actually unique.

    Two entities for one normalized manufacturer-and-part pair would mean the
    merge had been made twice with the members split across both, so every read
    would see a partial membership and none would look wrong. That is the silent
    corruption Principle III names; a `UniqueViolation` is the visible failure it
    prefers.
    """
    with assert_rejects(
        db_session, psycopg.errors.UniqueViolation, "uq_resolved_entity__normalized_identity"
    ):
        db_session.execute(ENTITY_INSERT, entity_row())

    assert entity is not None


# --------------------------------------------------------------------------- #
# Gap G-6 -- asserted as the disclosure it is
# --------------------------------------------------------------------------- #

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

#: The load-bearing words of G-6's two rows in `data-model.md` -- the gap itself
#: and its disclosure record. Phrases rather than whole sentences, so the
#: document can be reworded without breaking the test, but specific enough that a
#: passing mention elsewhere would not satisfy them.
G6_DISCLOSURE_PHRASES: tuple[str, ...] = (
    "PostgreSQL has no array-element foreign key",
    "may name a term absent from the vocabulary",
    "a reader resolving it finds nothing",
    "resolved_entity_agreement_attribute",
)


def test_gap_g6_an_agreement_attribute_absent_from_the_vocabulary_is_accepted(
    db_session: Session,
) -> None:
    """G-6: the schema **accepts** this row, and the test asserts that rather than a guarantee.

    `agreement_attribute_names` elements are meant to be `field_vocabulary`
    terms. PostgreSQL has no array-element foreign key, so nothing checks them,
    and an entity naming `lead_time_weeks` -- a plausible attribute that the
    seeded vocabulary does not hold -- is stored without complaint.

    Writing this test as a rejection would be asserting an enforcement the schema
    does not carry, which is precisely the failure Principle VII's disclosure
    discipline exists to prevent: a covering test that quietly claims the gap is
    closed is worse than no test, because it makes the gap record read as
    obsolete.

    So both halves of the disclosure are asserted:

    1. The row is **accepted** -- that is the gap.
    2. Resolving the attribute through `field_vocabulary` yields **no term** --
       that is `data-model.md`'s stated runtime consequence, "a reader resolving
       it finds nothing", reproduced rather than paraphrased. The reader is not
       misled by a wrong term; it gets nothing, which is visible.
    """
    unknown_term = "lead_time_weeks"
    known_term = "manufacturer"
    entity_id = insert_entity(
        db_session,
        entity_row(agreement_attribute_names=[known_term, unknown_term]),
    )

    assert (
        db_session.execute(COUNT_ENTITIES, {"resolved_entity_id": entity_id}).scalar_one() == 1
    ), (
        "G-6 discloses that array elements are unchecked, so this row must be ACCEPTED. If it "
        "was rejected, the schema has gained an enforcement the gap record says it lacks and "
        "data-model.md must be corrected rather than this test"
    )

    resolved = {
        row.name: row.label
        for row in db_session.execute(
            RESOLVE_AGREEMENT_ATTRIBUTES, {"resolved_entity_id": entity_id}
        ).all()
    }

    assert set(resolved) == {known_term, unknown_term}, (
        f"both stored elements must come back from the array; got {sorted(resolved)}"
    )
    assert resolved[unknown_term] is None, (
        f"{unknown_term!r} is not a vocabulary term, so a reader resolving it must find "
        f"nothing -- that is G-6's disclosed runtime consequence. It resolved to "
        f"{resolved[unknown_term]!r}"
    )
    assert resolved[known_term] is not None, (
        f"{known_term!r} IS a seeded vocabulary term and must resolve, or this test proves "
        "nothing about the unknown one -- the join would be returning NULL for every element"
    )


@pytest.mark.parametrize("phrase", G6_DISCLOSURE_PHRASES)
def test_gap_g6_is_disclosed_in_data_model_md_with_its_alternative(phrase: str) -> None:
    """TR-042, TR-063, Principle VII: the gap is recorded, not merely uncovered.

    A gap is a scope decision only if it is written down with what it costs and
    what would reverse it. `data-model.md` carries G-6 twice -- once in the gap
    table with why the database cannot hold it, and once in the disclosure record
    with the runtime consequence, the reversal trigger, and the production-scale
    alternative (a `resolved_entity_agreement_attribute` child table with a real
    foreign key, replacing the array).

    This test is what stops the array from quietly becoming an undisclosed
    limitation in a later edit of that document.
    """
    assert _normalized(phrase) in DATA_MODEL_TEXT, (
        f"data-model.md must disclose gap G-6 in terms including {phrase!r}. Either the "
        "disclosure was reworded past recognition or it was dropped; TR-042 requires every "
        "gap to be covered and Principle VII requires the miss to be published"
    )


# --------------------------------------------------------------------------- #
# TR-045 -- this table is the only sanctioned join
# --------------------------------------------------------------------------- #


def test_resolved_entity_member_is_the_only_relation_joining_values_to_lines(
    db_session: Session,
) -> None:
    """TR-045, SC-023: the positive half of a requirement usually stated as an absence.

    `test_extraction.py` asserts the absence -- no foreign key from
    `extracted_value` targets a purchase-order-line relation. That is necessary
    and not sufficient: a third table carrying a foreign key to each would
    reintroduce exactly the direct join TR-045 forbids, under a different name,
    and the absence test would still pass.

    So this asserts the whole schema at once: exactly one relation references
    both `extracted_value` and `purchase_order_line`, and it is
    `resolved_entity_member`. Anything else appearing here is a second join
    surface and a TR-045 violation regardless of what it is called.
    """
    joining = {row.relname for row in db_session.execute(RELATIONS_JOINING_VALUES_TO_LINES).all()}

    assert joining == {"resolved_entity_member"}, (
        "resolved_entity_member must be the ONLY relation carrying foreign keys to both "
        f"extracted_value and purchase_order_line (TR-045, SC-023). Found {sorted(joining)}"
    )


# --------------------------------------------------------------------------- #
# The explicit grant -- `0009` declined ALTER DEFAULT PRIVILEGES, so `0010` grants
# --------------------------------------------------------------------------- #

APPLICATION_ROLE_VERBS: tuple[str, ...] = ("SELECT", "INSERT", "UPDATE", "DELETE")

GRANTED_VERBS = text(
    """
    SELECT privilege_type
    FROM information_schema.role_table_grants
    WHERE grantee = :grantee AND table_schema = 'public' AND table_name = :table_name
    """
)


@pytest.mark.parametrize("table", ["resolved_entity", "resolved_entity_member"])
def test_the_application_role_holds_all_four_verbs_on_both_new_tables(
    db_session: Session, table: str
) -> None:
    """The consequence of `0009` declining `ALTER DEFAULT PRIVILEGES`, asserted.

    `0009` chose to fail closed: a table created after it acquires **no**
    privileges for `procurement_app` unless its own migration grants them, so
    that a future append-only table cannot silently inherit `UPDATE` and
    `DELETE`. The price is that every later revision must grant explicitly, and
    forgetting is silent -- nothing fails at migration time, and the role simply
    cannot read the table when something finally connects as it.

    All four verbs, deliberately. These are not provenance tables: a resolved
    entity is a revisable judgement about identity, and E009 must be able to
    withdraw a merge it later finds unsupported. Principle III's "withhold rather
    than merge" is worth little if a merge already made cannot be taken back.
    """
    granted = {
        row.privilege_type
        for row in db_session.execute(
            GRANTED_VERBS, {"grantee": APPLICATION_ROLE, "table_name": table}
        ).all()
    }

    assert set(APPLICATION_ROLE_VERBS) <= granted, (
        f"migration 0010 must grant {list(APPLICATION_ROLE_VERBS)} on {table} to "
        f"{APPLICATION_ROLE} explicitly -- 0009 declined ALTER DEFAULT PRIVILEGES, so nothing "
        f"grants them implicitly. Missing {sorted(set(APPLICATION_ROLE_VERBS) - granted)}"
    )


def test_the_application_role_can_actually_write_and_revise_a_resolved_entity(
    db_session: Session, submittal_value: UUID
) -> None:
    """The behavioural half: the grant works under a genuine privilege check.

    Run under `SET LOCAL ROLE procurement_app`, which drops superuser status --
    `test_extraction.py::test_set_local_role_genuinely_drops_superuser_status`
    asserts that, and every privilege claim in this repository rests on it.

    All four verbs are exercised in one transaction because they are one claim:
    the application role can create an entity, record a membership, revise the
    entity's agreement attributes, and withdraw the membership. A catalog
    assertion alone would pass while, say, `USAGE` on the schema was missing and
    every statement failed for an unrelated reason.
    """
    entity_id = uuid4()
    row = entity_row(resolved_entity_id=entity_id)

    with as_application_role(db_session):
        acting_as = db_session.execute(EFFECTIVE_ROLE).scalar_one()

        db_session.execute(ENTITY_INSERT, row)
        member = value_member_row(entity_id, submittal_value)
        db_session.execute(MEMBER_INSERT, member)

        db_session.execute(
            text(
                "UPDATE resolved_entity SET agreement_attribute_names = "
                "CAST(:names AS text[]) WHERE resolved_entity_id = :id"
            ),
            {"names": ["manufacturer"], "id": entity_id},
        )
        db_session.execute(
            text("DELETE FROM resolved_entity_member WHERE member_id = :id"),
            {"id": member["member_id"]},
        )

        stored = db_session.execute(COUNT_ENTITIES, {"resolved_entity_id": entity_id}).scalar_one()
        remaining = db_session.execute(
            COUNT_MEMBERS, {"resolved_entity_id": entity_id}
        ).scalar_one()

    assert acting_as == APPLICATION_ROLE, (
        f"the writes must have been attempted as {APPLICATION_ROLE}; they ran as {acting_as!r}"
    )
    assert (stored, remaining) == (1, 0), (
        f"{APPLICATION_ROLE} must be able to insert, select, update and delete on both P2 "
        f"tables. Entity rows {stored}, member rows after the delete {remaining}"
    )
