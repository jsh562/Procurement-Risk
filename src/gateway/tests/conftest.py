"""Gateway test-suite configuration.

Currently one concern: stopping a skipped database tier from reporting as a
pass.

**This is E003's lesson, adopted rather than re-learned.** Its schema tier skips
when `DATABASE_URL` is unset, and it records the measurement that made the
danger concrete: `env -u DATABASE_URL pytest tests -q` reported *81 passed, 344
skipped, exit 0*. Four fifths of a suite vanished and the run still looked
green. `test_migrations.py` has exactly that shape — it skips without a
database, and it is the only place this epic verifies TR-017's apply-from-empty
and TR-050's re-runnable postcondition.

**The same variable, deliberately.** `REQUIRE_DB` is E003's spelling and the
semantics are copied verbatim, including the fallback to `CI`. Two entries
disagreeing about how to demand a database would mean a workflow author has to
remember which is which, and the one they forget is the one that skips. The
fallback is the load-bearing half: a *new* CI step that runs this suite is
strict without anyone having remembered to opt in, which is the failure mode
`REQUIRE_DB` alone would still have.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL_ENV_VAR = "DATABASE_URL"
REQUIRE_DATABASE_ENV_VAR = "REQUIRE_DB"

#: Set by GitHub Actions and by every other CI provider.
CI_ENV_VAR = "CI"

#: Spellings of "no" accepted in either variable. An unset or blank value is
#: *absent* rather than false, and defers to the next channel — an
#: exported-but-empty variable is far more often a broken shell than a
#: considered instruction.
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def database_is_required() -> bool:
    """Whether an unset `DATABASE_URL` should abort the run rather than skip it.

    `REQUIRE_DB` wins when it says anything at all, including when it says no.
    Otherwise `CI` decides.
    """
    explicit = os.environ.get(REQUIRE_DATABASE_ENV_VAR, "").strip().lower()
    if explicit:
        return explicit not in FALSE_VALUES
    return os.environ.get(CI_ENV_VAR, "").strip().lower() not in ({""} | FALSE_VALUES)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to start when a database is required and none is configured.

    One message rather than one per test: failing inside the fixture instead
    reports the same problem thirteen times and buries the sentence saying how
    to fix it. Aborting before any test runs also keeps it from being mistaken
    for a test failure, or attributed to whichever test happened to be collected
    first.

    `UsageError` is the accurate category — nothing is wrong with the code or
    the schema; the environment was asked for a database and did not name one.
    """
    del config  # required by the hook signature; nothing here is configurable
    if not database_is_required():
        return
    if os.environ.get(DATABASE_URL_ENV_VAR, "").strip():
        return
    raise pytest.UsageError(
        f"{DATABASE_URL_ENV_VAR} is unset but {REQUIRE_DATABASE_ENV_VAR} or "
        f"{CI_ENV_VAR} says a database is required, so this epic's migration "
        f"verification would have skipped and the run would have reported "
        f"success having asserted nothing about TR-017's apply-from-empty or "
        f"TR-050's re-runnable postcondition. Refusing to start. Export "
        f"{DATABASE_URL_ENV_VAR}, or set {REQUIRE_DATABASE_ENV_VAR}=0 to allow "
        f"the skip deliberately."
    )
