"""Fixture package: the public surface reaching the provider module.

Stands in for E004's public-surface contract (TR-002). The real one forbids
`gateway.api`, `gateway.models` and `gateway.errors` from reaching
`gateway.provider`. That edge matters because `provider.py` legitimately
imports the SDK, so anything importing it can receive an SDK object and
re-expose it in a signature without ever importing the SDK itself — which the
single-provider-import contract cannot see.
"""
