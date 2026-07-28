"""T106 — NC-9: outside the pin reports a scope limit; inside it, the same mismatch fails.

NC-9 counts **two** plantings and they are the same digest mismatch under two
environments, because a degradation that fired unconditionally would be
indistinguishable from a check that never ran. Both are driven through
`compare_reproduction` rather than through `digest_claim` alone — the wiring that
reads the recorded pin off the run row and the observed one off the re-fit is
part of what SC-030 constrains, and `test_pin_scope.py` exercises the function
underneath it.

The injection is a library version appended to on the **re-fit's** side, which is
what "an observed environment differing from the recorded pin" is: the run row
holds the pin as it was, and the environment answering now is different.

**One consequence is stated rather than hidden.** A moved library version also
moves FR-043's provenance identity, which FR-022 requires to be *exactly* equal —
so the run's own verdict disagrees on that ground while the digest claim
degrades. The two are separate claims about separate things, and this file
asserts each on its own operand rather than letting one stand in for the other.
"""

from __future__ import annotations

import dataclasses

import pytest

from forecast.conftest import ReproducedRun
from forecast.test_pin_scope import VERSION_SUFFIX, artifacts_with_one_moved_digest
from model.forecast.manifest import LIBRARY_VERSION_KEYS
from model.forecast.reproduce import (
    DIGEST_CLAIM_EQUAL,
    DIGEST_CLAIM_FAILED,
    DIGEST_CLAIM_SCOPE_LIMIT,
    OUTCOME_AGREES,
    OUTCOME_DISAGREES,
    Refit,
    ReproductionOutcome,
    compare_reproduction,
    render_reproduction_report,
)

#: The key the injection moves. `blas` on purpose: it is the member of the pin
#: that moves floating results without moving any distribution version, so an
#: implementation reading only the PyMC-stack versions reports the environments
#: as identical and turns this scope limit into a failure.
INJECTED_KEY = "blas"


def refit_with_moved_digests(refit: Refit) -> Refit:
    """The re-fit with one line's draw digest moved and nothing else touched.

    The draws are left alone deliberately, so the day-tolerance comparison still
    agrees and the only thing in dispute is the optional digest claim. That is
    what lets the exit status below be attributed to the claim rather than to a
    reproduction that had also drifted.
    """
    return dataclasses.replace(refit, artifacts=artifacts_with_one_moved_digest(refit.artifacts))


def refit_outside_the_pin(refit: Refit) -> Refit:
    """The same re-fit, reporting an environment the recorded pin does not name."""
    moved = refit_with_moved_digests(refit)
    versions = dict(moved.provenance.library_versions)
    versions[INJECTED_KEY] = f"{versions[INJECTED_KEY]}{VERSION_SUFFIX}"
    return dataclasses.replace(
        moved, provenance=dataclasses.replace(moved.provenance, library_versions=versions)
    )


@pytest.fixture
def inside_the_pin(reproduced_run: ReproducedRun) -> ReproductionOutcome:
    """NC-9's second planting: the mismatch with the environment left alone."""
    return compare_reproduction(
        reproduced_run.reproduction.recorded,
        refit_with_moved_digests(reproduced_run.reproduction.refit),
    )


@pytest.fixture
def outside_the_pin(reproduced_run: ReproducedRun) -> ReproductionOutcome:
    """NC-9's first planting: the same mismatch under an injected version."""
    return compare_reproduction(
        reproduced_run.reproduction.recorded,
        refit_outside_the_pin(reproduced_run.reproduction.refit),
    )


def test_the_injection_is_a_real_difference_from_the_recorded_pin(
    outside_the_pin: ReproductionOutcome,
    reproduced_run: ReproducedRun,
) -> None:
    """The planting is what it claims to be, before anything is concluded from it.

    Without this the scope limit below could be produced by a comparison that
    read no pin at all, and the control would be evidence about nothing.
    """
    claim = outside_the_pin.claim

    assert INJECTED_KEY in LIBRARY_VERSION_KEYS
    assert claim.recorded_pin == reproduced_run.reproduction.outcome.claim.recorded_pin
    assert claim.differing_pin_keys == (INJECTED_KEY,)
    assert claim.observed_pin[INJECTED_KEY].endswith(VERSION_SUFFIX)


def test_a_mismatch_outside_the_pin_is_a_scope_limit_and_not_a_failure(
    outside_the_pin: ReproductionOutcome,
) -> None:
    """SC-030's disposition, over the whole wiring rather than over one function."""
    claim = outside_the_pin.claim

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.verdict != DIGEST_CLAIM_FAILED
    assert len(claim.differing_lines) == 1


def test_the_same_mismatch_inside_the_pin_fails(
    inside_the_pin: ReproductionOutcome,
) -> None:
    """NC-9's second direction, and the whole reason the first is not vacuous.

    Identical digests in dispute, identical lines, identical draws — only the
    environment differs between this and the test above, and the verdict moves
    with it. The day-tolerance verdict is asserted to still **agree**, so the
    non-zero status is attributable to the digest claim alone.
    """
    claim = inside_the_pin.claim

    assert claim.differing_pin_keys == ()
    assert claim.verdict == DIGEST_CLAIM_FAILED
    assert inside_the_pin.verdict == OUTCOME_AGREES
    assert inside_the_pin.exit_status != 0, (
        "a draw-digest mismatch in the recorded environment exited zero; there is no "
        "version difference to scope the claim out of, so it is a failure and SC-030's "
        "'rather than a failure' has nothing to contrast with"
    )


def test_the_untouched_pairing_is_the_control_for_both(
    reproduced_run: ReproducedRun,
) -> None:
    """Neither planting fires on the real reproduction.

    The same recorded run against the same re-fit, with nothing moved: the claim
    is equal, the run agrees, and the status is zero — so both plantings above
    changed the outcome rather than describing it.
    """
    outcome = reproduced_run.reproduction.outcome

    assert outcome.claim.verdict == DIGEST_CLAIM_EQUAL
    assert outcome.verdict == OUTCOME_AGREES
    assert outcome.exit_status == 0


def test_the_moved_version_also_breaks_the_provenance_equality_and_says_so(
    outside_the_pin: ReproductionOutcome,
) -> None:
    """The consequence, stated rather than hidden.

    FR-043's provenance identity carries `library_versions`, and FR-022 requires
    it exactly equal — so an environment outside the pin is not a reproduction of
    the recorded run whatever its digests do. The digest claim still degrades;
    the run's own verdict is decided on the other ground, and both are reported.
    """
    assert outside_the_pin.differing_provenance_fields == ("library_versions",)
    assert outside_the_pin.verdict == OUTCOME_DISAGREES
    assert outside_the_pin.claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT


def test_the_report_publishes_the_scope_limit_in_those_words(
    outside_the_pin: ReproductionOutcome,
    reproduced_run: ReproducedRun,
) -> None:
    """A scope limit a reader cannot find is not a reported scope limit.

    Principle VII's shape applied to FR-032: the degradation is published with
    the key it degraded on, so a reader can check the operands rather than taking
    the word. Rendered rather than written, because this planting must not leave
    a file beside the tier's real reproduction report.
    """
    body = render_reproduction_report(outside_the_pin, reproduced_run.reproduction.recorded)

    assert "scope limit, not a failure" in body
    assert f"`{INJECTED_KEY}`" in body
    assert "## 5. Draw-Digest Claim" in body
