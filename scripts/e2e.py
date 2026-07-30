#!/usr/bin/env python
"""Run the Playwright suite against this checkout's database, on free ports.

Two things this exists to prevent, both of which produce wrong results rather
than failures:

**Seeding a sibling's database.** `seed.py` opens by deleting four tables, and it
derives `procurement_e2e` from whatever URL it is handed. Four checkouts sit on
this machine, each with its own database container on its own published port — so
a hardcoded port means a run here deletes and re-seeds a *sibling's* fixture while
that sibling is using it. The port is asked of Compose, which resolves per
checkout, and there is no fallback: an absent database is a refusal.

**Adopting a foreign server.** `playwright.config.ts` no longer reuses an existing
server, because the thing usually sitting on the default port is the developer's
own `scripts/dev.py` — serving a different database from the one every spec here
asserts against.

Any argument is passed through:

    uv run python scripts/e2e.py
    uv run python scripts/e2e.py -g "the presentation contract"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _launcher import (  # noqa: E402
    MAX_ATTEMPTS,
    REPO_ROOT,
    DatabaseNotFound,
    configure_streams,
    ensure_database,
    publish,
    unpublish,
)

from tests.checks.helpers.ports import is_bindable, resolve_host_port  # noqa: E402

configure_streams()

#: Deliberately disjoint from `dev.py`'s 8001/3000. Both launchers preferring web
#: 3000 meant running the suite while the dev server was up contended every time,
#: and only worked because the resolver walked around it. Two launchers in one
#: checkout should not need the resolver at all.
PREFERRED = {"api": 8100, "web": 3100}

#: Separate from `dev.py`'s record, so a suite run does not erase a live dev
#: server's entry and then delete it on the way out.
ROLE = "e2e"

DB_CREDENTIALS = "procurement:local-development-only"


def main() -> int:
    try:
        db_port = ensure_database()
    except DatabaseNotFound as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    scratch = str(REPO_ROOT / ".tmp").replace("\\", "/")
    Path(scratch).mkdir(exist_ok=True)
    shared_db = f"postgresql://{DB_CREDENTIALS}@localhost:{db_port}/procurement"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "UV_NATIVE_TLS": "1",
        "MSYS_NO_PATHCONV": "1",
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
    }

    print(f"Database: {shared_db}")
    print("Seeding the frozen fixture (writes only to procurement_e2e):", flush=True)
    seeded = subprocess.run(
        ["uv", "run", "--directory", "src/api", "python", "tests/fixtures/frozen_run/seed.py"],
        cwd=REPO_ROOT,
        env={**env, "DATABASE_URL": shared_db},
        capture_output=True,
        text=True,
    )
    if seeded.returncode != 0:
        sys.stderr.write(seeded.stdout + seeded.stderr)
        return seeded.returncode
    print(f"  {seeded.stdout.strip().splitlines()[0]}", flush=True)

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"\nResolving ports (attempt {attempt} of {MAX_ATTEMPTS}):")
            resolved = {
                name: resolve_host_port(port, name=name) for name, port in PREFERRED.items()
            }
            for resolution in resolved.values():
                print(f"  {resolution.describe()}", flush=True)

            api_port, web_port = resolved["api"].port, resolved["web"].port
            # Published before the run rather than after, because unlike `dev.py`
            # this process is the suite: there is no steady state to publish from,
            # and a run in progress is exactly what a sibling wants to know about.
            publish(
                ROLE,
                {
                    "database": "procurement_e2e",
                    "db_port": db_port,
                    "api": api_port,
                    "web": web_port,
                    "api_base_url": f"http://127.0.0.1:{api_port}",
                    "worklist_url": f"http://127.0.0.1:{web_port}/worklist",
                },
            )
            print(flush=True)

            completed = subprocess.run(
                ["npx", "playwright", "test", *sys.argv[1:]],
                cwd=REPO_ROOT / "src" / "web",
                env={
                    **env,
                    "WORKLIST_API_PORT": str(api_port),
                    "WORKLIST_WEB_PORT": str(web_port),
                },
                shell=os.name == "nt",
            )
            if completed.returncode == 0:
                return 0

            # Playwright fails the same way whether a spec broke or a server could
            # not bind, so the ports are asked about directly. Both free means the
            # servers came up and the specs are what failed — retrying that would
            # only fail again, more slowly.
            stolen = [
                name for name, resolution in resolved.items() if not is_bindable(resolution.port)
            ]
            if not stolen:
                return completed.returncode
            print(
                f"\n  {', '.join(stolen)} could not hold its port — something else has "
                f"it now. Re-resolving and re-running.",
                file=sys.stderr,
            )
        print(
            f"\nGave up after {MAX_ATTEMPTS} attempts: a port was taken out from under "
            f"this run every time.",
            file=sys.stderr,
        )
        return 1
    finally:
        unpublish(ROLE)


if __name__ == "__main__":
    raise SystemExit(main())
