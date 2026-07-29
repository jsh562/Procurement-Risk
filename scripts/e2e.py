#!/usr/bin/env python
"""Run the Playwright suite on ports that are actually free.

`playwright.config.ts` parameterises both of its servers through
`WORKLIST_API_PORT` and `WORKLIST_WEB_PORT` and falls back to 8000 and 3000. Those
defaults collide with whatever else is on the machine — a dev server, a sibling
checkout, an unrelated process — and the config no longer reuses a foreign server
to paper over it, because adopting one meant running the whole suite against
whichever database that server happened to be pointed at.

So the ports are resolved here, before Playwright starts, through the same
`resolve_host_port` the orchestration checks and `scripts/dev.py` use. Any
argument after the script is passed through:

    uv run python scripts/e2e.py
    uv run python scripts/e2e.py -g "the presentation contract"

The frozen fixture is seeded first. It writes only to `procurement_e2e`, which it
derives from the shared URL and owns outright — the shared development database is
never touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The resolver's `describe()` carries an em dash, and this process inherits the
# console's encoding rather than the UTF-8 it hands its children. On a cp1252
# console that prints as a replacement character at best and raises at worst, so
# the stream is reconfigured before anything is written to it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(REPO_ROOT / "tests" / "checks"))

from helpers.ports import resolve_host_port  # noqa: E402

#: The config's own fallbacks, so a run that collides with nothing looks exactly
#: like a run that did not need this script.
PREFERRED = {"api": 8000, "web": 3000}

SHARED_DB = "postgresql://procurement:local-development-only@localhost:5434/procurement"


def main() -> int:
    scratch = str(REPO_ROOT / ".tmp").replace("\\", "/")
    Path(scratch).mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "UV_NATIVE_TLS": "1",
        "MSYS_NO_PATHCONV": "1",
        "TMPDIR": scratch,
        "TEMP": scratch,
        "TMP": scratch,
    }

    print("Seeding the frozen fixture (writes only to procurement_e2e):", flush=True)
    seeded = subprocess.run(
        ["uv", "run", "--directory", "src/api", "python", "tests/fixtures/frozen_run/seed.py"],
        cwd=REPO_ROOT,
        env={**env, "DATABASE_URL": SHARED_DB},
        capture_output=True,
        text=True,
    )
    if seeded.returncode != 0:
        sys.stderr.write(seeded.stdout + seeded.stderr)
        return seeded.returncode
    print(f"  {seeded.stdout.strip().splitlines()[0]}", flush=True)

    print("\nResolving ports:", flush=True)
    resolved = {
        name: resolve_host_port(preferred, name=name) for name, preferred in PREFERRED.items()
    }
    for resolution in resolved.values():
        print(f"  {resolution.describe()}", flush=True)
    print(flush=True)

    return subprocess.run(
        ["npx", "playwright", "test", *sys.argv[1:]],
        cwd=REPO_ROOT / "src" / "web",
        env={
            **env,
            "WORKLIST_API_PORT": str(resolved["api"].port),
            "WORKLIST_WEB_PORT": str(resolved["web"].port),
        },
        shell=os.name == "nt",
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
