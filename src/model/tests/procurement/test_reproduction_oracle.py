"""SC-012 — the digest reproduces under a hostile environment.

Four things that would move a naive digest and must not move this one: a fresh
process, a different absolute checkout path, a different `PYTHONHASHSEED`, and a
different time zone and locale. Each is a real way a "reproducible" dataset stops
reproducing on someone else's machine, and each is silent — the artifact still
looks fine, it just hashes differently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from model.procurement import paths
from model.procurement.serialize import dataset_content_hash, read_payload
from model.procurement.validate import check_reproduction

REPO_ROOT = paths.REPO_ROOT

#: Run the generator and print only its digest, so the parent can compare a
#: string rather than reach into a child's objects.
_PROBE = (
    "import json,sys;"
    "from model.procurement.generate import generate;"
    "from model.procurement.serialize import dataset_content_hash;"
    "import tempfile,pathlib;"
    "d=tempfile.mkdtemp();"
    "print(json.dumps({'digest': dataset_content_hash(generate(root=pathlib.Path(d)))}))"
)


def _child_digest(env_overrides: dict[str, str], cwd: Path | None = None) -> str:
    env = {**os.environ, **env_overrides}
    #  and a module-level literal probe — no caller-supplied
    # command reaches this, which is what S603 exists to catch.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO_ROOT / "src" / "model"),
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"probe failed:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])["digest"]


@pytest.fixture(scope="module")
def committed() -> str:
    return read_payload(paths.hash_path())["dataset_content_hash"]


def test_the_committed_fixture_matches_its_sidecar(committed) -> None:
    assert dataset_content_hash(read_payload(paths.fixture_path())) == committed


def test_regeneration_reproduces_the_committed_digest(committed) -> None:
    """The oracle itself: regenerate, do not re-read."""
    assert check_reproduction() == committed


class TestHostileEnvironments:
    def test_a_fresh_process_reproduces(self, committed) -> None:
        assert _child_digest({}) == committed

    @pytest.mark.parametrize("seed", ["0", "1", "12345"])
    def test_a_changed_hash_seed_reproduces(self, committed, seed: str) -> None:
        """`PYTHONHASHSEED` reorders set and dict-of-str iteration between
        processes. If any reached the write path, this is where it shows."""
        assert _child_digest({"PYTHONHASHSEED": seed}) == committed

    @pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"])
    def test_a_changed_time_zone_reproduces(self, committed, zone: str) -> None:
        """A naive datetime rendered in local time would move the digest — which
        is exactly why `rfc3339_utc` refuses a naive instant rather than
        assuming UTC."""
        assert _child_digest({"TZ": zone}) == committed

    @pytest.mark.parametrize("locale", ["C", "de_DE.UTF-8", "tr_TR.UTF-8"])
    def test_a_changed_locale_reproduces(self, committed, locale: str) -> None:
        """`tr_TR` is the interesting one: Turkish casing maps `I` to a dotless
        `ı`, so any locale-sensitive case operation on a category key would
        silently produce a different string."""
        assert _child_digest({"LC_ALL": locale, "LANG": locale}) == committed

    def test_a_changed_working_directory_reproduces(self, committed, tmp_path) -> None:
        """The absolute checkout path must not reach the payload. `paths.py`
        resolves the repository root from the module's own location, so running
        from elsewhere resolves the same files."""
        assert (
            _child_digest({"PYTHONPATH": str(REPO_ROOT / "src" / "model" / "src")}, cwd=tmp_path)
            == committed
        )


class TestTheDigestCoversContentNotBytes:
    def test_reformatting_the_file_does_not_move_the_digest(self, committed, tmp_path) -> None:
        """The digest is over re-serialized *parsed* content, so indentation and
        key order in the committed file cannot affect it — which is what lets a
        Windows checkout and a Linux runner agree."""
        payload = read_payload(paths.fixture_path())
        reformatted = tmp_path / "reformatted.json"
        reformatted.write_bytes(
            json.dumps(payload, indent=8, sort_keys=False, ensure_ascii=False).encode("utf-8")
        )
        assert dataset_content_hash(read_payload(reformatted)) == committed

    def test_crlf_line_endings_do_not_move_the_digest(self, committed, tmp_path) -> None:
        crlf = tmp_path / "crlf.json"
        crlf.write_bytes(paths.fixture_path().read_bytes().replace(b"\n", b"\r\n"))
        assert dataset_content_hash(read_payload(crlf)) == committed

    def test_a_changed_value_does_move_the_digest(self, committed, tmp_path) -> None:
        """The control: if the digest were insensitive to content it would be
        insensitive to everything above for the wrong reason."""
        payload = read_payload(paths.fixture_path())
        payload["lines"][0]["description"] = "changed"
        assert dataset_content_hash(payload) != committed
