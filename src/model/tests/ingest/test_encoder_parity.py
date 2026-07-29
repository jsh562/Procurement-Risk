"""FR-019 / SC-058: the export scores inside bounds declared before it ran.

ADR-0018 accepts the ONNX export **only** against a parity tolerance in three
parts, and each part is a separate assertion below rather than one combined
check:

1. **declared before the comparison** — the bounds live in `data/encoder/probes.json`
   beside the probe texts, committed with them, and this file reads them from
   there instead of restating them. A test carrying its own copy of the number
   can be edited to match an observation, which is the failure mode the
   "declared before" clause exists to prevent;
2. **measured over a committed probe set spanning both corpus layers** — the
   probe set's layer coverage is asserted, so a probe set silently reduced to
   one layer fails rather than passing over a narrower population;
3. **published with the observed maxima beside them** — the measurement is
   printed, and a breach fails.

The bounds are **cosine ≥ 0.999999 for every probe** and **maximum absolute
per-dimension difference ≤ 1e-5**. Getting the attention mask wrong — pooling
over padding — produces plausible vectors that are quietly wrong (HINT-005), so
this file is the only thing standing between that defect and every published
retrieval figure.

**An observation landing near a bound is a finding, not a licence to widen the
bound** (Principle VII, FR-019). The near-bound check below fails with that
instruction rather than passing quietly, because the tempting repair — moving
1e-5 to 1e-4 — is exactly the one the requirement forbids.

The oracle is a **derived** one: reference vectors produced once by
`sentence-transformers` and committed, so the comparison runs offline. It proves
agreement with the reference, which is precisely the claim ADR-0018 needs, and
not that the reference is itself correct.
"""

from __future__ import annotations

import json

import pytest

from model.ingest.artifacts import artifact_path, verified_encoder
from model.ingest.embed import ParityMeasurement, parity_against_reference
from model.ingest.report import encoder_parity_section

#: How close to a bound counts as "near" it. An order of magnitude: an observed
#: maximum inside a tenth of the declared tolerance is comfortable, and anything
#: above that is a fact about the export worth publishing.
NEAR_BOUND_FACTOR = 0.1

#: The run identifier item 21's figures are labelled under. Any identifier will
#: do — this file measures the export, not a run — but FR-072 admits no figure
#: without one, so the section cannot be built with a blank.
RUN_ID = "00000000-0000-4000-8000-00000000e006"


@pytest.fixture(scope="module")
def measurement() -> ParityMeasurement:
    return parity_against_reference()


def test_the_bounds_are_declared_in_the_committed_probe_set() -> None:
    """Part 1: the numbers come from the artifact, not from this file."""
    declared = json.loads(artifact_path("probes.json").read_text("utf-8"))["declared_bounds"]
    assert declared["cosine_similarity_min"] == 0.999999
    assert declared["max_absolute_per_dimension_difference"] == 1e-05


def test_the_probe_set_spans_both_layers(measurement: ParityMeasurement) -> None:
    """Part 2: measured over both corpus layers, not over whichever is handy."""
    assert measurement.layers == {"REAL", "SYNTHETIC"}
    assert len(measurement.per_probe) >= 20


def test_every_probe_meets_the_declared_cosine_bound(measurement: ParityMeasurement) -> None:
    """Cosine ≥ 0.999999 **for every probe**, not on average.

    Stated per probe deliberately: a mean over 21 probes hides a single vector
    that is wrong, and one wrong corpus vector is one document that never
    retrieves.
    """
    breaches = [
        (probe_id, cosine)
        for probe_id, _, cosine, _ in measurement.per_probe
        if cosine < measurement.declared_cosine_minimum
    ]
    assert not breaches, f"cosine below {measurement.declared_cosine_minimum}: {breaches}"


def test_every_probe_meets_the_declared_difference_bound(
    measurement: ParityMeasurement,
) -> None:
    """Maximum absolute per-dimension difference ≤ 1e-5, per probe."""
    breaches = [
        (probe_id, difference)
        for probe_id, _, _, difference in measurement.per_probe
        if difference > measurement.declared_max_absolute_difference
    ]
    assert not breaches, (
        f"per-dimension difference above {measurement.declared_max_absolute_difference}: {breaches}"
    )


def test_the_observed_maxima_are_published(measurement: ParityMeasurement, capsys) -> None:
    """Part 3: the observed extremes are emitted beside the declared bounds."""
    with capsys.disabled():
        # ASCII only: this line is read off a console whose encoding is the
        # runner's, and a published figure that raises on a Windows code page is
        # a figure nobody reads.
        print(
            "\nFR-019 encoder parity - declared: cosine >= "
            f"{measurement.declared_cosine_minimum}, max abs diff <= "
            f"{measurement.declared_max_absolute_difference}; observed over "
            f"{len(measurement.per_probe)} probes across {sorted(measurement.layers)}: "
            f"minimum cosine {measurement.observed_minimum_cosine:.9f}, "
            f"maximum abs diff {measurement.observed_maximum_absolute_difference:.3e}; "
            f"reference {measurement.reference.get('library')} "
            f"{measurement.reference.get('sentence_transformers')} on "
            f"torch {measurement.reference.get('torch')}"
        )
    assert measurement.within_bounds


def test_no_observation_sits_near_a_bound(measurement: ParityMeasurement) -> None:
    """Principle VII: a near-bound landing is published, never absorbed.

    If this fails, the repair is **not** to widen the bound. It is to publish
    the observation as a finding about the export and decide whether the export
    is acceptable at that margin.
    """
    slack = 1.0 - measurement.declared_cosine_minimum
    assert 1.0 - measurement.observed_minimum_cosine <= slack * (1 - NEAR_BOUND_FACTOR), (
        "the observed minimum cosine sits near the declared bound; publish it as a "
        "finding about the export rather than widening the bound"
    )
    assert (
        measurement.observed_maximum_absolute_difference
        <= measurement.declared_max_absolute_difference * (1 - NEAR_BOUND_FACTOR)
    ), (
        "the observed maximum per-dimension difference sits near the declared bound; "
        "publish it as a finding about the export rather than widening the bound"
    )


def test_the_real_measurement_renders_as_report_item_21(
    measurement: ParityMeasurement,
) -> None:
    """T096. Part 3 again, from the other side: what the report publishes.

    The section is built from **this** measurement rather than from a fixture,
    so the bounds it prints are the bounds these assertions enforced and the
    maxima it prints are the ones they observed. `encoder_parity_section` re-reads
    `declared_bounds` from `probes.json` and refuses a measurement taken against
    a different pair, which is what makes "declared before the comparison"
    checkable across the two files instead of within each of them.
    """
    section = encoder_parity_section(run_id=RUN_ID, measurement=measurement)
    assert section.item == 21
    published = {figure.label: figure.value for figure in section.figures}
    assert published["Declared bound — minimum cosine similarity"] == (
        measurement.declared_cosine_minimum
    )
    assert published["Observed minimum cosine similarity"] == measurement.observed_minimum_cosine
    assert published["Probes compared"] == len(measurement.per_probe)
    assert section.total_checks[0].outcome == "held"


def test_the_artifact_identity_is_the_one_recorded_on_every_chunk() -> None:
    """FR-020: the identity a chunk carries is the artifact that produced it."""
    artifact = verified_encoder()
    assert artifact.model_id == "sentence-transformers/all-MiniLM-L6-v2"
    assert artifact.revision == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    assert artifact.precision == "FP32"
    assert artifact.vector_dimension == 384
