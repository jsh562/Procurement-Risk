"""TR-007 / SC-004: every contract has a committed negative fixture.

A contract that has never been observed failing is an assertion about the
build, not a property of it. Each test here plants a real violation and
requires the contract to exit non-zero; the clean-tree run in
test_layout/test_dependency_isolation is the corresponding positive control.
"""

from __future__ import annotations

from tests.checks.helpers.contract_runner import FIXTURE_ROOT, run_contract
from tests.checks.helpers.source_scan import scan_source_root


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
