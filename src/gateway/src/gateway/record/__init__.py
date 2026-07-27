"""Persistence for the invocation record: the writer, the spool, the drain.

Three modules, and the split is the durability argument rather than tidiness.
`writer.py` commits to PostgreSQL on the gateway's own connection; `spool.py`
holds a record locally when that write fails after a provider request has
already been issued; `reconcile.py` moves spooled records into the invocation
table on the next successful connection.

The spool exists because of one case: a call that was **billed** and whose
record could not be written. Without it, TR-011's hundred-percent tracing claim
would be false on exactly the failure the claim exists to exclude, and the
alternative — narrowing the claim's denominator — puts an asterisk on the
product's loudest guarantee ({SAD:ADR-0015}).

Spooling does **not** soften the fail-closed rule (TR-041). A caller whose
record could not be committed to Postgres still receives an error and no
validated value; what the spool changes is that the record survives to be
reconciled rather than being lost with the exception.
"""

from __future__ import annotations

__all__: list[str] = []
