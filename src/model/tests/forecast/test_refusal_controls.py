"""T088, T089 — NC-2 / SC-017 and NC-14 / SC-036: the two pre-sampling refusals.

Both refuse **before** the sampler runs, and that is what distinguishes them
from NC-1. FR-017's observable evidence is that *nothing was written* although a
sample was taken; FR-035's and FR-021's is that *nothing was sampled*. A run can
breach one gate while satisfying the other, so no single test can evidence both
— which is why they are separate obligations and separate files.

**NC-2 (T088)**, FR-021: an as-of date past every terminal event leaves no line
open, and the run must refuse rather than write `open_line_count = 0`. The
column carries `ck_forecast_run__open_line_count_positive`, so the empty
forecast set is unrepresentable either way — but a run that reached the write to
discover that would have sampled first, and the point of FR-021 is that the
condition is knowable before it starts. Invoked at the **chain minimum**, so the
refusal is attributable to the anchor rather than to the chain count.

**NC-14 (T089)**, FR-035: below the published minimum, a non-zero exit naming
the precondition and its realized value, with nothing sampled. This is DV-035's
failing direction — the rule asserts `chain_count` on every emitted run, and a
run that refuses emits none, so the two together say that a run at two chains
neither ships nor stores.

"Nothing was sampled" is asserted over what the job reported, over the wall
clock it recorded, and over the emitted report's own Sampling section, because
no store holds a refused attempt for a test to interrogate (G-8).
"""

from __future__ import annotations

import re

from forecast.conftest import (
    ANCHOR_PAST_EVERY_TERMINAL_EVENT,
    BELOW_MINIMUM_CHAINS,
    SNAPSHOT_TABLES,
    RefusedInvocation,
)
from model.forecast.config import CHAINS_MIN
from model.forecast.report import NOTHING_SAMPLED

#: The line `fit.py` prints immediately before it calls the sampler, matched as
#: a whole line. Its absence is what "nothing was sampled" is measured by: the
#: job announces the shape it is about to sample at, so a refusal that reached
#: the sampler says so.
#:
#: Anchored rather than searched as a substring, and that is not fussiness: the
#: refusal message itself contains the words "pre-sampling" and "nothing was
#: sampled", so a bare `"sampling" not in stderr` is false on exactly the
#: refusals this file is about and would fail every one of them.
SAMPLING_ANNOUNCEMENT = re.compile(r"^sampling \d+ chains x \d+ draws", re.MULTILINE)

#: What PyMC itself prints on the way into a fit. Checked alongside the job's own
#: announcement, because the two are independent — one is this repository's, the
#: other the library's, and a refusal that somehow reached NUTS without the job
#: reporting it would still show here.
SAMPLER_BANNER = "Initializing NUTS"


def _only_report(invocation: RefusedInvocation) -> str:
    """The one file the attempt emitted, read as text.

    Exactly one, asserted rather than assumed: FR-037 makes it one file per
    attempt, and a second file under this root would mean the attempt emitted
    something the closed report set does not declare (FR-040).
    """
    emitted = invocation.emitted_reports

    assert len(emitted) == 1, f"the attempt emitted {[path.name for path in emitted]}"
    return emitted[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# NC-2 / SC-017 — an anchor past every terminal event (T088)
# ---------------------------------------------------------------------------


def test_an_anchor_past_every_terminal_event_refuses_rather_than_emitting(
    refused_with_no_open_line: RefusedInvocation,
) -> None:
    """FR-021: no line open at the anchor, so there is nothing to forecast.

    The alternative this refuses is not an error — it is a *published* run whose
    forecast set is empty, which reads to a coordinator as an absence of risk
    rather than as an absence of data. That is the failure the requirement
    names, and it is why the refusal is the correct outcome rather than a
    zero-row run being merely unhelpful.
    """
    stderr = refused_with_no_open_line.completed.stderr

    assert refused_with_no_open_line.completed.returncode != 0
    assert refused_with_no_open_line.completed.stdout == ""
    assert "at least one line open at the as-of date" in stderr
    assert f"0 line(s) open at {ANCHOR_PAST_EVERY_TERMINAL_EVENT.isoformat()}" in stderr, (
        f"the refusal does not state its realized value; FR-017's two-field set for a "
        f"precondition is the precondition and the value that failed it. Standard error "
        f"was:\n{stderr[:600]}"
    )


def test_the_zero_open_line_refusal_precedes_sampling(
    refused_with_no_open_line: RefusedInvocation,
) -> None:
    """The refusal is FR-021's rather than a late discovery inside the writer.

    Stated separately from the message check because the two fail differently: a
    job that sampled and then refused would produce an identical message and an
    identical set of untouched stores, while having spent the fit — and, more to
    the point, would be relying on the write path to catch a condition the
    schema makes unrepresentable only by accident of a `CHECK`.
    """
    stderr = refused_with_no_open_line.completed.stderr

    assert not SAMPLING_ANNOUNCEMENT.search(stderr)
    assert SAMPLER_BANNER not in stderr
    assert NOTHING_SAMPLED in _only_report(refused_with_no_open_line)


def test_the_zero_open_line_refusal_leaves_every_store_as_found(
    refused_with_no_open_line: RefusedInvocation,
) -> None:
    """FR-021 enumerates the same five stores and the same pointer FR-017 does.

    Stated in the requirement rather than inherited by assumption, because
    FR-017 was the only refusal requirement that stated it and an unstated
    enumeration is not a checkable one. This is the check.
    """
    for table in SNAPSHOT_TABLES:
        assert (
            refused_with_no_open_line.after[table] == refused_with_no_open_line.before[table]
        ), f"`{table}` changed across a pre-sampling refusal"
    assert (
        refused_with_no_open_line.after["active_run"]
        == refused_with_no_open_line.before["active_run"]
    )


# ---------------------------------------------------------------------------
# NC-14 / SC-036 — below the published chain minimum (T089)
# ---------------------------------------------------------------------------


def test_below_the_chain_minimum_the_run_refuses_naming_the_precondition(
    refused_below_the_chain_minimum: RefusedInvocation,
) -> None:
    """FR-035: the precondition and its realized value, and no threshold direction.

    A precondition is not a measured metric, so the two-field set is the whole
    obligation — there is no floor or ceiling for a direction to disambiguate.
    The message names a *precondition* rather than a breached diagnostic, which
    is what made the single compound form of this requirement unverifiable.
    """
    stderr = refused_below_the_chain_minimum.completed.stderr

    assert refused_below_the_chain_minimum.completed.returncode != 0
    assert refused_below_the_chain_minimum.completed.stdout == ""
    assert f"at least {CHAINS_MIN} chains" in stderr
    assert f"{BELOW_MINIMUM_CHAINS} chain(s) requested" in stderr, (
        f"the refusal does not state the realized chain count. Standard error "
        f"was:\n{stderr[:600]}"
    )


def test_below_the_chain_minimum_nothing_is_sampled(
    refused_below_the_chain_minimum: RefusedInvocation,
) -> None:
    """SC-036's distinguishing evidence, and the whole of why NC-14 is not NC-1.

    NC-1 refuses **after** sampling: the fit ran, the diagnostics were measured,
    and nothing was written. This refuses **before** it: there is no sampler
    output to inspect at all. The two leave different evidence, and a suite that
    only asserted "refused and wrote nothing" would not tell them apart.
    """
    stderr = refused_below_the_chain_minimum.completed.stderr
    report = _only_report(refused_below_the_chain_minimum)

    assert not SAMPLING_ANNOUNCEMENT.search(stderr)
    assert SAMPLER_BANNER not in stderr
    assert "nothing was sampled and nothing was written" in stderr
    assert NOTHING_SAMPLED in report, (
        "the emitted report does not record that nothing was sampled; FR-037 requires the "
        "report to carry the realized shape *or* that fact, and a reader holding the file "
        "cannot otherwise tell a pre-sampling refusal from a post-sampling one"
    )


def test_below_the_chain_minimum_leaves_every_store_as_found(
    refused_below_the_chain_minimum: RefusedInvocation,
) -> None:
    """SC-036: no row in any store, the active-run pointer unmoved."""
    for table in SNAPSHOT_TABLES:
        assert (
            refused_below_the_chain_minimum.after[table]
            == refused_below_the_chain_minimum.before[table]
        ), f"`{table}` changed across a refusal that never reached the sampler"
    assert (
        refused_below_the_chain_minimum.after["active_run"]
        == refused_below_the_chain_minimum.before["active_run"]
    )


def test_the_two_pre_sampling_refusals_share_one_exit_status_class(
    refused_below_the_chain_minimum: RefusedInvocation,
    refused_with_no_open_line: RefusedInvocation,
    refused_after_sampling: RefusedInvocation,
) -> None:
    """One non-zero class across three unrelated refusal categories.

    Two preconditions and one post-sampling breach, all reported through the
    same status. No requirement allocates a distinct code to any category — the
    category is carried by the reason text — so a consumer tests against zero
    and never against a particular value, POSIX having given no specific
    non-zero value a meaning.
    """
    statuses = {
        refused_below_the_chain_minimum.completed.returncode,
        refused_with_no_open_line.completed.returncode,
        refused_after_sampling.completed.returncode,
    }

    assert len(statuses) == 1
    assert 0 not in statuses
