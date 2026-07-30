"""Checks over the local launchers' shared plumbing.

`scripts/dev.py` and `scripts/e2e.py` decide two things that can go wrong
silently: which database this checkout talks to, and which ports its servers
take. Both were wrong at some point, and neither failure announced itself —

- the launchers hardcoded the database port, so one run from a sibling checkout
  read, and through `seed.py` deleted, another checkout's data;
- both preferred web port 3000, so a suite run and a dev server in one checkout
  contended every time and only worked because the resolver walked around it;
- both published their resolved ports to one file, so the suite's record
  overwrote the dev server's and then deleted it on the way out.

Every one of those was found by hand. These cover them, because a launcher only
misbehaves on somebody else's machine or somebody else's branch, which is exactly
where nobody is watching.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import _launcher  # noqa: E402
from scripts import dev as dev_launcher  # noqa: E402
from scripts import e2e as e2e_launcher  # noqa: E402


def _compose_reply(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Stand in for `docker compose port db 5432` without a daemon."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args or ["docker"], returncode, stdout, stderr)

    return fake_run


class TestDatabaseResolution:
    """Which database this checkout talks to — the one that corrupted data."""

    def test_an_explicit_port_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRC_DB_PORT", "5999")
        assert _launcher.resolve_database_port(5555) == 5555

    def test_the_environment_wins_over_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRC_DB_PORT", "5999")
        monkeypatch.setattr(subprocess, "run", _compose_reply("0.0.0.0:5434\n"))
        assert _launcher.resolve_database_port() == 5999

    def test_discovery_reads_the_published_binding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRC_DB_PORT", raising=False)
        monkeypatch.setattr(_launcher.subprocess, "run", _compose_reply("0.0.0.0:5437\n"))
        assert _launcher.resolve_database_port() == 5437

    def test_an_ipv6_binding_still_yields_the_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Compose prints one line per published binding and the v6 form carries
        # colons of its own, so the port is what follows the *last* one.
        monkeypatch.delenv("PRC_DB_PORT", raising=False)
        monkeypatch.setattr(_launcher.subprocess, "run", _compose_reply("[::]:5438\n"))
        assert _launcher.resolve_database_port() == 5438

    def test_no_database_refuses_rather_than_guessing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point. A fallback here is how a sibling's data gets deleted.

        `docker compose port` exits non-zero with "service db is not running" in a
        checkout that has not started one. Returning the committed default there
        would point this checkout at whichever *other* checkout holds 5434 — and
        `seed.py` opens by deleting four tables.
        """
        monkeypatch.delenv("PRC_DB_PORT", raising=False)
        monkeypatch.setattr(
            _launcher.subprocess,
            "run",
            _compose_reply(stderr='service "db" is not running', returncode=1),
        )
        with pytest.raises(_launcher.DatabaseNotFound) as raised:
            _launcher.resolve_database_port()
        assert "5434" not in str(raised.value), (
            "the refusal must not offer the committed default, even as a suggestion"
        )

    def test_ensure_database_starts_one_when_there_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`docker compose up -d db` fails in a second checkout — 5434 is taken —
        so the launcher starts the database itself rather than advising a command
        that fails exactly when it is followed."""
        monkeypatch.delenv("PRC_DB_PORT", raising=False)
        monkeypatch.setattr(
            _launcher.subprocess,
            "run",
            _compose_reply(stderr='service "db" is not running', returncode=1),
        )
        monkeypatch.setattr(_launcher, "start_database", lambda: 5435)
        assert _launcher.ensure_database() == 5435


class TestPublishedRecord:
    """What a launcher tells the rest of the machine about its ports."""

    def test_each_role_owns_its_own_file(self) -> None:
        # One shared file meant the suite's record replaced the dev server's while
        # both ran, and deleted it when the suite finished.
        assert _launcher.ports_file("dev") != _launcher.ports_file("e2e")

    def test_the_record_round_trips_and_is_removed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_launcher, "REPO_ROOT", tmp_path)
        (tmp_path / ".tmp").mkdir()
        _launcher.publish("dev", {"api": 8002, "web": 3001, "database": "procurement"})

        written = json.loads(_launcher.ports_file("dev").read_text(encoding="utf-8"))
        assert written["api"] == 8002
        assert written["web"] == 3001
        assert written["pid"] > 0, "a record nobody can attribute to a process is not useful"

        _launcher.unpublish("dev")
        assert not _launcher.ports_file("dev").exists(), (
            "a stale record sends the next reader to a port nothing is listening on"
        )


class TestCollisionDiscrimination:
    """Telling 'something took my port' from 'my server crashed'.

    Retrying the first is the fix; retrying the second just fails again, slower.
    """

    @staticmethod
    def _dead_child(port: int) -> _launcher.Child:
        finished = subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])
        finished.wait()
        return _launcher.Child("web", port, finished)

    def test_a_held_port_reads_as_a_collision(self) -> None:
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            holder.bind(("127.0.0.1", 0))
            holder.listen(1)
            port = holder.getsockname()[1]
            assert _launcher.lost_the_port(self._dead_child(port))
        finally:
            holder.close()

    def test_a_free_port_reads_as_the_child_s_own_failure(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        assert not _launcher.lost_the_port(self._dead_child(port))


def test_the_two_launchers_prefer_disjoint_ports() -> None:
    """Neither launcher may prefer a port the other one does.

    Both preferred web 3000, so running the suite while the dev server was up
    contended every single time — survivable only because the resolver walked
    around it, which made a routine arrangement look like a contended machine.
    Two launchers in one checkout should never need the resolver to tell them
    apart.
    """
    overlap = set(dev_launcher.PREFERRED.values()) & set(e2e_launcher.PREFERRED.values())
    assert not overlap, f"dev.py and e2e.py both prefer {sorted(overlap)}"


def test_the_launchers_publish_under_different_roles() -> None:
    assert dev_launcher.ROLE != e2e_launcher.ROLE
