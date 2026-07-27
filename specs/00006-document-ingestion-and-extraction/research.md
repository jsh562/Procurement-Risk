# Research: Document Ingestion and Extraction

> Feature E006 `00006-document-ingestion-and-extraction` | 2026-07-27 | Purpose: ground chunk-boundary policy, the encoder-cap interaction, per-field confidence semantics, line-item field sets, page-provenance validation, and evaluation-set stability for a product spec

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

## Summary

Three constraints in this feature are structural rather than tunable and should surface as acceptance criteria: the encoder truncates silently, so chunk length must be measured in the encoder's own tokenizer and a too-long leaf must fail rather than embed; page attribution is verifiable exhaustively from pdfplumber's per-object page number, so a sampled audit is a weaker claim than the one available for free; and a chunk-id-keyed frozen evaluation set freezes the chunker by accident, so span-keyed identity with run-time resolution is the version that survives re-chunking. Model self-reported confidence is the weakest signal in the design — it is defensible as a review-routing hint and indefensible as a published number, so the spec should say which one it is.

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
