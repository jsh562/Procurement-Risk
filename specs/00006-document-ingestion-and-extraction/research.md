# Research: Document Ingestion and Extraction

> Feature E006 `00006-document-ingestion-and-extraction` | 2026-07-27 | Purpose: ground chunk-boundary policy, the encoder-cap interaction, per-field confidence semantics, line-item field sets, page-provenance validation, and evaluation-set stability for a product spec
>
> Second pass | 2026-07-27 | Purpose: ground tokenizer measurement, sentence segmentation, transactional ingestion, derived-data generations, vector bulk-loading, and interval reporting for the implementation plan

## Structure-aware chunking of UFGS sections

- **Decision**: Chunk on the SpecsIntact/CSI hierarchy — section number and title, then PART 1 GENERAL / PART 2 PRODUCTS / PART 3 EXECUTION, then Article (`1.1`), Paragraph (`1.1.1`), subparagraphs to level 6 — taking the deepest unit that fits the cap, and carrying section number, part, and article path as chunk metadata rather than as prose inside the chunk.
- **Rationale**: SpecsIntact enforces exactly three parts and nests up to six numbered levels beneath them, so boundaries are lexically detectable rather than inferred. A specialised-domain chunking study found structure-aware chunking highest on top-K retrieval across four strategies (fixed-size sliding window, recursive, semantic breakpoint, structure-aware) at lower compute cost than the semantic and baseline strategies.
- **Rejected**: Fixed-size windows with overlap as the primary strategy — designed for unstructured text, they fragment articles and duplicate tokens across neighbours.
- **Pitfalls**: Unused parts are written "Not used" and produce empty chunks; Division 00 and 01 sections are explicitly exempted from the standard format, so a parser assuming three parts mis-handles exactly the SUBMITTALS section the product cares about; the PART 1 REFERENCES article is a dense designation list that embeds poorly and inflates cross-section near-duplicate similarity; agency variants of one MasterFormat number (`.00 10` USACE, `.00 20` NAVFAC, `.00 30` AFCEC, `.00 40` NASA) are separate documents whose chunks are near-identical.
- **Sources**: <https://nibs-s3-wbdg3-production.s3.us-east-1.amazonaws.com/tools/specsintact/Help/SIGeneral/SectionFormat.htm>, <https://arxiv.org/abs/2603.24556>

## Reconciling structural boundaries with the 256 word-piece cap

- **Decision**: Measure length in the pinned encoder's own tokenizer before embedding, and when a structural unit exceeds the cap, split recursively down the next structural level (article → paragraph → subparagraph → sentence), failing closed if a leaf still exceeds. Keep the parent article identifier on every child so retrieval can widen to the parent at read time rather than storing a larger chunk.
- **Rationale**: sentence-transformers truncates to `max_seq_length` with no signal — "longer texts will be truncated to the first `model.max_seq_length` tokens" — so a too-long chunk still yields a well-formed 384-vector representing only its head, and nothing downstream can detect it. Parent-document retrieval decouples retrieval granularity from generation context, reported at roughly 0.2 s added latency for a ~65% win rate.
- **Rejected**: Raising `max_seq_length` to 512 (reported to perform worse than truncating the same inputs to 256, since the model was trained at 256); sliding overlap as the primary mechanism, which multiplies chunk count and inflates near-duplicate similarity.
- **Pitfalls**: Character or word budgets are only a proxy — reference designations and part numbers explode into word pieces; the page constraint and the token constraint interact, so split on page first and structure second, or a structurally clean split will straddle a page and violate the scalar `page_number`; parent-widening gains little for isolated-fact queries and most for multi-hop ones.
- **Sources**: <https://sbert.net/examples/sentence_transformer/applications/computing-embeddings/README.html>, <https://zeroentropy.dev/concepts/parent-document-retrieval/>

## Per-field confidence in LLM extraction

- **Decision**: Treat a model-emitted per-field confidence as a routing hint only — never as a published metric, never as a storage gate. Any threshold must be fitted and reported against a labelled sample together with its discrimination (AUROC or equivalent), or the spec must state the score is unvalidated.
- **Rationale**: Verbalized confidence is systematically overconfident — models above 70B still deviate ~10% from true accuracy on average, and the best prompt formulation of 17 tested reached only ~7%; calibration depends heavily on *how* confidence is asked for, so the elicitation prompt is part of the measurement. In document field extraction specifically, token log-probabilities, verbalized confidence, and multi-sample self-consistency "all collapse toward all-positive behaviour at practical thresholds", giving no usable separation; a multi-signal engine fusing cross-call disagreement with layout and quality signals reached 0.928 ROC AUC on DocILE and 99.1% accuracy at 80% coverage, which is the bar a single self-reported number does not clear.
- **Rejected**: A fixed 0.8 or 0.9 cutoff with no measured discrimination; comparing confidence values across different fields as if they shared a scale.
- **Pitfalls**: Probability mass piled at one value (0.95, 1.0) makes every threshold either accept-all or reject-all, and a mean confidence figure hides it — publish the distribution, not the mean; a repair attempt shifts the distribution, so the `valid|repaired|failed` outcome must be stored beside the score or the two are conflated; confidence for an *absent* field is a different quantity from confidence for a present one.
- **Sources**: <https://arxiv.org/html/2412.14737v2>, <https://arxiv.org/abs/2606.24420>

## Line-item extraction from submittal transmittals

- **Decision**: Anchor the field set on ENG Form 4025 and UFGS 01 33 00 — transmittal number with revision suffix, contract number, referenced specification section number and paragraph, submittal descriptor `SD-01`…`SD-11` with the `G` government-approval tag, item description, manufacturer, model or part number, quantity, dates, and reviewer action code. Store manufacturer and part number as a raw-plus-normalized pair; the raw value is never overwritten.
- **Rationale**: The transmittal number itself carries the specification section as its first component, so the section link is a parsed field rather than an inference, and the decimal revision suffix is what makes a resubmittal chain reconstructable. Normalization is the join key: unnormalized casing, punctuation, or Unicode in a part number silently fractures an exact join, and the item disappears from the digital record with no error. Practitioners run fuzzy candidate generation with an explicit review band rather than a single cutoff — auto-merge only above a high similarity, route a middle band to human review.
- **Rejected**: Normalizing in place (destroys the evidence the citation points at); treating a manufacturer name as an identity key.
- **Pitfalls**: E002's derived `PAGE_SPLIT_FIELD` class — a label ending page *n* with its value on page *n+1* — is a direct collision with a scalar `chunk.page_number` and needs a stated rule before extraction, not after; near-duplicate resubmittals differ only in the revision suffix and will retrieve as duplicates; a missing SD descriptor or action stamp yields an item that exercises nothing downstream and should be recorded absent rather than defaulted.
- **Sources**: <https://www.wbdg.org/dod/ufgs/ufgs-01-33-00>, <https://www.publications.usace.army.mil/Portals/76/Publications/EngineerForms/Eng_Form_4025_2017May.pdf> (cached from E002; this host returns 403 to scripted retrieval)

## Validating parser page attribution

- **Decision**: Make page attribution a **total** check rather than a sampled one. pdfplumber carries `page_number` on the page and on every character object, so assert for every chunk that its normalized text is contained in that page's normalized extraction, under the same pinned word tolerances `src/model/src/model/corpus/derive.py` already fixes. Reserve human spot-checking for the claims a containment assertion cannot make.
- **Rationale**: A containment assertion whose denominator is the whole corpus needs no sampling argument at all. Where sampling is unavoidable, the rule of three states the honest bound: zero defects in *n* gives a 95% confidence interval of 0 to 3/n, so 30 clean samples bound the error rate only at 10%, and 60 at 5% — which is what "spot-check passed" is actually worth, and it should be published that way.
- **Rejected**: Accepting a page number the model returned, under any confidence; a sampled audit as the primary evidence when a total check is available.
- **Pitfalls**: Both sides must be normalized identically or containment fails spuriously — `derive.py` already fixes NFC plus whitespace collapse as the comparison form (`normalize_page_text`), and a second normalization would be a second answer; a chunk assembled from reordered words breaks containment even when the page is right, so compare on line-ordered word sequences rather than one page-wide string; page numbers are 1-based in pdfplumber and off-by-one is silent; the rule of three needs n > 30 to be a good approximation, so a 10-item spot-check supports no numeric claim.
- **Sources**: <https://github.com/jsvine/pdfplumber>, <https://jhanley.biostat.mcgill.ca/c607/ch08/zero_numerator.pdf>

## Retrieval evaluation-set stability under re-chunking

- **Decision**: Freeze the 50-item evaluation set on `(document, page, quoted span)` identity, not on chunk id, and hash *that* artifact. Resolve spans to chunk ids at harness run time; score a query as a hit when any retrieved chunk overlaps a labelled span.
- **Rationale**: A chunk id is a function of the chunker, so hashing chunk ids freezes the chunker as well as the evaluation set — a legitimate re-chunk then trips the hash gate for a reason the gate does not mean, and the pressure is to edit the frozen set, which is exactly what Principle VI exists to prevent. Independently, judgments built against one system are biased against systems that did not contribute to them: unjudged retrieved items scored as non-relevant systematically penalise the new chunker, and the unjudged share at rank k is large enough to move rankings.
- **Rejected**: Re-annotating after each chunker change (the set is no longer frozen, and the hash proves nothing); silently scoring unjudged retrieved chunks as non-relevant.
- **Pitfalls**: A span that a new chunker splits across two chunks changes what recall@k counts — fix the hit rule and the overlap predicate in writing before the first tuning run, since choosing it afterwards is tuning; publish the unjudged rate at k beside every metric, or figures from two chunker versions are not comparable; a span quoted with normalization applied will not resolve against chunks stored under different normalization.
- **Sources**: <https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=150469>, <https://dl.acm.org/doi/10.1145/1277741.1277755>

## Counting word pieces in the pinned encoder's tokenizer

- **Decision**: Load the tokenizer alone with `AutoTokenizer.from_pretrained(<pinned model id>, revision=<commit sha>)` and count `len(tokenizer(text)["input_ids"])`, leaving `add_special_tokens` at its default `True`. That is the number of pieces the model actually consumes. Cache the tokenizer once per process and encode in batches.
- **Rationale**: `from_pretrained` reads `tokenizer_config.json` for the tokenizer class and loads the serialized Rust pipeline from `tokenizer.json`; no model weights are fetched. The tokenizer files for a MiniLM-class encoder are well under a megabyte, load in well under a second, and the Rust backend parallelises batch encodes — so exact measurement is cheaper than any heuristic worth defending.
- **Rejected**: Character or word budgets scaled by a fudge factor; `tokenizer.tokenize()`, which returns pieces *without* `[CLS]`/`[SEP]` and so undercounts by 2 for BERT-family encoders; instantiating the full `SentenceTransformer` just to measure length.
- **Pitfalls**: `model_max_length` in `tokenizer_config.json` is `512` for `sentence-transformers/all-MiniLM-L6-v2`, while the effective sentence-transformers cap is `max_seq_length` `256` from `sentence_bert_config.json` — trusting the tokenizer's own field doubles the budget silently; the cap counts special tokens, so the content budget is 254, not 256; pin the tokenizer to the same revision as the encoder or the count and the truncation disagree; measure the exact string that will be embedded, after any normalization, not the pre-normalized source.
- **Sources**: <https://huggingface.co/docs/transformers/main/en/fast_tokenizers>, <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/tokenizer_config.json>

## Deterministic sentence segmentation for specification prose

- **Decision**: Use pySBD (`pysbd.Segmenter(language="en", clean=False, char_span=True)`), version-pinned, as the terminal split level below a paragraph. Keep character spans so each sentence maps back to an offset in the parent unit and the page-containment assertion still holds.
- **Rationale**: pySBD is purely rule-based — a regex cascade ported from the Ruby pragmatic-segmenter — with no model, no training data, and no download, so identical input yields identical output across runs and machines, which is what a reproducibility gate requires. It passes 97.92% of the English Golden Rules Set, about 25% above the next-best open-source Python tool, and the GRS exemplars are precisely the abbreviation, decimal-number, numbered-list and citation cases that naive splitters fail.
- **Rejected**: NLTK `punkt` (an unsupervised statistical model requiring a downloaded pickle, so behaviour is tied to a data artifact); spaCy's `parser`/`senter` (model download, heavier, statistical); a hand-rolled `[.!?]\s` regex, which breaks on `2.4.7`, `ASTM A653/A653M`, `No.`, and `approx.`.
- **Pitfalls**: pySBD is slow relative to a regex, so invoke it only on units that already exceed the cap, not on every paragraph; `clean=True` rewrites the text and would break both page containment and offset arithmetic — leave it off; the residual few percent of GRS failures are exactly designation-heavy strings, so a single "sentence" can still exceed the token cap and the fail-closed leaf rule must remain; the project is lightly maintained, so pin an exact version and treat an upgrade as a chunker-version bump.
- **Sources**: <https://github.com/nipunsadvilkar/pySBD>, <https://aclanthology.org/2020.nlposs-1.15/>

## Per-document transactional ingestion with psycopg 3

- **Decision**: Open one **autocommit** connection for the run and wrap each document in `with conn.transaction():`. Chunks, embeddings, extracted values, contributing-chunk rows, association rows, and any failure row for that document commit or roll back as one unit. Use `cursor.copy()` for the high-volume chunk and embedding inserts and `executemany` for the small child tables.
- **Rationale**: This is psycopg's own recommendation — "use an autocommit connection" plus "`with conn.transaction()` blocks to manage transactions only where needed". The block starts a transaction on entry and the transaction is committed, or rolled back if an exception is raised inside the block, which makes the document the unit of atomicity: an abort at document *k* leaves documents 1..*k*−1 committed and durable and document *k* entirely absent. The default non-autocommit connection begins an implicit transaction on first execute and commits at block exit, which would silently make the whole run a single transaction.
- **Rejected**: One transaction for the entire run (a late failure discards the whole batch and holds locks and bloat throughout); committing per row (loses per-document atomicity and admits half-ingested documents).
- **Pitfalls**: Nested `transaction()` blocks are savepoints, so a per-document error handler must catch *outside* the block or the inner rollback never happens; a failure row describing document *k* written inside *k*'s transaction is rolled back along with it — write it in a fresh transaction after the rollback; a failed statement poisons the rest of the transaction until rollback; `COPY` inside the block is transactional and rolls back with it; `psycopg.Rollback` abandons a block without propagating the exception.
- **Sources**: <https://www.psycopg.org/psycopg3/docs/basic/transactions.html>, <https://www.psycopg.org/psycopg3/docs/basic/copy.html>

## Generations of derived data with active/superseded state

- **Decision**: Add a generation table (run id, chunker version, encoder revision, status) with every chunk associated to it, and enforce "at most one active generation per document" with a partial unique index on `(document_id) WHERE status = 'active'`. Readers join through the generation or read a view that already filters it; nothing reads the chunk table unqualified. Promotion is one transaction: insert the new generation, set the old to `superseded`, set the new to `active`.
- **Rationale**: PostgreSQL documents the partial unique index for exactly this shape — a unique index over a subset of a table enforces uniqueness among the rows satisfying the predicate without constraining those that do not. That turns the invariant into a database guarantee: a second activation fails on write rather than producing two live generations that queries silently union. Keeping the superseded rows preserves the audit trail and makes a bad promotion reversible by flipping status back.
- **Rejected**: A bare `is_active` boolean with no index (concurrent promotions produce two actives); deleting the previous generation at promotion time (no rollback, no diff); a `max(version)` subquery re-implemented in every reader.
- **Pitfalls**: `ON DELETE RESTRICT` does not allow the check to be deferred whereas `NO ACTION` does — a retirement job therefore cannot delete parents before children inside one transaction and must delete strictly leaf-up (contributing-chunk rows → extracted values → chunks → generation); retire by age or retained-count in a separate job, never as part of promotion; constrain status with a `CHECK` so a typo cannot create a third state that no reader filters.
- **Sources**: <https://www.postgresql.org/docs/16/indexes-partial.html>, <https://www.postgresql.org/docs/16/ddl-constraints.html>

## Bulk-loading pgvector embeddings

- **Decision**: Load vectors with `COPY` through `cursor.copy()` with the pgvector psycopg 3 adapter registered, and build the HNSW index **after** the load rather than inside the per-document transaction.
- **Rationale**: pgvector is explicit that indexes should be added after loading the initial data for best performance, and points at `COPY` as the bulk path. Building the HNSW graph incrementally, one row-insert at a time, costs far more than a single bulk build. Before the build, raise `maintenance_work_mem` — indexes build significantly faster when the graph fits in it — and raise `max_parallel_maintenance_workers` above its default of 2, with `max_parallel_workers` raised to match.
- **Rejected**: Row-at-a-time `INSERT` for several thousand 384-dimension vectors; `CREATE INDEX CONCURRENTLY`, which is slower and buys availability this offline job does not need.
- **Pitfalls**: Index creation is DDL — keeping it in the per-document transaction would rebuild it per document and defeat the point; binary `COPY` requires source and target types to match exactly, so the declared `vector(384)` and the encoder's output dimension must agree or the load fails mid-stream; `m` and `ef_construction` drive build time as much as row count; between a drop and a rebuild every similarity query falls back to a sequential scan, which is acceptable offline but should be stated.
- **Sources**: <https://github.com/pgvector/pgvector>

## Wilson score intervals for per-field precision and recall

- **Decision**: Report every per-field precision and recall as a point estimate plus a 95% Wilson score interval, with the denominator printed beside it. Do not use the Wald/normal-approximation interval anywhere in the evaluation report.
- **Rationale**: Brown, Cai and DasGupta show the Wald interval's coverage is chaotically wrong and that the textbook rules for when it is "safe" cannot be trusted; they recommend the Wilson interval (or equal-tailed Jeffreys) for small *n*. Wilson is obtained by inverting the score test, is asymmetric, and keeps both bounds inside [0, 1] by construction, so it does not overshoot or produce zero-width intervals. Per-field denominators here are frequently under 20, which is precisely the regime where Wald misleads.
- **Rejected**: The normal-approximation interval; a bare point estimate with no interval; pooling distinct fields together to manufacture a larger *n*.
- **Pitfalls**: At 0 successes Wald collapses to [0, 0] and at *n* successes to [1, 1] — "100% precision" from 7 of 7 — whereas Wilson returns a real interval that makes the small denominator visible; Wilson still under-covers at extreme proportions for very small *n* unless continuity-corrected, so state which variant is used and use it consistently; precision and recall have different denominators, so their interval widths are not comparable; an interval presupposes a random sample, and a deliberately adversarial evaluation set is not one — label such figures descriptive rather than inferential.
- **Sources**: <https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full>, <https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval>

## Summary

Three constraints in this feature are structural rather than tunable and should surface as acceptance criteria: the encoder truncates silently, so chunk length must be measured in the encoder's own tokenizer and a too-long leaf must fail rather than embed; page attribution is verifiable exhaustively from pdfplumber's per-object page number, so a sampled audit is a weaker claim than the one available for free; and a chunk-id-keyed frozen evaluation set freezes the chunker by accident, so span-keyed identity with run-time resolution is the version that survives re-chunking. Model self-reported confidence is the weakest signal in the design — it is defensible as a review-routing hint and indefensible as a published number, so the spec should say which one it is. The clarification pass resolved this by computing confidence from parse signals instead, which moves it to the code side of the computation boundary.

On the implementation stack, four choices carry the most weight. The tokenizer is loadable standalone for under a megabyte and no weights, so exact word-piece counting is affordable — but `model_max_length` (512) is not the effective cap (256), and reading the wrong field is the likeliest way to ship silent truncation. An autocommit connection with `with conn.transaction()` per document is psycopg's own recommended shape and is exactly what makes a mid-run abort leave earlier documents intact; the corollary is that a failure row must be written *after* the rollback, in its own transaction. "One active generation per document" should be a partial unique index rather than application discipline, and because `ON DELETE RESTRICT` cannot be deferred, retirement must delete leaf-up. Finally, the HNSW index belongs after the load rather than inside the per-document transaction, and every per-field metric should carry a Wilson interval so that a 7-of-7 result is not read as certainty.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://nibs-s3-wbdg3-production.s3.us-east-1.amazonaws.com/tools/specsintact/Help/SIGeneral/SectionFormat.htm> | structure-aware chunking | 2026-07-27 |
| <https://arxiv.org/abs/2603.24556> | structure-aware chunking | 2026-07-27 |
| <https://sbert.net/examples/sentence_transformer/applications/computing-embeddings/README.html> | structural boundaries vs encoder cap | 2026-07-27 |
| <https://zeroentropy.dev/concepts/parent-document-retrieval/> | structural boundaries vs encoder cap | 2026-07-27 |
| <https://arxiv.org/html/2412.14737v2> | per-field confidence | 2026-07-27 |
| <https://arxiv.org/abs/2606.24420> | per-field confidence | 2026-07-27 |
| <https://www.wbdg.org/dod/ufgs/ufgs-01-33-00> | line-item extraction | 2026-07-25 (E002 cache) |
| <https://www.publications.usace.army.mil/Portals/76/Publications/EngineerForms/Eng_Form_4025_2017May.pdf> | line-item extraction | 2026-07-25 (E002 cache) |
| <https://github.com/jsvine/pdfplumber> | page-attribution validation | 2026-07-27 |
| <https://jhanley.biostat.mcgill.ca/c607/ch08/zero_numerator.pdf> | page-attribution validation | 2026-07-27 |
| <https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=150469> | evaluation-set stability | 2026-07-27 |
| <https://dl.acm.org/doi/10.1145/1277741.1277755> | evaluation-set stability | 2026-07-27 |
| <https://huggingface.co/docs/transformers/main/en/fast_tokenizers> | standalone tokenizer counting | 2026-07-27 |
| <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/tokenizer_config.json> | standalone tokenizer counting | 2026-07-27 |
| <https://github.com/nipunsadvilkar/pySBD> | deterministic sentence segmentation | 2026-07-27 |
| <https://aclanthology.org/2020.nlposs-1.15/> | deterministic sentence segmentation | 2026-07-27 |
| <https://www.psycopg.org/psycopg3/docs/basic/transactions.html> | per-document transactional ingestion | 2026-07-27 |
| <https://www.psycopg.org/psycopg3/docs/basic/copy.html> | per-document transactional ingestion | 2026-07-27 |
| <https://www.postgresql.org/docs/16/indexes-partial.html> | active/superseded generations | 2026-07-27 |
| <https://www.postgresql.org/docs/16/ddl-constraints.html> | active/superseded generations | 2026-07-27 |
| <https://github.com/pgvector/pgvector> | pgvector bulk loading | 2026-07-27 |
| <https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full> | Wilson score intervals | 2026-07-27 |
| <https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval> | Wilson score intervals | 2026-07-27 |
