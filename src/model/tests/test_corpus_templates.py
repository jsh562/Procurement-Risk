"""FR-023 / FR-029: the per-vendor layouts and what they refuse to be.

**Layout is not content.** A template fixes geometry and ordering and nothing
else: `field_order` is a permutation of exactly the transmittal block, so no
template can drop a field by reordering and two documents from two vendors carry
the same information laid out differently. Every refusal below is what keeps
that true, and a refusal nobody has observed is a property nobody can rely on.

VR-049's layer-level assertion — at least two template ids, none spanning all
twelve vendors — is T055's and is deliberately not restated here. What is here
is the *assignment function's* own guarantee, which is what makes that rule pass
by construction rather than by luck.
"""

from __future__ import annotations

import pytest

from model.corpus import templates as templates_module
from model.corpus.templates import (
    LABEL_SUFFIX,
    TEMPLATE_IDS,
    TEMPLATES,
    TRANSMITTAL_FIELD_KEYS,
    FieldEntry,
    LayoutTemplate,
    Line,
    TemplateError,
    assign_templates,
    layout_lines,
    page_text,
    template,
)

ANCHOR = "PRJ-001-T0001-R0 | Page 1 of 2"


def _entry(key: str = "transmittal_number", value: str = "PRJ-001-T0001") -> FieldEntry:
    return FieldEntry(key=key, value=value)


def _fields() -> tuple[FieldEntry, ...]:
    return tuple(FieldEntry(key=key, value=f"value for {key}") for key in TRANSMITTAL_FIELD_KEYS)


# --- FieldEntry: the seam the injector acts on -------------------------------


def test_a_field_entry_defaults_to_the_canonical_label() -> None:
    """A caller that supplies no label gets a correctly-labelled document, so an
    explicit label is always evidence that a substitution happened."""
    from model.corpus.codes import canonical_label

    assert _entry().label == canonical_label("transmittal_number")


@pytest.mark.parametrize(
    ("key", "value", "label"),
    [
        pytest.param("not_a_field", "x", "", id="key-outside-the-vocabulary"),
        pytest.param(None, "x", "", id="key-not-a-string"),
        pytest.param("transmittal_number", 7, "", id="value-not-a-string"),
        pytest.param("transmittal_number", "x", 7, id="label-not-a-string"),
    ],
)
def test_a_malformed_field_entry_is_refused(key: object, value: object, label: object) -> None:
    with pytest.raises(TemplateError):
        FieldEntry(key=key, value=value, label=label)  # type: ignore[arg-type]


# --- Line: what a page reads as ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "style"),
    [
        pytest.param("x", "not_a_style", id="style-outside-the-closed-set"),
        pytest.param("   ", "text", id="blank-text"),
        pytest.param(None, "text", id="text-not-a-string"),
    ],
)
def test_a_malformed_line_is_refused(text: object, style: str) -> None:
    with pytest.raises(TemplateError):
        Line(text=text, style=style)  # type: ignore[arg-type]


# --- LayoutTemplate ----------------------------------------------------------


def _template_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "template_id": "TPL-TEST",
        "form_title": "TITLE",
        "subtitle": "subtitle",
        "field_order": tuple(sorted(TRANSMITTAL_FIELD_KEYS)),
        "value_placement": "inline",
        "body_font_size": 9,
        "leading": 12,
        "label_width": 130,
        "rule_lines": False,
    }
    base.update(overrides)
    return base


def test_a_template_carrying_the_whole_transmittal_block_is_constructible() -> None:
    built = LayoutTemplate(**_template_kwargs())  # type: ignore[arg-type]
    assert set(built.field_order) == set(TRANSMITTAL_FIELD_KEYS)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"template_id": "  "}, id="blank-template-id"),
        pytest.param({"value_placement": "sideways"}, id="value-placement-outside-the-set"),
        pytest.param(
            {"field_order": (*sorted(TRANSMITTAL_FIELD_KEYS), "transmittal_number")},
            id="field-order-repeats-a-field",
        ),
        pytest.param(
            {"field_order": tuple(sorted(TRANSMITTAL_FIELD_KEYS))[:-1]},
            id="field-order-drops-a-field",
        ),
        pytest.param(
            {"field_order": (*sorted(TRANSMITTAL_FIELD_KEYS), "material_item")},
            id="field-order-adds-a-field",
        ),
        pytest.param({"body_font_size": 0}, id="font-size-not-positive"),
        pytest.param({"leading": True}, id="leading-not-an-integer"),
        pytest.param({"label_width": -1}, id="label-width-negative"),
        pytest.param({"leading": 9, "body_font_size": 9}, id="leading-does-not-exceed-font-size"),
    ],
)
def test_a_malformed_template_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(TemplateError):
        LayoutTemplate(**_template_kwargs(**overrides))  # type: ignore[arg-type]


def test_a_template_missing_a_structural_field_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-023 asserted on the template rather than on each document.

    Unreachable while the transmittal block contains the six — which is the
    intent — so the guard is exercised against a narrowed block rather than left
    as a branch nothing can enter.
    """
    narrowed = frozenset({"project_identifier", "contract_number"})
    monkeypatch.setattr(templates_module, "TRANSMITTAL_FIELD_KEYS", narrowed)
    with pytest.raises(TemplateError, match="FR-023"):
        LayoutTemplate(**_template_kwargs(field_order=tuple(sorted(narrowed))))  # type: ignore[arg-type]


@pytest.mark.parametrize("template_id", ["TPL-NOT-A-TEMPLATE", None])
def test_a_template_id_outside_the_closed_set_is_refused(template_id: object) -> None:
    with pytest.raises(TemplateError):
        template(template_id)  # type: ignore[arg-type]


def test_every_committed_template_is_reachable_by_its_id() -> None:
    assert tuple(sorted(TEMPLATES)) == TEMPLATE_IDS
    for template_id in TEMPLATE_IDS:
        assert template(template_id).template_id == template_id


# --- assign_templates: FR-029 as a property of the function ------------------


def test_assignment_spreads_vendors_across_templates() -> None:
    vendors = [f"VND-{index:03d}" for index in range(1, 13)]
    assignment = assign_templates(vendors)
    assert set(assignment) == set(vendors)
    assert len(set(assignment.values())) >= 2


def test_assignment_is_invariant_to_the_order_the_vendors_arrive_in() -> None:
    """Sorting removes the roster file's own ordering from the result, so
    reordering the roster without changing it does not reshuffle every layout."""
    vendors = [f"VND-{index:03d}" for index in range(1, 13)]
    assert dict(assign_templates(vendors)) == dict(assign_templates(list(reversed(vendors))))


@pytest.mark.parametrize(
    "vendors", [pytest.param([], id="no-vendors"), pytest.param(["VND-001"], id="one-vendor")]
)
def test_an_unsatisfiable_assignment_is_refused(vendors: list[str]) -> None:
    """FR-029 is unsatisfiable for fewer than two vendors, and saying so is
    better than returning an assignment that violates it."""
    with pytest.raises(TemplateError):
        assign_templates(vendors)


def test_a_single_template_id_cannot_satisfy_fr_029(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard behind the round-robin, exercised against a narrowed set.

    With the committed templates this cannot fire — which is the point of
    choosing a round-robin over a hash — so it is shown failing against a
    template set of one rather than left as an unenterable branch.
    """
    monkeypatch.setattr(templates_module, "TEMPLATE_IDS", (TEMPLATE_IDS[0],))
    with pytest.raises(TemplateError, match="FR-029"):
        assign_templates(["VND-001", "VND-002"])


# --- layout_lines and page_text ---------------------------------------------


def test_the_anchor_is_the_first_line_of_every_page() -> None:
    """FR-032: the citation anchor lives in the header band the raster never
    covers, and its position is a layout property rather than a masking trick."""
    lines = layout_lines(template(TEMPLATE_IDS[0]), anchor=ANCHOR, fields=_fields())
    assert lines[0].text == ANCHOR and lines[0].style == "anchor"


def test_a_blank_field_value_still_emits_its_label() -> None:
    """VR-035a derives `MISSING_OR_BLANK_FIELD` from a canonical label appearing
    with no value text, so a layout that dropped the label would erase the
    evidence the class is recognised by."""
    below = next(
        template(template_id)
        for template_id in TEMPLATE_IDS
        if template(template_id).value_placement == "below"
    )
    lines = layout_lines(
        below, anchor=ANCHOR, fields=(FieldEntry(key="transmittal_number", value=""),)
    )
    labels = [line.text for line in lines if line.style == "field_label"]
    assert labels and labels[0].endswith(LABEL_SUFFIX)
    assert not [line for line in lines if line.style == "field_value"]


def test_a_heading_and_trailing_text_are_placed_when_supplied() -> None:
    lines = layout_lines(
        template(TEMPLATE_IDS[0]),
        anchor=ANCHOR,
        fields=_fields(),
        heading="Submitted Items",
        trailing_text=("Remarks: none.",),
        with_title=False,
    )
    styles = [line.style for line in lines]
    assert "heading" in styles and "title" not in styles
    assert lines[-1].text == "Remarks: none."


def test_two_entries_sharing_one_transmittal_key_on_one_page_are_refused() -> None:
    """The transmittal block carries each field once; an item list repeats keys
    and is kept outside the block, which is why the check is scoped to it."""
    with pytest.raises(TemplateError):
        layout_lines(
            template(TEMPLATE_IDS[0]),
            anchor=ANCHOR,
            fields=(*_fields(), _entry()),
        )


def test_layout_lines_refuses_something_that_is_not_a_template() -> None:
    with pytest.raises(TemplateError):
        layout_lines("TPL-STACKED-A", anchor=ANCHOR, fields=_fields())  # type: ignore[arg-type]


def test_page_text_is_one_line_per_line_in_reading_order() -> None:
    lines = layout_lines(template(TEMPLATE_IDS[0]), anchor=ANCHOR, fields=_fields())
    assert page_text(lines).split("\n") == [line.text for line in lines]
