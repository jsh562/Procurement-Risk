"""FR-038 / FR-039 / SC-021: every row this epic writes names exactly one run.

T071, and the run record T069 writes is asserted here too because it is the
thing everything else resolves *to* — an anti-join against a run nothing
described would be a join against a hole.

**Two halves, and only one of them is a key.** `pk_ingestion_run_chunk` is the
chunk's own identifier, so a second association row for one chunk is a primary
key collision: "**at most** one run" needs no test, and the two tests that try
it here are there to show the mechanism firing rather than to establish the
property. The other half — that an association row exists **at all** — is
cross-table absence, is disclosed as **G-1**, and is what the anti-joins below
close. Nothing structural requires a chunk to be associated; only the write
order does, and a write order is a habit until something counts the rows it
missed.

**The anti-joins are corpus-wide and not scoped to the fixture.** `FROM chunk
LEFT JOIN ingestion_run_chunk` with no `WHERE document_id = …` is deliberate: a
check scoped to the rows the test just wrote would pass while an unattributed
row sat beside it, which is exactly the state SC-021 is about. Enumeration is
inside `db_session`'s outer transaction, so the population is this file's rows
plus anything already committed, and the count is asserted non-empty (FR-068) so
a query returning nothing cannot pass for a corpus with nothing wrong.

**Isolation.** Every write here is inside `db_session`'s outer transaction and
is rolled back in teardown. The modules under test hold a psycopg connection, so
they are handed the session's own driver connection — the same transaction, not
a second one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from model.ingest.failures import FAILURE_OUTCOMES, AttemptedChunk, ExtractionFailure
from model.ingest.lineitems import LineItemMembership
from model.ingest.runs import (
    RUN_STATES,
    AgentIdentity,
    RunError,
    RunIdentity,
    finish_run,
    read_run_state,
    write_run_record,
)
from model.ingest.writer import (
    RUN_ASSOCIATIONS,
    WriterError,
    write_failures,
    write_line_items,
    write_run_associations,
)

RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

DOCUMENT_ID = "prj-901-t0001-r0"

#: The agent identity this file records runs under, built from its parts rather
#: than typed. `ck_ingestion_run__agent_id_format` is what refuses a half
#: answer; `AgentIdentity` is what stops one being written by accident.
AGENT = AgentIdentity(
    principal_kind="automation",
    principal_id="e006-run-attribution",
    distribution="model",
    version="0.1.0",
    vcs_revision="0123abc",
)

DOCUMENT_ROW: Mapping[str, Any] = {
    "document_id": DOCUMENT_ID,
    "document_type": "submittal",
    "project_id": "PRJ-901",
    "title": "Run-attribution fixture transmittal",
    "source_kind": "SYNTHETIC",
    "source_ref": None,
    "issuing_body": None,
    "retrieval_date": None,
    "generator_id": "model.corpus.generate",
    "generation_seed": 901,
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

#: The vector is built server-side from `schema_constants.vector_dimension`, so
#: no 384-element literal crosses the driver boundary and this file holds no
#: second opinion about the width of the vector space.
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
        :extracted_value_id, :source_chunk_id, :cited_page, :field_name, 'text',
        :value_text, NULL, :confidence, 'single_chunk', 1
    )
    """
)

GENERATION_INSERT = text(
    """
    INSERT INTO ingestion_run_document (run_id, document_id, status, input_tuple_digest)
    VALUES (:run_id, :document_id, 'active', :input_tuple_digest)
    """
)

#: One statement per association, all three the same shape — built from
#: `writer.RUN_ASSOCIATIONS` so an association added to the epic and not to this
#: file is a `KeyError` here rather than a table nobody anti-joined.
ASSOCIATION_INSERTS: Mapping[str, Any] = {
    table: text(
        f"INSERT INTO {table} ({target}, run_id, document_id) "  # noqa: S608
        f"VALUES (:target, :run_id, :document_id)"
    )
    for table, target in RUN_ASSOCIATIONS
}

#: SC-021's anti-join, one per target table. `LEFT JOIN … WHERE a.<key> IS NULL`
#: rather than `NOT EXISTS` for one reason: the failing rows are what the
#: assertion message needs, and this form returns them.
ANTI_JOINS: Mapping[str, Any] = {
    table: text(
        f"SELECT t.{target} AS orphan FROM {source} t "  # noqa: S608
        f"LEFT JOIN {table} a ON a.{target} = t.{target} "
        f"WHERE a.{target} IS NULL"
    )
    for (table, target), source in zip(
        RUN_ASSOCIATIONS, ("chunk", "extracted_value", "extraction_failure"), strict=True
    )
}

POPULATIONS: Mapping[str, str] = {
    "ingestion_run_chunk": "chunk",
    "ingestion_run_extracted_value": "extracted_value",
    "ingestion_run_extraction_failure": "extraction_failure",
}


def identity_for(trace_id: str, *, agent: AgentIdentity | str = AGENT) -> RunIdentity:
    """A run identity the row's own `CHECK`s accept."""
    return RunIdentity(
        agent_id=agent,
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
def attributed_generation(
    db_session: Session, raw_connection: psycopg.Connection
) -> tuple[UUID, dict[str, list[UUID]]]:
    """One run, one generation, and one of each attributable row — associated.

    The three attributable rows are written by direct statements: the subject
    here is the *shape of the attribution*, and routing the chunk through the
    encoder and a fresh page read would make a schema assertion depend on a
    90-second parse of a PDF.

    **The associations themselves are written by `writer.write_run_associations`,
    not by this file.** That is deliberate and it is what makes the anti-joins
    below worth anything: they range over the rows the ingestion job's own
    §Write Order step 5 produces, so a step that skipped a table would fail here
    rather than being papered over by a fixture that wrote the rows the job
    forgot.
    """
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("a" * 32))
    db_session.execute(DOCUMENT_INSERT, dict(DOCUMENT_ROW))
    db_session.execute(
        GENERATION_INSERT,
        {
            "run_id": run_id,
            "document_id": DOCUMENT_ID,
            "input_tuple_digest": f"sha256:{'f' * 64}",
        },
    )

    written: dict[str, list[UUID]] = {table: [] for table, _ in RUN_ASSOCIATIONS}
    chunk_id = uuid4()
    db_session.execute(
        CHUNK_INSERT,
        {
            "chunk_id": chunk_id,
            "document_id": DOCUMENT_ID,
            "document_type": DOCUMENT_ROW["document_type"],
            "project_id": DOCUMENT_ROW["project_id"],
            "page_number": 1,
            "ordinal": 0,
            "body_text": "Manufacturer: Norhelm Transformer Wks.",
            "embedding_model_id": DOCUMENT_ROW["generator_id"],
            "embedding_model_revision": "e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
        },
    )
    written["ingestion_run_chunk"].append(chunk_id)

    value_id = uuid4()
    db_session.execute(
        VALUE_INSERT,
        {
            "extracted_value_id": value_id,
            "source_chunk_id": chunk_id,
            "cited_page": 1,
            "field_name": "manufacturer",
            "value_text": "Norhelm Transformer Wks.",
            "confidence": 1.0,
        },
    )
    written["ingestion_run_extracted_value"].append(value_id)

    # §Write Order step 4, through the writer's own statement. The failure row's
    # identifier is minted in the job process — `extraction_failure` carries no
    # default on its primary key — so this is also what proves the writer mints
    # one rather than relying on a default that does not exist.
    with raw_connection.cursor() as cursor:
        failure_ids = write_failures(
            cursor,
            DOCUMENT_ID,
            [
                ExtractionFailure(
                    source_chunk=AttemptedChunk(ordinal=0, page_number=1),
                    field_name="part_number",
                    outcome=FAILURE_OUTCOMES[0],
                    repair_attempt_count=0,
                    detail="the document prints no part number for this item",
                )
            ],
            [chunk_id],
        )
    written["ingestion_run_extraction_failure"].extend(failure_ids)

    with raw_connection.cursor() as cursor:
        counts = write_run_associations(
            cursor,
            run_id,
            DOCUMENT_ID,
            chunk_ids=written["ingestion_run_chunk"],
            value_ids=written["ingestion_run_extracted_value"],
            failure_ids=written["ingestion_run_extraction_failure"],
        )
        # §Write Order step 6's line-item row, after step 5 because it targets
        # `ingestion_run_extracted_value` and not `extracted_value` directly.
        write_line_items(
            cursor,
            run_id,
            DOCUMENT_ID,
            [
                LineItemMembership(
                    position=0, run_id=str(run_id), document_id=DOCUMENT_ID, item_ordinal=1
                )
            ],
            [value_id],
        )
    assert counts == {table: len(written[table]) for table, _ in RUN_ASSOCIATIONS}
    return run_id, written


# ---------------------------------------------------------------------------
# T069 / FR-038 — the run record everything else resolves to
# ---------------------------------------------------------------------------


def test_the_run_record_carries_the_composite_principal_and_build_identity(
    db_session: Session, raw_connection: psycopg.Connection
) -> None:
    """FR-038, SC-022. E003's TR-082 dropped its per-row agent column on the
    grounds that this epic holds identity at run granularity, so this column is
    the project's only record of who is responsible for a citation — and it is
    stored as the declared composite rather than as either half."""
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("b" * 32))
    stored = db_session.execute(
        text("SELECT agent_id FROM ingestion_run WHERE run_id = :id"), {"id": run_id}
    ).scalar_one()
    assert stored == str(AGENT)
    assert "principal=automation:e006-run-attribution" in stored
    assert "build=model@0.1.0+0123abc" in stored


@pytest.mark.parametrize(
    "half",
    [
        "principal=automation:e006-run-attribution",
        "build=model@0.1.0+0123abc",
        "e006-run-attribution",
    ],
)
def test_an_agent_identity_naming_only_one_half_is_unstorable(
    db_session: Session, raw_connection: psycopg.Connection, half: str
) -> None:
    """`ck_ingestion_run__agent_id_format`, and why presence alone is not enough.

    Each of these passes `ck_ingestion_run__agent_id_present` — none is blank —
    and each answers at most half of "who is responsible for this citation".
    The format check is what refuses them, and it is matched by **name** so a
    row rejected for being blank stays distinguishable from one rejected for
    naming only a person.
    """
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            write_run_record(
                raw_connection, run_id=uuid4(), identity=identity_for("c" * 32, agent=half)
            )
    finally:
        savepoint.rollback()
    assert caught.value.diag.constraint_name == "ck_ingestion_run__agent_id_format"


def test_a_run_carries_no_finish_until_it_completes(
    db_session: Session, raw_connection: psycopg.Connection
) -> None:
    """FR-038: the finish is recorded **when the run completes**, not before.

    A finish written at insert time would make an abort indistinguishable from a
    completion whose process died, which is the distinction the three readable
    states exist to keep.
    """
    run_id = uuid4()
    started = datetime.now(UTC)
    write_run_record(
        raw_connection, run_id=run_id, identity=identity_for("d" * 32), started_at=started
    )
    assert read_run_state(raw_connection, run_id) == "in_flight"
    assert (
        db_session.execute(
            text("SELECT finished_at FROM ingestion_run WHERE run_id = :id"), {"id": run_id}
        ).scalar_one()
        is None
    )

    finished = finish_run(raw_connection, run_id, finished_at=started + timedelta(seconds=5))
    assert read_run_state(raw_connection, run_id) == "complete"
    assert (
        db_session.execute(
            text("SELECT finished_at FROM ingestion_run WHERE run_id = :id"), {"id": run_id}
        ).scalar_one()
        == finished
    )


def test_a_run_that_recorded_a_run_level_failure_cannot_report_completion(
    db_session: Session,
    raw_connection: psycopg.Connection,
    assert_rejects: RejectionAsserter,
) -> None:
    """SC-044, as a database fact and as a refusal that names the run.

    `ck_ingestion_run__failed_run_unfinished` makes the pair unstorable;
    `finish_run` refuses one statement earlier so the message is about the run
    rather than about a column. Both are asserted, because neither is trusted to
    cover the other.
    """
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("e" * 32))
    db_session.execute(
        text(
            "UPDATE ingestion_run SET run_failure_kind = 'fixture_missing', "
            "run_failure_detail = 'no committed fixture for the resolution key' "
            "WHERE run_id = :id"
        ),
        {"id": run_id},
    )
    assert read_run_state(raw_connection, run_id) == "aborted"

    with pytest.raises(RunError, match="aborted"):
        finish_run(raw_connection, run_id)

    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_ingestion_run__failed_run_unfinished"
    ):
        db_session.execute(
            text("UPDATE ingestion_run SET finished_at = now() WHERE run_id = :id"),
            {"id": run_id},
        )


def test_the_three_run_states_are_the_declared_ones(
    raw_connection: psycopg.Connection,
) -> None:
    """FR-038 names three readable states and `read_run_state` returns those."""
    run_id = uuid4()
    write_run_record(raw_connection, run_id=run_id, identity=identity_for("f" * 32))
    assert read_run_state(raw_connection, run_id) in RUN_STATES
    with pytest.raises(RunError, match="no `ingestion_run` row"):
        read_run_state(raw_connection, uuid4())


# ---------------------------------------------------------------------------
# T071 / FR-039, SC-021 — the corpus-wide anti-join
# ---------------------------------------------------------------------------


def detach(db_session: Session, table: str, target: str, identifier: UUID) -> None:
    """Remove one association row, leaf-up.

    `extracted_value_line_item` and `extracted_value_parse_signal` reference
    `ingestion_run_extracted_value` with `ON DELETE RESTRICT`, which cannot be
    deferred — so the value association is not removable until its own children
    are. That is the same leaf-up rule the promotion's removal follows, and the
    tests below orphan a row deliberately rather than working around it: an
    orphan is what the anti-join has to find, and creating one honestly means
    obeying the referential actions on the way.
    """
    if table == "ingestion_run_extracted_value":
        for leaf in ("extracted_value_line_item", "extracted_value_parse_signal"):
            db_session.execute(
                text(f"DELETE FROM {leaf} WHERE extracted_value_id = :target"),  # noqa: S608
                {"target": identifier},
            )
    db_session.execute(
        text(f"DELETE FROM {table} WHERE {target} = :target"),  # noqa: S608
        {"target": identifier},
    )


def test_every_chunk_value_and_failure_resolves_to_an_ingestion_run(
    db_session: Session, attributed_generation: tuple[UUID, dict[str, list[UUID]]]
) -> None:
    """SC-021's half that no key enforces (G-1), corpus-wide.

    Three anti-joins, one per target table, each over **every** row of that
    table rather than over the rows this test wrote. A check scoped to the
    fixture would pass while an unattributed row from any other run sat beside
    it, which is precisely the state the criterion is about.
    """
    del attributed_generation
    for table, source in POPULATIONS.items():
        orphans = db_session.execute(ANTI_JOINS[table]).scalars().all()
        assert not orphans, (
            f"FR-039 / SC-021: {len(orphans)} row(s) of {source} resolve to no ingestion "
            f"run through {table}. First five: {[str(o) for o in orphans[:5]]}"
        )


def test_the_enumerated_population_is_published_and_is_not_empty(
    db_session: Session, attributed_generation: tuple[UUID, dict[str, list[UUID]]]
) -> None:
    """FR-068: an anti-join over an empty table finds nothing wrong for the same
    reason it does over a correct one, so the count is asserted beside it."""
    del attributed_generation
    counted = {
        source: db_session.execute(text(f"SELECT count(*) FROM {source}")).scalar_one()  # noqa: S608
        for source in POPULATIONS.values()
    }
    assert all(count > 0 for count in counted.values()), (
        f"the anti-join enumerated {counted}; a zero population passes vacuously"
    )


def test_every_association_row_names_the_same_run_and_document(
    db_session: Session, attributed_generation: tuple[UUID, dict[str, list[UUID]]]
) -> None:
    """One generation, so one `(run_id, document_id)` across all three tables.

    The composite foreign keys make each association name an *existing*
    generation; that they all name the *same* one for one document's rows is
    what makes "this document's output belongs to this run" a single fact rather
    than three.
    """
    run_id, _written = attributed_generation
    for table, _target in RUN_ASSOCIATIONS:
        pairs = db_session.execute(
            text(f"SELECT DISTINCT run_id, document_id FROM {table}")  # noqa: S608
        ).all()
        assert pairs == [(run_id, DOCUMENT_ID)], f"{table} names {pairs}"


@pytest.mark.parametrize(("table", "target"), RUN_ASSOCIATIONS)
def test_a_second_association_for_one_row_is_a_key_collision(
    db_session: Session,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
    assert_rejects: RejectionAsserter,
    table: str,
    target: str,
) -> None:
    """Exactly one run is a uniqueness fact, not a convention.

    The target row's own identifier is the association's **whole** primary key,
    so a second row attributing one chunk to a second run collides. Asserted per
    table, matching on the constraint name, so a table that lost the property
    fails in its own right rather than being covered by a sibling.
    """
    run_id, written = attributed_generation
    identifier = written[table][0]
    other_run = uuid4()
    with assert_rejects(db_session, psycopg.errors.UniqueViolation, f"pk_{table}"):
        db_session.execute(
            ASSOCIATION_INSERTS[table],
            {"target": identifier, "run_id": other_run, "document_id": DOCUMENT_ID},
        )
    del target


@pytest.mark.parametrize(("table", "target"), RUN_ASSOCIATIONS)
def test_an_association_naming_no_generation_is_refused(
    db_session: Session,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
    assert_rejects: RejectionAsserter,
    table: str,
    target: str,
) -> None:
    """`fk_<table>__generation`, composite over `(run_id, document_id)`.

    An association pointing at a run that never ingested this document would
    make the row resolvable to a run and unresolvable to a generation, so
    attribution would be readable and provenance would not.
    """
    _run_id, written = attributed_generation
    detach(db_session, table, target, written[table][0])
    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, f"fk_{table}__generation"):
        db_session.execute(
            ASSOCIATION_INSERTS[table],
            {"target": written[table][0], "run_id": uuid4(), "document_id": DOCUMENT_ID},
        )


def test_the_anti_join_finds_a_row_that_lost_its_association(
    db_session: Session, attributed_generation: tuple[UUID, dict[str, list[UUID]]]
) -> None:
    """The failing direction, demonstrated rather than assumed.

    An anti-join that always returned nothing would be indistinguishable from
    this one on a correct corpus. One association row is removed and each of the
    three checks is required to name exactly the row it orphaned — which also
    shows the three are independent rather than one query repeated.
    """
    _run_id, written = attributed_generation
    for table, target in RUN_ASSOCIATIONS:
        savepoint = db_session.begin_nested()
        try:
            orphaned = written[table][0]
            detach(db_session, table, target, orphaned)
            found = db_session.execute(ANTI_JOINS[table]).scalars().all()
            assert found == [orphaned], (
                f"removing {table}'s only row left the anti-join reporting {found}"
            )
        finally:
            savepoint.rollback()


# ---------------------------------------------------------------------------
# T070 / FR-039 — the value-level rows carry their value's own attribution
# ---------------------------------------------------------------------------


def test_a_value_level_row_cannot_carry_another_runs_attribution(
    raw_connection: psycopg.Connection,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
) -> None:
    """FR-039's second half, refused before the foreign key sees it.

    `fk_extracted_value_line_item__run_output` targets
    `uq_ingestion_run_extracted_value__value_generation` — all three columns in
    one referenced key — so a membership naming a different run has no referent
    and is rejected by the database. The writer refuses it one statement earlier
    with a message that names the value, because a foreign-key diagnostic about a
    three-column key is not what an operator needs to read at 3 a.m.
    """
    run_id, written = attributed_generation
    value_id = written["ingestion_run_extracted_value"][0]
    with raw_connection.cursor() as cursor:
        for membership in (
            LineItemMembership(
                position=0, run_id=str(uuid4()), document_id=DOCUMENT_ID, item_ordinal=1
            ),
            LineItemMembership(
                position=0, run_id=str(run_id), document_id="prj-999-t9999-r0", item_ordinal=1
            ),
        ):
            with pytest.raises(WriterError, match="FR-039"):
                write_line_items(cursor, run_id, DOCUMENT_ID, [membership], [value_id])


def test_a_membership_for_a_value_this_document_never_wrote_is_refused(
    raw_connection: psycopg.Connection,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
) -> None:
    """A membership for a value with no row has nothing to belong to (FR-059)."""
    run_id, written = attributed_generation
    with raw_connection.cursor() as cursor, pytest.raises(WriterError, match="FR-059"):
        write_line_items(
            cursor,
            run_id,
            DOCUMENT_ID,
            [
                LineItemMembership(
                    position=7, run_id=str(run_id), document_id=DOCUMENT_ID, item_ordinal=1
                )
            ],
            written["ingestion_run_extracted_value"],
        )


def test_a_value_grouped_twice_is_refused_before_the_primary_key_sees_it(
    db_session: Session,
    raw_connection: psycopg.Connection,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
) -> None:
    """`pk_extracted_value_line_item` is the value alone, so a second membership
    is unrepresentable — and failing here rather than at the write means the
    transaction has not already done its other work.

    The fixture's own membership for this value is removed first, so what the
    refusal reacts to is the duplicate *inside this call* rather than a
    collision with a row that was already there. Both would raise; only one of
    them is the rule under test.
    """
    run_id, written = attributed_generation
    value_id = written["ingestion_run_extracted_value"][0]
    db_session.execute(
        text("DELETE FROM extracted_value_line_item WHERE extracted_value_id = :id"),
        {"id": value_id},
    )
    membership = LineItemMembership(
        position=0, run_id=str(run_id), document_id=DOCUMENT_ID, item_ordinal=1
    )
    with raw_connection.cursor() as cursor, pytest.raises(WriterError, match="FR-059"):
        write_line_items(cursor, run_id, DOCUMENT_ID, [membership, membership], [value_id])


def test_a_failure_citing_a_chunk_this_document_never_wrote_is_refused(
    raw_connection: psycopg.Connection,
    attributed_generation: tuple[UUID, dict[str, list[UUID]]],
) -> None:
    """FR-035: a failure is as traceable as a success or it is not a record.

    Caught by the writer rather than by `fk_extraction_failure__chunk_page`, so
    the message names the field and the ordinal rather than an identifier nobody
    can trace back to a chunk that was never minted.
    """
    _run_id, written = attributed_generation
    with raw_connection.cursor() as cursor, pytest.raises(WriterError, match="FR-035"):
        write_failures(
            cursor,
            DOCUMENT_ID,
            [
                ExtractionFailure(
                    source_chunk=AttemptedChunk(ordinal=9, page_number=1),
                    field_name="part_number",
                    outcome=FAILURE_OUTCOMES[0],
                    repair_attempt_count=0,
                    detail="an ordinal this document has no chunk for",
                )
            ],
            written["ingestion_run_chunk"],
        )
