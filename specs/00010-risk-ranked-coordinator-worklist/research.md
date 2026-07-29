# Research: Risk-Ranked Coordinator Worklist

> Feature: E010 Risk-Ranked Coordinator Worklist | Date: 2026-07-28 | Purpose: ground the uncertainty presentation, the ranking's legibility, degraded-state honesty, row density, and probabilistic acceptance criteria for a read-time worklist computed from stored posterior and survival artifacts.

## Communicating an interval and a lateness probability to a non-statistical coordinator

- **Decision**: Label every quantile with its reference class and a frequency restatement. Render P50 and P80 as two named quantities — half of comparable orders land by X, four in five by Y — not as a bare "X–Y" range and never as an "expected" date.
- **Rationale**: The dominant lay failure is the deterministic construal error: non-experts frequently do not register that a display is probabilistic at all and read it as a single deterministic quantity. Explicit numeric probability text attached to the figure is the mitigation with the best evidence. Separately, a single-event probability with no stated reference class is read in mutually contradictory ways — "30% chance of rain" has been found to be read as 30% of the time, 30% of the area, or 30% of forecasters. Frequency-framed displays produced measurably better incentivized decisions than text-only uncertainty or a no-uncertainty control.
- **Pitfalls**: A two-ended range reads as a guarantee, its endpoints as hard bounds with no mass outside. P80 reads as a deadline or commitment rather than "one in five arrive later than this". Positive and negative framings are not behaviourally equivalent, so state both directions. Rounding P(miss) to 0% or 100% converts a forecast back into the certainty this product exists to remove.
- **Implication for the spec**: Require each quantile to carry an inline sentence naming its reference class and complementary frequency; require dual framing of P(miss); forbid any single-date rendering of a line's delivery anywhere — row, tooltip, sort label, export, or notification.
- **Sources**: https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2020.590232/full, https://onlinelibrary.wiley.com/doi/10.1111/j.1539-6924.2005.00608.x

## Ranking a queue by expected harm rather than by date or probability alone

- **Decision**: Rank on a continuous expected-harm score, and expose in each row the inputs that produced its position — P(miss), criticality, and slack to need-by — rather than the composite score alone.
- **Rationale**: Sequencing by expected cost of delay is established prior art; weighted-shortest-job-first orders a queue by cost of delay over duration, and the queueing result is that sequence, not per-item priority, determines total delay cost. Analysis of ordinal risk matrices shows the failure mode of the opaque alternative: collapsing continuous frequency and severity into ordinal cells resolves only a small fraction of randomly selected hazard pairs unambiguously, and can order worse than random where frequency and severity are negatively correlated. Operators adopt an imperfect algorithm far more readily, and perform better, when they can modify its output — even when the permitted modification is severely restricted.
- **Pitfalls**: A single opaque risk-score column invites either blind trust or wholesale rejection. Ordinal High/Medium/Low buckets can invert the true order. A ranking that cannot be interrogated per row is unfalsifiable to the operator, so their only recourse is to stop using it. Transparency is not monotonic either — trust rises with explanation but erodes again when the interface over-explains.
- **Implication for the spec**: Require the score to be computed on continuous inputs and decomposable in the row. Treat operator-editable need-by with immediate re-ranking as the trust mechanism rather than a convenience, which places it in P1. Require the active sort key and direction to be stated on screen.
- **Sources**: https://framework.scaledagile.com/wsjf, https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1539-6924.2008.01030.x

## Absent, stale, and failed model artifacts

- **Decision**: Name three distinct states with distinct copy — never modelled, artifact stale, run failed — and in all three suppress the figures entirely rather than degrade them. Show the artifact's as-of date on every populated row.
- **Rationale**: The first empty-state guideline is that the state must communicate system status, with an explicit warning against copy asserting that no data exists while the system is still working. Guidance on machine-learning failures separates system errors from context errors and recommends stating specifically why a result cannot be produced and what would change it — the worked example being a forecast withheld for insufficient data with a stated retry horizon — while returning control to the user rather than substituting a value.
- **Pitfalls**: A zero, an em-dash, or a stale figure rendered identically to a fresh one is the worst outcome, because the coordinator cannot see that it is absent. A page-level banner alone is insufficient once rows are sorted or filtered. "No results after filtering" and "no model run" are different states and must not share copy. A stale artifact silently reorders the list relative to reality with no visible signal.
- **Implication for the spec**: Require the three states as separately acceptance-tested screens, each with its own copy and its own path forward. Require an artifact as-of date visible without hover. Require lines with no posterior to be excluded from the ranking and listed separately, not sorted to the bottom carrying a zero.
- **Sources**: https://www.nngroup.com/articles/empty-state-interface-design/, https://pair.withgoogle.com/chapter/errors-failing/

## Row density and what belongs behind a detail view

- **Decision**: Cap the row at roughly four scannable quantities and push the remainder behind the detail view. Candidate row set: human-readable line identifier, need-by, P(miss), and the P50/P80 pair rendered as one labelled unit.
- **Rationale**: Working-memory capacity is better described as about four chunks than seven, so a row carrying six independent numbers is not comparable at a glance. Data-table guidance orders columns by importance, puts a human-readable identifier first rather than a generated ID, keeps related columns adjacent, and prefers expandable rows or a non-modal panel so the operator keeps scroll position and the neighbouring rows they are comparing against.
- **Pitfalls**: Treating P50 and P80 as two independent sortable columns doubles the perceived numeric load and invites sorting by P50 alone — which reintroduces the point estimate through the sort control. Icon-only row actions without labels. Modal detail that occludes the rows being compared.
- **Implication for the spec**: Require the quantile pair to be one visual unit under one label. Enumerate the permitted sort keys explicitly and justify each. State a maximum default column count and make extra columns opt-in. The full detail view belongs to E012, so E010 decomposes the score in-row rather than building a panel.
- **Sources**: https://www.nngroup.com/articles/data-tables/, https://www.cambridge.org/core/journals/behavioral-and-brain-sciences/article/magical-number-4-in-shortterm-memory-a-reconsideration-of-mental-storage-capacity/44023F1147D4A1D44BDC0AD226838496

## Acceptance criteria and boundary cases for a probabilistic screen

- **Decision**: Split criteria into two classes. Read-path criteria are fully deterministic — freeze a posterior fixture and assert exact rendered strings, ordering, and state transitions. Distributional quality is a separate offline gate and must not appear as a screen criterion.
- **Rationale**: The page performs no model call, so its output is a pure function of stored artifacts plus operator inputs; nothing about it requires probabilistic acceptance criteria, and writing them that way makes the screen untestable for no gain. Maximizing sharpness subject to calibration is the right criterion for the model gate and belongs there. Human-AI interaction guidance on making clear what a system can do, and how well it does it, converts directly into assertable on-screen text.
- **Pitfalls and boundary cases worth naming**: a need-by already in the past, where P(miss) is 1 by construction and the row must say "already late" rather than present a forecast; a line with no delivery history, which yields the absent state rather than a prior-only figure dressed as a forecast; P(miss) rounding to 0% or 100%, which should display as "<1%" and ">99%" because asserting certainty is a regression; P50 equal to P80 on a near-degenerate posterior; ties in the ranking with no stated tiebreak; an edit that legitimately changes no ordering, which must still visibly acknowledge the edit.
- **Implication for the spec**: Adopt monotonicity invariants as acceptance criteria, because they are testable without knowing the numbers — moving a need-by date earlier must not decrease P(miss); raising criticality must not lower a line's rank; re-ranking must record no model invocation. Require one named test per boundary case above.
- **Sources**: https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9868.2007.00587.x, https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf

## Open questions carried into the spec

- Whether P(miss) is evaluated against the artifact's as-of date or today's date is a product decision with a visible consequence: under the second, a row's probability changes overnight with no model run and no user action. Resolved in the spec as the artifact's as-of date, shown on screen.
- No source gives an evidence-based staleness threshold for a forecast artifact. Treated as a configured age with a stated basis rather than a cited figure, per Principle VII.
- Whether operator edits to need-by persist to the record or are a local what-if. E017 owns override persistence, so the spec's informed default is what-if, carried as its single clarification marker.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2020.590232/full | uncertainty comprehension | 2026-07-28 |
| https://onlinelibrary.wiley.com/doi/10.1111/j.1539-6924.2005.00608.x | uncertainty comprehension | 2026-07-28 |
| https://framework.scaledagile.com/wsjf | expected-harm ranking | 2026-07-28 |
| https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1539-6924.2008.01030.x | expected-harm ranking | 2026-07-28 |
| https://www.nngroup.com/articles/empty-state-interface-design/ | absent, stale, failed states | 2026-07-28 |
| https://pair.withgoogle.com/chapter/errors-failing/ | absent, stale, failed states | 2026-07-28 |
| https://www.nngroup.com/articles/data-tables/ | row density and detail view | 2026-07-28 |
| https://www.cambridge.org/core/journals/behavioral-and-brain-sciences/article/magical-number-4-in-shortterm-memory-a-reconsideration-of-mental-storage-capacity/44023F1147D4A1D44BDC0AD226838496 | row density and detail view | 2026-07-28 |
| https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9868.2007.00587.x | probabilistic acceptance criteria | 2026-07-28 |
| https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf | probabilistic acceptance criteria | 2026-07-28 |
