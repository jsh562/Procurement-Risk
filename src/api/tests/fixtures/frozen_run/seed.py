"""Load the frozen fixture into a database of its own, and commit.

The unit and integration suites seed inside a transaction they roll back, so
they leave the developer's database as they found it. The end-to-end specs
cannot: the page under test is served by a separate process, which sees only
committed rows.

**It seeds a dedicated database, never the shared one.** An earlier revision
truncated whatever `DATABASE_URL` pointed at and committed. That destroyed
E005's ~200-line procurement dataset, and `src/model`'s forecast tier — whose
`committed_dataset` fixture treats any non-empty `purchase_order_line` as proof
the right data is loaded — then fitted against E010's 16 fixture lines and
refused with "the sojourn frame is empty". CI never caught it because the model
step happens to run before the seed step, an ordering nothing enforces.

Two epics wanting to own the contents of one table is a resource conflict, and
the fix is to stop sharing the resource rather than to sequence the sharing more
carefully. This script therefore derives its own database name from the target,
creates it if absent, applies the migration chain, and seeds there. The shared
development database is never written to.

Run with::

    uv run --directory src/api python tests/fixtures/frozen_run/seed.py

It prints the URL it seeded, which is what `playwright.config.ts` hands to the
serving boundary.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fixtures.frozen_run import close_fixture_line, seed_frozen_run  # noqa: E402

#: Credentials E001 commits on purpose and declares non-secret. A URL that does
#: not carry them is not a local development server, and this script creates and
#: truncates databases.
DEVELOPMENT_MARKER = "local-development-only"

#: Suffix for the database this tier owns. Derived rather than configured so the
#: two can never be pointed at the same place by a stray environment variable.
E2E_SUFFIX = "_e2e"

#: `parents[5]` is the repository root — this file sits at
#: `src/api/tests/fixtures/frozen_run/`, so [0] is `frozen_run`, [1] `fixtures`,
#: [2] `tests`, [3] `api`, [4] `src`, [5] the root.
REPO_ROOT = Path(__file__).resolve().parents[5]


def e2e_url(url: str) -> str:
    """The dedicated database's URL, derived from the shared one."""
    parts = urlparse(url)
    name = parts.path.lstrip("/")
    if name.endswith(E2E_SUFFIX):
        return url
    return urlunparse(parts._replace(path=f"/{name}{E2E_SUFFIX}"))


def _server_url(url: str) -> str:
    """The same server, pointed at `postgres` — you cannot CREATE DATABASE from
    inside the database you are creating."""
    return urlunparse(urlparse(url)._replace(path="/postgres"))


def ensure_database(url: str) -> bool:
    """Create the database if it does not exist. Returns True if it was created."""
    name = urlparse(url).path.lstrip("/")
    with (
        psycopg.connect(_server_url(url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if cursor.fetchone():
            return False
        # The name is derived from `DATABASE_URL`, not from user input, and the
        # identifier cannot be parameterised — but it is still quoted rather
        # than interpolated bare.
        cursor.execute(f'CREATE DATABASE "{name}"')
    return True


def apply_migrations(url: str) -> None:
    """Run the committed migration chain against the dedicated database.

    Shelling out to the same `migrate` console script the workflow's
    `Apply the migration chain (model)` step runs, rather than importing alembic
    here: the chain is `src/model`'s to own, and a second invocation path would
    be a second thing to keep in step with it.
    """
    result = subprocess.run(
        ["uv", "run", "--directory", "src/model", "migrate"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"the migration chain failed against {urlparse(url).path.lstrip('/')!r}:\n"
            f"{result.stderr}"
        )


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is unset.", file=sys.stderr)
        return 1

    if DEVELOPMENT_MARKER not in url:
        print(
            f"Refusing to seed: DATABASE_URL does not carry the {DEVELOPMENT_MARKER!r} marker "
            "that identifies the committed development credentials. This script creates and "
            "truncates a database, so it runs only where doing so is intended.",
            file=sys.stderr,
        )
        return 1

    target = e2e_url(url)
    name = urlparse(target).path.lstrip("/")

    if ensure_database(target):
        print(f"created {name}")
    apply_migrations(target)

    with psycopg.connect(target) as connection:
        # Still a truncation, but of a database this tier owns outright. Idempotent
        # so a second run replaces the fixture rather than colliding with it.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM line_posterior")
            cursor.execute("DELETE FROM forecast_run")
            cursor.execute("DELETE FROM lifecycle_event")
            cursor.execute("DELETE FROM purchase_order_line")

        document = seed_frozen_run(connection)
        anchor = date.fromisoformat(document["run"]["as_of_date"])
        for line in document["lines"]:
            if line["case"] == "terminal_line":
                close_fixture_line(connection, line["po_line_id"], anchor=anchor)

        connection.commit()

    print(f"seeded {len(document['lines'])} lines from the frozen fixture into {name}")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
