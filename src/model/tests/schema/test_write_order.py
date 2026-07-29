"""FR-054 / FR-042 / FR-056 (T075, T077): the per-document transaction, end to end.

Everything asserted here needs a **real, autocommit** connection and therefore
cannot use `db_session`, whose whole design is an outer transaction that is never
committed. That is not an inconvenience to work around — it is the subject:

* `write_document_generation` **refuses** a non-autocommit connection, because on
  a default connection psycopg opens one implicit transaction for the whole run
  and every `with conn.transaction()` becomes a savepoint inside it. FR-042
  would then be false while the code still read as though it were true.
* `record_run_failure` refuses a connection that is *inside* a transaction, which
  is HINT-002 enforced rather than remembered: a row written inside document
  *d*'s transaction to explain why *d* failed rolls back with *d*.

So these tests run against a scratch database of their own, created and dropped
per test by `empty_scratch_database`, with the migration chain applied through
the `migrate` console entry — the sequence as anyone actually applies it. Rows
are really committed, and the database is really thrown away.

**What is deliberately faked, and what is not.** The chunking and the vectors are
constructed rather than produced by the chunker and the ONNX session: neither is
under test here, both are covered by `tests/ingest/`, and pulling an encoder
session into a transaction-boundary test would make it slow and would couple two
unrelated failure modes. Everything that touches the database is real — the write
order, the COPY, the composite foreign keys, the partial unique index, the
promotion's leaf-up removal, and the privilege-free recovery path.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import psycopg
import pytest
from sqlalchemy import URL

from model.ingest.chunker import Chunk, DocumentChunking
from model.ingest.cli import corpus_digest_mismatch
from model.ingest.documents import DocumentRecord
from model.ingest.parse import ParsedPage
from model.ingest.runs import (
    AgentIdentity,
    RunError,
    RunIdentity,
    active_generation,
    finish_run,
    read_run_state,
    record_run_failure,
    write_run_record,
)
from model.ingest.writer import (
    PreparedDocument,
    WriterError,
    connect,
    vector_dimension,
    write_document_generation,
    write_generations,
)
from model.schema.cli import main as migrate

AGENT = AgentIdentity(
    principal_kind="automation",
    principal_id="e006-write-order",
    distribution="model",
    version="0.1.0",
    vcs_revision="0123abc",
)

DIGEST_A = f"sha256:{'1' * 64}"
DIGEST_B = f"sha256:{'2' * 64}"

#: One line per chunk, so the containment guard has something real to check: the
#: chunk body must appear in a fresh extraction of the page it names, and these
#: pages are that extraction.
PAGE_LINES = {
    1: ("Submittal number SUB-0001", "Manufacturer: Norhelm Transformer Wks."),
    2: ("Part number NT-4412-A", "Approval date 2026-03-14"),
}


def _pages() -> tuple[ParsedPage, ...]:
    return tuple(
        ParsedPage(number=number, lines=lines, text="\n".join(lines))
        for number, lines in sorted(PAGE_LINES.items())
    )


def _record(document_id: str) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        document_type="transmittal",
        project_id="PRJ-901",
        title=document_id,
        source_kind="SYNTHETIC",
        license_basis="synthetic",
        content_hash=f"sha256:{'a' * 64}",
        path=Path("data/corpus/synthetic") / f"{document_id}.pdf",
        generator_id="model.corpus.generate",
        generation_seed="901",
        generated_at=date(2026, 7, 28),
        fixture_hashes=(f"sha256:{'d' * 64}",),
        roster_hash=f"sha256:{'e' * 64}",
    )


def _prepared(
    document_id: str, dimension: int, *, body_override: str | None = None
) -> PreparedDocument:
    """One document, two chunks, one per page, with unit vectors."""
    record = _record(document_id)
    chunks = []
    for ordinal, (page, lines) in enumerate(sorted(PAGE_LINES.items())):
        body = body_override if (body_override and ordinal == 0) else lines[0]
        chunks.append(
            Chunk(
                document_id=document_id,
                document_type=record.document_type,
                project_id=record.project_id,
                page_number=page,
                ordinal=ordinal,
                body_text=body,
                boundary_class="structural",
                structural_identifier=f"p{page}-body0",
                content_pieces=8,
            )
        )
    vectors = np.zeros((len(chunks), dimension), dtype=np.float32)
    vectors[:, 0] = 1.0
    return PreparedDocument(
        record=record,
        chunking=DocumentChunking(
            document_id=document_id, chunker_version="e006-chunker/test", chunks=tuple(chunks)
        ),
        embeddings=vectors,
        embedding_model_id="sentence-transformers/all-MiniLM-L6-v2",
        embedding_model_revision="e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
    )


def _identity(trace_id: str) -> RunIdentity:
    return RunIdentity(
        agent_id=AGENT,
        provider_model="claude-opus-5",
        chunker_version="e006-chunker/test",
        embedding_model_id="sentence-transformers/all-MiniLM-L6-v2",
        embedding_model_revision="e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
        corpus_manifest_digests=[f"sha256:{'c' * 64}"],
        extraction_prompt_digest=f"sha256:{'d' * 64}",
        extraction_schema_digest=f"sha256:{'e' * 64}",
        resolution_mode="replay",
        run_trace_id=trace_id,
    )


@pytest.fixture
def migrated(empty_scratch_database: URL) -> Iterator[psycopg.Connection]:
    """A migrated scratch database and one **autocommit** connection to it.

    The chain is applied through the `migrate` console entry, which reads
    `DATABASE_URL` — repointed at the scratch database by the fixture — so the
    tests and the migrations cannot disagree about which database they mean.
    """
    assert migrate(["head"]) == 0, "the migration chain did not apply to the scratch database"
    with connect(empty_scratch_database) as connection:
        yield connection


def _count(connection: psycopg.Connection, statement: str, *parameters: object) -> int:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _chunks(connection: psycopg.Connection, document_id: str) -> int:
    """How many chunks this document holds — the resident generation's, by
    {SAD:ADR-0020}, since exactly one generation's rows exist per document."""
    return _count(connection, "SELECT count(*) FROM chunk WHERE document_id = %s", document_id)


def _run(connection: psycopg.Connection, trace_id: str) -> UUID:
    run_id = uuid4()
    write_run_record(connection, run_id=run_id, identity=_identity(trace_id))
    return run_id


# ---------------------------------------------------------------------------
# FR-054 — the stated order, on an autocommit connection, one txn per document
# ---------------------------------------------------------------------------


def test_a_non_autocommit_connection_is_refused(empty_scratch_database: URL) -> None:
    """The setting FR-042 rests on, refused rather than silently demoted.

    On a default connection every `with conn.transaction()` below is a savepoint
    inside one run-long implicit transaction, so a failure at document 50 would
    discard the 49 already written while the code still read as though each had
    committed.
    """
    assert migrate(["head"]) == 0
    url = empty_scratch_database.set(drivername="postgresql")
    with (
        psycopg.connect(url.render_as_string(hide_password=False)) as connection,
        pytest.raises(WriterError, match="FR-042"),
    ):
        write_document_generation(
            connection,
            run_id=uuid4(),
            prepared=_prepared("prj-901-t0001-r0", 384),
            input_tuple_digest=DIGEST_A,
            fresh_pages=_pages(),
            dimension=384,
        )


def test_one_document_commits_every_row_it_wrote(migrated: psycopg.Connection) -> None:
    """Steps 0h through 7: the generation row, the chunks, the associations."""
    connection = migrated
    dimension = vector_dimension(connection)
    run_id = _run(connection, "a" * 32)
    outcome = write_document_generation(
        connection,
        run_id=run_id,
        prepared=_prepared("prj-901-t0001-r0", dimension),
        input_tuple_digest=DIGEST_A,
        fresh_pages=_pages(),
        dimension=dimension,
    )
    assert outcome.committed and outcome.chunks_written == 2
    assert outcome.promotion is None, "a first ingest replaces nothing and says so"
    assert outcome.containment is not None and outcome.containment.count == 2

    assert _chunks(connection, "prj-901-t0001-r0") == 2
    assert (
        _count(
            connection,
            "SELECT count(*) FROM ingestion_run_chunk WHERE run_id = %s AND document_id = %s",
            str(run_id),
            "prj-901-t0001-r0",
        )
        == 2
    )
    assert active_generation(connection, "prj-901-t0001-r0") == run_id


def test_a_chunk_not_on_the_page_it_names_aborts_its_own_document(
    migrated: psycopg.Connection,
) -> None:
    """FR-010 inside the transaction: never committed, not detected afterwards.

    The whole document rolls back — including the chunks that *were* contained —
    because the guard runs after step 1 and before the block closes.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    run_id = _run(connection, "b" * 32)
    with pytest.raises(WriterError, match="FR-010"):
        write_document_generation(
            connection,
            run_id=run_id,
            prepared=_prepared(
                "prj-901-t0002-r0",
                dimension,
                body_override="a line no page of this document prints",
            ),
            input_tuple_digest=DIGEST_A,
            fresh_pages=_pages(),
            dimension=dimension,
        )
    assert _chunks(connection, "prj-901-t0002-r0") == 0
    assert active_generation(connection, "prj-901-t0002-r0") is None


def test_the_run_stops_at_the_first_failure_and_earlier_documents_stay_durable(
    migrated: psycopg.Connection,
) -> None:
    """FR-042 and FR-073's four dispositions, as behaviour rather than as counts.

    Document 1 commits, document 2 aborts, document 3 is **never begun** — which
    is what makes `not_reached` mean something distinct from `rolled_back`. A
    loop that carried on past the failure would leave nothing for that
    disposition to describe and would keep issuing work against a configuration
    already known to be broken.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    run_id = _run(connection, "c" * 32)
    documents = [
        (_prepared("prj-901-t0011-r0", dimension), DIGEST_A),
        (
            _prepared("prj-901-t0012-r0", dimension, body_override="not printed on any page"),
            DIGEST_A,
        ),
        (_prepared("prj-901-t0013-r0", dimension), DIGEST_A),
    ]
    outcomes = list(
        write_generations(
            connection,
            run_id=run_id,
            documents=documents,
            fresh_pages_by_document={
                prepared.record.document_id: _pages() for prepared, _ in documents
            },
        )
    )

    assert [outcome.document_id for outcome in outcomes] == [
        "prj-901-t0011-r0",
        "prj-901-t0012-r0",
    ], "the generator stopped at the failure and never began the third document"
    assert outcomes[0].committed
    assert not outcomes[1].committed and "FR-010" in (outcomes[1].error or "")

    assert active_generation(connection, "prj-901-t0011-r0") is not None, (
        "documents committed before the abort remain durable and their generations active"
    )
    assert active_generation(connection, "prj-901-t0012-r0") is None
    assert active_generation(connection, "prj-901-t0013-r0") is None


# ---------------------------------------------------------------------------
# Steps 0a-0g — the promotion, and the two roles it separates
# ---------------------------------------------------------------------------


def test_a_resident_predecessor_is_refused_when_promotion_was_not_asked_for(
    migrated: psycopg.Connection,
) -> None:
    """The unattended path. The job holds no privilege to remove anything.

    Refused rather than attempted: an attempt under the application role would
    apply step 0a's mark and then abort on the first delete, which is a pointless
    privilege in front of a failed transaction.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    first = _run(connection, "d" * 32)
    write_document_generation(
        connection,
        run_id=first,
        prepared=_prepared("prj-901-t0021-r0", dimension),
        input_tuple_digest=DIGEST_A,
        fresh_pages=_pages(),
        dimension=dimension,
    )
    second = _run(connection, "e" * 32)
    with pytest.raises(WriterError, match="already has an active generation"):
        write_document_generation(
            connection,
            run_id=second,
            prepared=_prepared("prj-901-t0021-r0", dimension),
            input_tuple_digest=DIGEST_B,
            fresh_pages=_pages(),
            dimension=dimension,
        )
    assert active_generation(connection, "prj-901-t0021-r0") == first


def test_a_promotion_removes_the_predecessor_before_writing_the_successor(
    migrated: psycopg.Connection,
) -> None:
    """Steps 0a-0g then 0h-7, in one transaction ({SAD:ADR-0020}).

    The predecessor's chunks must be gone before the successor's ordinal 0 is
    written — `uq_chunk__document_ordinal` is scoped to the document, so the two
    generations can never be resident together, not even for a statement.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    first = _run(connection, "f" * 32)
    write_document_generation(
        connection,
        run_id=first,
        prepared=_prepared("prj-901-t0031-r0", dimension),
        input_tuple_digest=DIGEST_A,
        fresh_pages=_pages(),
        dimension=dimension,
    )
    second = _run(connection, "1" * 32)
    outcome = write_document_generation(
        connection,
        run_id=second,
        prepared=_prepared("prj-901-t0031-r0", dimension),
        input_tuple_digest=DIGEST_B,
        fresh_pages=_pages(),
        dimension=dimension,
        promote=True,
    )
    assert outcome.committed
    assert outcome.promotion is not None and outcome.promotion.superseded_run_id == first
    assert outcome.promotion.removed["chunks"] == 2

    assert active_generation(connection, "prj-901-t0031-r0") == second
    assert _chunks(connection, "prj-901-t0031-r0") == 2
    assert (
        _count(
            connection,
            "SELECT count(*) FROM ingestion_run_chunk WHERE run_id = %s",
            str(first),
        )
        == 0
    ), "the predecessor's run associations went with its rows"
    assert (
        _count(connection, "SELECT count(*) FROM ingestion_run WHERE run_id = %s", str(first)) == 1
    ), "promotion stops at the generation row; the replaced run's own record survives"


def test_a_promotion_that_aborts_restores_the_prior_generation_intact(
    migrated: psycopg.Connection,
) -> None:
    """The state it is correct to fail into (FR-042, §Write Order step 7).

    The removal rolls back with the write that was replacing it, so the document
    is left holding its previous generation, active — and no deletion privilege
    was needed to get there.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    first = _run(connection, "2" * 32)
    write_document_generation(
        connection,
        run_id=first,
        prepared=_prepared("prj-901-t0041-r0", dimension),
        input_tuple_digest=DIGEST_A,
        fresh_pages=_pages(),
        dimension=dimension,
    )
    second = _run(connection, "3" * 32)
    with pytest.raises(WriterError, match="FR-010"):
        write_document_generation(
            connection,
            run_id=second,
            prepared=_prepared(
                "prj-901-t0041-r0", dimension, body_override="not printed on any page"
            ),
            input_tuple_digest=DIGEST_B,
            fresh_pages=_pages(),
            dimension=dimension,
            promote=True,
        )
    assert active_generation(connection, "prj-901-t0041-r0") == first
    assert _chunks(connection, "prj-901-t0041-r0") == 2


# ---------------------------------------------------------------------------
# FR-056 / T077 — the run-level failure, after the rollback, in a fresh txn
# ---------------------------------------------------------------------------


def test_the_run_level_failure_is_written_after_the_rollback(
    migrated: psycopg.Connection,
) -> None:
    """The record that has to survive the thing it describes.

    Written after `write_generations` has returned — the generator's handler is
    already outside the document's `with conn.transaction()` block — and in a
    transaction of its own, so it is durable even though the document it
    explains is not.
    """
    connection = migrated
    dimension = vector_dimension(connection)
    run_id = _run(connection, "4" * 32)
    documents = [
        (
            _prepared("prj-901-t0051-r0", dimension, body_override="not printed on any page"),
            DIGEST_A,
        )
    ]
    outcomes = list(
        write_generations(
            connection,
            run_id=run_id,
            documents=documents,
            fresh_pages_by_document={"prj-901-t0051-r0": _pages()},
        )
    )
    assert not outcomes[0].committed

    failure = corpus_digest_mismatch(
        document_id="prj-901-t0051-r0",
        path="data/corpus/synthetic/prj-901-t0051-r0.pdf",
        recorded=f"sha256:{'a' * 64}",
        observed=f"sha256:{'b' * 64}",
    )
    record_run_failure(connection, run_id, kind=failure.kind, detail=failure.recorded_detail)

    assert read_run_state(connection, run_id) == "aborted"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_failure_kind, run_failure_detail, finished_at FROM ingestion_run "
            "WHERE run_id = %s",
            (str(run_id),),
        )
        kind, detail, finished_at = cursor.fetchone()  # type: ignore[misc]
    assert kind == "corpus_digest_mismatch"
    assert "prj-901-t0051-r0" in detail, "FR-056: the detail names the document in flight"
    assert finished_at is None, "an aborted run carries no finish"
    assert _chunks(connection, "prj-901-t0051-r0") == 0


def test_the_failure_write_is_refused_from_inside_a_transaction(
    migrated: psycopg.Connection,
) -> None:
    """HINT-002 as a refusal rather than as a comment.

    Inside the block, the `UPDATE` explaining why the document failed would roll
    back with the document — and it is the one record that has to outlive it.
    """
    connection = migrated
    run_id = _run(connection, "5" * 32)
    with connection.transaction(), pytest.raises(RunError, match="HINT-002"):
        record_run_failure(
            connection, run_id, kind="provider_unreachable", detail="inside the block"
        )


def test_a_finished_run_cannot_later_claim_to_have_aborted(
    migrated: psycopg.Connection,
) -> None:
    """`ck_ingestion_run__failed_run_unfinished`, refused with the run named."""
    connection = migrated
    run_id = _run(connection, "6" * 32)
    finish_run(connection, run_id)
    assert read_run_state(connection, run_id) == "complete"
    with pytest.raises(RunError, match="FR-056"):
        record_run_failure(connection, run_id, kind="fixture_missing", detail="too late")


def test_the_first_failure_kind_is_the_one_that_is_kept(
    migrated: psycopg.Connection,
) -> None:
    """The cause, not the consequence. A second write is refused, not applied."""
    connection = migrated
    run_id = _run(connection, "7" * 32)
    record_run_failure(
        connection, run_id, kind="fixture_missing", detail=f"key sha256:{'c' * 64} missed"
    )
    with pytest.raises(RunError, match="FR-056"):
        record_run_failure(
            connection, run_id, kind="provider_unreachable", detail="a later symptom"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_failure_kind FROM ingestion_run WHERE run_id = %s", (str(run_id),)
        )
        assert cursor.fetchone()[0] == "fixture_missing"  # type: ignore[index]


def test_a_kind_outside_the_closed_five_is_refused(migrated: psycopg.Connection) -> None:
    """A per-field outcome recorded here would read as why the run stopped."""
    connection = migrated
    run_id = _run(connection, "8" * 32)
    with pytest.raises(RunError, match="FR-056"):
        record_run_failure(connection, run_id, kind="schema_violation", detail="wrong domain")
