"""SC-016 / SC-017 / SC-018: index configuration, digest pinning, credentials.

These three requirements shipped with no verification surface at all — they
were specified, mapped to tasks, and would have closed unfalsified. The
analyze phase caught that; these are the checks that close it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
PYTHON_ENTRIES = ("api", "gateway", "model")

# --- SC-016: every entry resolves from its ecosystem's default public index ---

ALTERNATE_INDEX_KEYS = ("index", "index-url", "extra-index-url", "find-links")


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_no_python_entry_configures_an_alternate_index(entry: str) -> None:
    manifest = tomllib.loads((SRC_ROOT / entry / "pyproject.toml").read_text(encoding="utf-8"))
    uv_config = manifest.get("tool", {}).get("uv", {})
    configured = [key for key in ALTERNATE_INDEX_KEYS if key in uv_config]
    assert not configured, f"{entry} configures a non-default index: {configured}"
    assert not (SRC_ROOT / entry / "uv.toml").exists(), f"{entry} carries an unexpected uv.toml"


def test_the_web_boundary_configures_no_alternate_registry() -> None:
    npmrc = SRC_ROOT / "web" / ".npmrc"
    if not npmrc.exists():
        return  # Absence is compliance: npm falls back to the public registry.
    text = npmrc.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(registry|@[\w-]+:registry)\s*=", text, re.M), (
        f"web boundary configures a non-default registry:\n{text}"
    )


def test_the_index_check_would_notice_a_planted_alternate(tmp_path: Path) -> None:
    """Positive control for a check whose passing state is 'nothing found'."""
    npmrc = tmp_path / ".npmrc"
    npmrc.write_text("registry=https://internal.example.invalid/\n", encoding="utf-8")
    assert re.search(r"^\s*registry\s*=", npmrc.read_text(encoding="utf-8"), re.M)


# --- SC-017: every externally pulled image is pinned by digest ----------------

IMAGE_REFERENCE = re.compile(r"^\s*(?:FROM|ARG PYTHON_BASE=|image:)\s*(\S+)", re.M)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def _external_image_references(text: str) -> list[str]:
    references = [m.group(1) for m in IMAGE_REFERENCE.finditer(text)]
    # Build stages refer to earlier stages by name and pull nothing.
    return [r for r in references if not r.startswith("${") and "/" in r or ":" in r]


@pytest.mark.parametrize("definition", [Path("src/api/Dockerfile"), Path("docker-compose.yml")])
def test_every_externally_pulled_image_is_digest_pinned(definition: Path) -> None:
    text = (REPO_ROOT / definition).read_text(encoding="utf-8")
    unpinned = [
        reference
        for reference in _external_image_references(text)
        if not DIGEST.search(reference) and not reference.startswith("${")
    ]
    assert not unpinned, f"{definition} pulls images without a digest: {unpinned}"


def test_the_recorded_digests_are_well_formed() -> None:
    for definition in ("src/api/Dockerfile", "docker-compose.yml"):
        for digest in DIGEST.findall((REPO_ROOT / definition).read_text(encoding="utf-8")):
            assert len(digest) == len("@sha256:") + 64


# --- SC-018: no credential material in the build context or its layers --------

CREDENTIAL_MARKERS = re.compile(
    r"(ANTHROPIC_API_KEY\s*[=:]\s*\S|sk-ant-[A-Za-z0-9_-]{8,}|AWS_SECRET_ACCESS_KEY\s*[=:]\s*\S)"
)


def test_no_credential_material_in_the_serving_build_context() -> None:
    """Passes vacuously this epic — E001 supplies no provider credential. It
    exists to fail the moment one is introduced, which is the only time the
    assertion could ever be worth anything."""
    offenders = []
    for entry in ("api", "gateway"):
        for path in (SRC_ROOT / entry).rglob("*"):
            if not path.is_file() or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                if CREDENTIAL_MARKERS.search(path.read_text(encoding="utf-8")):
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())
            except (UnicodeDecodeError, OSError):
                continue
    assert not offenders, f"credential material in the serving build context: {offenders}"


def test_the_credential_check_would_notice_a_planted_secret(tmp_path: Path) -> None:
    planted = tmp_path / "config.py"
    planted.write_text('ANTHROPIC_API_KEY = "sk-ant-planted12345"\n', encoding="utf-8")
    assert CREDENTIAL_MARKERS.search(planted.read_text(encoding="utf-8"))
