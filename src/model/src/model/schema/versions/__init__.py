"""Marks the migration chain as a package so coverage can *find* these files.

This file declares no symbols and is imported by nothing. It exists for one
measured reason, and removing it reintroduces a silent hole in the coverage
gate (TR-001/TR-003).

**What breaks without it.** coverage.py's discovery of files that were never
executed -- `coverage.files.find_python_files` -- walks each configured
`source` root and, for every directory below the root itself, deletes the
subtree unless it contains an `__init__.py`, on the grounds that a
non-importable directory holds no importable modules. `versions/` was such a
directory. Measured, with this file absent, by running a subset of the suite
that touches no migration:

    coverage run --source=src/model/roster,src/model/schema -m pytest \\
        tests/schema/test_helpers.py -q     # 18 skipped, no chain executed

The report listed `cli.py`, `env.py`, `helpers.py` and `url.py` at 0% and did
not mention a single revision. So the ten revision modules appear in the
denominator only on runs where they *executed*: the moment the chain stops
running -- a broken fixture, a missing `DATABASE_URL`, a test deleted -- they
leave the denominator instead of dropping to 0%, and the aggregate percentage
goes *up*. That is the failure mode a coverage gate exists to prevent, and it
would be invisible.

**Why this rather than `include_namespace_packages`.** coverage's
`[report] include_namespace_packages = true` skips the `__init__.py` check and
fixes the same thing. It was rejected because the option is read from whichever
configuration file the *running* process finds, and two different processes
perform this discovery over this directory: `coverage run ... -m pytest tests`
from `src/model` (reads `src/model/pyproject.toml`) and `coverage run -m pytest
tests/checks` from the repository root (reads the root `pyproject.toml`, whose
`source` list also names this package). The option would have to be set, and
kept in sync, in both, and a third invocation from a third directory would be
wrong again. This file is read from disk and is therefore correct for all of
them.

**Why Alembic does not mind.** `ScriptDirectory` scans the version locations
with `_only_source_rev_file = re.compile(r"(?!\\.\\#|__init__)(.*\\.py)$")` --
the negative lookahead excludes `__init__` by name, so this file is never
considered a revision. The exclusion is Alembic's own, not a coincidence:
packaged version directories are a supported layout. Verified by
`test_migration_chain.py`, which discovers the chain through the same
`ScriptDirectory` the `migrate` entry point uses and asserts a single head and
that exactly the revisions on disk apply to an empty database.

Alembic loads each revision by file path under a module name taken from the
filename (`0001_enable_extensions`), not as an attribute of this package, and
those names begin with a digit, so nothing here can or should import them.
"""
