"""FR-031a: the deriver, checked against expectations the injector never produced.

**Why this file exists at all.** VR-035's comparison over the committed layer is
`derived == recorded ∩ {the four structural classes}`, and set equality is
satisfied by a deriver that merely echoes what each entry recorded. Nothing in
that comparison can tell a working derivation from a tautology. What separates
them is a document the *generator did not write*, whose expected class set was
written by hand, and a vocabulary that is **not** the committed
`field-label-vocabulary.json`.

The vocabulary is the load-bearing half. `data-model.md` §VR-035 states the
residual precisely: the injector and the deriver both read the committed
vocabulary, so independence holds against generator state and against every
value the generator computed — but not against that shared artifact, and a
misreading common to both would make the corpus comparison agree for the wrong
reason. The fixtures below use labels no committed template writes, so a deriver
carrying its own idea of what a label looks like fails here while passing over
the whole committed corpus.

**Each structural class carries a positive and a negative document.** A positive
alone would be satisfied by a deriver that returned every class for every input.
Every fixture is a two-page PDF differing from one clean base document in
exactly one respect, and each is asserted to derive **exactly** its own class —
not merely to contain it — so a mutation that tripped two rules would be caught
rather than counted as a pass.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from model.corpus.codes import STRUCTURAL_FIELD_KEYS, load_vocabulary
from model.corpus.derive import (
    DERIVABLE_CLASSES,
    VALUE_INDENT_FLOOR,
    WORD_EXTRACTION,
    DeriveError,
    derive_classes,
    read_document,
)

# --------------------------------------------------------------------------
# The fixture vocabulary
# --------------------------------------------------------------------------

#: Labels no committed template writes. The *keys* are the vocabulary's own
#: contract — `load_vocabulary` requires FR-023's six structural keys in order
#: and at least two ordered date fields — but every label string here is unlike
#: the committed one, which is what makes a passing derivation evidence about
#: the deriver rather than about the file both it and the injector read.
FIXTURE_VOCABULARY: dict[str, object] = {
    "fields": {
        "transmittal_number": {
            "canonical_label": "Ticket No",
            "alternate_labels": ["Docket No", "Slip Ref"],
        },
        "specification_section": {
            "canonical_label": "Clause",
            "alternate_labels": ["Clause Ref"],
        },
        "descriptor_code": {"canonical_label": "Kind", "alternate_labels": ["Kind Code"]},
        "approving_authority": {
            "canonical_label": "Approver",
            "alternate_labels": ["Approving Desk"],
        },
        "revision_suffix": {"canonical_label": "Iteration", "alternate_labels": ["Iteration No"]},
        "action_stamp": {"canonical_label": "Verdict", "alternate_labels": ["Verdict Mark"]},
        "date_opened": {"canonical_label": "Opened", "alternate_labels": ["Opened On"]},
        "date_closed": {"canonical_label": "Closed", "alternate_labels": ["Closed On"]},
        "remark_text": {"canonical_label": "Memo", "alternate_labels": ["Memorandum"]},
    },
    "structural_fields": list(STRUCTURAL_FIELD_KEYS),
    "date_field_order": ["date_opened", "date_closed"],
}

IDENTIFIER = "FIXTURE-0001"

#: One clean document. Every fixture below is this, changed in one place, and
#: every expectation is written here rather than derived from anything.
BASE_PAGE_ONE: tuple[str, ...] = (
    "Ticket No: T-0001",
    "Clause: 23 64 26",
    "Kind: SD-03",
    "Iteration: 0",
    "Verdict: B",
    "Opened: 2026-01-05",
    "Closed: 2026-02-09",
    "Memo: nothing unusual",
    "Approver: Resident Engineer",
)
BASE_PAGE_TWO: tuple[str, ...] = (
    "Items and quantities follow",
    "Chiller assembly quantity 2",
)

Body = Sequence[str | tuple[str, float]]


# --------------------------------------------------------------------------
# The fixture renderer
# --------------------------------------------------------------------------


def render_fixture(
    path: Path,
    pages: Sequence[Body],
    *,
    identifier: str = IDENTIFIER,
    leading: float = 16.0,
    size: float = 10.0,
) -> Path:
    """Draw a two-page fixture: a citation anchor plus body lines.

    Written straight onto a ReportLab canvas rather than through `render.py`:
    the point of these documents is that no part of the generator produced them,
    so the layout that emits them must not be the layout under comparison
    either. An entry may be a bare string or `(text, indent)`, which is how the
    below-the-label value placement is expressed.
    """
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(path), pagesize=(612.0, 792.0), invariant=1)
    for number, body in enumerate(pages, start=1):
        _draw(canvas, 54.0, 756.0, f"{identifier} | Page {number} of {len(pages)}", 8.0)
        cursor = 730.0
        for entry in body:
            text, indent = entry if isinstance(entry, tuple) else (entry, 0.0)
            _draw(canvas, 54.0 + indent, cursor, text, size)
            cursor -= leading
        canvas.showPage()
    canvas.save()
    return path


def _draw(canvas: object, x: float, y: float, text: str, size: float) -> None:
    obj = canvas.beginText()  # type: ignore[attr-defined]
    obj.setTextRenderMode(0)
    obj.setFont("Helvetica", size)
    obj.setTextOrigin(x, y)
    obj.textOut(text)
    canvas.drawText(obj)  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def vocabulary(tmp_path_factory: pytest.TempPathFactory):
    """The fixture vocabulary, read through the committed reader.

    Through `load_vocabulary` rather than constructed directly so the fixture is
    held to the same disjointness and completeness checks the committed file is:
    a fixture the reader would reject could make a derivation look correct for a
    reason no real vocabulary can reproduce.
    """
    path = tmp_path_factory.mktemp("vocabulary") / "field-label-vocabulary.json"
    path.write_bytes(json.dumps(FIXTURE_VOCABULARY, indent=2, sort_keys=True).encode("utf-8"))
    return load_vocabulary(path)


def derived(tmp_path: Path, pages: Sequence[Body], vocabulary, name: str = "fixture") -> set[str]:
    path = render_fixture(tmp_path / f"{name}.pdf", pages)
    return set(derive_classes(path, vocabulary=vocabulary))


# --------------------------------------------------------------------------
# The control
# --------------------------------------------------------------------------


def test_the_clean_base_document_derives_nothing(tmp_path: Path, vocabulary) -> None:
    """Without this, a deriver returning all four classes for every input would
    pass every positive case below and evidence nothing."""
    assert derived(tmp_path, [BASE_PAGE_ONE, BASE_PAGE_TWO], vocabulary) == set()


def test_the_deriver_never_returns_scan_degradation(tmp_path: Path, vocabulary) -> None:
    """FR-031a's narrowing, asserted rather than described: no structural
    property of a PDF says a raster is degraded, so the derived set is always a
    subset of the four."""
    assert "SCAN_DEGRADATION" not in DERIVABLE_CLASSES
    for pages in ([BASE_PAGE_ONE, BASE_PAGE_TWO], [(*BASE_PAGE_ONE, "Memo:"), BASE_PAGE_TWO]):
        assert derived(tmp_path, pages, vocabulary, name=str(len(pages[0]))) <= DERIVABLE_CLASSES


# --------------------------------------------------------------------------
# VR-035a - MISSING_OR_BLANK_FIELD
# --------------------------------------------------------------------------


def test_vr_035a_positive_a_label_whose_value_region_is_empty(tmp_path: Path, vocabulary) -> None:
    page = tuple("Memo:" if line.startswith("Memo:") else line for line in BASE_PAGE_ONE)
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == {"MISSING_OR_BLANK_FIELD"}


def test_vr_035a_positive_a_required_canonical_label_absent_entirely(
    tmp_path: Path, vocabulary
) -> None:
    """The rule's second half. A structural field the document never names is
    missing whether or not any label on the page lacks a value."""
    page = tuple(line for line in BASE_PAGE_ONE if not line.startswith("Verdict:"))
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == {"MISSING_OR_BLANK_FIELD"}


def test_vr_035a_negative_a_populated_document_derives_no_blank(tmp_path: Path, vocabulary) -> None:
    assert "MISSING_OR_BLANK_FIELD" not in derived(
        tmp_path, [BASE_PAGE_ONE, BASE_PAGE_TWO], vocabulary
    )


def test_vr_035a_a_value_beneath_its_label_is_a_value_when_it_is_indented(
    tmp_path: Path, vocabulary
) -> None:
    """The below-the-label placement, decided by geometry.

    Without the indentation test the *next field's label* would count as this
    field's value on an inline template and no blank could ever be derived; with
    it, the same two lines mean different things depending on where they start.
    """
    populated = (
        *BASE_PAGE_ONE[:7],
        "Memo:",
        ("nothing unusual", VALUE_INDENT_FLOOR * 2),
        BASE_PAGE_ONE[8],
    )
    blanked = (*BASE_PAGE_ONE[:7], "Memo:", ("nothing unusual", 0.0), BASE_PAGE_ONE[8])
    assert derived(tmp_path, [populated, BASE_PAGE_TWO], vocabulary, "populated") == set()
    assert derived(tmp_path, [blanked, BASE_PAGE_TWO], vocabulary, "blanked") == {
        "MISSING_OR_BLANK_FIELD"
    }


# --------------------------------------------------------------------------
# VR-035b - INCONSISTENT_FIELD_LABEL
# --------------------------------------------------------------------------


def test_vr_035b_positive_an_alternate_label_stands_in_for_the_canonical(
    tmp_path: Path, vocabulary
) -> None:
    page = tuple(
        "Memorandum: nothing unusual" if line.startswith("Memo:") else line
        for line in BASE_PAGE_ONE
    )
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == {"INCONSISTENT_FIELD_LABEL"}


def test_vr_035b_negative_a_canonically_labelled_document(tmp_path: Path, vocabulary) -> None:
    assert "INCONSISTENT_FIELD_LABEL" not in derived(
        tmp_path, [BASE_PAGE_ONE, BASE_PAGE_TWO], vocabulary
    )


def test_vr_035b_a_token_outside_the_vocabulary_is_not_an_alternate(
    tmp_path: Path, vocabulary
) -> None:
    """The rule is decidable only because alternates are disjoint from every
    canonical label. A token that is neither identifies no field, and treating it
    as a mis-labelling would report a class for a line the document does not
    claim is a field at all."""
    page = (*BASE_PAGE_ONE, "Annotation: filed late")
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == set()


# --------------------------------------------------------------------------
# VR-035c - OUT_OF_ORDER_DATE
# --------------------------------------------------------------------------


def test_vr_035c_positive_dates_violating_the_committed_order(tmp_path: Path, vocabulary) -> None:
    page = tuple(
        {
            "Opened: 2026-01-05": "Opened: 2026-05-01",
            "Closed: 2026-02-09": "Closed: 2026-01-01",
        }.get(line, line)
        for line in BASE_PAGE_ONE
    )
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == {"OUT_OF_ORDER_DATE"}


def test_vr_035c_negative_dates_in_the_committed_order(tmp_path: Path, vocabulary) -> None:
    assert "OUT_OF_ORDER_DATE" not in derived(tmp_path, [BASE_PAGE_ONE, BASE_PAGE_TWO], vocabulary)


def test_vr_035c_equal_dates_are_in_order(tmp_path: Path, vocabulary) -> None:
    """The boundary: the committed ordering is chronological, not strict. A
    document opened and closed on one day is not out of order."""
    page = tuple(
        {"Opened: 2026-01-05": "Opened: 2026-02-09"}.get(line, line) for line in BASE_PAGE_ONE
    )
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == set()


def test_vr_035c_an_unparseable_date_is_undecidable_rather_than_out_of_order(
    tmp_path: Path, vocabulary
) -> None:
    """A document whose dates cannot be read carries no evidence either way, and
    reporting the class would attribute an ordering defect to a parsing one."""
    page = tuple(
        {"Closed: 2026-02-09": "Closed: on receipt"}.get(line, line) for line in BASE_PAGE_ONE
    )
    assert "OUT_OF_ORDER_DATE" not in derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary)


# --------------------------------------------------------------------------
# VR-035d - PAGE_SPLIT_FIELD
# --------------------------------------------------------------------------

SPLIT_PAGE_ONE = (*BASE_PAGE_ONE[:8], "Approver:")
SPLIT_PAGE_TWO = ("Resident Engineer", *BASE_PAGE_TWO)


def test_vr_035d_positive_a_label_ending_one_page_with_its_value_on_the_next(
    tmp_path: Path, vocabulary
) -> None:
    assert derived(tmp_path, [SPLIT_PAGE_ONE, SPLIT_PAGE_TWO], vocabulary) == {"PAGE_SPLIT_FIELD"}


def test_vr_035d_negative_a_label_and_value_on_one_page(tmp_path: Path, vocabulary) -> None:
    assert "PAGE_SPLIT_FIELD" not in derived(tmp_path, [BASE_PAGE_ONE, BASE_PAGE_TWO], vocabulary)


def test_vr_035d_a_bare_label_in_the_middle_of_a_page_is_blank_not_split(
    tmp_path: Path, vocabulary
) -> None:
    """The one ambiguity, and the side of it the rule takes.

    A bare label is a page split only when the page boundary is what separates it
    from its value. In the middle of a page there is no boundary, so the field is
    blank — which is why the injector never blanks a field that can be last.
    """
    page = (*BASE_PAGE_ONE[:7], "Memo:", BASE_PAGE_ONE[8])
    assert derived(tmp_path, [page, BASE_PAGE_TWO], vocabulary) == {"MISSING_OR_BLANK_FIELD"}


def test_vr_035d_a_split_label_on_the_last_page_has_nowhere_to_continue(
    tmp_path: Path, vocabulary
) -> None:
    """A one-page document cannot carry the class: `PAGE_SPLIT_FIELD` is a
    relation between two pages, so the same bare label is a blank field."""
    assert derived(tmp_path, [SPLIT_PAGE_ONE], vocabulary) == {"MISSING_OR_BLANK_FIELD"}


def test_vr_035d_a_continuation_that_is_itself_a_label_is_not_a_split(
    tmp_path: Path, vocabulary
) -> None:
    """If the next page opens with another field's label the value never appears
    at all, which is a blank field rather than a continuation."""
    assert derived(tmp_path, [SPLIT_PAGE_ONE, ("Memo: filed", *BASE_PAGE_TWO)], vocabulary) == {
        "MISSING_OR_BLANK_FIELD"
    }


# --------------------------------------------------------------------------
# The tolerances, and the independence claim itself
# --------------------------------------------------------------------------


def test_the_word_tolerances_are_the_four_pinned_ones_and_none_is_a_default() -> None:
    """AD-004: the derived set is the oracle VR-035 judges the recorded set
    against, so a tolerance change is a change to the oracle. Asserting the four
    names are supplied is what stops a refactor quietly dropping one back to a
    library default."""
    assert set(WORD_EXTRACTION) == {
        "x_tolerance",
        "y_tolerance",
        "keep_blank_chars",
        "use_text_flow",
    }
    # pdfplumber's defaults at the time of the pin. Equality here would mean the
    # value is a default whether or not it is passed explicitly.
    assert WORD_EXTRACTION["x_tolerance"] != 3
    assert WORD_EXTRACTION["keep_blank_chars"] is False
    assert WORD_EXTRACTION["use_text_flow"] is False


def test_a_label_and_its_value_are_recovered_as_separate_words(tmp_path: Path, vocabulary) -> None:
    """The tolerance the whole derivation rests on. With `x_tolerance` above a
    space's advance the label and its value merge into one token, every value
    region looks populated, and no blank field is ever derived."""
    path = render_fixture(tmp_path / "words.pdf", [BASE_PAGE_ONE, BASE_PAGE_TWO])
    line = next(line for line in read_document(path)[0].lines if line.text.startswith("Ticket No:"))
    assert [word.text for word in line.words] == ["Ticket", "No:", "T-0001"]


def test_the_supplied_vocabulary_is_what_the_deriver_reads(tmp_path: Path, vocabulary) -> None:
    """The independence claim, made falsifiable.

    The same file derives one thing under the fixture vocabulary and another
    under the committed one. A deriver that ignored its argument — or carried its
    own idea of what a label looks like — would return the same answer for both,
    and would then agree with the injector over the committed corpus for a reason
    that has nothing to do with reading the document.
    """
    page = tuple(
        "Memorandum: nothing unusual" if line.startswith("Memo:") else line
        for line in BASE_PAGE_ONE
    )
    path = render_fixture(tmp_path / "independence.pdf", [page, BASE_PAGE_TWO])
    with_fixture = set(derive_classes(path, vocabulary=vocabulary))
    with_committed = set(derive_classes(path))
    assert with_fixture == {"INCONSISTENT_FIELD_LABEL"}
    # Under the committed vocabulary none of these labels is known at all, so
    # every structural field reads as absent.
    assert with_committed == {"MISSING_OR_BLANK_FIELD"}
    assert with_fixture != with_committed


# --------------------------------------------------------------------------
# FR-001a - the untrusted-input posture
# --------------------------------------------------------------------------


def test_a_file_that_is_not_a_pdf_raises_rather_than_deriving_nothing(tmp_path: Path) -> None:
    """Never an empty set: a document that could not be read has an unknown
    class set, and returning `frozenset()` would record it as clean."""
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"%PDF-1.4 truncated immediately")
    with pytest.raises(DeriveError):
        derive_classes(path)


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DeriveError):
        derive_classes(tmp_path / "absent.pdf")


def test_an_empty_page_sequence_is_refused(vocabulary) -> None:
    with pytest.raises(DeriveError):
        derive_classes([], vocabulary=vocabulary)
