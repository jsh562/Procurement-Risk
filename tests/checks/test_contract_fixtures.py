"""TR-007 / SC-004 / FR-035: every contract has a committed negative fixture.

A contract that has never been observed failing is an assertion about the
build, not a property of it. Each test here plants a real violation and
requires the contract to exit non-zero; the clean-tree run in
test_layout/test_dependency_isolation is the corresponding positive control.

**FR-035 — a pull request whose head violates an architecture contract reports a
failing check naming the violated contract — is discharged at the bottom of this
file**, by three assertions taken together rather than by any one of them:

1. the set of contracts standing in for is derived from the three entries' own
   manifests, so a contract added without a negative fixture fails here instead
   of quietly going unevidenced;
2. each fixture is executed and its output must *name the contract* — not merely
   exit non-zero, and not merely name the offending module, which is what the
   cases above already assert;
3. the step that runs these tests is shown to be an unconditional step of a
   workflow triggered by `pull_request`, which is what places the failing check
   *inside the pull-request run*. Without that last link the evidence would rest
   on a manual dispatch nobody is obliged to trigger — and the dispatch input
   offers a payload for only two of the three contracts anyway.
"""

from __future__ import annotations

import configparser

import pytest

from tests.checks.helpers.contract_runner import FIXTURE_ROOT, run_contract
from tests.checks.helpers.entries import PYTHON_ENTRIES, declared_contracts
from tests.checks.helpers.source_scan import scan_source_root
from tests.checks.helpers.workflow import (
    admits_branch,
    load_workflow,
    steps_running,
    triggers,
    unconditional,
)


def test_allowlist_contract_breaks_on_a_second_import_site() -> None:
    result = run_contract("provider_import")
    assert not result.passed, "a second direct import site did not break the contract"
    assert result.names("second_site"), f"failure did not name the offender:\n{result.output}"


def test_reexport_laundering_passes_the_contract() -> None:
    """The blind spot, asserted rather than described.

    Reading the client off a permitted module produces no direct import edge,
    so the contract is satisfied. This is not a bug in the contract — it is the
    reason a second, textual check exists.
    """
    assert run_contract("reexport").passed


def test_source_scan_catches_what_the_contract_missed() -> None:
    mentions = scan_source_root(FIXTURE_ROOT / "reexport", "anthropic")
    named = {m.path.name for m in mentions}
    assert "launderer.py" in named, f"scan missed the laundering module: {sorted(named)}"


def test_computation_boundary_breaks_on_a_direct_reach() -> None:
    result = run_contract("computation_boundary")
    assert not result.passed
    assert result.names("direct"), f"failure did not name the direct path:\n{result.output}"


def test_computation_boundary_breaks_on_an_indirect_reach() -> None:
    """The case that motivates keeping indirect detection on."""
    result = run_contract("computation_boundary")
    assert not result.passed
    assert result.names("indirect"), f"failure did not name the indirect path:\n{result.output}"


# --- VR-044 / FR-022: the corpus generator invokes no language model ----------
# The real contract forbids `model.corpus` from reaching `model.llm` or
# `gateway` with indirect detection on. It passes on this tree, so its failing
# direction needs evidence of its own — and that evidence is a committed fixture
# executed here on every triggering push and pull request, not a manual dispatch
# input, which offers no payload for this contract and evidences the harness
# rather than the contract.


def test_vr_044_corpus_offline_contract_breaks_on_a_laundered_reach() -> None:
    """The evasion `allow_indirect_imports = false` exists to catch.

    With indirect detection off this edge is invisible: the generator imports an
    ordinary relay, and only the relay names the client.
    """
    result = run_contract("corpus_offline")
    assert not result.passed, "VR-044: a laundered reach did not break the corpus contract"
    assert result.names("relay"), (
        f"VR-044: failure did not name the laundering module:\n{result.output}"
    )
    assert result.names("generator"), (
        f"VR-044: failure did not name the offending corpus module:\n{result.output}"
    )


def test_vr_044_corpus_offline_contract_breaks_on_a_direct_provider_import() -> None:
    """The second forbidden target: reaching the provider through the shared
    client is the same violation by another route, which is why the real
    contract names `gateway` beside `model.llm`."""
    result = run_contract("corpus_offline")
    assert not result.passed, "VR-044: a direct provider import did not break the contract"
    assert result.names("degrader"), (
        f"VR-044: failure did not name the direct provider import site:\n{result.output}"
    )


# --- FR-050 / Principle VIII: the baseline is an honest opponent --------------
# The real contract forbids `model.ingest.baseline` from `model.corpus.templates`,
# `.render` and `.model` — the per-vendor generation source, the renderer, and the
# pre-render document model. Those three are the answer key. A baseline reading
# any of them is not extracting from a document, and an opponent that cannot lose
# makes every quality figure published beside it flattery rather than evidence.
#
# The contract passes on this tree, so its failing direction needs evidence of its
# own. The dispatch input offers no payload for it, and a payload nobody is
# obliged to trigger is not evidence anyway.


def test_baseline_independence_breaks_on_a_direct_answer_key_read() -> None:
    """The crude violation: the baseline imports the pre-render document model
    and reports the values it was handed."""
    result = run_contract("baseline_independence")
    assert not result.passed, "FR-050: a direct answer-key read did not break the contract"
    assert result.names("baseline_fixture.corpus.model"), (
        f"FR-050: failure did not name the answer-key module:\n{result.output}"
    )


def test_baseline_independence_breaks_on_a_laundered_reach() -> None:
    """The evasion `allow_indirect_imports = false` exists to catch.

    The baseline imports an ordinary corpus module that violates nothing itself
    and merely reads the templates and the renderer, because generating a corpus
    is what it does. That leaves no direct edge to either, so with indirect
    detection off this reads as a clean module — which is the shape the evasion
    would actually take, not the direct read above.
    """
    result = run_contract("baseline_independence")
    assert not result.passed, "FR-050: a laundered reach did not break the contract"
    assert result.names("baseline_fixture.corpus.generator"), (
        f"FR-050: failure did not name the laundering module:\n{result.output}"
    )
    for answer_key in ("baseline_fixture.corpus.templates", "baseline_fixture.corpus.render"):
        assert result.names(answer_key), (
            f"FR-050: the laundered chain did not resolve to {answer_key}:\n{result.output}"
        )


# --- FR-035: a violated contract is named, by a failing check in the PR run ----

# Which fixture stands in for which real contract, keyed by the contract name as
# the entry's own manifest declares it. The keys are checked against those
# manifests below rather than trusted, so this map cannot drift into naming a
# contract the repository no longer has, or omitting one it has gained.
#
# The mapping is many-to-one in both directions, and each direction has a
# reason.
#
# Fewer keys than declarations: `api` and `model` each declare the
# computation-boundary contract under the same name and with the same shape, and
# one fixture stands in for both. The name is the unit FR-035 speaks about — a
# failing check names the violated contract — so deduplicating by name is the
# requirement's own granularity rather than a convenience.
#
# Two keys onto one fixture: the gateway's public surface is guarded by two
# contracts with different indirect-detection settings, and one fixture package
# violates both. Splitting it in two would duplicate the provider and relay
# modules to no end — the assertion below runs `lint-imports` over the fixture
# once per key and requires the output to name that key's contract, so a fixture
# that broke only one of the two still fails the other.
FIXTURE_FOR_CONTRACT = {
    "Only the provider wrapper imports the model-provider client": "provider_import",
    "Model-facing code does not reach the computation package": "computation_boundary",
    "Corpus code does not reach the model provider": "corpus_offline",
    "The provider-facing module does not reach the arithmetic modules": (
        "gateway_compute_boundary"
    ),
    "The gateway-owned type modules do not reach the provider module": ("gateway_public_surface"),
    "The public entry point does not import the provider module directly": (
        "gateway_public_surface"
    ),
    # E006 / FR-050. Added with the contract itself rather than after it: this
    # map is checked against the manifests, so declaring the contract without a
    # fixture fails `test_every_declared_contract_has_a_negative_fixture` — which
    # is the mechanism working, not an obstacle to it.
    "The baseline extractor does not reach the corpus generator": "baseline_independence",
}

# The step in verify.yml that executes this file.
CHECKS_STEP = "pytest tests/checks"
DEFAULT_BRANCH = "main"


def _fixture_contract_names(fixture: str) -> list[str]:
    """The contract names a fixture's own `.importlinter` declares."""
    parser = configparser.ConfigParser()
    parser.read(FIXTURE_ROOT / fixture / ".importlinter", encoding="utf-8")
    return [
        parser[section]["name"]
        for section in parser.sections()
        if section.startswith("importlinter:contract:")
    ]


def test_every_declared_contract_has_a_negative_fixture() -> None:
    """The no-gap assertion. Derived from the manifests, so adding a fifth
    contract to any entry fails this test until a fixture stands in for it."""
    declared = {name for entry in PYTHON_ENTRIES for name in declared_contracts(entry)}
    assert declared, "no import-linter contracts found in any entry manifest"
    assert declared == set(FIXTURE_FOR_CONTRACT), (
        "the contracts the entries declare and the contracts with negative fixtures "
        f"differ.\n  unevidenced: {sorted(declared - set(FIXTURE_FOR_CONTRACT))}"
        f"\n  stale map entries: {sorted(set(FIXTURE_FOR_CONTRACT) - declared)}"
    )


@pytest.mark.parametrize(("contract", "fixture"), sorted(FIXTURE_FOR_CONTRACT.items()))
def test_the_fixture_declares_the_contract_it_stands_in_for(contract: str, fixture: str) -> None:
    """A fixture whose contract is named differently from the real one is
    evidence about the fixture, not about the contract. Asserted because the two
    names drifted once already: the provider fixture said "Only the wrapper"
    where gateway says "Only the provider wrapper"."""
    assert contract in _fixture_contract_names(fixture), (
        f"fixture {fixture!r} declares {_fixture_contract_names(fixture)}, not {contract!r}"
    )


@pytest.mark.parametrize(("contract", "fixture"), sorted(FIXTURE_FOR_CONTRACT.items()))
def test_a_violated_contract_is_reported_by_name(contract: str, fixture: str) -> None:
    """FR-035's substance. A non-zero exit alone tells a reviewer that something
    broke; naming the contract is what tells them *which rule* their head
    violates, which is the difference between a usable check and a red cross."""
    result = run_contract(fixture)
    assert not result.passed, f"{contract!r}: the negative fixture did not break the contract"
    assert result.names(contract), (
        f"the failing check did not name the violated contract {contract!r}:\n{result.output}"
    )


def test_the_fixtures_execute_inside_the_pull_request_run() -> None:
    """The link that puts the failing check where FR-035 needs it.

    Two properties, and both matter. The workflow must trigger on
    `pull_request` against the default branch — otherwise these fixtures only
    ever run against a pushed ref, and the merge gate is evidenced by something
    other than the merge gate. And the step that runs them must be
    unconditional: the dispatch-only injection step in this same workflow shows
    how easily a step acquires an `if:` on `github.event_name`, and one placed
    here would silently take the fixtures out of the pull-request run while
    leaving them visibly present in the file.
    """
    document = load_workflow()
    declared = triggers(document)
    assert "pull_request" in declared and admits_branch(declared["pull_request"], DEFAULT_BRANCH), (
        f"verify.yml does not run on pull requests against {DEFAULT_BRANCH!r}: {declared.keys()}"
    )
    running = steps_running(document, CHECKS_STEP)
    assert running, f"no verify.yml step runs {CHECKS_STEP!r}; these fixtures execute nowhere in CI"
    assert any(unconditional(step) for step in running), (
        f"every step running {CHECKS_STEP!r} is gated by an `if:`, so the negative "
        "fixtures may not execute in the pull-request run: "
        f"{[step.get('if') for step in running]}"
    )
