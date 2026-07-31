"""Both thread counts are set on the session, not left to the runtime.

Spec FR-038. The counts were threaded from configuration through the api's
lifespan into `session_for`, and nothing asserted they arrived — so the whole
chain could be intact except for the one line that applies them, and every test
would still pass.

**Why an unset count is invisible rather than loud.** ONNX Runtime's default is
one thread per core *the operating system reports*, which under a CPU quota is
the host's core count rather than the container's. A runtime that picks the
count itself also pins thread affinity. So an unset count oversubscribes
silently: no error, no warning, just a latency figure that is worse than it
should be with nothing to attribute it to — and FR-033's figure is measured on
exactly that path.

**Read back from the created session, not from the options object.** Asserting
that `SessionOptions.intra_op_num_threads` holds what was assigned to it would
be asserting that Python attribute assignment works. What matters is whether the
runtime honoured it, which is a different question and the only one worth
asking.

**These assertions are not vacuous, and that was measured rather than assumed.**
A session created with default options reports **0** for both counts — the
runtime's "decide for me" sentinel, not a core count. So every assertion below
distinguishes an applied setting from an unapplied one, including the one that
requests the value the environment happens to want.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.inference.session import SessionError, session_for

ENCODER = Path(__file__).resolve().parents[3] / "data" / "encoder"
GRAPH = ENCODER / "model.onnx"

pytestmark = pytest.mark.skipif(
    not GRAPH.is_file(),
    reason="a real graph is needed; the assertion is about what the runtime honoured",
)


def _session_options(intra: int, inter: int):
    """One session, created the way the serving path creates it."""
    return session_for(GRAPH, intra_op_threads=intra, inter_op_threads=inter)


def test_both_counts_reach_the_created_session() -> None:
    """FR-038's derivation rule, as applied: intra-op from the quota, inter-op one.

    One and one is what the environment FR-033 fixes produces — a single vCPU.
    """
    session = _session_options(1, 1)
    options = session.get_session_options()  # type: ignore[attr-defined]
    assert options.intra_op_num_threads == 1
    assert options.inter_op_num_threads == 1


def test_a_different_count_is_honoured_rather_than_ignored() -> None:
    """The direction that decays silently.

    Asserting only the value the default happens to produce would pass against
    code that never applied the setting at all. Two is requested here precisely
    because it is not what an unset session would land on under a quota.
    """
    session = _session_options(2, 1)
    options = session.get_session_options()  # type: ignore[attr-defined]
    assert options.intra_op_num_threads == 2, (
        "the requested intra-op count did not reach the session; an unset count "
        "silently oversubscribes under a CPU quota and degrades FR-033's figure "
        "with no error to attribute it to"
    )


def test_the_cpu_provider_is_named_explicitly() -> None:
    """So a machine with an accelerator does not quietly produce other numbers.

    A session that fell back to a different execution provider would still
    answer every query correctly and would invalidate every latency figure
    measured against it.
    """
    session = _session_options(1, 1)
    assert session.get_providers() == ["CPUExecutionProvider"]  # type: ignore[attr-defined]


def test_a_graph_that_cannot_load_raises_this_package_s_error(tmp_path: Path) -> None:
    """Not a bare `onnxruntime` exception leaking through the boundary.

    The gateway owns the error type so a caller can handle a session failure
    without importing the runtime — which is the same reason the provider
    wrapper exists.
    """
    broken = tmp_path / "not-a-graph.onnx"
    broken.write_bytes(b"this is not a serialised graph")
    with pytest.raises(SessionError, match="cannot create an ONNX Runtime session"):
        session_for(broken, intra_op_threads=1, inter_op_threads=1)
