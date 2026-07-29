"""Test configuration for the modeling boundary.

`plan.md` §Property-Based Test Specification fixes the Hypothesis budget for
this entry: 200 examples, derandomized, no deadline. The profile is registered
here and loaded unconditionally rather than behind an environment switch, so
the verification workflow — which runs `pytest tests` with no extra
configuration — selects it by running the suite at all.

`derandomize=True` is the load-bearing setting. These tests judge determinism;
a determinism gate that generates a different population on each run reports
defects nobody can reproduce, and a CI failure a developer cannot reproduce
locally is a failure nobody fixes.

`deadline=None` because the digest path is cheap but the first example in a
process pays import and JIT-free interpreter warm-up; a per-example deadline
turns that into a flaky failure unrelated to the property under test.

Temporary files are redirected into the checkout's own gitignored `.tmp/`.
PyTensor's Numba backend writes a generated Python module per model build
through `tempfile` and imports it, and `tmp_path` writes there too, so an
unredirected suite scatters thousands of files through the system temp
directory. `tempfile.tempdir` is assigned rather than `TMPDIR` exported,
because `gettempdir()` caches on first call and a later env var is silently
ignored. The path is derived from this file, so each sibling checkout uses its
own `.tmp/` with no per-instance configuration and no absolute path to drift.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import settings

settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None)
settings.load_profile("ci")

#: This file is `<repo>/src/model/tests/conftest.py`, so the repository root is
#: four levels up. Gitignored, hence `mkdir` — it does not exist on a fresh clone.
_PYTEST_TMP = Path(__file__).resolve().parents[3] / ".tmp" / "pytest"
_PYTEST_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_PYTEST_TMP)

# PyTensor's C `base_compiledir` is deliberately *not* redirected here, and the
# reason is worth keeping. Redirecting it was tried and reverted: it empties the
# cache, PyTensor then has to build `lazylinker_ext`, and on a machine where it
# locates `g++` without that directory on `PATH` the build fails — seven tests in
# `test_model_logp.py` went red with `ImportError: Version check of the existing
# lazylinker compiled file`. Nothing is gained by moving it either. That cache
# lives under `AppData\Local\PyTensor`, not under the system temp directory, it is
# written once rather than per build, and it has never been flagged. Redirecting
# it would trade a real breakage for no reduction in temp-directory churn.
