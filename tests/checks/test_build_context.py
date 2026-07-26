"""TR-011 / SC-015: the serving build context reaches two entries and no more.

This is the file T026 named and never produced. The property held by
construction from `src/.dockerignore`, but nothing would have caught a
regression — an added `!model` line, or a fourth entry arriving under `/src`
and being admitted by default, would have gone unnoticed until an image check
failed for a reason nobody could explain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DOCKERIGNORE = SRC_ROOT / ".dockerignore"
DOCKERFILE = SRC_ROOT / "api" / "Dockerfile"
ADMITTED = frozenset({"api", "gateway"})


def _ignore_rules() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_context_denies_everything_before_admitting_anything() -> None:
    """An allowlist, not a denylist.

    A denylist admits every future entry under `/src` by default, so the
    guarantee would quietly weaken each time the repository grows.
    """
    rules = _ignore_rules()
    assert rules[0] == "*", f"first rule must deny everything, found {rules[0]!r}"


def test_only_the_serving_boundary_and_the_gateway_are_admitted() -> None:
    admitted = {rule.lstrip("!") for rule in _ignore_rules() if rule.startswith("!")}
    assert admitted == ADMITTED, f"context admits {sorted(admitted)}, expected {sorted(ADMITTED)}"


def test_every_entry_under_src_is_either_admitted_or_denied_deliberately() -> None:
    """Catches a new entry arriving and nobody revisiting the context."""
    entries = {p.name for p in SRC_ROOT.iterdir() if p.is_dir()}
    unconsidered = entries - ADMITTED - {"web", "model"}
    assert not unconsidered, (
        "entries exist that the build context has never been reasoned about: "
        f"{sorted(unconsidered)}"
    )


def test_the_dockerfile_copies_only_admitted_paths() -> None:
    copied = {
        line.split()[1].strip("/")
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("COPY ") and "--from=" not in line
    }
    assert copied <= ADMITTED, f"Dockerfile copies unadmitted paths: {sorted(copied - ADMITTED)}"


@pytest.mark.parametrize("excluded", ["model", "web"])
def test_excluded_entries_are_unreachable_from_the_build(excluded: str) -> None:
    """The claim is local-source unreachability, and nothing stronger.

    A scoped context prevents reaching the modeling boundary's source. It does
    not prevent installing the same distributions from a package index — which
    is why the in-image checks exist and why SC-015 states its boundary.
    """
    assert (SRC_ROOT / excluded).is_dir(), f"{excluded} entry missing; test is stale"
    assert f"!{excluded}" not in _ignore_rules(), f"{excluded} is admitted by the context"
    # Structure, not prose: the Dockerfile's header comment legitimately names
    # the modeling boundary while explaining why it is unreachable.
    copied = {
        line.split()[1].strip("/")
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("COPY ") and "--from=" not in line
    }
    assert excluded not in copied, f"Dockerfile copies the excluded entry {excluded!r}"


def test_docker_reports_the_same_context_we_assert() -> None:
    """Ask Docker, not just the file — a rule can be syntactically present and
    semantically inert."""
    result = subprocess.run(
        [
            "docker",
            "build",
            "--no-cache=false",
            "-f",
            str(DOCKERFILE),
            "-t",
            "procurement-api:contextcheck",
            "--target",
            "builder",
            str(SRC_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"build failed, context assertion inconclusive:\n{result.stderr[-500:]}"
    )
    listing = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "procurement-api:contextcheck",
            "-c",
            "ls /build",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    present = set(listing.stdout.split())
    assert present == ADMITTED, f"build stage holds {sorted(present)}, expected {sorted(ADMITTED)}"
