"""An ordinary corpus module, and the laundering path.

Nothing here is a violation. It reads the templates and the renderer because
that is its job — generating the corpus. It exists in this fixture because it
is what a baseline reaches *through*: importing this module is one hop from the
answer key, leaves no direct edge to `templates` or `render`, and is exactly
what `allow_indirect_imports = False` is set to catch.
"""

from baseline_fixture.corpus import render, templates

__all__ = ["render", "templates"]
