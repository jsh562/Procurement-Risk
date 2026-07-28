"""The deterministic per-vendor template baseline (FR-050, Principle VIII).

**Empty on purpose, and not for long.** T051 authors the extractor here; this
module exists ahead of it because the contract that governs it names it.
`src/model/pyproject.toml` declares "The baseline extractor does not reach the
corpus generator" as an import-linter forbidden contract with this module as its
source, and import-linter validates source modules against the import graph
before it checks anything — a contract naming a module that does not exist does
not merely pass vacuously, it raises `Module 'model.ingest.baseline' does not
exist.` and takes every other contract in the file down with it.

The alternative was to declare the contract only once the extractor was written.
That is the wrong order: the contract is what makes FR-050's **declared** label
("strong — authored under the independence contract") a checked claim rather
than an assertion, and the declared label must be fixed *before any figure
exists*. A rule introduced after the code it governs has already been written
audits nothing; it ratifies whatever was done.

So, the terms this file is authored under, stated before there is anything to
author: the baseline reads the **rendered** documents only. It may not import
`model.corpus.templates` (the per-vendor generation source), `model.corpus.render`
(what turns a document model into the PDF), or `model.corpus.model` (the
pre-render document model). Those three are the answer key, and an opponent
reading the answer key cannot lose — which would make every quality figure
published beside it flattery rather than evidence.
"""
