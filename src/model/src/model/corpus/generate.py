"""The offline, seeded synthetic submittal generator — `corpus-generate`.

FR-019 / FR-020 / FR-021 / FR-022 / FR-024 / FR-025, and the manifest emission
of FR-009 / FR-009b / FR-017a / FR-031.

**Nothing here declares a project or a vendor.** Both come from
`model.roster.reader.read_roster()` and nowhere else (FR-019). This module does
not name the roster's path, does not parse it, and does not copy an identifier
into a literal: E001 owns that file, two epics consume it, and a second
definition of the same data is how two consumers come to disagree about what
"unchanged" means. The roster's `content_hash` is recorded **verbatim**, exactly
as the reader emits it (FR-020, VR-029) — it is a digest over the roster's
canonical re-serialized content, so recomputing it from bytes here would record
a different number under the same name.

**Nothing here reaches a network or a language model** (FR-022). That is
enforced rather than asserted: an `import-linter` forbidden contract from
`model.corpus` to `model.llm` and to `gateway` with `allow_indirect_imports =
false` gates every commit, and a socket guard installed before this package is
imported covers the run itself (VR-043, VR-044). The imports below are stdlib
plus this package plus ReportLab, reached through `render.py`.

**Nothing here reads a clock.** Every date on every document is derived from
the committed `generation_date` constant by deterministic arithmetic (FR-009a,
DM-3), so a re-run under an unchanged seed and roster rewrites no byte and
VR-042's byte comparison stays a check on the writer rather than on the day it
ran.

**Coverage is asserted before anything is written.** FR-024's five projects and
twelve vendors and FR-025's per-project resubmittal chain are checked over the
planned layer, and a shortfall raises before the first PDF exists. A generator
that emitted twenty-four conforming documents and then discovered the
twenty-fifth was impossible would leave a corpus location half-populated and
every population rule reporting a number nobody chose.

**The injector seam is filled, and it is on by default.** `inject` rewrites the
planned documents — adding irregularity classes, substituting labels, blanking
values, splitting a field across a page boundary, selecting degradation
profiles — between planning and model construction, and `degrader` renders a
page whose directive names a profile. `irregularity.inject` and
`degrade.degrade_page` are the defaults rather than an opt-in, because the
committed layer is generated with them: a plain `corpus-generate` that produced
a *clean* layer would not reproduce the committed one, and VR-040a, VR-041 and
VR-042 all compare a plain re-run against exactly that. Passing `inject=None`
asks for the clean layer explicitly, which is what the deriver's negative
fixtures want and nothing else does.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType

from model.corpus.codes import (
    ACTION_CODES,
    APPROVING_AUTHORITIES,
    DESCRIPTOR_CODES,
    ActionCode,
    DescriptorCode,
    canonical_label,
)
from model.corpus.degrade import degrade_page
from model.corpus.equipment import CATEGORIES, section_for_category, unbacked_sections
from model.corpus.irregularity import inject as inject_irregularities
from model.corpus.manifest import (
    GENERATION_INPUT_PATHS,
    IRREGULARITY_CLASSES,
    Manifest,
    SyntheticEntry,
    SyntheticLicenseBasis,
    content_hash_of_file,
    generation_input_digests,
    roster_digest,
    write_manifest,
)
from model.corpus.model import DocumentModel, FieldValue, Page, RenderDirective, document_model_hash
from model.corpus.paths import (
    DEFAULT_CORPUS_ROOT,
    REPO_ROOT,
    CorpusPathError,
    repository_relative_path,
)
from model.corpus.render import UNDEGRADED_PROFILE, DocumentLayout, PageRender, render_document
from model.corpus.templates import (
    LABEL_SUFFIX,
    FieldEntry,
    Line,
    assign_templates,
    layout_lines,
    page_text,
    template,
)
from model.roster.reader import Roster, read_roster

__all__ = [
    "CONFIG_INPUT_PATH",
    "DEFAULT",
    "SYNTHETIC_LAYER_DIRECTORY",
    "DocumentPlan",
    "GeneratedDocument",
    "GenerationConfig",
    "GenerationResult",
    "GeneratorError",
    "MaterialItem",
    "build_document",
    "compose_layer",
    "generate_corpus",
    "load_config",
    "main",
    "plan_layer",
]

#: The sentinel meaning "use this epic's own injector / degrader". Needed
#: because `None` already means something different and load-bearing: an
#: explicit request for a layer with no injection at all. Two distinct absences
#: collapsed into one value would make "clean layer" unrequestable.
DEFAULT = object()

# Taken from the closed three `manifest.py` holds rather than written out again.
CONFIG_INPUT_PATH = next(
    path for path in GENERATION_INPUT_PATHS if path.endswith("generation-config.json")
)

# The layer directory, corpus-relative. Each location beneath it is named for a
# roster project id (FR-017a); nothing here enumerates those names.
SYNTHETIC_LAYER_DIRECTORY = "synthetic"

LICENSE_STATEMENT = (
    "Generated by this project from a committed seed and the project/vendor roster. "
    "No third-party material is reproduced and no third-party rights attach."
)

REMARKS = (
    "Remarks: review comments recorded on the government copy of this transmittal.",
    "Distribution: contractor file, government file, resident office.",
)


class GeneratorError(ValueError):
    """Raised when generation cannot proceed or would emit a non-conforming layer.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — this layer must not be
    written, and no manifest may be built from it.
    """


# ---------------------------------------------------------------------------
# The committed generation configuration (FR-009a)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationConfig:
    """The parsed contents of `generation-config.json`.

    `generation_date` is a **committed constant**, never a wall-clock read.
    That is the whole of FR-009a: a generator stamping today's date would
    rewrite every manifest entry on every run and turn VR-042's byte comparison
    into a test of the calendar.
    """

    generator_id: str
    seed: int
    generation_date: str
    renderer_requirement: str
    documents_per_project: int
    pages_per_document: int
    items_min: int
    items_max: int
    degradation_profiles: Mapping[str, Mapping[str, Mapping[str, int]]]
    path: Path

    @property
    def base_date(self) -> date:
        return date.fromisoformat(self.generation_date)


def _require(payload: Mapping[str, object], key: str, kind: type, what: str) -> object:
    if key not in payload:
        raise GeneratorError(f"{what} is missing the required key {key!r}")
    value = payload[key]
    if kind is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise GeneratorError(f"{what}.{key} must be an integer, found {type(value).__name__}")
    elif not isinstance(value, kind):
        raise GeneratorError(
            f"{what}.{key} must be a {kind.__name__}, found {type(value).__name__}"
        )
    if kind is str and not str(value).strip():
        raise GeneratorError(f"{what}.{key} must not be empty or whitespace-only")
    return value


def load_config(path: Path | None = None, *, root: Path | None = None) -> GenerationConfig:
    """Read and validate the committed generation configuration.

    The degradation profiles are validated here even though nothing in this
    phase injects one: the profile names and their parameter ranges are the
    **declared parameter domain** FR-032 holds the injector's boundedness over,
    so they have to be a closed, machine-readable set before an injector exists
    rather than after. Parameters are integer-ranged deliberately — the
    document model rejects floats outright, because a float's shortest
    representation is a property of the platform rather than of the value
    (DM-3).
    """
    if path is not None:
        target = Path(path)
    else:
        try:
            target = repository_relative_path(CONFIG_INPUT_PATH, root)
        except CorpusPathError as exc:
            raise GeneratorError(f"cannot resolve the generation configuration: {exc}") from exc

    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise GeneratorError(f"cannot read the generation configuration {target}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeneratorError(f"{target} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise GeneratorError(
            f"the generation configuration must be an object, found {type(payload).__name__}"
        )

    known = {
        "generator_id",
        "seed",
        "generation_date",
        "renderer_requirement",
        "documents_per_project",
        "pages_per_document",
        "items_per_document",
        "degradation",
    }
    unexpected = sorted(set(payload) - known)
    if unexpected:
        raise GeneratorError(f"{target} carries unexpected top-level keys {unexpected}")

    what = "the generation configuration"
    generation_date = str(_require(payload, "generation_date", str, what))
    try:
        date.fromisoformat(generation_date)
    except ValueError as exc:
        raise GeneratorError(
            f"{what}.generation_date is not a real calendar date: {generation_date!r} ({exc})"
        ) from exc

    items = _require(payload, "items_per_document", Mapping, what)
    items_min = int(_require(items, "min", int, f"{what}.items_per_document"))
    items_max = int(_require(items, "max", int, f"{what}.items_per_document"))
    if not 1 <= items_min <= items_max:
        raise GeneratorError(
            f"{what}.items_per_document must satisfy 1 <= min <= max, "
            f"found min={items_min} max={items_max}"
        )
    if items_max > len(CATEGORIES):
        raise GeneratorError(
            f"{what}.items_per_document.max is {items_max} but the equipment-category map "
            f"holds only {len(CATEGORIES)} categories to draw distinct items from"
        )

    degradation = _require(payload, "degradation", Mapping, what)
    raw_profiles = _require(degradation, "profiles", Mapping, f"{what}.degradation")
    if not raw_profiles:
        raise GeneratorError(f"{what}.degradation.profiles must not be empty")
    profiles: dict[str, Mapping[str, Mapping[str, int]]] = {}
    for name in sorted(raw_profiles):
        if name == UNDEGRADED_PROFILE:
            raise GeneratorError(
                f"{what}.degradation.profiles must not declare {UNDEGRADED_PROFILE!r}; that name "
                "is reserved by the renderer for a page carrying no degradation"
            )
        parameters = _require(raw_profiles, name, Mapping, f"{what}.degradation.profiles")
        if not parameters:
            raise GeneratorError(f"{what}.degradation.profiles[{name!r}] declares no parameter")
        bounds: dict[str, Mapping[str, int]] = {}
        profile_where = f"{what}.degradation.profiles[{name!r}]"
        for parameter in sorted(parameters):
            where = f"{profile_where}[{parameter!r}]"
            record = _require(parameters, parameter, Mapping, profile_where)
            low = int(_require(record, "min", int, where))
            high = int(_require(record, "max", int, where))
            if low > high:
                raise GeneratorError(f"{where} has min {low} above max {high}")
            bounds[parameter] = MappingProxyType({"min": low, "max": high})
        profiles[name] = MappingProxyType(bounds)

    documents_per_project = int(_require(payload, "documents_per_project", int, what))
    pages_per_document = int(_require(payload, "pages_per_document", int, what))
    if documents_per_project < 2:
        raise GeneratorError(
            f"{what}.documents_per_project must be at least 2; FR-025 requires a resubmittal "
            f"chain in every project and a chain needs two documents, found "
            f"{documents_per_project}"
        )
    if pages_per_document != 2:
        raise GeneratorError(
            f"{what}.pages_per_document must be 2: page one carries the transmittal block and "
            f"page two the item list, and PAGE_SPLIT_FIELD is a relation between them; "
            f"found {pages_per_document}"
        )

    return GenerationConfig(
        generator_id=str(_require(payload, "generator_id", str, what)),
        seed=int(_require(payload, "seed", int, what)),
        generation_date=generation_date,
        renderer_requirement=str(_require(payload, "renderer_requirement", str, what)),
        documents_per_project=documents_per_project,
        pages_per_document=pages_per_document,
        items_min=items_min,
        items_max=items_max,
        degradation_profiles=MappingProxyType(profiles),
        path=target,
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterialItem:
    """One item on a submittal, and the section its category answers.

    `section` is not stored independently of `category` — it is resolved
    through `section_for_category`, so an item naming a category the committed
    map does not hold cannot be constructed (FR-026).
    """

    category: str
    tag: str
    quantity: int

    @property
    def section(self) -> str:
        return section_for_category(self.category)

    @property
    def description(self) -> str:
        return f"{self.category.replace('_', ' ').title()} (Tag {self.tag})"


@dataclass(frozen=True)
class DocumentPlan:
    """Everything decided about one document before any text is composed.

    The seam the Phase-6 injectors act on: an injector receives the planned
    layer, returns a rewritten one, and every downstream step — the model, the
    hash, the render, the entry — follows from the plan it returns. Nothing
    downstream re-decides anything a plan already fixed, so an injected change
    is inside the reproducibility hash by construction rather than by an
    injector remembering to update it.
    """

    document_id: str
    project_id: str
    project_name: str
    vendor_id: str
    vendor_name: str
    submittal_number: str
    revision_suffix: str
    contract_number: str
    template_id: str
    descriptor: DescriptorCode
    action: ActionCode
    approving_authority: str
    dates: tuple[tuple[str, str], ...]
    items: tuple[MaterialItem, ...]
    page_directives: tuple[RenderDirective, ...]
    irregularity_classes: tuple[str, ...] = ()
    field_labels: Mapping[str, str] = MappingProxyType({})
    blank_fields: frozenset[str] = frozenset()
    #: The transmittal field whose label ends page one and whose value opens
    #: page two (`PAGE_SPLIT_FIELD`). Recorded on the plan rather than performed
    #: by the injector because `build_document` below is the single place a
    #: page's lines are decided, and a second composer would be a second answer
    #: to what the page says.
    split_field: str = ""

    @property
    def location(self) -> str:
        return f"{self.document_id}.pdf"

    @property
    def specification_section(self) -> str:
        """The section this transmittal answers: its first item's (FR-026)."""
        return self.items[0].section

    def label_for(self, key: str) -> str:
        """The label this document writes for `key`.

        Defaults to the canonical label. `field_labels` is the injector's
        override, so `INCONSISTENT_FIELD_LABEL` is a plan-level decision that
        travels into the hashed model rather than a rendering-time substitution
        the model would not see.
        """
        return self.field_labels.get(key) or canonical_label(key)

    def value_for(self, key: str, value: str) -> str:
        """The value this document writes, blanked where the injector said so."""
        return "" if key in self.blank_fields else value


def _rng(seed: int, *parts: str) -> random.Random:
    """A generator scoped to one document, derived from the committed seed.

    Per-document rather than one stream for the layer, deliberately: a single
    stream makes every document's content depend on how many documents were
    drawn before it, so inserting a project would silently rewrite every
    document after it. Seeding from the identity makes each document's content
    a function of the seed and its own identity and nothing else.
    """
    # noqa justification: a cryptographic generator is the wrong tool here, not
    # a stronger one. FR-021 requires the same seed to reproduce an identical
    # document-model hash across machines and runs; `secrets` would make that
    # impossible by construction. Nothing here is a secret, a token, or a
    # sampling decision anyone could gain by predicting.
    return random.Random(f"{seed}:" + ":".join(parts))  # noqa: S311


def _dates(rng: random.Random, base: date) -> tuple[tuple[str, str], ...]:
    """Three chronologically ordered dates, all derived from the constant.

    Ordered ascending, which is what makes `OUT_OF_ORDER_DATE` an injectable
    deviation rather than an accident: a generator emitting dates in arbitrary
    order would make the class underivable, because the deriver compares
    against the committed ordering and nothing else.
    """
    submitted = base - timedelta(days=rng.randint(60, 240))
    received = submitted + timedelta(days=rng.randint(1, 10))
    returned = received + timedelta(days=rng.randint(3, 21))
    return (
        ("date_submitted", submitted.isoformat()),
        ("date_received", received.isoformat()),
        ("date_returned", returned.isoformat()),
    )


def _items(
    rng: random.Random, config: GenerationConfig, project_index: int
) -> tuple[MaterialItem, ...]:
    count = rng.randint(config.items_min, config.items_max)
    categories = rng.sample(CATEGORIES, count)
    return tuple(
        MaterialItem(
            category=category,
            tag=f"{project_index + 1}{index + 1:02d}-{rng.randint(10, 99)}",
            quantity=rng.randint(1, 6),
        )
        for index, category in enumerate(categories)
    )


def plan_layer(roster: Roster, config: GenerationConfig) -> tuple[DocumentPlan, ...]:
    """Plan every document in the layer, deterministically, from roster and seed.

    Vendor assignment is a round-robin over the sorted roster vendors across
    the layer's *primary* documents rather than a per-document draw. A random
    draw covers all twelve vendors only with high probability, and FR-024 is
    not a probabilistic requirement — the round-robin makes coverage a property
    of the assignment, which the pre-write assertion then confirms rather than
    discovers.

    The last document of each project is a **resubmittal** of the one before
    it: same vendor, same submittal number, revision suffix incremented, and an
    action code that closes where the earlier one did not (FR-025, VR-047).
    """
    projects = sorted(roster.projects, key=lambda entry: entry.id)
    vendors = sorted(roster.vendors, key=lambda entry: entry.id)
    if not projects or not vendors:
        raise GeneratorError("the roster carries no projects or no vendors")

    templates = assign_templates(vendor.id for vendor in vendors)
    per_project = config.documents_per_project
    # Every document except each project's last is a first submission; the last
    # is the resubmittal that closes its project's chain.
    primaries_per_project = per_project - 1

    open_actions = tuple(action for action in ACTION_CODES if not action.closes_chain)
    closing_actions = tuple(action for action in ACTION_CODES if action.closes_chain)
    if not open_actions or not closing_actions:
        raise GeneratorError(
            "the approximated action codes must include one that closes a chain and one that "
            "does not, or FR-025's differing action code is unconstructible"
        )

    plans: list[DocumentPlan] = []
    for project_index, project in enumerate(projects):
        contract_number = f"DACA{project_index + 11:02d}-2026-C-{1000 + project_index:04d}"
        project_plans: list[DocumentPlan] = []
        for index in range(primaries_per_project):
            slot = project_index * primaries_per_project + index
            vendor = vendors[slot % len(vendors)]
            submittal_number = f"{project.id}-T{index + 1:04d}"
            rng = _rng(config.seed, project.id, submittal_number, "0")
            descriptor = rng.choice(DESCRIPTOR_CODES)
            # The document that will be resubmitted must not already be closed.
            is_chain_head = index == primaries_per_project - 1
            action = rng.choice(open_actions if is_chain_head else ACTION_CODES)
            project_plans.append(
                DocumentPlan(
                    document_id=f"{submittal_number}-R0",
                    project_id=project.id,
                    project_name=project.name,
                    vendor_id=vendor.id,
                    vendor_name=vendor.name,
                    submittal_number=submittal_number,
                    revision_suffix="0",
                    contract_number=contract_number,
                    template_id=templates[vendor.id],
                    descriptor=descriptor,
                    action=action,
                    approving_authority=APPROVING_AUTHORITIES[
                        0 if descriptor.government_approval else 1
                    ],
                    dates=_dates(rng, config.base_date),
                    items=_items(rng, config, project_index),
                    page_directives=_undegraded(templates[vendor.id], config),
                )
            )

        head = project_plans[-1]
        rng = _rng(config.seed, project.id, head.submittal_number, "1")
        resubmittal_action = rng.choice(
            tuple(action for action in closing_actions if action.letter != head.action.letter)
        )
        project_plans.append(
            replace(
                head,
                document_id=f"{head.submittal_number}-R1",
                revision_suffix="1",
                action=resubmittal_action,
                dates=_dates(rng, config.base_date),
                items=_items(rng, config, project_index),
            )
        )
        plans.extend(project_plans)
    return tuple(plans)


def _undegraded(template_id: str, config: GenerationConfig) -> tuple[RenderDirective, ...]:
    """One directive per page, all undegraded.

    Recorded explicitly rather than left implicit: the directives are inside
    the reproducibility hash (DM-2), so a page that carries no degradation
    still has to say so — otherwise a later run that degraded it would produce
    the same hash and FR-021 would not notice.
    """
    return tuple(
        RenderDirective(template_id=template_id, degradation_profile=UNDEGRADED_PROFILE)
        for _ in range(config.pages_per_document)
    )


# ---------------------------------------------------------------------------
# Composition: plan -> model + layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedDocument:
    """One planned document, composed into a model, a layout, and a hash."""

    plan: DocumentPlan
    model: DocumentModel
    layout: DocumentLayout
    model_hash: str


def _transmittal_fields(plan: DocumentPlan) -> tuple[FieldEntry, ...]:
    """The transmittal block, as FR-023's six plus the register fields.

    Built once and used for both the layout and the hashed model, so the value
    a page shows and the value the model records are the same object rather
    than two transcriptions.
    """
    values = {
        "transmittal_number": plan.submittal_number,
        "revision_suffix": plan.revision_suffix,
        "project_identifier": f"{plan.project_id} {plan.project_name}",
        "contract_number": plan.contract_number,
        "vendor_name": f"{plan.vendor_id} {plan.vendor_name}",
        "specification_section": plan.specification_section,
        "descriptor_code": f"{plan.descriptor.marker} {plan.descriptor.title}",
        "approving_authority": plan.approving_authority,
        "action_stamp": f"{plan.action.letter} {plan.action.meaning}",
        **dict(plan.dates),
    }
    return tuple(
        FieldEntry(key=key, value=plan.value_for(key, value), label=plan.label_for(key))
        for key, value in values.items()
    )


def _item_fields(plan: DocumentPlan) -> tuple[FieldEntry, ...]:
    entries: list[FieldEntry] = []
    for item in plan.items:
        entries.append(
            FieldEntry(
                key="material_item",
                value=plan.value_for("material_item", item.description),
                label=plan.label_for("material_item"),
            )
        )
        entries.append(
            FieldEntry(
                key="equipment_category",
                value=plan.value_for("equipment_category", f"{item.category} -> {item.section}"),
                label=plan.label_for("equipment_category"),
            )
        )
        entries.append(
            FieldEntry(
                key="quantity",
                value=plan.value_for("quantity", str(item.quantity)),
                label=plan.label_for("quantity"),
            )
        )
    return tuple(entries)


def _split_entry(plan: DocumentPlan, transmittal: Sequence[FieldEntry]) -> FieldEntry | None:
    """The transmittal entry this document splits across the page break, if any.

    Refuses a split of a field with no value to carry over: the point of the
    class is that the value continues on the next page, and a split of an empty
    value would render as a bare label ending page one with nothing following
    it — indistinguishable from `MISSING_OR_BLANK_FIELD` in the emitted artifact
    and therefore underivable (`derive.py`, the one ambiguity).
    """
    if not plan.split_field:
        return None
    entry = next((item for item in transmittal if item.key == plan.split_field), None)
    if entry is None:
        raise GeneratorError(
            f"{plan.document_id}: split_field {plan.split_field!r} is not a transmittal field of "
            f"this document; PAGE_SPLIT_FIELD is a relation between its two pages"
        )
    if not entry.value.strip():
        raise GeneratorError(
            f"{plan.document_id}: split_field {plan.split_field!r} carries no value to continue "
            "on the next page, so the split would be indistinguishable from a blanked field"
        )
    return entry


def build_document(plan: DocumentPlan) -> GeneratedDocument:
    """Compose one plan into a hashed model and the layout that renders it."""
    layout_template = template(plan.template_id)
    pages_expected = len(plan.page_directives)
    if pages_expected != 2:
        raise GeneratorError(
            f"{plan.document_id}: expected two pages of render directives, found {pages_expected}"
        )

    transmittal = _transmittal_fields(plan)
    items = _item_fields(plan)
    split = _split_entry(plan, transmittal)

    def anchor(number: int) -> str:
        # FR-032's citation anchor: the document identifier and the page
        # number, as text, in the header band the raster never covers.
        return f"{plan.document_id} | Page {number} of {pages_expected}"

    first = layout_lines(
        layout_template,
        anchor=anchor(1),
        fields=tuple(entry for entry in transmittal if split is None or entry.key != split.key),
        heading="Transmittal Record",
    )
    second = layout_lines(
        layout_template,
        anchor=anchor(2),
        fields=items,
        heading="Submitted Items and Equipment Categories",
        trailing_text=REMARKS,
        with_title=False,
    )
    if split is not None:
        # The label is appended after every other line on page one, so it is
        # the page's last text object; the value is inserted immediately after
        # page two's citation anchor, so it is that page's first body text
        # object. Both halves are what VR-035d decides the class from.
        label = f"{split.label}{LABEL_SUFFIX}"
        first = (*first, Line(text=label, style="field_label", label=label))
        second = (
            second[0],
            Line(text=split.value, style="field_value", value=split.value),
            *second[1:],
        )

    pages = tuple(
        Page(text=page_text(lines), directive=directive)
        for lines, directive in zip((first, second), plan.page_directives, strict=True)
    )
    model = DocumentModel(
        identity={
            "document_id": plan.document_id,
            "project_id": plan.project_id,
            "vendor_id": plan.vendor_id,
            "submittal_number": plan.submittal_number,
            "revision_suffix": plan.revision_suffix,
            "specification_section": plan.specification_section,
            "descriptor_code": plan.descriptor.code,
            "action_code": plan.action.letter,
            "template_id": plan.template_id,
        },
        fields=tuple(
            FieldValue(label=entry.label, value=entry.value) for entry in (*transmittal, *items)
        ),
        pages=pages,
    )
    return GeneratedDocument(
        plan=plan,
        model=model,
        layout=DocumentLayout(model=model, pages=(first, second)),
        model_hash=document_model_hash(model),
    )


# ---------------------------------------------------------------------------
# Pre-write coverage assertions (FR-024, FR-025, FR-026)
# ---------------------------------------------------------------------------


def _assert_coverage(roster: Roster, plans: Sequence[DocumentPlan]) -> None:
    """Every population rule this generator can decide, before it writes.

    Collected rather than raised one at a time: a run that failed on the first
    shortfall would report one missing vendor per attempt, and the operator
    would learn the shape of the problem five runs later.
    """
    failures: list[str] = []
    if not plans:
        failures.append("VR-066: the planned layer is empty")

    expected_projects = {entry.id for entry in roster.projects}
    covered_projects = {plan.project_id for plan in plans}
    if covered_projects != expected_projects:
        failures.append(
            f"FR-024: planned projects {sorted(covered_projects)} do not cover the roster's "
            f"{sorted(expected_projects)}"
        )

    expected_vendors = {entry.id for entry in roster.vendors}
    covered_vendors = {plan.vendor_id for plan in plans}
    missing = sorted(expected_vendors - covered_vendors)
    if missing:
        failures.append(f"FR-024: no submittal names roster vendors {missing}")
    unknown = sorted(covered_vendors - expected_vendors)
    if unknown:
        failures.append(f"FR-019: planned vendors {unknown} are not in the roster")

    for project_id in sorted(expected_projects):
        project_plans = [plan for plan in plans if plan.project_id == project_id]
        if not _has_resubmittal_chain(project_plans):
            failures.append(
                f"FR-025: project {project_id} has no resubmittal chain — two documents sharing "
                "a submittal number with a strictly incremented revision suffix and a differing "
                "action code"
            )

    locations = [plan.location for plan in plans]
    duplicates = sorted({name for name in locations if locations.count(name) > 1})
    if duplicates:
        failures.append(f"two planned documents share one filename: {duplicates}")

    unbacked = unbacked_sections()
    if unbacked:
        failures.append(
            f"FR-026: the equipment-category map points at sections the real layer does not "
            f"hold: {[f'{category} -> {section}' for category, section in unbacked]}"
        )

    if failures:
        raise GeneratorError(
            "generation preconditions failed; nothing was written:\n  " + "\n  ".join(failures)
        )


def _has_resubmittal_chain(plans: Sequence[DocumentPlan]) -> bool:
    by_submittal: dict[str, list[DocumentPlan]] = {}
    for plan in plans:
        by_submittal.setdefault(plan.submittal_number, []).append(plan)
    for chain in by_submittal.values():
        ordered = sorted(chain, key=lambda plan: int(plan.revision_suffix))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            incremented = int(later.revision_suffix) > int(earlier.revision_suffix)
            if incremented and later.action.letter != earlier.action.letter:
                return True
    return False


# ---------------------------------------------------------------------------
# Emission (FR-009, FR-009b, FR-017a, FR-028, FR-031)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationResult:
    """What one run produced, reported rather than printed.

    Returned so a test can assert on the run and the console entry point can
    render it, instead of a caller re-deriving the counts by walking the tree
    it was just handed.
    """

    root: Path
    documents: tuple[GeneratedDocument, ...]
    manifests: tuple[Path, ...]
    projects: tuple[str, ...]
    vendors: tuple[str, ...]
    chains: Mapping[str, tuple[str, ...]]

    @property
    def document_count(self) -> int:
        return len(self.documents)


def compose_layer(
    *,
    config: GenerationConfig | None = None,
    roster: Roster | None = None,
    inject: Callable[[Sequence[DocumentPlan]], Sequence[DocumentPlan]] | None | object = DEFAULT,
) -> tuple[GeneratedDocument, ...]:
    """Plan, inject and compose the whole layer — **writing nothing at all**.

    Separated from `generate_corpus` because two callers need the models
    without the files. `generate_corpus` is one. The other is VR-039, which
    compares each committed page's extracted text against the document model's;
    that comparison needs the models and must not need a rendered layer, so a
    validator can obtain them from committed inputs — the configuration, the
    roster, the vocabulary — without a single byte being written.
    """
    settings = config or load_config()
    projects_and_vendors = roster if roster is not None else read_roster()

    plans = plan_layer(projects_and_vendors, settings)
    if inject is DEFAULT:
        plans = inject_irregularities(plans, config=settings)
    elif inject is not None:
        plans = tuple(inject(plans))  # type: ignore[operator]
        for plan in plans:
            if not isinstance(plan, DocumentPlan):
                raise GeneratorError(
                    f"the injector returned a {type(plan).__name__}, expected a DocumentPlan"
                )
    _assert_coverage(projects_and_vendors, plans)
    return tuple(build_document(plan) for plan in plans)


def generate_corpus(
    root: Path | None = None,
    *,
    config: GenerationConfig | None = None,
    roster: Roster | None = None,
    inject: Callable[[Sequence[DocumentPlan]], Sequence[DocumentPlan]] | None | object = DEFAULT,
    degrader: Callable[[PageRender], None] | None | object = DEFAULT,
) -> GenerationResult:
    """Generate the whole synthetic layer under `root` and write its manifests.

    `root` is a corpus root, not a location: the five locations beneath it are
    named for the roster's project ids (FR-017a) and nothing here enumerates
    them. Passing a temporary root is how the determinism and byte-identity
    tests compare a fresh run against the committed layer without rewriting it
    in place — an in-place re-run would compare a file against itself.

    `inject` and `degrader` default to this epic's own — `irregularity.inject`
    and `degrade.degrade_page` — because the committed layer is generated with
    them and a plain re-run has to reproduce it. `inject=None` and
    `degrader=None` request a clean layer explicitly.

    The real layer is never touched. MS-6 makes the real manifest a
    write-once artifact, so a generator defect cannot perturb its record.
    """
    settings = config or load_config()
    projects_and_vendors = roster if roster is not None else read_roster()
    documents = compose_layer(config=settings, roster=projects_and_vendors, inject=inject)
    render_with = degrade_page if degrader is DEFAULT else degrader

    base = Path(root) if root is not None else DEFAULT_CORPUS_ROOT
    layer_directory = base / SYNTHETIC_LAYER_DIRECTORY
    # Computed once for the whole run: the digests are over the committed
    # generation inputs in the repository, never over anything under `root`,
    # so a run into a temporary tree records the same provenance as a run into
    # the working copy (FR-009b).
    input_digests = generation_input_digests(REPO_ROOT)
    recorded_roster_hash = roster_digest(projects_and_vendors)

    manifests: list[Path] = []
    by_project: dict[str, list[GeneratedDocument]] = {}
    for document in documents:
        by_project.setdefault(document.plan.project_id, []).append(document)

    for project_id in sorted(by_project):
        location = layer_directory / project_id
        entries: list[SyntheticEntry] = []
        for document in by_project[project_id]:
            target = location / document.plan.location
            render_document(document.layout, target, degrader=render_with)  # type: ignore[arg-type]
            entries.append(
                SyntheticEntry(
                    location=document.plan.location,
                    license_basis=SyntheticLicenseBasis(statement=LICENSE_STATEMENT),
                    content_hash=content_hash_of_file(target),
                    generator_id=settings.generator_id,
                    seed=settings.seed,
                    generation_date=settings.generation_date,
                    roster_hash=recorded_roster_hash,
                    generation_inputs=input_digests,
                    document_model_hash=document.model_hash,
                    irregularity_classes=document.plan.irregularity_classes,
                )
            )
        manifest = Manifest(
            location_id=f"{SYNTHETIC_LAYER_DIRECTORY}/{project_id}",
            entries=tuple(entries),
            project_id=project_id,
        )
        manifests.append(write_manifest(location, manifest))

    chains = {
        project_id: tuple(
            sorted(
                document.plan.document_id
                for document in by_project[project_id]
                if document.plan.revision_suffix != "0"
            )
        )
        for project_id in sorted(by_project)
    }
    return GenerationResult(
        root=base,
        documents=documents,
        manifests=tuple(manifests),
        projects=tuple(sorted(by_project)),
        vendors=tuple(sorted({document.plan.vendor_id for document in documents})),
        chains=MappingProxyType(chains),
    )


# ---------------------------------------------------------------------------
# Console entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """`corpus-generate`. Exit 0 on a complete layer, 1 on a precondition failure."""
    parser = argparse.ArgumentParser(
        prog="corpus-generate",
        description=(
            "Generate the synthetic submittal layer from the committed seed and the E001 "
            "roster. Offline and model-free; every date comes from the committed "
            "generation_date constant."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="corpus root to write into (default: the repository's data/corpus/)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="generation configuration (default: the committed data/corpus/synthetic/ one)",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_config(args.config)
        result = generate_corpus(args.root, config=settings)
    except (GeneratorError, CorpusPathError, ValueError) as exc:
        print(f"corpus-generate: {exc}", file=sys.stderr)
        return 1

    print(f"corpus-generate: wrote {result.document_count} document(s) under {result.root}")
    print(f"  projects: {len(result.projects)} - {', '.join(result.projects)}")
    print(f"  vendors:  {len(result.vendors)} - {', '.join(result.vendors)}")
    print(f"  manifests: {len(result.manifests)}")
    for project_id, chain in result.chains.items():
        print(f"  resubmittals in {project_id}: {', '.join(chain) or 'none'}")
    # FR-030 and SC-019 are layer-level claims, so the run reports the observed
    # distribution rather than leaving an operator to read twenty-five manifests
    # to find out whether the layer it just wrote satisfies them.
    counts = Counter(
        value for document in result.documents for value in document.plan.irregularity_classes
    )
    classed = sum(1 for document in result.documents if document.plan.irregularity_classes)
    degraded = sum(
        1
        for document in result.documents
        for page in document.model.pages
        if page.directive.degradation_profile != UNDEGRADED_PROFILE
    )
    print(
        f"  documents carrying >= 1 irregularity class: {classed}/{result.document_count} "
        f"({classed / result.document_count:.0%})"
        if result.document_count
        else "  no document"
    )
    for value in IRREGULARITY_CLASSES:
        print(f"    {value}: {counts.get(value, 0)}")
    print(f"  pages carrying injected degradation: {degraded}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
