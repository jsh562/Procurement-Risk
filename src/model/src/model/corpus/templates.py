"""Per-vendor layout templates, and the lines a template lays a page out as.

FR-023 / FR-029. Two obligations meet here. Every emitted document carries the
six structural fields of FR-023, which is a property of the *template* rather
than of a document, so a template that omitted one could not produce a
conforming document at all — it fails at construction instead. And no single
template may span every vendor (FR-029), because uniform layout across a
synthetic layer makes downstream chunking and retrieval look better than they
are, and the inflation is invisible in a pooled metric (research §Realism risk
in a synthetic document layer).

**Layout variation is vertical, never columnar, and that is a deliberate
constraint rather than a shortcut.** Templates differ in field order, in
whether a value sits beside its label or beneath it, in type size, leading,
rule lines, and header wording. They do **not** place two fields side by side,
because a two-column body makes extracted reading order interleave the columns,
and two rules depend on reading order being the order the model records:
VR-039 compares per-page extracted text against the model's page text, and
VR-035d decides `PAGE_SPLIT_FIELD` from a label being the last text object on
one page and its value the first on the next. A layout that scrambled reading
order would make both undecidable while looking more varied.

**One composition, two consumers.** `layout_lines` is the only place a page's
text is decided. The generator joins those lines into the model's per-page text
and the renderer draws the same objects; they cannot disagree about what the
page says, which is what makes VR-039 an assertion about rendering rather than
about two independent transcriptions of the same fields.

Labels come from the committed vocabulary via `codes.py`, never from a literal
here: the injector that substitutes an alternate label and the deriver that
recovers the substitution both read that file, and a template holding its own
label text would be a third vocabulary nothing compares.

Stdlib only; one error type; every collection ordered or frozen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from model.corpus.codes import STRUCTURAL_FIELD_KEYS, VOCABULARY, canonical_label

__all__ = [
    "LABEL_SUFFIX",
    "TEMPLATE_IDS",
    "TEMPLATES",
    "TRANSMITTAL_FIELD_KEYS",
    "FieldEntry",
    "LayoutTemplate",
    "Line",
    "TemplateError",
    "assign_templates",
    "layout_lines",
    "page_text",
    "template",
]

# Fixed across templates, deliberately. Varying the separator would vary the
# label *text* rather than the layout, and the deriver matches label text.
LABEL_SUFFIX = ":"

# Every field the transmittal block carries, independent of order. A template's
# `field_order` is a permutation of exactly this set, so no template can drop a
# field by reordering and no two templates can carry different information.
TRANSMITTAL_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "transmittal_number",
        "revision_suffix",
        "project_identifier",
        "contract_number",
        "vendor_name",
        "specification_section",
        "descriptor_code",
        "approving_authority",
        "date_submitted",
        "date_received",
        "date_returned",
        "action_stamp",
    }
)

# Line styles the renderer knows how to draw. Closed, so a template cannot
# introduce a style the renderer would silently skip.
LINE_STYLES: frozenset[str] = frozenset(
    {"anchor", "title", "subtitle", "heading", "field_inline", "field_label", "field_value", "text"}
)

INLINE = "inline"
BELOW = "below"
VALUE_PLACEMENTS: frozenset[str] = frozenset({INLINE, BELOW})


class TemplateError(ValueError):
    """Raised when a template is malformed or an assignment would violate FR-029.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — no document may be laid
    out from this template set.
    """


@dataclass(frozen=True)
class FieldEntry:
    """One field to lay out: its vocabulary key, the label to write, its value.

    `label` is carried rather than derived so an injector can substitute an
    alternate label for a canonical one without the layout having to know that
    a substitution happened. The default is the canonical label, so a caller
    that supplies none gets a correctly-labelled document.
    """

    key: str
    value: str
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or self.key not in VOCABULARY.fields:
            raise TemplateError(
                f"field key {self.key!r} is not in the committed vocabulary "
                f"{list(VOCABULARY.field_keys)}"
            )
        if not isinstance(self.value, str):
            raise TemplateError(
                f"field {self.key!r} value must be a string, found {type(self.value).__name__}"
            )
        if not isinstance(self.label, str):
            raise TemplateError(
                f"field {self.key!r} label must be a string, found {type(self.label).__name__}"
            )
        if not self.label:
            object.__setattr__(self, "label", canonical_label(self.key))


@dataclass(frozen=True)
class Line:
    """One laid-out line: the text it reads as, and how it is drawn.

    `text` is what the model records as part of the page and what extraction
    must recover. `label_width` matters only to `field_inline`, where the label
    is drawn at the left margin and the value at that offset — the two are one
    line of text and one comparison, not two.
    """

    text: str
    style: str
    label: str = ""
    value: str = ""

    def __post_init__(self) -> None:
        if self.style not in LINE_STYLES:
            raise TemplateError(f"line style {self.style!r} is not one of {sorted(LINE_STYLES)}")
        if not isinstance(self.text, str) or not self.text.strip():
            raise TemplateError(f"a {self.style} line must carry non-empty text: {self.text!r}")


@dataclass(frozen=True)
class LayoutTemplate:
    """One vendor-facing layout.

    Everything here is geometry or ordering. Nothing here is label text, and
    nothing here changes which fields a document carries — a template that
    could do either would make two documents differ in content while claiming
    to differ only in layout.
    """

    template_id: str
    form_title: str
    subtitle: str
    field_order: tuple[str, ...]
    value_placement: str
    body_font_size: int
    leading: int
    label_width: int
    rule_lines: bool

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not self.template_id.strip():
            raise TemplateError(f"template_id must be non-empty, found {self.template_id!r}")
        if self.value_placement not in VALUE_PLACEMENTS:
            raise TemplateError(
                f"{self.template_id}: value_placement must be one of "
                f"{sorted(VALUE_PLACEMENTS)}, found {self.value_placement!r}"
            )
        order = tuple(self.field_order)
        if len(set(order)) != len(order):
            raise TemplateError(f"{self.template_id}: field_order repeats a field: {list(order)}")
        if set(order) != set(TRANSMITTAL_FIELD_KEYS):
            unexpected = sorted(set(order) - TRANSMITTAL_FIELD_KEYS)
            missing = sorted(TRANSMITTAL_FIELD_KEYS - set(order))
            raise TemplateError(
                f"{self.template_id}: field_order must be a permutation of the transmittal "
                f"block; unexpected={unexpected} missing={missing}"
            )
        # FR-023, asserted on the template rather than on each document: a
        # template missing a structural field cannot produce a conforming
        # document, so it must not be constructible.
        absent = [key for key in STRUCTURAL_FIELD_KEYS if key not in set(order)]
        if absent:
            raise TemplateError(
                f"{self.template_id}: FR-023 requires the six structural fields; missing {absent}"
            )
        for name, value in (
            ("body_font_size", self.body_font_size),
            ("leading", self.leading),
            ("label_width", self.label_width),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise TemplateError(
                    f"{self.template_id}: {name} must be a positive integer, found {value!r}"
                )
        if self.leading <= self.body_font_size:
            raise TemplateError(
                f"{self.template_id}: leading {self.leading} must exceed body_font_size "
                f"{self.body_font_size}, or lines overlap"
            )
        object.__setattr__(self, "field_order", order)


_TEMPLATES: tuple[LayoutTemplate, ...] = (
    LayoutTemplate(
        template_id="TPL-STACKED-A",
        form_title="SUBMITTAL TRANSMITTAL AND REVIEW RECORD",
        subtitle="Contractor submittal register entry (approximated form)",
        field_order=(
            "transmittal_number",
            "revision_suffix",
            "project_identifier",
            "contract_number",
            "vendor_name",
            "specification_section",
            "descriptor_code",
            "approving_authority",
            "date_submitted",
            "date_received",
            "date_returned",
            "action_stamp",
        ),
        value_placement=BELOW,
        body_font_size=10,
        leading=14,
        label_width=150,
        rule_lines=True,
    ),
    LayoutTemplate(
        template_id="TPL-INLINE-B",
        form_title="TRANSMITTAL OF SUBMITTAL DATA",
        subtitle="Vendor register - equipment and material submittals",
        field_order=(
            "project_identifier",
            "contract_number",
            "transmittal_number",
            "revision_suffix",
            "vendor_name",
            "date_submitted",
            "specification_section",
            "descriptor_code",
            "approving_authority",
            "date_received",
            "action_stamp",
            "date_returned",
        ),
        value_placement=INLINE,
        body_font_size=11,
        leading=16,
        label_width=170,
        rule_lines=False,
    ),
    LayoutTemplate(
        template_id="TPL-COMPACT-C",
        form_title="SUBMITTAL REGISTER TRANSMITTAL",
        subtitle="Compact register format",
        field_order=(
            "vendor_name",
            "project_identifier",
            "transmittal_number",
            "revision_suffix",
            "specification_section",
            "descriptor_code",
            "contract_number",
            "approving_authority",
            "action_stamp",
            "date_submitted",
            "date_received",
            "date_returned",
        ),
        value_placement=INLINE,
        body_font_size=9,
        leading=12,
        label_width=140,
        rule_lines=True,
    ),
)

TEMPLATES: Mapping[str, LayoutTemplate] = MappingProxyType(
    {entry.template_id: entry for entry in _TEMPLATES}
)
TEMPLATE_IDS: tuple[str, ...] = tuple(sorted(TEMPLATES))


def template(template_id: str) -> LayoutTemplate:
    """Look a template up, failing on anything outside the closed set."""
    try:
        return TEMPLATES[template_id]
    except (KeyError, TypeError):
        raise TemplateError(
            f"template id {template_id!r} is not one of {list(TEMPLATE_IDS)}"
        ) from None


def assign_templates(vendor_ids: Iterable[str]) -> Mapping[str, str]:
    """Assign one template per vendor, deterministically and with spread (FR-029).

    Round-robin over the **sorted** vendor identifiers rather than a hash of
    each identifier. Both are deterministic, but only this one *guarantees* the
    outcome FR-029 requires: a hash-derived assignment could place every vendor
    on one template for some roster and would then satisfy the requirement by
    luck, leaving VR-049 to discover it after a corpus was committed. Here the
    spread is a property of the function, and the assertion below cannot fail
    for a roster of at least two vendors.

    Sorting also removes the roster file's own ordering from the result, so
    reordering the roster without changing its content does not reshuffle every
    document's layout.
    """
    ordered = sorted({vendor_id for vendor_id in vendor_ids})
    if not ordered:
        raise TemplateError("no vendors to assign templates to")
    if len(ordered) < 2:
        raise TemplateError(
            f"FR-029 requires no template to span every vendor, which is unsatisfiable "
            f"for {len(ordered)} vendor(s)"
        )

    assignment = {
        vendor_id: TEMPLATE_IDS[index % len(TEMPLATE_IDS)]
        for index, vendor_id in enumerate(ordered)
    }
    used = set(assignment.values())
    if len(used) < 2:
        raise TemplateError(
            f"FR-029: the assignment uses only {sorted(used)}; at least two template ids "
            "must appear across the layer"
        )
    for template_id in used:
        covered = {v for v, t in assignment.items() if t == template_id}
        if covered == set(ordered):
            raise TemplateError(
                f"FR-029: template {template_id!r} spans every vendor {sorted(ordered)}"
            )
    return MappingProxyType(assignment)


def _field_lines(layout: LayoutTemplate, entry: FieldEntry) -> tuple[Line, ...]:
    label = f"{entry.label}{LABEL_SUFFIX}"
    if layout.value_placement == INLINE:
        # One line of text: the label and its value are drawn on one baseline,
        # so extraction recovers them as one line and the whitespace between
        # them is layout rather than content.
        return (
            Line(
                text=f"{label} {entry.value}".rstrip(),
                style="field_inline",
                label=label,
                value=entry.value,
            ),
        )
    lines = [Line(text=label, style="field_label", label=label)]
    if entry.value.strip():
        lines.append(Line(text=entry.value, style="field_value", value=entry.value))
    return tuple(lines)


def layout_lines(
    layout: LayoutTemplate,
    *,
    anchor: str,
    fields: Sequence[FieldEntry],
    heading: str = "",
    trailing_text: Sequence[str] = (),
    with_title: bool = True,
) -> tuple[Line, ...]:
    """Compose one page's lines, in reading order.

    The anchor comes first on every page. That is a layout decision with a
    verification consequence: FR-032 requires the page number and document
    identifier to stay an undegraded text object outside every raster
    rectangle, and the header band above the body is the region the degradation
    injector never covers. Putting the anchor anywhere else would make its
    survival depend on where a raster happened to land.

    A blank field value still emits its label. `MISSING_OR_BLANK_FIELD` is
    derived from a canonical label appearing with no value text (VR-035a), so a
    layout that dropped the label of an empty field would erase the very
    evidence that class is recognised by.
    """
    if not isinstance(layout, LayoutTemplate):
        raise TemplateError(f"expected a LayoutTemplate, found {type(layout).__name__}")

    lines: list[Line] = [Line(text=anchor, style="anchor")]
    if with_title:
        lines.append(Line(text=layout.form_title, style="title"))
        lines.append(Line(text=layout.subtitle, style="subtitle"))
    if heading:
        lines.append(Line(text=heading, style="heading"))

    # The transmittal block is ordered by the template and each of its fields
    # appears once. Anything outside the block — an item list, whose keys
    # repeat once per item — keeps the order the caller built it in, because
    # reordering it would reorder the items themselves.
    block_keys = set(layout.field_order)
    block = [entry for entry in fields if entry.key in block_keys]
    if len({entry.key for entry in block}) != len(block):
        raise TemplateError("two transmittal field entries share one key within a page")
    extra = [entry for entry in fields if entry.key not in block_keys]
    ordered = sorted(block, key=lambda entry: layout.field_order.index(entry.key)) + extra
    for entry in ordered:
        lines.extend(_field_lines(layout, entry))

    for text in trailing_text:
        lines.append(Line(text=text, style="text"))
    return tuple(lines)


def page_text(lines: Iterable[Line]) -> str:
    """The text a page reads as: one line per `Line`, newline separated.

    This is what the document model records and what per-page extraction is
    compared against after NFC normalization and whitespace collapse (VR-039).
    """
    return "\n".join(line.text for line in lines)
