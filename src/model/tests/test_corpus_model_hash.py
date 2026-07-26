"""FR-021 / DM-1…DM-6: the pre-render document model and its canonical digest.

Written and committed **before** `src/model/src/model/corpus/model.py` exists
(HINT-001). Until that module lands this file fails at import with
`ModuleNotFoundError`, which is the point: every determinism claim in this epic
reduces to `document_model_hash`, and the red state is recorded as a fact in the
branch history rather than asserted afterwards by whoever wrote the module.

Properties PB-1…PB-4 and PB-6 of `plan.md` §Property-Based Test Specification,
plus the boundary cases that specification names separately, because generated
coverage is not systematic. PB-5 belongs to the manifest writer and lives in
`test_corpus_manifest.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st
from model.corpus.model import (
    DocumentModel,
    DocumentModelError,
    FieldValue,
    Page,
    RenderDirective,
    canonical_bytes,
    document_model_hash,
    parse_canonical_bytes,
)

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

# Closed sets stand in for the ones templates.py and degrade.py will own. The
# property under test is the serialization's, not the vocabulary's.
TEMPLATE_IDS = ("tmpl-transmittal-a", "tmpl-transmittal-b", "tmpl-transmittal-c")
DEGRADATION_PROFILES = ("none", "light-scan", "heavy-scan")

# Names and identifiers: letters and digits only, so they are non-blank by
# construction. Values and page text draw from the whole UTF-8 range instead —
# combining marks and non-NFC sequences are explicitly inside the domain.
names = st.text(
    alphabet=st.characters(categories=("Lu", "Ll", "Nd"), codec="utf-8"),
    min_size=1,
    max_size=16,
)
identity_values = st.text(
    alphabet=st.characters(categories=("Lu", "Ll", "Nd"), codec="utf-8"),
    min_size=1,
    max_size=16,
)
# min_size=0: an empty field value is a MISSING_OR_BLANK_FIELD irregularity, so
# the model must be able to hold one.
free_text = st.text(alphabet=st.characters(codec="utf-8"), max_size=24)

parameter_values = st.one_of(
    st.integers(min_value=0, max_value=255),
    st.text(alphabet=st.characters(categories=("Lu", "Ll", "Nd"), codec="utf-8"), max_size=8),
)
render_directives = st.builds(
    RenderDirective,
    template_id=st.sampled_from(TEMPLATE_IDS),
    degradation_profile=st.sampled_from(DEGRADATION_PROFILES),
    # At least one parameter, so PB-3's narrowest mutation always has a target.
    parameters=st.dictionaries(names, parameter_values, min_size=1, max_size=3),
)
pages = st.builds(Page, text=free_text, directive=render_directives)
field_values = st.builds(FieldValue, label=names, value=free_text)


@st.composite
def document_models(draw: st.DrawFn) -> DocumentModel:
    return DocumentModel(
        identity=draw(st.dictionaries(names, identity_values, min_size=1, max_size=4)),
        fields=tuple(draw(st.lists(field_values, min_size=1, max_size=6))),
        pages=tuple(draw(st.lists(pages, min_size=1, max_size=6))),
    )


def to_nfd(model: DocumentModel) -> DocumentModel:
    """The same model with every string in NFD rather than NFC (PB-1, DM-5)."""

    def d(value: str) -> str:
        return unicodedata.normalize("NFD", value)

    return DocumentModel(
        identity={d(k): d(v) for k, v in model.identity.items()},
        fields=tuple(FieldValue(label=d(f.label), value=d(f.value)) for f in model.fields),
        pages=tuple(
            Page(
                text=d(page.text),
                directive=RenderDirective(
                    template_id=d(page.directive.template_id),
                    degradation_profile=d(page.directive.degradation_profile),
                    parameters={
                        d(k): (d(v) if isinstance(v, str) else v)
                        for k, v in page.directive.parameters.items()
                    },
                ),
            )
            for page in model.pages
        ),
    )


# --------------------------------------------------------------------------
# PB-1 — metamorphic: Unicode normal form does not move the digest (DM-5)
# --------------------------------------------------------------------------


@given(model=document_models())
def test_pb1_an_nfd_equivalent_model_yields_the_same_digest(model: DocumentModel) -> None:
    assert document_model_hash(to_nfd(model)) == document_model_hash(model)


def test_pb1_boundary_the_named_nfc_nfd_pair() -> None:
    """The case the generated population is not guaranteed to reach: one
    precomposed character against its decomposed equivalent."""
    composed = "Sección Eléctrica"  # U+00F3, U+00E9
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed

    def build(value: str) -> DocumentModel:
        return DocumentModel(
            identity={"document_id": "PRJ-001-SUB-0001"},
            fields=(FieldValue(label="specification_section", value=value),),
            pages=(Page(text=value, directive=RenderDirective("tmpl-transmittal-a", "none")),),
        )

    assert document_model_hash(build(decomposed)) == document_model_hash(build(composed))


# --------------------------------------------------------------------------
# PB-2 — invariant: construction order and PYTHONHASHSEED do not move it
# --------------------------------------------------------------------------


@given(model=document_models())
def test_pb2_serialization_ignores_the_order_keys_were_constructed_in(
    model: DocumentModel,
) -> None:
    reversed_order = DocumentModel(
        identity={k: model.identity[k] for k in reversed(list(model.identity))},
        fields=model.fields,
        pages=tuple(
            Page(
                text=page.text,
                directive=RenderDirective(
                    template_id=page.directive.template_id,
                    degradation_profile=page.directive.degradation_profile,
                    parameters={
                        k: page.directive.parameters[k]
                        for k in reversed(list(page.directive.parameters))
                    },
                ),
            )
            for page in model.pages
        ),
    )
    assert canonical_bytes(reversed_order) == canonical_bytes(model)


SEEDED_SPEC = {
    "identity": {"document_id": "PRJ-001-SUB-0001", "vendor_id": "VND-007"},
    "fields": [
        {"label": "transmittal_number", "value": "TR-0042"},
        {"label": "reviewer_action", "value": "Revise and Resubmit"},
        {"label": "remarks", "value": ""},
    ],
    "pages": [
        {
            "text": "Sección 26 05 00 — cable tray submittal",
            "render": {
                "template_id": "tmpl-transmittal-b",
                "degradation_profile": "heavy-scan",
                "parameters": {"blur_radius": 3, "rotation_deg": 1, "noise": "gaussian"},
            },
        }
    ],
}

_SUBPROCESS_HASH = """
import json, sys
from model.corpus.model import (
    DocumentModel, FieldValue, Page, RenderDirective, document_model_hash,
)

spec = json.loads(sys.argv[1])
sys.stdout.write(
    document_model_hash(
        DocumentModel(
            identity=spec["identity"],
            fields=tuple(FieldValue(**f) for f in spec["fields"]),
            pages=tuple(
                Page(text=p["text"], directive=RenderDirective(**p["render"]))
                for p in spec["pages"]
            ),
        )
    )
)
"""


def hash_under_seed(seed: str) -> str:
    environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
    # Fixed argv against this interpreter, no shell: the only variable is the
    # environment the child starts with.
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_HASH, json.dumps(SEEDED_SPEC)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=True,
    )
    return result.stdout.strip()


def test_pb2_digest_does_not_depend_on_pythonhashseed() -> None:
    """Across processes, not only across calls: `PYTHONHASHSEED` is fixed at
    interpreter start, so two runs in one process cannot observe it."""
    first = hash_under_seed("0")
    second = hash_under_seed("524287")
    assert DIGEST_PATTERN.fullmatch(first)
    assert first == second


# --------------------------------------------------------------------------
# PB-3 — sensitivity: every hashed component moves the digest (DM-2, VR-040b)
# --------------------------------------------------------------------------


def mutate_identity(model: DocumentModel) -> DocumentModel:
    key = sorted(model.identity)[0]
    return DocumentModel(
        identity={**dict(model.identity), key: model.identity[key] + "x"},
        fields=model.fields,
        pages=model.pages,
    )


def mutate_field_value(model: DocumentModel) -> DocumentModel:
    head, *rest = model.fields
    changed = FieldValue(label=head.label, value=head.value + "x")
    return DocumentModel(identity=model.identity, fields=(changed, *rest), pages=model.pages)


def mutate_field_label(model: DocumentModel) -> DocumentModel:
    head, *rest = model.fields
    changed = FieldValue(label=head.label + "x", value=head.value)
    return DocumentModel(identity=model.identity, fields=(changed, *rest), pages=model.pages)


def mutate_page_text(model: DocumentModel) -> DocumentModel:
    head, *rest = model.pages
    changed = Page(text=head.text + "x", directive=head.directive)
    return DocumentModel(identity=model.identity, fields=model.fields, pages=(changed, *rest))


def replace_directive(model: DocumentModel, directive: RenderDirective) -> DocumentModel:
    head, *rest = model.pages
    return DocumentModel(
        identity=model.identity,
        fields=model.fields,
        pages=(Page(text=head.text, directive=directive), *rest),
    )


def mutate_template_id(model: DocumentModel) -> DocumentModel:
    current = model.pages[0].directive
    other = next(t for t in TEMPLATE_IDS if t != current.template_id)
    return replace_directive(
        model,
        RenderDirective(other, current.degradation_profile, dict(current.parameters)),
    )


def mutate_degradation_profile(model: DocumentModel) -> DocumentModel:
    current = model.pages[0].directive
    other = next(p for p in DEGRADATION_PROFILES if p != current.degradation_profile)
    return replace_directive(
        model,
        RenderDirective(current.template_id, other, dict(current.parameters)),
    )


def mutate_degradation_parameter(model: DocumentModel) -> DocumentModel:
    current = model.pages[0].directive
    parameters = dict(current.parameters)
    key = sorted(parameters)[0]
    value = parameters[key]
    parameters[key] = value + 1 if isinstance(value, int) else value + "x"
    return replace_directive(
        model,
        RenderDirective(current.template_id, current.degradation_profile, parameters),
    )


MUTATIONS = (
    mutate_identity,
    mutate_field_value,
    mutate_field_label,
    mutate_page_text,
    mutate_template_id,
    mutate_degradation_profile,
    mutate_degradation_parameter,
)


@given(model=document_models(), mutation=st.sampled_from(MUTATIONS))
def test_pb3_mutating_any_hashed_component_changes_the_digest(model, mutation) -> None:
    assert document_model_hash(mutation(model)) != document_model_hash(model)


def test_pb3_boundary_two_models_differing_only_in_one_degradation_parameter() -> None:
    """DM-2's narrowest case, and the reason render directives are hashed at
    all: the text layer is byte-identical between these two models."""

    def build(blur: int) -> DocumentModel:
        return DocumentModel(
            identity={"document_id": "PRJ-002-SUB-0007"},
            fields=(FieldValue(label="transmittal_number", value="TR-0007"),),
            pages=(
                Page(
                    text="Anchor bolt schedule",
                    directive=RenderDirective(
                        template_id="tmpl-transmittal-a",
                        degradation_profile="light-scan",
                        parameters={"blur_radius": blur},
                    ),
                ),
            ),
        )

    quiet, loud = build(1), build(2)
    assert [p.text for p in quiet.pages] == [p.text for p in loud.pages]
    assert document_model_hash(quiet) != document_model_hash(loud)


# --------------------------------------------------------------------------
# PB-4 — round trip: parse ∘ serialize is the identity, and re-serializing is
# a fixpoint (DM-5, MS-3)
# --------------------------------------------------------------------------


@given(model=document_models())
def test_pb4_parse_of_serialize_returns_the_model(model: DocumentModel) -> None:
    assert parse_canonical_bytes(canonical_bytes(model)) == model


@given(model=document_models())
def test_pb4_reserializing_the_parse_is_a_fixpoint(model: DocumentModel) -> None:
    once = canonical_bytes(model)
    assert canonical_bytes(parse_canonical_bytes(once)) == once


# --------------------------------------------------------------------------
# PB-6 — invariant: the emitted digest form (FR-007, VR-016)
# --------------------------------------------------------------------------


@given(model=document_models())
def test_pb6_every_emitted_digest_has_the_declared_form(model: DocumentModel) -> None:
    assert DIGEST_PATTERN.fullmatch(document_model_hash(model))


# --------------------------------------------------------------------------
# Remaining named boundary cases and the serialization's stated shape
# --------------------------------------------------------------------------


def test_boundary_a_single_page_document() -> None:
    model = DocumentModel(
        identity={"document_id": "PRJ-003-SUB-0001"},
        fields=(FieldValue(label="transmittal_number", value="TR-0001"),),
        pages=(
            Page(text="One page only", directive=RenderDirective("tmpl-transmittal-a", "none")),
        ),
    )
    assert len(model.pages) == 1
    assert DIGEST_PATTERN.fullmatch(document_model_hash(model))


def test_boundary_an_empty_per_page_text_string() -> None:
    """An empty page is inside the domain and must not collapse into an absent
    one: a two-page document whose second page is blank differs from a
    one-page document."""
    directive = RenderDirective("tmpl-transmittal-a", "none")
    one_page = DocumentModel(
        identity={"document_id": "PRJ-004-SUB-0002"},
        fields=(FieldValue(label="transmittal_number", value="TR-0002"),),
        pages=(Page(text="Cover", directive=directive),),
    )
    with_blank_page = DocumentModel(
        identity=dict(one_page.identity),
        fields=one_page.fields,
        pages=(*one_page.pages, Page(text="", directive=directive)),
    )
    assert document_model_hash(with_blank_page) != document_model_hash(one_page)


def test_serialization_is_compact_utf8_with_no_trailing_newline() -> None:
    """DM-5 as bytes rather than as a description: sorted keys, compact
    separators, non-ASCII preserved rather than escaped, no trailing newline."""
    raw = canonical_bytes(
        DocumentModel(
            identity={"vendor_id": "VND-001", "document_id": "PRJ-005-SUB-0003"},
            fields=(FieldValue(label="remarks", value="Sección"),),
            pages=(Page(text="Página 1", directive=RenderDirective("tmpl-transmittal-c", "none")),),
        )
    )
    assert b", " not in raw
    assert b'": ' not in raw
    assert not raw.endswith(b"\n")
    assert "Sección".encode() in raw
    assert raw.index(b'"document_id"') < raw.index(b'"vendor_id"')


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (1.5, "float — platform repr is not a stable serialization"),
        (True, "boolean"),
        (None, "null"),
        ([1], "container"),
    ],
)
def test_non_scalar_render_parameters_are_rejected(value: object, reason: str) -> None:
    """The domain is strings, integers, and containers of them (DM-3, CS-5).
    Rejected at construction, so no un-hashable value ever reaches the digest."""
    with pytest.raises(DocumentModelError):
        RenderDirective("tmpl-transmittal-a", "none", {"blur_radius": value})
