"""FR-063 / SC-026: every stored confidence recomputes from its own signal row.

T060, and T058's floor and weights are asserted here too because they are the
inputs this recomputation reads. The check that makes SC-026 worth anything is
stated as a rule about *where the weights come from*: they are read from the
`ingestion_run` row that produced the score, never from a code constant. A test
that hard-coded `0.15 / 0.10 / 0.25` would pass against a run scored under
different weights — the recomputation would succeed and agree with a policy the
row was never scored under — and that is exactly the silent disagreement the
columns exist to expose. So this file states no weight and no floor of its own,
except in the one place that proves the point: a second run recorded under a
*different* policy, whose scores are shown to recompute correctly under their own
weights and incorrectly under the first run's.

**Why the recomputation needs a table at all.** Two of the three signals exist in
no column anywhere. Nothing records that a printed label matched a known
alternate rather than the canonical form, and nothing records that an invocation
validated only after a repair — `extraction_failure.repair_attempt_count` covers
failures, and a value that repaired successfully produces no failure row. Without
`extracted_value_parse_signal`, "recompute the confidence from its signals"
reduces to reading the confidence and comparing it with itself.

**Isolation.** Every write here is inside `db_session`'s outer transaction and is
rolled back in teardown, so nothing reaches a shared database. The two modules
under test hold a psycopg connection, so they are handed the session's own driver
connection — the same transaction, not a second one, which is what lets a
module's `INSERT` and a test's `SELECT` see each other and still leave no rows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from model.compute.confidence import (
    SIGNAL_DOMAIN,
    DeductionWeights,
    ParseSignals,
    compute_confidence,
)
from model.ingest.runs import (
    DECLARED_POLICY,
    RUN_POLICY_COLUMNS,
    ConfidencePolicy,
    RunError,
    RunIdentity,
    read_confidence_policy,
    record_confidence_policy,
)

RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

DOCUMENT_ID = "prj-900-t0001-r0"

#: Seeded `text`-kind vocabulary terms, one per stored value.
#: `fk_extracted_value__field` is composite over `(field_name, value_kind)`, so a
#: made-up name is refused — and eight are named here so every one of FR-057's
#: eight signal combinations could be stored under a policy that admitted them
#: all, rather than only the three the declared policy stores.
TEXT_TERMS: tuple[str, ...] = (
    "manufacturer",
    "part_number",
    "model_number",
    "product_description",
    "material_category",
    "finish_or_grade",
    "compliance_standard",
    "unit_of_measure",
)

DOCUMENT_ROW: Mapping[str, Any] = {
    "document_id": DOCUMENT_ID,
    "document_type": "submittal",
    "project_id": "PRJ-900",
    "title": "Parse-signal fixture transmittal",
    "source_kind": "SYNTHETIC",
    "source_ref": None,
    "issuing_body": None,
    "retrieval_date": None,
    "generator_id": "model.corpus.generate",
    "generation_seed": 900,
    "generated_at": datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
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
#: this file holds no second opinion about the width of the vector space and no
#: 384-element literal crosses the driver boundary.
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
        :extracted_value_id, :source_chunk_id, :cited_page, :field_name, :value_kind,
        :value_text, NULL, :confidence, :provenance_kind, :source_chunk_count
    )
    """
)

GENERATION_INSERT = text(
    """
    INSERT INTO ingestion_run_document (run_id, document_id, status, input_tuple_digest)
    VALUES (:run_id, :document_id, 'active', :input_tuple_digest)
    """
)

RUN_VALUE_INSERT = text(
    """
    INSERT INTO ingestion_run_extracted_value (extracted_value_id, run_id, document_id)
    VALUES (:extracted_value_id, :run_id, :document_id)
    """
)

SIGNAL_INSERT = text(
    """
    INSERT INTO extracted_value_parse_signal (
        extracted_value_id, run_id, document_id,
        label_match, source_chunk_count, validated_after_repair
    )
    VALUES (
        :extracted_value_id, :run_id, :document_id,
        :label_match, :source_chunk_count, :validated_after_repair
    )
    """
)

#: The join SC-026 is checked over: every stored value, its recorded signals, and
#: the run that produced it. `JOIN` and not `LEFT JOIN` on purpose — a value with
#: no signal row must not silently drop out of the population, and the count
#: assertion beside this query is what catches it.
RECOMPUTATION_QUERY = text(
    """
    SELECT v.extracted_value_id, v.confidence, v.field_name,
           s.run_id, s.label_match, s.source_chunk_count, s.validated_after_repair
    FROM extracted_value v
    JOIN extracted_value_parse_signal s USING (extracted_value_id)
    WHERE s.document_id = :document_id
    ORDER BY v.field_name
    """
)


def identity_for(trace_id: str) -> RunIdentity:
    """A run identity the row's own `CHECK`s accept.

    The agent identity is spelled in FR-038's composite grammar because
    `ck_ingestion_run__agent_id_format` rejects anything else — a presence check
    alone would accept `x`, and half an answer to "who is responsible for this
    citation" is what that grammar exists to refuse.
    """
    return RunIdentity(
        agent_id="principal=automation:e006-parse-signals; build=ci@runner+0123abc",
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
    """The session's own psycopg connection, inside the same transaction.

    `model.ingest.runs` states its statements against a psycopg connection, as
    the ingestion job holds one. Handing it the session's driver connection —
    rather than opening a second one — means the module's `INSERT` and this
    file's `SELECT` see each other's work and the whole lot is still discarded
    in teardown.
    """
    return db_session.connection().connection.driver_connection  # type: ignore[return-value]


@pytest.fixture
def seeded_document(db_session: Session) -> tuple[UUID, UUID]:
    """One document and two chunks on two pages, returned in ordinal order.

    Two pages because the page-split signal is the value's own
    `source_chunk_count`, and a corpus of one chunk cannot exercise it.
    """
    db_session.execute(DOCUMENT_INSERT, dict(DOCUMENT_ROW))
    chunk_ids: list[UUID] = []
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
    return chunk_ids[0], chunk_ids[1]


def open_run(
    connection: psycopg.Connection,
    session: Session,
    *,
    policy: ConfidencePolicy = DECLARED_POLICY,
    trace_id: str = "0123456789abcdef0123456789abcdef",
) -> UUID:
    """Record a run under `policy` and give it a generation for the document."""
    run_id = uuid4()
    record_confidence_policy(
        connection, run_id=run_id, identity=identity_for(trace_id), policy=policy
    )
    session.execute(
        GENERATION_INSERT,
        {
            "run_id": run_id,
            "document_id": DOCUMENT_ID,
            "input_tuple_digest": f"sha256:{'f' * 64}",
        },
    )
    return run_id


def store_value(
    session: Session,
    *,
    run_id: UUID,
    chunk_id: UUID,
    page: int,
    field_name: str,
    signals: ParseSignals,
    confidence: float,
) -> UUID:
    """Write one value, its run association, and its signal row — write order 2, 5, 6."""
    value_id = uuid4()
    session.execute(
        VALUE_INSERT,
        {
            "extracted_value_id": value_id,
            "source_chunk_id": chunk_id,
            "cited_page": page,
            "field_name": field_name,
            "value_kind": "text",
            "value_text": f"printed {field_name}",
            "confidence": confidence,
            "provenance_kind": (
                "multi_chunk" if signals.source_chunk_count > 1 else "single_chunk"
            ),
            "source_chunk_count": signals.source_chunk_count,
        },
    )
    session.execute(
        RUN_VALUE_INSERT,
        {"extracted_value_id": value_id, "run_id": run_id, "document_id": DOCUMENT_ID},
    )
    session.execute(
        SIGNAL_INSERT,
        {
            "extracted_value_id": value_id,
            "run_id": run_id,
            "document_id": DOCUMENT_ID,
            "label_match": signals.label_match,
            "source_chunk_count": signals.source_chunk_count,
            "validated_after_repair": signals.validated_after_repair,
        },
    )
    return value_id


def admissible_signals(policy: ConfidencePolicy) -> tuple[ParseSignals, ...]:
    """The combinations `policy` stores, computed rather than listed.

    FR-057's floor excludes any repaired invocation and any value both
    alternate-labelled and page-split, so under the declared policy three of the
    eight survive. Which three is derived from the policy on the row instead of
    written out, so this helper stays correct for the second run below, which
    declares a different one.
    """
    return tuple(
        signals
        for signals in SIGNAL_DOMAIN
        if policy.admits(compute_confidence(signals, policy.weights))
    )


# ---------------------------------------------------------------------------
# T058 / FR-032, FR-057 — the declared policy, on the row, before any document
# ---------------------------------------------------------------------------


def test_the_run_row_carries_the_declared_floor_and_the_three_weights(
    db_session: Session, raw_connection: psycopg.Connection
) -> None:
    """FR-032, FR-046, SC-022. Read back through the module that wrote it, so
    the four columns and the four values are compared rather than assumed."""
    run_id = uuid4()
    record_confidence_policy(raw_connection, run_id=run_id, identity=identity_for("a" * 32))
    recorded = read_confidence_policy(raw_connection, run_id)
    assert recorded == DECLARED_POLICY

    row = db_session.execute(
        text(f"SELECT {', '.join(RUN_POLICY_COLUMNS)} FROM ingestion_run WHERE run_id = :id"),  # noqa: S608
        {"id": run_id},
    ).one()
    assert tuple(float(value) for value in row) == DECLARED_POLICY.row_values


def test_a_run_with_no_row_refuses_rather_than_defaulting_to_the_constants(
    raw_connection: psycopg.Connection,
) -> None:
    """The whole point of reading the policy off the row.

    Falling back to the declared constants would recompute every stored score
    under today's policy, succeed, and report agreement — which is the silent
    disagreement these columns exist to make visible.
    """
    with pytest.raises(RunError, match="no `ingestion_run` row"):
        read_confidence_policy(raw_connection, uuid4())


def test_a_generation_cannot_exist_under_a_run_that_was_never_recorded(
    db_session: Session,
    seeded_document: tuple[UUID, UUID],
    assert_rejects: RejectionAsserter,
) -> None:
    """FR-032's "before the first document", as a structural fact.

    The four policy columns are NOT NULL, so no run row exists without its
    policy; this foreign key is the other half — no document is written under a
    run row that does not exist. Together they make the reverse order
    unrepresentable rather than merely discouraged.
    """
    del seeded_document
    with assert_rejects(
        db_session, psycopg.errors.ForeignKeyViolation, "fk_ingestion_run_document__run"
    ):
        db_session.execute(
            GENERATION_INSERT,
            {
                "run_id": uuid4(),
                "document_id": DOCUMENT_ID,
                "input_tuple_digest": f"sha256:{'f' * 64}",
            },
        )


def assert_run_rejected(
    session: Session,
    constraint: str,
    *,
    connection: psycopg.Connection,
    policy: ConfidencePolicy,
    trace_id: str,
) -> None:
    """Assert the run row is rejected by `constraint`, naming it exactly.

    `conftest.assert_rejects` cannot be used here: it catches SQLAlchemy's
    `DBAPIError`, and `record_confidence_policy` states its `INSERT` against the
    psycopg connection directly, as the ingestion job does — so the driver's own
    exception is what arrives. The constraint *name* is still what is matched,
    never the message text, for the reason that fixture gives: message text is
    locale- and version-dependent while a constraint name is the schema's
    published contract.

    The statement runs inside a `SAVEPOINT` opened through the session, so the
    failed statement does not leave the outer transaction aborted for whatever
    the test does next.
    """
    savepoint = session.begin_nested()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            record_confidence_policy(
                connection, run_id=uuid4(), identity=identity_for(trace_id), policy=policy
            )
    finally:
        savepoint.rollback()
    assert caught.value.diag.constraint_name == constraint


def test_a_floor_that_fails_to_exclude_a_repair_is_unstorable(
    db_session: Session, raw_connection: psycopg.Connection
) -> None:
    """FR-057's first named exclusion, enforced by the row and not by code.

    The requirement states the floor by what it rejects, so the check is written
    over the columns and hard-codes none of the numbers: any weight-and-floor
    combination failing to reject a repaired invocation is refused on write.
    """
    assert_run_rejected(
        db_session,
        "ck_ingestion_run__floor_excludes_repair",
        connection=raw_connection,
        # The repair check demands `floor > 1.0 - 0.2 = 0.8`, which 0.5 fails;
        # the alt-split check demands `floor > 0.4`, which it passes. Only the
        # constraint under test fires, so a rejection by the other one would be
        # reported as the wrong rule rather than passing this test.
        policy=ConfidencePolicy(
            floor=0.5,
            weights=DeductionWeights(alternate_label=0.3, page_split=0.3, repaired=0.2),
        ),
        trace_id="b" * 32,
    )


def test_a_floor_that_fails_to_exclude_alternate_and_split_is_unstorable(
    db_session: Session, raw_connection: psycopg.Connection
) -> None:
    """FR-057's second named exclusion, on the same footing as the first."""
    assert_run_rejected(
        db_session,
        "ck_ingestion_run__floor_excludes_alt_split",
        connection=raw_connection,
        policy=ConfidencePolicy(
            floor=0.85,
            weights=DeductionWeights(alternate_label=0.05, page_split=0.05, repaired=0.5),
        ),
        trace_id="c" * 32,
    )


# ---------------------------------------------------------------------------
# T060 / FR-063, SC-026 — the recomputation
# ---------------------------------------------------------------------------


def test_every_stored_confidence_recomputes_from_its_own_signal_row(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
) -> None:
    """SC-026: reproduces the stored value **exactly**, at bit equality.

    The weights come from `ingestion_run`, read through
    `read_confidence_policy` against the `run_id` the *signal row* names — not
    from a constant, and not from a run the test chose. The population is every
    stored value of the document, and its size is asserted so a query that
    silently returned nothing cannot pass.
    """
    first, second = seeded_document
    run_id = open_run(raw_connection, db_session)
    admissible = admissible_signals(DECLARED_POLICY)
    assert admissible, "the declared policy stores at least one combination"

    for index, signals in enumerate(admissible):
        chunk_id, page = (second, 2) if signals.page_split else (first, 1)
        store_value(
            db_session,
            run_id=run_id,
            chunk_id=chunk_id,
            page=page,
            field_name=TEXT_TERMS[index],
            signals=signals,
            confidence=compute_confidence(signals, DECLARED_POLICY.weights),
        )

    rows = db_session.execute(RECOMPUTATION_QUERY, {"document_id": DOCUMENT_ID}).all()
    assert len(rows) == len(admissible)

    for row in rows:
        policy = read_confidence_policy(raw_connection, row.run_id)
        recomputed = compute_confidence(
            ParseSignals(
                label_match=row.label_match,
                source_chunk_count=int(row.source_chunk_count),
                validated_after_repair=bool(row.validated_after_repair),
            ),
            policy.weights,
        )
        stored = float(row.confidence)
        assert recomputed == stored
        assert recomputed.hex() == stored.hex()


def test_the_weights_come_from_the_row_and_not_from_a_code_constant(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
) -> None:
    """The defect this check closes, demonstrated rather than described.

    A second run declares a **different** policy and stores a value scored under
    it. The recomputation reads that run's own weights and reproduces the score;
    recomputing the same signals under the *first* run's weights does not. A
    test that hard-coded the declared numbers would have passed here while
    asserting nothing about the run that produced the row.
    """
    first, _ = seeded_document
    other = ConfidencePolicy(
        floor=0.65,
        weights=DeductionWeights(alternate_label=0.3, page_split=0.1, repaired=0.45),
    )
    run_id = open_run(raw_connection, db_session, policy=other, trace_id="d" * 32)

    signals = ParseSignals("alternate", 1, False)
    expected = compute_confidence(signals, other.weights)
    assert other.admits(expected)
    store_value(
        db_session,
        run_id=run_id,
        chunk_id=first,
        page=1,
        field_name="manufacturer",
        signals=signals,
        confidence=expected,
    )

    (row,) = db_session.execute(RECOMPUTATION_QUERY, {"document_id": DOCUMENT_ID}).all()
    from_the_row = read_confidence_policy(raw_connection, row.run_id)
    assert from_the_row == other
    assert from_the_row != DECLARED_POLICY

    recorded_signals = ParseSignals(
        label_match=row.label_match,
        source_chunk_count=int(row.source_chunk_count),
        validated_after_repair=bool(row.validated_after_repair),
    )
    assert compute_confidence(recorded_signals, from_the_row.weights) == float(row.confidence)
    assert compute_confidence(recorded_signals, DECLARED_POLICY.weights) != float(row.confidence)


def test_a_second_signal_row_for_one_value_is_unrepresentable(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
    assert_rejects: RejectionAsserter,
) -> None:
    """`pk_extracted_value_parse_signal` is the value alone.

    So a second, disagreeing signal row is refused rather than merely wrong —
    the same reason `extracted_value_line_item` is keyed this way. Two rows
    would make "recompute the confidence from its signals" ambiguous, and the
    recomputation would pick whichever the join returned first.
    """
    first, _ = seeded_document
    run_id = open_run(raw_connection, db_session)
    signals = ParseSignals("canonical", 1, False)
    value_id = store_value(
        db_session,
        run_id=run_id,
        chunk_id=first,
        page=1,
        field_name="manufacturer",
        signals=signals,
        confidence=compute_confidence(signals, DECLARED_POLICY.weights),
    )
    with assert_rejects(
        db_session, psycopg.errors.UniqueViolation, "pk_extracted_value_parse_signal"
    ):
        db_session.execute(
            SIGNAL_INSERT,
            {
                "extracted_value_id": value_id,
                "run_id": run_id,
                "document_id": DOCUMENT_ID,
                "label_match": "alternate",
                "source_chunk_count": 1,
                "validated_after_repair": False,
            },
        )


def test_the_page_split_signal_cannot_disagree_with_the_values_own_count(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
    assert_rejects: RejectionAsserter,
) -> None:
    """`fk_extracted_value_parse_signal__value_count`, against E003's existing
    `uq_extracted_value__id_source_count`.

    This is why the page-split signal is carried as the count rather than as an
    independent boolean: a boolean here could disagree with the value's own
    provenance, and the disagreement would be invisible — the recomputation
    would read the copy while the citation read the original.
    """
    first, _ = seeded_document
    run_id = open_run(raw_connection, db_session)
    signals = ParseSignals("canonical", 1, False)
    value_id = store_value(
        db_session,
        run_id=run_id,
        chunk_id=first,
        page=1,
        field_name="manufacturer",
        signals=signals,
        confidence=compute_confidence(signals, DECLARED_POLICY.weights),
    )
    db_session.execute(
        text("DELETE FROM extracted_value_parse_signal WHERE extracted_value_id = :id"),
        {"id": value_id},
    )
    with assert_rejects(
        db_session,
        psycopg.errors.ForeignKeyViolation,
        "fk_extracted_value_parse_signal__value_count",
    ):
        db_session.execute(
            SIGNAL_INSERT,
            {
                "extracted_value_id": value_id,
                "run_id": run_id,
                "document_id": DOCUMENT_ID,
                "label_match": "canonical",
                "source_chunk_count": 2,
                "validated_after_repair": False,
            },
        )


def test_a_signal_row_without_run_attribution_is_refused(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
    assert_rejects: RejectionAsserter,
) -> None:
    """`fk_extracted_value_parse_signal__run_output`. A signal row for a value
    with no run attribution would carry inputs nothing could attribute to a
    policy, so the recomputation would have no weights to read."""
    first, _ = seeded_document
    run_id = open_run(raw_connection, db_session)
    value_id = uuid4()
    db_session.execute(
        VALUE_INSERT,
        {
            "extracted_value_id": value_id,
            "source_chunk_id": first,
            "cited_page": 1,
            "field_name": "manufacturer",
            "value_kind": "text",
            "value_text": "printed manufacturer",
            "confidence": 1.0,
            "provenance_kind": "single_chunk",
            "source_chunk_count": 1,
        },
    )
    with assert_rejects(
        db_session,
        psycopg.errors.ForeignKeyViolation,
        "fk_extracted_value_parse_signal__run_output",
    ):
        db_session.execute(
            SIGNAL_INSERT,
            {
                "extracted_value_id": value_id,
                "run_id": run_id,
                "document_id": DOCUMENT_ID,
                "label_match": "canonical",
                "source_chunk_count": 1,
                "validated_after_repair": False,
            },
        )


def test_a_label_match_outside_the_vocabulary_is_refused_by_the_column(
    db_session: Session,
    raw_connection: psycopg.Connection,
    seeded_document: tuple[UUID, UUID],
    assert_rejects: RejectionAsserter,
) -> None:
    """`ck_extracted_value_parse_signal__label_match`. The code refuses a third
    value too, at construction — but the column is what makes it unstorable, and
    the two are asserted separately so neither is trusted to cover the other."""
    first, _ = seeded_document
    run_id = open_run(raw_connection, db_session)
    signals = ParseSignals("canonical", 1, False)
    value_id = store_value(
        db_session,
        run_id=run_id,
        chunk_id=first,
        page=1,
        field_name="manufacturer",
        signals=signals,
        confidence=compute_confidence(signals, DECLARED_POLICY.weights),
    )
    db_session.execute(
        text("DELETE FROM extracted_value_parse_signal WHERE extracted_value_id = :id"),
        {"id": value_id},
    )
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_extracted_value_parse_signal__label_match"
    ):
        db_session.execute(
            SIGNAL_INSERT,
            {
                "extracted_value_id": value_id,
                "run_id": run_id,
                "document_id": DOCUMENT_ID,
                "label_match": "Canonical",
                "source_chunk_count": 1,
                "validated_after_repair": False,
            },
        )
