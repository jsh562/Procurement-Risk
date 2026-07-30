# Datasheet: E008's frozen evaluation query set

Required by `project-instructions.md` §Data Provenance — *"Every synthetic
dataset MUST ship a datasheet disclosing its generative assumptions"* — and by
spec FR-050. This set is synthetic in the sense that matters: its **relevance
judgements are generated**, not observed.

## What this set is

| | |
|---|---|
| **Generator** | `e008-evaluation-set` |
| **Seed** | `0` |
| **Query count** | 3 |
| **Draw method** | Hand-authored against the committed fixture corpus, one query per retrieval behaviour the P1 criteria measure: a lexical match, a dense match, and a part-number lookup |
| **Judgement source** | The generator's pre-render document model — the record of what each document *prints*, not the rendered text |
| **Digest** | Recorded in `manifest.json`; verified before any query is returned |

## The ceiling, stated plainly

**Every query in this set is answerable by construction.** The judgements come
from the generator's own record of what each document contains, so a relevant
chunk provably exists for every query and the retrieval task is strictly easier
than a coordinator's.

Recall measured on this set is therefore an **upper bound on real-world
performance, not an estimate of it**, and FR-043 requires it published as such
rather than as a recall figure that happens to be high.

This is the disclosure most easily omitted and the one that changes how every
other number here is read. A recall of 0.9 measured this way does not mean nine
in ten real questions are answered; it means that when the answer is guaranteed
to be present, it is found nine times in ten.

## What it does not support

- **It is not a benchmark.** Three queries cannot separate two arms differing by
  a few points, and no comparison drawn from it should be reported as though it
  could. `specs/00008-*/spec.md` §Risks records the same limitation for the
  fifty-query set this one stands in for during development.
- **It is not held out.** The set is measured against without limit; what is
  disciplined is *tuning after measurement*. Any ranking-parameter change made
  after a figure is measured is recorded as a decision, the set re-measured, and
  **both figures emitted together** — publishing only the later one satisfies
  the re-measurement and hides the tuning.
- **It shares a corpus with the integration fixture.** Chunk identifiers here
  are the fixture's, so this set measures retrieval over six chunks. It is a
  wiring check, not a quality measurement.

## Why generator-derived judgements at all

Chosen over hand-labelling, which is unreproducible without the labeller, and
over pooled judgements, which still need a judge and must be rebuilt whenever an
arm is added. The cost is the ceiling above, and it is paid deliberately: a
reproducible measurement whose bias is *known and stated* is worth more than an
unreproducible one whose bias is merely absent from the write-up.

**Reversal trigger.** Any query set drawn from real coordinator questions — at
which point the generator-derived figure becomes the ceiling the real figure is
reported against, rather than the headline.

**Production-scale alternative.** Pooled judgements across arms, judged by a
domain reader, which is the standard remedy for judgements biased toward the
system that produced them.
