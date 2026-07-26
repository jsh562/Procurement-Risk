"""VR-050 / FR-031b / FR-032: the injectors, judged against an undegraded control.

**Covering a class is an assertion over the injector's effect on the emitted
artifact**, not a test named for the class. For each of the four structural
classes the deriver must recover exactly that class from the emitted document
and must **not** recover it from a control built from the same plan without the
injection — a one-sided assertion would be satisfied by a deriver that returned
everything, and a test that only named the class would be satisfied by an
injector that did nothing.

`SCAN_DEGRADATION` is the class no structural derivation confirms, so this file
is its whole evidence path. Its oracle is the same page rendered with the
degradation profile **disabled**, and a degraded page passes when

- its body raster differs from the control's (the control carries none at all,
  and the degraded raster differs pixel-for-pixel from the clean raster of the
  same body), and
- its extracted text layer is identical to the control's, and
- its citation anchor's text objects lie outside every raster rectangle.

The last two are asserted across the injector's **declared parameter domain** —
the profiles and ranges `generation-config.json` admits, sampled by the property
generator and pinned at both corners — rather than only over the values the
committed layer happens to use. That is what makes FR-032's "bounded" a property
of the injector rather than an observation about pages that were emitted.

Rendering a PDF and re-extracting it costs a few hundred milliseconds, so the
property test states its own example budget rather than taking the entry's
200-example profile. The budget is stated with the corner cases beside it: the
generated population samples the interior, and the corners are enumerated,
because generated coverage is not systematic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from model.corpus.degrade import (
    PARAMETER_NAMES,
    RASTER_MODE,
    DegradeError,
    body_raster,
    degrade_page,
    degraded_raster,
)
from model.corpus.derive import derive_classes, page_text, read_document
from model.corpus.generate import DocumentPlan, build_document, load_config, plan_layer
from model.corpus.irregularity import (
    CLASS_ROTATION,
    CLASSED_DOCUMENT_FLOOR,
    STRUCTURAL_CLASSES,
    IrregularityClass,
    IrregularityError,
    inject,
)
from model.corpus.model import RenderDirective
from model.corpus.render import (
    BODY_RECT,
    HEADER_BAND,
    UNDEGRADED_PROFILE,
    PageRender,
    render_document,
)
from model.corpus.templates import template
from model.roster.reader import read_roster

CONFIG = load_config()

#: The four the deriver recovers, as plain strings.
STRUCTURAL = tuple(sorted(member.value for member in STRUCTURAL_CLASSES))


# --------------------------------------------------------------------------
# One planned layer, injected once
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def planned() -> tuple[Mapping[str, DocumentPlan], Mapping[str, DocumentPlan]]:
    """The layer before and after injection, keyed by document identifier.

    The *pair* is the point: every case below renders an injected plan and the
    plan it was made from, so the control is the same document without the one
    change under test rather than a different document that happens to be clean.
    """
    roster = read_roster()
    before = plan_layer(roster, CONFIG)
    after = inject(before, config=CONFIG)
    return (
        {plan.document_id: plan for plan in before},
        {plan.document_id: plan for plan in after},
    )


def emit(plan: DocumentPlan, directory: Path, name: str) -> Path:
    """Render one plan, degrading any page whose directive names a profile."""
    directory.mkdir(parents=True, exist_ok=True)
    document = build_document(plan)
    return render_document(document.layout, directory / f"{name}.pdf", degrader=degrade_page)


def carrying(after: Mapping[str, DocumentPlan], class_name: str) -> DocumentPlan:
    """The first injected plan recording `class_name`, by document identifier."""
    for document_id in sorted(after):
        if class_name in after[document_id].irregularity_classes:
            return after[document_id]
    raise AssertionError(f"the injected layer records {class_name} on no document")


# --------------------------------------------------------------------------
# VR-050 - the four structural classes, each against its own control
# --------------------------------------------------------------------------


@pytest.mark.parametrize("class_name", STRUCTURAL)
def test_vr_050_the_deriver_recovers_exactly_the_injected_class(
    planned, class_name: str, tmp_path: Path
) -> None:
    _, after = planned
    injected = carrying(after, class_name)
    derived = set(derive_classes(emit(injected, tmp_path, "injected")))
    recorded = set(injected.irregularity_classes) & set(STRUCTURAL)
    assert derived == recorded, (
        f"VR-050: {class_name} — derived {sorted(derived)} from the emitted document but the "
        f"plan recorded {sorted(recorded)} among the structural four"
    )
    assert class_name in derived, f"VR-050: {class_name} was not recovered from the artifact"


@pytest.mark.parametrize("class_name", STRUCTURAL)
def test_vr_050_the_control_does_not_carry_the_class(
    planned, class_name: str, tmp_path: Path
) -> None:
    """The half that makes the case above evidence.

    Without it, a deriver returning every class for every input would satisfy
    "the class was recovered" for all four.
    """
    before, after = planned
    control = before[carrying(after, class_name).document_id]
    derived = set(derive_classes(emit(control, tmp_path, "control")))
    assert class_name not in derived, (
        f"VR-050: {class_name} was recovered from an uninjected control, so recovering it from "
        f"the injected document evidences nothing. Control derived {sorted(derived)}"
    )
    assert derived == set(), f"VR-050: an uninjected plan derived {sorted(derived)}"


def test_vr_050_every_structural_class_has_an_injected_document(planned) -> None:
    """The population this file quantifies over, asserted rather than assumed."""
    _, after = planned
    present = {value for plan in after.values() for value in plan.irregularity_classes}
    assert set(member.value for member in IrregularityClass) <= present


# --------------------------------------------------------------------------
# VR-050 - SCAN_DEGRADATION, over the declared parameter domain
# --------------------------------------------------------------------------


def profiles_and_parameters() -> st.SearchStrategy[tuple[str, dict[str, int]]]:
    """The injector's declared parameter domain, read from the committed config.

    Drawn from the configuration rather than from literals so the domain the
    property is asserted over is the domain the generator may inject from; a
    hand-written range here would let a configuration change move the injector
    outside what this file ever tries.
    """
    return st.sampled_from(sorted(CONFIG.degradation_profiles)).flatmap(
        lambda profile: st.fixed_dictionaries(
            {
                name: st.integers(min_value=int(bounds["min"]), max_value=int(bounds["max"]))
                for name, bounds in CONFIG.degradation_profiles[profile].items()
            }
        ).map(lambda parameters: (profile, parameters))
    )


def corner_cases() -> list[tuple[str, dict[str, int]]]:
    """Both ends of every range of every profile, enumerated.

    Named separately from the generated population because generated coverage is
    not systematic: the extremes are where a bounded operation stops being
    bounded, and a sampler is not obliged to reach them.
    """
    cases: list[tuple[str, dict[str, int]]] = []
    for profile in sorted(CONFIG.degradation_profiles):
        bounds = CONFIG.degradation_profiles[profile]
        for end in ("min", "max"):
            cases.append((profile, {name: int(bounds[name][end]) for name in bounds}))
    return cases


@pytest.fixture(scope="session")
def degradation_subject(planned, tmp_path_factory: pytest.TempPathFactory):
    """A plan with no degradation, its rendered control, and the control's pages.

    Session-scoped and parameter-independent: the control does not depend on the
    profile under test, so re-rendering it per example would multiply the cost of
    the property run without changing what it asserts.
    """
    before, _ = planned
    plan = replace(
        before[sorted(before)[0]],
        page_directives=tuple(
            RenderDirective(
                template_id=directive.template_id, degradation_profile=UNDEGRADED_PROFILE
            )
            for directive in before[sorted(before)[0]].page_directives
        ),
    )
    directory = tmp_path_factory.mktemp("degradation")
    control = emit(plan, directory, "control")
    return plan, directory, control, read_document(control)


def degraded_pages(plan: DocumentPlan, directory: Path, profile: str, parameters: Mapping):
    """Render the same plan with page one degraded, and read it back."""
    directives = list(plan.page_directives)
    directives[0] = RenderDirective(
        template_id=directives[0].template_id,
        degradation_profile=profile,
        parameters=dict(parameters),
    )
    degraded = emit(replace(plan, page_directives=tuple(directives)), directory, "degraded")
    return read_document(degraded)


def assert_searchable_scan(control_pages, degraded_pages_, profile: str, parameters) -> None:
    """VR-050's three conditions for `SCAN_DEGRADATION`, in one place."""
    where = f"{profile} {dict(sorted(parameters.items()))}"

    control_page, degraded_page = control_pages[0], degraded_pages_[0]
    # 1. The body raster differs from the control, which carries none at all.
    assert not control_page.images, "the control was rendered with a raster"
    assert degraded_page.images, f"VR-050: {where} emitted no raster over the body"

    # 2. The extracted text layer is identical to the control's.
    assert page_text(degraded_page) == page_text(control_page), (
        f"VR-050: {where} changed the retained text layer; a degraded page must extract exactly "
        "what its undegraded control extracts, without recognition"
    )

    # 3. The citation anchor lies outside every raster rectangle.
    anchor = degraded_page.lines[0]
    covered = [
        word.text
        for word in anchor.words
        for image in degraded_page.images
        if word.rect.intersects(image)
    ]
    assert not covered, f"VR-050: {where} covered citation-anchor word(s) {covered}"
    # Stronger than the word-level test above and independent of where the
    # anchor's glyphs happen to fall: no raster reaches into the header band at
    # all, so the guarantee is a property of the geometry rather than of this
    # document's line lengths. pdfplumber measures from the page top, so the
    # band's lower edge is `height - BODY_RECT[3]`.
    band_floor = degraded_page.height - BODY_RECT[3]
    assert all(image.top >= band_floor for image in degraded_page.images), (
        f"VR-050: {where} drew a raster into the citation anchor's band"
    )
    assert HEADER_BAND[1] == BODY_RECT[3], "the band and the body rectangle no longer meet"


@settings(max_examples=12, deadline=None)
@given(case=profiles_and_parameters())
def test_vr_050_scan_degradation_over_the_declared_parameter_domain(
    degradation_subject, case: tuple[str, dict[str, int]]
) -> None:
    """The property, sampled across every profile and every range.

    Twelve examples rather than the entry's 200: each one renders a two-page PDF
    with a rasterized body and re-extracts it, and the corner cases below
    enumerate the ends the sampler is not obliged to reach.
    """
    plan, directory, _, control_pages = degradation_subject
    profile, parameters = case
    assert_searchable_scan(
        control_pages, degraded_pages(plan, directory, profile, parameters), profile, parameters
    )


@pytest.mark.parametrize(("profile", "parameters"), corner_cases())
def test_vr_050_scan_degradation_at_both_ends_of_every_range(
    degradation_subject, profile: str, parameters: dict[str, int]
) -> None:
    plan, directory, _, control_pages = degradation_subject
    assert_searchable_scan(
        control_pages, degraded_pages(plan, directory, profile, parameters), profile, parameters
    )


@pytest.mark.parametrize(("profile", "parameters"), corner_cases())
def test_vr_050_the_degraded_raster_differs_from_the_clean_one(
    degradation_subject, profile: str, parameters: dict[str, int]
) -> None:
    """The substantive half of "the body raster differs from the control".

    The control carrying no raster at all makes the two trivially different; this
    compares the degraded raster against the **clean raster of the same body**,
    which is the comparison a degradation that did nothing would fail.
    """
    plan, _, _, _ = degradation_subject
    document = build_document(plan)
    render = PageRender(
        canvas=None,
        template=template(document.model.pages[0].directive.template_id),
        lines=document.layout.pages[0],
        profile=profile,
        parameters=parameters,
        body_rect=BODY_RECT,
        page_number=1,
        page_count=len(document.model.pages),
        anchor=document.layout.pages[0][0].text,
    )
    clean = body_raster(render)
    assert clean.mode == RASTER_MODE
    dirty = degraded_raster(clean, profile, parameters)
    assert dirty.size == clean.size
    assert dirty.tobytes() != clean.tobytes(), (
        f"VR-050: {profile} {parameters} left every pixel unchanged, so the page is recorded as "
        "degraded while carrying a clean render"
    )


@pytest.mark.parametrize(("profile", "parameters"), corner_cases())
def test_vr_050_the_raster_is_deterministic_for_one_parameter_set(
    degradation_subject, profile: str, parameters: dict[str, int]
) -> None:
    """The speckle stream is seeded from the recorded directive and nothing else.

    Without this the pixels would depend on the run, and FR-021's byte-identity
    claim would be false for a reason no reader could see.
    """
    plan, _, _, _ = degradation_subject
    document = build_document(plan)
    render = PageRender(
        canvas=None,
        template=template(document.model.pages[0].directive.template_id),
        lines=document.layout.pages[0],
        profile=profile,
        parameters=parameters,
        body_rect=BODY_RECT,
        page_number=1,
        page_count=len(document.model.pages),
        anchor=document.layout.pages[0][0].text,
    )
    clean = body_raster(render)
    first = degraded_raster(clean, profile, parameters).tobytes()
    second = degraded_raster(body_raster(render), profile, parameters).tobytes()
    assert first == second


def test_vr_050_two_parameter_sets_produce_two_rasters(degradation_subject) -> None:
    """The failing direction of the determinism case above: identical output for
    *different* parameters would satisfy it while making the declared domain
    meaningless."""
    plan, _, _, _ = degradation_subject
    document = build_document(plan)
    profile = sorted(CONFIG.degradation_profiles)[0]
    bounds = CONFIG.degradation_profiles[profile]
    render = PageRender(
        canvas=None,
        template=template(document.model.pages[0].directive.template_id),
        lines=document.layout.pages[0],
        profile=profile,
        parameters={},
        body_rect=BODY_RECT,
        page_number=1,
        page_count=len(document.model.pages),
        anchor=document.layout.pages[0][0].text,
    )
    clean = body_raster(render)
    low = {name: int(bounds[name]["min"]) for name in bounds}
    high = {name: int(bounds[name]["max"]) for name in bounds}
    assert degraded_raster(clean, profile, low).tobytes() != (
        degraded_raster(clean, profile, high).tobytes()
    )


# --------------------------------------------------------------------------
# The injector's own refusals
# --------------------------------------------------------------------------


def test_a_degradation_parameter_may_not_be_defaulted() -> None:
    """Every declared parameter is inside `document_model_hash` (DM-2), so a
    defaulted value would be a degradation decision the reproducibility
    comparison never sees."""
    from PIL import Image

    profile = sorted(CONFIG.degradation_profiles)[0]
    with pytest.raises(DegradeError):
        degraded_raster(Image.new(RASTER_MODE, (8, 8), 255), profile, {})
    assert set(PARAMETER_NAMES) == set(CONFIG.degradation_profiles[profile])


def test_the_reserved_undegraded_name_never_reaches_the_degrader() -> None:
    from PIL import Image

    with pytest.raises(DegradeError):
        degraded_raster(Image.new(RASTER_MODE, (8, 8), 255), UNDEGRADED_PROFILE, {})


def test_a_body_rectangle_reaching_the_header_band_is_refused(planned) -> None:
    """FR-032 as a refusal rather than a convention: a raster that could cover
    the citation anchor is never drawn, whatever a caller passes."""
    before, _ = planned
    document = build_document(before[sorted(before)[0]])
    render = PageRender(
        canvas=None,
        template=template(document.model.pages[0].directive.template_id),
        lines=document.layout.pages[0],
        profile=sorted(CONFIG.degradation_profiles)[0],
        parameters={name: 1 for name in PARAMETER_NAMES},
        body_rect=(BODY_RECT[0], BODY_RECT[1], BODY_RECT[2], HEADER_BAND[3]),
        page_number=1,
        page_count=2,
        anchor="x",
    )
    with pytest.raises(DegradeError, match="citation anchor"):
        degrade_page(render)


def test_a_page_naming_a_profile_with_no_degrader_refuses_to_render(
    planned, tmp_path: Path
) -> None:
    """`render.py`'s own guard, exercised from here because it is what makes
    "recorded as degraded" and "rendered degraded" the same thing: a silently
    clean page would satisfy every text rule while carrying none of the property
    `SCAN_DEGRADATION` records."""
    from model.corpus.render import RenderError

    _, after = planned
    plan = carrying(after, "SCAN_DEGRADATION")
    document = build_document(plan)
    with pytest.raises(RenderError, match="degradation profile"):
        render_document(document.layout, tmp_path / "clean.pdf", degrader=None)


def test_the_injector_refuses_a_layer_that_would_not_carry_all_five(planned) -> None:
    """FR-030 is asserted by the injector over the layer it produced, before
    anything is written; a layer missing a class leaves that class's rules
    quantifying over nothing."""
    _, after = planned
    with pytest.raises(IrregularityError, match="FR-030"):
        inject(tuple(after.values())[:1], config=CONFIG)


def test_the_rotation_leaves_a_stated_share_of_the_layer_clean(planned) -> None:
    """SC-019 is a floor, not a target: a layer in which every document is
    irregular is as unrepresentative as one in which none is."""
    _, after = planned
    plans = tuple(after.values())
    classed = sum(1 for plan in plans if plan.irregularity_classes)
    assert classed >= CLASSED_DOCUMENT_FLOOR * len(plans)
    assert classed < len(plans), "the rotation left no clean document"
    assert () in CLASS_ROTATION


def test_injection_is_deterministic_from_the_committed_seed(planned) -> None:
    """Two injections of one planned layer agree in every field the model
    hashes, so an injected change is inside `document_model_hash` by
    construction rather than by chance."""
    before, after = planned
    again = {plan.document_id: plan for plan in inject(tuple(before.values()), config=CONFIG)}
    for document_id, plan in after.items():
        twin = again[document_id]
        assert plan.irregularity_classes == twin.irregularity_classes
        assert plan.blank_fields == twin.blank_fields
        assert dict(plan.field_labels) == dict(twin.field_labels)
        assert plan.split_field == twin.split_field
        assert plan.dates == twin.dates
        assert plan.page_directives == twin.page_directives


def test_the_assignment_does_not_depend_on_the_order_plans_were_supplied(planned) -> None:
    """The assignment is made over the layer sorted by identifier, so which
    document carries which class is a function of the identifiers and the seed —
    not of the order the planner happened to emit them in."""
    before, after = planned
    shuffled: Sequence[DocumentPlan] = tuple(reversed(tuple(before.values())))
    again = {plan.document_id: plan for plan in inject(shuffled, config=CONFIG)}
    for document_id, plan in after.items():
        assert plan.irregularity_classes == again[document_id].irregularity_classes
