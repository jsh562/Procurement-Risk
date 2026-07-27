"""Fixture package: the public surface reaching the provider module.

Stands in for **both** of E004's public-surface contracts (TR-002), which is
why this directory declares two. The edge matters because `provider.py`
legitimately imports the SDK, so anything importing it can receive an SDK
object and re-expose it in a signature without ever importing the SDK itself —
which the single-provider-import contract cannot see.

The two halves get different strictness in the real manifest, and the fixture
mirrors that rather than flattening it:

- `models` and `errors` define types and nothing else, so no legitimate path
  takes them to the provider by any route. Indirect detection is ON, and
  `models` violates it through a relay to prove the laundered edge is caught.
- `api` is the composition front door. `api -> orchestrator -> provider` is the
  designed arrangement, so only the *direct* edge is forbidden, and `api`
  violates it directly.

The complementary property — that the legitimate indirect path is **not**
flagged — is evidenced by the real package rather than here: `gateway.api`
imports `gateway.orchestrator`, which imports `gateway.provider`, and all four
contracts report KEPT. A fixture cannot show that, because a fixture exists to
be broken.
"""
