# `/src/model/fixtures` — E006's extraction fixture record

**No fixture file lives in this directory, and the reason is not that none is
needed.** This file records where this epic's `replay`-mode fixtures actually
resolve from, why they are not here, and what would have to change for them to
move. `../README.md` §"Extraction fixtures, and when they must be re-recorded"
holds the trigger and the re-record procedure; this file holds the location and
the disclosed gap.

## Where the fixtures resolve from

`src/gateway/fixtures/`, laid out as:

```
sha256/<first two hex of the key>/<key>.response.json
sha256/<first two hex of the key>/<key>.provenance.json
```

That root is `gateway.orchestrator.DEFAULT_FIXTURE_ROOT`, computed as
`Path(gateway/__file__).parents[2] / "fixtures"`, and
`Resolution.from_environment` constructs the store with it. **It reads no
environment variable and takes no override**: there is no configuration by which
a caller can point the fixture store somewhere else.

So a `replay`-mode ingestion run resolves its fixtures from the gateway entry's
store, not from this directory. Committing them here would produce a directory
that looks authoritative and that nothing reads — the worst of the available
outcomes, and strictly worse than an empty directory with this file in it.

## The gap, stated rather than worked around

`specs/00006-document-ingestion-and-extraction/plan.md` §Project Structure places
this epic's committed fixtures at `src/model/fixtures/`, and the delivered
gateway does not admit that. The disagreement is real and is recorded here rather
than resolved locally, because every way of resolving it from this side is worse
than the gap:

- **Constructing a `FixtureStore` rooted here and invoking through it** would
  bypass `gateway.api.invoke`, which is the single traced path — no invocation
  record would be written, and FR-070's reconciliation of attempted against
  recorded invocations would have nothing to reconcile against.
- **Committing a second copy here** would be two fixture stores for one key
  space, and the copy nothing reads is the one that silently goes stale.
- **Adding a root override to the gateway** is an E004 change to a boundary this
  epic does not own, and it belongs in that epic's decision record rather than in
  a runbook here.

The gap is therefore: either E004 gains a configurable fixture root, or the plan
is corrected to name the gateway's store. Until one of those happens, the
gateway's store is where the fixtures go, and `../README.md` says so at the point
an operator needs to know it.

## What is committed today, and why it is nothing

**Zero E006 extraction fixtures are committed**, because none can be recorded
without reaching the provider. A fixture is a recorded provider response: the
only way to produce one is a `record`-mode run with a credential and the
`GATEWAY_ALLOW_PROVIDER_CALLS` opt-in, which continuous integration does not
have and which `tests/checks/test_ci_provider_gate_absent.py` asserts the absence
of. Fabricating a response and committing it as a fixture would be indistinguishable
downstream from a recorded one, which is the failure Principle I exists to
prevent — and it would make every extraction-quality figure a measurement of
whatever was typed here.

The consequence is stated rather than left to be discovered: **a `replay`-mode
run reaching the extraction stage today aborts with FR-056's `fixture_missing`**,
naming the resolution key that missed. That is the designed behaviour for an
absent fixture and not a defect in the mechanism; what it means in practice is
that the first `record`-mode run is a prerequisite for the first complete
replayed run, and its fixtures are committed with it.
