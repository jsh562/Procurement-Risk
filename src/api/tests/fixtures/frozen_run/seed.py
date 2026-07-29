"""Load the frozen fixture into a database, and commit.

The unit and integration suites seed inside a transaction they roll back, so
they leave the developer's database as they found it. The end-to-end specs
cannot: the page under test is served by a separate process, which sees only
committed rows.

Run with::

    uv run --directory src/api python tests/fixtures/frozen_run/seed.py

**This truncates the procurement tables first.** That is safe for the fixture's
purpose and would not be for anything else, so it refuses to run against a
database that does not look like a development one — a seeding script pointed at
the wrong `DATABASE_URL` is a data-loss incident with a helpful error message.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fixtures.frozen_run import close_fixture_line, seed_frozen_run  # noqa: E402

#: Credentials E001 commits on purpose and declares non-secret. A URL that does
#: not carry them is not the local development database, and this script
#: truncates tables.
DEVELOPMENT_MARKER = "local-development-only"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is unset.", file=sys.stderr)
        return 1

    if DEVELOPMENT_MARKER not in url:
        print(
            f"Refusing to seed: DATABASE_URL does not carry the {DEVELOPMENT_MARKER!r} marker "
            "that identifies the committed development credentials. This script truncates the "
            "procurement tables, so it runs only where losing their contents is intended.",
            file=sys.stderr,
        )
        return 1

    with psycopg.connect(url) as connection:
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

    print(f"seeded {len(document['lines'])} lines from the frozen fixture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
