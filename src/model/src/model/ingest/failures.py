"""Extraction failures: the closed seven, the five required fields, no value.

FR-034 / FR-035 / FR-036 / FR-037. A failed extraction is representable *only*
as an `extraction_failure` row (E003's TR-019, TR-061), and this module is where
one is built. Three rules hold here, and each is a type rather than a review
step:

1. **The outcome comes from a closed set of seven and no eighth is introduced**
   (FR-034). `FAILURE_OUTCOMES` restates `ck_extraction_failure__outcome`, and a
   value outside it is refused at construction rather than by the database six
   statements later. Where the two disagree the schema governs
   ({SAD:ADR-0017}); `src/model/tests/ingest/test_failures.py` reads the
   revision's own `CHECK` and compares, so they cannot drift quietly.
2. **Five fields are required on every failure** (FR-035): the source chunk, the
   attempted page, the field, the repair attempt count, and a diagnostic detail.
   None has a default. The chunk and the page travel together as one value for
   the same reason a citation does — a page supplied separately from its chunk
   is a page that can disagree with it, and `fk_extraction_failure__chunk_page`
   is what refuses the pair on write.
3. **A failure carries no value and no confidence** (FR-036). There is no field
   for either and no column for either: `FAILURE_COLUMNS` is what the row is
   built from, and `FORBIDDEN_COLUMNS` names what may never join it. A failure
   with a value attached is a value that was stored while being reported as
   absent, which is the invisible-corruption class Principle III targets.

**A field the document does not print is `no_value_found`, once per document**
(FR-037, FR-058). Not defaulted, not inferred, and not omitted: an omission is
indistinguishable from a field nobody attempted. Recorded once per *document*
rather than once per chunk, because attempting ten fields on every chunk of a
five-page transmittal would otherwise produce a failure table dominated by
structural absence. The source chunk it names is the **lowest-ordinal chunk the
field was attempted on**, which is a stated convention and deterministic under
FR-015 — deliberately not a claim about where the field would have been printed,
which nothing knows.

**What is not here.** Run-level failures (FR-056) — a missing fixture, an
unreachable provider — are not `extraction_failure` rows at all: that table's
`source_chunk_id` is NOT NULL against a chunk a rollback has just removed, so
the row is unstorable. They are recorded on `ingestion_run` and are
`ingest/runs.py`'s. This module imports neither `gateway` nor `model.llm`; the
outcome a schema refusal takes is decided by the error that reports it and
arrives here as a string.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, fields
from typing import Final

__all__ = [
    "FAILURE_COLUMNS",
    "FAILURE_OUTCOMES",
    "FORBIDDEN_COLUMNS",
    "OUTCOME_NO_VALUE_FOUND",
    "REQUIRED_FIELDS",
    "AttemptedChunk",
    "ExtractionFailure",
    "FailureError",
    "absent_field_records",
    "outcome_counts",
]


class FailureError(ValueError):
    """A failure record cannot be built as described.

    One type for every refusal. Each of them means the same thing: this row is
    not written, because writing it would record a failure that explains
    nothing or that claims an outcome the schema does not admit.
    """


#: `ck_extraction_failure__outcome`'s closed seven, in the order revision `0006`
#: declares them. FR-034 enumerates them rather than referring to them by count
#: and owner, so they are written out here too and compared against the revision
#: by test — a count alone cannot catch a substitution.
OUTCOME_NO_VALUE_FOUND: Final[str] = "no_value_found"
FAILURE_OUTCOMES: Final[tuple[str, ...]] = (
    OUTCOME_NO_VALUE_FOUND,
    "unparseable_value",
    "type_coercion_failed",
    "schema_violation",
    "missing_citation",
    "confidence_below_threshold",
    "repair_budget_exhausted",
)

#: FR-035's five, named as the requirement names them. Held as data rather than
#: only as constructor parameters so the report and the tests can assert over
#: the list instead of over five separate spellings of it.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "source_chunk",
    "attempted_page",
    "field_name",
    "repair_attempt_count",
    "detail",
)

#: The `extraction_failure` columns a record is written to. `failed_at` carries
#: a default and `extraction_failure_id` is minted inside the transaction, so
#: neither appears.
FAILURE_COLUMNS: Final[tuple[str, ...]] = (
    "source_chunk_id",
    "attempted_page",
    "field_name",
    "outcome",
    "repair_attempt_count",
    "detail",
)

#: FR-036, as a list the code checks rather than a sentence a reviewer checks.
#: A failure row carrying any of these would be a value stored while being
#: reported as absent — and nothing downstream could tell it from a real one.
FORBIDDEN_COLUMNS: Final[frozenset[str]] = frozenset(
    {"value_text", "value_number", "value_kind", "confidence"}
)


@dataclass(frozen=True)
class AttemptedChunk:
    """The chunk a failed extraction was attempted on, with its page.

    **By ordinal, not by identifier**, for the same reason `writer.CitedChunk`
    is: chunk identifiers are minted inside the document's transaction at write
    order step 1, so nothing upstream of the write can know one. The ordinal is
    assigned by the chunker, is unique within the document, and is the handle a
    failure carries from extraction to the write.

    The page travels with the ordinal so the two cannot be supplied separately
    and disagree — `fk_extraction_failure__chunk_page` is a composite foreign
    key against `chunk (chunk_id, page_number)`, and a failure is as traceable
    as a success.
    """

    ordinal: int
    page_number: int

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise FailureError(f"chunk ordinals are zero-based; got {self.ordinal}")
        if self.page_number < 1:
            raise FailureError(
                f"page numbers are one-based and "
                f"`ck_extraction_failure__page_positive` refuses anything below 1; got "
                f"{self.page_number}"
            )


@dataclass(frozen=True)
class ExtractionFailure:
    """One `extraction_failure` row, before its identifiers exist.

    Every field is required and none is defaulted, which is FR-035 stated as a
    type. There is no `value_text`, no `value_number` and no `confidence` field,
    which is FR-036 stated the same way: a failure with a value attached is not
    representable rather than merely discouraged.
    """

    source_chunk: AttemptedChunk
    field_name: str
    outcome: str
    repair_attempt_count: int
    detail: str

    def __post_init__(self) -> None:
        if self.outcome not in FAILURE_OUTCOMES:
            raise FailureError(
                f"outcome {self.outcome!r} is outside the closed set of seven "
                f"{list(FAILURE_OUTCOMES)}, which `ck_extraction_failure__outcome` fixes. "
                f"FR-034: no new outcome value is introduced — an eighth is a migration "
                f"and an amendment, not a new label."
            )
        if not self.field_name.strip():
            raise FailureError(
                "FR-035: a failure names the field it was attempted for. "
                "`fk_extraction_failure__field` resolves it against the seeded vocabulary."
            )
        if self.repair_attempt_count < 0:
            raise FailureError(
                f"repair_attempt_count is {self.repair_attempt_count}, which "
                f"`ck_extraction_failure__repair_count_non_negative` refuses. Zero is the "
                f"ordinary case: a failure recorded without any repair attempt."
            )
        if not self.detail.strip():
            raise FailureError(
                f"FR-035: the failure of {self.field_name!r} carries a blank diagnostic "
                f"detail, which `ck_extraction_failure__detail_present` refuses. A failure "
                f"record that explains nothing defeats the point of recording it."
            )

    @property
    def attempted_page(self) -> int:
        """FR-035's second field, inherited from the chunk and never supplied."""
        return self.source_chunk.page_number

    def row_values(self, source_chunk_id: object) -> tuple[object, ...]:
        """The row, in `FAILURE_COLUMNS` order.

        Args:
            source_chunk_id: the identifier minted for `source_chunk.ordinal`
                inside the document's transaction. Resolved by the caller
                because only the transaction knows it.

        Returns:
            One tuple per column, with the page taken from the chunk rather than
            from a separate argument — so the pair the composite foreign key
            checks cannot be assembled out of two disagreeing halves.
        """
        return (
            source_chunk_id,
            self.attempted_page,
            self.field_name,
            self.outcome,
            self.repair_attempt_count,
            self.detail,
        )


def _assert_no_value_columns() -> None:
    """FR-036, checked at import rather than asserted in prose.

    Both directions: the columns a record is written to name none of the value
    or confidence columns, and the record type itself declares no field by any
    of those names. A field added later under one of those spellings fails the
    import of this module, which is the loudest place available.
    """
    written = set(FAILURE_COLUMNS) & FORBIDDEN_COLUMNS
    if written:
        raise FailureError(
            f"FR-036: {sorted(written)} appear among the columns a failure row is written "
            f"to. A failure carries no value and no confidence."
        )
    declared = {entry.name for entry in fields(ExtractionFailure)} & FORBIDDEN_COLUMNS
    if declared:
        raise FailureError(
            f"FR-036: {sorted(declared)} are declared on `ExtractionFailure`. A failure "
            f"with a value attached is a value stored while being reported as absent."
        )


_assert_no_value_columns()


#: FR-058's stated convention, held as one string so the detail a record carries
#: and the sentence the report publishes cannot become two paraphrases.
ABSENCE_CONVENTION: Final[str] = (
    "The field was attempted on every chunk of this document and printed on none of "
    "them, so it is recorded once for the document (FR-037, FR-058) rather than once "
    "per chunk. The source chunk named is the lowest-ordinal chunk the field was "
    "attempted on and that chunk's page — a stated convention, deterministic under "
    "FR-015's contiguous ordinals, and deliberately not a claim about where the field "
    "would have been printed."
)


def absent_field_records(
    *,
    document_id: str,
    attempted_fields: Sequence[str],
    attempted_chunks: Sequence[AttemptedChunk],
    fields_with_values: Collection[str],
) -> tuple[ExtractionFailure, ...]:
    """One `no_value_found` per attempted field the document did not print.

    Args:
        document_id: the document, for the diagnostic detail.
        attempted_fields: the declared transmittal subset this run attempted,
            in declared order (FR-058). Duplicates are refused — a field
            attempted twice would be recorded absent twice.
        attempted_chunks: every chunk of the document extraction was attempted
            on. The whole set, not the first: the record names the lowest
            ordinal among them, and a caller that pre-selected one would have
            made the convention its own rather than this function's.
        fields_with_values: the field names that produced at least one stored
            value anywhere in the document. Membership decides absence, so a
            field found on the last page is not reported absent for the first.

    Returns:
        One record per absent field, in `attempted_fields` order, each with
        outcome `no_value_found`, `repair_attempt_count` 0, and the convention
        stated in its detail.

    Raises:
        FailureError: no field was attempted, no chunk was attempted, a field is
            named twice, or a field reported as having a value was never
            attempted. Every one of them is refused rather than defaulted: a
            zero-chunk document has no source chunk to name and a record naming
            none is unstorable, and silently returning nothing would report a
            document with no absences — which is the shape of a clean run.

    **Absence is recorded, never inferred.** FR-037's whole point is that a field
    the document does not print produces a row rather than a gap: a gap is
    indistinguishable from a field nobody attempted, and the two have opposite
    meanings for FR-060's recall denominator.
    """
    if not attempted_fields:
        raise FailureError(
            f"FR-037: {document_id} attempted zero fields, so every field is absent and "
            f"none is recorded. An empty attempt set is a vocabulary or configuration "
            f"failure, not a document with nothing on it."
        )
    if len(set(attempted_fields)) != len(attempted_fields):
        raise FailureError(
            f"FR-037: {document_id}'s attempted field list repeats a term "
            f"({sorted(attempted_fields)}), so an absent field would be recorded twice — "
            f"and FR-058 records it once per document."
        )
    if not attempted_chunks:
        raise FailureError(
            f"FR-037: {document_id} has no attempted chunk, so a `no_value_found` record "
            f"could name no source chunk — `extraction_failure.source_chunk_id` is NOT "
            f"NULL. A document that produced no chunk is a run-level failure, not a "
            f"document with ten absent fields."
        )
    unattempted = sorted(set(fields_with_values) - set(attempted_fields))
    if unattempted:
        raise FailureError(
            f"FR-069: {unattempted} produced values on {document_id} but are not in the "
            f"attempted set. An attempt ledger with a value nothing attempted does not "
            f"reconcile."
        )

    anchor = min(attempted_chunks, key=lambda chunk: chunk.ordinal)
    found = set(fields_with_values)
    return tuple(
        ExtractionFailure(
            source_chunk=anchor,
            field_name=name,
            outcome=OUTCOME_NO_VALUE_FOUND,
            repair_attempt_count=0,
            detail=f"{name} is not printed on {document_id}. {ABSENCE_CONVENTION}",
        )
        for name in attempted_fields
        if name not in found
    )


def outcome_counts(failures: Sequence[ExtractionFailure]) -> dict[str, int]:
    """The failure count for **each** of the seven, zeros included (FR-034).

    Returns:
        A mapping keyed by every member of `FAILURE_OUTCOMES` in its declared
        order, so an outcome no failure took is published as a `0` rather than
        as an absent row. An empty input therefore yields seven zeros and not an
        empty table — which is the difference between "no failures took this
        outcome" and "this outcome was forgotten".
    """
    counts = dict.fromkeys(FAILURE_OUTCOMES, 0)
    for failure in failures:
        counts[failure.outcome] += 1
    return counts
