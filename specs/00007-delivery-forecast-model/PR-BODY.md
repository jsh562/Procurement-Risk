# E007: Delivery Forecast Model

A hierarchical censored duration model over the delivered procurement history, producing per-line posterior draws and derived day-grid survival arrays under a run manifest — or refusing and writing nothing.

**QC passed.** 45/45 functional requirements, 42/42 success criteria, 2569 model tests and 402 gateway tests, 92% coverage against an 80 threshold, `ruff check` **and** `ruff format` clean at four roots, import-linter 8 contracts kept / 0 broken, `mypy` clean on the entry in scope. Full evidence in `specs/00007-delivery-forecast-model/qc-report.md`.

## What ships

Two console entry points — `forecast-fit`, `forecast-reproduce` — and 21 modules under `model.forecast`, plus four migrations in the claimed `0300`–`0399` block. No new third-party dependency: PyMC, ArviZ, NumPy and pandas were already declared, and the plan audit verified that rather than accepting it.

**The fit is a multi-state sojourn model, not a total-duration regression** (AD-001). One lognormal per lifecycle transition with a zero-sum hierarchical vendor and category term, plus a rework-versus-forward sub-model observed only at completed decision points. The total-duration route could not honour FR-002 without leaking or degenerating: path covariates evaluated at a held-out line's own order date are constant, so they inform only the population they are not fitted on, while using their realized totals uses the outcome to predict the outcome.

**Forecasts are two populations in two tables, distinguished by anchor** ({SAD:ADR-0018}). `line_posterior` keeps as-of-anchored open lines whose draws are *conditional remaining* durations; `held_out_prediction` keeps order-date-anchored delivered lines whose draws are *total* durations. Mixing them would mis-score every held-out row with nothing detecting it, because E010 computes `1 − survival[d − as_of_date]` and cannot tell the anchors apart.

**Refusal is achieved by ordering, not rollback** (AD-010). Both gates run before any statement is issued, so a refused run has nothing to roll back. Verified by snapshotting all five stores and the active-run pointer around a real forced-failure subprocess.

## Convergence — measured, and it did not pass first time

At the published shape across six seeds, the centered vendor hierarchy **failed the blocking gate on two of six**. `tau_vendor` posts 0.145 against residual log-scales of 0.71–1.13, so the vendor level sat at the neck of a funnel. **AD-012** non-centers it; all six seeds now pass with zero divergences, worst R-hat 1.0050 against a 1.01 bar.

No threshold moved. Widening `DIVERGENCES_MAX` was rejected outright — one failing seed failed on a single divergence, so widening would have bought a pass by lowering the bar, which is the post-hoc change FR-028 actually prohibits. A reparameterization moves neither the bar nor the posterior. Both costs are recorded: E-BFMI falls from 0.90–0.97 to 0.76–0.87 and tree depth rises 4–5 to 5–6, both far from their bars. The category hierarchy stays centered **on measurement** — non-centering it also passes but is worse on every efficiency measure.

## Six published criteria would have failed a correct implementation

This is the epic's most useful finding, and two of the six were introduced while fixing an earlier one.

- **SC-027 asserted monotone median residual life.** A lognormal's hazard rises to a peak and falls, so its median residual life *falls* first: measured at σ = 0.527 it runs 1.000 → 0.753 → 0.564 → 0.427 → 0.406 before turning. A correct inverse-CDF implementation fails that on four of the first five points. The re-basing discrimination it existed for is now carried by relations that hold everywhere — strict positivity, which catches the point mass re-basing puts at zero, and the truncation identity.
- **The shrinkage row required ρ's interval to widen as nⱼ falls and to be widest at nⱼ = 0.** ρ is logistic in `log n`, so its interval peaks near `n = σ²/τ²` — about 21 here — and collapses at both ends. At n = 0 every draw is exactly 0, so the honest triple is `(0, 0, 0)`: the narrowest. The monotone-width claim belongs to the vendor-effect interval, a different quantity.
- **Earlier: an `S(0)` element the schema does not store** (three times), **an exact equality against a `1e-9` tolerance**, **a flat 10% ablation floor** sitting above the only measured analogue available, and **a 3.0-day reproduction tolerance** whose derivation omitted the effective sample size and the two-run factor.

Every one was caught by computing the quantity before publishing the bound. None was caught by review.

## Defects the work surfaced

- **Negative remaining draws.** Once `S(e)` underflows, the precision cap pins `F*` to one quantile, `T` becomes a ceiling independent of `e`, and `T − e` goes negative without bound — −15,610 days at 20,000 elapsed. The comment above the cap claimed it "keeps the draw finite and enormous, which is the honest answer"; it is neither. Now refuses, naming the elapsed time.
- **AD-001 and AD-002 did not compose, and neither said so.** One fits per transition, the other conditions one lognormal, and nothing bridged them. **AD-013** records the Fenton–Wilkinson parent and **L-5** its cost — future rework loops are not predicted, so an open line at a decision point is biased short.
- **Two hard-coded migration-block tables, not one.** `AD-007` named only the `/tests` file; `test_migration_chain.py` holds its own `DECLARED_BLOCKS` and parametrizes over every revision the chain walks. Verified the premise rather than assuming it: part (a) alone turns one red assertion into two.
- **E007 broke a gateway test in another entry** by moving the chain head, invisible to every local model-suite run. Found by QC.
- **SC-005 was not achievable as implemented** — the vendor-effect interval existed only in the test file, so the comparison ran against a seeded stand-in with operands hard-coded at 5 and 35, rather than the run's own fitted τ/σ and stored split rows. Found by QC.
- **A test-module basename collision** between `tests/forecast` and `tests/procurement`, invisible until the forecast module went green.

## Test-first: 10 of 10

Every property-test file lands in a `test:` commit strictly earlier than the `feat:` commit adding its module, verified from file-addition history rather than from the pairs table. E005 closed this obligation at six of seven.

## Carried open, deliberately

**Twenty disclosed gaps** `G-1`–`G-20`, each recorded as uncovered rather than claimed. Three are worth naming: `G-8` — a refused run leaves no diagnostic row anywhere, by design, so its evidence lives only in the exit output and the refusal report; `G-11` — the schema cannot distinguish pre-registration from post-hoc adjustment, so FR-028's prohibition rests on the commit history and the tests demonstrate that rather than implying coverage; `G-20` — a line not yet ordered at the anchor is treated three different ways by three components, detected by no criterion.

**Six four-part limitations** `L-1`–`L-5` reach the reader-facing report, including that the fit's structure matches the structure its input was generated from, and that the realized held-out uncensored event count is ~44 against the PRD's ~120, so the registered coverage band is not measurable at this split. E007 publishes the miss and adjusts no band.

**`tasks.md` self-reports a failure on its own 200-character rule** — 17 lines exceed it. Recorded as a measured fail rather than fixed by shortening delivered task lines to make a self-report green.

## Amendments and propagation — recorded, not performed

Eleven obligations `P-1`–`P-11`. **`P-4`**: the PRD's ~120-event assumption and its 73–87% band conflict with the ~44 events a 0.25 split realizes. **`P-9`–`P-11`**: two PRD bullets and five SAD baseline entries were written on this branch during Specify and Plan and have been **reverted** — `project-instructions.md` § Governance reserves managed-section rewrites to the default branch, and the branch was meanwhile declining four other amendments on that same rule. The ADR-0018 catalog row was kept, since creating a record is not amending one.

**A systemic finding the revert does not fix**: `specify-feature` §6.5.4 and `plan-feature` §5.6.3 both *instruct* the rewrite Governance forbids on a feature branch. The next epic reproduces it. That belongs to whoever owns the skills.

## Artifacts

Migrations `0300`–`0303` · `model.forecast` (21 modules) · 67 test modules under `src/model/tests/forecast` · three emitted report kinds — run, reproduction, refusal
