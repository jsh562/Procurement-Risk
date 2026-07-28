"""The ingestion report: its closed content list, its labels, and its figures.

FR-068 / FR-071 / FR-072, and the US1 sections FR-003, FR-009, FR-011, FR-018,
FR-053 and FR-061 oblige. The report is **one committed artifact** at
`specs/00006-document-ingestion-and-extraction/ingestion-report.md`, one per
repository rather than one per run, regenerated in full by any run that writes
or replaces a generation.

**The content list is closed, and the builder enforces it in both directions**
(FR-071). An item in the report but absent from the list is a defect in the
list; a list entry with nothing under it is a defect in the report. So
`build_report` refuses a section whose item number is not on the list *and*
refuses to emit a report with an item missing or with an empty body. A short
report is not a smaller report — it is a report making a claim it did not
publish the basis for.

**Every figure is a labelled record, never a bare number** (FR-072). A `Figure`
carries the run it was computed under, the generation set it ranges over, its
kind — census, sampled, or descriptive — its counting unit, and its layer.
There is no constructor that omits them, which is what keeps the labelling from
being a documentation convention.

**A total check publishes what it enumerated, and an empty population fails**
(FR-068). `TotalCheck` refuses a count of zero at construction. That is the one
rule that stops "100% of chunks are attributable" from being true and worthless
because nothing was enumerated.

**What this module does not do.** It computes no confidence, invokes no
provider, and imports no `gateway`. The three arithmetic families it does carry
— the nearest-rank percentiles of FR-053's distribution, the rule-of-three
bound of FR-011, and the cosine comparison behind FR-061's cluster counts — are
assigned to this file by `plan.md` §Requirement Coverage Map. The one figure the
map assigns elsewhere is FR-011's Wilson branch, which is FR-060's interval and
lives in `model/compute/metrics.py`; it is reached by a deferred import so this
module states the method whether or not that module has landed yet.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from model.compute.confidence import (
    DEDUCTION_ORDER,
    SIGNAL_DOMAIN,
    ParseSignals,
    compute_confidence,
)
from model.compute.metrics import (
    F1_OMISSION_REASON,
    INTERVAL_METHOD,
    FieldFigures,
    wilson_interval,
)
from model.corpus.derive import normalize_page_text
from model.ingest.baseline import BASELINE_ID, BASELINE_INDEPENDENCE
from model.ingest.chunker import BOUNDARY_CLASSES, DocumentChunking
from model.ingest.documents import SHARED_LIBRARY_PROJECT
from model.ingest.failures import FAILURE_OUTCOMES
from model.ingest.runs import ConfidencePolicy
from model.ingest.writer import MultiChunkCounts

__all__ = [
    "ATTEMPT_UNIT",
    "BASELINE_LABELS",
    "COUNTING_UNITS",
    "DECLARED_BASELINE_CRITERION",
    "DECLARED_BASELINE_LABEL",
    "DECLARED_SIMILARITY_GRID",
    "DISPOSITIONS",
    "DISPOSITION_INGESTED",
    "DISPOSITION_MEANINGS",
    "DISPOSITION_NOT_REACHED",
    "DISPOSITION_ROLLED_BACK",
    "DISPOSITION_SKIPPED_UNCHANGED",
    "DOCUMENT_UNIT",
    "FIGURE_KINDS",
    "GENERATION_SETS",
    "HEURISTIC_ORDERING_STATEMENT",
    "INVOCATION_UNIT",
    "LAYERS",
    "NEAR_DUPLICATE_CAUSES",
    "PERCENTILE_POINTS",
    "REPORT_CONTENTS",
    "REPORT_PATH",
    "REPRODUCTION_ENCODER_PARITY",
    "REPRODUCTION_EXACT",
    "REPRODUCTION_TOLERANCES",
    "RESULTS_MANIFEST_PATH",
    "RULE_OF_THREE_MINIMUM",
    "AttemptLedger",
    "CarriedClaim",
    "ChunkVector",
    "ChunkingProfile",
    "ConfidenceDistribution",
    "ContentItem",
    "Figure",
    "FigureScope",
    "InvocationLedger",
    "LabelCensus",
    "LayerChunking",
    "MultiChunkCounts",
    "NearDuplicateCounts",
    "ReportError",
    "SampledClaim",
    "Section",
    "TotalCheck",
    "attempt_ledger_section",
    "build_report",
    "chunk_identity_section",
    "census_of_labels",
    "chunking_section",
    "collect_figures",
    "collect_total_checks",
    "confidence_domain",
    "confidence_section",
    "declared_baseline_label",
    "disposition_section",
    "extraction_quality_section",
    "failure_breakdown_section",
    "human_inspection_section",
    "index_procedure_section",
    "measure_near_duplicates",
    "near_duplicate_section",
    "observed_baseline_label",
    "page_split_section",
    "reproduction_section",
    "results_manifest",
    "profile_chunkings",
    "prj_000_section",
    "read_resident_chunks",
    "recognition_error_section",
    "reconciliation_section",
    "scope_labels_section",
    "tally_confidence",
    "total_checks_section",
]

#: FR-069's three counting units, named so a figure can carry the one it counts
#: rather than a word chosen at the call site. `FigureScope.unit` is not
#: restricted to these — a chunk-length figure counts leaves and a cluster count
#: counts clusters — but the ledger figures below are, because the whole point
#: of FR-069 is that an attempt-level and an invocation-level number never share
#: a table.
ATTEMPT_UNIT: str = "attempt"
INVOCATION_UNIT: str = "invocation"
DOCUMENT_UNIT: str = "document"
COUNTING_UNITS: tuple[str, ...] = (ATTEMPT_UNIT, INVOCATION_UNIT, DOCUMENT_UNIT)

#: FR-071: one artifact, at a fixed path, regenerated in full. Relative to the
#: repository root, which is where the caller resolves it — this module writes
#: no file, so nothing here depends on a working directory.
REPORT_PATH = Path("specs/00006-document-ingestion-and-extraction/ingestion-report.md")

#: FR-072's three kinds, closed. A census carries a population and a count and
#: **no** interval; a sampled estimate is one of FR-011's claims with its counts
#: and bound; a descriptive figure ranges over a designed set.
FIGURE_KINDS: tuple[str, ...] = ("census", "sampled", "descriptive")

#: FR-072: computed by query over the resident generations, or over one run's
#: own work and not recomputable from rows.
GENERATION_SETS: tuple[str, ...] = ("corpus-resident", "run-scoped")

#: E002's two layers plus the pooled figure. FR-053 and FR-061 require both the
#: per-layer and the pooled form, so `pooled` is a member rather than the
#: absence of one.
LAYERS: tuple[str, ...] = ("REAL", "SYNTHETIC", "pooled")


class ReportError(ValueError):
    """Raised when the report cannot be built, or must not be.

    One type for every failure. Each of them means the same thing to a caller:
    this report is not emitted, because emitting it would publish a claim
    without its basis.
    """


# ---------------------------------------------------------------------------
# FR-071 — the closed content list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentItem:
    """One entry of FR-071's closed list."""

    number: int
    title: str
    obliged_by: tuple[str, ...]


#: FR-071's table, item for item and in its order. This tuple **is** the closed
#: list: `build_report` accepts no section outside it and emits no report
#: missing one, so adding a section to the report means adding a row here and
#: amending the requirement, which is the point of writing it down.
REPORT_CONTENTS: tuple[ContentItem, ...] = (
    ContentItem(1, "`PRJ-000` convention and its unenforced reservation", ("FR-003",)),
    ContentItem(2, "Zero-recognition-error upper bound, per layer", ("FR-009",)),
    ContentItem(3, "Human-inspection claims with counts and bound", ("FR-011",)),
    ContentItem(4, "Chunk-identity contract", ("FR-018",)),
    ContentItem(5, "Recorded exclusion of the 26 real specifications", ("FR-022",)),
    ContentItem(
        6,
        "Floor, eight-score distribution with rejected and stored counted apart, "
        "weights, application order",
        ("FR-033", "FR-046", "FR-057"),
    ),
    ContentItem(7, "Failure count by each of the seven outcomes", ("FR-034",)),
    ContentItem(
        8,
        "Chunk counts, total and per layer, against the 5,000-15,000 estimate "
        "with cause of deviation",
        ("SC-005",),
    ),
    ContentItem(
        9,
        "Leaf-length distribution, sentence-split count, boundary-class counts, "
        "page-terminal documents with counts",
        ("FR-053",),
    ),
    ContentItem(10, "Multi-chunk value and contributing-chunk row counts", ("FR-029",)),
    ContentItem(11, "Near-duplicate cluster counts by cause, exact and per threshold", ("FR-061",)),
    ContentItem(
        12,
        "Per-field precision and recall with intervals and denominators, the baseline's "
        "figures, both labels and any disagreement, F1's omission and reason",
        ("FR-050", "FR-060"),
    ),
    ContentItem(
        13,
        "Valid, repaired, failed counts as invocation- and attempt-level tables with units",
        ("FR-069",),
    ),
    ContentItem(14, "Count of fields printed but not attempted", ("FR-058",)),
    ContentItem(15, "Attempted-versus-recorded invocation reconciliation", ("FR-070",)),
    ContentItem(16, "Per-document disposition ledger and its four counts", ("FR-073",)),
    ContentItem(17, "Population and count behind every total check", ("FR-068",)),
    ContentItem(
        18,
        "Sequential-scan fallback while the index is absent, and its absence after an abort",
        ("FR-064",),
    ),
    ContentItem(19, "Reproduction tolerance in force for each figure", ("FR-074",)),
    ContentItem(20, "Scope labels on every figure above", ("FR-072",)),
    ContentItem(
        21,
        "Encoder parity bounds declared before the comparison, with observed maxima",
        ("FR-019",),
    ),
)

_CONTENT_BY_NUMBER: Mapping[int, ContentItem] = {item.number: item for item in REPORT_CONTENTS}


# ---------------------------------------------------------------------------
# FR-072 — labelled figures; FR-068 — total checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureScope:
    """The five labels FR-072 requires on every published figure.

    Constructed with all five or not at all. A default for any of them would be
    a figure whose scope was guessed by whoever forgot to state it, and the
    label most often forgotten — the layer — is the one that decides whether a
    number about the 25 transmittals may be read as a number about the corpus.
    """

    run_id: str
    generation_set: str
    kind: str
    unit: str
    layer: str

    def __post_init__(self) -> None:
        if not str(self.run_id).strip():
            raise ReportError("FR-072: every figure names the run record it was computed under")
        if self.generation_set not in GENERATION_SETS:
            raise ReportError(
                f"FR-072: generation set {self.generation_set!r} is outside {GENERATION_SETS}"
            )
        if self.kind not in FIGURE_KINDS:
            raise ReportError(f"FR-072: figure kind {self.kind!r} is outside {FIGURE_KINDS}")
        if not self.unit.strip():
            raise ReportError("FR-072: every figure names its counting unit")
        if self.layer not in LAYERS:
            raise ReportError(f"FR-072: layer {self.layer!r} is outside {LAYERS}")


#: FR-074's two reproduction classes, declared as the text printed beside every
#: figure rather than as a symbol a reader has to resolve elsewhere.
#:
#: **Exact is the default and covers almost everything**: every count, every rate
#: derived from counts, every interval computed from them, and every stored
#: confidence reproduces as **bit equality** on a replay-mode run from a clean
#: checkout. Nothing in that list is floating-point-sensitive — the confidences
#: are deductions applied left to right in a declared order from weights read off
#: the run row, and the intervals are computed from integer counts.
REPRODUCTION_EXACT: str = "exact (bit equality)"

#: The one class **not** claimed exact (FR-061, FR-074). The near-duplicate
#: cluster counts are computed from floating-point vectors produced by the
#: exported encoder, so they reproduce within the encoder parity band ADR-0018
#: declares and FR-019 measures — and the band is printed with the counts rather
#: than cited, because a tolerance a reader has to look up is one nobody checks.
REPRODUCTION_ENCODER_PARITY: str = (
    "encoder parity: cosine >= 0.999999, max per-dimension |diff| <= 1e-5"
)

#: Both, in the order item 19 tabulates them.
REPRODUCTION_TOLERANCES: tuple[str, ...] = (REPRODUCTION_EXACT, REPRODUCTION_ENCODER_PARITY)


@dataclass(frozen=True)
class Figure:
    """One published number with its labels attached, never a bare value.

    **The reproduction tolerance is a field on the figure, not a paragraph
    elsewhere** (FR-074). It defaults to `REPRODUCTION_EXACT` because that is
    what almost every figure this report publishes must reproduce to, and it is
    rendered as a column of the same table the value is in — so a figure without
    its tolerance is unconstructible rather than merely undocumented, and a
    reader checking a reproduction never has to resolve a reference to find the
    band they are checking against.
    """

    label: str
    value: object
    scope: FigureScope
    note: str | None = None
    tolerance: str = REPRODUCTION_EXACT

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ReportError("a figure carries a label naming what it counts")
        if not self.tolerance.strip():
            raise ReportError(
                f"FR-074: the figure {self.label!r} publishes no reproduction tolerance. A "
                f"figure without the band it must reproduce within is not reproducible, and "
                f"the results manifest is a committed artifact checked against it."
            )

    def row(self) -> str:
        note = self.note or ""
        return (
            f"| {self.label} | {self.value} | {self.scope.run_id} | "
            f"{self.scope.generation_set} | {self.scope.kind} | {self.scope.unit} | "
            f"{self.scope.layer} | {self.tolerance} | {note} |"
        )


_FIGURE_HEADER = (
    "| Figure | Value | Run | Generation set | Kind | Unit | Layer | Reproduction tolerance | "
    "Note |\n|---|---|---|---|---|---|---|---|---|"
)


@dataclass(frozen=True)
class TotalCheck:
    """A check claimed over a whole population, with what it enumerated (FR-068).

    Raises at construction on an empty population, which is the requirement
    stated as a type rather than as a review step: `MUST fail rather than report
    success when that count is zero`. A total check that enumerated nothing has
    not passed, and there is no way to build one that says it has.
    """

    name: str
    population: str
    count: int
    scope: FigureScope
    outcome: str = "held"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ReportError("FR-068: a total check is published under a name")
        if not self.population.strip():
            raise ReportError(
                f"FR-068: the total check {self.name!r} publishes no enumerated population. "
                f"A '100%' or 'zero' claim without the population it ranged over is not "
                f"checkable."
            )
        if self.count <= 0:
            raise ReportError(
                f"FR-068: the total check {self.name!r} enumerated {self.count} members of "
                f"{self.population}. An empty population fails rather than passes, so this "
                f"check is not published as a success."
            )

    def row(self) -> str:
        return (
            f"| {self.name} | {self.population} | {self.count} | {self.outcome} | "
            f"{self.scope.run_id} | {self.scope.generation_set} | {self.scope.unit} | "
            f"{self.scope.layer} |"
        )


_TOTAL_CHECK_HEADER = (
    "| Total check | Enumerated population | Count | Outcome | Run | Generation set | "
    "Unit | Layer |\n|---|---|---|---|---|---|---|---|"
)


@dataclass(frozen=True)
class Section:
    """One item of the closed list, with its prose, figures, and total checks."""

    item: int
    body: str
    figures: tuple[Figure, ...] = ()
    total_checks: tuple[TotalCheck, ...] = ()

    def __post_init__(self) -> None:
        if self.item not in _CONTENT_BY_NUMBER:
            raise ReportError(
                f"FR-071: item {self.item} is not on the closed content list, whose members "
                f"are {sorted(_CONTENT_BY_NUMBER)}. An item in the report but absent from "
                f"the list is a defect in the list."
            )
        if not self.body.strip():
            raise ReportError(
                f"FR-071: item {self.item} "
                f"({_CONTENT_BY_NUMBER[self.item].title}) has nothing under it, which is a "
                f"defect in the report. A short report is not emitted."
            )

    def render(self) -> str:
        entry = _CONTENT_BY_NUMBER[self.item]
        parts = [
            f"## {entry.number}. {entry.title}",
            "",
            f"*Obliged by {', '.join(entry.obliged_by)}.*",
            "",
            self.body.strip(),
        ]
        if self.figures:
            parts += ["", _FIGURE_HEADER, *(figure.row() for figure in self.figures)]
        if self.total_checks:
            parts += ["", _TOTAL_CHECK_HEADER, *(check.row() for check in self.total_checks)]
        return "\n".join(parts)


def build_report(sections: Sequence[Section], *, run_id: str) -> str:
    """Render the report, or refuse (FR-071, FR-068).

    Args:
        sections: one `Section` per item of the closed content list.
        run_id: the run record the report describes, named by identifier
            (FR-072).

    Returns:
        The whole report as Markdown, ready to replace the committed artifact.

    Raises:
        ReportError: when an item is missing, when an item appears twice, when
            `run_id` is blank, or when a figure or total check names a different
            run. A section whose item is not on the list, or whose body is
            empty, is refused earlier by `Section` itself.

    **Regeneration replaces** (FR-071): the return value is the whole report,
    not a fragment to be merged into the previous one. There is no incremental
    path, deliberately — a report assembled from a previous run's sections plus
    this run's would carry figures under a run identifier that did not produce
    them. The run-identifier check below is what makes that structural rather
    than a property of there being no incremental path: an assembled report is
    refused even if someone builds one by hand (FR-072, item 20).
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the report names the run record it describes, by identifier")

    foreign = sorted(
        {
            labelled.scope.run_id
            for section in sections
            for labelled in (*section.figures, *section.total_checks)
            if labelled.scope.run_id != run_id
        }
    )
    if foreign:
        raise ReportError(
            f"FR-072: this report describes run {run_id!r} and carries figures or total "
            f"checks computed under {foreign}. The report names the run record it "
            f"describes by identifier, and a figure from another run inside it reads as "
            f"this run's work."
        )

    seen: dict[int, Section] = {}
    for section in sections:
        if section.item in seen:
            raise ReportError(
                f"FR-071: item {section.item} is supplied twice. The content list is closed "
                f"and each entry has exactly one place in the report."
            )
        seen[section.item] = section

    missing = [item for item in REPORT_CONTENTS if item.number not in seen]
    if missing:
        detail = "; ".join(
            f"{item.number} ({item.title}) — {', '.join(item.obliged_by)}" for item in missing
        )
        raise ReportError(
            f"FR-071: {len(missing)} of {len(REPORT_CONTENTS)} required items have nothing "
            f"under them, so no report is emitted: {detail}"
        )

    head = [
        "# Ingestion Report — Document Ingestion and Extraction",
        "",
        f"**Run**: `{run_id}`",
        "",
        "Every figure below carries the run it was computed under, the generation set it "
        "ranges over, its kind (census / sampled / descriptive), its counting unit, and its "
        "layer (FR-072). Every total check carries the population it enumerated and that "
        "population's count; a check enumerating zero members fails rather than passing "
        "(FR-068).",
        "",
        "This report's contents are the closed list FR-071 fixes. It is regenerated in full "
        "and replaces its predecessor wholesale.",
    ]
    body = [seen[item.number].render() for item in REPORT_CONTENTS]
    return "\n\n".join(["\n".join(head), *body]) + "\n"


# ---------------------------------------------------------------------------
# Item 1 — FR-003, the `PRJ-000` convention
# ---------------------------------------------------------------------------


def prj_000_section(*, run_id: str, real_documents: int) -> Section:
    """Item 1: the shared-library project, published as a convention (FR-003)."""
    scope = FigureScope(
        run_id=run_id,
        generation_set="corpus-resident",
        kind="census",
        unit="document",
        layer="REAL",
    )
    body = (
        f"Real specifications are governing documents shared by every project, so each is "
        f"recorded once under the reserved shared-library project `{SHARED_LIBRARY_PROJECT}` "
        f"rather than duplicated per referencing project.\n\n"
        f"**The reservation is a convention and nothing in the schema enforces it.** E003's "
        f"`ck_document__project_id_format` admits `{SHARED_LIBRARY_PROJECT}` exactly as it "
        f"admits any other well-formed project identifier, so nothing prevents a future "
        f"writer minting an ordinary project under it. What this epic does instead is refuse "
        f"to mint it for a synthetic document (`model/ingest/documents.py`) and publish the "
        f"absence of enforcement here, which is the honest form of the claim."
    )
    return Section(
        item=1,
        body=body,
        figures=(
            Figure(
                label=f"Documents recorded under {SHARED_LIBRARY_PROJECT}",
                value=real_documents,
                scope=scope,
                note="every real specification; enforcement is by convention only",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 2 — FR-009, the zero-recognition-error upper bound, per layer
# ---------------------------------------------------------------------------


def recognition_error_section(*, run_id: str, documents_by_layer: Mapping[str, int]) -> Section:
    """Item 2: the upper bound, stated **per layer** and narrower on one (FR-009).

    The two layers do not carry the same claim and the requirement is explicit
    that they must not be published as though they did:

    * **Synthetic** — the 25 transmittals are rendered from a document model,
      so their text layer is the generator's own strings. Recognition error is
      zero **by construction**, and this is the only layer extraction accuracy
      is measured on (SC-012), which is what makes every accuracy figure in
      this report an upper bound a genuinely scanned corpus would not
      reproduce.
    * **Real** — the 26 UFGS sections carry the narrower claim: **no
      recognition step is performed at any point** in this pipeline. Whether
      the embedded text layer already disagrees with the page it prints is
      **unmeasured**, and no figure here bounds it.
    """
    missing = {"REAL", "SYNTHETIC"} - set(documents_by_layer)
    if missing:
        raise ReportError(
            f"FR-009: the claim is stated per layer and {sorted(missing)} has no document "
            f"count, so the bound would be published over an unstated population"
        )

    def scope(layer: str) -> FigureScope:
        return FigureScope(
            run_id=run_id,
            generation_set="corpus-resident",
            kind="census",
            unit="document",
            layer=layer,
        )

    body = (
        "No optical character recognition is required for any document in this corpus and "
        "none is performed. The consequence for every accuracy figure in this report is "
        "stated here rather than on request: **each is an upper bound that a genuinely "
        "scanned corpus would not reproduce.** The claim differs by layer and is published "
        "separately for each.\n\n"
        "**Synthetic layer — zero recognition error by construction.** The transmittals are "
        "rendered from a document model, so the text layer is the generator's own strings "
        "and the datasheet records zero recognition error rather than estimating it. This "
        "is the only layer extraction accuracy is measured on (SC-012).\n\n"
        "**Real layer — the narrower claim.** No recognition step is performed at any point. "
        "Whether the embedded text layer of a UFGS section already disagrees with what the "
        "page prints is **unmeasured**: nothing in this epic validates the embedded text "
        "against a rendered page, and no figure below bounds that residual. The claim here "
        "is the absence of a recognition step, not the correctness of the text layer."
    )
    return Section(
        item=2,
        body=body,
        figures=(
            Figure(
                label="Documents carrying zero recognition error by construction",
                value=documents_by_layer["SYNTHETIC"],
                scope=scope("SYNTHETIC"),
                note="rendered from a document model; the datasheet records it",
            ),
            Figure(
                label="Documents on which no recognition step is performed",
                value=documents_by_layer["REAL"],
                scope=scope("REAL"),
                note="embedded-text-layer residual unmeasured",
            ),
            Figure(
                label="Documents whose embedded text layer was validated against its printed page",
                value=0,
                scope=scope("REAL"),
                note="the named residual; published as a zero rather than omitted",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 4 — FR-018, the chunk-identity contract
# ---------------------------------------------------------------------------


def chunk_identity_section(*, run_id: str, chunks_minted: int) -> Section:
    """Item 4: what a consumer may and may not rely on about a chunk id (FR-018).

    Stated over the **run** rather than over the chunker, which is the part
    most easily got wrong: FR-043's input tuple carries the provider model and
    the extraction prompt and schema digests, so a prompt change or a
    provider-model change re-mints every identifier of every document it
    touches even where the chunker produced byte-identical boundaries.
    """
    scope = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="chunk",
        layer="pooled",
    )
    body = (
        "**A chunk identifier is minted by the run that writes it.** It is stable only while "
        "that generation is resident, and exactly one generation's rows are resident per "
        "document ({SAD:ADR-0020}) — so a replacing generation's identifiers are new and the "
        "predecessor's are gone, not superseded in place.\n\n"
        "**The contract is over the run, not over the chunker.** FR-043's input tuple carries "
        "the provider model and the extraction prompt and schema digests alongside the "
        "chunker version. A prompt change or a provider-model change therefore re-mints every "
        "identifier of every document it reloads **even where the chunker cut byte-identical "
        "boundaries**. Reading identifier stability off boundary stability is the mistake this "
        "paragraph exists to prevent.\n\n"
        "**Chunk identity is deliberately not a function of content and position.** It is not "
        "a digest of the body text, the document, and the ordinal, and no consumer should "
        "derive one and expect it to match.\n\n"
        "**What a retrieval evaluation set must key on**: the **document identifier, the page "
        "number, and a quoted span** of the passage, resolved to chunks at harness run time. "
        "An evaluation set keyed on chunk identifiers silently empties the first time any "
        "member of the input tuple moves, and it empties without an error — the identifiers "
        "simply match nothing."
    )
    return Section(
        item=4,
        body=body,
        figures=(
            Figure(
                label="Chunk identifiers minted by this run",
                value=chunks_minted,
                scope=scope,
                note="stable only while this generation is resident",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 3 — FR-011, the enumerated human-inspection claim set
# ---------------------------------------------------------------------------

#: FR-011: `3/n` is never quoted at or below this many inspected items. Below
#: the threshold the rule of three's approximation to the exact binomial upper
#: bound is poor enough that quoting it would overstate what the sample
#: supports.
RULE_OF_THREE_MINIMUM = 30


@dataclass(frozen=True)
class SampledClaim:
    """A claim resting on human inspection, with what that inspection supports.

    `bound` is not a stored number: it is derived from `inspected` and `defects`
    by FR-011's fixed method, so the method cannot be chosen after the counts
    are known.
    """

    name: str
    inspected: int
    defects: int
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ReportError("FR-011: an enumerated claim is published under a name")
        if self.inspected < 0 or self.defects < 0:
            raise ReportError(f"FR-011: {self.name!r} carries a negative count")
        if self.defects > self.inspected:
            raise ReportError(
                f"FR-011: {self.name!r} records {self.defects} defects among "
                f"{self.inspected} inspected items"
            )

    @property
    def method(self) -> str:
        """The method FR-011 fixes for this claim's counts, named before the number."""
        if self.defects == 0:
            if self.inspected > RULE_OF_THREE_MINIMUM:
                return f"rule of three (95% upper bound 3/n, n = {self.inspected})"
            return f"none quoted (3/n is not quoted for n <= {RULE_OF_THREE_MINIMUM})"
        return "continuity-corrected Wilson 95% interval (FR-060)"

    @property
    def bound(self) -> str:
        """The bound the sample supports, or the reason there is none.

        The zero-defect branch is FR-011's rule of three and is computed here,
        which is where `plan.md` §Requirement Coverage Map assigns FR-011. The
        one-or-more-defect branch is FR-060's continuity-corrected Wilson
        interval and belongs to `model/compute/metrics.py`; until that module
        lands it is **named** rather than approximated, because an interval
        computed by a second implementation would be a second answer to a figure
        this epic publishes exactly one of.
        """
        if self.defects == 0:
            if self.inspected > RULE_OF_THREE_MINIMUM:
                return f"<= {3 / self.inspected:.4f} defect rate (95% upper bound)"
            if self.inspected == 0:
                return "no bound — nothing has been inspected"
            return f"no bound — n = {self.inspected} is at or below {RULE_OF_THREE_MINIMUM}"
        return _wilson_bound(self.defects, self.inspected)


def _wilson_bound(defects: int, inspected: int) -> str:
    """FR-060's continuity-corrected Wilson interval, from `model.compute`.

    One implementation, imported rather than restated. FR-060 requires the same
    variant everywhere an interval on a proportion is published in this epic —
    FR-011's defect bound included — so a local approximation here would be the
    second answer that requirement exists to prevent.

    The import was deferred to the point of use while `compute/metrics.py` was
    still ahead of its red-green pair (T049 → T050); it is a module-level import
    now that the pair has landed, because a `try/except ImportError` whose
    handler is unreachable is a fallback nobody can test.
    """
    low, high = wilson_interval(defects, inspected)
    return f"[{low:.4f}, {high:.4f}] defect rate ({INTERVAL_METHOD}, n = {inspected})"


@dataclass(frozen=True)
class CarriedClaim:
    """A claim that rests on a total check rather than on inspection.

    FR-011 requires **every** claim to appear in the enumeration: one either
    carries its inspected count or names the total check that discharges it.
    This is the second form, and it exists so the enumeration can be read as
    complete rather than as a list of the claims someone remembered to sample.
    """

    name: str
    carried_by: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.carried_by.strip():
            raise ReportError("FR-011: a carried claim names both the claim and its total check")


def human_inspection_section(
    *,
    run_id: str,
    sampled: Sequence[SampledClaim],
    carried: Sequence[CarriedClaim],
) -> Section:
    """Item 3: the enumerated claim set, its counts, and its bounds (FR-011).

    Every member is published, **including the ones whose inspected count is
    zero**. A claim omitted because nobody inspected anything is the failure
    mode this enumeration exists to make visible.
    """
    if not sampled:
        raise ReportError(
            "FR-011: the enumerated claim set is empty. Its known member is structural "
            "detection on the 26 real specifications, which is published with an inspected "
            "count of zero rather than omitted."
        )
    if not carried:
        raise ReportError(
            "FR-011: no claim is recorded as carried by a total check. Every claim appears in "
            "the enumeration either with its inspected count or naming the check that "
            "discharges it, and a report publishing total checks (FR-068) while naming none "
            "of them here leaves the enumeration unreadable as complete."
        )

    scope = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="sampled",
        unit="inspected item",
        layer="pooled",
    )

    rows = [
        "| Claim | Inspected | Defects | Method | Bound | Note |",
        "|---|---|---|---|---|---|",
        *(
            f"| {claim.name} | {claim.inspected} | {claim.defects} | {claim.method} | "
            f"{claim.bound} | {claim.note} |"
            for claim in sampled
        ),
    ]
    carried_rows = [
        "| Claim | Total check that carries it |",
        "|---|---|",
        *(f"| {claim.name} | {claim.carried_by} |" for claim in carried),
    ]
    body = (
        "**The set of claims resting on human inspection is enumerated below, not summarised.** "
        "A claim with an inspected count of zero is published as a zero; omitting it would "
        "make the enumeration a list of the claims someone happened to sample.\n\n"
        "**The method is fixed and was fixed before the counts existed.** With zero defects "
        f"the bound is the rule-of-three 95% upper bound `3/n`, stated with *n* and **never "
        f"quoted for n <= {RULE_OF_THREE_MINIMUM}**. With one or more defects it is the "
        "continuity-corrected Wilson 95% interval on the observed defect proportion (FR-060), "
        "with its denominator printed. Neither is chosen after the observation.\n\n"
        + "\n".join(rows)
        + "\n\n**Every other claim in this report is carried by a total check rather than by a "
        "sample.** Each is named here with the check that discharges it, so the enumeration "
        "above can be read as complete.\n\n" + "\n".join(carried_rows)
    )
    return Section(
        item=3,
        body=body,
        figures=tuple(
            Figure(
                label=f"{claim.name} — items inspected",
                value=claim.inspected,
                scope=scope,
                note=f"{claim.defects} defects; {claim.method}; {claim.bound}",
            )
            for claim in sampled
        ),
    )


# ---------------------------------------------------------------------------
# Item 9 — FR-053, the measured chunking profile
# ---------------------------------------------------------------------------

#: Percentiles published for the leaf-length distribution. Reported under
#: `schema_constants.percentile_convention` — nearest rank, one-based, no
#: interpolation — which is the convention every figure in this project is
#: computed under and is named beside the numbers rather than assumed.
PERCENTILE_POINTS: tuple[int, ...] = (50, 75, 90, 95, 99)

#: A chunk under no structural marker at all: `structure.py` names a residual
#: run of lines `p<page>-body<n>`, and a document whose chunks carry those
#: identifiers was chunked at the page-terminal fallback.
_PAGE_TERMINAL = re.compile(r"^p[0-9]+-body[0-9]+$")


def _percentile(values: Sequence[int], point: int) -> int:
    """Nearest rank, one-based, no interpolation.

    `schema_constants.percentile_convention` publishes exactly this rule, and
    the arithmetic is written out rather than delegated to a library default:
    NumPy's `percentile` interpolates by default and would give a different
    answer for the same data under the same name.
    """
    if not values:
        raise ReportError("FR-053: a percentile over an empty distribution is not published")
    ordered = sorted(values)
    rank = math.ceil(point / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


@dataclass(frozen=True)
class LayerChunking:
    """FR-053's measured figures for one layer, or pooled over both."""

    layer: str
    documents: int
    chunks: int
    leaf_lengths: tuple[int, ...]
    boundary_class_counts: Mapping[str, int]
    leaves_split_into_sentences: int
    page_terminal_chunks_by_document: Mapping[str, int]

    @property
    def page_terminal_documents(self) -> int:
        return len(self.page_terminal_chunks_by_document)

    @property
    def percentiles(self) -> Mapping[int, int]:
        return {point: _percentile(self.leaf_lengths, point) for point in PERCENTILE_POINTS}


@dataclass(frozen=True)
class ChunkingProfile:
    """The whole corpus's chunking, per layer and pooled (FR-053, FR-072)."""

    by_layer: Mapping[str, LayerChunking]

    def __post_init__(self) -> None:
        for layer in LAYERS:
            if layer not in self.by_layer:
                raise ReportError(
                    f"FR-053: every figure is published per layer as well as pooled, and "
                    f"{layer!r} is absent"
                )


def profile_chunkings(
    chunkings: Sequence[tuple[str, DocumentChunking]],
) -> ChunkingProfile:
    """Measure FR-053's figures over the run's own chunkings.

    Args:
        chunkings: `(layer, chunking)` for every document the run chunked.
            The layer travels with the chunking because FR-053 requires every
            figure per layer, and a chunking carries no layer of its own.

    Returns:
        The per-layer and pooled profile.

    Raises:
        ReportError: when nothing was chunked, or when a layer outside E002's
            two appears.

    **Measured, never inferred.** The 26 real specifications are structurally
    uncharacterized, so the distribution is taken from the encoder's own word
    pieces as the chunker counted them, not derived from the standard's format
    rules.
    """
    if not chunkings:
        raise ReportError("FR-053: the chunking profile enumerated zero documents")

    buckets: dict[str, dict[str, object]] = {
        layer: {
            "documents": set(),
            "chunks": 0,
            "lengths": [],
            "classes": dict.fromkeys(BOUNDARY_CLASSES, 0),
            "split_leaves": set(),
            "page_terminal": {},
        }
        for layer in LAYERS
    }

    for layer, chunking in chunkings:
        if layer not in ("REAL", "SYNTHETIC"):
            raise ReportError(f"FR-053: {layer!r} is not one of the corpus's two layers")
        for target in (layer, "pooled"):
            bucket = buckets[target]
            bucket["documents"].add(chunking.document_id)  # type: ignore[union-attr]
            bucket["chunks"] = int(bucket["chunks"]) + len(chunking.chunks)
            for chunk in chunking.chunks:
                bucket["lengths"].append(chunk.content_pieces)  # type: ignore[union-attr]
                bucket["classes"][chunk.boundary_class] += 1  # type: ignore[index]
                if chunk.boundary_class == "sentence":
                    # One *leaf* per (document, page, structural identifier):
                    # a leaf split into four chunks is one leaf that required a
                    # sentence-level split, not four.
                    bucket["split_leaves"].add(  # type: ignore[union-attr]
                        (chunking.document_id, chunk.page_number, chunk.structural_identifier)
                    )
                if _PAGE_TERMINAL.fullmatch(chunk.structural_identifier):
                    counts = bucket["page_terminal"]
                    counts[chunking.document_id] = counts.get(chunking.document_id, 0) + 1  # type: ignore[union-attr,index]

    return ChunkingProfile(
        by_layer={
            layer: LayerChunking(
                layer=layer,
                documents=len(bucket["documents"]),  # type: ignore[arg-type]
                chunks=int(bucket["chunks"]),
                leaf_lengths=tuple(bucket["lengths"]),  # type: ignore[arg-type]
                boundary_class_counts=dict(bucket["classes"]),  # type: ignore[arg-type]
                leaves_split_into_sentences=len(bucket["split_leaves"]),  # type: ignore[arg-type]
                page_terminal_chunks_by_document=dict(bucket["page_terminal"]),  # type: ignore[arg-type]
            )
            for layer, bucket in buckets.items()
        }
    )


def chunking_section(*, run_id: str, profile: ChunkingProfile) -> Section:
    """Item 9: the measured leaf-length distribution and its companions (FR-053)."""

    def scope(layer: str, unit: str) -> FigureScope:
        return FigureScope(
            run_id=run_id,
            generation_set="run-scoped",
            kind="descriptive" if unit == "content word piece" else "census",
            unit=unit,
            layer=layer,
        )

    figures: list[Figure] = []
    for layer in LAYERS:
        measured = profile.by_layer[layer]
        percentiles = measured.percentiles
        figures.append(
            Figure(
                label="Leaf length — median (p50)",
                value=percentiles[50],
                scope=scope(layer, "content word piece"),
                note=(
                    "nearest rank, one-based, no interpolation; "
                    f"n = {len(measured.leaf_lengths)} chunks"
                ),
            )
        )
        for point in PERCENTILE_POINTS[1:]:
            figures.append(
                Figure(
                    label=f"Leaf length — p{point}",
                    value=percentiles[point],
                    scope=scope(layer, "content word piece"),
                    note="nearest rank, one-based, no interpolation",
                )
            )
        figures.append(
            Figure(
                label="Leaf length — maximum",
                value=max(measured.leaf_lengths),
                scope=scope(layer, "content word piece"),
                note="against the 254-piece content budget",
            )
        )
        figures.append(
            Figure(
                label="Leaves requiring a sentence-level split",
                value=measured.leaves_split_into_sentences,
                scope=scope(layer, "leaf unit"),
                note="a leaf split into k chunks counts once",
            )
        )
        for boundary_class in BOUNDARY_CLASSES:
            figures.append(
                Figure(
                    label=f"Chunks closed by a {boundary_class} boundary",
                    value=measured.boundary_class_counts[boundary_class],
                    scope=scope(layer, "chunk"),
                    note="a class holding no boundaries is published as a zero",
                )
            )
        figures.append(
            Figure(
                label="Documents chunked at the page-terminal fallback",
                value=measured.page_terminal_documents,
                scope=scope(layer, "document"),
                note="a page with no structural marker; each document's count is below",
            )
        )

    pooled = profile.by_layer["pooled"]
    page_terminal_rows = [
        "| Document | Page-terminal chunks |",
        "|---|---|",
        *(
            f"| {document_id} | {count} |"
            for document_id, count in sorted(pooled.page_terminal_chunks_by_document.items())
        ),
    ]
    if not pooled.page_terminal_chunks_by_document:
        page_terminal_rows = ["No document was chunked at the page-terminal fallback."]

    body = (
        "**Measured, not inferred.** The 26 real specifications are structurally "
        "uncharacterized, so the leaf-length distribution below is taken from the encoder's "
        "own tokenizer as the chunker counted it, in content word pieces against the "
        "254-piece budget. Percentiles are nearest rank, one-based, with no interpolation — "
        "the convention `schema_constants.percentile_convention` publishes.\n\n"
        "**Every figure is published per layer as well as pooled** (FR-053, FR-072), and a "
        "boundary class holding no boundaries is published as a zero rather than omitted.\n\n"
        "**Documents chunked at the page-terminal fallback**, each with its own count of "
        "page-terminal chunks — a page carrying no structural marker at all, where the page "
        "is the terminal unit:\n\n" + "\n".join(page_terminal_rows)
    )
    return Section(
        item=9,
        body=body,
        figures=tuple(figures),
        total_checks=(
            TotalCheck(
                name="Every chunk measured in the encoder's own tokenizer",
                population="every chunk this run cut, over all documents it chunked",
                count=pooled.chunks,
                scope=FigureScope(
                    run_id=run_id,
                    generation_set="run-scoped",
                    kind="census",
                    unit="chunk",
                    layer="pooled",
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 10 — FR-029, multi-chunk values and their contributing-chunk rows
# ---------------------------------------------------------------------------


def page_split_section(*, run_id: str, counts: MultiChunkCounts) -> Section:
    """Item 10: how many values were assembled across a page break (FR-029).

    Args:
        run_id: the run the report describes.
        counts: the multi-chunk tally, as `writer.multi_chunk_counts` produces
            from the citations themselves. Passed in rather than queried here
            for the reason the rest of this module is written that way — the
            report publishes what it is given, and the counting belongs beside
            the provenance it counts.

    Returns:
        Item 10, with both counts FR-071 names and the arithmetic that relates
        them stated rather than left to a reader.

    Raises:
        ReportError: the run stored no value at all. `TotalCheck` refuses an
            empty population at construction, and it is refused here with a
            message that names the cause: "zero values were assembled across a
            page break" is a measurement, but only if some value was measured.

    **The two counts are published together because either alone is a different
    claim.** `MultiChunkCounts` already refuses a row count below the value
    count, so the pair cannot be published in a shape that hides a dropped
    contributor; what is added here is the *relation*, printed rather than
    implied: a value drawing on *n* pages carries `n - 1` rows, because the
    anchor is contributor 1 and lives on `extracted_value`. So the row count is
    exactly the multi-chunk value count when every split spans two pages, and
    exceeds it only where a value spans three or more.

    **Zero is published as a measurement, never as an absent row.** A corpus in
    which nothing split across a page would report `0` here, beside the
    population it was counted over — which is what distinguishes it from a run
    whose contributing-chunk write never executed.
    """
    if counts.values <= 0:
        raise ReportError(
            "FR-029 / FR-068: the multi-chunk count is denominated on the values this run "
            "stored, and it stored none. Zero multi-chunk values out of zero values is not "
            "a measurement — an empty population fails rather than passes."
        )

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="extracted value",
        layer="SYNTHETIC",
    )
    row_labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="contributing-chunk row",
        layer="SYNTHETIC",
    )
    excess = counts.contributing_rows - counts.multi_chunk_values
    body = (
        f"**A field whose label ends one page and whose value begins the next is a "
        f"multi-source value, never a chunk spanning the break** (FR-029). Its citation "
        f"anchors on the chunk carrying the **printed value** — so the cited page is the "
        f"*later* page — and every further page it draws on is recorded as an additional "
        f"contributing chunk.\n\n"
        f"**The anchor is contributor 1 and never appears among the contributing rows.** "
        f"`ck_evcc__ordinal_min` fixes the ordinal floor at 2 for exactly that reason, so "
        f"a value assembled across one page break carries **one** contributing row and a "
        f"`source_chunk_count` of 2. The row count below is therefore "
        f"`sum(source_chunk_count - 1)` over the multi-chunk values, and the "
        f"{excess} row(s) beyond the multi-chunk value count are the values spanning three "
        f"or more pages.\n\n"
        f"**Reassembly is in ascending page order, not contributor order** (SC-027). The "
        f"anchor being the later page, ordering a comparison by contributor position would "
        f"put the value before its own label; the contributor ordinal is identity within "
        f"the set and carries no precedence meaning.\n\n"
        f"| Term | Count |\n|---|---|\n"
        f"| extracted values stored | {counts.values} |\n"
        f"| read from a single chunk | {counts.single_chunk_values} |\n"
        f"| assembled across a page break | {counts.multi_chunk_values} |\n"
        f"| contributing-chunk rows recorded | {counts.contributing_rows} |\n"
    )
    return Section(
        item=10,
        body=body,
        figures=(
            Figure(
                label="Extracted values assembled across a page break",
                value=counts.multi_chunk_values,
                scope=labels,
                note=(
                    "zero published rather than omitted"
                    if counts.multi_chunk_values == 0
                    else f"denominator: {counts.values} stored values"
                ),
            ),
            Figure(
                label="Extracted values read from a single chunk",
                value=counts.single_chunk_values,
                scope=labels,
            ),
            Figure(
                label="Contributing-chunk rows recorded",
                value=counts.contributing_rows,
                scope=row_labels,
                note="the anchor is contributor 1 and is not among them",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every multi-chunk value records one row per page beyond its anchor",
                population="every extracted value this run stored",
                count=counts.values,
                scope=labels,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 11 — FR-061, near-duplicate cluster counts by cause
# ---------------------------------------------------------------------------

#: **Declared before the run and not chosen after the clusters were observed**
#: (FR-061). The count is published at every point, so what this report carries
#: is a curve rather than a fitted point — a single threshold picked once the
#: clusters are visible is a result dressed as a parameter.
DECLARED_SIMILARITY_GRID: tuple[float, ...] = (0.80, 0.85, 0.90, 0.95, 0.99)

#: FR-061's three causes, with the candidate rule each is measured under. The
#: rules are lexical properties of the corpus fixed in advance; a cause with no
#: candidate pair in this corpus is published as a zero rather than dropped.
NEAR_DUPLICATE_CAUSES: tuple[tuple[str, str], ...] = (
    (
        "dense reference-designation list",
        "chunks whose nearest heading names REFERENCES — the PART 1 article every UFGS "
        "section prints, carrying the same standards designations",
    ),
    (
        "agency variant of one MasterFormat number",
        "chunks from two different real documents whose MasterFormat number agrees once the "
        "trailing agency code is removed; a document sharing its number with no other "
        "contributes no candidate, since it can form no pair",
    ),
    (
        "resubmittal chain differing only by revision suffix",
        "chunks from two different synthetic documents whose identifiers agree once the "
        "trailing revision suffix is removed; a document sharing its stem with no other "
        "contributes no candidate, since it can form no pair",
    ),
)

_REFERENCES_HEADING = re.compile(r"REFERENCES", re.IGNORECASE)
#: `ufgs-26-11-13-00-20` -> `26-11-13`: three MasterFormat groups, then an
#: optional two-group agency code that distinguishes the Army, Navy and Air
#: Force variants of one number.
_MASTERFORMAT = re.compile(r"^ufgs-(?P<number>[0-9]{2}-[0-9]{2}-[0-9]{2})(?:-[0-9]{2}-[0-9]{2})?$")
#: `prj-001-t0004-r1` -> `prj-001-t0004`.
_REVISION_SUFFIX = re.compile(r"^(?P<stem>.+)-r[0-9]+$")


@dataclass(frozen=True)
class ChunkVector:
    """One stored chunk, as the near-duplicate measurement needs to see it."""

    document_id: str
    layer: str
    ordinal: int
    page_number: int
    heading: str | None
    body_text: str
    embedding: np.ndarray

    @property
    def normalized_text(self) -> str:
        """The chunk's text in the one committed comparison form (SC-037).

        `corpus.derive.normalize_page_text` and nothing else — the same
        function the containment guard compares through, so "exactly equal
        normalized text" means the same thing in both places.
        """
        return normalize_page_text(self.body_text)


@dataclass(frozen=True)
class NearDuplicateCounts:
    """Cluster counts for one cause, exact and at each declared threshold."""

    cause: str
    candidate_rule: str
    candidates: int
    exact_clusters: int
    clusters_by_threshold: Mapping[float, int]
    layer: str


#: The chunks of the resident generation set, joined to their document for the
#: layer the cause rules key on. Ordered so two runs of the measurement over one
#: database enumerate the same population in the same order — the cluster counts
#: are order-independent, but the failure message naming a cluster's members is
#: not.
_RESIDENT_CHUNKS = """
SELECT c.document_id, d.source_kind, c.ordinal, c.page_number, c.heading,
       c.body_text, c.embedding
FROM chunk AS c
JOIN document AS d ON d.document_id = c.document_id
JOIN ingestion_run_chunk AS a ON a.chunk_id = c.chunk_id
JOIN v_active_ingestion_generation AS g
  ON g.run_id = a.run_id AND g.document_id = a.document_id
ORDER BY c.document_id, c.ordinal
"""


def read_resident_chunks(connection: object) -> tuple[ChunkVector, ...]:
    """Every chunk of the resident generation set, with its stored vector.

    Args:
        connection: a psycopg connection with pgvector's types registered —
            `writer.connect` returns one. Typed loosely so this module does not
            import psycopg for a signature; the only thing required of it is
            `cursor()`.

    Returns:
        One `ChunkVector` per resident chunk, ordered by document and ordinal.

    The generation set is read through `v_active_ingestion_generation`, so the
    population is 'the generations resident when the report was written' —
    FR-072's **corpus-resident** scope — rather than 'whatever this process
    happens to hold in memory'. Reading the vector back rather than reusing the
    run's array is what makes the near-duplicate figure recomputable by query.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RESIDENT_CHUNKS)
        rows = cursor.fetchall()
    return tuple(
        ChunkVector(
            document_id=row[0],
            layer=row[1],
            ordinal=row[2],
            page_number=row[3],
            heading=row[4],
            body_text=row[5],
            embedding=_as_vector(row[6]),
        )
        for row in rows
    )


def _as_vector(stored: object) -> np.ndarray:
    """A stored `vector` column as float32, whichever form the adapter returns.

    pgvector's psycopg adapter returns its own `Vector` wrapper for a text
    result and a bare NumPy array for a binary one, and which of the two arrives
    depends on the cursor rather than on anything this module controls. Both are
    handled here rather than at the call site, because the failure otherwise
    surfaces as a `TypeError` deep inside NumPy naming neither column nor row.
    """
    to_numpy = getattr(stored, "to_numpy", None)
    if callable(to_numpy):
        return np.asarray(to_numpy(), dtype=np.float32)
    return np.asarray(stored, dtype=np.float32)


def _masterformat(document_id: str) -> str | None:
    match = _MASTERFORMAT.fullmatch(document_id)
    return match.group("number") if match else None


def _revision_stem(document_id: str) -> str | None:
    match = _REVISION_SUFFIX.fullmatch(document_id)
    return match.group("stem") if match else None


def _shared_keys(
    vectors: Sequence[ChunkVector],
    layer: str,
    key: object,
) -> frozenset[str]:
    """Keys held by two or more *documents* on `layer`.

    Both the second and third causes are defined over a pair of **different**
    documents agreeing on a key, so a document holding a key no other document
    holds can contribute no pair at all. Its chunks are therefore not
    candidates, and saying so is not a shortcut: publishing 6,391 candidate
    chunks of which none could ever pair would make the candidate count
    uninterpretable next to the cluster count it is printed beside.
    """
    documents: dict[str, set[str]] = {}
    for vector in vectors:
        if vector.layer != layer:
            continue
        derived = key(vector.document_id)  # type: ignore[operator]
        if derived is None:
            continue
        documents.setdefault(derived, set()).add(vector.document_id)
    return frozenset(derived for derived, held in documents.items() if len(held) > 1)


def _candidates(cause: str, vectors: Sequence[ChunkVector]) -> tuple[int, ...]:
    """Indices of the chunks a cause's declared rule admits."""
    if cause == NEAR_DUPLICATE_CAUSES[0][0]:
        return tuple(
            index
            for index, vector in enumerate(vectors)
            if vector.heading and _REFERENCES_HEADING.search(vector.heading)
        )
    if cause == NEAR_DUPLICATE_CAUSES[1][0]:
        shared = _shared_keys(vectors, "REAL", _masterformat)
        return tuple(
            index
            for index, vector in enumerate(vectors)
            if vector.layer == "REAL" and _masterformat(vector.document_id) in shared
        )
    shared = _shared_keys(vectors, "SYNTHETIC", _revision_stem)
    return tuple(
        index
        for index, vector in enumerate(vectors)
        if vector.layer == "SYNTHETIC" and _revision_stem(vector.document_id) in shared
    )


def _pairs_admitted(cause: str, left: ChunkVector, right: ChunkVector) -> bool:
    """Whether the declared rule admits this pair as a candidate for `cause`."""
    if cause == NEAR_DUPLICATE_CAUSES[0][0]:
        return True
    if cause == NEAR_DUPLICATE_CAUSES[1][0]:
        return left.document_id != right.document_id and _masterformat(
            left.document_id
        ) == _masterformat(right.document_id)
    return left.document_id != right.document_id and _revision_stem(
        left.document_id
    ) == _revision_stem(right.document_id)


class _Components:
    """Union-find, so a cluster is a connected component and not a pair count.

    Counting *pairs* above a threshold would report a group of five identical
    reference lists as ten near-duplicates. FR-061 asks for cluster counts, so
    the pairs are unioned and clusters of two or more members are counted.
    """

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[left_root] = right_root

    def clusters(self) -> int:
        sizes: dict[int, int] = {}
        for node in range(len(self._parent)):
            root = self.find(node)
            sizes[root] = sizes.get(root, 0) + 1
        return sum(1 for size in sizes.values() if size > 1)


def measure_near_duplicates(
    vectors: Sequence[ChunkVector],
    *,
    grid: Sequence[float] = DECLARED_SIMILARITY_GRID,
) -> tuple[NearDuplicateCounts, ...]:
    """Cluster counts by cause, exact and at every declared threshold (FR-061).

    Args:
        vectors: the stored chunks with their embeddings — **this epic's own**
            vectors, read back from `chunk.embedding`, not a second encoding.
        grid: the declared threshold grid. Defaulted to
            `DECLARED_SIMILARITY_GRID`, which is fixed in this module before any
            run; the parameter exists for the test that asserts the curve is
            monotone, not so a caller can pick a threshold after the fact.

    Returns:
        One `NearDuplicateCounts` per cause, in `NEAR_DUPLICATE_CAUSES` order.

    Raises:
        ReportError: when no chunk is supplied — the measurement would then
            report zero clusters for every cause from an empty population,
            which FR-068 refuses.

    **The measure is cosine similarity over the chunk embeddings this epic
    already computes**, which are L2-normalized at write time, so the cosine is
    the inner product and no second normalization is applied here. Both are
    fixed before the run: the measure by this requirement and the grid by the
    constant above.
    """
    if not vectors:
        raise ReportError(
            "FR-061/FR-068: the near-duplicate measurement enumerated zero chunks. An empty "
            "population fails rather than reporting zero clusters for every cause."
        )
    thresholds = tuple(float(value) for value in grid)
    results: list[NearDuplicateCounts] = []

    for cause, rule in NEAR_DUPLICATE_CAUSES:
        indices = _candidates(cause, vectors)
        layer = "REAL" if cause == NEAR_DUPLICATE_CAUSES[1][0] else "pooled"
        if cause == NEAR_DUPLICATE_CAUSES[2][0]:
            layer = "SYNTHETIC"
        if len(indices) < 2:
            results.append(
                NearDuplicateCounts(
                    cause=cause,
                    candidate_rule=rule,
                    candidates=len(indices),
                    exact_clusters=0,
                    clusters_by_threshold=dict.fromkeys(thresholds, 0),
                    layer=layer,
                )
            )
            continue

        members = [vectors[index] for index in indices]
        matrix = np.array([member.embedding for member in members], dtype=np.float32)
        similarity = matrix @ matrix.T

        exact = _Components(len(members))
        per_threshold = {threshold: _Components(len(members)) for threshold in thresholds}
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if not _pairs_admitted(cause, members[i], members[j]):
                    continue
                if members[i].normalized_text == members[j].normalized_text:
                    exact.union(i, j)
                score = float(similarity[i, j])
                for threshold in thresholds:
                    if score >= threshold:
                        per_threshold[threshold].union(i, j)

        results.append(
            NearDuplicateCounts(
                cause=cause,
                candidate_rule=rule,
                candidates=len(members),
                exact_clusters=exact.clusters(),
                clusters_by_threshold={
                    threshold: components.clusters()
                    for threshold, components in per_threshold.items()
                },
                layer=layer,
            )
        )
    return tuple(results)


def near_duplicate_section(
    *,
    run_id: str,
    counts: Sequence[NearDuplicateCounts],
    chunks_measured: int,
) -> Section:
    """Item 11: the cluster counts by cause, as a curve over the grid (FR-061)."""
    if len(counts) != len(NEAR_DUPLICATE_CAUSES):
        raise ReportError(
            f"FR-061: three causes are published and {len(counts)} were supplied; a cause "
            f"with no cluster in this corpus is published as a zero, never dropped"
        )

    thresholds = tuple(sorted({t for entry in counts for t in entry.clusters_by_threshold}))
    header = (
        "| Cause | Candidate chunks | Exact matches | "
        + " | ".join(f"cos >= {threshold:.2f}" for threshold in thresholds)
        + " |"
    )
    divider = "|---|---|---|" + "---|" * len(thresholds)
    rows = [
        f"| {entry.cause} | {entry.candidates} | {entry.exact_clusters} | "
        + " | ".join(str(entry.clusters_by_threshold[threshold]) for threshold in thresholds)
        + " |"
        for entry in counts
    ]
    rule_rows = [
        "| Cause | Declared candidate rule |",
        "|---|---|",
        *(f"| {entry.cause} | {entry.candidate_rule} |" for entry in counts),
    ]

    figures: list[Figure] = []
    for entry in counts:
        scope = FigureScope(
            run_id=run_id,
            generation_set="corpus-resident",
            kind="census",
            unit="cluster",
            layer=entry.layer,
        )
        figures.append(
            Figure(
                label=f"{entry.cause} — clusters of exactly equal normalized text",
                value=entry.exact_clusters,
                scope=scope,
                note=f"{entry.candidates} candidate chunks under the declared rule",
                # Exact: this count compares normalized *text*, not vectors, so
                # nothing floating-point enters it. Only the thresholded counts
                # below inherit the encoder's tolerance (FR-074).
            )
        )
        for threshold in thresholds:
            figures.append(
                Figure(
                    label=f"{entry.cause} — clusters at cosine >= {threshold:.2f}",
                    value=entry.clusters_by_threshold[threshold],
                    scope=scope,
                    note="declared grid; not a fitted threshold",
                    tolerance=REPRODUCTION_ENCODER_PARITY,
                )
            )

    body = (
        "**The measure and the thresholds were fixed before the run.** The measure is "
        "**cosine similarity over the chunk embeddings this epic already computes**, read "
        "back from `chunk.embedding` rather than re-encoded; the vectors are L2-normalized "
        "at write time, so the cosine is their inner product. The grid is "
        f"{', '.join(f'{t:.2f}' for t in DECLARED_SIMILARITY_GRID)}, declared as a constant "
        "in `model/ingest/report.py`, and the count is published **at every point** — what "
        "follows is a curve, not a fitted threshold chosen once the clusters were visible.\n\n"
        "A **cluster** is a connected component of two or more chunks, not a pair: five "
        "identical reference lists are one cluster, not ten near-duplicates.\n\n"
        + "\n".join([header, divider, *rows])
        + "\n\n**The candidate rule for each cause was declared before the measurement** and "
        "is published with it, because a cluster count is only interpretable against the set "
        "of pairs that were eligible to form one. A cause with no candidate pair in this "
        "corpus is published as a zero.\n\n" + "\n".join(rule_rows)
    )
    return Section(
        item=11,
        body=body,
        figures=tuple(figures),
        total_checks=(
            TotalCheck(
                name="Chunks entering the near-duplicate measurement",
                population="every chunk of the resident generation set, before the candidate "
                "rules select from it",
                count=chunks_measured,
                scope=FigureScope(
                    run_id=run_id,
                    generation_set="corpus-resident",
                    kind="census",
                    unit="chunk",
                    layer="pooled",
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 12 — FR-050 and FR-060, the quality figures and the two baseline labels
# ---------------------------------------------------------------------------

#: FR-050's two labels, closed. Neither is a scale and neither is a score: they
#: are the two answers each of the two stated criteria can give.
BASELINE_LABELS: tuple[str, ...] = ("strong", "weak")

#: The criterion for the **declared** label, in the requirement's own terms.
#: Published beside the label, because a label without its criterion is an
#: opinion.
DECLARED_BASELINE_CRITERION = (
    "**strong** where the baseline is authored under the independence contract *and* is "
    "template-driven over a corpus generated from fixed per-vendor templates; **weak** "
    "otherwise. Both conditions hold here: the independence contract is committed in "
    "`src/model/pyproject.toml` and enforced by `lint-imports`, and E002's synthetic "
    "layer is generated from a fixed set of per-vendor layout templates — which is "
    "precisely what makes a template extractor able to win."
)


def declared_baseline_label(*, independent: bool, template_driven: bool) -> str:
    """FR-050's declared criterion, as a function rather than as a sentence.

    Executable so the criterion can be *checked* rather than read: the constant
    below is derived from it at import, so a label and a criterion that disagree
    fail at import instead of being published together.
    """
    return "strong" if independent and template_driven else "weak"


#: **Fixed before any figure exists** (FR-050, SC-029), and fixed in the
#: strongest sense available to a program: it is a committed constant rather
#: than a value computed during a run. A declared label a run could compute is
#: a label that could be computed *after* the figures — which is the one thing
#: the requirement forbids, and the reason `extraction_quality_section` below
#: takes no declared label from its caller.
#:
#: Both conditions in `DECLARED_BASELINE_CRITERION` hold at the time of writing:
#: the independence contract is committed and enforced (T008, before the
#: extractor was authored at T051), and the synthetic layer is generated from
#: fixed per-vendor templates.
DECLARED_BASELINE_LABEL: str = declared_baseline_label(independent=True, template_driven=True)


def _figures_by_cell(figures: Sequence[FieldFigures]) -> dict[tuple[str, str], FieldFigures]:
    return {(figure.layer, figure.field): figure for figure in figures}


def observed_baseline_label(
    model_figures: Sequence[FieldFigures], baseline_figures: Sequence[FieldFigures]
) -> str:
    """FR-050's observed label, **read off the published table**.

    Strong where the baseline beats or ties the model on at least one per-field
    figure; weak where the model dominates every field. "Every field" means
    every field-and-layer cell the two have in common, on both figures — a
    baseline that tied on one recall in one cell is a strong baseline, because
    the claim being tested is "a deterministic opponent could not do this", and
    one tie refutes it.

    Point estimates, not intervals. The label is about what was observed, and
    two overlapping intervals do not make the observation a tie.

    Raises:
        ReportError: the two tables share no cell. A label read off an empty
            comparison would be read off nothing, and "weak" is the answer an
            empty comparison would give — the flattering one.
    """
    model = _figures_by_cell(model_figures)
    baseline = _figures_by_cell(baseline_figures)
    shared = sorted(set(model) & set(baseline))
    if not shared:
        raise ReportError(
            "FR-050: the model's figures and the baseline's share no field-and-layer "
            "cell, so the observed label would be read off an empty comparison — which "
            "would return 'weak' by default and flatter the model."
        )
    for cell in shared:
        if baseline[cell].precision.point >= model[cell].precision.point:
            return "strong"
        if baseline[cell].recall.point >= model[cell].recall.point:
            return "strong"
    return "weak"


def extraction_quality_section(
    *,
    run_id: str,
    model_figures: Sequence[FieldFigures],
    baseline_figures: Sequence[FieldFigures],
    unmeasured_layers: Mapping[str, str],
) -> Section:
    """Item 12: per-field figures, the baseline's, both labels (FR-050, FR-060).

    Args:
        run_id: the run these figures were computed under.
        model_figures: the model path's per-field precision and recall, from
            `model.compute.metrics.per_field_figures`.
        baseline_figures: the deterministic baseline's, over the same documents.
        unmeasured_layers: layers published as *not measured*, each with its
            reason — the real layer and `ingest/reference.py`'s statement of why
            (FR-060, SC-047). A layer row that was blank or read `0/0` is what
            this keeps out of the table.

    Raises:
        ReportError: either table is empty, or the declared label is outside the
            closed pair. An empty table would publish a quality claim with no
            figures under it.

    **The declared label is not a parameter.** It is `DECLARED_BASELINE_LABEL`, a
    committed constant, because FR-050 requires it fixed before any figure
    exists and a parameter is something a caller could compute from the figures
    it is about to publish.

    **A disagreement is published as a finding and is not reconciled** (Principle
    VIII). Neither label is revised to match the other, and nothing here chooses
    between them: the two answer different questions — one about how the
    opponent was built, one about how it did — and a disagreement is information
    about the measurement rather than a defect in it.

    **No F1** (FR-060, SC-047). The omission is published with its reason, which
    `model.compute.metrics` owns so the report and the module cannot state it
    differently.
    """
    if not model_figures or not baseline_figures:
        raise ReportError(
            "FR-050 / FR-060: item 12 publishes per-field precision and recall beside "
            "the baseline's. A table with no figures on one side is not a smaller "
            "table — it is a quality claim with no basis published for it."
        )
    if DECLARED_BASELINE_LABEL not in BASELINE_LABELS:
        raise ReportError(
            f"FR-050: the declared baseline label {DECLARED_BASELINE_LABEL!r} is outside "
            f"{BASELINE_LABELS}"
        )

    observed = observed_baseline_label(model_figures, baseline_figures)
    model = _figures_by_cell(model_figures)
    baseline = _figures_by_cell(baseline_figures)

    comparison = [
        "| Field | Layer | Model precision | Baseline precision | Model recall | Baseline recall |",
        "|---|---|---|---|---|---|",
        *(
            f"| {field} | {layer} | {model[(layer, field)].precision.rendered()} | "
            f"{baseline[(layer, field)].precision.rendered()} | "
            f"{model[(layer, field)].recall.rendered()} | "
            f"{baseline[(layer, field)].recall.rendered()} |"
            for layer, field in sorted(set(model) & set(baseline))
        ),
    ]

    model_only = sorted(set(model) - set(baseline))
    baseline_only = sorted(set(baseline) - set(model))

    disagreement = (
        f"**Finding — the two labels disagree.** The baseline is declared "
        f"**{DECLARED_BASELINE_LABEL}** and observed **{observed}**. The disagreement is "
        f"published as it stands: neither label is revised to match the other, and no "
        f"figure is recomputed to resolve it (Principle VIII). A declared-strong, "
        f"observed-weak baseline means an opponent built to be able to win did not, "
        f"which is a fact about this corpus and this extractor; a declared-weak, "
        f"observed-strong one means an opponent built without the advantages still tied "
        f"or beat the model, which is a stronger result than the declaration claimed."
        if observed != DECLARED_BASELINE_LABEL
        else (
            f"The two labels agree at **{observed}**. That agreement is reported rather "
            f"than assumed: it is what a strong opponent built under the independence "
            f"contract is *expected* to produce, and expecting it is exactly why a "
            f"disagreement would have been worth publishing."
        )
    )

    unmeasured = (
        "\n\n".join(
            f"**Layer `{layer}` is not measured.** {reason}"
            for layer, reason in sorted(unmeasured_layers.items())
        )
        or "Every layer in scope carries figures."
    )

    body = (
        f"Every figure below is a **descriptive figure over a designed set** (FR-072), "
        f"published with the {INTERVAL_METHOD} interval and with its denominator printed. "
        f"The two denominators are different populations and both are stated on every "
        f"figure: precision is denominated on the values the run stored for that field "
        f"and layer, recall on the fields the generator recorded as printed. Recall is "
        f"never denominated on stored values — a recall that could not see a value which "
        f"was never stored would measure nothing.\n\n"
        f"**The expected side of every comparison is the reference set** (FR-067): the "
        f"generator's pre-render document model, reproduced from the committed "
        f"generation inputs and required equal to each manifest's digest before any "
        f"figure here was computed. No figure is scored against the chunk text a value "
        f"was read out of, or against this epic's own parse.\n\n"
        f"**The opponent.** `{BASELINE_ID}`. {BASELINE_INDEPENDENCE}\n\n"
        f"**Declared label: {DECLARED_BASELINE_LABEL}.** Criterion, fixed before any "
        f"figure existed: {DECLARED_BASELINE_CRITERION}\n\n"
        f"**Observed label: {observed}.** Criterion: strong where the baseline beats or "
        f"ties the model on at least one per-field figure, weak where the model "
        f"dominates every field. Read off the table below, on point estimates.\n\n"
        f"{disagreement}\n\n" + "\n".join(comparison) + f"\n\n{unmeasured}\n\n"
        f"**{F1_OMISSION_REASON}**"
    )

    if model_only or baseline_only:
        body += (
            f"\n\nCells published by one side only, and therefore outside the label "
            f"comparison: model only {[f'{field} ({layer})' for layer, field in model_only]}, "
            f"baseline only "
            f"{[f'{field} ({layer})' for layer, field in baseline_only]}. They are listed "
            f"rather than dropped: a cell quietly missing from one side is how a "
            f"comparison narrows to the fields one extractor happened to be good at."
        )

    figures: list[Figure] = []
    for layer, field in sorted(set(model) | set(baseline)):
        for who, table in (("model", model), ("baseline", baseline)):
            entry = table.get((layer, field))
            if entry is None:
                continue
            scope = FigureScope(
                run_id=run_id,
                generation_set="run-scoped",
                kind="descriptive",
                unit="stored value",
                layer=layer,
            )
            figures.append(
                Figure(
                    label=f"precision — {field} ({who})",
                    value=entry.precision.rendered(),
                    scope=scope,
                )
            )
            figures.append(
                Figure(
                    label=f"recall — {field} ({who})",
                    value=entry.recall.rendered(),
                    scope=FigureScope(
                        run_id=run_id,
                        generation_set="run-scoped",
                        kind="descriptive",
                        unit="printed field",
                        layer=layer,
                    ),
                )
            )

    return Section(item=12, body=body, figures=tuple(figures))


# ---------------------------------------------------------------------------
# Item 15 — FR-070, attempted versus recorded invocations
# ---------------------------------------------------------------------------


def reconciliation_section(*, run_id: str, trace_id: str, attempted: int, recorded: int) -> Section:
    """Item 15: attempted against recorded invocations (FR-070, SC-011).

    Args:
        run_id: the run the report describes.
        trace_id: the run's single trace identifier, as recorded on
            `ingestion_run.run_trace_id`.
        attempted: invocations the run issued, from `ingest/cli.py`'s ledger.
        recorded: `llm_invocation` rows carrying `trace_id`.

    Raises:
        ReportError: `attempted` is zero, or either count is negative. A
            zero-attempt reconciliation agrees with a zero-recorded one for no
            reason at all, which is FR-068's empty population reaching the one
            figure that would otherwise be vacuously true.

    **Both counts are published whether or not they agree.** SC-011 measures
    this as a reconciliation and not only as a contract: the placement check
    says no module outside `model.llm` *can* reach the provider, and this says
    no module outside it *did*. A section publishing only the verdict would be
    the claim rather than its basis.

    Scalars rather than a reconciliation object, so `report.py` does not import
    `ingest/cli.py` — the orchestrator imports the report, and the reverse edge
    would close a cycle.
    """
    if attempted < 0 or recorded < 0:
        raise ReportError(
            f"FR-070: invocation counts are non-negative; got attempted={attempted}, "
            f"recorded={recorded}"
        )
    if attempted == 0:
        raise ReportError(
            "FR-070 / FR-068: the reconciliation compares zero attempted invocations, "
            "which agrees with zero recorded ones for no reason at all. A run that "
            "attempted none has no reconciliation to publish; one that attempted some "
            "and counted none has a defect in its ledger."
        )

    agrees = attempted == recorded
    verdict = (
        "The two counts are equal, so every invocation the run attempted is recorded "
        "under the run's trace identifier and no invocation recorded under it came from "
        "anywhere else."
        if agrees
        else (
            f"**The two counts disagree by {recorded - attempted:+d}.** More recorded "
            f"than attempted means a model request was issued outside this run's ledger; "
            f"fewer means an attempted invocation left no row. Either is a defect in the "
            f"traced path, and neither is reconciled by adjusting a count."
        )
    )
    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="invocation",
        layer="SYNTHETIC",
    )
    body = (
        f"Every extraction invocation of this run was issued under the single trace "
        f"identifier `{trace_id}`, recorded on `ingestion_run.run_trace_id`. The "
        f"identifier is passed explicitly on every call into the traced path — the "
        f"gateway reads no ambient context — so a per-invocation identifier could not "
        f"have been substituted without this reconciliation failing.\n\n"
        f"{verdict}\n\n"
        f"The counting unit is the **invocation**: one model request covering one "
        f"chunk's declared field subset. It is not the *attempt*, which is one field on "
        f"one chunk, and the two are published in separate tables for that reason "
        f"(FR-069)."
    )
    return Section(
        item=15,
        body=body,
        figures=(
            Figure(label="Invocations attempted by the run", value=attempted, scope=labels),
            Figure(
                label="Invocations recorded under the run's trace identifier",
                value=recorded,
                scope=labels,
                note="`llm_invocation` rows joined on `ingestion_run.run_trace_id`",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Attempted invocations equal those recorded under the run trace id",
                population=f"every extraction invocation issued under trace id {trace_id}",
                count=attempted,
                scope=labels,
                outcome="held" if agrees else "FAILED",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 6 — FR-033, FR-046, FR-057: floor, eight-score distribution, weights
# ---------------------------------------------------------------------------

#: FR-033's required disclosure, held as one string so the sentence the report
#: prints and the sentence a reader quotes are the same object. Printed **beside
#: the distribution** and not only in a limitations table, with the condition
#: that would reverse it — a claim with no stated way of being wrong is not a
#: disclosure.
HEURISTIC_ORDERING_STATEMENT: str = (
    "**This score is a heuristic ordering, not a calibrated probability.** It is "
    "deterministic arithmetic over three parse signals (FR-031); it is not a frequency, "
    "it has not been fitted to any labelled outcome, and two fields' scores are not "
    "comparable as though they shared a scale. **What would reverse this statement**: a "
    "frozen, hashed, labelled sample of extracted fields against which the score's "
    "ordering could be measured — at which point the number would be reported as a "
    "calibrated quantity with its calibration set named, or withdrawn."
)


@dataclass(frozen=True)
class ConfidenceDistribution:
    """FR-033's distribution: all eight scores, stored and rejected apart.

    **Two populations, counted separately, and the rejected half is carried from
    the run's own tally rather than queried from rows.** A rejected value has no
    row — it was recorded as a failure with outcome `confidence_below_threshold`
    and its score was never stored — so a distribution built by querying
    `extracted_value` would silently be the stored half only, and would report
    that the floor rejected nothing.

    Keys are the eight `ParseSignals` combinations. A combination nothing took
    appears with a count of **zero** rather than as an absent row, which is what
    makes "zero admissible scores are omitted" (SC-017) checkable.
    """

    stored: Mapping[str, int]
    rejected: Mapping[str, int]

    def __post_init__(self) -> None:
        expected = {signals.description for signals in confidence_domain()}
        for name, counts in (("stored", self.stored), ("rejected", self.rejected)):
            if set(counts) != expected:
                missing = sorted(expected - set(counts))
                extra = sorted(set(counts) - expected)
                raise ReportError(
                    f"FR-033: the {name} distribution covers {len(counts)} of the eight "
                    f"combinations FR-057's signals admit; missing={missing} extra={extra}. "
                    f"A score nothing took is published as a zero, not as an absent row."
                )
            if any(value < 0 for value in counts.values()):
                raise ReportError(f"FR-033: the {name} distribution carries a negative count")

    @property
    def stored_total(self) -> int:
        return sum(self.stored.values())

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())

    @property
    def computed_total(self) -> int:
        """Every confidence the run computed, which is what FR-033 denominates on."""
        return self.stored_total + self.rejected_total


def confidence_domain() -> tuple[ParseSignals, ...]:
    """The eight signal combinations, from the module that defines the score.

    Imported through a function rather than at module scope so the report states
    the domain by reference to `model.compute.confidence` — a second enumeration
    here would be a second answer, and the one that disagreed would be the one
    the report printed.
    """
    return SIGNAL_DOMAIN


def tally_confidence(
    stored: Iterable[ParseSignals], rejected: Iterable[ParseSignals]
) -> ConfidenceDistribution:
    """Count the run's computed scores into the eight combinations (FR-033).

    Args:
        stored: the signals of every value the run persisted.
        rejected: the signals of every score the floor rejected, carried from
            the run's own tally. These have no rows to be queried from.

    Returns:
        The distribution, with every one of the eight present and a zero where
        nothing took that combination.
    """
    counts: dict[str, dict[str, int]] = {
        "stored": {signals.description: 0 for signals in confidence_domain()},
        "rejected": {signals.description: 0 for signals in confidence_domain()},
    }
    # No unknown-key branch: `ParseSignals` validates its own domain, and
    # `description` is derived from exactly the three binary facts the domain is
    # built from — so every well-formed signal set lands on one of the eight
    # keys by construction. A guard here would be a branch nothing can reach.
    for population, signal_set in (("stored", stored), ("rejected", rejected)):
        for signals in signal_set:
            counts[population][signals.description] += 1
    return ConfidenceDistribution(stored=counts["stored"], rejected=counts["rejected"])


def confidence_section(
    *, run_id: str, policy: ConfidencePolicy, distribution: ConfidenceDistribution
) -> Section:
    """Item 6: the floor, the eight-score distribution, the weights, the order.

    Args:
        run_id: the run the report describes.
        policy: the floor and the three weights, **read from that run's own
            `ingestion_run` row** rather than from the declared constants. A
            report printing today's policy beside last run's distribution would
            publish a floor that never rejected anything in it.
        distribution: the run's own tally over the eight combinations.

    Returns:
        Item 6, with the distribution as a table and the floor, weights and
        totals as labelled figures.

    Raises:
        ReportError: the run computed no confidence at all. FR-068's rule
            applied to the one figure that would otherwise be vacuously
            complete — a distribution over zero scores omits no admissible
            score and discloses nothing.

    **A distribution, never a mean** (FR-033, Principle II). A mean confidence
    would collapse exactly the shape the floor is defined against: two runs with
    identical means can differ entirely in what the floor rejected.
    """
    if distribution.computed_total <= 0:
        raise ReportError(
            "FR-033: the run computed zero confidences, so the published distribution "
            "would omit no admissible score while disclosing nothing. An empty population "
            "fails rather than passes (FR-068)."
        )

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="descriptive",
        unit="extracted value",
        layer="SYNTHETIC",
    )
    policy_labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="descriptive",
        unit="score",
        layer="pooled",
    )

    rows = [
        "| Label | Provenance | Invocation | Score | Stored | Rejected |",
        "|---|---|---|---|---|---|",
    ]
    for signals in confidence_domain():
        score = compute_confidence(signals, policy.weights)
        key = signals.description
        rows.append(
            f"| {signals.label_match} | "
            f"{'page-split' if signals.page_split else 'single-chunk'} | "
            f"{'repaired' if signals.validated_after_repair else 'first attempt'} | "
            f"{score!r} | {distribution.stored[key]} | {distribution.rejected[key]} |"
        )

    weight_rows = [
        "| Order | Signal | Deduction | Column |",
        "|---|---|---|---|",
    ]
    for position, name in enumerate(DEDUCTION_ORDER, start=1):
        weight_rows.append(
            f"| {position} | {name.replace('_', ' ')} | "
            f"{getattr(policy.weights, name)!r} | `ingestion_run.deduction_{name}` |"
        )

    body = (
        f"**Declared floor: {policy.floor!r}**, fixed before the first run and not moved in "
        f"response to the distribution below (FR-032). It is read from this run's own "
        f"`ingestion_run.confidence_floor`, not from a code constant, so what is printed "
        f"here is the floor that actually decided what was stored.\n\n"
        f"The floor is stated by **what it excludes** rather than by its number: any "
        f"repaired invocation, and any value both alternate-labelled and page-split. Both "
        f"are database facts on the run row — `ck_ingestion_run__floor_excludes_repair` and "
        f"`ck_ingestion_run__floor_excludes_alt_split` — written over the weight columns, "
        f"so a run declaring a floor that fails to reject either is unstorable.\n\n"
        f"**Deductions from 1.0, applied left to right in this order** (FR-046, FR-057). "
        f"The order is part of the record: `double precision` subtraction is not "
        f"associative, so `1.0 - a - p` and `1.0 - (a + p)` need not be bit-identical, and "
        f"SC-026's 'reproduces the stored value exactly' means bit equality.\n\n"
        + "\n".join(weight_rows)
        + "\n\n"
        "**Distribution over all eight scores the three signals admit** (FR-033). A score "
        "nothing took is published as a zero rather than as an absent row. The two "
        "populations are counted apart: **stored** is the values persisted with their "
        "confidence intact, **rejected** is every score the floor refused — carried from "
        "the run's own tally, because a rejected score has no row to be queried from and a "
        "distribution built from rows alone would report that the floor rejected "
        "nothing.\n\n" + "\n".join(rows) + "\n\n" + HEURISTIC_ORDERING_STATEMENT
    )

    return Section(
        item=6,
        body=body,
        figures=(
            Figure(label="Declared confidence floor", value=policy.floor, scope=policy_labels),
            *(
                Figure(
                    label=f"Deduction weight — {name.replace('_', ' ')}",
                    value=getattr(policy.weights, name),
                    scope=policy_labels,
                    note=f"application order position {position} of {len(DEDUCTION_ORDER)}",
                )
                for position, name in enumerate(DEDUCTION_ORDER, start=1)
            ),
            Figure(
                label="Values stored with their confidence intact",
                value=distribution.stored_total,
                scope=labels,
            ),
            Figure(
                label="Scores the floor rejected",
                value=distribution.rejected_total,
                scope=labels,
                note="carried from the run's own tally; a rejected score has no row",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every computed confidence is counted in exactly one of the two populations",
                population="every confidence this run computed, stored and rejected",
                count=distribution.computed_total,
                scope=labels,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 7 — FR-034, the failure count broken down by each of the seven
# ---------------------------------------------------------------------------


def failure_breakdown_section(*, run_id: str, counts: Mapping[str, int], attempts: int) -> Section:
    """Item 7: the failure count by each of the seven outcomes, zeros included.

    Args:
        run_id: the run the report describes.
        counts: one entry per member of `FAILURE_OUTCOMES`, as
            `failures.outcome_counts` produces. An outcome no failure took
            carries a `0`.
        attempts: the run's attempt total, which is the denominator FR-069
            assigns to per-field outcomes.

    Returns:
        Item 7, with one labelled figure per outcome and the attempt
        denominator beside them.

    Raises:
        ReportError: an outcome is missing from `counts`, an outcome outside the
            closed seven appears, a count is negative, the failure total exceeds
            the attempt total, or the attempt total is not positive. The missing
            case matters most: an omitted outcome reads as an outcome that took
            no failures, and the two are what the zero-inclusion rule exists to
            distinguish.

    **Denominated per attempt, and the unit is on every figure** (FR-069,
    SC-018). Per-field outcomes and any failure rate are attempt-level; the
    valid, repaired and failed counts of item 13 are invocation-level. They are
    two tables in two items rather than one table whose rows do not share a
    denominator.
    """
    missing = [outcome for outcome in FAILURE_OUTCOMES if outcome not in counts]
    if missing:
        raise ReportError(
            f"FR-034: the failure breakdown omits {missing}. An outcome no failure took is "
            f"published as a zero — an omitted row and a zero row read the same to a "
            f"reader, and only one of them is a measurement."
        )
    unknown = sorted(set(counts) - set(FAILURE_OUTCOMES))
    if unknown:
        raise ReportError(
            f"FR-034: {unknown} are outside the closed set of seven "
            f"{list(FAILURE_OUTCOMES)}. No new outcome value is introduced."
        )
    if any(value < 0 for value in counts.values()):
        raise ReportError("FR-034: the failure breakdown carries a negative count")
    if attempts <= 0:
        raise ReportError(
            "FR-069: the failure breakdown is denominated on attempts, and this run "
            "reports none. An empty population fails rather than passes (FR-068)."
        )
    total = sum(counts.values())
    if total > attempts:
        raise ReportError(
            f"FR-069: {total} failures against {attempts} attempts. Every attempt resolves "
            f"to exactly one stored value or one failure, so failures cannot exceed "
            f"attempts — the ledger does not reconcile."
        )

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit=ATTEMPT_UNIT,
        layer="SYNTHETIC",
    )
    body = (
        f"**Every extraction failure carries one outcome from the closed set of seven** "
        f"(FR-034), which restates `ck_extraction_failure__outcome`; no eighth value is "
        f"introduced, and an eighth would be a migration and an amendment rather than a "
        f"new label.\n\n"
        f"The breakdown below names **all seven**, an outcome no failure took appearing as "
        f"a **zero** rather than as an absent row — an omitted row and a zero row read the "
        f"same to a reader and only one of them is a measurement.\n\n"
        f"**Counting unit: the attempt** — one field on one chunk, except a field absent "
        f"from a whole document, which is one attempt for that document (FR-069). The "
        f"denominator is this run's {attempts} attempts. The valid, repaired and failed "
        f"counts of item 13 are **invocation**-level and are published in their own table "
        f"for that reason: the two units do not share a denominator and a single table "
        f"would imply they did.\n\n"
        f"Total failures: **{total}** of {attempts} attempts."
    )
    return Section(
        item=7,
        body=body,
        figures=tuple(
            Figure(
                label=f"Failures with outcome `{outcome}`",
                value=counts[outcome],
                scope=labels,
                note="zero published rather than omitted" if counts[outcome] == 0 else None,
            )
            for outcome in FAILURE_OUTCOMES
        )
        + (
            Figure(
                label="Failures, all outcomes",
                value=total,
                scope=labels,
                note=f"denominator: {attempts} attempts",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every failure carries one outcome from the closed set of seven",
                population="every extraction failure this run recorded",
                count=total if total else attempts,
                scope=labels,
                outcome="held" if total else "held (no failure recorded)",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 13 — FR-069, the attempt ledger and its two counting units
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationLedger:
    """SC-018's three counts, at the **invocation** unit.

    One invocation is one model request covering one chunk's declared field
    subset — the unit FR-025's validity and FR-026's repair budget are counted
    in. `repaired` is a success: the value validated, on the second attempt.
    """

    valid: int
    repaired: int
    failed: int

    def __post_init__(self) -> None:
        if min(self.valid, self.repaired, self.failed) < 0:
            raise ReportError("FR-069: invocation counts are non-negative")

    @property
    def total(self) -> int:
        return self.valid + self.repaired + self.failed

    @property
    def repaired_rate(self) -> float:
        """SC-018 requires the repaired rate in its own right, not folded in.

        Raises:
            ReportError: the run issued no invocation. A rate over an empty
                denominator is not zero — it is undefined, and publishing it as
                zero would report a run that repaired nothing.
        """
        if self.total <= 0:
            raise ReportError(
                "FR-069: the repaired rate has no denominator — this run issued no "
                "invocation. An empty population fails rather than passing as a zero rate."
            )
        return self.repaired / self.total


@dataclass(frozen=True)
class AttemptLedger:
    """FR-069's ledger, at the **attempt** unit.

    An attempt is one field on one chunk, except a field absent from a whole
    document, which is one attempt for that document. Every attempt resolves to
    exactly one stored value or one failure record, with zero unaccounted for —
    which is what `unaccounted` measures rather than assumes.
    """

    attempted: int
    stored: int
    failed: int

    def __post_init__(self) -> None:
        if min(self.attempted, self.stored, self.failed) < 0:
            raise ReportError("FR-069: attempt counts are non-negative")
        if self.attempted <= 0:
            raise ReportError(
                "FR-069: the attempt ledger reports zero attempts, which reconciles with "
                "zero resolutions for no reason at all. An empty population fails rather "
                "than passes (FR-068)."
            )

    @property
    def resolved(self) -> int:
        return self.stored + self.failed

    @property
    def unaccounted(self) -> int:
        """Attempts that resolved to neither a stored value nor a failure.

        Signed deliberately: a negative value means more resolutions than
        attempts, which is a different defect from a lost attempt and would be
        hidden by an absolute difference.
        """
        return self.attempted - self.resolved

    @property
    def reconciles(self) -> bool:
        return self.unaccounted == 0


def attempt_ledger_section(
    *, run_id: str, invocations: InvocationLedger, attempts: AttemptLedger
) -> Section:
    """Item 13: valid, repaired and failed, as two tables with their units.

    Args:
        run_id: the run the report describes.
        invocations: SC-018's three counts, at the invocation unit.
        attempts: FR-069's ledger, at the attempt unit.

    Returns:
        Item 13, with the two units in two tables and each figure labelled with
        the unit it counts.

    Raises:
        ReportError: the run issued no invocation, or the ledger reports no
            attempt. Both are refused rather than published as zeros, for the
            reason FR-068 gives.

    **Two tables and not one.** A single table would put an invocation count and
    an attempt count in adjacent rows, which reads as though they shared a
    denominator. They do not: one invocation covers a chunk's whole declared
    field subset, so one invocation is many attempts.
    """
    if invocations.total <= 0:
        raise ReportError(
            "FR-069: the invocation ledger reports zero invocations. A run that issued "
            "none has no valid, repaired or failed counts to publish, and three zeros "
            "would read as a run that tried and failed at nothing."
        )

    invocation_labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit=INVOCATION_UNIT,
        layer="SYNTHETIC",
    )
    attempt_labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit=ATTEMPT_UNIT,
        layer="SYNTHETIC",
    )

    body = (
        f"**The counting units are named beside every figure, and there are three** "
        f"(FR-069). An **attempt** is one field on one chunk, except a field absent from a "
        f"whole document, which is one attempt for that document (FR-058). An "
        f"**invocation** is one model request covering one chunk's declared field subset, "
        f"and is the unit FR-025's validity and FR-026's repair budget are counted in. A "
        f"**document** is the unit of the whole-document absence record and of the "
        f"transaction (FR-054).\n\n"
        f"**Invocation-level** (SC-018). `repaired` is a success — the value validated, on "
        f"the second attempt — and it is published in its own right rather than folded "
        f"into `valid`, because the repair is FR-057's third deduction signal and a run "
        f"that repaired half its invocations is not the same run as one that repaired "
        f"none.\n\n"
        f"| Outcome | Count | Unit |\n|---|---|---|\n"
        f"| valid | {invocations.valid} | invocation |\n"
        f"| repaired | {invocations.repaired} | invocation |\n"
        f"| failed | {invocations.failed} | invocation |\n"
        f"| **total** | {invocations.total} | invocation |\n"
        f"| repaired rate | {invocations.repaired_rate:.4f} | proportion of invocations |\n\n"
        f"**Attempt-level** (FR-069). Every attempt resolves to exactly one stored value or "
        f"one failure record, with zero unaccounted for. The unaccounted count is published "
        f"whether or not it is zero: a ledger that printed only its verdict would be a "
        f"claim about itself.\n\n"
        f"| Term | Count | Unit |\n|---|---|---|\n"
        f"| attempted | {attempts.attempted} | attempt |\n"
        f"| resolved to a stored value | {attempts.stored} | attempt |\n"
        f"| resolved to a failure record | {attempts.failed} | attempt |\n"
        f"| **unaccounted for** | {attempts.unaccounted} | attempt |\n"
    )

    return Section(
        item=13,
        body=body,
        figures=(
            Figure(
                label="Invocations valid on the first attempt",
                value=invocations.valid,
                scope=invocation_labels,
            ),
            Figure(
                label="Invocations valid only after a repair",
                value=invocations.repaired,
                scope=invocation_labels,
                note="FR-057's third deduction signal",
            ),
            Figure(
                label="Invocations that produced no schema-valid value",
                value=invocations.failed,
                scope=invocation_labels,
            ),
            Figure(
                label="Repaired rate",
                value=round(invocations.repaired_rate, 6),
                scope=FigureScope(
                    run_id=run_id,
                    generation_set="run-scoped",
                    kind="descriptive",
                    unit="proportion of invocations",
                    layer="SYNTHETIC",
                ),
            ),
            Figure(
                label="Field extractions attempted", value=attempts.attempted, scope=attempt_labels
            ),
            Figure(
                label="Attempts resolved to a stored value",
                value=attempts.stored,
                scope=attempt_labels,
            ),
            Figure(
                label="Attempts resolved to a failure record",
                value=attempts.failed,
                scope=attempt_labels,
            ),
            Figure(
                label="Attempts unaccounted for",
                value=attempts.unaccounted,
                scope=attempt_labels,
                note="published whether or not it is zero",
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every attempt resolves to exactly one stored value or one failure",
                population="every field extraction this run attempted",
                count=attempts.attempted,
                scope=attempt_labels,
                outcome="held" if attempts.reconciles else "FAILED",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 17 — FR-068, the population and count behind every total check
# ---------------------------------------------------------------------------


def total_checks_section(*, run_id: str, checks: Sequence[TotalCheck]) -> Section:
    """Item 17: every total check, with what it enumerated (FR-068).

    Args:
        run_id: the run the report describes.
        checks: every total check the report claims, from every section.

    Raises:
        ReportError: when no check is supplied. A report claiming nothing total
            has no basis for any of the '100%' or 'zero' criteria this epic
            states, so an empty list is a defect rather than a quiet pass. Each
            individual check has already refused an empty population at
            construction.
    """
    del run_id  # each check carries its own scope; the report head names the run
    if not checks:
        raise ReportError(
            "FR-068: the report publishes no total check. Every criterion phrased as '100%' "
            "or 'zero' rests on one, so a report with none is not emitted."
        )
    body = (
        "**Every total check this report claims is listed here with the population it "
        "enumerated and that population's count.** A check whose population is empty is not "
        "published as a success: `TotalCheck` refuses a count of zero at construction, so a "
        "'100% of chunks' claim over nothing cannot be written down at all.\n\n"
        "Each check below is a **census** — it carries a population and a count and no "
        "interval (FR-072)."
    )
    return Section(item=17, body=body, total_checks=tuple(checks))


def collect_total_checks(sections: Iterable[Section]) -> tuple[TotalCheck, ...]:
    """Every total check the given sections publish, in order.

    Used to build item 17 from the sections that already exist rather than from
    a second list someone maintains by hand — a hand-maintained list is how a
    check ends up in the report without appearing in the census of checks.
    """
    collected: list[TotalCheck] = []
    for section in sections:
        collected.extend(section.total_checks)
    return tuple(collected)


# ---------------------------------------------------------------------------
# Item 20 — FR-072, the scope labels on every figure above
# ---------------------------------------------------------------------------


def collect_figures(sections: Iterable[Section]) -> tuple[Figure, ...]:
    """Every figure the given sections publish, in order.

    Built from the sections themselves rather than from a second list, for the
    reason `collect_total_checks` is: a hand-maintained inventory is how a
    figure ends up in the report without appearing in the census of figures,
    which is the one place its labels would have been checked.
    """
    collected: list[Figure] = []
    for section in sections:
        collected.extend(section.figures)
    return tuple(collected)


@dataclass(frozen=True)
class LabelCensus:
    """FR-072's five labels, counted over every figure the report publishes.

    A census of the labelling rather than of the figures: the interesting number
    is not how many figures there are but that **all** of them carry each label,
    and that the values fall inside the declared vocabularies. `FigureScope`
    refuses a figure without all five at construction, so what this adds is the
    published evidence that the refusal ranged over everything.
    """

    figures: int
    by_kind: Mapping[str, int]
    by_generation_set: Mapping[str, int]
    by_layer: Mapping[str, int]
    by_unit: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.figures <= 0:
            raise ReportError(
                "FR-072 / FR-068: the report publishes no figure, so 'every figure carries "
                "its labels' is true of nothing. An empty population fails rather than "
                "passes."
            )
        for name, tally in (
            ("kind", self.by_kind),
            ("generation set", self.by_generation_set),
            ("layer", self.by_layer),
        ):
            if sum(tally.values()) != self.figures:
                raise ReportError(
                    f"FR-072: {sum(tally.values())} figures carry a {name} and "
                    f"{self.figures} were published. Every figure carries every label."
                )


def census_of_labels(figures: Sequence[Figure]) -> LabelCensus:
    """Count the five labels over `figures`, publishing zeros (FR-072).

    The three closed vocabularies — kind, generation set, layer — are tallied
    over their **whole** declared set, so a kind nothing took appears as a zero
    rather than as an absent row. An omitted row and a zero row read the same to
    a reader and only one of them is a measurement; that rule is FR-034's and it
    applies here for the same reason.

    Counting units are **not** a closed set and are tallied as observed: FR-069
    fixes three units for the ledger figures, but a chunk-length figure counts
    leaves and a cluster count counts clusters, so a closed enumeration here
    would either be wrong or would force every figure into a unit it does not
    have.
    """

    def tally(vocabulary: Sequence[str], of: Sequence[str]) -> dict[str, int]:
        counts = dict.fromkeys(vocabulary, 0)
        for value in of:
            counts[value] += 1
        return counts

    units: dict[str, int] = {}
    for figure in figures:
        units[figure.scope.unit] = units.get(figure.scope.unit, 0) + 1
    return LabelCensus(
        figures=len(figures),
        by_kind=tally(FIGURE_KINDS, [figure.scope.kind for figure in figures]),
        by_generation_set=tally(
            GENERATION_SETS, [figure.scope.generation_set for figure in figures]
        ),
        by_layer=tally(LAYERS, [figure.scope.layer for figure in figures]),
        by_unit=dict(sorted(units.items())),
    )


def scope_labels_section(*, run_id: str, sections: Sequence[Section]) -> Section:
    """Item 20: every figure's run, generation set, kind, unit and layer (FR-072).

    Args:
        run_id: the run record this report describes, named by identifier.
        sections: the report's other sections. Item 20 is built **from** them
            rather than beside them, so a figure added anywhere is counted here
            without anyone remembering to. Its own two figures are necessarily
            outside the count — a census cannot enumerate figures it has not
            produced yet — and are covered by `build_report`, which checks the
            run identifier over every figure in the report including these.

    Returns:
        Item 20, with the census of labels and the total check over it.

    Raises:
        ReportError: a figure names a run other than the one the report
            describes, or no figure is published at all. The first is the defect
            this item exists to catch — a figure carried over from a previous
            run reads as this run's, and every label on it would be correct
            except the one that matters.

    **The labels are a type, not a convention.** `FigureScope` takes all five or
    raises, and there is no constructor that defaults any of them: the label most
    often forgotten is the layer, and it is the one that decides whether a number
    about the 25 transmittals may be read as a number about the corpus. What this
    section adds is the published evidence — the count of figures the rule ranged
    over, and the distribution of each closed vocabulary with its zeros.

    **What a kind means, restated where a reader meets the counts.** A *census*
    carries a population and a count and no interval; a *sampled estimate* is one
    of FR-011's claims with its inspected and defect counts and its bound; a
    *descriptive figure over a designed set* is what the per-field extraction
    figures are — the 25 transmittals are a seeded set from which no population
    was sampled, so their intervals are not confidence statements about
    extraction outside this corpus.
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the report names the run record it describes, by identifier")
    figures = collect_figures(sections)
    foreign = sorted({figure.scope.run_id for figure in figures if figure.scope.run_id != run_id})
    if foreign:
        raise ReportError(
            f"FR-072: this report describes run {run_id!r} and publishes figures computed "
            f"under {foreign}. A figure carried over from another run reads as this run's "
            f"work, and every label on it would be correct except the one that decides "
            f"whether the number describes what the report says it does."
        )
    census = census_of_labels(figures)

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="published figure",
        layer="pooled",
    )
    kinds = "\n".join(f"| {kind} | {census.by_kind[kind]} |" for kind in FIGURE_KINDS)
    sets = "\n".join(f"| {name} | {census.by_generation_set[name]} |" for name in GENERATION_SETS)
    layers = "\n".join(f"| {layer} | {census.by_layer[layer]} |" for layer in LAYERS)
    units = "\n".join(f"| {unit} | {count} |" for unit, count in census.by_unit.items())
    body = (
        f"**Every figure this report publishes carries five labels** (FR-072): the run it "
        f"was computed under, the generation set it ranges over, its kind, its counting "
        f"unit, and its layer. The run record this report describes is named by "
        f"identifier — `{run_id}` — and **every one of the {census.figures} figures below "
        f"names that same run**, which is checked rather than assumed: a figure carried "
        f"over from a previous run would read as this run's work with every other label "
        f"intact.\n\n"
        f"The labels are a type rather than a convention. `FigureScope` takes all five or "
        f"refuses, and defaults none of them; the one most often forgotten is the layer, "
        f"and it is the one that decides whether a number about the 25 synthetic "
        f"transmittals may be read as a number about the 51-document corpus.\n\n"
        f"This census ranges over the report's **other** sections: it cannot enumerate the "
        f"two figures it publishes itself. Those carry the same five labels from the same "
        f"type, and the run-identifier check that closes the gap is `build_report`'s, which "
        f"ranges over every figure and every total check the report contains.\n\n"
        f"**Generation set.** A *corpus-resident* figure is computed by query over the "
        f"generations resident when this report was written, naming the runs they belong "
        f"to. A *run-scoped* figure ranges over the one named run's own work.\n\n"
        f"| Generation set | Figures |\n|---|---|\n{sets}\n\n"
        f"**Kind.** A *census* carries a population and a count and **no** interval "
        f"(FR-068). A *sampled estimate* is one of FR-011's claims with its inspected "
        f"count, defect count and bound. A *descriptive figure over a designed set* is "
        f"what the per-field extraction figures are (FR-060): the 25 transmittals are a "
        f"seeded set from which no population was sampled, so their intervals are "
        f"descriptive and must not be read as confidence statements about extraction "
        f"outside this corpus.\n\n"
        f"| Kind | Figures |\n|---|---|\n{kinds}\n\n"
        f"**Layer.** A figure whose population spans both layers is published per layer as "
        f"well as pooled (FR-053, FR-061), so `pooled` is a member of the vocabulary "
        f"rather than the absence of one.\n\n"
        f"| Layer | Figures |\n|---|---|\n{layers}\n\n"
        f"**Counting unit.** FR-069 fixes three units for the ledger figures — attempt, "
        f"invocation, document — and they are what an attempt-level and an "
        f"invocation-level number are kept apart by. The vocabulary is **not** closed: a "
        f"leaf-length figure counts leaves and a cluster count counts clusters, and forcing "
        f"those into one of the three would label them with a unit they do not have. The "
        f"units observed:\n\n"
        f"| Unit | Figures |\n|---|---|\n{units}\n"
    )
    return Section(
        item=20,
        body=body,
        figures=(
            Figure(
                label="Published figures carrying all five scope labels",
                value=census.figures,
                scope=labels,
                note=f"every one names run `{run_id}`",
            ),
            Figure(
                label="Distinct counting units in use",
                value=len(census.by_unit),
                scope=labels,
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every published figure carries its run, generation set, kind, unit and layer",
                population="every figure the report's other sections publish",
                count=census.figures,
                scope=labels,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 16 — FR-073, the per-document disposition ledger (T086)
# ---------------------------------------------------------------------------

#: FR-073's closed four. Declared here rather than in `ingest/cli.py` because
#: this module is the one every other module in the package may import: `cli`
#: imports `report`, so the reverse edge would be a cycle. `cli` re-exports
#: them, as it already does for the three counting units.
DISPOSITION_INGESTED: str = "ingested"
DISPOSITION_SKIPPED_UNCHANGED: str = "skipped_unchanged"
DISPOSITION_ROLLED_BACK: str = "rolled_back"
DISPOSITION_NOT_REACHED: str = "not_reached"
DISPOSITIONS: tuple[str, ...] = (
    DISPOSITION_INGESTED,
    DISPOSITION_SKIPPED_UNCHANGED,
    DISPOSITION_ROLLED_BACK,
    DISPOSITION_NOT_REACHED,
)

#: What each disposition means, in FR-073's own words. Published beside the
#: counts because three of the four leave a document with no new rows, and a
#: reader looking at the corpus cannot tell them apart: a skipped document, a
#: rolled-back one and one never begun are identical in the database and differ
#: only in what the run did.
DISPOSITION_MEANINGS: Mapping[str, str] = {
    DISPOSITION_INGESTED: "a generation was written for it",
    DISPOSITION_SKIPPED_UNCHANGED: (
        "its input tuple was unchanged, so FR-043 created no rows for it"
    ),
    DISPOSITION_ROLLED_BACK: "it was the document in flight when the run aborted",
    DISPOSITION_NOT_REACHED: "it was enumerated but never begun",
}


def disposition_section(*, run_id: str, counts: Mapping[str, int], enumerated: int) -> Section:
    """Item 16: every enumerated document under exactly one of four (FR-073).

    Args:
        run_id: the run this report describes.
        counts: the four counts, keyed by `DISPOSITIONS`. All four are required
            **including zeros** — a disposition holding no documents is
            published as a zero, because an omitted row and a zero row read the
            same to a reader and only one of them is a measurement.
        enumerated: the document count FR-068 publishes for this run. The four
            counts must sum to it.

    Returns:
        Item 16, with a figure per disposition and the total check over the sum.

    Raises:
        ReportError: a disposition is missing, one appears that is not among the
            four, a count is negative, or the four do not sum to `enumerated`.

    **The sum is an assertion, not a printed total.** A ledger that does not sum
    is precisely the defect FR-073 exists to prevent — a document a run silently
    dropped is invisible because nothing claims it — so the four counts are
    checked against the enumerated corpus here and refused rather than published
    beside a total a reader is left to verify. `cli.DispositionLedger` asserts
    the same property one level earlier, over the identifiers rather than the
    counts, so a document claimed by two dispositions is caught where four
    counts summing correctly would hide it.

    **The skipped count is published rather than inferred** (SC-055). A skipped
    document creates no rows, which is exactly what a document the run never
    reached also leaves behind; the two are distinguishable only because the run
    wrote down which it was.
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the report names the run record it describes, by identifier")
    missing = [name for name in DISPOSITIONS if name not in counts]
    if missing:
        raise ReportError(
            f"FR-073: the disposition ledger omits {missing}. All four are published, "
            f"including the ones holding no documents — an omitted row and a zero row read "
            f"the same and only one of them is a measurement."
        )
    outside = sorted(set(counts) - set(DISPOSITIONS))
    if outside:
        raise ReportError(
            f"FR-073: {outside} is outside the closed four {list(DISPOSITIONS)}. A fifth "
            f"disposition would make the partition sum by admitting a category nothing "
            f"defined."
        )
    negative = sorted(name for name in DISPOSITIONS if counts[name] < 0)
    if negative:
        raise ReportError(f"FR-073: {negative} carry a negative count")
    total = sum(counts[name] for name in DISPOSITIONS)
    if enumerated <= 0:
        raise ReportError(
            "FR-073 / FR-068: the disposition ledger ranges over zero enumerated documents. "
            "An empty population fails rather than passes — four zeros summing to zero is a "
            "ledger that balances because nothing happened."
        )
    if total != enumerated:
        raise ReportError(
            f"FR-073: the four dispositions hold {total} documents and the run enumerated "
            f"{enumerated}. They partition the enumerated corpus; a shortfall is a document "
            f"the run lost track of and a surplus is one counted twice."
        )

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit=DOCUMENT_UNIT,
        layer="pooled",
    )
    rows = "\n".join(
        f"| `{name}` | {counts[name]} | {DISPOSITION_MEANINGS[name]} |" for name in DISPOSITIONS
    )
    body = (
        f"**Every one of the {enumerated} documents this run enumerated carries exactly one "
        f"of four dispositions**, and the four sum to the enumerated count (FR-073, SC-055). "
        f"The sum is asserted where the ledger is built rather than printed for a reader to "
        f"check: a ledger that does not add up is the defect this requirement exists to "
        f"prevent, because a document a run silently dropped is invisible precisely because "
        f"nothing claims it.\n\n"
        f"| Disposition | Documents | Meaning |\n|---|---|---|\n{rows}\n\n"
        f"**Three of these four leave the same trace in the database — none.** A document "
        f"skipped as unchanged, one rolled back when the run aborted, and one the run never "
        f"began are indistinguishable by query: all three have no new rows. They are "
        f"distinguishable only because the run wrote down which it was, which is why the "
        f"skipped count is **published rather than inferred from the absence of new rows** "
        f"and why a run that aborted publishes what it never reached separately from what it "
        f"rolled back.\n\n"
        f"**A disposition holding no document is published as a zero**, never omitted. An "
        f"omitted row and a zero row read the same to a reader and only one of them is a "
        f"measurement — the same rule FR-034 applies to the seven failure outcomes.\n\n"
        f"`rolled_back` holds at most one document: the per-document transaction means the "
        f"run aborts with one document in flight, and everything committed before it stays "
        f"durable with its generation active (FR-042)."
    )
    return Section(
        item=16,
        body=body,
        figures=tuple(
            Figure(
                label=f"Documents {name}",
                value=counts[name],
                scope=labels,
                note=DISPOSITION_MEANINGS[name],
            )
            for name in DISPOSITIONS
        ),
        total_checks=(
            TotalCheck(
                name="Every enumerated document carries exactly one disposition",
                population="every document this run enumerated, both layers",
                count=enumerated,
                scope=labels,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 18 — FR-064, the index window and what an abort leaves (T083)
# ---------------------------------------------------------------------------

#: E003's object, named exactly as migration `0004` declares it. Written down
#: because the runbook's `CREATE INDEX` must reproduce the name verbatim: a
#: rebuild under any other name leaves the live schema disagreeing with
#: `specs/00003-core-data-schema/data-model.md`, which is normative over it.
VECTOR_INDEX_NAME: str = "ix_chunk__embedding_hnsw"


def index_procedure_section(*, run_id: str, chunks_resident: int) -> Section:
    """Item 18: the sequential-scan window, and the index absent after an abort.

    Args:
        run_id: the run this report describes.
        chunks_resident: the chunks a sequential scan would range over while the
            index is absent — the number that decides whether the window is
            tolerable, so it is published rather than described.

    Returns:
        Item 18, with the scanned population as its figure.

    Raises:
        ReportError: `chunks_resident` is not positive. A window measured over
            no rows is not a measured window (FR-068).

    **Both disclosed states, neither presented as closed** (FR-064). Between the
    drop and the rebuild every similarity query falls back to a sequential scan:
    correctness is unaffected and latency is not, which is acceptable offline
    and is what makes the procedure viable at all. And **an aborted run leaves
    the index absent until the procedure is re-run** — nothing in the database
    restores it, no migration recreates it on an already-migrated database, and
    a retrieval consumer would get correct-but-slow answers with no signal.
    That residual is carried as **G-7**, whose closure is a startup check
    reading `pg_indexes` before serving.

    The procedure itself is `src/model/README.md` (T083). It is **not reachable
    from the ingestion job**: `DROP INDEX` requires ownership of the table, the
    job connects as `procurement_app`, and that role owns nothing.
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the report names the run record it describes, by identifier")
    if chunks_resident <= 0:
        raise ReportError(
            f"FR-064 / FR-068: the sequential-scan window is published over "
            f"{chunks_resident} resident chunks. A window measured over no rows says nothing "
            f"about whether the window is tolerable, and an empty population fails rather "
            f"than passes."
        )
    labels = FigureScope(
        run_id=run_id,
        generation_set="corpus-resident",
        kind="census",
        unit="chunk",
        layer="pooled",
    )
    body = (
        f"**The vector index is dropped before a full-corpus load and rebuilt after it**, as "
        f"an operator procedure under the schema-owning role (FR-064, AD-006, "
        f"`src/model/README.md`). `{VECTOR_INDEX_NAME}` is E003's object, declared in "
        f"migration `0004`; `DROP INDEX` requires ownership of `chunk`, and the ingestion "
        f"job connects as `procurement_app`, which holds table-level grants and owns "
        f"nothing. **The job cannot perform this and is not meant to.**\n\n"
        f"**Two states this opens, both disclosed rather than presented as closed.**\n\n"
        f"1. **While the index is absent, every similarity query falls back to a sequential "
        f"scan.** Correctness is unaffected — a sequential scan returns the exact nearest "
        f"neighbours, where the HNSW index returns approximate ones — and latency is. The "
        f"window ranges over the {chunks_resident} chunks resident at the time of this "
        f"report, which is the number that decides whether the window is tolerable; E003's "
        f"scale note records exact scan as viable at this order of magnitude, so the cost is "
        f"offline latency rather than a wrong answer.\n"
        f"2. **An aborted run leaves the index absent until the procedure is re-run.** "
        f"Nothing in the database restores it. No migration recreates it on an "
        f"already-migrated database — the drop and rebuild is deliberately not a revision, "
        f"since a revision would run on every fresh database where there is nothing to load "
        f"and nothing to gain. A retrieval consumer starting against such a database gets "
        f"correct-but-slow answers **with no signal at all**, which is the failure this "
        f"paragraph exists to make visible. Carried as **G-7**; its closure is a startup "
        f"check reading `pg_indexes` before serving, and it belongs to the consumer rather "
        f"than to this epic.\n\n"
        f"**The rebuild reproduces the declaration verbatim** — same name, same operator "
        f"class, same `m` and `ef_construction`. Any deviation makes the live schema "
        f"disagree with `specs/00003-core-data-schema/data-model.md`, which is normative "
        f"over `chunk`. Not `CREATE INDEX CONCURRENTLY`: it is slower and buys availability "
        f"an offline job does not need."
    )
    return Section(
        item=18,
        body=body,
        figures=(
            Figure(
                label="Chunks a sequential scan ranges over while the index is absent",
                value=chunks_resident,
                scope=labels,
                note=f"`{VECTOR_INDEX_NAME}` dropped and rebuilt around a full-corpus load",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Item 19 — FR-074, the reproduction tolerance in force for each figure (T087)
# ---------------------------------------------------------------------------

#: FR-074's committed artifact, beside the report it describes. Relative to the
#: repository root, like `REPORT_PATH`: this module writes no file, so nothing
#: here depends on a working directory.
RESULTS_MANIFEST_PATH = Path("specs/00006-document-ingestion-and-extraction/results-manifest.json")


def reproduction_section(*, run_id: str, sections: Sequence[Section]) -> Section:
    """Item 19: the tolerance in force for each figure the report publishes.

    Args:
        run_id: the run this report describes.
        sections: the report's other sections. Item 19 is built **from** them,
            as item 20 is, so a figure added anywhere is covered here without
            anyone remembering to — a hand-kept list is how a figure ends up
            published with no stated tolerance.

    Returns:
        Item 19, with the census of tolerances over the report's figures.

    Raises:
        ReportError: a figure carries a tolerance outside the two declared
            classes, or no figure is published at all.

    **The tolerance is printed beside every figure, not only counted here.**
    `Figure.tolerance` is a field with no way to omit it and it is rendered as a
    column of every figure table in this report, so the band a reader is
    checking against is in the same row as the number. This section publishes
    the *census* — how many figures fall in each class — and the reason the two
    classes are what they are.

    **A reproduction outside the stated band is published as a failure of the
    gate.** Widening the band to admit an observed value is adjusting a target
    to match a result, which Principle VII forbids; the band is declared here,
    before the comparison, and the reproduction job compares against it.
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the report names the run record it describes, by identifier")
    figures = collect_figures(sections)
    if not figures:
        raise ReportError(
            "FR-074 / FR-068: no figure was published, so 'every figure carries its "
            "reproduction tolerance' is true of nothing. An empty population fails rather "
            "than passes."
        )
    undeclared = sorted({f.tolerance for f in figures} - set(REPRODUCTION_TOLERANCES))
    if undeclared:
        raise ReportError(
            f"FR-074: {undeclared} is outside the declared reproduction classes "
            f"{list(REPRODUCTION_TOLERANCES)}. A third band invented at a call site is a "
            f"band nobody declared before the comparison, which is the direction Principle "
            f"VII forbids."
        )
    tally = {
        tolerance: sum(1 for figure in figures if figure.tolerance == tolerance)
        for tolerance in REPRODUCTION_TOLERANCES
    }

    labels = FigureScope(
        run_id=run_id,
        generation_set="run-scoped",
        kind="census",
        unit="published figure",
        layer="pooled",
    )
    rows = "\n".join(
        f"| {tolerance} | {tally[tolerance]} |" for tolerance in REPRODUCTION_TOLERANCES
    )
    body = (
        f"**Every figure this report publishes carries the tolerance it must reproduce "
        f"within, in its own row** (FR-074). The band is a field on the figure with no way "
        f"to omit it, rendered as a column of the same table the value is in — a figure "
        f"without its tolerance is not reproducible, and a reader checking a reproduction "
        f"should never have to resolve a reference to find what they are checking "
        f"against.\n\n"
        f"| Reproduction tolerance | Figures |\n|---|---|\n{rows}\n\n"
        f"**Exact means bit equality**, and it covers every count, every rate derived from "
        f"counts, every interval computed from them, and every stored confidence. None of "
        f"those is floating-point-sensitive in the way the phrase usually implies: the "
        f"confidences are deductions applied left to right in a declared order from weights "
        f"read off the run's own row, and the intervals are computed from integer counts.\n\n"
        f"**One class is not claimed exact, and it is named rather than left to be "
        f"discovered.** The near-duplicate cluster counts at each declared similarity "
        f"threshold (item 11, FR-061) are computed from floating-point vectors produced by "
        f"the exported encoder, so they reproduce within the encoder parity band ADR-0018 "
        f"declares and FR-019 measures: **{REPRODUCTION_ENCODER_PARITY}**. The observed "
        f"maxima are published beside the bound in item 21. The exact-match cluster counts "
        f"in the same section are **not** in this class — they compare normalized text, so "
        f"no vector enters them.\n\n"
        f"**Reproduction is measured by a replay-mode run from a clean checkout** (FR-045) "
        f"against the committed results manifest at `{RESULTS_MANIFEST_PATH}`, which carries "
        f"every figure's label, value and tolerance. A reproduction outside the stated band "
        f"is published as a **failure of the gate**; widening the band to admit it would be "
        f"adjusting a target to match a result, which Principle VII forbids."
    )
    return Section(
        item=19,
        body=body,
        figures=(
            Figure(
                label="Figures reproducing exactly",
                value=tally[REPRODUCTION_EXACT],
                scope=labels,
            ),
            Figure(
                label="Figures reproducing within the encoder parity band",
                value=tally[REPRODUCTION_ENCODER_PARITY],
                scope=labels,
                note=REPRODUCTION_ENCODER_PARITY,
            ),
        ),
        total_checks=(
            TotalCheck(
                name="Every published figure names the tolerance it reproduces within",
                population="every figure the report's other sections publish",
                count=len(figures),
                scope=labels,
            ),
        ),
    )


def results_manifest(sections: Sequence[Section], *, run_id: str) -> str:
    """FR-074's committed artifact: every figure, its value, and its tolerance.

    Args:
        sections: the report's sections. The manifest is built from the same
            objects the report renders, so the two cannot disagree — a manifest
            assembled from a second list is a second set of numbers.
        run_id: the run these figures were computed under.

    Returns:
        The manifest as JSON text, ready to replace the committed artifact at
        `RESULTS_MANIFEST_PATH`. Sorted by figure label and rendered with a
        fixed indent, so a reproduction diff shows a changed *value* rather than
        a reordering.

    Raises:
        ReportError: no figure was published, a figure names a run other than
            the one given, or two figures share a label. The last is what makes
            the manifest checkable at all: a comparison keyed on a label that
            names two different numbers cannot report which one moved.

    **The run identifier is recorded and is not part of the comparison key.** It
    changes on every run by construction, so a reproduction compares labels,
    values and tolerances; the identifier is carried so the manifest says which
    run produced the values it holds.
    """
    if not str(run_id).strip():
        raise ReportError("FR-072: the manifest names the run record it describes, by identifier")
    figures = collect_figures(sections)
    if not figures:
        raise ReportError(
            "FR-074 / FR-068: the results manifest would carry no figure, so a reproduction "
            "against it would pass having compared nothing."
        )
    foreign = sorted({f.scope.run_id for f in figures if f.scope.run_id != run_id})
    if foreign:
        raise ReportError(
            f"FR-072: the manifest describes run {run_id!r} and carries figures computed "
            f"under {foreign}."
        )
    seen: dict[str, Figure] = {}
    for figure in figures:
        if figure.label in seen:
            raise ReportError(
                f"FR-074: two figures are published under the label {figure.label!r}. The "
                f"manifest is keyed on the label, so a duplicate makes a reproduction unable "
                f"to say which of the two moved."
            )
        seen[figure.label] = figure
    payload = {
        "run_id": run_id,
        "figures": [
            {
                "label": label,
                "value": seen[label].value,
                "tolerance": seen[label].tolerance,
                "unit": seen[label].scope.unit,
                "layer": seen[label].scope.layer,
                "kind": seen[label].scope.kind,
                "generation_set": seen[label].scope.generation_set,
            }
            for label in sorted(seen)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
