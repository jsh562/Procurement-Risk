"""Alembic migration environment for the modeling entry.

TR-001: the chain applies cleanly to an empty database with no manual step, so
everything this module needs comes from configuration or the environment.
TR-003: re-application is a no-op, which Alembic's `alembic_version` table gives
us for free -- no migration body may add its own "have I run yet?" guard.

The connection target is read from `DATABASE_URL` and from nowhere else, by
`model.schema.url.get_database_url`. That lookup lives in its own module because
the schema integration-test fixtures need the identical answer and cannot import
this one: Alembic executes this file for its side effect of running the chain.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from model.schema.url import (
    DATABASE_URL_ENV_VAR,
    DatabaseUrlNotConfiguredError,
    get_database_url,
)

# Re-exported: `DATABASE_URL_ENV_VAR` and `DatabaseUrlNotConfiguredError` were
# introduced here and are referenced as part of this module's surface. They are
# named in `__all__` so the imports are not read as unused.
__all__ = [
    "DATABASE_URL_ENV_VAR",
    "DatabaseUrlNotConfiguredError",
    "get_database_url",
    "run_migrations_offline",
    "run_migrations_online",
]

config = context.config

# `config_file_name` is None when Alembic is driven programmatically without an
# ini (pytest-alembic does this). Logging is then the caller's business.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No declarative metadata: this schema is authored as explicit DDL in the
# migration bodies, not reflected from SQLAlchemy models. The invariants that
# matter here -- composite foreign keys, named CHECK constraints, generated
# columns, partial unique indexes -- have no faithful representation to
# autogenerate from, so a metadata object would be a second, weaker definition
# of the same schema. Autogenerate is consequently not used to write migrations.
target_metadata = None

# Comparison options, set deliberately rather than left at their defaults
# because `alembic check` is a build gate (AD-002) and a gate that cries wolf
# gets ignored.
#
# compare_type=True           -- a column whose type drifts from the migrated
#                                DDL is a real defect worth failing on.
# compare_server_default=False -- PostgreSQL hands defaults back normalized and
#                                cast (`'0'::numeric`, `now()`), which almost
#                                never matches the literal text a migration
#                                wrote. Left on, every run reports differences
#                                that are not differences.
COMPARISON_OPTIONS = {
    "compare_type": True,
    "compare_server_default": False,
}


def run_migrations_offline() -> None:
    """Emit the migration SQL to stdout without connecting to a database.

    Used to produce a reviewable script for an environment where the migration
    role is not ours to drive. Applies the same chain as the online path.
    """
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMPARISON_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply the migration chain over a live connection.

    Reuses a connection supplied by the caller when there is one -- pytest-alembic
    and the test fixtures pass theirs through `config.attributes` so migrations
    run inside the test's own transaction. Otherwise a short-lived engine is
    built from `DATABASE_URL` and disposed before returning; `NullPool` because
    a migration run is one connection used once, and a pool would only keep a
    socket open past the end of the process's useful life.
    """
    existing_connection = config.attributes.get("connection")
    if isinstance(existing_connection, Connection):
        _run_migrations_on_connection(existing_connection)
        return

    connectable = create_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _run_migrations_on_connection(connection)
    finally:
        connectable.dispose()


def _run_migrations_on_connection(connection: Connection) -> None:
    """Configure the migration context against `connection` and run the chain."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **COMPARISON_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
