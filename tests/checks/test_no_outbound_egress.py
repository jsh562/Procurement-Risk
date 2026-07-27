"""TR-067 / SC-008: "0 outbound provider requests" is observed, not inferred.

SC-008 claims a number. A number nobody counted is not a measurement, and an
absence nothing watched reports the same zero whether the suite made no requests
or made ten and swallowed the errors. TR-067 therefore names the observation
point rather than the outcome: **a network guard installed for the whole
automated check suite that fails any outbound connection attempt from the check
process**.

This file asserts the guard exists, is installed for every test rather than on
request, and actually fires. It does not itself watch the network — the guard in
`src/gateway/tests/conftest.py` does that, on every test in the tier where the
provider-reaching code lives.

**The disclosed limit is asserted as a limit** (TR-067). The guard patches the
standard library's socket layer, which covers every client built on it. Code
reaching the network through a native extension that bypasses `socket` would not
be seen, and TR-001's import contract would not see it either — that module
would satisfy every contract in the spec while opening a connection. Code review
is the only guard against it, which TR-067 says in as many words. Recording that
here keeps it a disclosed residue rather than an assumed impossibility.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_CONFTEST = REPO_ROOT / "src" / "gateway" / "tests" / "conftest.py"

#: The guard's permitted-destination set and its failure type live in a plain
#: module rather than in the conftest, because pytest imports a conftest under
#: its own module name — so a test importing it by path catches a *different*
#: class object from the one the guard raises. That is not hypothetical; it is
#: what happened when the behavioural control was first written.
GATEWAY_NETGUARD = REPO_ROOT / "src" / "gateway" / "tests" / "netguard.py"


def _conftest_tree() -> ast.Module:
    assert GATEWAY_CONFTEST.is_file(), (
        f"{GATEWAY_CONFTEST} is missing. It carries the network guard SC-008's "
        f"count rests on; without it the zero is inferred from nothing."
    )
    return ast.parse(GATEWAY_CONFTEST.read_text(encoding="utf-8"))


def _guard_function() -> ast.FunctionDef:
    for node in ast.walk(_conftest_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == "no_outbound_network":
            return node
    raise AssertionError(
        "the gateway suite declares no `no_outbound_network` fixture, so no "
        "outbound connection attempt is observed (TR-067, SC-008)"
    )


def test_the_network_guard_exists() -> None:
    assert _guard_function() is not None


def test_the_guard_is_autouse() -> None:
    """Installed for every test rather than requested by the ones that remember.

    A guard a test has to opt into is a guard the one test that mattered
    forgot — and the test that reaches the network by accident is exactly the
    one whose author did not think it would.
    """
    decorators = [ast.unparse(decorator) for decorator in _guard_function().decorator_list]
    assert any("autouse=True" in decorator for decorator in decorators), (
        f"the network guard is not autouse: {decorators}. SC-008 counts requests "
        f"across the whole suite, not across the tests that asked to be watched."
    )


def test_the_guard_patches_both_connect_entry_points() -> None:
    """`connect` and `connect_ex` are separate entry points to the same act.

    A guard covering only `connect` would miss every client that uses the
    non-raising variant — and "the suite made no connections" would be true of
    the calls that were watched and false of the suite.
    """
    patched = {
        node.args[1].value
        for node in ast.walk(_guard_function())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    }
    assert {"connect", "connect_ex"} <= patched, (
        f"the guard patches {sorted(patched)}; both connect entry points must be "
        f"covered or the count is partial"
    )


def test_the_guard_raises_rather_than_returning_quietly() -> None:
    """A guard that logged and continued would let the suite pass while making
    the very requests SC-008 counts at zero."""
    raises = [node for node in ast.walk(_guard_function()) if isinstance(node, ast.Raise)]
    assert raises, "the network guard does not raise; it observes without objecting"


def test_the_permitted_destinations_are_loopback_only() -> None:
    """The guard must permit *something*: the invocation record is written over
    a socket, and forbidding all sockets would forbid the record.

    Loopback only, and asserted by name — a permitted host that was not loopback
    would be an external destination the suite may reach, which is precisely
    what TR-067 says only the provider-facing module may do, and only in
    opted-in `record` mode.
    """
    source = GATEWAY_NETGUARD.read_text(encoding="utf-8")
    tree = ast.parse(source)
    permitted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "LOOPBACK_HOSTS"
            for target in node.targets
        ):
            permitted = {
                element.value
                for element in ast.walk(node.value)
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    assert permitted, "the guard declares no permitted destinations"
    assert permitted <= {"127.0.0.1", "::1", "localhost"}, (
        f"the guard permits non-loopback destinations: "
        f"{sorted(permitted - {'127.0.0.1', '::1', 'localhost'})}. Only the "
        f"provider-facing module in opted-in record mode may reach outside "
        f"(TR-067)."
    )
    assert "provider" not in source.lower().split("loopback_hosts")[-1][:200], (
        "a provider host appears among the permitted destinations"
    )


def test_the_disclosed_limit_is_recorded_where_the_guard_is() -> None:
    """TR-067 requires the claim's *reach* to be stated rather than assumed.

    The guard sees the standard library's socket layer. A native extension that
    bypassed it would be invisible here and invisible to the import contract
    too. That residue has code review as its only guard, and a residue nobody
    wrote down is one the next reader will assume does not exist.
    """
    guard_docstring = ast.get_docstring(_guard_function()) or ""
    assert "disclosed limit" in guard_docstring.lower(), (
        "the network guard does not record the reach of what it observes; "
        "TR-067 requires the limit stated rather than assumed"
    )


def test_the_firing_control_lives_in_the_tier_that_installs_the_guard() -> None:
    """Where the *behavioural* control is, and why it is not here.

    Every assertion above is about the guard's shape, and a guard shaped
    correctly that never fires would satisfy all of them. The control that
    watches it fire has to run with the fixture installed — which is the gateway
    suite, not this one. Reproducing the guard here by `exec`-ing the conftest
    would test a copy of it and would pass while the installed one did nothing.

    So this asserts the control exists over there, and names it, rather than
    pretending to be it.
    """
    gateway_tests = REPO_ROOT / "src" / "gateway" / "tests" / "test_network_guard.py"
    assert gateway_tests.is_file(), (
        f"{gateway_tests} is missing. The shape assertions in this file are "
        f"satisfied by a guard that never fires; the behavioural control lives "
        f"in the tier where the fixture is installed."
    )
    source = gateway_tests.read_text(encoding="utf-8")
    assert "OutboundConnectionAttempted" in source, (
        "the gateway's network-guard control does not assert the guard's own "
        "failure type, so it may be passing for an unrelated reason"
    )
