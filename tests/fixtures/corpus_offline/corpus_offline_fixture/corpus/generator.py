"""Violation A: the reach is laundered through an ordinary intermediate module.

This is the one `allow_indirect_imports = false` exists for. With indirect
detection off the edge below is invisible — the generator imports a relay, and
the relay is the module that names the language-model client.
"""

from corpus_offline_fixture import relay

__all__ = ["relay"]
