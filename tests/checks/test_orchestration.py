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
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
PERSISTENT = frozenset({"db", "api", "web"})
JOBS = frozenset({"ingest", "fit"})


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
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


def test_the_database_binds_a_non_colliding_host_port(definition: dict) -> None:
    """TR-015. 5432 is occupied on the target machine; a collision must surface
    at `up` rather than as a confusing failure at first query."""
    ports = definition["services"]["db"]["ports"]
    assert any(str(p).startswith("5434:") for p in ports), f"db ports are {ports}"


def test_the_database_declares_a_healthcheck(definition: dict) -> None:
    assert "healthcheck" in definition["services"]["db"]


# --- behavioural: these start containers --------------------------------------


@pytest.fixture(scope="module")
def running_stack():
    """Bring the persistent services up once, tear them down once."""
    _compose("up", "-d", "--wait")
    yield
    _compose("down", "-v", check=False)


def test_up_starts_only_persistent_services(running_stack) -> None:
    """SC-009's first half, observed rather than reasoned about."""
    result = _compose("ps", "--format", "json")
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
    )
    assert "vector" in result.stdout, f"pgvector unavailable:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("job", sorted(JOBS))
def test_each_job_runs_to_completion_and_leaves_nothing_behind(running_stack, job: str) -> None:
    """SC-009's second half. `--rm` is asserted, not assumed: a job that exits
    but leaves its container is indistinguishable from one that is still
    running, and accumulates across a working day."""
    before = _compose("ps", "-a", "--format", "json").stdout
    result = _compose("--profile", "jobs", "run", "--rm", job, check=False)
    assert result.returncode == 0, (
        f"job {job!r} exited {result.returncode}:\n{result.stderr[-400:]}"
    )
    after = _compose("ps", "-a", "--format", "json").stdout
    leftover = {json.loads(line)["Service"] for line in after.splitlines() if line.strip()} & JOBS
    assert not leftover, f"job container survived the run: {sorted(leftover)}"
    assert len(after) <= len(before) + 200, "container count grew after a --rm run"
