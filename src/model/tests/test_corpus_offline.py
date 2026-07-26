"""VR-043 / FR-022 / SC-014: the generator performs no network I/O.

**The observation window is the point, and it is stated rather than assumed.**
A socket guard installed inside an already-running pytest process cannot cover
the generator's import, because `model.corpus` was imported by the first test
module that touched it — long before any guard existed. An import-time fetch
would then sit outside the window entirely, and the assertion would be about
calls made during the run rather than about the module.

Every case below therefore runs in a **fresh interpreter**: the guard is
installed first, the script asserts that no `model.corpus` module is in
`sys.modules` yet, and only then is the generator imported and run. The guard
stays installed until the process exits.

The static half of FR-022 — that no language model is invoked — is a different
mechanism and lives elsewhere: an `import-linter` forbidden contract over the
module graph (VR-044), with its own committed negative fixture under
`tests/fixtures/corpus_offline/`. A socket guard cannot see an import edge and a
contract cannot see a socket, which is why both exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# The guard, as source rather than as a helper import: it has to be installed
# before anything else in the child process, and a helper imported to install it
# would already have run an import the guard was meant to cover.
GUARD = """
import socket
import sys


class OutboundConnectionAttempted(RuntimeError):
    '''VR-043: raised on any attempt to open an outbound connection.'''


def _refuse(*args, **kwargs):
    raise OutboundConnectionAttempted(
        "VR-043: the generator attempted a network connection: "
        + repr(args[1:2] or args[:1])
    )


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.socket.sendto = _refuse
socket.create_connection = _refuse

# The window, asserted rather than described: nothing of the package under
# observation may have been imported before the guard was installed.
_early = sorted(name for name in sys.modules if name.startswith("model.corpus"))
assert not _early, "VR-043: the generator package was imported before the guard: " + repr(_early)
"""

GENERATE = """
from pathlib import Path

from model.corpus.generate import generate_corpus

result = generate_corpus(Path(ROOT))
print("VR-043 OK", result.document_count)
"""

IMPORT_TIME_FETCH = """
import pathlib
import sys

# A module that reaches the network *at import*, which is the case the window
# exists to cover. Written into the child's own temporary directory so nothing
# resembling it is ever committed.
module = pathlib.Path(ROOT) / "eager_fetcher.py"
module.write_text(
    "import socket\\n"
    "socket.create_connection(('example.invalid', 443), timeout=1)\\n",
    encoding="utf-8",
)
sys.path.insert(0, str(pathlib.Path(ROOT)))
import eager_fetcher  # noqa: F401
print("VR-043 UNGUARDED")
"""

DIRECT_ATTEMPT = """
import socket

socket.create_connection(("example.invalid", 443), timeout=1)
print("VR-043 UNGUARDED")
"""


def run_guarded(body: str, root: Path) -> subprocess.CompletedProcess[str]:
    """Run `body` in a fresh interpreter with the guard installed first."""
    script = f"ROOT = {json.dumps(str(root))}\n{GUARD}\n{body}"
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_vr_043_the_generator_runs_to_completion_under_the_socket_guard(tmp_path: Path) -> None:
    """The positive control: a whole layer generated with the guard installed.

    Writes into `tmp_path`, never the working copy — the committed layer is
    written once, by its own run.
    """
    root = tmp_path / "corpus"
    root.mkdir()
    completed = run_guarded(GENERATE, root)
    assert completed.returncode == 0, (
        f"VR-043: the guarded generator did not complete\n{completed.stdout}\n{completed.stderr}"
    )
    assert "VR-043 OK" in completed.stdout, completed.stdout
    assert list((root / "synthetic").glob("*/manifest.json")), "no layer was written"


def test_vr_043_the_guard_refuses_an_outbound_connection(tmp_path: Path) -> None:
    """The failing direction: a guard that cannot fire evidences nothing."""
    completed = run_guarded(DIRECT_ATTEMPT, tmp_path)
    assert completed.returncode != 0, f"VR-043: the guard did not fire\n{completed.stdout}"
    assert "OutboundConnectionAttempted" in completed.stderr, completed.stderr
    assert "VR-043 UNGUARDED" not in completed.stdout, completed.stdout


def test_vr_043_an_import_time_fetch_is_inside_the_observation_window(tmp_path: Path) -> None:
    """The half a run-time-only guard would miss.

    A module fetching at import is caught here because the guard was installed
    before any import of the package under observation, which is exactly what
    the window's stated boundary buys.
    """
    completed = run_guarded(IMPORT_TIME_FETCH, tmp_path)
    assert completed.returncode != 0, (
        f"VR-043: an import-time fetch escaped the window\n{completed.stdout}"
    )
    assert "OutboundConnectionAttempted" in completed.stderr, completed.stderr
    assert "VR-043 UNGUARDED" not in completed.stdout, completed.stdout


def test_vr_043_the_window_assertion_itself_can_fail(tmp_path: Path) -> None:
    """The guard's own precondition, exercised in its failing direction.

    Importing the generator *before* the guard is installed must be refused;
    otherwise the window assertion is a comment rather than a check, and a
    future edit that moved the import above the guard would go unnoticed.
    """
    script = (
        f"ROOT = {json.dumps(str(tmp_path))}\n"
        "import model.corpus.generate  # deliberately before the guard\n"
        f"{GUARD}\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0, completed.stdout
    assert "imported before the guard" in completed.stderr, completed.stderr
