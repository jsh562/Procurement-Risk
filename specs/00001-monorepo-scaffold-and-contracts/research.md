# Research — Monorepo Scaffold and Contracts

## Python dependency isolation across the three Python entries

A uv *workspace* is the opposite of what this epic needs: one root lockfile, one shared resolution, one virtual environment, and an intersected `requires-python`. Astral states plainly that uv cannot ensure workspace members avoid importing each other's dependencies. Use **three independent uv projects** — the serving boundary, the modeling boundary, and the shared gateway package — each with its own `pyproject.toml`, `uv.lock`, and `.venv`. Never declare the modeling package as a path dependency of the serving package; that pulls its full transitive graph into the serving resolution. Both boundaries do declare the gateway as a path dependency, which is safe only because the gateway carries no web framework and no modeling stack, so its transitive graph adds nothing either boundary must exclude. Gate with `uv lock --check` and `uv sync --locked`; exact-sync semantics are what the image assertion later relies on.

### Sources
- https://docs.astral.sh/uv/concepts/projects/workspaces/ — shared-lockfile model and the explicit no-isolation caveat
- https://docs.astral.sh/uv/concepts/projects/sync/ — `--locked` / `--check` and exact-sync behaviour

## Enforcing a single import site

The correct import-linter contract is **`protected`**, not `forbidden`. A `forbidden` contract detects indirect imports by default, so applied to "one module may import the SDK" it is unsatisfiable — every module transitively reaching the wrapper is flagged. `protected` names the SDK and allowlists the single wrapper, and checks direct imports only. Keep indirect detection on for the separate computation-boundary rule, where transitivity is the point. Config belongs in each Python entry's own `pyproject.toml` — the two boundaries and the gateway package alike — run from that entry's environment, because the root package must be importable. A single repo-root config would require every stack installed together, defeating the isolation being proved. The gateway's own config is where the allowlist naming its single permitted importer lives.

### Sources
- https://import-linter.readthedocs.io/en/stable/contract_types/protected/ — allowlist semantics, direct-imports-only scope
- https://import-linter.readthedocs.io/en/stable/contract_types/forbidden/ — indirect-import defaults

## Asserting package absence in a built image

Assert an **allowlist**, not a denylist: the image's installed distribution set must equal the set derived from the serving boundary's lockfile. A denylist of specific modelling package names is a false-negative machine. The structural guarantee is the Docker build context scoped to the serving boundary only, so the modelling package is unreachable at build time; the test detects regressions rather than creating the guarantee. Named blind spots: vendored or copied source carries no distribution metadata and is invisible to metadata queries; distribution names differ from import names; transitive dependencies arrive under unrelated names.

### Sources
- https://github.com/GoogleContainerTools/container-structure-test — image assertion types and drivers
- https://docs.python.org/3/library/importlib.metadata.html — distribution-versus-import name distinction

## Web application in a repository subdirectory

Next.js has no repository-root assumption — the project root is wherever the config file sits, and output file tracing already defaults to that directory, so sibling Python directories are excluded automatically. The hazard is root *inference*: recent versions warn when multiple lockfiles are present, and a misdetected root has caused broken module resolution. Pin the tracing and bundler roots explicitly and keep exactly one JavaScript lockfile inside the web boundary. Hosting configuration sets a per-project root directory. Do not create both an app directory and a nested source app directory — one silently wins.

### Sources
- https://nextjs.org/docs/app/api-reference/config/next-config-js/output — tracing root defaults in subdirectory layouts
- https://vercel.com/docs/monorepos — per-project root directory configuration

## Requirements-quality dimension vocabulary

ISO/IEC/IEEE 29148:2018 splits characteristics into **individual** — necessary, appropriate, unambiguous, complete, singular, feasible, verifiable, correct, conforming — and **set** — complete, consistent, feasible, comprehensible, able to be validated. Coverage-and-gap questions take set characteristics; wording questions take individual ones. Traceability is a requirement *attribute*, not a characteristic, so trace-style items are tagged as such. ISO/IEC 25010 is a product quality model and must not be used to judge requirement wording; ISO/IEC 25012 supplies the data-specific set — accuracy (syntactic and semantic), completeness, consistency, credibility, currentness, plus traceability, precision, understandability, compliance.

### Sources
- https://cdn.standards.iteh.ai/samples/72089/62bb2ea1ef8b4f33a80d984f826267c1/ISO-IEC-IEEE-29148-2018.pdf — both characteristic sets
- https://iso25000.com/index.php/en/iso-25000-standards/iso-25012 — data quality characteristics

## Quality criteria for build-time enforcement requirements

No standard addresses requirements for rules that must *fail* rather than features that must work, so the criteria derive from two anchors. Verifiability requires a stated means of measurement, so each enforcement rule must name its detection mechanism and its failure signal — non-zero exit, named rule, named location. Completeness requires all conditions and exceptions captured, which converts "is the mechanism's blind spot stated?" into a completeness question rather than an editorial one. Coverage is the degree to which specified coverage items have been exercised, so the denominator is the coverage-item set: a coverage requirement naming a target without naming its item set is unverifiable by construction.

### Sources
- https://www.nasa.gov/reference/appendix-c-how-to-write-a-good-requirement/ — verifiability, conditions and exceptions
- https://glossary.istqb.org/en_US/term/coverage-item — coverage item as denominator

## Quality criteria for committed-fixture data requirements

Fixture constraints map onto ISO 25012: cardinality and stable identifiers to completeness and consistency; naming-convention and exclusion-list conformance to syntactic accuracy and compliance; hash and recorded-hash lineage to traceability. RFC 8785 shows what a canonical-serialization requirement must pin down — member ordering, number and string serialization, encoding, whitespace, input constraints such as duplicate-key rejection and whether Unicode normalization is performed, and which fields fall inside the hashed scope. Any file excluded from the hash needs its own stated drift story. Drift detection is complete only when the requirement names the recorder, the comparison point, and the mismatch consequence; recording alone is recordable, not detectable. Datasheets for Datasets supplies the provenance completeness categories: motivation, composition, generation process, preprocessing, uses, distribution, maintenance.

### Sources
- https://www.rfc-editor.org/rfc/rfc8785 — what canonical serialization must specify
- https://arxiv.org/abs/1803.09010 — dataset documentation completeness

## Quality criteria for build and supply-chain boundary requirements

OWASP ASVS 5.0 is the requirements-adequacy anchor rather than a scanning guide: 15.1.2 requires a third-party inventory drawn from pre-defined trusted repositories; 15.2.4 requires components and all transitive dependencies to come from the expected repository with no dependency-confusion exposure; 15.2.5 requires isolation around risky components; 13.3.1 requires secrets absent from source code and build artifacts. SLSA v1.1 supplies isolation and provenance vocabulary across build levels and, usefully, explicitly declines to require hermetic builds while stating it detects tampering rather than preventing risky practice — precedent for demanding that a scoping claim state its boundary. A build context scoped to two paths constrains local source reachability, not package-index installation.

### Sources
- https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x24-V15-Secure-Coding-and-Architecture.md — dependency inventory, expected repository, isolation
- https://slsa.dev/spec/v1.1/requirements — build isolation, provenance levels, explicit non-coverage

## Cross-cutting conclusion

Only topology guarantees anything structurally: separate dependency projects with separate locks, and a build context that cannot see the modelling boundary. Import contracts and image metadata queries are regression detectors with named blind spots — re-export laundering, dynamic imports, metadata-less vendored source, name-mismatched transitives. Success criteria must therefore claim what actually holds, phrased as no *direct* import outside the designated module and installed set *equals* the lock, rather than the stronger guarantees these tools appear to offer.

### Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| https://docs.astral.sh/uv/concepts/projects/workspaces/ | dependency isolation | 2026-07-25 |
| https://docs.astral.sh/uv/concepts/projects/sync/ | dependency isolation | 2026-07-25 |
| https://import-linter.readthedocs.io/en/stable/contract_types/protected/ | import enforcement | 2026-07-25 |
| https://import-linter.readthedocs.io/en/stable/contract_types/forbidden/ | import enforcement | 2026-07-25 |
| https://github.com/GoogleContainerTools/container-structure-test | image assertion | 2026-07-25 |
| https://docs.python.org/3/library/importlib.metadata.html | image assertion | 2026-07-25 |
| https://nextjs.org/docs/app/api-reference/config/next-config-js/output | web subdirectory | 2026-07-25 |
| https://vercel.com/docs/monorepos | web subdirectory | 2026-07-25 |
