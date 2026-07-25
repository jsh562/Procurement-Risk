"""Reading the four entries' declared and resolved dependency sets.

Shared by the layout and isolation checks. This logic lives in an importable
helper rather than inline in test functions so it lands in the coverage
denominator — a check whose logic hides inside a test body is measured as
covered the moment the test runs, which says nothing about the logic itself.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

PYTHON_ENTRIES = ("api", "gateway", "model")
ALL_ENTRIES = ("api", "gateway", "model", "web")

# PEP 503 normalization. Distribution names differ from import names and from
# each other only by separator and case; comparing raw strings produces false
# passes on exactly the pairs that matter.
_NORMALIZE = re.compile(r"[-_.]+")


def normalize(name: str) -> str:
    return _NORMALIZE.sub("-", name).lower()


def _requirement_name(spec: str) -> str:
    return normalize(re.split(r"[<>=!~\[;\s]", spec, maxsplit=1)[0].strip())


def manifest(entry: str) -> dict:
    return tomllib.loads((SRC_ROOT / entry / "pyproject.toml").read_text(encoding="utf-8"))


def first_party_sources(entry: str) -> set[str]:
    """Names this entry resolves from a local path rather than an index."""
    sources = manifest(entry).get("tool", {}).get("uv", {}).get("sources", {})
    return {normalize(name) for name in sources}


def declared_third_party(entry: str) -> set[str]:
    """Direct dependencies excluding first-party path dependencies.

    The exclusion is a derivation rule, not a hand-maintained list. Without it
    the gateway — which both boundaries are required to declare — would fall
    inside every set built here, and the isolation assertions would contradict
    the layout they are checking.
    """
    declared = {_requirement_name(s) for s in manifest(entry)["project"].get("dependencies", [])}
    return declared - first_party_sources(entry)


def locked_distributions(entry: str) -> set[str]:
    """Every distribution in this entry's resolved set, from its own lockfile."""
    lock = tomllib.loads((SRC_ROOT / entry / "uv.lock").read_text(encoding="utf-8"))
    return {normalize(package["name"]) for package in lock["package"]}


def javascript_lockfiles() -> list[Path]:
    names = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb")
    web = SRC_ROOT / "web"
    return sorted(p for name in names for p in web.glob(name))
