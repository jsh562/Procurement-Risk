"""The `migrate` console entry point: applies the Alembic chain and exits.

TR-007 / ADR-0011: a modeling-owned one-shot job is reached as a console entry
point through this entry's own environment -- `uv run --directory src/model
migrate` -- and never as a container job. That is not a packaging preference.
`src/model/pyproject.toml` declares `gateway = { path = "../gateway" }`, which
sits outside any build context rooted at `src/model`, and widening the context
to `./src` would require admitting `!model` to `src/.dockerignore` and deleting
the two build-context contracts that keep the modeling boundary out of the
serving image. The run's determinism is therefore bound by `uv.lock`, not by an
image digest.

Alembic is driven through `alembic.command`, not through its console script, so
there is no subprocess to spawn and no argument string to quote. The database
URL never passes through this module: `env.py` reads it from `DATABASE_URL` via
`model.schema.url.get_database_url`, so the password is not handled, logged, or
placed on a command line here.

Note for anyone extending this: the target is `command.upgrade`, *not*
`env.run_migrations_online`. That function is the hook Alembic itself calls
during a run and depends on `alembic.context`, a module-level proxy that exists
only while a migration is executing; calling it directly fails outside one.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util import CommandError

from model.schema.url import DatabaseUrlNotConfiguredError

__all__ = [
    "ALEMBIC_INI_PATH",
    "DEFAULT_REVISION",
    "build_config",
    "main",
]

#: Applied when no revision is named. `head` is the only value CI ever passes;
#: the argument exists so a developer can step to an intermediate revision while
#: bisecting a migration failure.
DEFAULT_REVISION = "head"

#: This module sits at `src/model/src/model/schema/cli.py`; `alembic.ini` sits at
#: the entry root, four levels up. Resolved from `__file__` rather than from the
#: process's working directory for the same reason the ini itself uses
#: `%(here)s`: `uv run --directory src/model` leaves the caller's cwd in place,
#: so a relative path would resolve differently depending on where the developer
#: happened to be standing. Alembic then derives `%(here)s` from this absolute
#: path, and `script_location`, `version_locations`, and `prepend_sys_path`
#: follow it.
ALEMBIC_INI_PATH = Path(__file__).resolve().parents[3] / "alembic.ini"

#: Exit codes. Non-zero on every failure, because CI gates on the status alone.
#: 2 is left alone deliberately -- argparse exits 2 on a usage error, and reusing
#: it would make a mistyped argument indistinguishable from a real failure.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 3


def build_config(ini_path: Path = ALEMBIC_INI_PATH) -> Config:
    """Load the entry's Alembic configuration.

    Args:
        ini_path: Absolute path to `alembic.ini`. Defaults to the entry's own.

    Raises:
        FileNotFoundError: if the ini is not there. Checked rather than left to
            Alembic, which treats a missing config file as an empty one and then
            fails further in with "No 'script_location' key found in
            configuration" -- an error that describes a symptom of the real
            problem rather than the problem.
    """
    if not ini_path.is_file():
        raise FileNotFoundError(
            f"Alembic configuration not found at {ini_path}. The migration assets are "
            f"resolved relative to the installed `model` package, so this normally means "
            f"the package was installed from a built wheel rather than from the source "
            f"tree -- alembic.ini sits at the entry root and is not packaged. Run "
            f"`uv sync --directory src/model` and retry."
        )
    return Config(str(ini_path))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="migrate",
        description=(
            "Apply the forward-only migration chain to the database named by DATABASE_URL. "
            "Re-running is a no-op: Alembic's alembic_version table records what has "
            "already been applied (TR-003)."
        ),
    )
    parser.add_argument(
        "revision",
        nargs="?",
        default=DEFAULT_REVISION,
        help=(
            "Alembic revision to upgrade to, such as a four-digit id like 0002. "
            f"Defaults to {DEFAULT_REVISION}. Migrations are forward-only (TR-002), so a "
            "revision behind the current one is refused rather than reversed."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run `alembic upgrade <revision>` against the configured database.

    Args:
        argv: Command-line arguments, `sys.argv[1:]` when None.

    Returns:
        `EXIT_OK` when the database is at the requested revision, whether this
        run moved it there or found it already there.

    Two failures are caught and reported as a message rather than a traceback,
    because both are answerable by the person who ran the command: an unset
    `DATABASE_URL`, and Alembic's own refusal to resolve or apply a revision.
    Anything else -- a refused connection, a failing DDL statement -- propagates
    with its traceback intact, which is what a caller needs in order to see
    which statement broke, and still exits non-zero.
    """
    args = _parse_args(argv)

    try:
        command.upgrade(build_config(), args.revision)
    except DatabaseUrlNotConfiguredError as exc:
        # url.py wrote this message for a human and it carries no credential.
        print(f"migrate: {exc}", file=sys.stderr)
        return EXIT_NOT_CONFIGURED
    except (CommandError, FileNotFoundError) as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return EXIT_FAILED

    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - parity with the console script
    sys.exit(main())
