"""Database schema assets for the whole repository.

ADR-0013: `/src/model` owns every migration and is the only entry that declares
a database client. `/src/api` and `/src/gateway` neither import this package nor
carry a copy of the DDL -- the serving boundary reads what it needs (the
`schema_constants` row, TR-047) over the connection, which is what lets ADR-0010's
"neither Python boundary depends on the other" rule stand without an exception.

Contents:

- `env.py`      -- the Alembic migration environment, configured by the entry's
                   `alembic.ini` (`script_location` points here).
- `versions/`   -- the forward-only migration chain. Filename prefixes `0001`-`0010`
                   sit inside E003's reserved `0001`-`0099` block (TR-004).

Deliberately not a re-export surface. Migration modules are loaded by Alembic
from their file paths, never imported by name, and importing `env.py` outside a
migration run would touch `alembic.context`, which only exists during one.
"""

from __future__ import annotations

__all__: list[str] = []
