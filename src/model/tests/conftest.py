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
"""

from __future__ import annotations

from hypothesis import settings

settings.register_profile("ci", max_examples=200, derandomize=True, deadline=None)
settings.load_profile("ci")
