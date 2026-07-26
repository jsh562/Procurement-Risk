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
