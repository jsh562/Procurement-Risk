"""Violation B: the reach is a direct import of the external provider client.

The fixture's analogue of `model.corpus` importing `gateway` — reaching the
provider through the shared client is the same violation by another route, which
is why the real contract names both and why this fixture plants both.
"""

import anthropic

__all__ = ["anthropic"]
