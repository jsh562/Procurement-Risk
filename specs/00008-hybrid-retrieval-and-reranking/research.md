# Research: Hybrid Retrieval and Reranking

> Feature: E008 Hybrid Retrieval and Reranking | Date: 2026-07-29 | Purpose: ground user-story
> priorities, acceptance criteria, and edge cases for fused sparse/dense retrieval with local
> cross-encoder reranking over 5k–15k construction-procurement chunks, evaluated on a frozen set
> and published with intervals.

## Rank fusion for hybrid retrieval

- **Practice**: Reciprocal rank fusion sums `1/(k + rank)` per arm. `k = 60` is Elastic's default
  and the de facto field constant; Elastic states plainly that RRF requires no tuning. Bruch et al.
  contradict that robustness claim directly: convex combination of normalised scores outperforms RRF
  both in-domain and out-of-domain, RRF is sensitive to its parameter rather than robust, and convex
  combination is largely agnostic to the choice of normaliser. RRF's mechanism is score-blindness —
  it discards how far ahead rank 1 was. Two conventions the formula does not fix: what to do when a
  document appears in only one arm's list, and how ties break. Changing the window size changes
  result order even for identical ranks.
- **Implies**: RRF remains defensible here on determinism and single-statement grounds — per-arm
  window ranks fuse in one statement, which is what the deterministic computation boundary requires
  — but the justification is reproducibility, not accuracy. `k`, the tie-break key, and the
  missing-arm convention are named parameters that change published output, so they belong in the
  results manifest rather than in code comments. Fetch depth 50 is part of the ranking definition,
  not a tuning knob.
- **Flag**: Contested, and the popular direction is the weaker one. Do not assert that RRF beats
  score fusion. Arithmetic worth stating: at depth 50 with `k = 60`, rank 1 contributes `1/61` and
  rank 50 contributes `1/110` — a ratio of 1.8, so fusion is nearly uniform across the window and
  the ordering it produces is weak by construction. That is an argument for the reranker carrying
  the quality, and against any story that depends on fusion-only ordering being good.
- **Sources**: <https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion>,
  <https://arxiv.org/abs/2210.11934>

## Sparse retrieval quality on technical text

- **Practice**: BEIR established BM25 as a robust zero-shot baseline that dense retrievers
  frequently underperform out of domain; lexical retrieval wins on rare distinctive tokens — part
  numbers, section codes, standard references, manufacturer names — precisely where a dense model
  returns semantically adjacent text that never contains the typed token. **PostgreSQL native
  full-text search is not BM25.** The documentation is explicit that the ranking functions use no
  global information — there is no inverse document frequency. `ts_rank` ranks on matching-lexeme
  frequency, `ts_rank_cd` on cover density. Ranking can be I/O bound because it must consult each
  matching document's `tsvector`. The default parser emits overlapping tokens for hyphenated
  compounds and has distinct types (`numword`, `hword_numpart`, `version`, `file`, `url`), so
  identifiers such as `AISC 360-16`, `PN-4472/A` or `M12x1.5` split unpredictably, and stemming can
  further mangle codes.
- **Implies**: Name the sparse arm "native `tsvector` ranking", never "BM25", and publish no
  BM25-parity claim. A lexical-identifier probe set — section numbers, standard designations, model
  numbers, manufacturer names — verified to survive `to_tsvector` and match the corresponding
  `tsquery` belongs in P1. The absent IDF hurts less under RRF than under score fusion, because rank
  fusion consumes only order from each arm; that is a genuine argument for the fusion choice.
- **Flag**: Evidence is thin. No benchmark measures `ts_rank` quality on technical corpora, so the
  sparse arm's contribution is unknown rather than assumed. That makes a sparse-only ablation arm a
  first-class published row rather than an optional extra. A naive design assumes the sparse arm is
  BM25 and inherits BEIR's numbers; it is not and does not.
- **Sources**: <https://www.postgresql.org/docs/16/textsearch-controls.html>,
  <https://www.postgresql.org/docs/16/textsearch-parsers.html>

## Cross-encoder reranking

- **Practice**: BEIR reports reranking pipelines achieving the best average zero-shot performance
  across 18 datasets at materially higher compute cost, but gives no transferable constant for the
  lift. Against unbounded depth, Meng et al. found the best existing rerankers improve as they score
  progressively more documents but then decline, and can degrade below the first-stage retriever
  past a limit — reranking more candidates is not monotonically better. On quantization, published
  speedups exist but no ranking-quality retention numbers, with the explicit warning that a backend
  can perform slightly worse than the reference and must be tested per model and per dataset.
- **Implies**: Depth 50 sits inside the safe band and should be stated as a bounded choice, not a
  starting point. "Increase candidates to improve recall" is not a free lever and must not appear as
  a mitigation. ADR-0006's decision to publish quantized against full-precision as its own arm is
  the only defensible route, since no citable quantization cost exists. Cross-encoders truncate at
  maximum sequence length, so an over-long chunk is silently scored on its prefix: the chunk
  token-length distribution against the model's limit, and the fraction truncated, must be
  published.
- **Flag**: No source supports a numeric expected lift. Any criterion of the form "reranking
  improves MRR by X" is unfounded. Phrase it as the reranked arm and the fusion-only arm each
  reported with intervals and their paired difference, and accept that a statistically unresolvable
  difference at this set size is a valid, publishable outcome.
- **Sources**: <https://arxiv.org/abs/2104.08663>, <https://arxiv.org/abs/2411.11767>

## Retrieval evaluation

- **Practice**: Fuhr's list of common IR evaluation mistakes is the standard reference: omitting
  significance testing and confidence intervals, comparing against weak baselines, using too few
  topics, reporting only improvements. He additionally argues the reciprocal transform moves rank
  onto an ordinal scale, making the mean in MRR invalid; Sakai disputes this, so the point is
  contested rather than settled. Fifty topics is the traditional convention the frozen set matches,
  but power analyses conclude 50 is insufficient to resolve small differences, with hundreds needed
  for reasonable power.
- **Implies**: Recall@5 is a binomial proportion, so a Wilson interval is the right instrument.
  **MRR is not a proportion — a Wilson interval on MRR is a category error** and is replaced by a
  percentile bootstrap over the 50 queries. Baseline honesty means comparing against the strongest
  single arm, with dense-only and sparse-only both published. Ablation arms are reported as per-arm
  intervals plus paired per-query differences, with an explicit "not resolvable at n = 50" verdict
  wherever intervals overlap — that verdict is a result, not a failure.
- **Flag**: At n = 50, a recall@5 point estimate of 0.86 carries a Wilson 95% interval of roughly
  [0.74, 0.93]. Whether the PRD's ≥ 0.85 gate applies to the point estimate or the lower bound was
  undefined; **resolved during Specify as the point estimate, with the interval published beside
  it**. Under the lower-bound reading the target would be effectively unattainable at this set size.
- **Sources**: <http://sigir.org/wp-content/uploads/2018/01/p032.pdf>,
  <http://www.sigir.org/wp-content/uploads/2020/06/p14.pdf>

## Query-side handling

- **Practice**: Prompt-only query rewriting is strongly domain-dependent rather than a default win —
  measured at −9.0% nDCG@10 on one financial-domain set (p < 0.001), +5.1% on a biomedical set
  (p = 0.024), and non-significant on a scientific-claims set. Four failure patterns recur:
  terminology drift, hallucinated context injection, over-specification, and over-formalisation,
  with lexical substitution occurring in 95% of rewrites regardless of outcome. The stated
  conclusion is that never rewriting is safest for well-optimised verticals with stable terminology,
  and that selective rewriting predicts poorly, capping even oracle-level gains at about +3 points.
  Hypothetical-document generation carries the same risk in sharper form.
- **Implies**: Construction procurement is exactly the stable-jargon vertical the evidence says not
  to rewrite — fixed section codes, standard designations, manufacturer names. No query
  transformation at P1, stated as a cited decision rather than an omission. Any transformation added
  later enters as an ablation arm measured on the frozen set, never as an always-on default. Note
  the asymmetry: because the sparse arm matches lexemes, a rewrite that substitutes vocabulary
  damages the arm carrying the part numbers hardest.
- **Flag**: Rewriting does not break citation resolution — passages still come from the index and
  still carry their page. It changes *which* page is cited, which is worse in a coordinator-facing
  product: the failure mode is a confidently cited wrong page, indistinguishable from a correct one
  at the interface.
- **Sources**: <https://arxiv.org/html/2603.13301>

## Graceful degradation in retrieval systems

- **Practice**: Google's site-reliability guidance defines graceful degradation as reducing the work
  performed, and its worked example is this exact case — using a less accurate but faster ranking
  algorithm when overloaded. Two operational rules follow: monitor and alert when servers enter
  these modes, and remember that the code path never used is the code path that often does not work,
  with the recommendation to exercise the degraded path deliberately and regularly. Over-complex
  degradation logic backfires through unintended triggers and feedback loops.
- **Implies**: The fusion-only fallback is surfaced in the response payload and to the operator, not
  merely logged. It is exercised by an automated test that forces model-load failure and asserts
  both that the flag is set and that results are still returned. Any evaluation output records the
  mode it ran in, so no figure can be read as reranked when it was not. A container whose warm-up
  fails is **ready-degraded**, resolved during Specify.
- **Flag**: No standard exists for a machine-readable "these results are degraded" signal — HTTP has
  no suitable status code and the `Warning` header is deprecated. This is written from repository
  convention, citing nothing. The naive failure here is a fallback correct in code and untested in
  practice, which the guidance says is the normal outcome.
- **Sources**: <https://sre.google/sre-book/addressing-cascading-failures/>

## Open questions carried into the specification

Four were resolved during Specify and are recorded here so the reasoning is not lost:

- **Recall gate reading** — resolved: the point estimate, with the Wilson interval published beside
  it. The lower-bound reading would make ≥ 0.85 effectively unattainable at n = 50.
- **MRR interval method** — resolved: percentile bootstrap over queries. Wilson is invalid for a
  statistic that is not a proportion. The PRD names Wilson for MRR; E008 records the need for a PRD
  amendment and does not perform it.
- **Degraded readiness semantics** — resolved: ready-degraded, flagged in every response and at the
  readiness endpoint.
- **Ablation ownership** — resolved: E008 exposes each arm as an independently runnable path and
  proves each returns results; E014 owns the frozen set, the intervals, and the manifest.

Carried open, and belonging to E014 rather than E008:

- **Pooling bias.** If relevance judgements are built only from what the current system returns,
  every future ablation arm is scored against a pool biased toward today's design. The judging pool
  — union across arms, or manual — must be decided before the set is frozen.
- **Freeze discipline.** Re-tuning against the frozen set converts it into a training set. A stated
  rule is needed on how many times it may be consulted, and by whom.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion> | rank fusion | 2026-07-29 |
| <https://arxiv.org/abs/2210.11934> | rank fusion | 2026-07-29 |
| <https://www.postgresql.org/docs/16/textsearch-controls.html> | sparse retrieval | 2026-07-29 |
| <https://www.postgresql.org/docs/16/textsearch-parsers.html> | sparse retrieval | 2026-07-29 |
| <https://arxiv.org/abs/2104.08663> | reranking; lexical vs dense | 2026-07-29 |
| <https://arxiv.org/abs/2411.11767> | reranking depth | 2026-07-29 |
| <http://sigir.org/wp-content/uploads/2018/01/p032.pdf> | evaluation | 2026-07-29 |
| <http://www.sigir.org/wp-content/uploads/2020/06/p14.pdf> | evaluation | 2026-07-29 |
| <https://arxiv.org/html/2603.13301> | query-side handling | 2026-07-29 |
| <https://sre.google/sre-book/addressing-cascading-failures/> | graceful degradation | 2026-07-29 |
