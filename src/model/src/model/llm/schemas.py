"""The output schema, the vocabulary bound, and the declared transmittal subset.

FR-024 / FR-058. Two rules live here and nowhere else:

**A field name comes from the seeded vocabulary, and only from a term unretired
at run time** (FR-024). The 22 terms revision `0005` seeds are transcribed below
with their declared `value_kind`, and `src/model/tests/llm/test_extraction.py`
compares the transcription against the revision's own `INSERT` so the two cannot
drift. The *unretired* half cannot be answered from a constant — `retired_at` is
a column, retirement is advisory at the storage boundary (E003 gap G-7), and
this epic's obligation is to filter on it at run time. So the caller supplies
the set of unretired names it read from `field_vocabulary` and `attempted_terms`
intersects; a subset term the database does not offer is a refusal rather than a
silently narrowed attempt.

**Only the declared transmittal subset is attempted** (FR-058, AD-009). Twelve of
the twenty-two cannot appear on a transmittal at all — a purchase-order number
is printed on a purchase order, a unit price on a quotation — and attempting
them per chunk would make the failure table *chunks × 22*, dominated by
structural absences, and buy a dozen impossible model calls per chunk. The
subset is **declared**, with each exclusion's reason stated beside it, rather
than discovered from an empty result: `EXCLUDED_TERMS` is the record FR-058
requires. Publishing a printed field that falls outside the subset as
unattempted-but-printed, rather than absorbing it into the miss total, is
`ingest/reference.py`'s `printed_without_term` and report item 14 — it works
over the generator's printed keys, which is the population that includes the
seven keys the vocabulary has no term for at all.

**The bound is applied in code, not in the schema, and that is deliberate.** A
`Literal` over the attempted names would make one mis-named field fail the whole
invocation through the gateway's validator — an invocation-level error. FR-024
requires the opposite: the refusal is recorded as an extraction failure with
outcome `schema_violation`, which is a *per-field* record. So `ChunkExtraction`
bounds the shape and `bound_field_names` bounds the vocabulary, and the caller
learns which individual values were refused instead of losing the chunk.

Keeping the schema static has a second consequence worth stating: the gateway's
fixture key digests the output schema (TR-038), so a per-run schema class would
move every fixture key whenever a term was retired. The attempted field list
reaches the key through the **prompt** instead, which is a hashed request field —
so a changed subset still misses, as FR-045 requires, without the schema digest
becoming a function of database state.

Nothing here imports `gateway` — that is `extraction.py`'s alone — and nothing
here imports `model.compute`, which the committed forbidden contract refuses for
every module under `model.llm`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DOCUMENT_SCOPE",
    "EXCLUDED_TERMS",
    "LINE_ITEM_SCOPE",
    "SEEDED_VOCABULARY",
    "TRANSMITTAL_FIELD_SUBSET",
    "VALUE_KINDS",
    "ChunkExtraction",
    "ExtractedField",
    "FieldTerm",
    "SchemaError",
    "attempted_terms",
    "output_schema_digest",
    "bound_field_names",
    "term",
]


class SchemaError(ValueError):
    """Raised when a field name or a vocabulary set cannot be honoured.

    One type for every failure here, as the rest of this feature uses: each of
    them means the same thing to a caller — this extraction is not attempted
    under a vocabulary the run cannot vouch for.
    """


#: E003's `ck_field_vocabulary__value_kind`, restated so a transcription defect
#: below fails at import rather than at the first insert.
VALUE_KINDS: frozenset[str] = frozenset({"text", "number", "date"})

#: FR-059's two groups. A value a transmittal prints once for the whole document
#: belongs to item ordinal 0; a value printed against a numbered item belongs to
#: that item. The scope is declared per term rather than guessed per value,
#: because ordinal 0 is a *named group* and not a sentinel to pattern-match.
DOCUMENT_SCOPE = "document"
LINE_ITEM_SCOPE = "line_item"


@dataclass(frozen=True)
class FieldTerm:
    """One seeded vocabulary term, as this epic needs to read it.

    `label` and `description` are E003's own, transcribed from revision `0005`:
    they are what the prompt shows the model, so the model is told what a term
    means by the schema that will store it rather than by the generator that
    printed it. The generator's per-vendor labels are deliberately **not** here —
    a prompt keyed to them would be reading the layout templates.
    """

    name: str
    value_kind: str
    label: str
    description: str
    scope: str = LINE_ITEM_SCOPE

    def __post_init__(self) -> None:
        if self.value_kind not in VALUE_KINDS:
            raise SchemaError(
                f"{self.name}: value_kind {self.value_kind!r} is outside {sorted(VALUE_KINDS)}, "
                f"which `ck_field_vocabulary__value_kind` refuses"
            )
        if self.scope not in {DOCUMENT_SCOPE, LINE_ITEM_SCOPE}:
            raise SchemaError(
                f"{self.name}: scope {self.scope!r} is outside "
                f"{{{DOCUMENT_SCOPE!r}, {LINE_ITEM_SCOPE!r}}} (FR-059)"
            )


#: Revision `0005`'s 22 terms, in the order it seeds them, with the `scope` this
#: epic adds. Order is preserved so a reader can diff this against the migration
#: line for line; `test_extraction.py` does exactly that rather than trusting it.
SEEDED_VOCABULARY: tuple[FieldTerm, ...] = (
    FieldTerm(
        "manufacturer",
        "text",
        "Manufacturer",
        "Manufacturer or brand named for the material item.",
    ),
    FieldTerm(
        "part_number",
        "text",
        "Part Number",
        "Vendor or manufacturer catalogue number as printed.",
    ),
    FieldTerm(
        "model_number",
        "text",
        "Model Number",
        "Model designation where distinct from the part number.",
    ),
    FieldTerm(
        "product_description",
        "text",
        "Product Description",
        "Free-text description of the material item.",
    ),
    FieldTerm(
        "specification_section",
        "text",
        "Specification Section",
        "MasterFormat division and section reference.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "material_category",
        "text",
        "Material Category",
        "Trade-level grouping of the item.",
    ),
    FieldTerm(
        "finish_or_grade",
        "text",
        "Finish or Grade",
        "Surface finish, alloy, or material grade.",
    ),
    FieldTerm(
        "compliance_standard",
        "text",
        "Compliance Standard",
        "Referenced standard the item must satisfy (cited, never reproduced).",
    ),
    FieldTerm("quantity", "number", "Quantity", "Ordered or specified count."),
    FieldTerm(
        "unit_of_measure",
        "text",
        "Unit of Measure",
        "Unit the quantity is expressed in.",
    ),
    FieldTerm(
        "unit_price",
        "number",
        "Unit Price",
        "Price per unit as stated on the source document.",
    ),
    FieldTerm(
        "extended_price",
        "number",
        "Extended Price",
        "Line total as stated on the source document.",
    ),
    FieldTerm(
        "quoted_lead_time_days",
        "number",
        "Quoted Lead Time (days)",
        "The single optimistic integer this product replaces with a distribution.",
    ),
    FieldTerm(
        "warranty_period_months",
        "number",
        "Warranty Period (months)",
        "Stated warranty duration.",
    ),
    FieldTerm(
        "submittal_number",
        "text",
        "Submittal Number",
        "Submittal register identifier.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "submittal_status",
        "text",
        "Submittal Status",
        "Review outcome as stated on the submittal.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "submittal_date",
        "date",
        "Submittal Date",
        "Date the submittal was transmitted, ISO-8601 in `value_text`.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "approval_date",
        "date",
        "Approval Date",
        "Date review was completed, ISO-8601 in `value_text`.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "purchase_order_number",
        "text",
        "Purchase Order Number",
        "Purchase order identifier as printed.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "order_date",
        "date",
        "Order Date",
        "Date the order was placed, ISO-8601 in `value_text`.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "promised_delivery_date",
        "date",
        "Promised Delivery Date",
        "Vendor-stated delivery date, ISO-8601 in `value_text`.",
        DOCUMENT_SCOPE,
    ),
    FieldTerm(
        "required_on_site_date",
        "date",
        "Required On-Site Date",
        "Need-by date as stated on the source document.",
        DOCUMENT_SCOPE,
    ),
)

_BY_NAME: Mapping[str, FieldTerm] = MappingProxyType({t.name: t for t in SEEDED_VOCABULARY})


def term(name: str) -> FieldTerm:
    """The seeded term called `name`, or a refusal (FR-024).

    Raises:
        SchemaError: `name` is outside the seeded vocabulary. Never widened at
            run time — a vocabulary that grows to fit whatever a model returned
            is not a closed set, and FR-024 says so in as many words.
    """
    found = _BY_NAME.get(name)
    if found is None:
        raise SchemaError(
            f"{name!r} is not one of the {len(SEEDED_VOCABULARY)} seeded vocabulary terms "
            f"(FR-024). The vocabulary is not widened at run time; a value naming a term "
            f"outside it is refused and recorded as an extraction failure with outcome "
            f"`schema_violation`."
        )
    return found


#: FR-058's declared subset: the ten seeded terms the corpus generator can print
#: on a transmittal. Declared here, before any extraction runs, so "the subset
#: covers every term the generator can print" is a claim fixed in advance rather
#: than a shape read off whatever the run happened to return.
TRANSMITTAL_FIELD_SUBSET: tuple[FieldTerm, ...] = (
    term("submittal_number"),
    term("submittal_status"),
    term("submittal_date"),
    term("approval_date"),
    term("specification_section"),
    term("material_category"),
    term("product_description"),
    term("manufacturer"),
    term("part_number"),
    term("quantity"),
)

#: The other twelve, each with the reason it cannot appear on a transmittal.
#: **Recorded rather than implied** (FR-058): a term absent from the subset with
#: no stated reason is indistinguishable from one forgotten, and the count of
#: fields printed but not attempted is only meaningful beside it.
EXCLUDED_TERMS: Mapping[str, str] = MappingProxyType(
    {
        "model_number": (
            "the transmittal prints a part number and a description; no separate model "
            "designation appears in any per-vendor layout"
        ),
        "finish_or_grade": (
            "a finish or alloy grade is a datasheet or specification fact, not a "
            "transmittal register field"
        ),
        "compliance_standard": (
            "standards are cited by the governing specification; the transmittal names "
            "the specification section instead"
        ),
        "unit_of_measure": "quantities are printed as bare counts, with no unit column",
        "unit_price": "price is commercial and appears on a quotation or purchase order",
        "extended_price": "as `unit_price` — a line total belongs to the priced document",
        "quoted_lead_time_days": "lead time is quoted by a vendor, not transmitted for review",
        "warranty_period_months": "warranty duration is a datasheet or contract fact",
        "purchase_order_number": "a purchase order is a different document kind entirely",
        "order_date": "as `purchase_order_number` — an ordering fact, printed downstream",
        "promised_delivery_date": "a vendor delivery promise, printed on the order acknowledgement",
        "required_on_site_date": "a schedule fact, held in the programme rather than the submittal",
    }
)


def _validate_declaration() -> None:
    """The subset and the exclusions partition the vocabulary — checked at import.

    FR-058's "the subset MUST cover every vocabulary term the generator can print
    on a transmittal" cannot be checked from inside this module; what *can* be
    checked, and is, is that every seeded term is either attempted or excluded
    with a stated reason. A term in neither list would be silently unattempted
    and silently unreported, which is the failure mode the declaration exists to
    prevent.
    """
    attempted = {entry.name for entry in TRANSMITTAL_FIELD_SUBSET}
    excluded = set(EXCLUDED_TERMS)
    overlap = attempted & excluded
    if overlap:
        raise SchemaError(
            f"FR-058: {sorted(overlap)} are declared both attempted and excluded; a term "
            f"is one or the other"
        )
    unclassified = set(_BY_NAME) - attempted - excluded
    if unclassified:
        raise SchemaError(
            f"FR-058: {sorted(unclassified)} are seeded vocabulary terms that are neither "
            f"in the declared transmittal subset nor in the recorded exclusions. An "
            f"unclassified term is unattempted and unreported at once."
        )
    unknown = excluded - set(_BY_NAME)
    if unknown:
        raise SchemaError(f"FR-058: {sorted(unknown)} are excluded but are not seeded terms")


_validate_declaration()


def attempted_terms(unretired: Collection[str]) -> tuple[FieldTerm, ...]:
    """The declared subset, filtered to terms unretired at run time (FR-024).

    Args:
        unretired: every `field_vocabulary.field_name` whose `retired_at` is
            null, read from the database by the caller. Supplied rather than
            queried here because `model.llm` holds no connection and should not:
            the run-time fact belongs to the orchestrator, and passing it in
            keeps this module a pure declaration.

    Returns:
        The attempted terms, in the declared order.

    Raises:
        SchemaError: `unretired` is empty, or names a term outside the seeded
            vocabulary. An empty set would silently attempt nothing and report
            "no values found" for every document — the shape of a total failure
            wearing the shape of a clean run.
    """
    offered = set(unretired)
    if not offered:
        raise SchemaError(
            "FR-024: the run offered zero unretired vocabulary terms, so every field "
            "would be unattempted and every document would report no values found. An "
            "empty vocabulary is a migration or connection failure, not a narrow run."
        )
    unknown = sorted(offered - set(_BY_NAME))
    if unknown:
        raise SchemaError(
            f"FR-024: {unknown} were offered as unretired vocabulary terms but are not "
            f"among the {len(SEEDED_VOCABULARY)} this epic knows. The vocabulary is not "
            f"widened at run time."
        )
    return tuple(entry for entry in TRANSMITTAL_FIELD_SUBSET if entry.name in offered)


def bound_field_names(
    names: Iterable[str], attempted: Collection[FieldTerm]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split returned field names into the accepted and the refused (FR-024).

    Returned as a pair rather than raising on the first offender, because the
    refusal is a **per-field** record: FR-024 routes it to an extraction failure
    with outcome `schema_violation`, so a caller needs every refused name to
    write one row each, and needs the accepted ones to keep the rest of the
    chunk's work.

    Order is preserved and duplicates are kept: two returned values naming the
    same field are two attempts, and collapsing them here would lose one from
    FR-069's ledger.
    """
    admissible = {entry.name for entry in attempted}
    accepted: list[str] = []
    refused: list[str] = []
    for name in names:
        (accepted if name in admissible else refused).append(name)
    return tuple(accepted), tuple(refused)


# FR-058's escape hatch — the printed fields nobody attempted — is
# `ingest/reference.py`'s `printed_without_term`, published through report item
# 14. A `printed_but_unattempted` here took a set of *vocabulary term names* and
# subtracted the attempted subset, which is the narrow reading QC F6 corrected:
# the population FR-058 most needs to reach is the seven printed generator keys
# the vocabulary has no term for at all, and those never appear in this
# function's input, so it returned () on a corpus with 170 of them. It had no
# caller, and keeping a superseded and wrong second answer beside the right one
# is worse than having only the right one.


class ExtractedField(BaseModel):
    """One value the model reports, as printed.

    **No coerced form and no confidence here.** The typed numeric or ISO date is
    `model.compute.coerce`'s and the score is `model.compute.confidence`'s, both
    applied by the orchestrator *after* this returns — the forbidden contract
    means this package cannot reach either, and that is the point rather than an
    inconvenience: a model that reported its own typed value would be doing the
    computation the boundary reserves for deterministic code (FR-049), and one
    that reported its own confidence would be reporting a number nothing could
    recompute (FR-031).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str = Field(min_length=1)
    printed_label: str = Field(
        min_length=1,
        description=(
            "The label text exactly as printed beside the value. Reported rather "
            "than classified: whether it is the canonical label or a known "
            "alternate is decided by deterministic code against the committed "
            "field-label vocabulary, and is FR-063's `label_match` signal."
        ),
    )
    value_text: str = Field(
        min_length=1,
        description=(
            "The value exactly as printed, with no normalization, cleaning, or "
            "canonicalization (FR-027). This is the evidence the citation points "
            "at (FR-062)."
        ),
    )
    item_ordinal: int = Field(
        default=0,
        ge=0,
        description=(
            "The printed item number this value belongs to, or 0 for a value the "
            "document prints once for the whole document (FR-059). Zero is a "
            "declared group, not a sentinel."
        ),
    )


class ChunkExtraction(BaseModel):
    """Every value one invocation read out of one chunk.

    `extra="forbid"` on both models is what makes the JSON contract in the
    prompt enforceable: a model that invents a key fails validation and spends
    the single repair attempt, rather than having the key silently dropped and
    the invocation recorded as `valid`.

    An empty `values` list is legitimate and means what it says — this chunk
    printed none of the attempted fields. It is not an error, and FR-037 records
    it as `no_value_found` once per document rather than once per chunk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[ExtractedField, ...] = ()


def output_schema_digest() -> str:
    """`ingestion_run.extraction_schema_digest` — the submitted output schema.

    Returns:
        `sha256:<64 hex>` over `ChunkExtraction`'s JSON Schema, in the form
        `ck_ingestion_run__extraction_schema_digest_format` admits.

    **Over the generated JSON Schema rather than over the source text**, because
    the schema is what the gateway submits and what validation refuses against.
    A digest of the module's source would move when a docstring changed and
    would sit still when a constraint moved through an imported default — the
    two failure directions this value exists to exclude, since FR-045 makes a
    changed schema constraint resolve to a fixture miss.

    Serialized with sorted keys and a fixed separator so the digest is a
    function of the schema and not of pydantic's key ordering.
    """
    schema = ChunkExtraction.model_json_schema()
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
