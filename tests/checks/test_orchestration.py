"""TR-014 / TR-015 / SC-009 / SC-010: local orchestration, asserted not demonstrated.

Both criteria were confirmed by hand during implementation and by nothing else.
Hand-verification is exactly the evidence this epic exists to replace, so these
bring the two into the same category as every other rule here.

The tests are ordered so the cheap structural assertions run first and the
container-starting ones last; a malformed compose file fails in milliseconds
rather than after a database has been pulled and started.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import warnings
from pathlib import Path

import pytest
import yaml

from tests.checks.helpers.ports import CONVENTIONAL, Resolution, resolve_host_port

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
PERSISTENT = frozenset({"db", "api", "web"})
JOBS = frozenset({"ingest", "fit"})

# Each published port is `${VAR:-default}:container`. The variable is what a
# sibling checkout overrides; the default is what SC-010, TR-015 and IP-004
# describe, so both halves are parsed rather than either being assumed.
PUBLISHED = re.compile(r"^\$\{(?P<var>[A-Z_][A-Z0-9_]*):-(?P<default>\d+)\}:(?P<container>\d+)$")

# Which service publishes which override, so the fixture resolves exactly the
# bindings the file declares instead of a list restated here.
OVERRIDES = {"db": "PRC_DB_PORT", "api": "PRC_API_PORT", "web": "PRC_WEB_PORT"}


def published_binding(definition: dict, service: str) -> re.Match:
    """The parsed host binding for `service`, or a failure naming the offender."""
    ports = definition["services"][service]["ports"]
    assert len(ports) == 1, f"{service!r} publishes {len(ports)} ports, expected 1: {ports}"
    matched = PUBLISHED.match(str(ports[0]))
    assert matched, (
        f"{service!r} publishes {ports[0]!r}, which is not an overridable "
        f"`${{VAR:-default}}:container` binding. A literal cannot be moved by a "
        f"sibling checkout without editing a committed file."
    )
    return matched


def _compose(
    *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


@pytest.fixture(scope="module")
def definition() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# --- structural: no containers required ---------------------------------------


def test_every_job_sits_behind_a_non_default_profile(definition: dict) -> None:
    """TR-014. A job without a profile starts with `up`, which is the failure."""
    for name in JOBS:
        profiles = definition["services"][name].get("profiles")
        assert profiles, f"job {name!r} declares no profile and would start with `up`"
        assert "jobs" in profiles


def test_no_persistent_service_carries_a_profile(definition: dict) -> None:
    """The mirror image: a profiled service would silently not start."""
    for name in PERSISTENT:
        assert not definition["services"][name].get("profiles"), (
            f"{name!r} carries a profile and would be skipped by a plain `up`"
        )


def test_the_committed_database_default_is_not_a_conventional_port(definition: dict) -> None:
    """TR-015 / SC-010, first half.

    The criterion asks for a port that "does not collide with a conventional
    default" — it never named 5434. Asserting the literal was stronger than the
    requirement and is what made parameterising the binding look like a
    violation. What must hold is that the *default a plain `up` uses* is not a
    port a reader would assume belongs to something else.
    """
    default = int(published_binding(definition, "db").group("default"))
    assert default not in CONVENTIONAL, (
        f"the committed db default {default} is a conventional port; a database "
        f"answering there is indistinguishable from an unrelated one"
    )


def test_every_published_port_can_be_moved_without_editing_the_file(definition: dict) -> None:
    """The property that makes several checkouts on one machine workable.

    A literal binding can only be changed by editing committed state, so the
    second checkout to start either loses or dirties its working tree. Each
    published port must therefore carry an override variable.
    """
    for service, expected_var in OVERRIDES.items():
        assert published_binding(definition, service).group("var") == expected_var


def test_the_database_declares_a_healthcheck(definition: dict) -> None:
    assert "healthcheck" in definition["services"]["db"]


# --- behavioural: these start containers --------------------------------------


@pytest.fixture(scope="module")
def resolved_ports(definition: dict) -> dict[str, Resolution]:
    """Pick a usable host port per service before anything starts.

    Resolution happens here rather than inside `up` because Compose reports a
    collision as a message naming a port and nothing else — no holder, no
    remedy. Deciding first turns that into a substitution the run can explain.
    """
    resolutions = {
        service: resolve_host_port(
            int(published_binding(definition, service).group("default")), name=service
        )
        for service in OVERRIDES
    }

    displaced = [r for r in resolutions.values() if r.substituted]
    if displaced:
        # Loud on purpose. The stack under test now differs from the committed
        # file, and a green run that hid that would be claiming evidence for a
        # topology it never exercised.
        warnings.warn(
            "local orchestration bound substitute host ports; the stack under "
            "test differs from docker-compose.yml's committed defaults:\n  "
            + "\n  ".join(r.describe() for r in displaced),
            stacklevel=1,
        )
    return resolutions


@pytest.fixture(scope="module")
def compose_env(resolved_ports: dict[str, Resolution]) -> dict[str, str]:
    """The parent environment plus whichever overrides this run needs."""
    env = dict(os.environ)
    for service, resolution in resolved_ports.items():
        env[OVERRIDES[service]] = str(resolution.port)
    return env


@pytest.fixture(scope="module")
def running_stack(compose_env: dict[str, str]):
    """Bring the persistent services up once, tear them down once."""
    _compose("up", "-d", "--wait", env=compose_env)
    yield compose_env
    _compose("down", "-v", check=False, env=compose_env)


def test_the_effective_database_port_is_not_a_conventional_port(
    resolved_ports: dict[str, Resolution],
) -> None:
    """SC-010, second half — the claim is about the port actually bound.

    Substitution must never reintroduce the ambiguity the criterion forbids, so
    the resolver skipping conventional defaults is asserted against the value
    this run will really use rather than trusted from the resolver's own code.
    """
    effective = resolved_ports["db"].port
    assert effective not in CONVENTIONAL, (
        f"resolved db port {effective} is a conventional default; substitution "
        f"must never land on one"
    )


def test_up_starts_only_persistent_services(running_stack) -> None:
    """SC-009's first half, observed rather than reasoned about."""
    result = _compose("ps", "--format", "json", env=running_stack)
    running = {json.loads(line)["Service"] for line in result.stdout.splitlines() if line.strip()}
    assert running >= PERSISTENT, f"expected {sorted(PERSISTENT)} up, saw {sorted(running)}"
    assert not (running & JOBS), f"job containers started with `up`: {sorted(running & JOBS)}"


def test_the_vector_extension_is_available(running_stack) -> None:
    """SC-010. The healthcheck is `pg_isready`, which proves the server accepts
    connections and says nothing about pgvector. E003's schema depends on the
    extension existing, so its absence must fail here rather than in a
    migration."""
    result = _compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "procurement",
        "-d",
        "procurement",
        "-tAc",
        "CREATE EXTENSION IF NOT EXISTS vector;"
        " SELECT extname FROM pg_extension WHERE extname='vector';",
        env=running_stack,
    )
    assert "vector" in result.stdout, f"pgvector unavailable:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("job", sorted(JOBS))
def test_each_job_runs_to_completion_and_leaves_nothing_behind(running_stack, job: str) -> None:
    """SC-009's second half. `--rm` is asserted, not assumed: a job that exits
    but leaves its container is indistinguishable from one that is still
    running, and accumulates across a working day."""
    before = _compose("ps", "-a", "--format", "json", env=running_stack).stdout
    result = _compose("--profile", "jobs", "run", "--rm", job, check=False, env=running_stack)
    assert result.returncode == 0, (
        f"job {job!r} exited {result.returncode}:\n{result.stderr[-400:]}"
    )
    after = _compose("ps", "-a", "--format", "json", env=running_stack).stdout
    leftover = {json.loads(line)["Service"] for line in after.splitlines() if line.strip()} & JOBS
    assert not leftover, f"job container survived the run: {sorted(leftover)}"
    assert len(after) <= len(before) + 200, "container count grew after a --rm run"
