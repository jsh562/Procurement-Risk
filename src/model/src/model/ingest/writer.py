"""The per-document transaction, the chunk write, and the containment guard.

FR-010 / FR-020 / FR-021 / FR-042 / FR-054. This module is where a chunking
stops being a value in memory and becomes rows, and the three things that have
to be true at that moment are all here rather than distributed over the callers:

1. **One document, one transaction** (FR-042, FR-054). The connection is
   **autocommit** and each document is wrapped in `with conn.transaction():`.
   psycopg 3's default non-autocommit connection opens an implicit transaction
   on the first execute and would silently make the whole run one transaction —
   a failure on document 50 would then discard the 49 that had already
   succeeded. `data-model.md` §Write Order states the statement order inside
   the block and this module implements it for the first-ingest path.
2. **The embedding's identity travels with every vector** (FR-020), and **the
   vector's width is read from the database at run time** (FR-021). Neither is
   a literal in this file. `schema_constants.vector_dimension` is the schema's
   published value and E003's TR-076 makes the DDL literal govern it, so
   reading the row is reading what the column will actually accept — a compiled
   -in 384 would agree with it until the day it did not.
3. **A chunk that is not on the page it names is never committed** (FR-010).
   The job-side half of the total containment check runs *inside* the document's
   transaction, against a **fresh read of the document's own bytes**, so a
   mis-attributed chunk aborts its document rather than being found later by the
   verification suite. The suite's half is
   `src/model/tests/ingest/test_page_attribution.py`.
4. **Every stored value carries the signals its score was computed from**
   (FR-063), and the two are checked to agree *before* either is written. The
   weights come from this run's own `ingestion_run` row rather than from a code
   constant, so a score is compared against the policy that produced it; the
   comparison is bit equality, because the deductions are applied left to right
   in a declared order and `double precision` subtraction is not associative
   (SC-026). A value below the run's declared floor is refused here rather than
   stored (FR-032) — it belongs in `extraction_failure` with outcome
   `confidence_below_threshold`.

**The error handler catches outside the `with` block** (HINT-002,
`data-model.md` §Write Order). A nested `transaction()` in psycopg 3 is a
savepoint, so a handler *inside* the block rolls back to the savepoint and lets
the outer block commit the partial document. `write_generations` is written the
way it is for that one reason.

**One generation's rows per document, and this module writes only the first.**
`uq_chunk__document_ordinal UNIQUE (document_id, ordinal)` is scoped to the
document, so a second resident generation's ordinal 0 collides ({SAD:ADR-0020});
the predecessor is removed by the promotion, which runs under the schema-owning
role and is `data-model.md` §Operator Procedures 3. This module therefore
**refuses** a document that already has an active generation instead of
attempting a removal it holds no privilege for.

`model.ingest` never imports `gateway`; nothing here reaches a provider, and the
only arithmetic is the containment comparison, which is a substring test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from sqlalchemy import URL

from model.compute.confidence import ParseSignals, compute_confidence
from model.corpus.derive import normalize_page_text
from model.ingest.chunker import Chunk, DocumentChunking, chunk_pages
from model.ingest.documents import DocumentRecord
from model.ingest.embed import embed_chunks, embedding_identity
from model.ingest.parse import ParsedPage, page_by_number, read_pages
from model.ingest.runs import ConfidencePolicy, read_confidence_policy
from model.schema.url import get_database_url

__all__ = [
    "CHUNK_COLUMNS",
    "EXTRACTED_VALUE_COLUMNS",
    "PARSE_SIGNAL_COLUMNS",
    "CitedChunk",
    "ContainmentMiss",
    "ContainmentResult",
    "DocumentOutcome",
    "PreparedDocument",
    "PreparedValue",
    "ValueCitation",
    "WriterError",
    "check_confidence_agrees",
    "cite_value",
    "connect",
    "prepare_document",
    "vector_dimension",
    "verify_page_containment",
    "write_document_generation",
    "write_generations",
]

#: The columns this module writes to E003's `chunk`, in COPY order.
#:
#: `search_vector` is `GENERATED ALWAYS` and `created_at` carries a default, so
#: naming either would be rejected by the server; both are deliberately absent.
#: `part_numbers` is written as NULL — this epic extracts no part designations
#: from a chunk, and a column filled with a guess is worse than one left empty.
CHUNK_COLUMNS: tuple[str, ...] = (
    "chunk_id",
    "document_id",
    "document_type",
    "project_id",
    "page_number",
    "ordinal",
    "spec_section",
    "heading",
    "part_numbers",
    "body_text",
    "embedding",
    "embedding_model_id",
    "embedding_model_revision",
)

#: The PostgreSQL type of each column above, for `copy.set_types`. Binary COPY
#: carries no type information, so the list is required rather than an
#: optimisation: an omitted or mis-ordered entry produces a row the server
#: decodes as something else entirely.
_CHUNK_COPY_TYPES: tuple[str, ...] = (
    "uuid",
    "text",
    "text",
    "text",
    "int4",
    "int4",
    "text",
    "text",
    "text",
    "text",
    "vector",
    "text",
    "text",
)

_CHUNK_COPY = f"COPY chunk ({', '.join(CHUNK_COLUMNS)}) FROM STDIN WITH (FORMAT BINARY)"  # noqa: S608

#: `ck_ingestion_run_document__tuple_digest_format`. Checked before the write so
#: a malformed digest is reported by the module that was given it rather than by
#: a constraint violation five statements later.
INPUT_TUPLE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class WriterError(RuntimeError):
    """Raised when a document cannot be written, or must not be.

    One type for every failure, as the rest of this package uses: the caller
    learns the same thing from each of them — this document has no rows, its
    transaction rolled back, and the documents before it are untouched.
    """


# ---------------------------------------------------------------------------
# FR-010 — the containment guard the job itself runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContainmentMiss:
    """One chunk whose text is not on the page it names."""

    document_id: str
    ordinal: int
    page_number: int
    excerpt: str

    def __str__(self) -> str:
        return (
            f"{self.document_id} ordinal {self.ordinal} names page {self.page_number}, "
            f"which does not contain {self.excerpt!r}"
        )


@dataclass(frozen=True)
class ContainmentResult:
    """What a containment check enumerated, and what it found (FR-010, FR-068).

    The population and its count are carried rather than a bare boolean,
    because FR-068 requires every total check to publish what it ranged over:
    a check that enumerated nothing and found nothing wrong is not a pass.
    """

    document_id: str
    population: str
    count: int
    misses: tuple[ContainmentMiss, ...]

    @property
    def holds(self) -> bool:
        return not self.misses


def verify_page_containment(
    document_id: str,
    chunks: Sequence[Chunk],
    pages: Sequence[ParsedPage],
) -> ContainmentResult:
    """Every chunk's text against a fresh extraction of the page it names.

    Args:
        document_id: the document being checked, for the failure message.
        chunks: every chunk of that document — the whole population, never a
            sample.
        pages: a **fresh** read of the document's own bytes. The caller supplies
            it rather than this function reading the file, so the freshness is
            visible at the call site and a caller cannot accidentally hand over
            the chunker's own pages.

    Returns:
        The enumerated population, its count, and every miss.

    Raises:
        WriterError: when the population is empty. A document that produced no
            chunk is not silently committed as a document with nothing in it —
            FR-068's rule that an empty population fails rather than passes,
            applied at the one place it can still abort a write.

    **Both sides are normalized by `corpus.derive`'s own comparison form** and by
    nothing else. Chunk text is the reader's lines joined by newline, page text
    is the reader's assembly of the same lines, and the normalization collapses
    whitespace — so containment is a plain substring test rather than a fuzzy
    match with a threshold nobody declared.

    **This is not independent of the parser, and that is disclosed rather than
    implied.** FR-008 pins one reader for the whole repository, so both sides of
    this comparison come through it. What the check establishes is that the
    *page number recorded on the chunk* addresses a page whose text contains the
    chunk — the attribution — and not that the reader read either page
    correctly.
    """
    if not chunks:
        raise WriterError(
            f"FR-068: the containment check for {document_id} enumerated zero chunks. "
            f"An empty population fails rather than passes, so the document is not written."
        )
    misses: list[ContainmentMiss] = []
    for chunk in chunks:
        page = page_by_number(pages, chunk.page_number)
        haystack = normalize_page_text(page.text)
        needle = normalize_page_text(chunk.body_text)
        if needle not in haystack:
            misses.append(
                ContainmentMiss(
                    document_id=document_id,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    excerpt=needle[:160],
                )
            )
    return ContainmentResult(
        document_id=document_id,
        population=f"every chunk of {document_id}, addressed by its recorded page number",
        count=len(chunks),
        misses=tuple(misses),
    )


# ---------------------------------------------------------------------------
# The connection, and the two values read from the database
# ---------------------------------------------------------------------------


def _conninfo(url: str | URL | None = None) -> str:
    """A libpq connection string from the entry's one connection channel.

    `model.schema.url` resolves `DATABASE_URL` to the SQLAlchemy driver name
    `postgresql+psycopg`, which libpq does not recognise, so the scheme is put
    back before the string is handed to psycopg. Resolving it here rather than
    reading the environment directly keeps the migrations, the schema tests, and
    this writer pointed at one database by construction.
    """
    resolved = get_database_url() if url is None else url
    if isinstance(resolved, str):
        return resolved
    return resolved.set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def connect(url: str | URL | None = None) -> Iterator[psycopg.Connection]:
    """An **autocommit** connection with pgvector's types registered.

    Autocommit is the load-bearing setting and the reason this exists rather
    than a bare `psycopg.connect` at each call site: on a default connection,
    psycopg 3 opens an implicit transaction at the first execute and holds it
    until an explicit commit, so every `with conn.transaction():` below would
    become a *savepoint* inside one run-long transaction and FR-042 would be
    false while the code still read as though it were true.

    `register_vector` is what lets `chunk.embedding` be written by binary COPY
    and read back as a vector rather than as its text representation; without it
    the near-duplicate measurement would be parsing strings.
    """
    connection = psycopg.connect(_conninfo(url), autocommit=True)
    try:
        register_vector(connection)
        yield connection
    finally:
        connection.close()


def vector_dimension(connection: psycopg.Connection) -> int:
    """FR-021: the vector width, read from the schema at run time.

    Read from `schema_constants` — E003's TR-043 single record of the constants
    the schema is built around — rather than written as 384 here. TR-076 makes
    the `vector(384)` typmod on `chunk.embedding` the governing value and this
    row its published copy, and E003's own SC-019 asserts the two agree; reading
    the published copy is therefore reading the width the column will accept,
    and it moves when the schema moves without an edit to this file.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT vector_dimension FROM schema_constants")
        row = cursor.fetchone()
    if row is None:
        raise WriterError(
            "FR-021: `schema_constants` holds no row, so the vector dimension the schema "
            "publishes cannot be read. The row is seeded by revision 0002 in the same "
            "revision that creates the table; a database without it is not migrated."
        )
    dimension = int(row[0])
    if dimension < 1:
        raise WriterError(f"FR-021: `schema_constants.vector_dimension` is {dimension}")
    return dimension


# ---------------------------------------------------------------------------
# What a document looks like on the way in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedDocument:
    """One document chunked and embedded, before any row exists.

    Carries the chunking and the vectors together because FR-020's guarantee is
    about their *pairing*: row *i* of `embeddings` is the vector of
    `chunking.chunks[i]`, and the model identity recorded beside it is the
    identity of the session that produced it. Splitting them across two
    arguments to the writer would make a mismatch a call-site convention.
    """

    record: DocumentRecord
    chunking: DocumentChunking
    embeddings: np.ndarray
    embedding_model_id: str
    embedding_model_revision: str

    def __post_init__(self) -> None:
        chunks = self.chunking.chunks
        if self.embeddings.shape[0] != len(chunks):
            raise WriterError(
                f"{self.record.document_id}: {len(chunks)} chunks and "
                f"{self.embeddings.shape[0]} vectors; every chunk carries exactly one"
            )
        if not self.embedding_model_id.strip() or not self.embedding_model_revision.strip():
            raise WriterError(
                f"FR-020: {self.record.document_id} carries a blank embedding identity, "
                f"which `ck_chunk__embedding_model_id_present` refuses and which would "
                f"leave two model versions' vectors indistinguishable"
            )


def prepare_document(
    record: DocumentRecord,
    pages: Sequence[ParsedPage] | None = None,
) -> PreparedDocument:
    """Chunk and embed one document, recording the encoder's identity (FR-020).

    `pages` is accepted so a caller that has already read the document does not
    read it twice; the containment guard's fresh read is a *separate* read and
    is never this one.
    """
    read = tuple(pages) if pages is not None else read_pages(record.path)
    chunking = chunk_pages(record, read)
    model_id, revision = embedding_identity()
    vectors = embed_chunks([chunk.body_text for chunk in chunking.chunks])
    return PreparedDocument(
        record=record,
        chunking=chunking,
        embeddings=vectors,
        embedding_model_id=model_id,
        embedding_model_revision=revision,
    )


@dataclass(frozen=True)
class DocumentOutcome:
    """What one document's transaction did, or why it did nothing."""

    document_id: str
    chunks_written: int
    containment: ContainmentResult | None
    values_written: int = 0
    error: str | None = None

    @property
    def committed(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# FR-029 — the citation, inherited from the chunk, anchored on the printed value
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedChunk:
    """One chunk a value was read out of, named by its ordinal within the document.

    **By ordinal, not by identifier.** Chunk identifiers are minted inside the
    document's transaction at write-order step 1, so nothing upstream of the
    write can know one. The ordinal is assigned by the chunker, is unique within
    the document, and is what `uq_chunk__document_ordinal` already enforces — so
    it is the only handle a value can carry from extraction to the write.

    The page travels with it because that is what the citation *is*: FR-029 says
    the cited page is inherited from the source chunk, and carrying the pair
    means a caller cannot supply a page the chunk does not have.
    """

    ordinal: int
    page_number: int

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise WriterError(f"chunk ordinals are zero-based; got {self.ordinal}")
        if self.page_number < 1:
            raise WriterError(f"page numbers are one-based; got {self.page_number}")


@dataclass(frozen=True)
class ValueCitation:
    """Where one extracted value points, and what else it was assembled from.

    **The anchor is the chunk carrying the printed value** (FR-029), never the
    one carrying only the label. On a page-split field the label ends page *k*
    and the value begins page *k+1*, so the anchor is the *later* page — which
    is why every comparison that reassembles such a value orders its chunks by
    page rather than by contributor position (SC-027), and why `contributors`
    below is sorted by page.

    **The citation is inherited, not supplied.** `cited_page` is the anchor
    chunk's own page and there is no constructor that lets the two differ, which
    is the application-side half of "a citation disagreeing with its chunk is
    unstorable"; the storage-side half is `fk_extracted_value__chunk_page`, a
    composite foreign key against `chunk (chunk_id, page_number)`.
    """

    anchor: CitedChunk
    contributors: tuple[CitedChunk, ...] = ()

    def __post_init__(self) -> None:
        ordinals = [chunk.ordinal for chunk in self.contributors]
        if self.anchor.ordinal in ordinals:
            raise WriterError(
                f"FR-029: chunk ordinal {self.anchor.ordinal} is the citation anchor and "
                f"also appears among the contributing chunks. The anchor is contributor 1 "
                f"by definition and never appears again — a second row for it would make "
                f"`source_chunk_count` disagree with the rows that explain it."
            )
        if len(set(ordinals)) != len(ordinals):
            raise WriterError(
                f"FR-029: a contributing chunk is named twice for one value; ordinals "
                f"were {ordinals}"
            )
        later = [
            chunk for chunk in self.contributors if chunk.page_number > self.anchor.page_number
        ]
        if later:
            raise WriterError(
                f"FR-029: the anchor is the chunk carrying the printed value, so it is "
                f"the *later* page — but ordinal(s) "
                f"{[chunk.ordinal for chunk in later]} contribute from a page after the "
                f"anchor's page {self.anchor.page_number}. Either the anchor is the "
                f"label's chunk rather than the value's, or the value continues past the "
                f"chunk cited for it."
            )
        object.__setattr__(
            self,
            "contributors",
            tuple(sorted(self.contributors, key=lambda chunk: (chunk.page_number, chunk.ordinal))),
        )

    @property
    def cited_page(self) -> int:
        """FR-029: inherited from the source chunk, never stated separately."""
        return self.anchor.page_number

    @property
    def source_chunk_count(self) -> int:
        """The anchor plus its contributors. At least 1, never 0."""
        return 1 + len(self.contributors)

    @property
    def provenance_kind(self) -> str:
        """`ck_extracted_value__provenance_agrees_with_count`, as a derivation.

        Derived rather than supplied, because the column pair is a biconditional
        in the database: `multi_chunk` with a count of 1 is refused as firmly as
        `single_chunk` with a count of 3, and two independently supplied values
        can disagree while one derived from the other cannot.
        """
        return "multi_chunk" if self.source_chunk_count > 1 else "single_chunk"

    def pages_in_reading_order(self) -> tuple[int, ...]:
        """Every page this value draws on, ascending (SC-027).

        Ascending **page** order, not contributor order. The anchor is the later
        page on a page-split value, so reassembling by contributor position would
        put the value before its own label.
        """
        return tuple(sorted({chunk.page_number for chunk in (self.anchor, *self.contributors)}))


def cite_value(value_chunk: CitedChunk, label_chunks: Sequence[CitedChunk] = ()) -> ValueCitation:
    """Build a citation anchored on the chunk that printed the value (FR-029).

    Args:
        value_chunk: the chunk carrying the **printed value**. This becomes the
            anchor and its page becomes the cited page.
        label_chunks: the chunks carrying only the field's label, where the
            field split across a page break. Empty for the ordinary case, which
            is most values.

    Returns:
        The citation, with contributors sorted by page.

    Raises:
        WriterError: the anchor appears among the contributors, a contributor is
            named twice, or a contributor sits on a page after the anchor's.

    Named `cite_value` rather than taking a `page` argument so the FR-029 rule is
    in the signature: a caller passes the chunk that printed the value and gets
    the citation, and there is no parameter through which the label's page could
    become the cited one.
    """
    return ValueCitation(anchor=value_chunk, contributors=tuple(label_chunks))


@dataclass(frozen=True)
class PreparedValue:
    """One `extracted_value` row and its parse-signal row, before the identifiers.

    Everything except the identifiers, which are minted inside the transaction.
    `value_text` and `value_number` come from `model.compute.coerce`; the
    `confidence` comes from `model.compute.confidence`. Both are computed by the
    orchestrator and passed in, because `model.ingest` applies the computation
    and never performs it inside the module that talks to the provider.

    **The signals travel with the score** (FR-063). They are what
    `extracted_value_parse_signal` records, and carrying them beside the
    confidence is what lets the write check that the two agree before either is
    stored — SC-026's recomputation asserted at the boundary rather than only
    afterwards. Two of the three exist in no E003 column, so without this the
    recomputation would read the score and compare it with itself.

    The page-split signal is held equal to the citation's own chunk count here,
    as `fk_extracted_value_parse_signal__value_count` holds it equal in the
    database: a signal row saying "one chunk" beside a value assembled from two
    is a disagreement the recomputation cannot see, because it would read the
    copy while the citation read the original.
    """

    field_name: str
    value_kind: str
    value_text: str
    value_number: object | None
    confidence: float
    citation: ValueCitation
    signals: ParseSignals

    def __post_init__(self) -> None:
        if self.signals.source_chunk_count != self.citation.source_chunk_count:
            raise WriterError(
                f"{self.field_name}: the parse signal records "
                f"{self.signals.source_chunk_count} source chunk(s) and the citation "
                f"records {self.citation.source_chunk_count}. "
                f"`fk_extracted_value_parse_signal__value_count` refuses the pair, and the "
                f"page-split deduction would otherwise be computed from a copy that can "
                f"disagree with the value's own provenance."
            )
        if not self.value_text.strip():
            raise WriterError(
                f"{self.field_name}: `ck_extracted_value__value_text_present` refuses a "
                f"blank value; a field that is not printed is FR-037's `no_value_found`"
            )
        if (self.value_kind == "number") != (self.value_number is not None):
            raise WriterError(
                f"{self.field_name}: `ck_extracted_value__numeric_iff_number_kind` is a "
                f"biconditional — the typed numeric is populated exactly on number-kind "
                f"values; got kind={self.value_kind!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise WriterError(
                f"{self.field_name}: `ck_extracted_value__confidence_range` admits "
                f"[0.0, 1.0] inclusive at both ends; got {self.confidence}"
            )


#: The columns this module writes to E003's `extracted_value`, in insert order.
#: `extracted_at` carries a default and is deliberately absent.
EXTRACTED_VALUE_COLUMNS: tuple[str, ...] = (
    "extracted_value_id",
    "source_chunk_id",
    "cited_page",
    "field_name",
    "value_kind",
    "value_text",
    "value_number",
    "confidence",
    "provenance_kind",
    "source_chunk_count",
)

_EXTRACTED_VALUE_INSERT = (
    f"INSERT INTO extracted_value ({', '.join(EXTRACTED_VALUE_COLUMNS)}) "  # noqa: S608
    f"VALUES ({', '.join('%s' for _ in EXTRACTED_VALUE_COLUMNS)})"
)

#: FR-063's row, one per stored value. `extracted_value_id` is the whole primary
#: key, so a second, disagreeing signal row for one value is unrepresentable
#: rather than merely wrong.
PARSE_SIGNAL_COLUMNS: tuple[str, ...] = (
    "extracted_value_id",
    "run_id",
    "document_id",
    "label_match",
    "source_chunk_count",
    "validated_after_repair",
)

_PARSE_SIGNAL_INSERT = (
    f"INSERT INTO extracted_value_parse_signal ({', '.join(PARSE_SIGNAL_COLUMNS)}) "  # noqa: S608
    f"VALUES ({', '.join('%s' for _ in PARSE_SIGNAL_COLUMNS)})"
)


def check_confidence_agrees(value: PreparedValue, policy: ConfidencePolicy) -> None:
    """The stored score is the one its signals compute to, under this run's policy.

    Raises:
        WriterError: the score disagrees with its signals, or it is below the
            run's declared floor.

    **Bit equality, not a tolerance** (SC-026, FR-057). The deductions are
    applied left to right in the declared order and `double precision`
    subtraction is not associative, so a comparison within a tolerance would
    accept exactly the grouping error the declared order exists to exclude.

    **The floor is enforced here because here is the storage boundary**
    (FR-032, Principle III). A value below the run's declared floor is recorded
    as a failure with outcome `confidence_below_threshold` and is not persisted;
    reaching this function with one means the orchestrator tried to store it,
    and a value stored wrong is the silent failure the principle biases against.

    The policy is read from the run's **own row**, never from the declared
    constants: a check against today's policy would recompute a score under
    weights the row was never scored with, succeed, and report agreement.
    """
    expected = compute_confidence(value.signals, policy.weights)
    if value.confidence != expected:
        raise WriterError(
            f"FR-063 / SC-026: {value.field_name} carries confidence "
            f"{value.confidence!r}, but its parse signals "
            f"({value.signals.description}) compute to {expected!r} under this run's own "
            f"weights. The recomputation is bit equality, not equality within a "
            f"tolerance: the deductions are applied left to right in the declared order "
            f"and `double precision` subtraction is not associative."
        )
    if not policy.admits(value.confidence):
        raise WriterError(
            f"FR-032: {value.field_name} scores {value.confidence!r}, below this run's "
            f"declared floor of {policy.floor!r}. A value below the floor is recorded as "
            f"an extraction failure with outcome `confidence_below_threshold` and is not "
            f"persisted — recorded absent rather than stored wrong."
        )


def _write_extracted_values(
    cursor: psycopg.Cursor,
    document_id: str,
    values: Sequence[PreparedValue],
    chunk_ids: Sequence[UUID],
) -> tuple[UUID, ...]:
    """§Write Order step 2 — `INSERT extracted_value`, citations resolved.

    The cited page must resolve to a chunk written at step 1, which is what makes
    step 2 sit where it does. Resolution is by **ordinal**: `chunk_ids[i]` is the
    identifier minted for ordinal *i*, because `_copy_chunks` writes chunks in
    ordinal order and `ingest/chunker.py` assigns contiguous zero-based ordinals.

    Raises:
        WriterError: a value cites an ordinal this document has no chunk for.
            Caught here rather than by the foreign key so the message names the
            value and the ordinal rather than an identifier nobody can trace.
    """
    minted: list[UUID] = []
    for value in values:
        anchor = value.citation.anchor
        if anchor.ordinal >= len(chunk_ids):
            raise WriterError(
                f"FR-029: {document_id} value {value.field_name!r} cites chunk ordinal "
                f"{anchor.ordinal}, but the document wrote {len(chunk_ids)} chunks "
                f"(ordinals 0 to {len(chunk_ids) - 1}). A citation that names no chunk is "
                f"not a weaker citation — it is not one."
            )
        value_id = uuid4()
        minted.append(value_id)
        cursor.execute(
            _EXTRACTED_VALUE_INSERT,
            (
                value_id,
                chunk_ids[anchor.ordinal],
                # Inherited from the anchor chunk and not supplied: the composite
                # foreign key would reject a disagreeing pair, and this makes the
                # disagreement unconstructible rather than merely rejected.
                value.citation.cited_page,
                value.field_name,
                value.value_kind,
                value.value_text,
                value.value_number,
                value.confidence,
                value.citation.provenance_kind,
                value.citation.source_chunk_count,
            ),
        )
    return tuple(minted)


def _write_parse_signals(
    cursor: psycopg.Cursor,
    run_id: UUID | str,
    document_id: str,
    values: Sequence[PreparedValue],
    value_ids: Sequence[UUID],
) -> int:
    """§Write Order step 6 — one `extracted_value_parse_signal` row per value.

    FR-063. Two of the three signals exist in no column anywhere: nothing
    records that a printed label matched a known alternate rather than the
    canonical form, and nothing records that an invocation validated only after
    a repair — `extraction_failure.repair_attempt_count` covers failures, and a
    value that repaired successfully produces no failure row. Without these rows
    SC-026's "recompute every stored confidence from the signals recorded with
    it" reduces to reading the confidence and comparing it with itself.

    Step 6 and not earlier: the row references
    `ingestion_run_extracted_value (extracted_value_id, run_id, document_id)`,
    written at step 5, and the value's own `(id, source_chunk_count)` key from
    step 2. It is written for **every** stored value with no exemption, which is
    what makes the recomputation total rather than a sample.

    Returns:
        The number of signal rows written, which is the number of stored values.
    """
    for value, value_id in zip(values, value_ids, strict=True):
        cursor.execute(
            _PARSE_SIGNAL_INSERT,
            (
                value_id,
                str(run_id),
                document_id,
                value.signals.label_match,
                value.signals.source_chunk_count,
                value.signals.validated_after_repair,
            ),
        )
    return len(value_ids)


# ---------------------------------------------------------------------------
# The write itself — `data-model.md` §Write Order, first-ingest path
# ---------------------------------------------------------------------------

_DOCUMENT_INSERT = """
INSERT INTO document (
    document_id, document_type, project_id, title, source_kind, source_ref,
    issuing_body, generator_id, generation_seed, generated_at, fixture_hashes,
    license_basis, retrieval_date, roster_hash
) VALUES (
    %(document_id)s, %(document_type)s, %(project_id)s, %(title)s, %(source_kind)s,
    %(source_ref)s, %(issuing_body)s, %(generator_id)s, %(generation_seed)s,
    %(generated_at)s, %(fixture_hashes)s, %(license_basis)s, %(retrieval_date)s,
    %(roster_hash)s
)
ON CONFLICT (document_id) DO NOTHING
"""

_ACTIVE_GENERATION = """
SELECT run_id FROM ingestion_run_document
WHERE document_id = %s AND status = 'active'
"""

_GENERATION_INSERT = """
INSERT INTO ingestion_run_document (run_id, document_id, status, input_tuple_digest)
VALUES (%s, %s, 'active', %s)
"""

_RUN_CHUNK_INSERT = """
INSERT INTO ingestion_run_chunk (chunk_id, run_id, document_id)
SELECT chunk_id, %s, %s FROM chunk WHERE document_id = %s
"""

#: §Write Order step 5's second association. Written here rather than left to
#: T070 because the parse-signal row of step 6 targets
#: `uq_ingestion_run_extracted_value__value_generation` through
#: `fk_extracted_value_parse_signal__run_output` — without this row the signal
#: row has no referent and FR-063 is unstorable. T070 adds the third association
#: (`ingestion_run_extraction_failure`) and the corpus-wide anti-join that makes
#: SC-021 a fact rather than a habit.
_RUN_VALUE_INSERT = """
INSERT INTO ingestion_run_extracted_value (extracted_value_id, run_id, document_id)
VALUES (%s, %s, %s)
"""


def _document_parameters(record: DocumentRecord) -> dict[str, object]:
    """`DocumentRecord` as E003's `document` columns.

    `content_hash` and `path` are on the record and in no column: `document`
    carries neither, the manifest is where the hash lives, and FR-065 forbids
    this epic adding a column to a table E003 owns. The manifest hash reaches
    the database through FR-043's per-document input tuple digest instead.
    """
    return {
        "document_id": record.document_id,
        "document_type": record.document_type,
        "project_id": record.project_id,
        "title": record.title,
        "source_kind": record.source_kind,
        "source_ref": record.source_ref,
        "issuing_body": record.issuing_body,
        "generator_id": record.generator_id,
        "generation_seed": record.generation_seed,
        "generated_at": record.generated_at,
        "fixture_hashes": list(record.fixture_hashes) if record.fixture_hashes else None,
        "license_basis": record.license_basis,
        "retrieval_date": record.retrieval_date,
        "roster_hash": record.roster_hash,
    }


def _copy_chunks(
    cursor: psycopg.Cursor,
    prepared: PreparedDocument,
    dimension: int,
) -> tuple[UUID, ...]:
    """§Write Order step 1 — `INSERT chunk` through `cursor.copy()`.

    COPY inside the block is transactional and rolls back with it, so the bulk
    path costs nothing in atomicity. Ordinal 0 is free for this document because
    it has no resident predecessor; that is checked before this runs, not
    assumed here.
    """
    minted: list[UUID] = []
    with cursor.copy(_CHUNK_COPY) as copy:
        copy.set_types(_CHUNK_COPY_TYPES)
        for chunk, vector in zip(prepared.chunking.chunks, prepared.embeddings, strict=True):
            if vector.shape != (dimension,):
                raise WriterError(
                    f"FR-021: {prepared.record.document_id} ordinal {chunk.ordinal} carries a "
                    f"vector of {vector.shape} where the schema publishes {dimension} "
                    f"dimensions; the width is read from `schema_constants` and not assumed"
                )
            chunk_id = uuid4()
            minted.append(chunk_id)
            copy.write_row(
                (
                    chunk_id,
                    chunk.document_id,
                    chunk.document_type,
                    chunk.project_id,
                    chunk.page_number,
                    chunk.ordinal,
                    chunk.spec_section,
                    chunk.heading,
                    None,
                    chunk.body_text,
                    vector,
                    prepared.embedding_model_id,
                    prepared.embedding_model_revision,
                )
            )
    return tuple(minted)


def write_document_generation(
    connection: psycopg.Connection,
    *,
    run_id: UUID | str,
    prepared: PreparedDocument,
    input_tuple_digest: str,
    values: Sequence[PreparedValue] = (),
    fresh_pages: Sequence[ParsedPage] | None = None,
    dimension: int | None = None,
) -> DocumentOutcome:
    """Write one document's first generation, in one transaction (FR-042, FR-054).

    Args:
        connection: an **autocommit** connection, as `connect` returns. A
            non-autocommit connection is refused rather than silently demoting
            this block to a savepoint.
        run_id: the `ingestion_run` this generation belongs to. The run row is
            written by `ingest/runs.py` before the first document.
        prepared: the document, chunked and embedded.
        input_tuple_digest: FR-043's per-document digest, `sha256:<64 hex>`.
        values: the extracted values, each citing a chunk of this document by
            ordinal (FR-029). Empty for a specification, which extraction does
            not reach (FR-022) — and empty is the *recorded* state there rather
            than an inferred one, which `ingest/cli.py` publishes.
        fresh_pages: the containment guard's independent read. Read here when
            not supplied, which is the ordinary path — the caller supplying it
            is the test harness, which needs to hand over a *known* extraction.
        dimension: the schema's published vector width. Read from
            `schema_constants` when not supplied; a caller writing 51 documents
            passes the value it read once.

    Returns:
        The outcome, with the containment population and count the guard
        enumerated.

    Raises:
        WriterError: on a non-autocommit connection, a malformed digest, a
            resident predecessor generation, a containment miss, a value citing
            an ordinal the document has no chunk for, or a document row that
            disagrees with the record. Every one of them leaves the document
            with zero rows.

    The statement order is `data-model.md` §Write Order, first-ingest path:
    steps 0a–0g are the promotion's removal and are skipped entirely when no
    predecessor is resident (and refused when one is — see the module
    docstring), then 0h, step 1, step 2, then step 5's chunk association. The
    containment guard runs after step 1 and before the block closes, which is
    what makes "never committed" true rather than "detected afterwards".

    Step 2 sits after step 1 because a value's cited page has to resolve to a
    chunk this transaction has already written — `fk_extracted_value__chunk_page`
    is a composite foreign key against `chunk (chunk_id, page_number)`, so the
    ordering is the constraint's rather than a preference. Step 6's parse-signal
    rows sit after step 5's value association for the same kind of reason: they
    reference `ingestion_run_extracted_value`, not `extracted_value` directly.
    Steps 3 and 4 — contributing chunks and failure rows — and step 6's
    line-item rows attach at the same seams in T066, T061 and T047.
    """
    if not connection.autocommit:
        raise WriterError(
            "FR-042: the ingestion connection must be autocommit. On a non-autocommit "
            "connection psycopg opens one implicit transaction for the whole run and "
            "`with conn.transaction()` becomes a savepoint inside it, so a late failure "
            "would discard every document already written."
        )
    if not INPUT_TUPLE_DIGEST_PATTERN.fullmatch(input_tuple_digest):
        raise WriterError(
            f"{prepared.record.document_id}: input_tuple_digest {input_tuple_digest!r} does "
            f"not match {INPUT_TUPLE_DIGEST_PATTERN.pattern}, which "
            f"`ck_ingestion_run_document__tuple_digest_format` refuses"
        )

    record = prepared.record
    width = vector_dimension(connection) if dimension is None else dimension

    # FR-046 / SC-026: the run's own floor and weights, read from its row before
    # anything is written. A run whose row is absent fails here rather than
    # scoring a document against a policy nobody recorded — which is what makes
    # "the policy is recorded before the first document" (FR-032) enforced
    # rather than sequenced. Read only when there is a score to check, so a
    # specification — which extraction does not reach (FR-022) — needs no run
    # policy to be chunked.
    policy = read_confidence_policy(connection, run_id) if values else None
    if policy is not None:
        for value in values:
            check_confidence_agrees(value, policy)
    # Read **before** the transaction opens and from the document's own bytes:
    # this is FR-010's independent extraction, and reading it inside the block
    # would not make it any fresher while making the transaction longer.
    pages = tuple(fresh_pages) if fresh_pages is not None else read_pages(record.path)

    # The transaction is the outer context and the cursor the inner one, in one
    # statement: the cursor closes first on the way out and the transaction
    # commits after it, which is the order both need.
    with connection.transaction(), connection.cursor() as cursor:
        # 0a–0g: the predecessor's removal. Not attempted — the ingestion job
        # holds `DELETE` on none of the tables involved, and the promotion is an
        # operator procedure under the schema-owning role ({SAD:ADR-0020},
        # `data-model.md` §Operator Procedures 3).
        cursor.execute(_ACTIVE_GENERATION, (record.document_id,))
        resident = cursor.fetchone()
        if resident is not None:
            raise WriterError(
                f"{record.document_id} already has an active generation under run "
                f"{resident[0]}. Exactly one generation's rows are resident per document "
                f"({{SAD:ADR-0020}}); replacing it is the promotion procedure, which "
                f"removes the predecessor under the schema-owning role and is not "
                f"reachable from the ingestion job."
            )

        # E003's `document` row is the composite-FK target of both the
        # generation row and every chunk, so it precedes step 0h. Never an
        # update: a document whose manifest content hash has moved is a
        # re-ingest, and the check above has already refused that. `DO NOTHING`
        # therefore covers exactly one case — a row left behind by a promotion
        # that removed its generation.
        cursor.execute(_DOCUMENT_INSERT, _document_parameters(record))

        # 0h — every association's FK targets this row.
        cursor.execute(_GENERATION_INSERT, (str(run_id), record.document_id, input_tuple_digest))

        # 1 — the chunks.
        minted = _copy_chunks(cursor, prepared, width)

        # FR-010, inside the transaction and before it commits.
        containment = verify_page_containment(record.document_id, prepared.chunking.chunks, pages)
        if not containment.holds:
            raise WriterError(
                f"FR-010: {len(containment.misses)} of {containment.count} chunks of "
                f"{record.document_id} are not present in a fresh extraction of the page "
                f"they name, so the document is not committed. First: "
                f"{containment.misses[0]}"
            )

        # 2 — the extracted values, citing chunks written at step 1.
        written_values = _write_extracted_values(cursor, record.document_id, values, minted)

        # 5 — the run-output associations. Chunks first, then the values written
        # at step 2; step 6's rows target the value association and not
        # `extracted_value` directly, which is what fixes this order.
        cursor.execute(_RUN_CHUNK_INSERT, (str(run_id), record.document_id, record.document_id))
        for value_id in written_values:
            cursor.execute(_RUN_VALUE_INSERT, (value_id, str(run_id), record.document_id))

        # 6 — FR-063's parse-signal rows, one per stored value.
        _write_parse_signals(cursor, run_id, record.document_id, values, written_values)

    return DocumentOutcome(
        document_id=record.document_id,
        chunks_written=len(minted),
        containment=containment,
        values_written=len(written_values),
    )


def write_generations(
    connection: psycopg.Connection,
    *,
    run_id: UUID | str,
    documents: Iterable[tuple[PreparedDocument, str]],
) -> Iterator[DocumentOutcome]:
    """Write each document in its own transaction, continuing past a failure.

    Yields one `DocumentOutcome` per document, in the order given, so a caller
    can build FR-073's disposition ledger without re-deriving what happened.

    **The handler catches outside `write_document_generation`, and therefore
    outside its `with conn.transaction()` block** (HINT-002). Catching inside
    would roll back to a savepoint and let the outer block commit a document
    that failed halfway through. The consequence is the one `data-model.md`
    §Write Order states: an abort at document *k* leaves 1..*k*−1 committed and
    durable and document *k* entirely absent.

    Writing the **run-level** failure that explains an aborted document is
    `ingest/runs.py`'s, in a fresh transaction after the rollback: a row written
    inside *d*'s transaction to explain why *d* failed rolls back with it, and
    an `extraction_failure` row cannot carry it either — its `source_chunk_id`
    is NOT NULL against a chunk the rollback has just removed.
    """
    dimension = vector_dimension(connection)
    for prepared, digest in documents:
        try:
            yield write_document_generation(
                connection,
                run_id=run_id,
                prepared=prepared,
                input_tuple_digest=digest,
                dimension=dimension,
            )
        except (WriterError, psycopg.Error) as exc:
            yield DocumentOutcome(
                document_id=prepared.record.document_id,
                chunks_written=0,
                containment=None,
                error=f"{type(exc).__name__}: {exc}",
            )
