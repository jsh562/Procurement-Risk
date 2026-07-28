"""T105 — DV-019 / SC-030 / FR-032: the digest claim is scoped to the whole pin.

FR-032 lets this epic publish a draw-digest equality claim **in addition** to
FR-022's day-tolerance reproduction, and this epic does publish one. Publishing
it brings the obligation SC-030 states: where the observed environment differs
from the recorded pin, a digest mismatch is reported as a **scope limit rather
than a failure** — the treatment E005 established for the same problem.

Two things are asserted here and the second is what makes the first mean
anything.

* The claim as actually published on the tier's shared reproduction. The re-fit
  runs in the same environment the recorded run was fitted in, so the pins are
  identical on every key and the digests are equal — which is the case in which a
  scope limit must **not** fire, because there is no version difference to scope
  anything out of.
* The pin the claim is scoped to is `forecast_run.library_versions`, the **whole
  recorded set** rather than a chosen subset of it, so no alternative pairing of
  operands satisfies the criterion. Asserted key by key: moving any single key of
  the recorded pin turns the same digest mismatch into a scope limit, which an
  implementation comparing only `pymc` — or only the three PyMC-stack keys — would
  fail on the key it left out.

`test_pin_scope_controls.py` carries NC-9's two planted directions.
"""

from __future__ import annotations

import dataclasses
import uuid

import numpy as np
import pytest

from forecast.conftest import ReproducedRun
from model.forecast.manifest import LIBRARY_VERSION_KEYS
from model.forecast.reproduce import (
    DIGEST_CLAIM_EQUAL,
    DIGEST_CLAIM_FAILED,
    DIGEST_CLAIM_SCOPE_LIMIT,
    HELD_OUT_STORE,
    LINE_POSTERIOR_STORE,
    StoredArtifact,
    digest_claim,
)

#: A digest that covers nothing — 32 bytes, the length both stores'
#: `ck_…__draw_digest_length` require, so what makes it a mismatch is its value
#: rather than its shape.
FOREIGN_DIGEST = bytes(range(32))

#: What a moved library version looks like. Appended rather than replaced, so
#: the injected value is recognisably the recorded one with something done to it
#: and no test depends on which versions this environment happens to resolve.
VERSION_SUFFIX = "+moved-for-dv-019"


def artifacts_with_one_moved_digest(
    artifacts: dict[str, dict[uuid.UUID, StoredArtifact]],
) -> dict[str, dict[uuid.UUID, StoredArtifact]]:
    """A copy of one run's artifacts with a single line's draw digest moved.

    One line and not all of them, because the claim must fire on a single
    disagreeing row: a mismatch that needed the whole population to move would
    pass on the one artifact that actually drifted.
    """
    moved = {store: dict(rows) for store, rows in artifacts.items()}
    store = LINE_POSTERIOR_STORE
    po_line_id = sorted(moved[store], key=str)[0]
    original = moved[store][po_line_id]
    moved[store][po_line_id] = StoredArtifact(
        po_line_id=po_line_id,
        draws=np.asarray(original.draws, dtype=float),
        draw_digest=FOREIGN_DIGEST,
    )
    return moved


def test_the_published_claim_is_equal_in_an_environment_that_matches_the_pin(
    reproduced_run: ReproducedRun,
) -> None:
    """FR-032's claim, as published. The scope limit must not fire here.

    The re-fit ran on the recorded pin, so a digest mismatch would be a genuine
    failure rather than an environment difference — and asserting *equality*
    rather than merely "not a failure" is what shows the claim is measured: a
    harness that never compared digests would report equality too, which is why
    the differing-lines list is asserted empty against a claim that can produce
    one two tests below.
    """
    claim = reproduced_run.reproduction.outcome.claim

    assert claim.verdict == DIGEST_CLAIM_EQUAL
    assert claim.differing_lines == ()
    assert claim.differing_pin_keys == ()
    assert reproduced_run.reproduction.outcome.exit_status == 0


def test_the_recorded_pin_is_the_whole_library_versions_set(
    reproduced_run: ReproducedRun,
) -> None:
    """SC-030's "whole recorded set", checked against what the run actually stores.

    The pin is the six keys `ck_forecast_run__library_versions_shape` requires
    present, read off the run row rather than off this file's idea of them. A
    claim scoped to a subset would still satisfy every equality above.
    """
    claim = reproduced_run.reproduction.outcome.claim

    assert set(claim.recorded_pin) == set(LIBRARY_VERSION_KEYS)
    assert set(claim.observed_pin) == set(LIBRARY_VERSION_KEYS)
    assert claim.recorded_pin == claim.observed_pin


def test_a_mismatch_inside_the_recorded_pin_is_a_failure(
    reproduced_run: ReproducedRun,
) -> None:
    """The other disposition, so the scope limit below is not unconditional.

    Same digests, same pins, one line moved: with no version difference there is
    nothing to scope the claim out of, and the mismatch is what it appears to be.
    """
    outcome = reproduced_run.reproduction.outcome
    recorded = reproduced_run.reproduction.recorded
    pin = outcome.claim.recorded_pin

    claim = digest_claim(
        recorded.artifacts, artifacts_with_one_moved_digest(recorded.artifacts), pin, pin
    )

    assert claim.verdict == DIGEST_CLAIM_FAILED
    assert len(claim.differing_lines) == 1
    assert claim.differing_pin_keys == ()


@pytest.mark.parametrize("key", LIBRARY_VERSION_KEYS)
def test_moving_any_single_key_of_the_pin_degrades_the_same_mismatch_to_a_scope_limit(
    reproduced_run: ReproducedRun, key: str
) -> None:
    """DV-019, asserted over **every** key rather than over a chosen one.

    One test per key, so a failure names the version an implementation stopped
    reading rather than reporting that "the pin" is wrong. This is the assertion
    that makes "the whole recorded set" a property of the code: a claim comparing
    only the PyMC-stack keys passes five of these six and fails on `blas`, which
    is the one that moves floating results without moving a distribution version.
    """
    recorded = reproduced_run.reproduction.recorded
    pin = dict(reproduced_run.reproduction.outcome.claim.recorded_pin)
    observed = {**pin, key: f"{pin[key]}{VERSION_SUFFIX}"}

    claim = digest_claim(
        recorded.artifacts, artifacts_with_one_moved_digest(recorded.artifacts), pin, observed
    )

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.differing_pin_keys == (key,)
    assert len(claim.differing_lines) == 1


def test_a_scope_limit_is_reported_rather_than_failed(
    reproduced_run: ReproducedRun,
) -> None:
    """"Rather than a failure" — as an exit status, not as a word in a report.

    The outcome the job returns is substituted with the degraded claim and its
    status is read back. FR-032 requires the claim to *degrade*, and a degradation
    that still exited non-zero would be a failure wearing another name.
    """
    outcome = reproduced_run.reproduction.outcome
    recorded = reproduced_run.reproduction.recorded
    pin = dict(outcome.claim.recorded_pin)
    observed = {**pin, LIBRARY_VERSION_KEYS[0]: f"{pin[LIBRARY_VERSION_KEYS[0]]}{VERSION_SUFFIX}"}

    degraded = dataclasses.replace(
        outcome,
        claim=digest_claim(
            recorded.artifacts,
            artifacts_with_one_moved_digest(recorded.artifacts),
            pin,
            observed,
        ),
    )

    assert degraded.claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert degraded.exit_status == 0
    assert degraded.verdict == outcome.verdict, (
        "the degraded digest claim moved FR-022's reproduction verdict; the day tolerance "
        "and the optional digest claim are separate claims, and the reproduction verdict is "
        "never bitwise equality of draws"
    )


def test_the_claim_is_published_in_the_emitted_report_with_the_pin_beside_it(
    reproduced_run: ReproducedRun,
) -> None:
    """FR-032's "where it does" — the claim has to be somewhere a reader finds it.

    A claim computed and not published is not published, and SC-030's obligation
    attaches to a *published* claim. Both pins are rendered beside the verdict,
    because a scope limit a reader cannot check the operands of is an excuse
    rather than a disclosure.
    """
    body = reproduced_run.reproduction.report.read_text(encoding="utf-8")
    claim = reproduced_run.reproduction.outcome.claim

    assert "## 5. Draw-Digest Claim" in body
    assert "- **Recorded library pin**:" in body
    assert "- **Observed library pin**:" in body
    assert DIGEST_CLAIM_EQUAL in body
    for key, version in claim.recorded_pin.items():
        assert key in body
        assert version in body


def test_a_store_the_re_fit_did_not_produce_at_all_is_a_differing_line(
    reproduced_run: ReproducedRun,
) -> None:
    """The claim cannot be satisfied by a re-fit that produced nothing to compare.

    An absent counterpart is a difference rather than a skip: without this, a
    re-fit that emitted an empty `held_out_prediction` would report every digest
    it did produce as equal and publish agreement over half the population.
    """
    recorded = reproduced_run.reproduction.recorded
    pin = dict(reproduced_run.reproduction.outcome.claim.recorded_pin)
    truncated = {
        LINE_POSTERIOR_STORE: recorded.artifacts[LINE_POSTERIOR_STORE],
        HELD_OUT_STORE: {},
    }

    claim = digest_claim(recorded.artifacts, truncated, pin, pin)

    assert claim.verdict == DIGEST_CLAIM_FAILED
    assert len(claim.differing_lines) == len(recorded.artifacts[HELD_OUT_STORE])
