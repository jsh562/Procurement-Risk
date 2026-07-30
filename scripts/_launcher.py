"""Shared plumbing for the local launchers.

`dev.py` and `e2e.py` both have to answer three questions before they can start
anything, and both got at least one of them wrong when they answered it alone:

- **Which database is mine?** Four checkouts of this repository sit on this
  machine and each Compose project is named after its directory, so each has its
  own `db` container on its own published port — 5434 here, 5437 for the sibling.
  Both launchers hardcoded 5434, so either one run from a sibling checkout read
  *this* checkout's database, and `seed.py` opens by deleting four tables. That is
  a cross-checkout corruption which reports green about the wrong data rather than
  failing, which is the worse of the two failures.
- **Did my server actually get the port I picked?** Resolution and binding are
  separate steps and nothing holds the port in between.
- **Who else needs to know what I chose?** A substituted port that reaches only
  the process that chose it is half a fix.

Kept here rather than duplicated because the duplicated versions disagreed: one
launcher published its ports and the other did not, and both preferred web 3000
so they contended with each other every single time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO_ROOT / "tests" / "checks"))

from helpers.ports import is_bindable  # noqa: E402


def ports_file(role: str) -> Path:
    """Where `role` publishes what it resolved, for readers that are not its
    children.

    Per role as well as per checkout. One file for both launchers would have the
    suite's record overwrite the dev server's while both run — and then delete it
    outright when the suite finished, leaving a live dev server undiscoverable.
    Inside the checkout and gitignored, so four checkouts keep four sets.
    """
    return REPO_ROOT / ".tmp" / f"{role}-ports.json"


#: How long a child gets to survive before it counts as started. A child that
#: loses a port race dies well inside this; one that is merely slow to build does
#: not exit at all, so the window only has to outlast process spawn.
STARTUP_GRACE_SECONDS = 6.0

#: Attempts, not retries. Two spare attempts is enough for a contended machine
#: and few enough that a genuinely broken command still fails promptly.
MAX_ATTEMPTS = 3


def configure_streams() -> None:
    """Force UTF-8 on this process's own output.

    The resolver's `describe()` carries an em dash and these launchers inherit the
    console encoding rather than the UTF-8 they hand their children — a cp1252
    console prints a replacement character at best and raises at worst.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


class DatabaseNotFound(RuntimeError):
    """This checkout has no reachable database, and guessing one is not safe."""


def resolve_database_port(explicit: int | None = None) -> int:
    """The published port of *this checkout's* database container.

    Order: an explicit flag, then `PRC_DB_PORT`, then Compose itself. Compose
    derives its project name from the directory, so `docker compose port` run here
    answers for this checkout and not for a sibling — measured returning 5434 from
    this checkout and 5437 from the one beside it.

    **There is deliberately no fallback to the committed default.** Falling back is
    exactly how a launcher in a checkout whose database is down ends up reading,
    and then deleting, a sibling checkout's data. A refusal naming the remedy is
    worth more than a default that is right three times out of four.
    """
    if explicit is not None:
        return explicit
    from_env = os.environ.get("PRC_DB_PORT")
    if from_env:
        return int(from_env)

    try:
        published = subprocess.run(
            ["docker", "compose", "port", "db", "5432"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DatabaseNotFound(
            f"could not ask Compose which port the database is on: {exc}"
        ) from exc

    binding = published.stdout.strip()
    if published.returncode != 0 or not binding:
        raise DatabaseNotFound(
            "this checkout has no running database.\n"
            f"  {published.stderr.strip() or published.stdout.strip()}\n"
            "  Start it with:  docker compose up -d db\n"
            "  Refusing to fall back to the committed default: another checkout is "
            "probably on it, and this process would read and overwrite its data."
        )
    # `0.0.0.0:5434`, or an IPv6 form — the port is what follows the last colon.
    return int(binding.rsplit(":", 1)[1])


@dataclass(frozen=True)
class Child:
    """A spawned server and the port it was told to bind."""

    name: str
    port: int
    process: subprocess.Popen


def lost_the_port(child: Child) -> bool:
    """Whether `child` died because something else took its port.

    Decided by asking who holds the port now rather than by parsing the child's
    output. Output would have to be captured to be parsed, and capturing it takes
    the server's own logs away from the terminal the developer is watching.

    The inference is exact in the case that matters. The child is dead, so if the
    port is still held, the holder is somebody else and this was a collision. If
    the port is free, nobody took it and the child failed for its own reasons —
    retrying would just fail again, more slowly.
    """
    return not is_bindable(child.port)


def publish(role: str, record: dict[str, object]) -> None:
    """Write what was finally resolved, for readers that are not our children.

    `dev.py` calls this *after* its servers are up rather than before. An earlier
    version published at resolution time, which is wrong the moment a retry moves a
    port: the file would advertise a port nothing is listening on, and — worse —
    the api's CORS allowlist would name an origin the web tier no longer has,
    blocking every client-side re-query while the first paint still worked. That
    reads as an interface bug and is a launcher bug.
    """
    path = ports_file(role)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), **record}, indent=2) + "\n", "utf-8")


def unpublish(role: str) -> None:
    """Remove the record. A file naming ports nothing is listening on sends the
    next reader somewhere empty, which is worse than sending it nowhere."""
    ports_file(role).unlink(missing_ok=True)


def terminate(children: list[Child]) -> None:
    """Stop every child, and do not return while one is still running.

    `npm run dev` spawns the server it names, so signalling the wrapper leaves the
    `next` process holding its port. On Windows the wrapper is a shell that absorbs
    signals, so the whole tree goes through `taskkill /T`.
    """
    import signal

    for child in children:
        if child.process.poll() is not None:
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(child.process.pid), signal.SIGTERM)
        except OSError as exc:
            print(f"could not stop {child.name} ({child.process.pid}): {exc}", file=sys.stderr)

    for child in children:
        try:
            child.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print(
                f"{child.name} ({child.process.pid}) did not exit and may still hold its port",
                file=sys.stderr,
            )
