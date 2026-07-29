# E004 fix: the fixture key excludes the correlation identifier

`trace_id` was part of the fixture-key digest. FR-070 obliges one run-scoped trace identifier per run, so the same request keyed differently on every run and **no recorded fixture could ever be replayed** by a caller obliged to supply one. Measured before the fix: six runs of one request produced six distinct keys.

**Gateway tier 418 passed / 5 skipped** (402/5 before). Root `tests/checks` 227 passed. `ruff check` and `ruff format --check` clean at the repository root and in `src/gateway`; `lint-imports` 4 contracts kept / 0 broken; `mypy src` clean over 18 files.

## The seam

Both epics were internally correct and the defect existed only between them.

`resolve_trace_id` (`orchestrator.py`) mints an identifier when the caller supplies none, so every E004 test keys with `trace_id` null and is perfectly stable. E006 is the first caller obliged to supply one.

The requirement text carries the same seam. **TR-019 enumerates what the key covers** — provider, requested model, system and message content, every sampling parameter, the submitted schema, the schema version, the prompt-template version. A trace identifier is not among them. **TR-020 then generalised** that enumeration to "the hashed set is every field the gateway's own request model declares", which is broader than TR-019's list by however many declared fields are not semantic inputs. At the time it was written the difference was empty. **TR-080 later made it non-empty** by requiring the trace identifier to be an explicit field on the request type.

So this is not a change to what a fixture key hashes. It is a repair of a generalisation that outran the requirement it generalised.

## Approach: excluded at the hash, not on the model

Both candidates produce the identical digest today, because `hashing.py` holds the only `model_dump_json()` call on an `InvocationRequest` in the repository. They differ in what they cost later.

`Field(exclude=True)` on the model removes the identifier from *every* serialization of a request — any log line, any spooled payload, any debug dump — for every future caller, invisibly. The field a reader most needs to trace back is the last one that should silently vanish from a dump. The exclusion is a property of the hash, not of the type, so it lives with the hash:

- `UNHASHED_REQUEST_FIELDS` — a named `frozenset` in `compute/hashing.py` carrying the reasoning and the test for admitting a second member ("can this field change what the provider is asked, or what it answers?").
- `hashed_request_payload(request)` — the payload construction, extracted so the field set is *assertable*. Folded inline, the exclusion could only be observed as "these two keys are equal", which is also what a hash ignoring the whole request looks like.

`output_schema`'s existing `exclude=True` is the opposite case and stays: a class is not JSON, so no serialization can carry it, and it reaches the key by digest instead. The module docstrings now state that these are two different rules — reading them as one is how someone concludes the model is the place to exclude a field from the hash.

TR-020's closure now reads "every declared field, less `UNHASHED_REQUEST_FIELDS`", stated in `hashing.py`'s module docstring, in `fixture_key`'s docstring, and on the model field itself.

## The fixture did not need re-keying, and that is the second finding

`sha256:72a4e4a4…` is unchanged. No rename, and the provenance sidecar records no key, so nothing in it changed either.

The reason is the more interesting half. E004's `test_the_exemplar_key_is_reproducible` derived the expected key from a **hand-built dict** naming `prompt` and `model` and no third key — asserting what its author believed `fixture_key` hashed rather than what it hashed. The two had already diverged when it was written: the real request serialised `trace_id: null`, keying to `sha256:fddb3574…`, while the fixture and the test both said `72a4e4a4…`.

**The committed exemplar was unreachable from the invocation path from the day it landed**, and the one test that existed to catch that agreed with the fixture because it shared the mistake. The fix makes the real request derive `72a4e4a4…` for the first time. The test now derives its key through the function under test, which cannot share a mistake with it.

## Also fixed: `DEFAULT_FIXTURE_ROOT` off an installed package

`Path(__file__).resolve().parents[2] / "fixtures"` walks up to the gateway entry root, which holds in an editable install. Measured from the model entry it resolves to `src/model/.venv/Lib/fixtures`, which does not exist — so `Resolution.from_environment`, the path every real caller takes, built a store rooted at a missing directory. E006's `plan.md` places its fixtures at `src/model/fixtures/`, so the committed plan and the committed code disagreed.

`GATEWAY_FIXTURE_ROOT` is added on the path `from_environment` actually takes, following the existing `spool_path` pattern: read by `load_config` into `GatewayConfig.fixture_root`, preferred by `from_environment`, blank treated as absent because `Path("")` is the working directory. A **default, not a fallback** — unset keeps the gateway's own store, so nothing that worked before depends on it now being set. `Resolution.from_environment` had no test at all; it has four now.

## Same class of bug, checked

`InvocationRequest` declares four fields. `prompt` and `model` are semantic inputs. `output_schema` is semantic and reaches the key by digest. `trace_id` was the only correlation or timing identifier, and there is no second candidate to remove.

Two adjacent things were checked and are clean. `repair_fixture_key`'s `instruction` is built from pydantic's `loc` and `msg` alone, so it carries no run-scoped content and the repair key is stable; it is asserted separately anyway, because its derivation from `fixture_key` is an implementation detail a later change could drop. `schema_version` and `prompt_template_version` digest semantic inputs.

**One residual, disclosed rather than fixed.** The gateway cannot see run-scoped content a caller embeds in the *prompt* — that changes what the provider is asked, so hashing it is correct, and a template interpolating a run identifier would reproduce this defect's symptom with none of its cause. That belongs to whoever writes the prompt templates.

## Decision record: none written, 0022 stays free

This is a bug fix inside **ADR-0007**'s existing decision, not a new decision. ADR-0007 chose "responses cached under a key derived from a hash of prompt, model, and parameters"; a correlation identifier is none of the three, so the chosen option never included it and no consequence recorded against it changes. The change *restores* conformance with ADR-0007 and with TR-019's own enumeration rather than departing from either.

`specs/sad.md` therefore needs no catalog row and no edit — its fixture-key statement ("cache keys include a prompt hash") remains true.

**Recorded, not performed** (Governance: a feature branch records the need for an amendment): **TR-020's closure sentence in `specs/00004-traced-model-gateway/spec.md` is now false by one stated exception.** E004 is a completed epic carrying `.qc-passed`, and amending a completed epic's spec from another feature's branch is not this branch's call. The code states the exception truthfully in three places; the requirement text has not caught up.

## A test that hid itself

The end-to-end replay test was first written guarded on `committed.has(fixture_key(request))`. Neutralising the fix turned its failure into a **silent skip** — the guard was the condition under test. Caught by deliberately reverting the exclusion and re-running, which is also how each new assertion was confirmed to be a real regression guard rather than a tautology: 8 failures with the exclusion emptied, 0 with it restored.

## Files

`src/gateway/src/gateway/compute/hashing.py` · `src/gateway/src/gateway/models.py` · `src/gateway/src/gateway/config.py` · `src/gateway/src/gateway/orchestrator.py` · `src/gateway/tests/test_compute_hashing.py` · `src/gateway/tests/test_fixtures.py` · `src/gateway/tests/test_invoke.py` · `src/gateway/tests/test_config.py`

No fixture file renamed. No migration. No change to any other entry.
