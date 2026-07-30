#!/usr/bin/env python
"""Start the worklist locally, on this checkout's database and on free ports.

Four checkouts of this repository sit on this machine, each with its own Compose
project and therefore its own database container on its own published port. This
script asks Compose which one is *this* checkout's rather than assuming, resolves
free ports for both servers, wires the two to each other, retries if it loses a
port race, and publishes what it ended up with so a sibling process can find it.

Usage, from the repository root:

    uv run python scripts/dev.py                          # frozen demo fixture
    uv run python scripts/dev.py --database procurement   # the real forecast run

Ctrl-C stops both servers and does not return while one still holds a port.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _launcher import (  # noqa: E402
    MAX_ATTEMPTS,
    REPO_ROOT,
    STARTUP_GRACE_SECONDS,
    Child,
    DatabaseNotFound,
    configure_streams,
    lost_the_port,
    ports_file,
    publish,
    resolve_database_port,
    terminate,
    unpublish,
)
from helpers.ports import Resolution, resolve_host_port  # noqa: E402

configure_streams()

#: Names this launcher's published record. Separate from the suite's, so the
#: two can run together without one erasing the other.
ROLE = "dev"

#: Local-only credentials. The e2e seed refuses a URL without this marker.
DB_CREDENTIALS = "procurement:local-development-only"

#: Preferred, never assumed, and deliberately disjoint from `e2e.py`'s. Both
#: launchers used to prefer web 3000, so running the suite while the dev server
#: was up contended every single time and only worked because the resolver walked
#: around it. Two launchers in one checkout should not need the resolver at all.
PREFERRED = {"api": 8001, "web": 3000}


def resolve_ports() -> dict[str, Resolution]:
    """Resolve both ports, and say plainly which ones moved."""
    resolved = {name: resolve_host_port(port, name=name) for name, port in PREFERRED.items()}
    for resolution in resolved.values():
        print(f"  {resolution.describe()}", flush=True)
    return resolved


def spawn(ports: dict[str, Resolution], database_url: str, scratch: str) -> list[Child]:
    """Start both servers, each told about the other's resolved port."""
    api_port, web_port = ports["api"].port, ports["web"].port
    api_base = f"http://127.0.0.1:{api_port}"
    shared = {
        **os.environ,
        "PYTHONUTF8": "1",
        "UV_NATIVE_TLS": "1",
        # project-instructions.md § Temporary Files: the checkout's own gitignored
        # scratch directory, forward-slashed.
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
        # The names the rest of the repository reads a port through, so anything
        # this process spawns inherits the resolution instead of the defaults.
        "WORKLIST_API_PORT": str(api_port),
        "WORKLIST_WEB_PORT": str(web_port),
        "PRC_API_PORT": str(api_port),
        "PRC_WEB_PORT": str(web_port),
    }

    api = subprocess.Popen(
        [
            "uv",
            "run",
            "--directory",
            "src/api",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env={
            **shared,
            "DATABASE_URL": database_url,
            "WORKLIST_TIMEZONE": "UTC",
            # Both origins, because the browser sends one of them and a blocked
            # re-query reads as an interface bug rather than a deployment one.
            "WORKLIST_ALLOWED_ORIGINS": (
                f"http://127.0.0.1:{web_port},http://localhost:{web_port}"
            ),
        },
    )
    web = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(web_port)],
        cwd=REPO_ROOT / "src" / "web",
        env={
            **shared,
            # Both spellings are required and not redundant: the page is a server
            # component whose first fetch runs in Node, while every adjustment
            # fetches from the browser and Next inlines only NEXT_PUBLIC_ values.
            "WORKLIST_API_BASE_URL": api_base,
            "NEXT_PUBLIC_WORKLIST_API_BASE_URL": api_base,
        },
        shell=os.name == "nt",
    )
    return [Child("api", api_port, api), Child("web", web_port, web)]


def settle(children: list[Child]) -> list[Child]:
    """Wait out the startup window; return the children that died to a collision.

    A child that loses a port race exits almost immediately, so the window only has
    to outlast process spawn. One that is merely slow to build never exits at all.
    """
    deadline = time.monotonic() + STARTUP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if any(child.process.poll() is not None for child in children):
            break
        time.sleep(0.25)
    time.sleep(0.4)  # let a straggler's port actually be released or claimed
    return [c for c in children if c.process.poll() is not None and lost_the_port(c)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the worklist locally.")
    parser.add_argument(
        "--database",
        default="procurement_e2e",
        help=(
            "Database to serve. The default is the frozen 16-line fixture, which "
            "exercises every degraded state. `procurement` is E005's 199-line set "
            "and needs an active forecast run; without one the page renders "
            "no_active_run."
        ),
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=None,
        help="Override this checkout's published Postgres port. Discovered from "
        "Compose when omitted.",
    )
    args = parser.parse_args()

    try:
        db_port = resolve_database_port(args.db_port)
    except DatabaseNotFound as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    database_url = f"postgresql://{DB_CREDENTIALS}@localhost:{db_port}/{args.database}"
    print(f"Database: {database_url}")

    scratch = str(REPO_ROOT / ".tmp").replace("\\", "/")
    Path(scratch).mkdir(exist_ok=True)

    children: list[Child] = []
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"\nResolving ports (attempt {attempt} of {MAX_ATTEMPTS}):")
            ports = resolve_ports()
            children = spawn(ports, database_url, scratch)

            collided = settle(children)
            if not collided:
                break

            for child in collided:
                print(
                    f"  {child.name} lost port {child.port} between resolving it and "
                    f"binding it — something else holds it now. Re-resolving.",
                    file=sys.stderr,
                )
            terminate(children)
            children = []
        else:
            print(
                f"\nGave up after {MAX_ATTEMPTS} attempts: a port was taken out from "
                f"under this process every time.",
                file=sys.stderr,
            )
            return 1

        dead = [c for c in children if c.process.poll() is not None]
        if dead:
            for child in dead:
                print(
                    f"\n{child.name} exited with {child.process.returncode} — not a "
                    f"port collision, since {child.port} is free. See its output above.",
                    file=sys.stderr,
                )
            return 1

        # Published only now. Publishing at resolution time is wrong the moment a
        # retry moves a port: the record would name a port nothing is listening on.
        api_port = next(c.port for c in children if c.name == "api")
        web_port = next(c.port for c in children if c.name == "web")
        publish(
            ROLE,
            {
                "database": args.database,
                "db_port": db_port,
                "api": api_port,
                "web": web_port,
                "api_base_url": f"http://127.0.0.1:{api_port}",
                "worklist_url": f"http://127.0.0.1:{web_port}/worklist",
            },
        )

        print(f"\n  api        http://127.0.0.1:{api_port}/api/v1/worklist")
        print(f"  worklist   http://127.0.0.1:{web_port}/worklist")
        print(f"  published  {ports_file(ROLE).relative_to(REPO_ROOT).as_posix()}")
        print("\nCtrl-C to stop both.\n", flush=True)

        while all(child.process.poll() is None for child in children):
            time.sleep(0.5)
        for child in children:
            if child.process.poll() is not None:
                print(f"\n{child.name} exited with {child.process.returncode}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
        return 0
    finally:
        terminate(children)
        unpublish(ROLE)


if __name__ == "__main__":
    raise SystemExit(main())
