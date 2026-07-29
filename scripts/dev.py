#!/usr/bin/env python
"""Start the worklist locally on ports that are actually free.

Every service here has a preferred port and no claim on it. Several checkouts sit
on one machine, unrelated software holds conventional numbers — a long-running
`python -m http.server` owns 8000 on the machine this was written on — and a dev
server orphaned by an earlier session holds whatever it was given. A fixed port
is therefore a coin flip, and starting a service to find out is how a collision
becomes a misdiagnosis: an orphaned `next` process on 3000 once failed four
orchestration checks, and the dev server was twice ruled out as the cause before
the process was found.

So resolution happens here, before anything starts, through the same
`resolve_host_port` the orchestration checks use — which prefers the committed
default, refuses to substitute a conventional port, consults Docker's own
published-port list as well as a socket probe, and reports what displaced it.

Usage, from the repository root:

    uv run python scripts/dev.py                          # frozen demo fixture
    uv run python scripts/dev.py --database procurement   # the real forecast run

Ctrl-C stops both servers and does not return while one still holds a port.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The resolver's `describe()` carries an em dash, and this process inherits the
# console's encoding rather than the UTF-8 it hands its children. On a cp1252
# console that prints as a replacement character at best and raises at worst, so
# the stream is reconfigured before anything is written to it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# `tests/checks` is not an installed package; the resolver lives there because the
# orchestration checks were its first consumer. Imported by path rather than
# copied, so the two cannot drift — a second implementation of "is this port
# free" is a second set of blind spots to find out about the hard way.
sys.path.insert(0, str(REPO_ROOT / "tests" / "checks"))

from helpers.ports import Resolution, resolve_host_port  # noqa: E402

#: Local-only credentials. The e2e seed refuses a URL without this marker.
DB_BASE = "postgresql://procurement:local-development-only@localhost"

#: Preferred, never assumed. These are the same numbers `docker-compose.yml`
#: publishes through `PRC_API_PORT` / `PRC_WEB_PORT`, so a substitution here is
#: expressed the way the rest of the repository expresses one.
PREFERRED = {"api": 8001, "web": 3000}


#: Where the resolved ports are published for anything else on this machine.
#: Gitignored, and inside the checkout, so two checkouts publish separately.
PORTS_FILE = REPO_ROOT / ".tmp" / "dev-ports.json"

#: The names the rest of the repository already reads a port through:
#: `playwright.config.ts` takes the first pair, `docker-compose.yml` the second.
#: A substitution that did not reach these is a substitution only this process
#: knows about.
PORT_ENV = {
    "api": ("WORKLIST_API_PORT", "PRC_API_PORT"),
    "web": ("WORKLIST_WEB_PORT", "PRC_WEB_PORT"),
}


def resolve(names: list[str]) -> dict[str, Resolution]:
    """Resolve every port up front, and say plainly which ones moved."""
    resolved = {name: resolve_host_port(PREFERRED[name], name=name) for name in names}
    for resolution in resolved.values():
        print(f"  {resolution.describe()}", flush=True)
    return resolved


def publish(resolved: dict[str, Resolution], database: str) -> dict[str, str]:
    """Write the chosen ports where other tooling can find them, and return the
    environment overrides that carry them to anything this process spawns.

    A resolved port that only this process knows is half a fix. The launcher
    wires its own two children to each other correctly — the web tier gets the
    api's real address, the api's CORS allowlist gets the web tier's real origin
    — and then a second terminal, or a parallel test run, still assumes the
    committed defaults and collides with exactly the thing the resolution moved
    out of the way.

    So the ports go two places. Into the child environment under the names the
    rest of the repository already reads, and into a gitignored file a sibling
    process can read without being a child of this one.
    """
    PORTS_FILE.parent.mkdir(exist_ok=True)
    PORTS_FILE.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "database": database,
                **{name: resolution.port for name, resolution in resolved.items()},
                "api_base_url": f"http://127.0.0.1:{resolved['api'].port}",
                "worklist_url": f"http://127.0.0.1:{resolved['web'].port}/worklist",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        var: str(resolution.port) for name, resolution in resolved.items() for var in PORT_ENV[name]
    }


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
    parser.add_argument("--db-port", type=int, default=5434, help="Published Postgres port.")
    args = parser.parse_args()

    print(f"Resolving ports (preferred api {PREFERRED['api']}, web {PREFERRED['web']}):")
    ports = resolve(["api", "web"])
    api_port, web_port = ports["api"].port, ports["web"].port
    api_base = f"http://127.0.0.1:{api_port}"
    database_url = f"{DB_BASE}:{args.db_port}/{args.database}"

    scratch = str(REPO_ROOT / ".tmp").replace("\\", "/")
    Path(scratch).mkdir(exist_ok=True)
    # project-instructions.md § Temporary Files: the checkout's own gitignored
    # scratch directory, forward-slashed.
    shared = {
        **os.environ,
        "PYTHONUTF8": "1",
        "UV_NATIVE_TLS": "1",
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
        **publish(ports, args.database),
    }

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        procs.append(
            (
                "api",
                subprocess.Popen(
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
                        # Both origins, because the browser sends one of them and
                        # a blocked re-query reads as an interface bug.
                        "WORKLIST_ALLOWED_ORIGINS": (
                            f"http://127.0.0.1:{web_port},http://localhost:{web_port}"
                        ),
                    },
                ),
            )
        )
        procs.append(
            (
                "web",
                subprocess.Popen(
                    ["npm", "run", "dev", "--", "--port", str(web_port)],
                    cwd=REPO_ROOT / "src" / "web",
                    env={
                        **shared,
                        # Both spellings are required and not redundant: the page
                        # is a server component whose first fetch runs in Node,
                        # while every adjustment fetches from the browser and Next
                        # inlines only NEXT_PUBLIC_-prefixed values.
                        "WORKLIST_API_BASE_URL": api_base,
                        "NEXT_PUBLIC_WORKLIST_API_BASE_URL": api_base,
                    },
                    shell=os.name == "nt",
                ),
            )
        )

        print(f"\n  database   {database_url}")
        print(f"  api        {api_base}/api/v1/worklist")
        print(f"  worklist   http://127.0.0.1:{web_port}/worklist")
        print(f"  published  {PORTS_FILE.relative_to(REPO_ROOT).as_posix()}")
        print("\nCtrl-C to stop both.\n", flush=True)

        while all(proc.poll() is None for _, proc in procs):
            time.sleep(0.5)
        for name, proc in procs:
            if proc.poll() is not None:
                print(f"\n{name} exited with {proc.returncode}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
        return 0
    finally:
        _terminate(procs)
        # Removed on the way out: a stale file advertising ports nothing is
        # listening on sends the next reader somewhere empty, which is worse than
        # sending it nowhere.
        PORTS_FILE.unlink(missing_ok=True)


def _terminate(procs: list[tuple[str, subprocess.Popen]]) -> None:
    """Stop both servers, and do not return while one is still running.

    `npm run dev` spawns the server it names, so signalling the wrapper is not
    enough — the `next` process survives it and keeps the port, which is the
    orphan described at the top of this file. On Windows the wrapper is a shell
    that absorbs signals, so the whole tree goes via `taskkill /T`.
    """
    for name, proc in procs:
        if proc.poll() is not None:
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError as exc:
            print(f"could not stop {name} ({proc.pid}): {exc}", file=sys.stderr)

    for name, proc in procs:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print(
                f"{name} ({proc.pid}) did not exit and may still hold its port",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
