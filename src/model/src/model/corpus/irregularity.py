"""The closed five irregularity classes and the four structural injectors.

FR-030 / FR-031. A synthetic layer that is uniformly clean makes downstream
chunking, extraction and retrieval look better than they are, and the inflation
is invisible in a pooled metric (`spec.md` §Risk). This module is what stops
that: it rewrites *planned* documents so a stated share of the layer carries a
named, recorded, independently re-derivable defect.

**The enum is closed and is compared against the recorded vocabulary.**
`manifest.py` already holds `IRREGULARITY_CLASSES` — the tuple a manifest entry
is validated against — so the five names exist in exactly one place that
decides what may be recorded. This module restates them as an enum for
readability and then *compares* the two at import, the way `codes.py` restates
and compares `STRUCTURAL_FIELD_KEYS`. A restatement that is compared is a
cross-check; one that is merely repeated is drift.

**Four of the five are structural; the fifth is not.** `INCONSISTENT_FIELD_LABEL`,
`MISSING_OR_BLANK_FIELD`, `OUT_OF_ORDER_DATE` and `PAGE_SPLIT_FIELD` leave
evidence in the emitted PDF's text and geometry, so `derive.py` recovers them
from the file without consulting anything the generator recorded (FR-031a).
`SCAN_DEGRADATION` does not: no structural derivation confirms that a raster is
*degraded*, which is why its evidence path is the injector unit tests of VR-050
and why VR-036 is a necessary condition only.

**The injector acts on the plan, never on the render.** `generate.py` applies
`inject()` between planning and model construction, so every downstream
step — the model, its hash, the render, the manifest entry — follows from the
rewritten plan. An injected change is therefore inside `document_model_hash` by
construction rather than by an injector remembering to update it (DM-2).

**Three injection sites are chosen so the four structural classes stay
independently decidable**, which is a property of the choice rather than of the
deriver:

- Blanking never touches a **structural** field (FR-023's six must be non-empty
  on every emitted document, VR-046) and never touches a **date** field (a
  missing date would make `OUT_OF_ORDER_DATE` underivable rather than false).
- Relabelling never touches a structural field either. VR-035a derives
  `MISSING_OR_BLANK_FIELD` when a required canonical label is absent from the
  document *entirely*, and substituting an alternate for a structural field's
  canonical label would satisfy that literally — one injection recording one
  class while the artifact evidences two.
- Splitting only ever moves a structural field, whose canonical label stays on
  the page it left; the value continues on the next page. That keeps the split
  label distinguishable from a blanked one, which is the single ambiguity the
  page-boundary rule in `derive.py` has to break.

Stdlib plus this package. No clock, no network, no model. `DocumentPlan` is
imported for typing only, so `generate.py` may import this module at module
level without a cycle.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from model.corpus.codes import DATE_FIELD_ORDER, STRUCTURAL_FIELD_KEYS, alternate_labels
from model.corpus.manifest import IRREGULARITY_CLASSES
from model.corpus.model import RenderDirective
from model.corpus.render import UNDEGRADED_PROFILE

if TYPE_CHECKING:  # pragma: no cover - typing only; importing these at runtime would cycle
    from model.corpus.generate import DocumentPlan, GenerationConfig

__all__ = [
    "BLANKABLE_FIELDS",
    "CLASS_ROTATION",
    "CLASSED_DOCUMENT_FLOOR",
    "RELABELLABLE_FIELDS",
    "SPLITTABLE_FIELDS",
    "STRUCTURAL_CLASSES",
    "IrregularityClass",
    "IrregularityError",
    "inject",
]


class IrregularityError(ValueError):
    """Raised when a layer cannot be injected or would not satisfy FR-030.

    One type for every failure, as `RosterError`, `ManifestError` and
    `GeneratorError` are: a caller learns the same thing from each of them —
    this layer must not be written.
    """


class IrregularityClass(StrEnum):
    """FR-030's closed five.

    A `StrEnum` so a member is usable wherever the recorded string is, and so
    `sorted()` over members orders them the way `manifest.py` sorts the
    recorded list (MS-2).
    """

    INCONSISTENT_FIELD_LABEL = "INCONSISTENT_FIELD_LABEL"
    MISSING_OR_BLANK_FIELD = "MISSING_OR_BLANK_FIELD"
    OUT_OF_ORDER_DATE = "OUT_OF_ORDER_DATE"
    PAGE_SPLIT_FIELD = "PAGE_SPLIT_FIELD"
    SCAN_DEGRADATION = "SCAN_DEGRADATION"


#: The cross-check the module docstring describes. Evaluated at import, so a
#: divergence between the recorded vocabulary and this enum is an import-time
#: failure of every module that injects or validates a class.
if tuple(sorted(member.value for member in IrregularityClass)) != IRREGULARITY_CLASSES:
    raise IrregularityError(
        "IrregularityClass and manifest.IRREGULARITY_CLASSES have drifted apart: "
        f"{sorted(member.value for member in IrregularityClass)} vs {list(IRREGULARITY_CLASSES)}"
    )

#: The four `derive.py` recovers from an emitted PDF. `SCAN_DEGRADATION` is
#: deliberately absent: FR-031a's comparison is `derived == recorded ∩ this
#: set`, and comparing against the whole recorded set would fail every degraded
#: document for something that is not a defect (`data-model.md` §Irregularity
#: Class).
STRUCTURAL_CLASSES: frozenset[IrregularityClass] = frozenset(
    {
        IrregularityClass.INCONSISTENT_FIELD_LABEL,
        IrregularityClass.MISSING_OR_BLANK_FIELD,
        IrregularityClass.OUT_OF_ORDER_DATE,
        IrregularityClass.PAGE_SPLIT_FIELD,
    }
)

#: Fields whose value may be blanked. Non-structural, so FR-023's six stay
#: non-empty on every document (VR-046); non-date, so a blank cannot make
#: `OUT_OF_ORDER_DATE` underivable; and none of them is last in any template's
#: `field_order`, so a blanked label is never the final text object on a page
#: and can never be mistaken for a page-split label.
BLANKABLE_FIELDS: tuple[str, ...] = ("contract_number", "project_identifier", "vendor_name")

#: Fields whose canonical label may be replaced by one of its alternates.
#: Non-structural for the reason the module docstring gives, and non-date so
#: the date derivation is exercised against canonical labels only.
RELABELLABLE_FIELDS: tuple[str, ...] = (
    "contract_number",
    "project_identifier",
    "vendor_name",
    "material_item",
    "equipment_category",
    "quantity",
)

#: Fields whose label may end one page with its value continuing on the next.
#: Structural, so the canonical label is guaranteed present and the split is
#: decidable from the page boundary rather than from a missing label.
SPLITTABLE_FIELDS: tuple[str, ...] = (
    "specification_section",
    "descriptor_code",
    "approving_authority",
    "action_stamp",
)

#: SC-019's floor, restated here because the injector is what has to meet it:
#: at least this share of the layer carries at least one class. VR-033 is the
#: corpus-level rule that checks the committed result.
CLASSED_DOCUMENT_FLOOR = 0.80

#: The per-document assignment, applied by position over the layer sorted by
#: document identifier. A rotation rather than a draw, for the same reason
#: `assign_templates` is a round-robin: FR-030 is not a probabilistic
#: requirement, and a random assignment would satisfy "all five present" only
#: with high probability and leave VR-032 to discover the exception after a
#: corpus had been committed. Here the coverage is a property of the schedule
#: and `_assert_layer` confirms it rather than discovering it.
#:
#: The sixth slot is empty on purpose. A layer in which *every* document is
#: irregular is as unrepresentative as one in which none is, and VR-033's floor
#: is a floor rather than a target.
CLASS_ROTATION: tuple[tuple[IrregularityClass, ...], ...] = (
    (IrregularityClass.MISSING_OR_BLANK_FIELD,),
    (IrregularityClass.INCONSISTENT_FIELD_LABEL,),
    (IrregularityClass.OUT_OF_ORDER_DATE,),
    (IrregularityClass.PAGE_SPLIT_FIELD, IrregularityClass.SCAN_DEGRADATION),
    (IrregularityClass.SCAN_DEGRADATION,),
    (),
)


def _document_rng(seed: int, document_id: str) -> random.Random:
    """A stream scoped to one document, derived from the committed seed.

    Per document rather than one stream for the layer, exactly as
    `generate._rng` is: a single stream makes each document's injection depend
    on how many documents were injected before it, so inserting a project would
    silently rewrite every document after it.
    """
    # Suppression rationale: see generate.py — seeded determinism is the requirement (FR-021),
    # so a cryptographic generator would defeat the property rather than harden
    # it. Which irregularities a document carries must be reproducible.
    return random.Random(f"{seed}:irregularity:{document_id}")  # noqa: S311


def _blank(plan: DocumentPlan, rng: random.Random, config: GenerationConfig) -> DocumentPlan:
    """`MISSING_OR_BLANK_FIELD` — a field keeps its label and loses its value.

    The label stays deliberately. VR-035a derives the class from a canonical
    label appearing with no value text in its value region, so a layout that
    dropped the label of an empty field would erase the evidence the class is
    recognised by.
    """
    key = rng.choice(BLANKABLE_FIELDS)
    return replace(plan, blank_fields=frozenset({*plan.blank_fields, key}))


def _relabel(plan: DocumentPlan, rng: random.Random, config: GenerationConfig) -> DocumentPlan:
    """`INCONSISTENT_FIELD_LABEL` — an alternate label stands in for the canonical.

    The alternate comes from the committed vocabulary, which requires alternates
    disjoint from every canonical label; that disjointness is what makes VR-035b
    decidable rather than a guess about which field a token names.
    """
    key = rng.choice(RELABELLABLE_FIELDS)
    alternates = alternate_labels(key)
    if not alternates:  # pragma: no cover - the vocabulary reader rejects this at load
        raise IrregularityError(f"field {key!r} declares no alternate label to substitute")
    return replace(
        plan,
        field_labels=MappingProxyType({**dict(plan.field_labels), key: rng.choice(alternates)}),
    )


def _out_of_order(plan: DocumentPlan, rng: random.Random, config: GenerationConfig) -> DocumentPlan:
    """`OUT_OF_ORDER_DATE` — the first and last committed date values are swapped.

    The *keys* keep their positions and only the values move, so the document
    still carries every date field its template declares. The deviation is
    therefore a chronology violation rather than a missing field, which is what
    keeps VR-035c and VR-035a from firing on one injection.
    """
    recorded = dict(plan.dates)
    first, last = DATE_FIELD_ORDER[0], DATE_FIELD_ORDER[-1]
    missing = [key for key in (first, last) if key not in recorded]
    if missing:
        raise IrregularityError(
            f"{plan.document_id}: date field(s) {missing} are not on the plan, so the committed "
            f"chronological order {list(DATE_FIELD_ORDER)} cannot be violated"
        )
    swapped = {**recorded, first: recorded[last], last: recorded[first]}
    return replace(plan, dates=tuple((key, swapped[key]) for key, _ in plan.dates))


def _split(plan: DocumentPlan, rng: random.Random, config: GenerationConfig) -> DocumentPlan:
    """`PAGE_SPLIT_FIELD` — a label ends one page and its value opens the next.

    Recorded on the plan rather than performed here: `generate.build_document`
    is the single place a page's lines are composed, and a second composer would
    be a second definition of what the page says.
    """
    return replace(plan, split_field=rng.choice(SPLITTABLE_FIELDS))


def _degrade(plan: DocumentPlan, rng: random.Random, config: GenerationConfig) -> DocumentPlan:
    """`SCAN_DEGRADATION` — one page's render directive names a profile.

    Profile *and* parameters go into the directive, and the directive is inside
    `document_model_hash` (DM-2). Without the parameters a generator that
    degraded the same page with different noise on each run would reproduce the
    hash and satisfy FR-021 while being nondeterministic.
    """
    profiles = sorted(config.degradation_profiles)
    if not profiles:  # pragma: no cover - load_config rejects an empty profile set
        raise IrregularityError("the generation configuration declares no degradation profile")
    profile = rng.choice(profiles)
    bounds = config.degradation_profiles[profile]
    parameters = {
        name: rng.randint(int(bounds[name]["min"]), int(bounds[name]["max"]))
        for name in sorted(bounds)
    }
    directives = list(plan.page_directives)
    if not directives:
        raise IrregularityError(f"{plan.document_id}: the plan carries no page to degrade")
    index = rng.randrange(len(directives))
    base = directives[index]
    directives[index] = RenderDirective(
        template_id=base.template_id,
        degradation_profile=profile,
        parameters=parameters,
    )
    return replace(plan, page_directives=tuple(directives))


_INJECTORS: Mapping[
    IrregularityClass, Callable[[DocumentPlan, random.Random, GenerationConfig], DocumentPlan]
] = MappingProxyType(
    {
        IrregularityClass.MISSING_OR_BLANK_FIELD: _blank,
        IrregularityClass.INCONSISTENT_FIELD_LABEL: _relabel,
        IrregularityClass.OUT_OF_ORDER_DATE: _out_of_order,
        IrregularityClass.PAGE_SPLIT_FIELD: _split,
        IrregularityClass.SCAN_DEGRADATION: _degrade,
    }
)


def _assert_layer(plans: Sequence[DocumentPlan]) -> None:
    """FR-030 and SC-019, asserted over the injected layer before it is built.

    Collected rather than raised one at a time, exactly as the generator's own
    pre-write assertions are: a run that failed on the first shortfall would
    report one missing class per attempt.
    """
    failures: list[str] = []
    if not plans:
        failures.append("VR-066: the layer to inject is empty")

    present = {value for plan in plans for value in plan.irregularity_classes}
    missing = sorted({member.value for member in IrregularityClass} - present)
    if missing:
        failures.append(
            f"FR-030: the layer carries no document recording class(es) {missing}; all five "
            "must appear at least once or their rules have nothing to assert against"
        )

    classed = sum(1 for plan in plans if plan.irregularity_classes)
    if plans and classed < CLASSED_DOCUMENT_FLOOR * len(plans):
        failures.append(
            f"SC-019: {classed} of {len(plans)} document(s) carry at least one irregularity "
            f"class ({classed / len(plans):.0%}), below the floor of {CLASSED_DOCUMENT_FLOOR:.0%}"
        )

    degraded_pages = sum(
        1
        for plan in plans
        for directive in plan.page_directives
        if directive.degradation_profile != UNDEGRADED_PROFILE
    )
    if not degraded_pages:
        failures.append(
            "SC-020: no page carries injected degradation; the citation-anchor rule would then "
            "be true over an empty set (VR-038)"
        )

    if failures:
        raise IrregularityError(
            "injection would not satisfy FR-030; nothing was written:\n  " + "\n  ".join(failures)
        )


def inject(plans: Sequence[DocumentPlan], *, config: GenerationConfig) -> tuple[DocumentPlan, ...]:
    """Rewrite a planned layer so it carries all five irregularity classes.

    The returned plans are in the order they were supplied; the *assignment* is
    made over the layer sorted by document identifier, so which document gets
    which class is a function of the identifiers and the committed seed and not
    of the order the planner happened to emit them in.

    Each returned plan records the classes injected into it, so the manifest
    entry built from it records exactly what was done rather than what was
    intended (FR-031).
    """
    supplied = tuple(plans)
    seed = int(getattr(config, "seed", 0))
    assigned: dict[str, DocumentPlan] = {}
    for index, plan in enumerate(sorted(supplied, key=lambda item: item.document_id)):
        classes = CLASS_ROTATION[index % len(CLASS_ROTATION)]
        rng = _document_rng(seed, plan.document_id)
        current = plan
        for member in classes:
            current = _INJECTORS[member](current, rng, config)
        assigned[plan.document_id] = replace(
            current, irregularity_classes=tuple(sorted(member.value for member in classes))
        )

    injected = tuple(assigned[plan.document_id] for plan in supplied)
    _assert_layer(injected)
    return injected


# FR-023's six are referenced by the module docstring's reasoning about which
# fields may be blanked or relabelled; asserting the disjointness here makes
# that reasoning a property of the module rather than a comment about it.
if set(BLANKABLE_FIELDS) & set(STRUCTURAL_FIELD_KEYS):
    raise IrregularityError(
        "BLANKABLE_FIELDS names a structural field; FR-023 requires all six non-empty on "
        "every emitted document (VR-046)"
    )
if set(RELABELLABLE_FIELDS) & set(STRUCTURAL_FIELD_KEYS):
    raise IrregularityError(
        "RELABELLABLE_FIELDS names a structural field; substituting an alternate would make "
        "its canonical label absent and satisfy VR-035a's second condition as well"
    )
if set(BLANKABLE_FIELDS) & set(DATE_FIELD_ORDER):
    raise IrregularityError(
        "BLANKABLE_FIELDS names a date field; a blank date makes OUT_OF_ORDER_DATE underivable"
    )
if not set(SPLITTABLE_FIELDS) <= set(STRUCTURAL_FIELD_KEYS):
    raise IrregularityError(
        "SPLITTABLE_FIELDS names a non-structural field; the split label must be guaranteed "
        "present so the page boundary, not a missing label, decides PAGE_SPLIT_FIELD"
    )
