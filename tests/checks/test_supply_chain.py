"""SC-016 / SC-017 / SC-018: index configuration, digest pinning, credentials.

These three requirements shipped with no verification surface at all — they
were specified, mapped to tasks, and would have closed unfalsified. The
analyze phase caught that; these are the checks that close it.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "procurement-api:e001"
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


def test_the_web_manifest_configures_no_alternate_registry() -> None:
    """SC-016 names package.json among the inspected artifacts.

    `.npmrc` absence counts as compliance, so a check that reads only `.npmrc`
    returns early and inspects nothing — the criterion passes while the file
    that can actually redirect resolution is never opened.
    """
    manifest = json.loads((SRC_ROOT / "web" / "package.json").read_text(encoding="utf-8"))

    publish_registry = manifest.get("publishConfig", {}).get("registry")
    assert not publish_registry, f"web manifest sets publishConfig.registry: {publish_registry}"

    # A dependency pinned to a URL or a git ref bypasses the registry entirely,
    # which is the same escape the .npmrc check exists to close.
    offenders = {
        name: spec
        for section in ("dependencies", "devDependencies", "overrides", "resolutions")
        for name, spec in (manifest.get(section) or {}).items()
        if isinstance(spec, str)
        and any(spec.startswith(p) for p in ("http:", "https:", "git+", "git:", "file:"))
    }
    assert not offenders, f"dependencies resolve outside the public registry: {offenders}"


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


# --- SC-018: no credential material in the committed tree or the image layers -

CREDENTIAL_MARKERS = re.compile(
    r"(ANTHROPIC_API_KEY\s*[=:]\s*\S|sk-ant-[A-Za-z0-9_-]{8,}|AWS_SECRET_ACCESS_KEY\s*[=:]\s*\S)"
)

# Scanning only the serving build context left the modelling entry and the
# corpus tree outside the check: a retrieval or generation script under
# src/model, or a manifest under data/, could carry a credential the scan
# would never open. Both are committed, so both are in scope.
CREDENTIAL_SCAN_ROOTS = (*(SRC_ROOT / entry for entry in PYTHON_ENTRIES), REPO_ROOT / "data")


def _credential_offenders(roots: tuple[Path, ...]) -> list[Path]:
    """Walk `roots` for credential markers, tolerating anything unreadable as text.

    data/ holds committed PDFs, so a binary file is expected rather than
    exceptional; it decodes to a UnicodeDecodeError and is skipped, exactly as
    an unreadable file always was.
    """
    offenders = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                if CREDENTIAL_MARKERS.search(path.read_text(encoding="utf-8")):
                    offenders.append(path)
            except (UnicodeDecodeError, OSError):
                continue
    return offenders


def test_no_credential_material_in_the_committed_source_and_data_trees() -> None:
    """Passes vacuously this epic — neither E001 nor E002 supplies a provider
    credential, and FR-002a forbids allow-listing a source that needs one. It
    exists to fail the moment one is introduced, which is the only time the
    assertion could ever be worth anything."""
    # A scan root that stopped existing would scan nothing and pass silently,
    # which is the failure mode this whole check is built against.
    missing = [r.relative_to(REPO_ROOT).as_posix() for r in CREDENTIAL_SCAN_ROOTS if not r.is_dir()]
    assert not missing, f"credential scan roots are missing: {missing}"

    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _credential_offenders(CREDENTIAL_SCAN_ROOTS)
    ]
    assert not offenders, f"credential material in the committed tree: {offenders}"


def test_the_credential_check_would_notice_a_planted_secret(tmp_path: Path) -> None:
    """Positive control, run through the same walk the real scan uses so that
    widening the population cannot leave this asserting on the regex alone."""
    planted = tmp_path / "config.py"
    planted.write_text('ANTHROPIC_API_KEY = "sk-ant-planted12345"\n', encoding="utf-8")
    # A committed PDF next to it: the walk must reach the secret regardless.
    (tmp_path / "document.pdf").write_bytes(b"%PDF-1.7\n\x00\x80\xff\xfe binary\n")

    assert _credential_offenders((tmp_path,)) == [planted]


def test_no_credential_material_in_the_built_image() -> None:
    """SC-018's second half: the built image and its layers, not just source.

    A credential can enter through a build argument, an ENV line, or a file
    deleted in a later layer but still present in an earlier one. None of that
    is visible to a scan over committed source, which is all the previous check
    performed.
    """
    history = subprocess.run(
        ["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", IMAGE_TAG],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert not CREDENTIAL_MARKERS.search(history), "credential material in an image layer command"

    env = subprocess.run(
        ["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", IMAGE_TAG],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert not CREDENTIAL_MARKERS.search(env), f"credential material in image environment: {env}"

    # The filesystem too — an ENV-free image can still carry a copied secret.
    found = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "grep -rIl -E 'sk-ant-[A-Za-z0-9_-]{8,}' /app 2>/dev/null || true",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not found, f"credential material inside the image filesystem: {found}"


def test_the_image_credential_scan_would_notice_a_planted_secret() -> None:
    """Positive control: a scan whose passing state is "found nothing" proves
    nothing unless it is shown finding something."""
    planted = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            IMAGE_TAG,
            "-c",
            "printf 'ANTHROPIC_API_KEY=sk-ant-planted12345' > /tmp/leak && "
            "grep -rIl -E 'sk-ant-[A-Za-z0-9_-]{8,}' /tmp 2>/dev/null || true",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert "/tmp/leak" in planted, "the image-side scan cannot detect a planted secret"
