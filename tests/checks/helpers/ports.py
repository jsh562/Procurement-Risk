"""Host-port resolution for local orchestration.

`docker-compose.yml` publishes three host ports, and their defaults were chosen
to avoid the conventional ones. That solves a collision with *other software*
and not the collision that actually occurs: several checkouts of this
repository can sit on one machine, each publishing the same defaults, so
whichever comes up second fails at `up` with a message naming a port and
nothing else.

This module resolves a usable port before the stack starts and reports what
displaced it. Two properties are load-bearing:

- **A substitute is never a conventional default.** SC-010 requires the
  database on a port that does not collide with a conventional default. Auto-
  substitution would otherwise be free to hand back 5432, satisfying "a free
  port" while breaking the criterion it exists to serve.
- **A substitution is announced, never silent.** The stack under test then
  differs from the committed file, and a green run that hides that would be
  claiming evidence for a topology it did not exercise.

Availability is decided by asking **two** sources, because neither is
sufficient alone:

- **Docker's own published-port list.** On Docker Desktop the published port
  lives in the virtual machine's network namespace, not the host's, so a host
  socket bind on an occupied port *succeeds*. Measured on this machine: with a
  sibling checkout publishing 5434, binding both `127.0.0.1:5434` and
  `0.0.0.0:5434` succeeded while `docker compose up` still failed with "port is
  already allocated". A socket probe alone would report the port free and walk
  straight into the collision this module exists to prevent.
- **A real socket bind.** Docker cannot see a port held by an unrelated
  process, which is the case on a Linux runner where containers publish
  straight onto the host.

A port is available only when both agree. Neither `netstat` nor a parsed
`ss` table is used: they describe listening sockets rather than what Docker
will accept, and they race against a sibling starting at the same moment.
"""

from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass

# Ports a substitute must never land on, whatever else is free. These are the
# defaults a reader would assume, which is exactly why SC-010 forbids them: a
# database answering on 5432 is indistinguishable from someone's system
# Postgres, and that ambiguity is the failure the criterion prevents.
CONVENTIONAL = frozenset({80, 443, 3306, 5000, 5432, 5433, 6379, 8000, 8080, 8443, 27017})

# How far to walk before giving up. Far enough to clear a handful of sibling
# checkouts, short enough that an exhausted search is a real answer rather than
# a hang.
SEARCH_SPAN = 64

# The highest number a TCP port can carry. The search is bounded by this rather
# than by the span alone: a preferred port within `SEARCH_SPAN` of the ceiling
# would otherwise generate candidates no socket call accepts, and `bind` answers
# those with `OverflowError` rather than the `OSError` an availability probe is
# written to expect. Stated as a constant because the callers walking upward
# from a resolved port need the same ceiling and must not restate it.
MAX_PORT = 65535


@dataclass(frozen=True)
class Holder:
    """What occupies a port, when that is discoverable."""

    container: str | None = None
    project: str | None = None

    def describe(self) -> str:
        if self.container is None:
            return "an unidentified process (not a Docker container)"
        if self.project:
            return f"container {self.container!r} from Compose project {self.project!r}"
        return f"container {self.container!r}"


@dataclass(frozen=True)
class Resolution:
    """A resolved binding, and whether it is the one the file asks for."""

    name: str
    preferred: int
    port: int
    holder: Holder | None = None

    @property
    def substituted(self) -> bool:
        return self.port != self.preferred

    def describe(self) -> str:
        if not self.substituted:
            return f"{self.name}: {self.port} (the committed default)"
        held_by = self.holder.describe() if self.holder else "an unidentified process"
        return (
            f"{self.name}: {self.port} — substituted because the committed "
            f"default {self.preferred} is held by {held_by}"
        )


def is_bindable(port: int, host: str = "127.0.0.1") -> bool:
    """True when this process can bind `port` right now.

    `SO_REUSEADDR` is deliberately *not* set. It would let the bind succeed over
    a socket in TIME_WAIT and report a port free that Docker will then refuse,
    which is the false negative this whole module exists to avoid.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


PORT_PUBLICATION = re.compile(r":(\d+)->")


def docker_published_ports() -> dict[int, Holder]:
    """Every host port Docker currently publishes, mapped to what publishes it.

    This is the authoritative source for the collision that actually happens
    here, and the one a socket probe cannot see through on Docker Desktop.
    Returns an empty mapping when the daemon is unreachable — the socket probe
    still applies, and a Docker collision is impossible when Docker is not
    running.
    """
    try:
        listing = subprocess.run(
            [
                "docker",
                "ps",
                "--format",
                '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if listing.returncode != 0:
        return {}

    published: dict[int, Holder] = {}
    for line in listing.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        holder = Holder(
            container=fields[0].strip() or None,
            project=fields[2].strip() if len(fields) > 2 and fields[2].strip() else None,
        )
        for match in PORT_PUBLICATION.finditer(fields[1]):
            published.setdefault(int(match.group(1)), holder)
    return published


def resolve_host_port(
    preferred: int, *, name: str, published: dict[int, Holder] | None = None
) -> Resolution:
    """Return `preferred` when it is available, else the next usable port.

    A port is available only when Docker is not already publishing it *and* a
    socket bind succeeds — see the module docstring for why either alone is
    insufficient. The search skips conventional defaults so a substitute can
    never reintroduce the ambiguity SC-010 forbids.

    `published` is injectable so the search can be tested without a daemon.
    """
    if published is None:
        published = docker_published_ports()

    def available(port: int) -> bool:
        return port not in published and is_bindable(port)

    if available(preferred):
        return Resolution(name=name, preferred=preferred, port=preferred)

    holder = published.get(preferred)
    # The ceiling truncates the span rather than each candidate being tested
    # against it inside the loop: a candidate above `MAX_PORT` is not a port
    # that happens to be taken, it is not a port at all, and generating one only
    # to discard it is what let an out-of-range number reach a `bind` call.
    for candidate in range(preferred + 1, min(preferred + 1 + SEARCH_SPAN, MAX_PORT + 1)):
        if candidate in CONVENTIONAL:
            continue
        if available(candidate):
            return Resolution(name=name, preferred=preferred, port=candidate, holder=holder)

    held_by = holder.describe() if holder else "an unidentified process"
    raise RuntimeError(
        f"no free host port for {name!r}: the committed default {preferred} is "
        f"held by {held_by}, and no port in the following {SEARCH_SPAN} is free "
        f"either (conventional defaults {sorted(CONVENTIONAL)} are never used "
        f"as substitutes). Free a port, or set the override explicitly."
    )
