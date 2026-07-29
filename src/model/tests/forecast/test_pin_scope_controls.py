"""T106 — NC-9: the same digest mismatch under two environments, and neither fails.

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

**What the two directions contrast is the reported reason, not pass against
fail.** An earlier revision of this file asserted that the in-pin planting
*fails*, on the premise that a matching pin means a matching environment. It does
not: `library_versions` records package versions and cannot record the BLAS
thread count or reduction order that decide the low bits of a floating sum, and
on Linux the tier's own re-fit moved every one of 68 lines' digests under an
identical pin while agreeing to 0.12 days against a 5.0-day tolerance. So both
directions degrade, neither has a failing disposition, and what moves between
them is the reason — which is a fact about the run that a reader is owed and that
an implementation reading no pin at all cannot produce. The in-pin direction
carries the zero-status assertion, because it is the one where the status is
attributable to the claim; the out-of-pin direction exits non-zero for the
separate reason recorded below.

**One consequence is stated rather than hidden.** A moved library version also
moves FR-043's provenance identity, which FR-022 requires to be *exactly* equal —
so the run's own verdict disagrees on that ground while the digest claim
degrades. The two are separate claims about separate things, and this file
asserts each on its own operand rather than letting one stand in for the other.

**Line counts are asserted as membership, never as a total.** One side of every
comparison here is a real re-fit, and how many of its digests reproduce is a
property of the platform. What the planting owns is that its own line is among
the differing ones and carries the foreign digest; asserting a total of one would
be asserting bitwise reproduction of the other 67.
"""

from __future__ import annotations

import dataclasses

import pytest

from forecast.conftest import ReproducedRun
from forecast.test_pin_scope import (
    FOREIGN_DIGEST,
    VERSION_SUFFIX,
    artifacts_with_one_moved_digest,
    planted_line,
)
from model.forecast.manifest import LIBRARY_VERSION_KEYS
from model.forecast.reproduce import (
    DIGEST_CLAIM_SCOPE_LIMIT,
    DIGEST_SCOPE_PIN_DIFFERS,
    DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS,
    OUTCOME_AGREES,
    OUTCOME_DISAGREES,
    Refit,
    ReproductionOutcome,
    compare_reproduction,
    render_reproduction_report,
)

#: The key the injection moves. `blas` on purpose: it is the member of the pin
#: that moves floating results without moving any distribution version, so an
#: implementation reading only the PyMC-stack versions reports the two
#: environments as identical and reports the wrong reason for this scope limit.
#: That the pin carries the key at all does **not** make it a numeric
#: determinant — a BLAS build holds its version while varying its thread count
#: and reduction order, which is the gap G-21 records.
INJECTED_KEY = "blas"


def refit_with_moved_digests(refit: Refit) -> Refit:
    """The re-fit with one line's draw digest moved and nothing else touched.

    The draws are left alone deliberately, so the day-tolerance comparison still
    agrees and the only thing in dispute is the optional digest claim. That is
    what lets the in-pin outcome below be read as evidence about the claim: its
    verdict agrees and its status is zero, so neither is arriving from a
    reproduction that had also drifted past the tolerance.
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
    reproduced_run: ReproducedRun,
) -> None:
    """SC-030's disposition, over the whole wiring rather than over one function.

    The planted line is required to be **among** the differing lines rather than
    to be the only one. A total of one would assert that the other 67 reproduced
    bit for bit, which is the claim FR-022 says reproduction is never expressed
    as — and it is the assertion that failed on Linux with `68 == 1`.

    The status is **not** asserted here, and deliberately: the injected version
    also moves FR-043's provenance identity, so this outcome disagrees on that
    ground and exits non-zero for a reason that has nothing to do with the digest
    claim. The test below owns that consequence, and the claim's own inability to
    fail a run is evidenced where the two are separable — on the in-pin planting
    below, and by substitution in `test_pin_scope.py`.
    """
    claim = outside_the_pin.claim
    planted = planted_line(reproduced_run.reproduction.refit.artifacts)

    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.scope_reason == DIGEST_SCOPE_PIN_DIFFERS
    assert planted in claim.differing_lines


def test_the_same_mismatch_inside_the_pin_is_the_other_scope_limit_and_still_exits_zero(
    inside_the_pin: ReproductionOutcome,
    reproduced_run: ReproducedRun,
) -> None:
    """NC-9's second direction, and the whole reason the first is not vacuous.

    Identical digests in dispute, identical lines, identical draws — only the
    environment differs between this and the test above, and the reported
    **reason** moves with it while the disposition does not. That contrast is
    what stops the scope limit being unconditional: an implementation that read
    no pin reports one reason on both and fails one of the two.

    The day-tolerance verdict is asserted to still **agree** and the status to be
    zero. Together those are the corrected semantics: a digest mismatch, however
    it arose, leaves FR-022's gate exactly where it was.
    """
    claim = inside_the_pin.claim
    planted = planted_line(reproduced_run.reproduction.refit.artifacts)

    assert claim.differing_pin_keys == ()
    assert claim.verdict == DIGEST_CLAIM_SCOPE_LIMIT
    assert claim.scope_reason == DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS
    assert planted in claim.differing_lines
    assert inside_the_pin.verdict == OUTCOME_AGREES
    assert inside_the_pin.exit_status == 0, (
        "a draw-digest mismatch inside the recorded pin exited non-zero; the pin records "
        "package versions and does not determine bitwise numerics, so the mismatch is a "
        "reported scope limit and FR-022's day tolerance is the only gate"
    )


def test_the_untouched_pairing_is_the_control_for_both(
    reproduced_run: ReproducedRun,
) -> None:
    """Neither planting fires on the real reproduction.

    The same recorded run against the same re-fit, with nothing moved: the pins
    are identical on every key, the run agrees, and the status is zero — so the
    version injection above changed the outcome rather than describing it.

    The digest half of the planting is controlled by its **value** rather than by
    the claim's disposition, and that is the substantive change here. Whether the
    untouched re-fit reproduces the digests is a property of the platform, so
    `claim.verdict == equal` is not a control anywhere; that `FOREIGN_DIGEST`
    appears nowhere in either run's artifacts is, on every platform.
    """
    outcome = reproduced_run.reproduction.outcome
    recorded = reproduced_run.reproduction.recorded
    refit = reproduced_run.reproduction.refit

    assert outcome.claim.differing_pin_keys == ()
    assert outcome.verdict == OUTCOME_AGREES
    assert outcome.exit_status == 0
    for run in (recorded.artifacts, refit.artifacts):
        digests = {row.draw_digest for rows in run.values() for row in rows.values()}

        assert digests
        assert FOREIGN_DIGEST not in digests


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
