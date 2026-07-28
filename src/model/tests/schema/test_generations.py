"""FR-055 / SC-043 / {SAD:ADR-0020}: one generation per document, and only one.

T073, over T072's promotion. Three claims, each asserted rather than described:

1. **At most one generation is active per document.** A partial unique index on
   `(document_id) WHERE status = 'active'` makes a second live generation
   unrepresentable, so two readers cannot silently union two chunkings of one
   document.
2. **Zero superseded rows remain after a promotion completes.** Under
   {SAD:ADR-0020} `superseded` is a *within-transaction* state: the mark names
   the generation the removal then deletes, and both are steps of one
   transaction — so every committed row carries `active` and the retention bound
   is zero rather than a policy a purge job is trusted to honour.
3. **Zero rows are left behind.** Not just the generation row: the chunks, the
   values, their contributing chunks and parse signals and line items, the
   failures, and all three run-output associations. The promotion is the only
   thing that knows which of E003's rows belonged to the predecessor, and it
   knows it only through associations it is about to delete — which is why the
   identifier sets are captured first and why this file asserts the counts per
   table rather than a single boolean.

**The constraint that forced the record is E003's, not this epic's.**
`uq_chunk__document_ordinal UNIQUE (document_id, ordinal)` is scoped to the
document because at the time it was written there was no generation to scope it
by. Chunk ordinals are zero-based, so two resident generations of one document
both contain `(document_id, 0)`. E006 may add no constraint to `chunk` and may
not widen that one, so retention was not expensive but **unstorable**. That is
asserted here directly, because it is the premise the whole design rests on.

**Isolation and privilege.** Every write is inside `db_session`'s outer
transaction and is rolled back in teardown. The promotion deletes, and the
deployed application role holds `DELETE` on none of these tables — which is the
point: promotion is an operator procedure under the schema-owning role
(`data-model.md` §Operator Procedures 3), and this harness connects as the
schema owner. `src/model/tests/schema/test_privileges.py` (T085) is where the
refusals under `SET LOCAL ROLE procurement_app` are asserted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from model.compute.confidence import ParseSignals
from model.ingest.failures import FAILURE_OUTCOMES
from model.ingest.runs import (
    GENERATION_STATUSES,
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    AgentIdentity,
    RunIdentity,
    active_generation,
    promote_generation,
    write_run_record,
)
from model.ingest.writer import (
    CitedChunk,
    PreparedValue,
    cite_value,
    write_contributing_chunks,
)

RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

DOCUMENT_ID = "prj-902-t0001-r0"
DIGEST = f"sha256:{'f' * 64}"

AGENT = AgentIdentity(
    principal_kind="automation",
    principal_id="e006-generations",
    distribution="model",
    version="0.1.0",
    vcs_revision="0123abc",
)

#: Every table a generation leaves rows in, and how to count this document's.
#: Written down as data so "zero rows left" is a loop over the whole set rather
#: than a handful of assertions someone has to keep in step with the schema.
#: E003's three are counted by `document_id`; E006's four by the generation key.
RESIDENT_COUNTS: Mapping[str, str] = {
    "chunk": "SELECT count(*) FROM chunk WHERE document_id = :document_id",
    "extracted_value": (
        "SELECT count(*) FROM extracted_value v JOIN chunk c "
        "ON c.chunk_id = v.source_chunk_id WHERE c.document_id = :document_id"
    ),
    "extracted_value_contributing_chunk": (
        "SELECT count(*) FROM extracted_value_contributing_chunk e JOIN chunk c "
        "ON c.chunk_id = e.chunk_id WHERE c.document_id = :document_id"
    ),
    "extraction_failure": (
        "SELECT count(*) FROM extraction_failure f JOIN chunk c "
        "ON c.chunk_id = f.source_chunk_id WHERE c.document_id = :document_id"
    ),
    "ingestion_run_chunk": (
        "SELECT count(*) FROM ingestion_run_chunk WHERE document_id = :document_id"
    ),
    "ingestion_run_extracted_value": (
        "SELECT count(*) FROM ingestion_run_extracted_value WHERE document_id = :document_id"
    ),
    "ingestion_run_extraction_failure": (
        "SELECT count(*) FROM ingestion_run_extraction_failure WHERE document_id = :document_id"
    ),
    "extracted_value_line_item": (
        "SELECT count(*) FROM extracted_value_line_item WHERE document_id = :document_id"
    ),
    "extracted_value_parse_signal": (
        "SELECT count(*) FROM extracted_value_parse_signal WHERE document_id = :document_id"
    ),
    "ingestion_run_document": (
        "SELECT count(*) FROM ingestion_run_document WHERE document_id = :document_id"
    ),
}

DOCUMENT_ROW: Mapping[str, Any] = {
    "document_id": DOCUMENT_ID,
    "document_type": "submittal",
    "project_id": "PRJ-902",
    "title": "Generation fixture transmittal",
    "source_kind": "SYNTHETIC",
    "source_ref": None,
    "issuing_body": None,
    "retrieval_date": None,
    "generator_id": "model.corpus.generate",
    "generation_seed": 902,
    "generated_at": datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    "fixture_hashes": [f"sha256:{'a' * 64}"],
    "roster_hash": f"sha256:{'b' * 64}",
    "license_basis": "synthetic",
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
            FROM generate_series(1, (SELECT vector_dimension FROM schema_constants))
            AS axis(component)
        )::vector,
        :embedding_model_id, :embedding_model_revision
    )
    """
)

VALUE_INSERT = text(
    """
    INSERT INTO extracted_value (
        extracted_value_id, source_chunk_id, cited_page, field_name, value_kind,
        value_text, value_number, confidence, provenance_kind, source_chunk_count
    )
    VALUES (
        :extracted_value_id, :source_chunk_id, :cited_page, 'manufacturer', 'text',
        'Norhelm Transformer Wks.', NULL, 0.9, 'multi_chunk', 2
    )
    """
)

#: The page-split value every generation below stores: its label ends page one
#: and its printed value opens page two, so the anchor is ordinal 1 on page 2 and
#: the label's chunk is its one contributing row (FR-029). Confidence 0.9 is what
#: the declared policy computes for `('canonical', 2, False)` — one page-split
#: deduction and nothing else — so the row agrees with its own signals.
PAGE_SPLIT_VALUE = PreparedValue(
    field_name="manufacturer",
    value_kind="text",
    value_text="Norhelm Transformer Wks.",
    value_number=None,
    confidence=0.9,
    citation=cite_value(
        CitedChunk(ordinal=1, page_number=2), [CitedChunk(ordinal=0, page_number=1)]
    ),
    signals=ParseSignals("canonical", 2, False),
)

FAILURE_INSERT = text(
    """
    INSERT INTO extraction_failure (
        extraction_failure_id, source_chunk_id, attempted_page, field_name,
        outcome, repair_attempt_count, detail
    )
    VALUES (
        :extraction_failure_id, :source_chunk_id, :attempted_page, 'part_number',
        :outcome, 0, 'the document prints no part number for this item'
    )
    """
)

GENERATION_INSERT = text(
    """
    INSERT INTO ingestion_run_document (run_id, document_id, status, input_tuple_digest)
    VALUES (:run_id, :document_id, :status, :input_tuple_digest)
    """
)

RUN_CHUNK_INSERT = text(
    "INSERT INTO ingestion_run_chunk (chunk_id, run_id, document_id) "
    "VALUES (:chunk_id, :run_id, :document_id)"
)
RUN_VALUE_INSERT = text(
    "INSERT INTO ingestion_run_extracted_value (extracted_value_id, run_id, document_id) "
    "VALUES (:extracted_value_id, :run_id, :document_id)"
)
RUN_FAILURE_INSERT = text(
    "INSERT INTO ingestion_run_extraction_failure "
    "(extraction_failure_id, run_id, document_id) "
    "VALUES (:extraction_failure_id, :run_id, :document_id)"
)
LINE_ITEM_INSERT = text(
    "INSERT INTO extracted_value_line_item "
    "(extracted_value_id, run_id, document_id, item_ordinal) "
    "VALUES (:extracted_value_id, :run_id, :document_id, 1)"
)
PARSE_SIGNAL_INSERT = text(
    "INSERT INTO extracted_value_parse_signal "
    "(extracted_value_id, run_id, document_id, label_match, source_chunk_count, "
    "validated_after_repair) "
    "VALUES (:extracted_value_id, :run_id, :document_id, 'canonical', 2, false)"
)


@dataclass(frozen=True)
class Generation:
    """One written generation, and the identifiers it minted."""

    run_id: UUID
    chunk_ids: tuple[UUID, ...]
    value_id: UUID
    failure_id: UUID


def identity_for(trace_id: str) -> RunIdentity:
    return RunIdentity(
        agent_id=AGENT,
        provider_model="claude-opus-5",
        chunker_version="e006-chunker-1",
        embedding_model_id="sentence-transformers/all-MiniLM-L6-v2",
        embedding_model_revision="e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
        corpus_manifest_digests=[f"sha256:{'c' * 64}"],
        extraction_prompt_digest=f"sha256:{'d' * 64}",
        extraction_schema_digest=f"sha256:{'e' * 64}",
        resolution_mode="replay",
        run_trace_id=trace_id,
    )


@pytest.fixture
def raw_connection(db_session: Session) -> psycopg.Connection:
    """The session's own psycopg connection, inside the same transaction."""
    return db_session.connection().connection.driver_connection  # type: ignore[return-value]


@pytest.fixture
def seeded_document(db_session: Session) -> None:
    db_session.execute(DOCUMENT_INSERT, dict(DOCUMENT_ROW))


def write_generation(
    db_session: Session, raw_connection: psycopg.Connection, *, trace_id: str
) -> Generation:
    """One complete generation of `DOCUMENT_ID`, in `data-model.md` write order.

    Two chunks and one page-split value, so the removal has a row in **every**
    table a generation touches — including `extracted_value_contributing_chunk`,
    which is reachable only through the value the associations name.
    """
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for(trace_id))
    db_session.execute(
        GENERATION_INSERT,
        {
            "run_id": run_id,
            "document_id": DOCUMENT_ID,
            "status": STATUS_ACTIVE,
            "input_tuple_digest": DIGEST,
        },
    )

    chunk_ids = []
    for ordinal, page in enumerate((1, 2)):
        chunk_id = uuid4()
        chunk_ids.append(chunk_id)
        db_session.execute(
            CHUNK_INSERT,
            {
                "chunk_id": chunk_id,
                "document_id": DOCUMENT_ID,
                "document_type": DOCUMENT_ROW["document_type"],
                "project_id": DOCUMENT_ROW["project_id"],
                "page_number": page,
                "ordinal": ordinal,
                "body_text": f"Manufacturer: Norhelm Transformer Wks. (page {page})",
                "embedding_model_id": DOCUMENT_ROW["generator_id"],
                "embedding_model_revision": "e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
            },
        )

    # The anchor is the chunk printing the value — the later page (FR-029).
    value_id = uuid4()
    db_session.execute(
        VALUE_INSERT,
        {"extracted_value_id": value_id, "source_chunk_id": chunk_ids[1], "cited_page": 2},
    )
    # §Write Order step 3, through the writer's own statement (T066). One row
    # for the label's page and none for the anchor, which is contributor 1 and
    # lives on `extracted_value` — `ck_evcc__ordinal_min` refuses ordinal 1 here.
    with raw_connection.cursor() as cursor:
        contributing = write_contributing_chunks(
            cursor, DOCUMENT_ID, [PAGE_SPLIT_VALUE], [value_id], chunk_ids
        )
    assert contributing == 1
    failure_id = uuid4()
    db_session.execute(
        FAILURE_INSERT,
        {
            "extraction_failure_id": failure_id,
            "source_chunk_id": chunk_ids[0],
            "attempted_page": 1,
            "outcome": FAILURE_OUTCOMES[0],
        },
    )

    for chunk_id in chunk_ids:
        db_session.execute(
            RUN_CHUNK_INSERT,
            {"chunk_id": chunk_id, "run_id": run_id, "document_id": DOCUMENT_ID},
        )
    db_session.execute(
        RUN_VALUE_INSERT,
        {"extracted_value_id": value_id, "run_id": run_id, "document_id": DOCUMENT_ID},
    )
    db_session.execute(
        RUN_FAILURE_INSERT,
        {"extraction_failure_id": failure_id, "run_id": run_id, "document_id": DOCUMENT_ID},
    )
    db_session.execute(
        LINE_ITEM_INSERT,
        {"extracted_value_id": value_id, "run_id": run_id, "document_id": DOCUMENT_ID},
    )
    db_session.execute(
        PARSE_SIGNAL_INSERT,
        {"extracted_value_id": value_id, "run_id": run_id, "document_id": DOCUMENT_ID},
    )
    return Generation(
        run_id=run_id, chunk_ids=tuple(chunk_ids), value_id=value_id, failure_id=failure_id
    )


def resident(db_session: Session) -> dict[str, int]:
    """Rows this document holds, per table. The population "zero" is measured on."""
    return {
        table: db_session.execute(text(statement), {"document_id": DOCUMENT_ID}).scalar_one()
        for table, statement in RESIDENT_COUNTS.items()
    }


# ---------------------------------------------------------------------------
# FR-055 / SC-043 — at most one active generation per document
# ---------------------------------------------------------------------------


def test_a_second_active_generation_of_one_document_is_unrepresentable(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: None,
    assert_rejects: RejectionAsserter,
) -> None:
    """`ix_ingestion_run_document__single_active`, as a write-time refusal.

    Not a convention a reader has to remember: a second activation fails on
    write rather than producing two live generations that downstream readers
    silently union. The index is **per document** — 51 independent invariants —
    unlike E003's global `ix_forecast_run__single_active`.
    """
    del seeded_document
    write_generation(db_session, raw_connection, trace_id="a" * 32)
    other = uuid4()
    write_run_record(raw_connection, run_id=other, identity=identity_for("b" * 32))
    with assert_rejects(
        db_session, psycopg.errors.UniqueViolation, "ix_ingestion_run_document__single_active"
    ):
        db_session.execute(
            GENERATION_INSERT,
            {
                "run_id": other,
                "document_id": DOCUMENT_ID,
                "status": STATUS_ACTIVE,
                "input_tuple_digest": DIGEST,
            },
        )


def test_a_status_outside_the_declared_pair_is_refused(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: None,
    assert_rejects: RejectionAsserter,
) -> None:
    """`ck_ingestion_run_document__status`. The state space is exactly two."""
    del seeded_document
    assert GENERATION_STATUSES == (STATUS_ACTIVE, STATUS_SUPERSEDED)
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("c" * 32))
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_ingestion_run_document__status"
    ):
        db_session.execute(
            GENERATION_INSERT,
            {
                "run_id": run_id,
                "document_id": DOCUMENT_ID,
                "status": "retired",
                "input_tuple_digest": DIGEST,
            },
        )


def test_a_null_status_is_refused_by_the_column_and_not_by_the_check(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: None,
) -> None:
    """The third state arrived at by omission, and why `NOT NULL` is load-bearing.

    A `CHECK` rejects only on *false*, so a NULL status would pass it; and
    `status = 'active'` is NULL for a NULL status, so the row would fall out of
    the partial index predicate as well — invisible to the invariant and to
    every reader. `NOT NULL` is what closes it, and the rejection is asserted to
    come from the column rather than from the check.
    """
    del seeded_document
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("d" * 32))
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(Exception) as caught:  # noqa: PT011 - class asserted below
            db_session.execute(
                GENERATION_INSERT,
                {
                    "run_id": run_id,
                    "document_id": DOCUMENT_ID,
                    "status": None,
                    "input_tuple_digest": DIGEST,
                },
            )
    finally:
        savepoint.rollback()
    assert isinstance(caught.value.orig, psycopg.errors.NotNullViolation)  # type: ignore[attr-defined]
    assert caught.value.orig.diag.column_name == "status"  # type: ignore[attr-defined,union-attr]


def test_two_resident_generations_are_unstorable_even_without_the_index(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: None,
    assert_rejects: RejectionAsserter,
) -> None:
    """E003's `uq_chunk__document_ordinal` — the constraint that forced ADR-0020.

    Scoped to the document rather than to the generation, and E006 may neither
    add a constraint to `chunk` nor widen this one. Chunk ordinals are
    zero-based, so a second resident generation's ordinal 0 collides. Retention
    was therefore not expensive but **unstorable**, and this is the premise
    stated as a test rather than quoted from the record.
    """
    del seeded_document
    write_generation(db_session, raw_connection, trace_id="e" * 32)
    with assert_rejects(db_session, psycopg.errors.UniqueViolation, "uq_chunk__document_ordinal"):
        db_session.execute(
            CHUNK_INSERT,
            {
                "chunk_id": uuid4(),
                "document_id": DOCUMENT_ID,
                "document_type": DOCUMENT_ROW["document_type"],
                "project_id": DOCUMENT_ROW["project_id"],
                "page_number": 1,
                "ordinal": 0,
                "body_text": "a second generation's first chunk",
                "embedding_model_id": DOCUMENT_ROW["generator_id"],
                "embedding_model_revision": "e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
            },
        )


# ---------------------------------------------------------------------------
# FR-055 / {SAD:ADR-0020} — the promotion, and what it leaves
# ---------------------------------------------------------------------------


def test_a_first_ingest_has_no_predecessor_and_removes_nothing(
    db_session: Session, raw_connection: psycopg.Connection, seeded_document: None
) -> None:
    """The line between first ingestion and re-ingestion, as an outcome value.

    A run of first ingests and skips alone never removes a row and runs
    unattended under the application role; a run that replaces any existing
    generation does not. `replaced` is the whole difference, and it is reported
    rather than inferred from a zero count — a promotion that silently removed
    nothing would otherwise be indistinguishable from a first ingest.
    """
    del seeded_document
    assert active_generation(raw_connection, DOCUMENT_ID) is None
    outcome = promote_generation(raw_connection, DOCUMENT_ID)
    assert outcome.replaced is False
    assert outcome.superseded_run_id is None
    assert outcome.rows_removed == 0


def test_a_promotion_leaves_zero_rows_of_the_generation_it_replaced(
    db_session: Session, raw_connection: psycopg.Connection, seeded_document: None
) -> None:
    """SC-043's third clause, over **every** table a generation touches.

    The population is enumerated in `RESIDENT_COUNTS` and asserted non-empty
    before the promotion, so "zero rows left" is measured against something
    rather than being true because nothing was there (FR-068).
    """
    del seeded_document
    first = write_generation(db_session, raw_connection, trace_id="f" * 32)
    before = resident(db_session)
    assert all(count > 0 for count in before.values()), (
        f"the generation to be replaced holds {before}; a zero population makes "
        f"'zero rows left' vacuous"
    )

    outcome = promote_generation(raw_connection, DOCUMENT_ID)
    assert outcome.replaced is True
    assert outcome.superseded_run_id == first.run_id

    after = resident(db_session)
    assert all(count == 0 for count in after.values()), f"the promotion left {after}"

    # The run row outlives its rows, and that is the point ({SAD:ADR-0020}).
    surviving = db_session.execute(
        text("SELECT count(*) FROM ingestion_run WHERE run_id = :id"), {"id": first.run_id}
    ).scalar_one()
    assert surviving == 1, (
        "promotion stops at the generation row: a replaced run's identity, input tuple "
        "configuration, timings and model identifiers survive its output"
    )


def test_the_removal_reports_a_count_for_every_table_it_touched(
    db_session: Session, raw_connection: psycopg.Connection, seeded_document: None
) -> None:
    """Step 0b, evidenced by what step 0e could only have found through it.

    `extracted_value`, `extraction_failure` and `chunk` are E003's and carry no
    run column: the **only** thing that says which of their rows belonged to
    this generation is the three run-output associations, which the leaf-up
    order deletes at step 0d. A non-zero count for those three is therefore
    proof that the identifier sets were materialised before the associations
    went — an implementation that identified as it went would report zero and
    leave the rows behind.
    """
    del seeded_document
    write_generation(db_session, raw_connection, trace_id="1" * 32)
    outcome = promote_generation(raw_connection, DOCUMENT_ID)
    assert outcome.removed == {
        "line_items": 1,
        "parse_signals": 1,
        "value_associations": 1,
        "failure_associations": 1,
        "chunk_associations": 2,
        "failures": 1,
        "contributing_chunks": 1,
        "values": 1,
        "chunks": 2,
        "generation": 1,
    }


def test_no_superseded_row_survives_the_transaction_that_marked_it(
    db_session: Session, raw_connection: psycopg.Connection, seeded_document: None
) -> None:
    """SC-043's second clause. `superseded` is a within-transaction state.

    The mark names the generation the removal then deletes, and both are steps
    of one transaction — so the value is real, is what the delete statements
    select on, and is never observable in committed state. Both halves are
    asserted: the vocabulary still admits it, and no row carries it afterwards.
    """
    del seeded_document
    write_generation(db_session, raw_connection, trace_id="2" * 32)
    promote_generation(raw_connection, DOCUMENT_ID)
    lingering = db_session.execute(
        text("SELECT count(*) FROM ingestion_run_document WHERE status = :status"),
        {"status": STATUS_SUPERSEDED},
    ).scalar_one()
    assert lingering == 0


def test_the_successor_is_written_as_active_after_the_removal(
    db_session: Session, raw_connection: psycopg.Connection, seeded_document: None
) -> None:
    """The whole promotion, end to end, in the order the write order fixes.

    Removal precedes the write and no setting rescues the reverse order:
    `CREATE UNIQUE INDEX … WHERE` produces an index rather than a constraint and
    PostgreSQL admits `DEFERRABLE` only on constraints. After step 0g the
    successor's ordinal 0 is free — which is `uq_chunk__document_ordinal`
    releasing, the constraint that made retention impossible in the first place.
    """
    del seeded_document
    first = write_generation(db_session, raw_connection, trace_id="3" * 32)
    promote_generation(raw_connection, DOCUMENT_ID)
    assert active_generation(raw_connection, DOCUMENT_ID) is None

    second = write_generation(db_session, raw_connection, trace_id="4" * 32)
    assert second.run_id != first.run_id
    assert active_generation(raw_connection, DOCUMENT_ID) == second.run_id

    statuses = (
        db_session.execute(
            text("SELECT status FROM ingestion_run_document WHERE document_id = :document_id"),
            {"document_id": DOCUMENT_ID},
        )
        .scalars()
        .all()
    )
    assert statuses == [STATUS_ACTIVE]

    after = resident(db_session)
    assert after["chunk"] == 2, "the successor's chunks are resident and the predecessor's are not"
