# Frozen forecast fixture — provenance

Synthetic data committed to the repository. It carries **generator provenance**,
because that is the provenance it has. It carries **no retrieval provenance**,
because it was not retrieved from anywhere, and inventing a source for a file
that has none is the exact defect provenance exists to prevent (FR-037).

| Field | Value |
|---|---|
| Kind | Test fixture (not a corpus document, not a published dataset) |
| Layer | `SYNTHETIC` |
| Generator | [`generate.py`](generate.py) |
| Seed | `20260601` |
| Generated on | 2026-07-28 |
| Regenerate | `uv run --directory src/api python tests/fixtures/frozen_run/generate.py` |
| Row digest | `sha256:bdae4ab31770d7596a0c9dba9405e04bf0d4f5403b35ace4e42e624f1024f41c` |
| Size | 623,556 bytes |

## Why it is frozen

The generated file is committed and is not rebuilt at test time. If it were, an
edit to `generate.py` would silently move every expected value, and a test whose
expected values move with the code under test asserts nothing.

Regenerating is therefore a deliberate act with a reviewable diff. The row
digest is what that review checks: nobody reads a 4000-element draw array by
eye, so a silent edit has to be detectable some other way.
`test_the_row_digest_matches_the_rows` recomputes it on every run.

## Run parameters

| | |
|---|---|
| `run_id` | `3f7c2b90-5a44-4e11-8b0a-6d9e1c33a201` |
| `as_of_date` | 2026-06-01 |
| `horizon_days` | 365 |
| `draw_count` | 4000 |
| `model_version` | `lognormal-hierarchical-v3` |

`today` is **injected**, never read from a clock (FR-038). That is what makes the
staleness cases constructible without waiting: a single run at a fixed as-of date
is exactly at the seven-day threshold when `today = 2026-06-08` and past it when
`today = 2026-06-09`, so both halves of FR-036's staleness case come from one
run rather than from two that could not both be active.

## How it loads

Only through the committed schema — plain `INSERT`s against the migrated tables,
so no test can exercise a state the storage layer forbids. The constraints do
real work here rather than decorating the load: the first draft of this fixture
gave the already-late line negative draws, and
`ck_line_posterior__draws_non_negative` rejected it. A draw is a *remaining
duration from the as-of date*, so it cannot be negative however far in the past
the need-by date sits — a hand-rolled loader would have accepted it and every
figure derived from that line would have been quietly wrong.

## Boundary cases (FR-036)

Each line below realises one named case. A boundary case with no fixture line is
untested however many tests run, so `test_every_boundary_case_has_a_line_behind_it`
asserts the map rather than trusting this table.

| Line | Case | Need-by | Crit. | P(miss) | P50 | P80 | Harm | Residual tail |
|---|---|---|---|---|---|---|---|---|
| `PO-4471-1` | nominal | 2026-07-31 | 4 | 0.30000 | 51.4535 | 70.2231 | 18.366285 | 0.00000 |
| `PO-4471-2` | median equals eightieth | 2026-07-11 | 3 | 0.20000 | 34.0000 | 34.0000 | 1.835880 | 0.00000 |
| `PO-4472-1` | exact harm tie | 2026-07-21 | 4 | 0.25000 | 43.2776 | 54.2046 | 10.202228 | 0.00000 |
| `PO-4472-2` | exact harm tie | 2026-07-21 | 4 | 0.25000 | 43.2776 | 54.2046 | 10.202228 | 0.00000 |
| `PO-4473-1` | no posterior | 2026-08-10 | 5 | — | — | — | — | — |
| `PO-4473-2` | roster mismatch | 2026-08-20 | 3 | 0.35000 | 73.1190 | 93.1128 | 16.065307 | 0.00000 |
| `PO-4474-1` | need-by equals as-of | 2026-06-01 | 5 | — | 15.3084 | 24.3014 | 76.518741 | 0.00000 |
| `PO-4474-2` | need-by before as-of | 2026-05-22 | 4 | — | 15.2729 | 24.3296 | 101.204911 | 0.00000 |
| `PO-4475-1` | need-by between as-of and today | 2026-06-02 | 2 | 0.90000 | 14.6938 | 24.6140 | 27.542482 | 0.00000 |
| `PO-4475-2` | last in-grid day | 2027-06-01 | 3 | 0.01000 | 335.3330 | 353.3300 | 0.919253 | 0.01000 |
| `PO-4476-1` | day after horizon | 2027-06-02 | 5 | — | 336.1631 | 354.3603 | 1.335920 | 0.02425 |
| `PO-4476-2` | residual tail rounds to extreme | 2027-06-16 | 4 | — | 321.3686 | 347.4260 | 0.000500 | 0.00075 |
| `PO-4477-1` | P(miss) on half-percent boundary | 2026-07-26 | 3 | 0.35500 | 48.2807 | 68.4215 | 16.282153 | 0.00000 |
| `PO-4477-2` | P(miss) on half-percent boundary | 2026-07-26 | 3 | 0.36500 | 48.5951 | 68.8905 | 16.747012 | 0.00000 |
| `PO-4478-1` | adjustment changes no ordering | 2026-08-30 | 1 | 0.01500 | 82.6257 | 87.1583 | 0.114775 | 0.00000 |
| `PO-4479-1` | terminal line (off the worklist) | 2026-07-01 | 2 | 0.02500 | 15.3768 | 24.5162 | 0.765119 | 0.00000 |

P50 and P80 are offsets in days from the as-of date, under the nearest-rank,
one-based, no-interpolation convention `schema_constants` records. Harm is
`E[max(0, delivery − need_by)] × criticality`. `—` means the quantity is not
defined for that line, not that it is zero: `PO-4473-1` has no posterior at all,
and the four lines with no P(miss) have a need-by outside the survival grid.

### Cases worth reading twice

**The tie is exact, not close.** `PO-4472-1` and `PO-4472-2` are built from one
shared draw stream, so their harms are equal to the last representable digit.
They also share a need-by date and a criticality, which exhausts every earlier
tiebreak — so `po_line_id` is what actually resolves them, and the tiebreak's
totality is genuinely under test rather than incidentally satisfied.

**The equal quantiles are equal, not near.** `PO-4471-2`'s posterior has a
constant body, so the 50th and 80th percentiles land on day 34 identically. A
"near-degenerate" posterior whose quantiles merely round to the same day would
not exercise the display path that has to show an interval anyway.

**The residual tail is 0.075%, which rounds to 0%.** `PO-4476-2` exists so the
display is forced to read `<1%` rather than `0%`: a bound of zero states a
certainty the posterior does not carry. Note this is mass beyond **day 365**, not
beyond the line's need-by date at day 380 — the two are different numbers, and
conflating them is what an earlier draft of the generator did.

**The half-percent pair sits on the boundary from both sides.** 35.5% and 36.5%
exactly. Whatever rounding rule is chosen it must be stated and applied
identically to both, which is only checkable with both present.

**Two lines differ by one day across the horizon edge.** `PO-4475-2` at day 365
and `PO-4476-1` at day 366. That makes the edge a tested boundary rather than an
assumption about where the grid stops.

## The recorded adjustment (FR-012)

| | |
|---|---|
| Line | `PO-4478-1` |
| Need-by of record | 2026-08-30 |
| Adjusted to | 2026-08-26 (4 days earlier) |
| Harm before | 0.1147749267 |
| Harm after | 0.6962806242 |
| Ordering | unchanged |

The pull is **searched for at generation time**, not hand-picked: the generator
takes the largest pull that leaves the full ordering identical. A hand-picked
date would leave its own harmlessness resting on numbers nobody rechecks when
the fixture moves.

The harm rises and the position does not — which is the case FR-012 names. The
interface must say the adjustment was applied *and* that the order did not
change, because silence is indistinguishable from the adjustment having been
ignored.

## Survival semantics

`survival[k] = count(draws > k) / draw_count` — the probability the delivery has
**not** occurred by day `k`, and therefore the probability of missing a need-by
at offset `k`. It is read directly, with **no complement**.

`test_survival_is_the_probability_of_being_late_not_of_being_on_time` checks
every line's curve against its own draws. Taking `1 − survival[k]` instead would
rank the safest lines first and look entirely plausible on screen, which is why
it is asserted rather than assumed.
