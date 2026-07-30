"""Scratch stays inside the checkout, on the tier that actually writes it.

`AGENTS.md` §Temporary Files. The existing check lives under `src/api/tests/`,
which is the tier that downloads nothing and compiles nothing. This epic put a
model download and a native toolchain run in the gateway, so this is where the
rule now needs a verifier.

The rule is not a preference. A one-off task in this repository once installed a
~1 GB PyTorch environment under `%LOCALAPPDATA%\\Temp` and the machine's
antivirus blocked it as a dropper — correctly, because a gigabyte of unsigned
native DLLs appearing at once in a user-profile temp path is indistinguishable
from an attack by behaviour alone. ONNX Runtime writes intermediate files during
quantization and session creation, and those resolve through `tempfile` unless
`TMPDIR` says otherwise.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKOUT_TMP = REPO_ROOT / ".tmp"


def _inside_checkout(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - a path that cannot resolve is not inside
        return False
    return resolved == REPO_ROOT.resolve() or REPO_ROOT.resolve() in resolved.parents


@pytest.mark.parametrize("variable", ["TMPDIR", "TEMP", "TMP"])
def test_each_scratch_variable_points_inside_the_checkout(variable: str) -> None:
    """All three, because they are read by different things.

    Python's `tempfile` consults all three in order on Windows, and a subprocess
    may read whichever its own runtime prefers — so setting one is a redirect
    that works until something reads another.
    """
    value = os.environ.get(variable)
    if value is None:
        pytest.skip(f"{variable} is unset; the suite was not run under the documented command")
    assert _inside_checkout(Path(value)), (
        f"{variable} resolves to {value}, which is outside {REPO_ROOT}. "
        f"AGENTS.md requires temporary files inside this checkout's own .tmp/ — "
        f"a sibling checkout writing to a shared system temp directory is how "
        f"work belonging to one project becomes invisible to anyone looking at it."
    )


def test_tempfile_resolves_inside_the_checkout() -> None:
    """The property that actually matters, measured rather than inferred.

    Reading the environment variables says what was *requested*; this says what
    Python will actually do — and the two came apart once already in this
    project, when a path handed to PyTensor was flattened into a relative
    directory that was then created inside the source tree and committed.
    """
    if os.environ.get("TMPDIR") is None:
        pytest.skip("TMPDIR is unset; the suite was not run under the documented command")
    resolved = Path(tempfile.gettempdir())
    assert _inside_checkout(resolved), (
        f"tempfile.gettempdir() resolves to {resolved}, outside {REPO_ROOT}. "
        f"A redirect that silently degrades is worse than none, because it looks "
        f"like it worked."
    )


def test_a_temporary_file_is_actually_created_inside_the_checkout() -> None:
    """End to end: create one and look at where it landed.

    The strongest form of the check, and the cheapest. `gettempdir()` can be
    right while an override elsewhere sends a particular writer somewhere else.
    """
    if os.environ.get("TMPDIR") is None:
        pytest.skip("TMPDIR is unset; the suite was not run under the documented command")
    with tempfile.NamedTemporaryFile(prefix="gateway-scratch-", delete=True) as handle:
        created = Path(handle.name)
        assert _inside_checkout(created), (
            f"a temporary file was created at {created}, outside {REPO_ROOT}"
        )


def test_the_checkout_scratch_directory_is_gitignored() -> None:
    """Otherwise the rule trades one problem for a worse one.

    Scratch inside the checkout that is *not* ignored gets committed, and a
    build's intermediate files in version control is a bigger mess than the same
    files in a temp directory.
    """
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert any(
        line.strip() in {".tmp/", "*.tmp*/", "/.tmp/"} or line.strip().startswith(".tmp")
        for line in ignore.splitlines()
    ), ".tmp/ is not gitignored; scratch inside the checkout must not be committable"
