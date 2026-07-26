# Research: Traced Model Gateway

> Feature: E004 Traced Model Gateway | Date: 2026-07-25 | Purpose: Inform objective priorities, architecture decisions, source structure, and testing strategy

## Single provider-import boundary

- **Decision**: Keep E001's `import-linter` `protected` contract naming the SDK with one allowlisted module, and add a public surface built only from gateway-owned types.
- **Rationale**: The contract constrains imports only, so a gateway returning SDK objects stays green while still coupling both Python boundaries to the provider.
- **Rejected**: Re-exporting SDK symbols from the package init, because it is the same structural coupling with an extra hop.
- **Pitfalls**: Leaving `exclude_type_checking_imports` at a non-default true, which would let a `TYPE_CHECKING`-guarded SDK import slip past the contract.
- **Sources**: https://import-linter.readthedocs.io/en/stable/contract_types/protected/, https://import-linter.readthedocs.io/en/stable/usage.html

## Structured output and the bounded repair loop

- **Decision**: Submit the schema through the provider's native structured-output mode and express unsupported constraints as post-decode validators, with exactly one repair attempt carrying the failing field path and message.
- **Rationale**: Constrained decoding guarantees syntax, required fields, and types but not numeric bounds, string length, or pattern — which is where this domain's confidence scores and page numbers live.
- **Rejected**: Prompt-and-parse with unbounded repair, since self-repair gains are modest and an uninformative retry entrenches the same error.
- **Pitfalls**: Letting transport retries consume the repair budget, which would record a rate-limit failure as `repaired` and corrupt the published quality signal.
- **Sources**: https://platform.claude.com/docs/en/docs/build-with-claude/structured-outputs, https://arxiv.org/abs/2306.09896

## Invocation record and cost as versioned code

- **Decision**: Name recorded fields after the OpenTelemetry generative-AI semantic conventions at a pinned version, store requested and resolved model, and compute cost from stored token counts against a `(model, effective_from)` price table.
- **Rationale**: Recording both model identifiers and the price-table version is what keeps a historical figure recomputable after rates change.
- **Rejected**: Hardcoded per-million-token constants and a table keyed on model name alone, both of which break the first time a rate changes on a date.
- **Pitfalls**: Folding cache-write and cache-read counts into plain input tokens, since the provider reports them outside that count and bills them at different multipliers.
- **Sources**: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md, https://platform.claude.com/docs/en/about-claude/pricing

## Content-hash fixtures and offline replay

- **Decision**: Key each fixture on a canonical serialization of the whole request over a closed field list, and offer only `record` and `replay`, with `replay` treating a miss as an error.
- **Rationale**: Anything omitted from hash scope becomes a silent stale replay, which is worse than a miss because it is invisible.
- **Rejected**: vcrpy's `once` default and URI-plus-method matching, since the former records silently when the cassette is absent and the latter collides every request on one endpoint.
- **Pitfalls**: Leaving the submitted schema or its version out of the key, which changes the grammar and the input token count without changing the fixture.
- **Sources**: https://vcrpy.readthedocs.io/en/latest/advanced.html, https://www.rfc-editor.org/rfc/rfc8785

## Credential redaction inside the boundary

- **Decision**: Read the credential once at client construction, keep it off any repr'd or serialized object, and treat logs, exception payloads, and committed fixtures as three separate redaction obligations.
- **Rationale**: Variable-capturing tracebacks dump local frames holding the client and provider error bodies can echo request headers, so a logging filter covers one path of three.
- **Rejected**: Relying on an environment variable as a control, since OWASP notes it is readable by every process and appears in dumps — it is a transport, not a boundary.
- **Pitfalls**: Assertion messages that interpolate the client or the raw request, which leak through test output rather than application logs.
- **Sources**: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html, https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html

## Optional provider extra and lazy import

- **Decision**: Declare the SDK under `[project.optional-dependencies]` as `provider`, import it inside the invocation function of the single allowlisted module, and re-raise a missing-module error as a gateway error naming the install command.
- **Rationale**: A function-local import is the only form leaving the public surface importable and type-checkable with the SDK absent, since a `TYPE_CHECKING` guard still counts as an import under the contract.
- **Rejected**: Module-level `__getattr__` re-exporting SDK symbols lazily, which adds a hop and leaks SDK types into the public surface — type the client handle as a locally defined protocol instead.
- **Pitfalls**: Proving the absent-extra path by mocking alone, rather than in a synthetic environment resolved without the extra plus an in-process check that forces the import to fail.
- **Sources**: https://docs.python.org/3/reference/import.html, https://peps.python.org/pep-0562/

## Local spool for failed record writes

- **Decision**: Spool the record to a local SQLite file with write-ahead logging and full synchronous durability, keyed on invocation id, reconciling with an insert that ignores conflicts and deleting the spool row only after the Postgres transaction commits.
- **Rationale**: Outbox delivery is at-least-once by construction, so an idempotent sink keyed on a unique id turns it into an exactly-once effect with no distributed transaction.
- **Rejected**: Hand-rolled append-only JSON Lines with manual fsync, because SQLite already supplies atomic commit, torn-write recovery, and cross-process locking that a flat file must reimplement.
- **Pitfalls**: Unbounded spool growth while Postgres is down, and the irreducible billed-but-unrecorded window if the process dies between the provider response and the spool commit.
- **Sources**: https://microservices.io/patterns/data/transactional-outbox.html, https://www.sqlite.org/whentouse.html

## Migrations owned by the modeling entry, with claimed prefix blocks

- **Decision**: Alembic, owned by `/src/model` per {SAD:ADR-0013}, with each epic's ownership expressed as a reserved filename-prefix block layered over Alembic's revision identifiers — `0001`–`0099` for E003, `0100`–`0199` for E004.
- **Rationale**: One database implies one schema owner, and the entry that writes nearly every domain table is the natural holder of the tooling; prefix blocks give parallel epics disjoint authoring space without renegotiating numbers.
- **Rejected**: A raw-SQL runner owned by the gateway — this epic's original choice, withdrawn because it would have required superseding ADR-0010's gateway-minimality consequence, and because two runners against one database is the collision the arrangement exists to prevent.
- **Pitfalls**: No runner enforces a block, so ownership must be asserted by a check over the revision directory covering prefix range, duplicate prefixes, and a single head — a merge revision or a second head silently makes the apply order ambiguous.
- **Sources**: https://alembic.sqlalchemy.org/en/latest/branches.html, https://ollycope.com/software/yoyo/latest/

## Coverage instrumentation across monorepo entries

- **Decision**: Point the gateway's test step at its own coverage data file inside a repository-root data directory, run in parallel mode with relative files and the same branch setting as every other entry, then combine at the root against a path-remapping section.
- **Rationale**: Relative files plus path remapping is what collapses measurements taken inside one entry onto a canonical source path at the root, and parallel mode's suffix stops entries overwriting each other.
- **Rejected**: One shared data file written by all four entries, since non-parallel runs clobber rather than merge and combining consumes its inputs unless explicitly kept.
- **Pitfalls**: Silent-empty or duplicated measurements from tests importing a non-editable installed copy so recorded paths never match the remapping, or a first remap entry that is not a real path on the reporting machine.
- **Sources**: https://coverage.readthedocs.io/en/latest/config.html, https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html

## Per-request deadline and retry budget

- **Decision**: Set an explicit per-attempt timeout across connect, read, write, and pool, wrap the whole invocation in an outer monotonic deadline, and count expiry of either as one transport failure against the retry budget and never against the repair budget.
- **Rationale**: HTTP client timeouts are per-operation inactivity limits rather than a total, so attempts multiplied by a read timeout is unbounded until an outer deadline caps it.
- **Rejected**: A per-request timeout with retries and no ceiling — cap attempts and propagate the remaining deadline into each attempt so later attempts get less, not a fresh full timeout.
- **Pitfalls**: Retrying a read timeout can double-bill because the provider may have completed the call, and a per-response elapsed value covers one request rather than the invocation's total wall clock.
- **Sources**: https://www.python-httpx.org/advanced/timeouts/, https://sre.google/sre-book/handling-overload/

## Data-integrity requirement quality

- **Decision**: Review each data-integrity item against ISO/IEC 25012's two levels — inherent characteristics as named column-level constraints, system-dependent characteristics as the spool and migration claims — and hold every one to INCOSE's measurability rules (units on every value, no vague terms), so the item names a constraint rather than a property.
- **Rationale**: A reviewer can return a verdict on "trace identifier is NOT NULL" or "cost stored at a stated scale with a stated rounding order", but cannot fail "the record is correct".
- **Rejected**: Framing the checklist on ACID alone, because ACID describes the engine's guarantees rather than the requirement's content and says nothing about whether the uniqueness key, the referential action, or the rounding order was ever written down.
- **Pitfalls**: Exactly-once stated as a system property instead of an idempotency key plus a conflict action; monetary precision without a scale and a stated sum-then-round order, which makes any stored-versus-recomputed criterion undecidable; append-only asserted in prose with no named enforcement point.
- **Sources**: https://iso25000.com/index.php/en/iso-25000-standards/iso-25012, https://www.incose.org/docs/default-source/working-groups/requirements-wg/guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf

## Secret-handling security requirement quality

- **Decision**: Cite OWASP ASVS at a pinned version and map the three redaction obligations onto 5.0.0's chapters — V16 Security Logging and Error Handling, V14 Data Protection, V13 Configuration — requiring each secret-handling item to name its sink, its detector, and a negative test.
- **Rationale**: ASVS 5.0.0 renumbered exactly the chapters in question, so a checklist row naming a bare chapter number is ambiguous between two live standards.
- **Rejected**: A single blanket "no secret material anywhere" item, because it has no enumerable denominator and therefore no completeness check.
- **Pitfalls**: A redaction detector matching a pattern the actual credential shape does not; an egress inventory listing only application logs while omitting exception payloads, committed artifacts, test output, and provider-echoed error bodies.
- **Sources**: https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md, https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x22-V13-Configuration.md

## Telemetry-record requirement quality

- **Decision**: Require of any telemetry-record requirement a closed field list, a naming provenance per field (a semantic-convention attribute at a pinned convention version, or a project-owned prefix), a stated stability class, an explicit not-captured list, and a stated denominator for every derived rate.
- **Rationale**: OpenTelemetry guarantees attribute-key stability only for stable conventions and the generative-AI set is not one, so an unpinned field name is an unfalsifiable requirement.
- **Rejected**: Any open-ended field set ("records relevant metadata"), since it yields no field a reviewer can mark present or absent.
- **Pitfalls**: High-cardinality values such as trace identifier, fixture key, and schema digest specified as if they were metric dimensions rather than record columns; a percentage criterion whose denominator is unstated.
- **Sources**: https://opentelemetry.io/docs/specs/otel/versioning-and-stability/, https://opentelemetry.io/docs/specs/semconv/general/naming/

## Summary

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Single provider-import boundary | `protected` contract plus a gateway-owned public surface | The contract constrains imports; the return type is what creates the boundary |
| Structured output and bounded repair | Native structured output, unsupported constraints as validators, one repair | Constrained decoding stops short of the numeric and length constraints this domain uses |
| Invocation record and cost | Pinned telemetry field names, both model identifiers, cost from stored counts | Recomputability after a rate change requires counts and a version, not a derived figure |
| Content-hash fixtures and offline replay | Closed-field canonical request hash; `record` and `replay` only; miss is an error | Anything outside hash scope becomes an invisible stale replay |
| Credential redaction | Three separate egress obligations, not one logging filter | Tracebacks and provider error bodies leak on paths a log filter never sees |
| Optional provider extra and lazy import | Optional-dependency extra plus a function-local SDK import and a local protocol for typing | Only form importable and type-checkable with the SDK absent under the import contract |
| Local spool for failed record writes | SQLite spool keyed on invocation id, conflict-ignoring insert, delete after commit | At-least-once delivery plus an idempotent sink yields an exactly-once effect |
| Migrations owned by the modeling entry | Alembic in `/src/model`, prefix blocks over revision ids, plus prefix and single-head checks | One database implies one schema owner; block ownership is a convention no runner enforces |
| Coverage across monorepo entries | Per-entry data file, parallel mode, relative files, root combine with path remapping | Path remapping is what merges per-entry measurements into one source tree |
| Per-request deadline and retry budget | Per-attempt timeout inside an outer monotonic deadline; expiry is one transport failure | Per-operation timeouts multiply by attempts and are unbounded without a ceiling |
| Data-integrity requirement quality | ISO/IEC 25012 inherent-versus-system-dependent split, stated as named constraints under INCOSE measurability rules | A constraint has a verdict; a property does not |
| Secret-handling security requirement quality | Pinned ASVS version, one requirement per egress sink, each with a detector | ASVS 5.0.0 renumbered the relevant chapters, and a blanket item has no denominator |
| Telemetry-record requirement quality | Closed field list with per-field naming provenance, stability class, not-captured list, stated denominators | Gen-AI semantic conventions carry no attribute-key stability guarantee, so unpinned names are unfalsifiable |

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| https://import-linter.readthedocs.io/en/stable/contract_types/protected/ | provider-import boundary | 2026-07-25 |
| https://import-linter.readthedocs.io/en/stable/usage.html | provider-import boundary | 2026-07-25 |
| https://platform.claude.com/docs/en/docs/build-with-claude/structured-outputs | structured output and repair | 2026-07-25 |
| https://arxiv.org/abs/2306.09896 | structured output and repair | 2026-07-25 |
| https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md | invocation record and cost | 2026-07-25 |
| https://platform.claude.com/docs/en/about-claude/pricing | invocation record and cost | 2026-07-25 |
| https://vcrpy.readthedocs.io/en/latest/advanced.html | fixtures and replay | 2026-07-25 |
| https://www.rfc-editor.org/rfc/rfc8785 | fixtures and replay | 2026-07-25 |
| https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html | credential redaction | 2026-07-25 |
| https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html | credential redaction | 2026-07-25 |
| https://docs.python.org/3/reference/import.html | optional extra and lazy import | 2026-07-25 |
| https://peps.python.org/pep-0562/ | optional extra and lazy import | 2026-07-25 |
| https://microservices.io/patterns/data/transactional-outbox.html | local spool for failed writes | 2026-07-25 |
| https://www.sqlite.org/whentouse.html | local spool for failed writes | 2026-07-25 |
| https://ollycope.com/software/yoyo/latest/ | migrations ownership and prefix blocks | 2026-07-25 |
| https://alembic.sqlalchemy.org/en/latest/branches.html | migrations ownership and prefix blocks | 2026-07-25 |
| https://coverage.readthedocs.io/en/latest/config.html | coverage across monorepo entries | 2026-07-25 |
| https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html | coverage across monorepo entries | 2026-07-25 |
| https://www.python-httpx.org/advanced/timeouts/ | deadline and retry budget | 2026-07-25 |
| https://sre.google/sre-book/handling-overload/ | deadline and retry budget | 2026-07-25 |
| https://iso25000.com/index.php/en/iso-25000-standards/iso-25012 | data-integrity requirement quality | 2026-07-25 |
| https://www.incose.org/docs/default-source/working-groups/requirements-wg/guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf | data-integrity requirement quality | 2026-07-25 |
| https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md | secret-handling requirement quality | 2026-07-25 |
| https://github.com/OWASP/ASVS/blob/v5.0.0/5.0/en/0x22-V13-Configuration.md | secret-handling requirement quality | 2026-07-25 |
| https://opentelemetry.io/docs/specs/otel/versioning-and-stability/ | telemetry-record requirement quality | 2026-07-25 |
| https://opentelemetry.io/docs/specs/semconv/general/naming/ | telemetry-record requirement quality | 2026-07-25 |
