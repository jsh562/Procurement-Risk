"""Comparing a built image's installed distributions against its lockfile.

TR-012 and TR-013. Two checks with opposite shapes live here:

* an **allowlist** — everything installed must be accounted for by the serving
  boundary's lockfile. This is the load-bearing one. A denylist of known-bad
  names is a false-negative machine: it is blind to anything not named in it,
  and distribution names differ from import names, so a modeling package can
  arrive transitively under a name nobody thought to list.
* a **denylist** — specific modeling modules must fail to import. Kept despite
  the above because it observes the runtime property directly rather than
  inferring it from metadata, and it catches vendored source that carries no
  distribution metadata at all.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from packaging.markers import Marker

REPO_ROOT = Path(__file__).resolve().parents[3]
API_LOCK = REPO_ROOT / "src" / "api" / "uv.lock"
MODEL_LOCK = REPO_ROOT / "src" / "model" / "uv.lock"
IMAGE_TAG = "procurement-api:e001"

_NORMALIZE = re.compile(r"[-_.]+")


def normalize(name: str) -> str:
    """PEP 503 name normalization.

    Not cosmetic: `import-linter`, `uv`, and `importlib.metadata` disagree on
    separator and case, so comparing raw strings produces false passes on
    exactly the pairs a reviewer would not notice.
    """
    return _NORMALIZE.sub("-", name).lower()


def _lock_packages(lock_path: Path) -> dict[str, dict]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {normalize(p["name"]): p for p in data["package"]}


# The image's target platform, not the host's. HINT-002: environment markers
# resolve against whoever evaluates them, and this host is Windows while the
# image is Linux — evaluating on the host expects `colorama` (win32-only) to be
# present and the equality assertion below fails on a correct image.
IMAGE_ENVIRONMENT: dict[str, str] = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "os_name": "posix",
    "python_full_version": "3.12.7",
    "python_version": "3.12",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
}


def _dependency_applies(dependency: dict, environment: dict[str, str]) -> bool:
    """Whether a lockfile dependency edge is live for the target platform."""
    marker = dependency.get("marker")
    if not marker:
        return True
    return Marker(marker).evaluate(environment)


def expected_distributions(
    lock_path: Path = API_LOCK,
    root: str = "api",
    environment: dict[str, str] | None = None,
) -> set[str]:
    """The runtime closure of ``root`` for one platform, walked from the lockfile.

    Walked rather than read flat because a lockfile also records the dev group,
    which `uv sync --no-dev` deliberately leaves out of the image. Comparing
    against the flat package list would expect pytest and ruff to be present.

    Marker-aware because a lockfile is a *universal* resolution: it records
    every platform's edges at once. Ignoring markers does not merely add noise —
    it makes the set wrong in a direction that only shows up when the equality
    the requirement actually asks for is asserted.
    """
    environment = environment or IMAGE_ENVIRONMENT
    packages = _lock_packages(lock_path)
    seen: set[str] = set()
    queue = [normalize(root)]
    while queue:
        current = queue.pop()
        if current in seen or current not in packages:
            continue
        seen.add(current)
        for dependency in packages[current].get("dependencies", []):
            if _dependency_applies(dependency, environment):
                queue.append(normalize(dependency["name"]))
    return seen - {normalize(root)}


def modeling_module_names() -> set[str]:
    """Top-level module names the modeling boundary's distributions provide.

    TR-013. Derived on the host from the modeling lockfile, because the serving
    image cannot supply them — TR-011 keeps the modeling boundary out of its
    build context, so the metadata is unreachable from inside the container.
    """
    manifest = tomllib.loads(
        (REPO_ROOT / "src" / "model" / "pyproject.toml").read_text(encoding="utf-8")
    )
    first_party = {normalize(n) for n in manifest.get("tool", {}).get("uv", {}).get("sources", {})}
    declared = {
        normalize(re.split(r"[<>=!~\[;\s]", spec, maxsplit=1)[0])
        for spec in manifest["project"]["dependencies"]
    }
    # The first-party exclusion is a derivation rule, not a hand-maintained
    # list. Without it the gateway — which the modeling boundary is required to
    # declare — would land in the denylist, and the serving image is required
    # to contain the gateway. STF-001 was filed about exactly this.
    third_party = declared - first_party

    # Read the top-level modules each distribution actually provides, from the
    # modeling boundary's synced environment. Substituting `name.replace("-",
    # "_")` for this lookup happens to be right for the four distributions
    # declared today and is wrong in general — distribution names and import
    # names are independent, which is the whole reason TR-013 specifies
    # metadata rather than a naming transformation.
    probe = (
        "import json;from importlib.metadata import packages_distributions;"
        "m={};\n"
        "for mod, dists in packages_distributions().items():\n"
        "    for d in dists: m.setdefault(d.replace('_','-').lower(), []).append(mod)\n"
        "print(json.dumps(m))"
    )
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "src" / "model"),
            "--no-sync",
            "python",
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "UV_NATIVE_TLS": "1"},
    )
    provided = json.loads(completed.stdout)

    modules: set[str] = set()
    for distribution in third_party:
        # Fall back to the naming transformation only when metadata records
        # nothing, so a distribution that ships no top-level record still
        # contributes a name rather than silently shrinking the denylist.
        modules.update(provided.get(distribution) or [distribution.replace("-", "_")])
    return modules


def installed_distributions(image: str = IMAGE_TAG) -> set[str]:
    """Query the built image for what it actually installed."""
    script = (
        "import json;from importlib.metadata import distributions;"
        "print(json.dumps(sorted({d.metadata['Name'] for d in distributions()"
        " if d.metadata['Name']})))"
    )
    completed = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/app/.venv/bin/python", image, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return {normalize(name) for name in json.loads(completed.stdout)}


def import_succeeds(module: str, image: str = IMAGE_TAG) -> bool:
    """Whether ``module`` imports inside the image, under its own interpreter.

    HINT-004: the interpreter is pinned to the copied virtualenv rather than
    left to a bare `python`, which can resolve the base image's system
    interpreter — under which an ImportError says nothing about what this image
    installed. Exit code alone is also insufficient: a container that fails to
    start for any reason exits non-zero and would read as a passing check, so
    the caller pairs every negative with a positive control.
    """
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/app/.venv/bin/python",
            image,
            "-c",
            f"import {module}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def inject_stub_and_probe(distribution: str, image: str = IMAGE_TAG) -> dict:
    """Create a stub distribution inside a live container, then probe it.

    TR-007's negative case for the two image-operating checks. A committed
    source file cannot express "this distribution is installed in the built
    image" — the violation only exists once the image is running, so it is
    injected at runtime into a container started from the real serving image
    rather than from a doctored build.

    The stub carries `.dist-info` metadata, not just an importable directory,
    because the allowlist check reads distribution metadata: a bare module
    would be invisible to it and the negative case would prove nothing.
    """
    version = "9.9.9"
    # Built by substitution rather than as an f-string with escapes: the
    # payload crosses a shell boundary, and an escaped newline that survives
    # one layer but not the next becomes a syntax error inside the container
    # which surfaces only as a non-zero exit.
    template = """
import importlib, importlib.metadata as md, json, pathlib, sys
NAME = "__DIST__"
VERSION = "__VERSION__"
site = pathlib.Path([p for p in sys.path if p.endswith("site-packages")][0])
package = site / NAME
package.mkdir(parents=True, exist_ok=True)
(package / "__init__.py").write_text("__version__ = " + repr(VERSION))
info = site / (NAME + "-" + VERSION + ".dist-info")
info.mkdir(parents=True, exist_ok=True)
(info / "METADATA").write_text(
    chr(10).join(["Metadata-Version: 2.1", "Name: " + NAME, "Version: " + VERSION, ""])
)
(info / "RECORD").write_text("")
importlib.invalidate_caches()
names = sorted({d.metadata["Name"].lower() for d in md.distributions() if d.metadata["Name"]})
try:
    importlib.import_module(NAME)
    imported = True
except ImportError:
    imported = False
print(json.dumps({"installed": names, "imported": imported}))
"""
    payload = template.replace("__DIST__", distribution).replace("__VERSION__", version)
    completed = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/app/.venv/bin/python", image, "-c", payload],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    result["installed"] = {normalize(n) for n in result["installed"]}
    return result
