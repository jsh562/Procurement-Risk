"""Violation A: the public entry point imports the provider directly.

Directly, and not through the relay, because the contract this stands in for
allows the indirect path — the entry point composes through an orchestrator
that legitimately imports the provider. A laundered edge here would prove
nothing about the rule.
"""

from gateway_surface_fixture import provider

__all__ = ["provider"]
