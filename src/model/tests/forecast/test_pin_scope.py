"""T105 — DV-019 / SC-030 / FR-032: the digest claim degrades and never fails.

FR-032 lets this epic publish a draw-digest equality claim **in addition** to
FR-022's day-tolerance reproduction, and this epic does publish one. Publishing
it brings the obligation SC-030 states: where the observed environment differs
from the recorded pin, a digest mismatch is reported as a **scope limit rather
than a failure** — the treatment E005 established for the same problem.

**The recorded pin is measured not to determine the stored digest, so every
mismatch is a scope limit.** An earlier revision of this file asserted that the
tier's shared reproduction publishes `equal`, and that a mismatch *inside* the
pin is a failure. Both were wrong in the same way. On Linux the same re-fit, in
the same pytest session, at the same seed and shape and with all six recorded
keys equal, moved every one of 68 lines' digests — while the realized median
drift was 0.12 days against the published 5.0-day tolerance. The claim asserting
`equal` failed on a run FR-022's actual gate passed with three orders of
magnitude to spare. **Why the digests move is unestablished** (G-21): sampler
determinism at a fixed seed, model-construction determinism across a rebuild and
float64 fidelity through the storage round-trip were each measured on that
platform and each held bitwise. This file asserts the observation and no cause.

Three things are asserted here and the third is what makes the first two mean
anything.

* The claim as actually published on the tier's shared reproduction: whatever
  the digests do, the claim is one of its two dispositions, its reason names the
  in-pin reading, and **the run still exits zero**. That last clause is the
  behavioural one — a digest disposition that could move the exit status is the
  defect this file now exists to catch.
* The pin the claim is published against is `forecast_run.library_versions`, the
  **whole recorded set** rather than a chosen subset of it, so no alternative
  pairing of operands satisfies the criterion. Asserted key by key: moving any
  single key changes the reported *reason* from the in-pin one to the out-of-pin
  one, which an implementation comparing only `pymc` — or only the three
  PyMC-stack keys — fails on the key it left out.
* The two reasons are contrasted on one and the same digest mismatch, so neither
  is unconditional. That contrast is what the removed `failed` disposition used
  to supply, and it is supplied here without asserting a failure that a correct
  implementation does not produce.

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
    DIGEST_CLAIM_SCOPE_LIMIT,
    DIGEST_SCOPE_PIN_DIFFERS,
    DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS,
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


def planted_line(artifacts: dict[str, dict[uuid.UUID, StoredArtifact]]) -> uuid.UUID:
    """Which line `artifacts_with_one_moved_digest` moves, named on its own.

    Separately callable because a caller that has to assert *which* line the
    planting reached would otherwise re-derive the choice, and two derivations
    of one choice are two chances to disagree about what was planted.
    """
    return sorted(artifacts[LINE_POSTERIOR_STORE], key=str)[0]


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
    po_line_id = planted_line(artifacts)
    original = moved[store][po_line_id]
    moved[store][po_line_id] = StoredArtifact(
        po_line_id=po_line_id,
        draws=np.asarray(original.draws, dtype=float),
        draw_digest=FOREIGN_DIGEST,
    )
    return moved


def test_the_published_claim_in_an_environment_that_matches_the_pin_never_fails(
    reproduced_run: ReproducedRun,
) -> None:
    """FR-032's claim, as published, on a re-fit that ran inside the recorded pin.

    **Not asserted as `equal`**, because the pin does not determine bitwise
    numerics and a correct implementation reproduces the digests on one platform
    and not on another. What is asserted is the pair of properties that hold on
    both: the claim resolves to one of its two dispositions with the in-pin
    reason when it degrades, and the **exit status is zero either way**.

    That still discriminates. A digest claim wired back into the exit status
    fails the status assertion. A claim resolving to a disposition this module
    does not export fails the membership assertion. A claim reporting the
    out-of-pin reason on an environment whose pin is identical fails the reason
    assertion — and that is the one an implementation reading no pin at all
    would trip, since with the differing-key list empty there is nothing for the
    out-of-pin reason to name.
    """
    outcome = reproduced_run.reproduction.outcome
    claim = outcome.claim

    assert claim.verdict in (DIGEST_CLAIM_EQUAL, DIGEST_CLAIM_SCOPE_LIMIT)
    assert claim.differing_pin_keys == ()
    assert claim.scope_reason in (None, DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS)
    assert (claim.scope_reason is None) == (claim.verdict == DIGEST_CLAIM_EQUAL)
    assert (claim.differing_lines == ()) == (claim.verdict == DIGEST_CLAIM_EQUAL)
    assert outcome.exit_status == 0, (
        f"the reproduction exited non-zero on a run whose own verdict is "
        f"{outcome.verdict!r} and whose digest claim is {claim.verdict!r}; FR-022's three "
        f"outcomes govern the status and the optional digest claim never reaches it"
    )


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


def test_a_mismatch_inside_the_recorded_pin_is_a_scope_limit_naming_the_other_reason(
    reproduced_run: ReproducedRun,
) -> None:
    """The other reading, so the out-of-pin one below is not unconditional.

    Same digests, same pins, one line moved. There is no version difference to
    name, and the claim degrades anyway — because the thing that moved is not
    something `library_versions` has a field for. The reason is asserted, not
    just the disposition: a claim that reported the out-of-pin reason here would
    be naming a version difference that does not exist, and one that reported no
    reason at all would leave a reader unable to tell the two cases apart.

    Both operands are the **recorded** run's artifacts, so the single moved
    digest is the only difference between them on every platform — which is what
    lets the differing-line count be asserted exactly here and not in the
    controls file, where one side is a re-fit.
    """
    outcome = reproduced_run.reproduction.outcome
    recorded = reproduced_run.reproduction.recorded
    pin = outcome.claim.recorded_pin

    claim = digest_claim(
        recorded.artifacts, artifacts_with_one_moved_digest(recorded.artifacts), pin, pin
    )

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.scope_reason == DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS
    assert len(claim.differing_lines) == 1
    assert claim.differing_pin_keys == ()


@pytest.mark.parametrize("key", LIBRARY_VERSION_KEYS)
def test_moving_any_single_key_of_the_pin_moves_the_reported_reason(
    reproduced_run: ReproducedRun, key: str
) -> None:
    """DV-019, asserted over **every** key rather than over a chosen one.

    One test per key, so a failure names the version an implementation stopped
    reading rather than reporting that "the pin" is wrong. This is the assertion
    that makes "the whole recorded set" a property of the code: a claim comparing
    only the PyMC-stack keys passes five of these six and fails on `blas`, which
    is the one that moves floating results without moving a distribution version.

    What moves with the key is the **reason**, not the disposition — the
    disposition is a scope limit on both sides of the contrast now. The reason is
    the discriminating observable: an implementation that stopped reading this
    key reports `DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS` here, and an
    implementation that read no pin at all reports it on every one of the six.
    """
    recorded = reproduced_run.reproduction.recorded
    pin = dict(reproduced_run.reproduction.outcome.claim.recorded_pin)
    observed = {**pin, key: f"{pin[key]}{VERSION_SUFFIX}"}

    claim = digest_claim(
        recorded.artifacts, artifacts_with_one_moved_digest(recorded.artifacts), pin, observed
    )

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.scope_reason == DIGEST_SCOPE_PIN_DIFFERS
    assert claim.differing_pin_keys == (key,)
    assert len(claim.differing_lines) == 1


def test_no_disposition_of_the_claim_is_reported_as_a_failure(
    reproduced_run: ReproducedRun,
) -> None:
    """ "Rather than a failure" — as an exit status, not as a word in a report.

    The outcome the job returns is substituted with each claim in turn and its
    status is read back. **All three**, not just the out-of-pin one: the test
    above observes whichever disposition this platform happens to produce, so on
    a machine where the digests reproduce it evidences only the `equal` branch.
    Substituting covers the branches this platform did not take.

    FR-032 requires the claim to *degrade*, and a degradation that still exited
    non-zero would be a failure wearing another name. That is the regression this
    catches: an `exit_status` reading `self.claim.verdict` again fails on the
    second and third substitutions and passes the first, which is exactly the
    shape the defect had.
    """
    outcome = reproduced_run.reproduction.outcome
    recorded = reproduced_run.reproduction.recorded
    pin = dict(outcome.claim.recorded_pin)
    moved = artifacts_with_one_moved_digest(recorded.artifacts)
    outside = {**pin, LIBRARY_VERSION_KEYS[0]: f"{pin[LIBRARY_VERSION_KEYS[0]]}{VERSION_SUFFIX}"}
    claims = (
        digest_claim(recorded.artifacts, recorded.artifacts, pin, pin),
        digest_claim(recorded.artifacts, moved, pin, pin),
        digest_claim(recorded.artifacts, moved, pin, outside),
    )

    assert [claim.scope_reason for claim in claims] == [
        None,
        DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS,
        DIGEST_SCOPE_PIN_DIFFERS,
    ]
    for claim in claims:
        substituted = dataclasses.replace(outcome, claim=claim)

        assert substituted.exit_status == 0, (
            f"a {claim.verdict!r} digest claim ({claim.scope_reason}) exited "
            f"{substituted.exit_status}; FR-022's three outcomes govern the status and this "
            f"claim has no failing disposition"
        )
        assert substituted.verdict == outcome.verdict, (
            "the substituted digest claim moved FR-022's reproduction verdict; the day "
            "tolerance and the optional digest claim are separate claims, and the "
            "reproduction verdict is never bitwise equality of draws"
        )


def test_the_claim_is_published_in_the_emitted_report_with_the_pin_beside_it(
    reproduced_run: ReproducedRun,
) -> None:
    """FR-032's "where it does" — the claim has to be somewhere a reader finds it.

    A claim computed and not published is not published, and SC-030's obligation
    attaches to a *published* claim. Both pins are rendered beside the verdict,
    because a scope limit a reader cannot check the operands of is an excuse
    rather than a disclosure.

    The disposition is read out of **section 5 alone** rather than out of the
    whole file, and the disposition the section states is required to be the one
    the outcome resolved to. Searching the whole document for the word `equal`
    was the earlier form and it asserted nothing: `Provenance field equality`
    and "exactly equal" both satisfy it on a report whose digest claim degraded.
    """
    body = reproduced_run.reproduction.report.read_text(encoding="utf-8")
    claim = reproduced_run.reproduction.outcome.claim
    section = body.split("## 5. Draw-Digest Claim", 1)[-1].split("\n## ", 1)[0]
    stated = "**equal**" if claim.verdict == DIGEST_CLAIM_EQUAL else "scope limit, not a failure"

    assert "## 5. Draw-Digest Claim" in body
    assert "- **Recorded library pin**:" in section
    assert "- **Observed library pin**:" in section
    assert stated in section, (
        f"the report's digest-claim section does not state {stated!r}, while the outcome the "
        f"job exited on resolved to {claim.verdict!r}; the verdict a job exits on and the "
        f"verdict its report states have to be the same one"
    )
    for key, version in claim.recorded_pin.items():
        assert key in section
        assert version in section


def test_a_store_the_re_fit_did_not_produce_at_all_is_a_differing_line(
    reproduced_run: ReproducedRun,
) -> None:
    """The claim cannot be satisfied by a re-fit that produced nothing to compare.

    An absent counterpart is a difference rather than a skip: without this, a
    re-fit that emitted an empty `held_out_prediction` would report every digest
    it did produce as equal and publish agreement over half the population.

    Both operands are the recorded run's own artifacts with one store emptied, so
    the differing-line count is exactly that store's population on every
    platform — an equality rather than a containment, which is what excludes an
    implementation that noticed the absence on one line and stopped.
    """
    recorded = reproduced_run.reproduction.recorded
    pin = dict(reproduced_run.reproduction.outcome.claim.recorded_pin)
    truncated = {
        LINE_POSTERIOR_STORE: recorded.artifacts[LINE_POSTERIOR_STORE],
        HELD_OUT_STORE: {},
    }

    claim = digest_claim(recorded.artifacts, truncated, pin, pin)

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.scope_reason == DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS
    assert len(claim.differing_lines) == len(recorded.artifacts[HELD_OUT_STORE])
    assert set(claim.differing_lines) == set(recorded.artifacts[HELD_OUT_STORE])
