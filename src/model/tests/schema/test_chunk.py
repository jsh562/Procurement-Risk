"""The `chunk` table and the `document` row it resolves through.

Four groups of claims, each of which fails a different way when it is wrong:

* **Weighting (TR-010).** `search_vector` carries four arms at four weights --
  A heading, B part number, C section, D body. Wrong-arm text is not a visible
  error; it is a ranking that looks plausible and orders results by the wrong
  thing. So the arms are asserted twice over: once structurally, by reading the
  weight label PostgreSQL stored against each lexeme, and once behaviourally, by
  showing a heading hit outranks the same term in a body by exactly the factor
  `ts_rank`'s default weight array declares.
* **Configuration pinning (TR-038).** The stored vector must be a function of
  the row alone, not of whoever wrote it. The only honest test is two sessions
  that genuinely disagree about `default_text_search_config`, and it is only
  worth anything if that disagreement is shown to be *capable* of changing a
  vector -- which is why the one-argument `to_tsvector` control below is not
  decoration. Without it the test would pass against a schema that pinned
  nothing, on a machine where both sessions happened to share a default.
* **Two retrieval arms on one relation (TR-011, TR-012, TR-013).** Exact and
  approximate are the same column on the same table, so switching between them
  is a planner decision and never DDL. Asserted by forcing each plan with
  planner GUCs and reading the plan back, with the relation's index set
  snapshotted either side to show nothing was built or dropped in between.
* **Rejection (TR-014, TR-041, TR-046, TR-074, TR-075, TR-087).** The epic's
  actual deliverable. Every case names the constraint it expects, because
  `pytest.raises(IntegrityError)` is green when a typo'd fixture row trips a
  different rule first and the constraint under test was never reached. See
  `conftest.assert_rejects`.

**Two kinds of rejection deliberately do not use `assert_rejects`,** and both
exceptions are load-bearing rather than convenience:

* A wrong-dimension vector is refused by the `vector` *type* at cast time --
  SQLSTATE 22000, `psycopg.errors.DataException` -- before any constraint is
  consulted. It therefore carries no `constraint_name` diagnostic at all, and
  forcing it through a helper that requires one would only prove the helper's
  error path. Recording the class is the honest assertion: the dimension is
  enforced by the type, which is a stronger claim than a check would be, since
  no row can be written around it.
* A NOT NULL violation -- a missing `page_number`, a missing `license_basis`, a
  missing layer label. On PostgreSQL 16 those report `column_name` and no
  `constraint_name` (catalogued, nameable NOT NULL constraints arrive in 17), so
  the diagnostic asserted in each case is the column.

**Disclosed gaps this file covers.** G-8 -- cross-row agreement on embedding
model identity is *not* enforced, and the test asserts the disclosed behaviour
rather than a guarantee the schema does not make: two vector spaces coexist in
one corpus and are detectable from the rows. G-9 -- the `document_id` format
this epic declares on E002's behalf is enforced by `ck_document__id_format`.

**Isolation.** Everything runs on `db_session`, whose outer transaction is rolled
back in teardown, so no test leaves a row behind. The pinning test needs two
*independent* sessions, which one fixture-provided session cannot supply; it
opens them through `independent_sessions` below, which rolls back on every path.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

#: `conftest.assert_rejects` as seen through its fixture. Requested as a fixture
#: rather than imported, because the import form depends on pytest having put
#: this directory on `sys.path` and stops working the day someone adds an
#: `__init__.py` beside it.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

#: A well-formed `sha256:`+64hex value, in the format E001 froze and
#: `fn_all_sha256_prefixed` enforces element-wise.
VALID_SHA256 = "sha256:" + "a" * 64
ROSTER_SHA256 = "sha256:" + "b" * 64

#: A `REAL` document: retrieval provenance present, generation provenance absent.
#: Both halves matter -- v1.2.0's Data Provenance rule is stated per layer, and
#: `0003` enforces it in both directions, so this row is the baseline every
#: single-field perturbation below is measured against.
REAL_DOCUMENT: Mapping[str, Any] = {
    "document_id": "example-standards-piping-2022",
    "document_type": "specification",
    "project_id": "PRJ-001",
    "title": "Process Piping Specification",
    "source_kind": "REAL",
    "source_ref": "https://standards.example.gov/piping/2022",
    "issuing_body": "Example Standards Body",
    "retrieval_date": date(2026, 1, 15),
    "generator_id": None,
    "generation_seed": None,
    "generated_at": None,
    "fixture_hashes": None,
    "roster_hash": None,
    "license_basis": "public-domain",
}

#: A `SYNTHETIC` document: the exact mirror image. A generated document carries
#: no issuing body, because a fabricated one is indistinguishable downstream
#: from a verified one -- the failure Principle I exists to prevent.
SYNTHETIC_DOCUMENT: Mapping[str, Any] = {
    "document_id": "generated-submittal-log-001",
    "document_type": "submittal",
    "project_id": "PRJ-001",
    "title": "Generated Submittal Log",
    "source_kind": "SYNTHETIC",
    "source_ref": None,
    "issuing_body": None,
    "retrieval_date": None,
    "generator_id": "corpus-generator",
    "generation_seed": "seed-000042",
    "generated_at": date(2026, 2, 1),
    "fixture_hashes": [VALID_SHA256],
    "roster_hash": ROSTER_SHA256,
    "license_basis": "CC0-1.0",
}

DOCUMENT_BASELINES: Mapping[str, Mapping[str, Any]] = {
    "REAL": REAL_DOCUMENT,
    "SYNTHETIC": SYNTHETIC_DOCUMENT,
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

#: Every column bound by name, so nothing is ever concatenated into this
#: statement (Ruff S608) and a perturbed field cannot silently fall out of the
#: column list.
#:
#: The embedding is built *server-side* from one scalar. Two reasons. A `vector`
#: literal crossing the driver boundary would be testing psycopg's adaptation of
#: a 384-element list as much as the schema, and the dimension is read from
#: `schema_constants.vector_dimension` -- the published copy every consumer reads
#: under TR-047 -- rather than restated here, so this file holds no second
#: opinion about the size of the vector space. `1.0` on the first axis with
#: `:embedding_tilt` on the rest gives a family of vectors whose *directions*
#: differ, which is what cosine distance is sensitive to; a uniformly filled
#: vector would be cosine-identical to every other uniformly filled one
#: regardless of magnitude, and every ranking assertion below would be vacuous.
CHUNK_INSERT = text(
    """
    INSERT INTO chunk (
        chunk_id, document_id, document_type, project_id,
        page_number, ordinal, spec_section, heading, part_numbers, body_text,
        embedding, embedding_model_id, embedding_model_revision
    )
    VALUES (
        :chunk_id, :document_id, :document_type, :project_id,
        :page_number, :ordinal, :spec_section, :heading, :part_numbers, :body_text,
        (
            SELECT array_agg(
                (CASE WHEN axis.component = 1 THEN 1.0 ELSE :embedding_tilt END)::real
                ORDER BY axis.component
            )
            FROM generate_series(
                1, (SELECT vector_dimension FROM schema_constants)
            ) AS axis(component)
        )::vector,
        :embedding_model_id, :embedding_model_revision
    )
    """
)

#: The wrong-dimension case, and the only statement here that writes a `vector`
#: literal. Three components against a `vector(384)` column: refused by the type
#: at cast time, so the row never reaches a constraint.
CHUNK_INSERT_THREE_DIMENSIONAL_EMBEDDING = text(
    """
    INSERT INTO chunk (
        chunk_id, document_id, document_type, project_id,
        page_number, ordinal, body_text,
        embedding, embedding_model_id, embedding_model_revision
    )
    VALUES (
        :chunk_id, :document_id, :document_type, :project_id,
        1, 0, 'Three components against a 384-dimension column.',
        '[1,2,3]'::vector, :embedding_model_id, :embedding_model_revision
    )
    """
)

DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_REVISION = "e4ce9877abf3edfe10b0d82785e83bdcb973e22e"


def document_row(layer: str, **overrides: Any) -> dict[str, Any]:
    """A valid `document` row for `layer`, with `overrides` applied.

    Perturbing exactly one field of an otherwise-valid row is what makes a
    rejection attributable. Omit two required fields at once and PostgreSQL
    reports whichever check it evaluated first, so the test would name one
    constraint and be satisfied by another.

    The parameter is `layer` and not `source_kind` so that `source_kind` itself
    stays perturbable through `overrides` -- the discriminator is one of the two
    fields OBJ2 VC7 requires on *every* layer, and naming the parameter after the
    column would make `document_row("REAL", source_kind=None)` a duplicate-argument
    `TypeError` instead of the row that test needs. Callers pass the layer
    positionally, which is also what keeps `layer` meaning "the baseline this row
    is built from" in the cases where the label written is deliberately something
    else.
    """
    return {**DOCUMENT_BASELINES[layer], **overrides}


def chunk_row(document: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """A valid `chunk` row referencing `document`, with `overrides` applied.

    The three foreign-key columns are copied from the document rather than
    restated, so a test that means to exercise a check constraint cannot trip
    `fk_chunk__document` by accident on the way there.
    """
    row: dict[str, Any] = {
        "chunk_id": uuid4(),
        "document_id": document["document_id"],
        "document_type": document["document_type"],
        "project_id": document["project_id"],
        "page_number": 1,
        "ordinal": 0,
        "spec_section": "23 05 00",
        "heading": None,
        "part_numbers": None,
        "body_text": "Piping shall be supported at intervals not exceeding three metres.",
        "embedding_tilt": 0.25,
        "embedding_model_id": DEFAULT_MODEL_ID,
        "embedding_model_revision": DEFAULT_MODEL_REVISION,
    }
    row.update(overrides)
    return row


def insert_document(session: Session, row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Insert `row` into `document` and return it, for chaining into `chunk_row`."""
    session.execute(DOCUMENT_INSERT, dict(row))
    return row


def insert_chunk(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `chunk` and return its `chunk_id`."""
    session.execute(CHUNK_INSERT, dict(row))
    return row["chunk_id"]


@pytest.fixture
def real_document(db_session: Session) -> Mapping[str, Any]:
    """A committed-in-savepoint `REAL` document for chunks to reference."""
    return insert_document(db_session, document_row("REAL"))


# --------------------------------------------------------------------------- #
# Independent sessions (TR-038 needs two, disagreeing)
# --------------------------------------------------------------------------- #


@contextmanager
def independent_sessions(engine: Engine, count: int) -> Iterator[tuple[Session, ...]]:
    """`count` sessions on `count` separate connections, all rolled back on exit.

    `db_session` cannot serve the pinning test: one session has one value of
    `default_text_search_config`, and the claim under test is about two sessions
    that disagree. Changing the GUC twice on one connection would test the same
    server behaviour, but it would not be the sentence TR-038 makes, and a reader
    checking this file against the requirement should not have to accept a
    substitution.

    Each session keeps the isolation contract `conftest.db_session` establishes:
    an outer transaction that is never committed and is rolled back
    unconditionally, with the `Session` joined to it by savepoint. `ExitStack`
    unwinds in reverse construction order, so a failure while opening the second
    connection still closes the first -- the plain list comprehension that reads
    more naturally here would leak it.
    """
    with ExitStack() as stack:
        sessions: list[Session] = []
        for _ in range(count):
            connection = stack.enter_context(engine.connect())
            transaction = connection.begin()
            # Bound late and guarded: SQLAlchemy deactivates a transaction whose
            # statement failed, and rolling one back twice raises an exception
            # that would replace whatever assertion error we are reporting.
            stack.callback(lambda t=transaction: t.rollback() if t.is_active else None)
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            stack.callback(session.close)
            sessions.append(session)
        yield tuple(sessions)


#: Complete statements, keyed by the configuration they select. A dict of whole
#: statements rather than one f-string: Ruff S608 exists because SQL assembled
#: from values is how injection happens, and `SET` admits no bind parameter, so
#: the only safe form is a literal that was never assembled.
#:
#: Plain `SET`, not `SET LOCAL`: the GUC is a *session* setting and that is the
#: level TR-038 speaks about. It is still discarded, because the transaction that
#: issued it is rolled back.
SET_TEXT_SEARCH_CONFIG = {
    "pg_catalog.english": text("SET default_text_search_config = 'pg_catalog.english'"),
    "pg_catalog.simple": text("SET default_text_search_config = 'pg_catalog.simple'"),
}

CURRENT_TEXT_SEARCH_CONFIG = text("SELECT current_setting('default_text_search_config')")

# --------------------------------------------------------------------------- #
# T017 -- weighting (TR-010) and configuration pinning (TR-038)
# --------------------------------------------------------------------------- #

#: One marker token per weighted arm, each appearing in exactly one column.
#: Realism is deliberately traded away here: the point is that every lexeme in
#: the stored vector has exactly one possible origin, so the weight label
#: PostgreSQL reports against it is attributable to a single arm and to nothing
#: else. All four are words the `english` dictionary stems to themselves, so the
#: assertion can name the lexeme directly instead of re-deriving a stem -- and
#: `to_tsvector` would otherwise return `actuat` for `actuator`, which is exactly
#: the kind of quiet mismatch that makes a lexeme assertion look broken.
ARM_MARKERS: Mapping[str, str] = {
    "heading": "gasket",
    "part_numbers": "louver",
    "spec_section": "damper",
    "body_text": "switchgear",
}

#: The weight each arm is set to by `0004`. A is the strongest.
ARM_WEIGHTS: Mapping[str, str] = {
    "heading": "A",
    "part_numbers": "B",
    "spec_section": "C",
    "body_text": "D",
}

#: `unnest(tsvector)` decomposes the stored vector into lexeme, positions, and
#: weights -- so the arm assignment is read off the server's own representation
#: rather than inferred from a rank comparison. `weights` is a `text[]` of the
#: distinct labels a lexeme carries.
STORED_VECTOR_WEIGHTS = text(
    """
    SELECT lexeme, weights
    FROM chunk, unnest(chunk.search_vector)
    WHERE chunk.chunk_id = :chunk_id
    ORDER BY lexeme
    """
)

#: `ts_rank`'s default weight array is `{D, C, B, A}` = `{0.1, 0.2, 0.4, 1.0}`,
#: which makes an A hit worth exactly ten D hits at equal occurrence counts.
#: Asserted as the ratio rather than as two magnitudes, because the magnitudes
#: are `ts_rank`'s business and the ratio is the schema's.
HEADING_TO_BODY_WEIGHT_RATIO = 10.0

RANK_BY_TERM = text(
    """
    SELECT ordinal,
           ts_rank(search_vector, to_tsquery('pg_catalog.english', :term)) AS rank
    FROM chunk
    WHERE document_id = :document_id
    ORDER BY rank DESC, ordinal
    """
)

STORED_VECTOR_TEXT = text("SELECT search_vector::text FROM chunk WHERE chunk_id = :chunk_id")

#: The one-argument form, which reads `default_text_search_config` from the
#: session. This is the control: if it does *not* differ between the two
#: sessions, they never disagreed and the pinning assertion proves nothing.
ONE_ARGUMENT_TSVECTOR = text("SELECT to_tsvector(:body_text)::text")

#: Body text for the pinning test. Deliberately full of stop words and
#: inflections, because that is where `english` and `simple` diverge most
#: visibly: `english` drops `the`/`are`/`to` and stems `flanges` to `flang`,
#: `simple` keeps every token as a lowercased word.
PINNING_BODY_TEXT = "The flanges are welded to the running pipes and are then inspected"
PINNING_HEADING = "Welded Flange Inspection"

#: The two configurations to compare, each with the manifest key its session
#: writes under. Spelled out rather than derived from the configuration name: a
#: key built by slicing `'pg_catalog.simple'` carries a `.`, which
#: `ck_document__id_format` rejects -- correctly, and for a reason that has
#: nothing to do with TR-038.
PINNING_PROBES: Mapping[str, str] = {
    "pg_catalog.english": "pinning-probe-english",
    "pg_catalog.simple": "pinning-probe-simple",
}


def test_heading_arm_is_weight_a_and_body_arm_is_weight_d(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-010: each of the four arms carries the weight `0004` assigns it.

    Read structurally, off `unnest(search_vector)`. A rank comparison alone
    could not distinguish "heading is weight A" from "heading happens to be
    scored higher"; the weight label is the schema's actual claim.
    """
    chunk_id = insert_chunk(
        db_session,
        chunk_row(
            real_document,
            heading=ARM_MARKERS["heading"].title(),
            part_numbers=ARM_MARKERS["part_numbers"].title(),
            spec_section=ARM_MARKERS["spec_section"].title(),
            body_text=ARM_MARKERS["body_text"].title(),
        ),
    )

    stored = {
        lexeme: list(weights)
        for lexeme, weights in db_session.execute(
            STORED_VECTOR_WEIGHTS, {"chunk_id": chunk_id}
        ).all()
    }

    expected = {ARM_MARKERS[arm]: [weight] for arm, weight in ARM_WEIGHTS.items()}
    assert stored == expected, (
        "every arm of search_vector must carry the weight 0004 sets it to "
        f"(A heading, B part number, C section, D body); got {stored}"
    )


def test_heading_match_outranks_body_match_by_the_declared_weight_ratio(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-010: the same term ranks ten times higher in a heading than in a body.

    Two chunks of one document, one carrying the search term in its heading and
    the other in its body, each exactly once and nowhere else. The heading chunk
    must rank first -- that is the retrieval-visible consequence -- and the ratio
    must be exactly the `A:D` ratio of `ts_rank`'s default weight array, which is
    what pins the *arms* rather than merely their order.
    """
    term = "flange"
    heading_ordinal, body_ordinal = 0, 1

    insert_chunk(
        db_session,
        chunk_row(
            real_document,
            ordinal=heading_ordinal,
            page_number=11,
            heading="Flange Installation",
            part_numbers="PN-4471",
            body_text="Bolts shall be tightened in a crossing sequence and torque recorded.",
        ),
    )
    insert_chunk(
        db_session,
        chunk_row(
            real_document,
            ordinal=body_ordinal,
            page_number=12,
            heading="Bolt Torque Records",
            part_numbers="PN-4472",
            body_text="Each flange shall be installed by the erector.",
        ),
    )

    ranked = db_session.execute(
        RANK_BY_TERM, {"term": term, "document_id": real_document["document_id"]}
    ).all()
    by_ordinal = {ordinal: rank for ordinal, rank in ranked}

    assert [ordinal for ordinal, _ in ranked] == [heading_ordinal, body_ordinal], (
        f"the chunk matching {term!r} in its heading must outrank the chunk matching it "
        f"in its body; ranks were {by_ordinal}"
    )
    assert by_ordinal[heading_ordinal] == pytest.approx(
        HEADING_TO_BODY_WEIGHT_RATIO * by_ordinal[body_ordinal]
    ), (
        "a weight-A hit must score exactly ten times a weight-D hit under ts_rank's "
        f"default {{0.1, 0.2, 0.4, 1.0}} array; ranks were {by_ordinal}"
    )


def test_stored_vector_is_identical_across_sessions_with_different_defaults(
    engine: Engine,
) -> None:
    """TR-038: the pinned `regconfig` beats the session default, in both directions.

    Two sessions, one on `english` and one on `simple`, write the same heading
    and body. The claim is that their `search_vector` values are byte-identical,
    because `0004` names the configuration in the generated expression and the
    server resolved it to a fixed `regconfig` at DDL time.

    The one-argument control is not optional. It shows the two sessions really do
    disagree and that the disagreement really can change a vector, which is what
    makes the identity above evidence of pinning rather than evidence of a
    machine where both sessions happened to share a default. Comparing the
    canonical `tsvector` text is comparing the value: that output is lossless
    over lexemes, positions, and weight labels, which is the whole of a
    `tsvector`.
    """
    configs = tuple(PINNING_PROBES)
    stored: dict[str, str] = {}
    one_argument: dict[str, str] = {}
    observed_gucs: dict[str, str] = {}

    with independent_sessions(engine, len(configs)) as sessions:
        for config, session in zip(configs, sessions, strict=True):
            session.execute(SET_TEXT_SEARCH_CONFIG[config])
            observed_gucs[config] = session.execute(CURRENT_TEXT_SEARCH_CONFIG).scalar_one()

            # Each session needs its own document: uncommitted rows are invisible
            # across connections, so a shared referent is not available and a
            # shared key would be a primary-key collision waiting on commit.
            # Only the *indexed text* has to match, and it does.
            document = insert_document(
                session, document_row("REAL", document_id=PINNING_PROBES[config])
            )
            chunk_id = insert_chunk(
                session,
                chunk_row(document, heading=PINNING_HEADING, body_text=PINNING_BODY_TEXT),
            )
            stored[config] = session.execute(
                STORED_VECTOR_TEXT, {"chunk_id": chunk_id}
            ).scalar_one()
            one_argument[config] = session.execute(
                ONE_ARGUMENT_TSVECTOR, {"body_text": PINNING_BODY_TEXT}
            ).scalar_one()

    assert observed_gucs == dict(zip(configs, configs, strict=True)), (
        "each session must actually hold the configuration it was given, or the "
        f"comparison below has nothing to prove; got {observed_gucs}"
    )
    assert one_argument[configs[0]] != one_argument[configs[1]], (
        "the one-argument to_tsvector must differ between these sessions -- otherwise "
        "the session default is not capable of changing a vector here and the pinning "
        f"assertion is vacuous; both produced {one_argument[configs[0]]!r}"
    )
    assert stored[configs[0]] == stored[configs[1]], (
        "search_vector must be a function of the row alone (TR-038): a generated column "
        "pinned to a named regconfig cannot vary with default_text_search_config. Got "
        f"{stored[configs[0]]!r} under {configs[0]} and {stored[configs[1]]!r} under "
        f"{configs[1]}"
    )


# --------------------------------------------------------------------------- #
# T018 -- two arms on one relation (TR-013), model identity (TR-012), gap G-8
# --------------------------------------------------------------------------- #

#: Forced plans. Every GUC is set explicitly in *both* arms rather than only the
#: one being flipped, so neither arm inherits a setting from the other and the
#: order the arms run in cannot matter. `SET LOCAL` scopes them to the test's own
#: transaction, which `db_session` rolls back.
#:
#: `enable_seqscan = off` does not forbid a sequential scan, it prices one
#: absurdly -- which is why the plan is *read back* below rather than assumed.
EXACT_ARM_PLANNER_GUCS = (
    text("SET LOCAL enable_seqscan = on"),
    text("SET LOCAL enable_indexscan = off"),
    text("SET LOCAL enable_bitmapscan = off"),
)
APPROXIMATE_ARM_PLANNER_GUCS = (
    text("SET LOCAL enable_seqscan = off"),
    text("SET LOCAL enable_indexscan = on"),
    text("SET LOCAL enable_bitmapscan = on"),
)

#: The nearest-neighbour query both arms run, unchanged between them. `<=>` is
#: cosine distance, matching `vector_cosine_ops` on the HNSW index -- an index
#: built for another operator class is not merely slower here, it is never
#: considered, and the symptom is a silent full scan rather than an error.
#:
#: **No `WHERE` clause, deliberately.** A `document_id` predicate hands the
#: planner `ix_chunk__document_page`, which satisfies the filter and leaves the
#: distance ordering to a `Sort` -- so the approximate arm quietly stops being
#: approximate while still returning the right rows. That is precisely the
#: silent failure this test exists to catch, and it cannot be caught by a query
#: that invites it. The rows are scoped instead by the rollback fixture: nothing
#: is committed to `chunk`, so the only rows visible here are this test's, and
#: the subset assertion below fails loudly if that ever stops being true.
NEAREST_NEIGHBOUR_PROBE_TILT = 0.02
NEAREST_NEIGHBOUR_LIMIT = 3

NEAREST_NEIGHBOURS = text(
    """
    SELECT chunk_id
    FROM chunk
    ORDER BY embedding <=> (
        SELECT array_agg(
            (CASE WHEN axis.component = 1 THEN 1.0 ELSE :probe_tilt END)::real
            ORDER BY axis.component
        )
        FROM generate_series(
            1, (SELECT vector_dimension FROM schema_constants)
        ) AS axis(component)
    )::vector
    LIMIT :neighbour_limit
    """
)

EXPLAIN_NEAREST_NEIGHBOURS = text(
    """
    EXPLAIN (FORMAT TEXT)
    SELECT chunk_id
    FROM chunk
    ORDER BY embedding <=> (
        SELECT array_agg(
            (CASE WHEN axis.component = 1 THEN 1.0 ELSE :probe_tilt END)::real
            ORDER BY axis.component
        )
        FROM generate_series(
            1, (SELECT vector_dimension FROM schema_constants)
        ) AS axis(component)
    )::vector
    LIMIT :neighbour_limit
    """
)

#: The relation's index set. Snapshotted either side of the two arms, so "no DDL
#: between them" is an assertion about the catalog rather than an assertion about
#: how the test body reads.
CHUNK_INDEXES = text(
    """
    SELECT indexrelid::regclass::text
    FROM pg_index
    WHERE indrelid = 'chunk'::regclass
    ORDER BY 1
    """
)

EXACT_SCAN_NODE = "Seq Scan on chunk"
HNSW_SCAN_NODE = "Index Scan using ix_chunk__embedding_hnsw on chunk"

#: A `vector(384)` literal renders as 384 floats inside a plan's `Sort Key` or
#: `Order By` line. Collapsing it keeps a failure message readable without
#: touching the substrings any assertion actually matches on.
VECTOR_LITERAL_IN_PLAN = re.compile(r"'\[[^]]*\]'::vector")

CORPUS_VECTOR_SPACES = text(
    """
    SELECT count(*) AS chunk_count,
           count(DISTINCT (embedding_model_id, embedding_model_revision)) AS space_count
    FROM chunk
    WHERE document_id = :document_id
    """
)

MODEL_IDENTITY_OF = text(
    """
    SELECT embedding_model_id, embedding_model_revision
    FROM chunk
    WHERE chunk_id = :chunk_id
    """
)


def _plan_of(session: Session, parameters: Mapping[str, Any]) -> str:
    """The `EXPLAIN` output for the nearest-neighbour query, as one string."""
    rows = session.execute(EXPLAIN_NEAREST_NEIGHBOURS, dict(parameters)).scalars().all()
    return "\n".join(rows)


def _readable(plan: str) -> str:
    """`plan` with vector literals collapsed, for use in a failure message."""
    return VECTOR_LITERAL_IN_PLAN.sub("'[...]'::vector", plan)


def _graded_corpus(session: Session, document: Mapping[str, Any], count: int) -> tuple[UUID, ...]:
    """Insert `count` chunks whose embeddings fan out in direction, not magnitude."""
    return tuple(
        insert_chunk(
            session,
            chunk_row(
                document,
                ordinal=ordinal,
                page_number=ordinal + 1,
                embedding_tilt=round(ordinal * 0.1, 3),
            ),
        )
        for ordinal in range(count)
    )


def test_exact_and_approximate_arms_run_against_one_relation_with_no_ddl_between(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-013: both retrieval arms are the same column of the same table.

    The two arms are forced with planner GUCs and the plan is read back, because
    the interesting failure is not "the query errored" -- it is "the approximate
    arm silently fell back to a scan", which returns the right answer and proves
    nothing about the index. So each arm asserts on the node it wants *and* on
    the absence of the other's.

    "No DDL between" is asserted against `pg_index` either side rather than left
    to the reader's inspection of the test body: the claim TR-013 makes is that
    switching arms needs no second relation and no index build, and a catalog
    snapshot is the only form of that claim a future edit cannot quietly break.
    """
    inserted = _graded_corpus(db_session, real_document, count=8)
    parameters = {
        "probe_tilt": NEAREST_NEIGHBOUR_PROBE_TILT,
        "neighbour_limit": NEAREST_NEIGHBOUR_LIMIT,
    }

    indexes_before = db_session.execute(CHUNK_INDEXES).scalars().all()

    for guc in EXACT_ARM_PLANNER_GUCS:
        db_session.execute(guc)
    exact_plan = _plan_of(db_session, parameters)
    exact_result = db_session.execute(NEAREST_NEIGHBOURS, parameters).scalars().all()

    for guc in APPROXIMATE_ARM_PLANNER_GUCS:
        db_session.execute(guc)
    approximate_plan = _plan_of(db_session, parameters)
    approximate_result = db_session.execute(NEAREST_NEIGHBOURS, parameters).scalars().all()

    indexes_after = db_session.execute(CHUNK_INDEXES).scalars().all()

    assert EXACT_SCAN_NODE in exact_plan, (
        "with index scans disabled the exact arm must scan `chunk` itself; plan was\n"
        f"{_readable(exact_plan)}"
    )
    assert "ix_chunk__embedding_hnsw" not in exact_plan, (
        "the exact arm must not reach the HNSW index, or it is not the exact arm; "
        f"plan was\n{_readable(exact_plan)}"
    )
    assert HNSW_SCAN_NODE in approximate_plan, (
        "with sequential scans disabled the approximate arm must use "
        f"ix_chunk__embedding_hnsw; plan was\n{_readable(approximate_plan)}"
    )
    assert EXACT_SCAN_NODE not in approximate_plan, (
        "the approximate arm silently fell back to a sequential scan of `chunk`, which "
        "returns the right rows while exercising no index; plan was\n"
        f"{_readable(approximate_plan)}"
    )
    assert indexes_before == indexes_after, (
        "no DDL may run between the two arms (TR-013): they are one relation and one "
        f"column. Indexes were {indexes_before} before and {indexes_after} after"
    )
    assert "ix_chunk__embedding_hnsw" in indexes_before, (
        f"the HNSW index must exist for the approximate arm to have one; got {indexes_before}"
    )
    assert len(exact_result) == NEAREST_NEIGHBOUR_LIMIT
    assert set(exact_result) <= set(inserted), (
        "the unfiltered query must see only this test's rows -- `chunk` holds nothing "
        "committed, so anything else here means the rollback fixture is not isolating "
        f"as documented; got {exact_result} against {inserted}"
    )
    assert approximate_result == exact_result, (
        "at this scale the HNSW graph is visited exhaustively (default hnsw.ef_search of "
        "40 exceeds the row count), so the approximate arm must agree with the exact one; "
        f"exact {exact_result} vs approximate {approximate_result}"
    )


def test_embedding_model_identity_is_readable_off_the_row(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-012: model identity and revision are recorded per chunk and read back.

    Recorded *per row* rather than per corpus, which is what makes a mixed
    vector space detectable at all -- see gap G-8 below for what the schema
    deliberately does not do with that information.
    """
    chunk_id = insert_chunk(
        db_session,
        chunk_row(
            real_document,
            embedding_model_id="sentence-transformers/all-MiniLM-L12-v2",
            embedding_model_revision="a05860a77cef7b37e0048a7864658139bc18a854",
        ),
    )

    identity = db_session.execute(MODEL_IDENTITY_OF, {"chunk_id": chunk_id}).one()

    assert identity == (
        "sentence-transformers/all-MiniLM-L12-v2",
        "a05860a77cef7b37e0048a7864658139bc18a854",
    )


@pytest.mark.parametrize(
    ("column", "blank"),
    [
        ("embedding_model_id", ""),
        ("embedding_model_id", "\t"),
        ("embedding_model_revision", ""),
        ("embedding_model_revision", "   "),
    ],
)
def test_blank_embedding_model_identity_is_rejected(
    db_session: Session,
    real_document: Mapping[str, Any],
    assert_rejects: RejectionAsserter,
    column: str,
    blank: str,
) -> None:
    """TR-012: a model identity of whitespace names no model, so presence is checked.

    Not the same claim as `NOT NULL`. A one-tab identity satisfies `NOT NULL`
    while leaving the vector space exactly as unidentifiable as absence would,
    which is why `0004` carries a presence check on each column.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, f"ck_chunk__{column}_present"):
        insert_chunk(db_session, chunk_row(real_document, **{column: blank}))


def test_gap_g8_two_vector_spaces_coexist_and_are_detectable_from_the_rows(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """Gap G-8, asserted as disclosed: detectable, *not* prevented.

    data-model.md records cross-row agreement on embedding model identity as
    enforcement this schema does not carry -- a `CHECK` cannot see sibling rows,
    and a deferred `CHECK` does not exist. The disclosed consequence is that at
    runtime a corpus can hold two vector spaces, with the mismatch left visible
    in the rows so retrieval can refuse to serve on it (E008) rather than return
    distances computed across both.

    So this test asserts the disclosure and nothing stronger. Both rows insert;
    no constraint intervenes; and a one-line query over the corpus finds the
    disagreement. Asserting a guarantee here would be asserting something the
    schema does not claim, and it would fail the moment someone read the gap
    record -- which is the outcome G-8 was written to avoid.
    """
    first = insert_chunk(
        db_session,
        chunk_row(
            real_document,
            ordinal=0,
            page_number=1,
            embedding_model_id="sentence-transformers/all-MiniLM-L6-v2",
            embedding_model_revision="e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
        ),
    )
    second = insert_chunk(
        db_session,
        chunk_row(
            real_document,
            ordinal=1,
            page_number=2,
            embedding_model_id="BAAI/bge-small-en-v1.5",
            embedding_model_revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        ),
    )

    assert first != second
    counts = db_session.execute(
        CORPUS_VECTOR_SPACES, {"document_id": real_document["document_id"]}
    ).one()

    assert counts.chunk_count == 2, (
        "G-8 is disclosed as unenforced: rows carrying different embedding model "
        "identities must both be accepted, because nothing in the schema stops them"
    )
    assert counts.space_count == 2, (
        "the mismatch must be detectable from the rows -- that is the whole of what "
        "G-8 promises, and what lets E008 refuse to serve rather than mix spaces"
    )


def test_wrong_dimension_vector_is_rejected_by_the_type_not_a_constraint(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-011: `vector(384)` refuses a three-component literal at cast time.

    Asserted on the psycopg error class, not through `assert_rejects`. This
    rejection comes from the *type*, before any constraint is consulted, so it
    carries no `constraint_name` diagnostic -- `assert_rejects` requires one and
    would fail here for a reason that has nothing to do with the schema. The
    class is the assertion that matters: `DataException` is SQLSTATE 22000 and
    nothing else, and enforcement by the type is a stronger property than a
    check would be, because there is no row that can be written around it.
    """
    row = {
        "chunk_id": uuid4(),
        "document_id": real_document["document_id"],
        "document_type": real_document["document_type"],
        "project_id": real_document["project_id"],
        "embedding_model_id": DEFAULT_MODEL_ID,
        "embedding_model_revision": DEFAULT_MODEL_REVISION,
    }

    savepoint = db_session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed below
        db_session.execute(CHUNK_INSERT_THREE_DIMENSIONAL_EMBEDDING, row)
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.DataException), (
        "a vector of the wrong dimension must be refused by the column type "
        f"(SQLSTATE 22000), not by a constraint; got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert getattr(original.diag, "constraint_name", None) is None, (
        "this rejection carries no constraint_name, which is why it cannot go through "
        "assert_rejects; if it now carries one, the enforcement mechanism changed and "
        "the docstring above is wrong"
    )


# --------------------------------------------------------------------------- #
# T019 -- chunk rejections (TR-014)
# --------------------------------------------------------------------------- #

DELETE_DOCUMENT = text("DELETE FROM document WHERE document_id = :document_id")


#: TR-014's check-constraint rejections, keyed by the case each one names.
CHUNK_CHECK_REJECTIONS: Mapping[str, tuple[Mapping[str, Any], str]] = {
    "empty body": ({"body_text": ""}, "ck_chunk__body_text_present"),
    "spaces only": ({"body_text": "   "}, "ck_chunk__body_text_present"),
    "tab only": ({"body_text": "\t"}, "ck_chunk__body_text_present"),
    "newline only": ({"body_text": "\n"}, "ck_chunk__body_text_present"),
    "vertical tab only": ({"body_text": "\v"}, "ck_chunk__body_text_present"),
    "unpadded project id": ({"project_id": "PRJ-1"}, "ck_chunk__project_id_format"),
    "lowercase prefix": ({"project_id": "prj-001"}, "ck_chunk__project_id_format"),
    "trailing text": ({"project_id": "PRJ-001X"}, "ck_chunk__project_id_format"),
    "empty project id": ({"project_id": ""}, "ck_chunk__project_id_format"),
}


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    list(CHUNK_CHECK_REJECTIONS.values()),
    ids=list(CHUNK_CHECK_REJECTIONS),
)
def test_chunk_check_rejections(
    db_session: Session,
    real_document: Mapping[str, Any],
    assert_rejects: RejectionAsserter,
    overrides: Mapping[str, Any],
    constraint: str,
) -> None:
    """TR-014: no searchable text, and a project identifier off the frozen format.

    The whitespace cases are the ones worth having. `btrim` with one argument
    strips *spaces only*, so a body of a single tab or vertical tab would satisfy
    a naively written check while producing an empty `search_vector` -- a chunk
    that is unfindable rather than merely badly ranked, which is exactly the row
    TR-014 refuses. The vertical-tab case in particular is what catches
    `E'\\v'`, which PostgreSQL reads as the letter `v` and not as a control
    character.

    A malformed `project_id` is caught by the check rather than by
    `fk_chunk__document`, because PostgreSQL evaluates check constraints before
    firing referential-integrity triggers. Both would reject the row; only one
    of them is TR-014's frozen-format claim, which is why the constraint name is
    part of the assertion.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, constraint):
        insert_chunk(db_session, chunk_row(real_document, **overrides))


def test_missing_page_number_is_rejected(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-014, TR-058: a chunk with no page number has no citable location.

    Not routed through `assert_rejects`, and the reason is a property of
    PostgreSQL 16 rather than a preference: a NOT NULL violation reports
    `column_name` and no `constraint_name` (nameable, catalogued NOT NULL
    constraints arrive in 17). The diagnostic asserted is therefore the column,
    which is every bit as specific -- it is what distinguishes this rejection
    from a null in any other required column of the same row.
    """
    savepoint = db_session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed below
        insert_chunk(db_session, chunk_row(real_document, page_number=None))
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.NotNullViolation), (
        "a chunk with no page_number must be refused as a NOT NULL violation "
        f"(SQLSTATE 23502); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.diag.column_name == "page_number", (
        "the rejection must name page_number, or some other required column was null "
        f"and this test never reached the rule it claims to cover; got "
        f"{original.diag.column_name!r} on {original.diag.table_name!r}"
    )


#: Each column of the composite reference, broken one at a time.
UNRESOLVABLE_DOCUMENT_REFERENCES: Mapping[str, Mapping[str, Any]] = {
    "no such document": {"document_id": "no-such-document-at-all"},
    "document type disagrees": {"document_type": "submittal"},
    "project disagrees": {"project_id": "PRJ-002"},
}


@pytest.mark.parametrize(
    "overrides",
    list(UNRESOLVABLE_DOCUMENT_REFERENCES.values()),
    ids=list(UNRESOLVABLE_DOCUMENT_REFERENCES),
)
def test_chunk_without_a_matching_document_row_is_rejected(
    db_session: Session,
    real_document: Mapping[str, Any],
    assert_rejects: RejectionAsserter,
    overrides: Mapping[str, Any],
) -> None:
    """TR-014, TR-046: a chunk's document reference must resolve to a real row.

    All three columns of the reference are tested, not only `document_id`,
    because `fk_chunk__document` points at the composite key
    `(document_id, document_type, project_id)`. That is the mechanism that keeps
    the chunk's denormalized type and project from disagreeing with its
    document -- unable to, rather than unlikely to -- and a test that only ever
    varied `document_id` would pass against a single-column foreign key.

    `PRJ-002` is a well-formed project identifier, so it reaches the foreign key
    instead of stopping at `ck_chunk__project_id_format`.
    """
    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, "fk_chunk__document"):
        insert_chunk(db_session, chunk_row(real_document, **overrides))


def test_deleting_a_document_with_chunks_is_refused(
    db_session: Session,
    real_document: Mapping[str, Any],
    assert_rejects: RejectionAsserter,
) -> None:
    """TR-046, TR-078: `ON DELETE RESTRICT` -- a citation is never silently orphaned.

    The other direction of the same foreign key. Dropping a document out from
    under its chunks would leave every page citation resolving through them
    pointing at nothing, which is the unattributable number Principle I forbids.
    """
    insert_chunk(db_session, chunk_row(real_document))

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, "fk_chunk__document"):
        db_session.execute(DELETE_DOCUMENT, {"document_id": real_document["document_id"]})


def test_a_chunk_resolves_to_its_named_source_document(
    db_session: Session, real_document: Mapping[str, Any]
) -> None:
    """TR-046: the positive half -- a page citation names a source, in one join.

    Without this, every rejection above could be satisfied by a table nothing can
    ever be inserted into.
    """
    chunk_id = insert_chunk(db_session, chunk_row(real_document, page_number=7))

    resolved = db_session.execute(
        text(
            """
            SELECT document.title, document.source_kind, chunk.page_number
            FROM chunk JOIN document USING (document_id, document_type, project_id)
            WHERE chunk.chunk_id = :chunk_id
            """
        ),
        {"chunk_id": chunk_id},
    ).one()

    assert resolved == (real_document["title"], "REAL", 7)


# --------------------------------------------------------------------------- #
# T019 -- layer-conditional provenance on `document` (TR-075, TR-087)
# --------------------------------------------------------------------------- #

#: One perturbation each, against the valid baseline of its own layer. Both
#: directions of every field group, because permitting *absence* on the wrong
#: layer would leave a fabricated issuing body representable, and that is the
#: asymmetry TR-075 exists to close.
PROVENANCE_REJECTIONS: tuple[tuple[str, str, Mapping[str, Any], str], ...] = (
    # A SYNTHETIC document carrying retrieval provenance it cannot have.
    (
        "synthetic with source_ref",
        "SYNTHETIC",
        {"source_ref": "https://standards.example.gov/piping/2022"},
        "ck_document__synthetic_has_no_source_ref",
    ),
    (
        "synthetic with issuing_body",
        "SYNTHETIC",
        {"issuing_body": "Example Standards Body"},
        "ck_document__synthetic_has_no_issuing_body",
    ),
    (
        "synthetic with retrieval_date",
        "SYNTHETIC",
        {"retrieval_date": date(2026, 1, 15)},
        "ck_document__synthetic_has_no_retrieval_date",
    ),
    # A REAL document missing retrieval provenance it must have. Absent and
    # whitespace-only are separate cases: `NOT NULL` would catch neither, and a
    # source reference of one tab is no more traceable than none.
    ("real without source_ref", "REAL", {"source_ref": None}, "ck_document__real_has_source_ref"),
    (
        "real with blank source_ref",
        "REAL",
        {"source_ref": " \t"},
        "ck_document__real_has_source_ref",
    ),
    (
        "real without issuing_body",
        "REAL",
        {"issuing_body": None},
        "ck_document__real_has_issuing_body",
    ),
    (
        "real with blank issuing_body",
        "REAL",
        {"issuing_body": "   "},
        "ck_document__real_has_issuing_body",
    ),
    (
        "real without retrieval_date",
        "REAL",
        {"retrieval_date": None},
        "ck_document__real_has_retrieval_date",
    ),
    # A REAL document carrying generator provenance it cannot have.
    (
        "real with generator_id",
        "REAL",
        {"generator_id": "corpus-generator"},
        "ck_document__real_has_no_generator",
    ),
    (
        "real with generation_seed",
        "REAL",
        {"generation_seed": "seed-000042"},
        "ck_document__real_has_no_seed",
    ),
    (
        "real with generated_at",
        "REAL",
        {"generated_at": date(2026, 2, 1)},
        "ck_document__real_has_no_generated_at",
    ),
    (
        "real with fixture_hashes",
        "REAL",
        {"fixture_hashes": [VALID_SHA256]},
        "ck_document__real_has_no_fixture_hashes",
    ),
    (
        "real with roster_hash",
        "REAL",
        {"roster_hash": ROSTER_SHA256},
        "ck_document__real_has_no_roster_hash",
    ),
    # A SYNTHETIC document missing generator provenance it must have.
    (
        "synthetic without generator_id",
        "SYNTHETIC",
        {"generator_id": None},
        "ck_document__synthetic_has_generator",
    ),
    (
        "synthetic with blank generator_id",
        "SYNTHETIC",
        {"generator_id": "\t"},
        "ck_document__synthetic_has_generator",
    ),
    (
        "synthetic without generation_seed",
        "SYNTHETIC",
        {"generation_seed": None},
        "ck_document__synthetic_has_seed",
    ),
    (
        "synthetic with blank generation_seed",
        "SYNTHETIC",
        {"generation_seed": " "},
        "ck_document__synthetic_has_seed",
    ),
    (
        "synthetic without generated_at",
        "SYNTHETIC",
        {"generated_at": None},
        "ck_document__synthetic_has_generated_at",
    ),
    (
        "synthetic without fixture_hashes",
        "SYNTHETIC",
        {"fixture_hashes": None},
        "ck_document__synthetic_has_fixture_hashes",
    ),
    (
        "synthetic with malformed fixture hash",
        "SYNTHETIC",
        {"fixture_hashes": ["not-a-content-hash"]},
        "ck_document__synthetic_has_fixture_hashes",
    ),
    (
        "synthetic without roster_hash",
        "SYNTHETIC",
        {"roster_hash": None},
        "ck_document__synthetic_has_roster_hash",
    ),
    (
        "synthetic with malformed roster_hash",
        "SYNTHETIC",
        {"roster_hash": "sha256:not-hex"},
        "ck_document__synthetic_has_roster_hash",
    ),
)


@pytest.mark.parametrize(
    ("layer", "overrides", "constraint"),
    [case[1:] for case in PROVENANCE_REJECTIONS],
    ids=[case[0] for case in PROVENANCE_REJECTIONS],
)
def test_layer_conditional_provenance_is_enforced(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    layer: str,
    overrides: Mapping[str, Any],
    constraint: str,
) -> None:
    """TR-075, TR-087: provenance is required *and* rejected per layer.

    `project-instructions.md` v1.2.0 states the Data Provenance rule per layer:
    a retrieved document records source, issuing body, and retrieval date; a
    generated one records generator identity, seed, generation date, and fixture
    hashes; and a generated document must not carry retrieval provenance it does
    not have. Both halves are enforced here at the storage boundary, so a
    fabricated issuing body is unrepresentable rather than merely discouraged.

    Every case perturbs exactly one field of an otherwise-valid row of its own
    layer. That is what makes the named constraint the *only* one that can fire:
    omit two fields and PostgreSQL reports whichever check it happened to
    evaluate first, and the test would be green while naming a rule it never
    reached.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, constraint):
        insert_document(db_session, document_row(layer, **overrides))


@pytest.mark.parametrize("layer", ["REAL", "SYNTHETIC"])
def test_a_well_formed_document_of_either_layer_is_accepted(
    db_session: Session, layer: str
) -> None:
    """The positive control for every provenance rejection above.

    Twenty-two rejections against a baseline that was itself invalid would all
    pass while proving that the baseline is broken. This is the assertion that
    makes them mean what they say.
    """
    row = insert_document(db_session, document_row(layer))

    stored = db_session.execute(
        text("SELECT source_kind, license_basis FROM document WHERE document_id = :document_id"),
        {"document_id": row["document_id"]},
    ).one()

    assert stored == (layer, row["license_basis"])


# --------------------------------------------------------------------------- #
# T019 -- unconditional provenance on `document` (TR-075, OBJ2 VC7)
# --------------------------------------------------------------------------- #

#: The two fields OBJ2 VC7 calls mandatory *on every layer*. Neither is
#: conditioned on `source_kind`, which is why `PROVENANCE_REJECTIONS` above
#: cannot reach them: every case in that table pairs one perturbation with the
#: one layer it belongs to, and these two belong to both. So the parametrisations
#: below cross each perturbation with *both* baselines instead.
MANDATORY_ON_EVERY_LAYER = ("license_basis", "source_kind")

#: The widened trim set `ck_document__license_basis_present` is written against,
#: one case per character. Single-argument `btrim` strips *spaces only*, so a
#: license basis of one tab would satisfy a naively written check while recording
#: no license at all -- and Data Provenance makes the basis mandatory precisely so
#: that no corpus location can end up mixing licenses.
#:
#: The vertical tab is the case that catches `E'\\v'`: PostgreSQL's escape-string
#: syntax has no `\\v` and drops the backslash, so a trim set spelled that way
#: holds the *letter* `v` rather than U+000B. That single typo both admits `"\v"`
#: here and rejects a legitimate `'vvv'`, so both directions are asserted -- this
#: list, and `test_a_license_basis_of_only_the_letter_v_is_accepted` below.
BLANK_LICENSE_BASES = ("", "   ", "\t", "\n", "\r", "\f", "\v")

#: Labels outside the closed set `ck_document__source_kind` declares. Lowercase
#: and empty are here because the set is compared with `IN`, which is
#: case-sensitive and admits no empty string: a loader that downcased its manifest
#: value would otherwise store a layer label no downstream branch recognises.
UNDECLARED_SOURCE_KINDS = ("MADE_UP", "real", "")


@pytest.mark.parametrize("column", MANDATORY_ON_EVERY_LAYER)
@pytest.mark.parametrize("layer", ["REAL", "SYNTHETIC"])
def test_a_document_missing_a_field_mandatory_on_every_layer_is_rejected(
    db_session: Session, layer: str, column: str
) -> None:
    """OBJ2 VC7, TR-075: no document is stored without a license basis or a layer label.

    Both columns are `NOT NULL` on the table itself rather than inside one of the
    conditional pairs above, and both are perturbed against *both* baselines,
    because the criterion's claim is that they are mandatory on every layer.

    For `source_kind`, `layer` names the provenance the row *carries* -- the label
    itself is what has been removed. Covering both shapes matters more here than
    anywhere else in this file: with `source_kind` null every layer-conditional
    check reduces to `NULL <> 'REAL' OR ...`, which is NULL, and a check rejects a
    row only on *false*. So all sixteen of them pass, and this `NOT NULL` is the
    only rule standing between the table and a row whose provenance cannot be
    classified at all -- whichever provenance it happens to hold.

    Not routed through `assert_rejects`, for the PostgreSQL 16 reason
    `test_missing_page_number_is_rejected` records: a NOT NULL violation reports
    `column_name` and carries no `constraint_name`. The column is the assertion,
    and with six of the fourteen columns this insert supplies being NOT NULL, it
    is what distinguishes this rejection from a null in any of the others.
    """
    savepoint = db_session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed below
        insert_document(db_session, document_row(layer, **{column: None}))
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.NotNullViolation), (
        f"a {layer} document with no {column} must be refused as a NOT NULL violation "
        f"(SQLSTATE 23502); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.diag.column_name == column, (
        f"the rejection must name {column}, or some other required column was null and "
        f"this test never reached the rule it claims to cover; got "
        f"{original.diag.column_name!r} on {original.diag.table_name!r}"
    )


@pytest.mark.parametrize("blank", BLANK_LICENSE_BASES)
@pytest.mark.parametrize("layer", ["REAL", "SYNTHETIC"])
def test_a_blank_license_basis_is_rejected_on_either_layer(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    layer: str,
    blank: str,
) -> None:
    """OBJ2 VC7, TR-075: a license basis of whitespace states no license basis.

    Not the same claim as the `NOT NULL` above, which is why both exist: a
    one-tab basis satisfies `NOT NULL` while leaving the document exactly as
    unlicensed as absence would, and the whole reason the basis is replicated onto
    every per-project row (TR-074) is that a reader can tell what licenses a
    corpus location holds.

    Run against both baselines because the check is unconditional and the
    criterion says every layer. `test_a_well_formed_document_of_either_layer_is_accepted`
    is the positive control for both: it reads `license_basis` back off a valid row
    of each layer, so these rejections cannot be passing because the baseline was
    itself unacceptable.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_document__license_basis_present"
    ):
        insert_document(db_session, document_row(layer, license_basis=blank))


@pytest.mark.parametrize("undeclared", UNDECLARED_SOURCE_KINDS)
@pytest.mark.parametrize("layer", ["REAL", "SYNTHETIC"])
def test_an_undeclared_layer_label_is_rejected_whatever_provenance_the_row_carries(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    layer: str,
    undeclared: str,
) -> None:
    """OBJ2 VC7, TR-075: the layer label is drawn from a closed set of two.

    `layer` again names the provenance the row carries rather than the label
    written, since the label is the perturbation -- so each case is a row with
    complete, valid provenance of one layer wearing a label that names neither.

    That combination is the reason the closed set has to be a constraint. Every
    conditional check above is of the form `source_kind <> 'REAL' OR ...`, so an
    unrecognised label makes *both* halves of every pair true and all sixteen
    pass vacuously. `ck_document__source_kind` is then the only rule that stops a
    row carrying full retrieval provenance under a label nothing downstream can
    branch on -- which is a corpus whose REAL/SYNTHETIC split silently has a third
    bucket in it, the failure the Data Provenance rule exists to prevent.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_document__source_kind"):
        insert_document(db_session, document_row(layer, source_kind=undeclared))


@pytest.mark.parametrize("layer", ["REAL", "SYNTHETIC"])
def test_a_license_basis_of_only_the_letter_v_is_accepted(db_session: Session, layer: str) -> None:
    """The other half of the `E'\\v'` trap: a legitimate value must not be rejected.

    A trim set written `E' \\t\\n\\r\\f\\v'` contains the letter `v`, so
    `btrim('vvv', ...)` returns the empty string and this perfectly ordinary value
    would be refused. The vertical-tab case above shows the character is *in* the
    set; this shows the letter is not. Neither test alone can distinguish the two
    spellings, which is why the assertion is a read-back rather than an insert
    that merely does not raise.
    """
    row = insert_document(db_session, document_row(layer, license_basis="vvv"))

    stored = db_session.execute(
        text("SELECT license_basis FROM document WHERE document_id = :document_id"),
        {"document_id": row["document_id"]},
    ).scalar_one()

    assert stored == "vvv", (
        "a license basis of 'vvv' is not blank and must be stored unchanged; a trim set "
        f"spelled E' \\t\\n\\r\\f\\v' would have rejected the row outright. Got {stored!r}"
    )


# --------------------------------------------------------------------------- #
# T019 -- G-9 document_id format (TR-041, TR-077) and TR-074
# --------------------------------------------------------------------------- #

#: The format this epic declares on E002's behalf: lowercase kebab slug,
#: 3 to 128 characters. Gap G-9 records it as an open obligation -- E002 and
#: E006 adopt it or E003 amends -- but the *format itself* is not a gap: it is
#: enforced here, so a manifest key outside it fails the load rather than
#: storing an unresolvable citation.
MALFORMED_DOCUMENT_IDS: Mapping[str, str] = {
    "uppercase": "Example-Standards-2022",
    "underscore": "example_standards_2022",
    "leading hyphen": "-example-standards",
    "trailing hyphen": "example-standards-",
    "doubled hyphen": "example--standards",
    "embedded space": "example standards",
    "dot": "example.standards",
    "too short": "ab",
    "too long": "a" * 129,
    "empty": "",
}

WELL_FORMED_DOCUMENT_IDS: Mapping[str, str] = {
    "minimum length": "abc",
    "digits only": "2022",
    "alphanumeric segments": "asme-b31-3-2022-process-piping",
    "maximum length": "a" * 128,
}


@pytest.mark.parametrize(
    "document_id",
    list(MALFORMED_DOCUMENT_IDS.values()),
    ids=list(MALFORMED_DOCUMENT_IDS),
)
def test_malformed_document_id_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    document_id: str,
) -> None:
    """TR-041, TR-077, gap G-9: the declared key format is enforced, not documented.

    Both halves of the format are covered -- the character pattern and the 3-to-128
    length bound -- because `ck_document__id_format` is one constraint carrying
    two rules and a test of only the pattern would pass against a schema that
    dropped the bound.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_document__id_format"):
        insert_document(db_session, document_row("REAL", document_id=document_id))


@pytest.mark.parametrize(
    "document_id",
    list(WELL_FORMED_DOCUMENT_IDS.values()),
    ids=list(WELL_FORMED_DOCUMENT_IDS),
)
def test_well_formed_document_id_is_accepted(db_session: Session, document_id: str) -> None:
    """The boundary from the other side: 3 and 128 characters both insert.

    A check written `BETWEEN 4 AND 127` would pass every rejection above.
    """
    insert_document(db_session, document_row("REAL", document_id=document_id))

    stored = db_session.execute(
        text("SELECT count(*) FROM document WHERE document_id = :document_id"),
        {"document_id": document_id},
    ).scalar_one()

    assert stored == 1


def test_one_source_under_two_projects_inserts_twice(db_session: Session) -> None:
    """TR-074: one row per source-and-project pair, so no global source uniqueness.

    A public reference standard cited by three projects is three rows, because
    `project_id` is NOT NULL on every document -- SC-006 requires every chunk to
    carry a project identifier and the chunk inherits it through the composite
    foreign key. The two rows here share a `source_ref` and differ only in
    project, which is the shape a global unique constraint on the source would
    forbid and this schema deliberately admits.
    """
    shared_source = "https://standards.example.gov/piping/2022"

    for project_id in ("PRJ-001", "PRJ-002"):
        insert_document(
            db_session,
            document_row(
                "REAL",
                document_id=f"example-standards-piping-2022-{project_id.lower()}",
                project_id=project_id,
                source_ref=shared_source,
            ),
        )

    projects = (
        db_session.execute(
            text(
                """
                SELECT project_id FROM document
                WHERE source_ref = :source_ref
                ORDER BY project_id
                """
            ),
            {"source_ref": shared_source},
        )
        .scalars()
        .all()
    )

    assert list(projects) == ["PRJ-001", "PRJ-002"], (
        "the same source under two project identifiers must produce two rows (TR-074); "
        f"got {list(projects)}"
    )
