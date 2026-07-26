"""Resolution of the database connection target for the modeling entry.

This lives beside `env.py` rather than inside it because two callers need the
same answer and must not disagree about it: the Alembic migration environment,
and the schema integration-test fixtures (`tests/schema/conftest.py`). `env.py`
cannot be imported to obtain it -- Alembic executes that module for its side
effect of running the chain, so importing it would run migrations. Duplicating
the lookup instead would give the tests their own idea of which driver and which
host to use, which is exactly the drift this module exists to prevent.

The connection target is read from `DATABASE_URL` and from nowhere else. It is
neither hardcoded here nor set as `sqlalchemy.url` in `alembic.ini`, for two
reasons:

1. The URL carries the database password. A committed one is a leaked
   credential (Ruff S105/S106) whether or not the value is a development
   placeholder.
2. Host and port genuinely differ per environment -- the local Compose service
   publishes on one host port, the CI service container on another, and a
   deployed database on neither. Any literal written here would be correct in
   exactly one of them and would silently win wherever the variable was
   forgotten. Failing loudly on an unset variable is the safer default, so this
   module refuses to guess rather than falling back.
"""

from __future__ import annotations

import os

from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError

#: The sole configuration channel for the connection target. Named as a constant
#: so the string is not repeated between the lookup and the error message.
DATABASE_URL_ENV_VAR = "DATABASE_URL"

#: The SQLAlchemy dialect+driver for psycopg 3, which is the client this entry
#: declares (`psycopg[binary]` in pyproject.toml).
#:
#: A bare `postgresql://` URL resolves to psycopg *2*, which is not installed and
#: never will be. E001 froze the shape of DATABASE_URL and TR-037 forbids changing
#: it, so the driver cannot be pinned in the variable; choosing the DBAPI belongs
#: to the entry that declares the dependency, which is this one.
PSYCOPG3_DRIVERNAME = "postgresql+psycopg"


class DatabaseUrlNotConfiguredError(RuntimeError):
    """Raised when no database URL is available.

    A named type rather than a bare `RuntimeError` so a caller -- the `migrate`
    console entry point (TR-007), or a test fixture deciding between "skip, this
    machine has no database configured" and "fail, the database is broken" --
    can distinguish "you have not configured me" from "the operation failed".
    """


def get_database_url() -> URL:
    """Return the connection URL from the environment, resolved to psycopg 3.

    Raises:
        DatabaseUrlNotConfiguredError: if `DATABASE_URL` is unset, blank, or not
            parseable as a URL. Whitespace counts as unset -- an
            exported-but-empty variable is a broken environment, and treating it
            as a URL only moves the failure somewhere more confusing.

    Neither the value nor any part of it is logged or placed in an error
    message: it carries the password, and these exceptions exist to be printed.
    A `URL` is returned rather than a string partly for this reason -- its
    `__str__` masks the password, so an incidental log line cannot leak it.
    """
    raw_url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not raw_url:
        raise DatabaseUrlNotConfiguredError(
            f"{DATABASE_URL_ENV_VAR} is not set. The connection target is read from "
            f"that variable only -- there is no default and no value in alembic.ini, "
            f"because host, port, and credentials differ between the local Compose "
            f"service, CI, and a deployed database. Export {DATABASE_URL_ENV_VAR} as "
            f"postgresql://<user>:<password>@<host>:<port>/<database> "
            f"(see docker-compose.yml for the local service's published port) and retry."
        )

    try:
        url = make_url(raw_url)
    except ArgumentError as exc:
        raise DatabaseUrlNotConfiguredError(
            f"{DATABASE_URL_ENV_VAR} is set but could not be parsed as a database URL. "
            f"Expected postgresql://<user>:<password>@<host>:<port>/<database>. The "
            f"value is not repeated here because it carries a password."
        ) from exc

    # Only the bare scheme is redirected. An explicitly named driver is the
    # caller stating an intent this function has no business overruling.
    if url.drivername == "postgresql":
        url = url.set(drivername=PSYCOPG3_DRIVERNAME)
    return url
