# Research: Line Detail and Traceability

> Feature: E012 Line Detail and Traceability | Date: 2026-07-30 | Purpose: ground the encoding of a single line's posterior predictive distribution, the deadline annotation, the day-grid survival array, the accessible equivalent, the covariate panel, and the source-navigation links — for a procurement coordinator who is not a statistician and who must be prevented from reading any of it as a single date.

## Which encoding of a predictive distribution resists point-estimate reading

- **Decision**: Make a quantile dotplot of the stored draws the primary encoding. No error bar, no violin, no bare density, and no drawn central mark of any kind.
- **Rationale**: In a controlled incentivized experiment where subjects chose when to catch a bus, decisions made with quantile dotplots of 50 outcomes reached expected payoffs 97% of optimal (95% CI [95%, 98%]), five points above a no-uncertainty control (95% CI [2, 8]); CDF plots performed nearly as well, and both beat textual uncertainty, which was itself sensitive to which probability interval was quoted. Separately, symmetric non-binary encodings — gradient and violin — removed the within-the-bar bias that bar-plus-error-bar produces, in which outcomes falling inside the bar are judged likelier than equally probable outcomes outside it; the violin's extra distributional detail bought nothing over the gradient.
- **Pitfalls**: An error bar or any two-ended interval is read as hard bounds with nothing outside. Continuous encodings — density, gradient, fan — support no counting, so the reader has no way to check a stated probability against the picture. Textual uncertainty alone is fragile to the interval chosen. The forbidden mean, mode or lone quantile is not merely redundant here: it would become the most salient mark in the figure and therefore the one the reader answers from.
- **Sources**: https://www.mjskay.com/papers/chi2018-uncertain-bus-decisions.pdf, https://graphics.cs.wisc.edu/Papers/2014/CG14/Preprint.pdf

## Discrete outcomes versus continuous density, and how many marks

- **Decision**: Discretise to 50 dots, each dot standing for 2 in 100 comparable orders, with the denominator named in a sentence beside the plot rather than in a legend.
- **Rationale**: A quantile dotplot is the inverse CDF evaluated at evenly spaced probabilities, so a one-sided probability is answered by counting — the second dot from the left is where you arrive if you are willing to miss twice in twenty tries. That counting property is why frequency framing survives translation into a picture. 50 was the best-performing tested condition. On the frequency side, icon arrays over a stated denominator eliminated denominator neglect and improved comprehension of risk-reduction statistics for low-numeracy adults specifically, not only for the numerate.
- **Pitfalls**: The tested conditions were 20 and 50; nothing above 50 has measured support, so a 100-mark or full-draw plot is an unevidenced extrapolation and must not be presented as following from this finding. The project's own measured field failure — "50 in 100" read as fifty of the hundred parts on that line — is a denominator failure, not a dot-count failure, and no dot count fixes it. The denominator sentence must name *comparable orders* and sit next to the figure.
- **Sources**: https://www.mjskay.com/papers/chi2018-uncertain-bus-decisions.pdf, https://pure.mpg.de/rest/items/item_2099767_4/component/file_2562291/content

## Annotating the need-by date on the distribution

- **Decision**: Mark the need-by date on the dotplot, shade the mass beyond it, *and* print that mass as a frequency sentence in both directions. Shading alone is not sufficient, and the printed figure alone is not sufficient either.
- **Rationale**: Across three incentivized road-salting experiments, participants given an explicit numeric likelihood of the threshold event — temperature falling below freezing — made more accurate decisions and trusted the forecast more than participants given only a single nighttime-low estimate. Printing the number is therefore load-bearing. Keeping it beside discretised draws is what makes it checkable: the reader can count marks past the line and confirm the sentence rather than take it on faith.
- **Pitfalls**: Annotation genuinely does reintroduce deterministic reading, because the salient mark is what viewers judge from. Cone-of-uncertainty viewers read the expanding boundary as the storm growing, judging size 0.69 units higher than ensemble viewers; ensemble viewers in turn over-weighted a single track when a point of interest fell on it, dropping from near-unanimous correct choice to 54–64%. So the need-by mark must never be the only labelled feature and must never be worded as a target or an expected date. Evidence directly comparing shaded mass against a separately stated figure was not found — requiring both is inference from these two results, not a measured comparison.
- **Sources**: https://www.apa.org/pubs/journals/features/xap-18-1-126.pdf, https://pmc.ncbi.nlm.nih.gov/articles/PMC5626802/

## The day-grid array as a cumulative or survival curve

- **Decision**: Where the day grid is rendered, render it as an increasing cumulative curve — delivered by day k — with the survival complement stated in words. Keep it secondary to the dotplot, and never let it be the only encoding.
- **Rationale**: The evidence is split and should be treated that way. CDF plots performed nearly as well as the best quantile dotplot in the incentivized transit study. But in a cross-sectional survey of 177 chronic-kidney-disease patients shown four formats of the same survival data, the Kaplan-Meier curve had the lowest correct-interpretation rate at 69% — against 81% for a pictograph and 79% for a histogram — and was least preferred, at 12%.
- **Pitfalls**: The two results are not contradictory but nor are they comparable: one measures an incentivized threshold lookup, the other unaided interpretation. Lay comprehension of a survival curve should be recorded as unresolved rather than assumed. The stored array is a negated, decreasing quantity and carries double the reading load of its complement. Residual mass beyond the horizon must be published as a labelled quantity; a curve terminating above zero is read as terminating at zero.
- **Sources**: https://www.mjskay.com/papers/chi2018-uncertain-bus-decisions.pdf, https://pmc.ncbi.nlm.nih.gov/articles/PMC5607842/

## Accessible equivalents for the probabilistic figures

- **Decision**: Ship a structured alternative, not alt text — a short summary naming the reference class, the labelled quantile pair, the miss probability in both directions, the residual mass, and a coarse banded table of the day grid — associated with the figure semantically and visible to everyone, not only to screen readers.
- **Rationale**: W3C guidance for complex images is explicitly two-part: a short identification plus a long description carrying the essential information the image conveys, where that description may include headings and a table, and it recommends making the long description available to all readers. Interviews with blind screen-reader users found alt text forces the reader to accept the author's interpretation with no ability to drill down, and that data tables — the standard accessible fallback — impose high cognitive load because the reader must hold prior rows in memory; participants nonetheless wanted them, for their standardised navigation. The recommendation was tables alongside a navigable structure, not as a replacement.
- **Pitfalls**: A 365-row table is not an equivalent, and several thousand draws are not an equivalent. Banding the grid is the substantive design decision and needs a stated basis. A textual summary that collapses to one date is exactly the regression the chart exists to avoid — the accessible path is the easiest place for a point estimate to re-enter unnoticed.
- **Sources**: https://www.w3.org/WAI/tutorials/images/complex/, https://vis.mit.edu/pubs/rich-screen-reader-vis-experiences

## Presenting the covariates that drove the forecast

- **Decision**: Show the covariates as named model inputs with their observed values and a plain statement that they are associated with slower delivery in comparable orders, not causes of it. Do not publish per-covariate contribution percentages.
- **Rationale**: In pre-registered randomized experiments with roughly 3,800 participants on functionally identical models, showing a clear model with few input features improved participants' ability to simulate its predictions but produced no improvement in following the model when doing so was beneficial — and increased transparency actively hampered their ability to notice and correct a sizable model mistake, apparently through information overload. Separately, SHAP's own documentation records that attribution makes a predictive model's correlations transparent without making them causal, and that both observed and unobserved confounding redistribute credit across correlated features.
- **Pitfalls**: A bare "approval cycles: 3" invites the conclusion that removing one cycle moves the date, which the model does not support. Contribution percentages assert a decomposition that confounding makes unreliable and that reads as precision. More explanation is not monotonically better, and the measured harm was to error detection specifically — the thing a demonstration surface most wants to preserve.
- **Sources**: https://arxiv.org/pdf/1802.07810, https://shap.readthedocs.io/en/latest/example_notebooks/overviews/Be%20careful%20when%20interpreting%20predictive%20models%20in%20search%20of%20causal%20insights.html

## Provenance links back to the originating document page

- **Decision**: Every linked specification, submittal and purchase-order record resolves to document, page, and the extracted span — placed next to the figure it supports and labelled with the document title and page number, never a bare "Source". Treat citation precision as a measured property of the feature rather than an assumption.
- **Rationale**: A human audit of four generative search engines found only 51.5% of generated statements fully supported by their citations and only 74.5% of citations supporting the statement they were attached to. A link that looks right frequently is not, which is why link quality has to be measured rather than asserted. Usability work reaches the complementary conclusion from the reader's side: the mere presence of a citation raises confidence in the output while readers rarely click it, so the recommended patterns are placing the citation adjacent to the specific claim, deep-linking to the relevant section rather than the document, and using meaningful labels.
- **Pitfalls**: A citation that raises trust without being checked is a liability rather than a feature, because it earns confidence it has not tested. Linking to a whole document rather than a page transfers the verification cost to the reader, which is the same failure in slower form. Step-by-step narratives of how a model reasoned are post-hoc rationalisations and must not substitute for a resolvable link.
- **Sources**: https://aclanthology.org/2023.findings-emnlp.467/, https://www.nngroup.com/articles/explainable-ai/

## Open questions carried into the spec

- No source gives an evidenced mark count above 50. The dot count is evidenced *at* 50 and not beyond, and the spec states that rather than implying a general rule about discretisation.
- Whether the day grid is charted at all for a coordinator, or offered only as a banded table, is unresolved by the evidence above.
- No evidence was found comparing shaded threshold mass against a separately stated figure. Requiring both is the conservative reading and is recorded as inference under Principle VII, not cited as a result.
- Whether the day bands in the accessible table follow a fixed calendar grid or the quantiles of the posterior is a product decision with a visible consequence for which figures a screen-reader user can read off directly.
- Page-level citation storage is owned by E006; E012 consumes it and must not assert provenance the ingestion boundary did not derive.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| https://www.mjskay.com/papers/chi2018-uncertain-bus-decisions.pdf | encoding, discretisation, cumulative curves | 2026-07-30 |
| https://graphics.cs.wisc.edu/Papers/2014/CG14/Preprint.pdf | encoding | 2026-07-30 |
| https://pure.mpg.de/rest/items/item_2099767_4/component/file_2562291/content | discretisation and frequency framing | 2026-07-30 |
| https://www.apa.org/pubs/journals/features/xap-18-1-126.pdf | threshold annotation | 2026-07-30 |
| https://pmc.ncbi.nlm.nih.gov/articles/PMC5626802/ | threshold annotation | 2026-07-30 |
| https://pmc.ncbi.nlm.nih.gov/articles/PMC5607842/ | cumulative curves | 2026-07-30 |
| https://www.w3.org/WAI/tutorials/images/complex/ | accessible equivalents | 2026-07-30 |
| https://vis.mit.edu/pubs/rich-screen-reader-vis-experiences | accessible equivalents | 2026-07-30 |
| https://arxiv.org/pdf/1802.07810 | covariate presentation | 2026-07-30 |
| https://shap.readthedocs.io/en/latest/example_notebooks/overviews/Be%20careful%20when%20interpreting%20predictive%20models%20in%20search%20of%20causal%20insights.html | covariate presentation | 2026-07-30 |
| https://aclanthology.org/2023.findings-emnlp.467/ | provenance links | 2026-07-30 |
| https://www.nngroup.com/articles/explainable-ai/ | provenance links | 2026-07-30 |
