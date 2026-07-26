# Research: Public Corpus and Manifest

> Feature `00002-public-corpus-and-manifest` | 2026-07-25 | Purpose: ground the corpus's provenance model, its synthetic layer's fidelity, and the Python toolchain that renders and validates it

## Public-domain basis for federal guide specifications

- **Decision**: Record a license basis as governing statute, issuing body, exact section identifier with revision date, retrieval URL and date, and the outcome of a point-of-use copyright check — never a bare "public domain (federal work)".
- **Rationale**: 17 U.S.C. §105(a) removes copyright from works *of* the government but permits assigned copyrights and leaves contractor-produced works to agency discretion, so "federal, therefore public domain" is an assumption rather than a basis.
- **Rejected**: Treating a MasterFormat number as document identity — the same number ships as separately dated agency variants (`.00 10` USACE, `.00 20` NAVFAC, `.00 40` NASA).
- **Pitfalls**: The UFC copyright notice identifies copyrighted material at point of use; UFGS REFERENCES articles cite ASTM/NEMA/IEEE by designation and title only, so verbatim vendoring is clean, but the per-document check is what makes that defensible rather than asserted.
- **Sources**: <https://www.law.cornell.edu/uscode/text/17/105>, <https://www.wbdg.org/dod/ufgs>

## Corpus manifest and provenance-record practice

- **Decision**: Define REAL/SYNTHETIC as a required closed enum of the project's own, with required-field validation that fails rather than defaults, and one manifest per corpus location.
- **Rationale**: No interchange format carries a real/synthetic field, and Croissant binds `license` at dataset level only, which makes "one license per location, one manifest per location" the natural expression of the no-mixed-licenses rule rather than an extra rule.
- **Rejected**: Optional provenance fields — a defaulted license basis is indistinguishable downstream from a verified one.
- **Pitfalls**: DCAT-3 permits distribution-level licensing, which would tempt a single manifest spanning license bases; the synthetic layer additionally needs a Datasheets-for-Datasets-style document rather than manifest fields alone.
- **Sources**: <https://docs.mlcommons.org/croissant/docs/croissant-spec.html>, <https://www.w3.org/TR/vocab-dcat-3/>

## Submittal and transmittal document structure

- **Decision**: Every synthetic document carries a transmittal number, referenced specification section, submittal descriptor code, approving-authority marker, revision suffix, and reviewer action stamp.
- **Rationale**: ENG Form 4025 organizes a transmittal into contractor request, approval action with a per-item review code, and government remarks, and the decimal revision suffix on a transmittal number is what makes a resubmittal chain reconstructable.
- **Rejected**: Reproducing the live form's exact code letters — the current revision returned 403 to automated retrieval, so an unverified set presented as real would be an unsupportable provenance claim.
- **Pitfalls**: Descriptors run SD-01 through SD-11 and a `G` tag marks government-approval items; a document missing the descriptor or the action stamp exercises nothing downstream.
- **Sources**: <https://www.wbdg.org/dod/ufgs/ufgs-01-33-00>, <https://www.publications.usace.army.mil/Portals/76/Publications/EngineerForms/Eng_Form_4025_2017May.pdf>

## Realism risk in a synthetic document layer

- **Decision**: Keep the layers separately identifiable in the manifest and record per-document irregularity classes, so no quality metric is ever computed on generated material alone without being labelled.
- **Rationale**: Where a generator overfits, the overfitting is copied into the synthetic test set and inflates measured performance through a synthetic-specific form of leakage.
- **Rejected**: Pooling real and synthetic results — DocILE's precedent distributes them as separate components and reports synthetic subsets separately by layout cluster.
- **Pitfalls**: Uniform templates across vendors make chunking and retrieval look better than they are; the levers that matter are layout variety, missing fields, inconsistent naming, out-of-order dates, and near-duplicate resubmittals.
- **Sources**: <https://arxiv.org/abs/2305.09235>, <https://arxiv.org/abs/2302.05658>

## MasterFormat coverage for long-lead equipment

- **Decision**: State coverage against high-lead-time sections rather than division count — Division 26 unit substations, medium-voltage transformers and switchgear, low-voltage switchgear, switchboards, generators; Division 23 chillers and air handling.
- **Rationale**: Lead times as of June 2026 span 128 weeks for a power transformer and 144 for a generator step-up against 15–23 for panelboards, so uniform per-division quotas would spend corpus budget on sections that do not constrain a schedule.
- **Rejected**: Counting agency-suffixed variants of one number as separate sections toward coverage — they are separate documents but one section.
- **Pitfalls**: Coverage arithmetic has to state whether variants count once or individually before any criterion depends on the number.
- **Sources**: <https://www.wbdg.org/ffc/dod/unified-facilities-guide-specifications-ufgs/ufgs-26-23-00>, <https://usevawn.com/resources/electrical-equipment-lead-times/>

## Reproducible PDF generation

- **Decision**: ReportLab with `invariant=True`, an explicit producer, and exact `reportlab` and `pillow` pins in the modeling entry's lockfile.
- **Rationale**: `invariant` feeds one fixed timestamp into `CreationDate`, `ModDate`, and the md5 that becomes the `/ID` trailer, so the identifier is content-derived rather than clock- or build-path-seeded.
- **Rejected**: WeasyPrint, whose `SOURCE_DATE_EPOCH` reproducibility breaks once a CSS background image is present — exactly the degraded-scan case; and fpdf2, which leaves its default file-id path internal and verifies through qpdf normalization rather than raw bytes.
- **Pitfalls**: Font subset prefixes are assigned by subset order, so a glyph-set change reshuffles tags across the whole file — which is why the reproducibility criterion hashes the pre-render document model and treats byte-identity as a secondary check under the pin.
- **Sources**: <https://reproducible-builds.org/docs/source-date-epoch/>, <https://www.reportlab.com/docs/reportlab-userguide.pdf>

## Degradation without forcing recognition

- **Decision**: Draw the degraded raster over the body region only, overlay the same body text at text render mode 3 (invisible), and draw the citation anchor as an ordinary visible text object outside the raster rectangle.
- **Rationale**: This is the searchable-scan construction that a genuinely filed scanned submittal already is, so "visually degraded, still fully text-extractable" matches production reality rather than evading it.
- **Rejected**: Augraphy, whose dependency set pulls matplotlib, numba, opencv, scikit-image, scipy and a network client into a job required to run offline — plain Pillow operations cover five degradation classes and Pillow arrives with ReportLab anyway.
- **Pitfalls**: A real recognition text layer carries character errors and broken reading order while a synthesized one is perfect, so the datasheet must disclose that the corpus evidences no robustness to genuine scan noise.
- **Sources**: <https://arxiv.org/abs/2208.14558>, <https://ocrmypdf.readthedocs.io/en/latest/introduction.html>

## Extraction for independent validation

- **Decision**: pdfplumber, pinned exactly, using `extract_words()` for geometry-dependent checks and per-page `extract_text()` for text-only checks.
- **Rationale**: Only word-level bounding boxes can prove that a label and its value landed on opposite sides of a page break, which the structural re-derivation requires.
- **Rejected**: pypdf, whose word geometry is reachable only through a low-level visitor callback, and pdfminer.six used directly, which is the same engine with more layout boilerplate and no dependency saving.
- **Pitfalls**: This adds `charset-normalizer` and `cryptography` transitively to the modeling entry — a reviewed addition, not an incidental one — and word-splitting tolerances must be pinned explicitly since the defaults are not a stable contract.
- **Sources**: <https://pypi.org/project/pdfplumber/>, <https://pypi.org/pypi/pdfminer.six/json>

## Manifest schema validation

- **Decision**: `jsonschema[format-nongpl]` with `Draft202012Validator`, reporting failures through `iter_errors()` as document identifier plus `json_path` plus `validator` plus message.
- **Rationale**: The requirement is to name both the offending document and the violated rule, and `iter_errors()` yields every failure independently while `best_match()` collapses siblings and would hide concurrent defects.
- **Rejected**: fastjsonschema, which collapses failures into one message with no structured instance path, and pydantic, which is code-first and produces no publishable schema artifact to check the manifest contract against.
- **Pitfalls**: Format assertions are off by default and require an explicit `FormatChecker`; `date-time` needs `rfc3339-validator` and `uri` needs a non-GPL validator, which the `format-nongpl` extra supplies.
- **Sources**: <https://python-jsonschema.readthedocs.io/en/stable/validate/>, <https://python-jsonschema.readthedocs.io/en/stable/errors/>

## Retrieving and vendoring the real layer

- **Decision**: A committed retrieval script for WBDG-hosted sections, with manual retrieval plus a recorded digest for any host that blocks scripted access.
- **Rationale**: WBDG's `robots.txt` allows everything except `/auth/`, and its UFGS URLs 301-redirect to a public storage origin that served a real PDF to a non-browser client — so the earlier 403 is host-specific and must not be generalized.
- **Rejected**: Spoofing a user agent to defeat the USACE forms host's 403, and making retrieval any part of the generation job, which must run offline.
- **Pitfalls**: The script must follow the cross-host redirect, must stay out of the test path so no required check depends on the network, and cannot be the recorded provenance for a document it was never able to fetch.
- **Sources**: <https://www.wbdg.org/robots.txt>, <https://www.publications.usace.army.mil/Portals/76/Publications/EngineerForms/Eng_Form_4025_2017May.pdf>

## Summary

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Public-domain basis | Statute, section identifier with revision date, retrieval record, point-of-use check outcome | "Federal, therefore public domain" is an assumption, not a basis |
| Manifest practice | Project-defined REAL/SYNTHETIC enum; required fields; one manifest per location | No standard carries the field, and dataset-level licensing makes per-location the natural unit |
| Document structure | Six structural fields per synthetic document | A document missing the descriptor or action stamp exercises nothing downstream |
| Realism risk | Per-document irregularity classes, layers separately identifiable | Synthetic-specific leakage inflates measured performance invisibly in a pooled metric |
| MasterFormat coverage | Target high-lead-time sections, not division count | Lead times span 15 to 144 weeks; uniform quotas waste corpus budget |
| PDF generation | ReportLab `invariant=True`, exact pins | Only generator whose determinism survives compositing a raster |
| Degradation | Raster body, invisible text, anchor outside the rectangle | Mirrors a real searchable scan; keeps recognition out of scope |
| Extraction | pdfplumber word boxes | Page-split detection needs geometry, not text alone |
| Schema validation | `jsonschema` draft 2020-12, `iter_errors()` | Must name document and rule; `best_match()` hides siblings |
| Retrieval | Committed script for WBDG, manual fallback | WBDG is scriptable; the 403 was another host |

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://www.law.cornell.edu/uscode/text/17/105> | public-domain basis | 2026-07-25 |
| <https://www.wbdg.org/dod/ufgs> | public-domain basis | 2026-07-25 |
| <https://docs.mlcommons.org/croissant/docs/croissant-spec.html> | manifest practice | 2026-07-25 |
| <https://www.w3.org/TR/vocab-dcat-3/> | manifest practice | 2026-07-25 |
| <https://www.wbdg.org/dod/ufgs/ufgs-01-33-00> | document structure | 2026-07-25 |
| <https://www.publications.usace.army.mil/Portals/76/Publications/EngineerForms/Eng_Form_4025_2017May.pdf> | document structure, retrieval | 2026-07-25 |
| <https://arxiv.org/abs/2305.09235> | realism risk | 2026-07-25 |
| <https://arxiv.org/abs/2302.05658> | realism risk | 2026-07-25 |
| <https://www.wbdg.org/ffc/dod/unified-facilities-guide-specifications-ufgs/ufgs-26-23-00> | MasterFormat coverage | 2026-07-25 |
| <https://usevawn.com/resources/electrical-equipment-lead-times/> | MasterFormat coverage | 2026-07-25 |
| <https://reproducible-builds.org/docs/source-date-epoch/> | PDF generation | 2026-07-25 |
| <https://www.reportlab.com/docs/reportlab-userguide.pdf> | PDF generation | 2026-07-25 |
| <https://arxiv.org/abs/2208.14558> | degradation | 2026-07-25 |
| <https://ocrmypdf.readthedocs.io/en/latest/introduction.html> | degradation | 2026-07-25 |
| <https://pypi.org/project/pdfplumber/> | extraction | 2026-07-25 |
| <https://pypi.org/pypi/pdfminer.six/json> | extraction | 2026-07-25 |
| <https://python-jsonschema.readthedocs.io/en/stable/validate/> | schema validation | 2026-07-25 |
| <https://python-jsonschema.readthedocs.io/en/stable/errors/> | schema validation | 2026-07-25 |
| <https://www.wbdg.org/robots.txt> | retrieval | 2026-07-25 |
