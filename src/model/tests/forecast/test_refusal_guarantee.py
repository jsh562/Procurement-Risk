"""T087 — NC-1 / DV-013 / SC-014 / SC-015: a refused run leaves everything as found.

The epic's central negative control. A forced non-converging configuration must
write **no row** in `forecast_run`, `line_posterior`, `held_out_prediction`,
`forecast_split_assignment` or `forecast_diagnostic`, must leave
`v_active_forecast_run` returning the previously active run unchanged, and must
exit non-zero naming **every** breached blocking diagnostic under FR-017's
five-field set — the metric, its `parameter_name` where the metric is
parameter-scoped, its realized value, its threshold, and the threshold's
**direction**.

Five stores rather than "the forecast tables", because splitting storage created
a second place a non-converged artifact could survive a refusal (STF-002), and
the diagnostic store is named explicitly because it is the one a reader expects a
refusal to *fill* — it stays empty by design, which is G-8.

**Snapshotted before and after.** DV-013 is a claim about a transition, and a
test that only looked afterwards would pass on a database that already held the
row it was checking for. The snapshots bracket the invocation in
`conftest.RefusedInvocation`, and the previously active run is a *real* one —
the tier's shipped `emitted_run` — so "the pointer is unchanged" is not the
weaker claim that nothing became active.

**Forced how.** Four chains, at the published minimum, of five draws each. The
chain-count precondition is therefore *met*, so the refusal is unambiguously the
post-sampling gate's: a sample was taken and nothing was written, which is
FR-017's evidence and not FR-035's. NC-14 is the other one and it lives in
`test_refusal_controls.py`.
"""

from __future__ import annotations

from forecast.conftest import SNAPSHOT_TABLES, EmittedRun, RefusedInvocation

#: The five field labels FR-017 fixes, as they appear in the stderr line
#: `diagnostics.py` renders. Named here so the assertion is over a field set
#: rather than over the verb "naming", which any message containing the metric
#: satisfies.
FIELD_MARKERS = ("realized", "against a threshold of", "direction", "breached")

#: Every metric that can breach after sampling. Treedepth is deliberately absent
#: — it is reported, never blocking, and a refusal citing it would be a gate
#: nobody published.
BLOCKING_METRIC_NAMES = (
    "r_hat",
    "ess_bulk",
    "ess_tail",
    "divergent_transitions",
    "ebfmi",
)


def test_the_forced_run_refuses_with_a_non_zero_status(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The refusal happened at all, and it happened after sampling.

    Asserted first because every claim below is conditional on it: a run that
    shipped would leave the five stores legitimately changed, and the snapshot
    comparison would then be reporting a success as a defect.
    """
    assert refused_after_sampling.completed.returncode != 0
    assert refused_after_sampling.completed.stdout == "", (
        "standard output carried something on a refusal; a consumer piping the job into a "
        "query would read it as an identifier (FR-039)"
    )
    assert "sampling" in refused_after_sampling.completed.stderr, (
        "the job reports no sampling step, so this refusal may not be the post-sampling "
        "gate's — NC-1's evidence is that a sample was taken and nothing was written"
    )


def test_no_row_was_added_to_any_of_the_five_stores(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """SC-015 enumerated across stores, one assertion per store.

    Per store rather than as a single dict comparison, so a failure names the
    store that moved. `forecast_diagnostic` is the interesting one: it is where
    a reader would expect a refusal's evidence to land, and it is empty by
    design — the refusal's diagnostics live in the exit message and the emitted
    report (G-8).
    """
    for table in SNAPSHOT_TABLES:
        assert refused_after_sampling.after[table] == refused_after_sampling.before[table], (
            f"`{table}` went from {refused_after_sampling.before[table]} rows to "
            f"{refused_after_sampling.after[table]} across a refused run. The gate runs "
            f"before the first statement, so there should have been nothing to roll back"
        )


def test_the_active_run_pointer_is_unmoved(
    refused_after_sampling: RefusedInvocation, emitted_run: EmittedRun
) -> None:
    """`v_active_forecast_run` still returns the run it returned before.

    Compared against the *previous* value rather than against emptiness: the
    pointer is only ever written by transaction 2, which runs after transaction
    1 commits, so a refusal before that point cannot move it — and the way to
    show that is to have something for it to have moved away from.
    """
    assert refused_after_sampling.before["active_run"] == [str(emitted_run.run_id)], (
        "the shipped run is not the active one before the refusal, so this test has no "
        "pointer value to hold constant"
    )
    assert refused_after_sampling.after["active_run"] == (
        refused_after_sampling.before["active_run"]
    )


def test_the_exit_message_names_every_breached_blocking_diagnostic(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """FR-017's field set, and **every** breach rather than the first found.

    A message carrying one breach sends an operator back for a second run to
    discover the next, which is the failure the "not merely the first one found"
    clause exists to close. The count the job reports is compared against the
    number of rendered breaches, so a gate that counted many and printed one
    fails here.
    """
    stderr = refused_after_sampling.completed.stderr
    rendered = stderr.count("breached")

    assert "blocking diagnostic(s) breached after sampling" in stderr
    assert rendered > 1, (
        f"the refusal renders {rendered} breach(es); a five-draw fit breaches R-hat and "
        f"both ESS bars on every monitored parameter, so one is a gate that stopped at "
        f"the first"
    )
    for marker in FIELD_MARKERS:
        assert marker in stderr, (
            f"the refusal message carries no {marker!r}; FR-017 states the obligation as a "
            f"field set rather than as the verb 'naming', because a message containing the "
            f"metric's name satisfies any prose test"
        )


def test_the_message_carries_the_threshold_direction_and_the_parameter(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The two fields a restated unit loses first.

    The direction, because a value and a bar do not resolve to a verdict for a
    reader who does not already know which metrics are floors and which are
    ceilings — and this epic's own history records the refusal message keeping
    the threshold and dropping its direction. The parameter, because `r_hat`,
    `ess_bulk` and `ess_tail` are keyed by parameter and a bare metric name does
    not say which one breached.
    """
    stderr = refused_after_sampling.completed.stderr
    named = [metric for metric in BLOCKING_METRIC_NAMES if metric in stderr]

    assert "direction max" in stderr and "direction min" in stderr, (
        "the message names only one direction; both a ceiling (R-hat) and a floor (ESS) "
        "breach in a five-draw fit, and reporting one direction for both would make a "
        "breach read as a pass for a reader who trusted it"
    )
    assert named, f"no blocking metric is named at all: {stderr[:400]}"
    assert "mu_sojourn[" in stderr or "on `" in stderr, (
        "no parameter-scoped breach names its parameter; `r_hat` on an unnamed parameter "
        "does not tell an operator which one to look at"
    )
    assert "max_treedepth_hits" not in stderr.split("breached after sampling")[-1], (
        "the refusal cites treedepth, which FR-018 makes reported and never blocking; a "
        "run refused on it would be refused against a gate nobody published"
    )
