"""TR-003 / SC-002 / OBJ1 VC1: the gateway is usable with no provider installed.

The criterion is specific: "in an environment with the gateway installed
without its provider extra and **0 provider packages present**, a consumer
imports, type-checks, and tests against the gateway public surface with zero
failures." Every clause of that needs an environment that does not exist
anywhere else in the repository, so this check builds one.

**Why it lives at the repository root.** The spec's placement rule is explicit
about this file: no entry can host it, because each entry resolves the extra it
needs — the gateway's own `.venv` carries the provider SDK precisely so the
provider tests can run. A check asserting the SDK's absence cannot execute in
an environment that has it, so it belongs to no entry, which is the narrow
`/tests` exception it sits under.

**Why an environment and not a lockfile read.** `test_image_contents.py`
already walks the lockfile and would tell you the extra is optional. That is a
claim about resolution. OBJ1 VC1 is a claim about *use* — that a consumer
imports and type-checks — and the failure it guards against is a lazy import
regressing to module scope, which no lockfile can show. ADR-0014 accepted a
runtime failure as the cost of the optional extra; this is what holds that cost
to the one place it was accepted.

Naming the provider distribution is permitted here and nowhere in `/src`:
`test_single_import_site.py` scans the source root, and this file is outside it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY = REPO_ROOT / "src" / "gateway"
PROVIDER_DISTRIBUTION = "anthropic"

#: What a consumer must be able to name. Not the whole of `__all__` — the point
#: is a realistic consumer, and one that mentioned every export would be a
#: transcription of the module rather than a use of it.
CONSUMER = """
from gateway.api import GatewayError, InvocationRequest, InvocationResult, invoke


def ask(prompt: str, trace_id: str | None = None) -> str:
    request = InvocationRequest(prompt=prompt, trace_id=trace_id)
    try:
        result: InvocationResult = invoke(request)
    except GatewayError:
        return ""
    return result.content
"""

#: The same consumer with one added line: an annotation naming a type that only
#: exists inside the provider SDK. In an environment without the SDK this must
#: fail to type-check — that is what makes the passing run above evidence.
CONSUMER_REACHING_FOR_THE_SDK = CONSUMER + """

from anthropic import Anthropic


def client() -> Anthropic:
    raise NotImplementedError
"""


def _interpreter(venv: Path) -> Path:
    """Both layouts, because the checks run on Windows locally and Linux in CI
    and a check that only worked on one would be evidence about one."""
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise AssertionError(f"no interpreter under {venv}")


def _uv(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", *args],  # noqa: S607 - resolved from PATH by design; CI provides it
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )


@pytest.fixture(scope="module")
def no_provider_env(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The gateway installed without its extra, plus a type checker.

    Module-scoped: building it once and asserting five things about it is the
    same evidence as building it five times, and the environment is the
    expensive part.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")

    venv = tmp_path_factory.mktemp("no-provider-env")
    created = _uv("venv", "-q", str(venv))
    assert created.returncode == 0, f"could not create the environment:\n{created.stderr}"

    installed = _uv(
        "pip",
        "install",
        "-q",
        "--python",
        str(_interpreter(venv)),
        str(GATEWAY),
        "mypy",
    )
    assert installed.returncode == 0, (
        "could not install the gateway without its provider extra. If this failed "
        "on certificate verification, the environment needs UV_NATIVE_TLS=1 — "
        "`verify.yml` sets it as job env, so a local shell needs it exported "
        "rather than the check carrying a flag.\n"
        f"{installed.stderr}"
    )
    return venv


def _run(venv: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(_interpreter(venv)), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_environment_holds_zero_provider_packages(no_provider_env: Path) -> None:
    """The precondition SC-002 states as a number. Asserted against the
    installed distributions rather than an import attempt: a package can be
    present and fail to import for unrelated reasons, and "0 present" is the
    claim."""
    listed = _uv(
        "pip", "list", "--format", "json", "--python", str(_interpreter(no_provider_env))
    )
    assert listed.returncode == 0, listed.stderr
    names = {package["name"].lower() for package in json.loads(listed.stdout)}
    assert PROVIDER_DISTRIBUTION not in names, (
        f"the environment carries {PROVIDER_DISTRIBUTION!r}; the gateway's default "
        f"resolution is pulling in its optional extra, so nothing below is evidence "
        f"about an environment without one. Installed: {sorted(names)}"
    )


def test_the_public_surface_imports(no_provider_env: Path) -> None:
    """TR-003's substance. This is the assertion that fails if the lazy import
    ever regresses to module scope — the whole of ADR-0014 rests on it."""
    result = _run(no_provider_env, "-c", "import gateway.api")
    assert result.returncode == 0, (
        "the gateway public surface does not import without the provider extra "
        f"(TR-003):\n{result.stderr}"
    )


def test_every_exported_name_is_reachable(no_provider_env: Path) -> None:
    """Importing the module is weaker than using it: a name whose definition
    touches the SDK would resolve at import and fail on access."""
    probe = (
        "import gateway.api as a;"
        "missing = [n for n in a.__all__ if not hasattr(a, n)];"
        "assert not missing, missing"
    )
    result = _run(no_provider_env, "-c", probe)
    assert result.returncode == 0, (
        f"a public name is unreachable without the extra:\n{result.stderr}"
    )


def test_a_consumer_type_checks(no_provider_env: Path, tmp_path: Path) -> None:
    """OBJ1 VC1's "type-checks" clause, and the reason the repository grew a
    Python type checker at all (`project-instructions.md` v1.2.4)."""
    consumer = tmp_path / "consumer.py"
    consumer.write_text(CONSUMER, encoding="utf-8")
    result = _run(no_provider_env, "-m", "mypy", "--strict", str(consumer))
    assert result.returncode == 0, (
        "a consumer does not type-check against the gateway with no provider "
        f"package present (SC-002):\n{result.stdout}\n{result.stderr}"
    )


def test_the_type_check_fails_when_a_consumer_reaches_for_the_sdk(
    no_provider_env: Path, tmp_path: Path
) -> None:
    """The negative control, and it carries more than the usual weight.

    `mypy` reports a missing import as an error by default, but that default is
    configurable — one `ignore_missing_imports` in a consumer's settings would
    turn the check above into one that passes on any input. Planting the
    failure is what distinguishes "the consumer type-checks" from "the type
    checker ran".
    """
    consumer = tmp_path / "reaching_consumer.py"
    consumer.write_text(CONSUMER_REACHING_FOR_THE_SDK, encoding="utf-8")
    result = _run(no_provider_env, "-m", "mypy", "--strict", str(consumer))
    assert result.returncode != 0, (
        "a consumer naming a provider SDK type type-checked cleanly in an "
        "environment with no provider package — the type check above proves "
        f"nothing:\n{result.stdout}"
    )
    assert PROVIDER_DISTRIBUTION in result.stdout.lower(), (
        f"the type check failed but did not name {PROVIDER_DISTRIBUTION!r}, so it "
        f"may have failed for an unrelated reason:\n{result.stdout}"
    )


def test_reaching_the_provider_raises_the_gateway_owned_error(no_provider_env: Path) -> None:
    """ADR-0014's accepted cost, held to its stated shape.

    The decision accepted that a consumer omitting the extra learns at first
    invocation. It did not accept a `ModuleNotFoundError` surfacing from inside
    the gateway — an SDK-shaped failure crossing the boundary is the coupling
    the boundary exists to prevent, and a caller with `except GatewayError`
    would not catch it.
    """
    probe = (
        "from gateway.errors import ProviderUnavailableError;"
        "from gateway import provider;"
        "\ntry:\n"
        "    provider.load_client_class()\n"
        "except ProviderUnavailableError as exc:\n"
        "    assert exc.__cause__ is None and exc.__context__ is None, 'chained (TR-064)'\n"
        "    assert 'gateway[provider]' in str(exc), f'no install hint: {exc}'\n"
        "else:\n"
        "    raise AssertionError('no error raised in an environment without the extra')\n"
    )
    result = _run(no_provider_env, "-c", probe)
    assert result.returncode == 0, (
        f"the missing extra did not surface as a gateway-owned error:\n{result.stderr}"
    )


def test_the_environment_is_not_this_one() -> None:
    """A guard against the whole file passing by accident.

    If the fixture ever silently fell back to the interpreter running pytest,
    every assertion above would be about an environment nobody constructed. The
    root checks' own environment has no provider extra either, so the fallback
    would not even fail loudly.
    """
    assert (GATEWAY / "pyproject.toml").exists(), f"gateway entry not found at {GATEWAY}"
    assert Path(sys.executable).parent != _interpreter(GATEWAY / ".venv").parent, (
        "the root checks are running from the gateway's own environment; this file "
        "would then be asserting the absence of an extra that environment installs"
    )
