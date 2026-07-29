"""FR-024: the interface tier declares and installs no database driver.

The observable form of "the interface tier opens no datastore connection". Two
assertions, over the manifest and over the lockfile, because a direct-dependency
check would miss a driver arriving underneath something else and a transitive
driver is as connectable as a declared one.

**Why this lives at the repository root** rather than under `src/api/tests/`.
The artifact under assertion is `/src/web`'s dependency manifest, which no
Python entry owns — and asserting it from inside `src/api` would make one
entry's suite the guardian of another entry's boundary. That is the same
exception `test_orchestration.py`, `test_layout.py` and
`test_network_free_required_checks.py` sit under, and it is the placement T026
and `plan.md` § Observation procedures both name.

QC iteration 1 (T062) found these assertions running from `src/api`'s suite
instead. They ran and they passed; the placement contradicted the plan that
justified it. Moved rather than the plan amended, because the rationale for the
root placement is the correct one.

The *request-set* half of FR-024 — that the page's data actually arrives over
HTTP from the serving boundary — stays in `src/api/tests/test_read_path_isolation.py`
beside the read path it concerns. Either site alone is escapable: a driver could
be vendored rather than declared, or declared and reached only on a path no test
runs.
"""

from __future__ import annotations

import json
from pathlib import Path

#: `parents[2]` is the repository root — this file sits at `tests/checks/`.
WEB = Path(__file__).resolve().parents[2] / "src" / "web"

#: Distributions that speak a database wire protocol. A dependency on any of
#: them in the interface tier is a datastore connection waiting to be opened,
#: whether or not any code path currently opens one — "no code calls it yet" is
#: a property of today's code, not of the boundary.
DATABASE_DRIVERS = frozenset(
    {
        "pg",
        "pg-promise",
        "postgres",
        "postgresql",
        "node-postgres",
        "mysql",
        "mysql2",
        "mongodb",
        "mongoose",
        "sqlite3",
        "better-sqlite3",
        "prisma",
        "@prisma/client",
        "drizzle-orm",
        "typeorm",
        "sequelize",
        "knex",
        "kysely",
    }
)


def test_the_interface_tier_declares_no_database_driver() -> None:
    """The manifest: nothing named directly."""
    manifest = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    declared = set(manifest.get("dependencies", {})) | set(manifest.get("devDependencies", {}))

    offending = declared & DATABASE_DRIVERS
    assert not offending, f"the web boundary declares {sorted(offending)}"


def test_no_database_driver_reaches_the_interface_tier_transitively() -> None:
    """The lockfile: nothing arriving underneath something else."""
    lockfile = json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    installed = {
        path.rsplit("node_modules/", 1)[-1] for path in lockfile.get("packages", {}) if path
    }

    offending = installed & DATABASE_DRIVERS
    assert not offending, f"a database driver is installed in the web boundary: {sorted(offending)}"


def test_the_driver_list_is_not_empty() -> None:
    """The two assertions above are set intersections. An empty allowlist would
    make both pass unconditionally and say nothing — this is what keeps them
    from being vacuous."""
    assert len(DATABASE_DRIVERS) > 10
    assert "pg" in DATABASE_DRIVERS, "the driver Postgres clients actually use"
