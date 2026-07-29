"""The laundered violation — the evasion `allow_indirect_imports = False` catches.

A dependency routed through an intermediate module is still a dependency, and it
is the shape someone reaches for when the direct import fails the build.
"""

from boundary_fixture.relay import stale_check

__all__ = ["stale_check"]
