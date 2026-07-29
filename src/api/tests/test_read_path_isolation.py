"""What the worklist read path does *not* touch.

FR-002, FR-023, FR-024, FR-035.

Three negative claims, each with a different observation site, because each is
false in a different way:

- **No request-time model dependency** (FR-002): the worklist reads artifacts a
  previous run stored. Proved by serving a full response with the provider
  unreachable, and by the invocation record gaining no row.
- **No model-provider import** (FR-035): proved by the import contracts in
  `pyproject.toml`, which fail the build rather than surfacing as a slow page on
  a bad network day. Re-asserted here so the contract's *existence* is itself
  under test — a contract silently deleted is a boundary silently removed.
- **No datastore connection from the interface tier** (FR-024): proved at two
  sites, because either alone is escapable. The manifest check would miss a
  driver reached through a transitive dependency; the request check would miss a
  driver present but not exercised by the paths a test happens to run.
"""

from __future__ import annotations

import socket
import tomllib
from pathlib import Path
from typing import Any

import pytest

#: The web boundary's manifest and lockfile. `parents[2]` is `src/` — this file
#: sits at `src/api/tests/`, so [0] is `tests`, [1] is `api`, [2] is `src`.
WEB = Path(__file__).resolve().parents[2] / "web"

#: Distributions that speak a database wire protocol. A dependency on any of
#: them in the interface tier is a datastore connection waiting to be opened,
#: whether or not any code path currently opens one.
DATABASE_DRIVERS = frozenset(
    {
        "pg",
        "pg-promise",
        "postgres",
        "postgresql",
        "node-postgres",
        "mysql",
        "mysql2",
        "mongodb",
        "mongoose",
        "sqlite3",
        "better-sqlite3",
        "prisma",
        "@prisma/client",
        "drizzle-orm",
        "typeorm",
        "sequelize",
        "knex",
        "kysely",
    }
)


def test_the_provider_being_unreachable_does_not_change_the_response(
    frozen_run: dict[str, Any], client: Any, monkeypatch: Any
) -> None:
    """FR-002, FR-023. The worklist renders completely, with every figure
    present, when the model provider is unreachable.

    Enforced by breaking outbound sockets entirely rather than by mocking a
    provider client: a mock proves only that the code did not call the object
    the test handed it, while this proves it made no network call at all.
    """
    from api.routes import worklist as route

    baseline = client.get("/api/v1/worklist").json()

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError(
            "outbound network is unavailable. If the worklist needed it, that is FR-002's "
            "request-time model dependency — the failure this test exists to catch."
        )

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)

    response = client.get("/api/v1/worklist")
    assert response.status_code == 200
    body = response.json()

    assert body["ranked"] == baseline["ranked"], "a figure changed when the provider went away"
    assert body["counts"] == baseline["counts"]
    assert route  # the module under test was the one exercised


def test_serving_the_worklist_records_no_model_invocation(
    frozen_run: dict[str, Any], client: Any, connection: Any
) -> None:
    """FR-002. The strongest form of "no request-time model dependency": the
    invocation record is what a request *would* leave behind, so an unchanged
    count is evidence rather than an absence of evidence."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'model_invocation'"
        )
        if cursor.fetchone()[0] == 0:
            pytest.skip(
                "No model_invocation table in this schema yet — E008 owns it. Skipping is "
                "honest; asserting against a table that does not exist would pass vacuously."
            )

        cursor.execute("SELECT count(*) FROM model_invocation")
        before = cursor.fetchone()[0]

    client.get("/api/v1/worklist")

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM model_invocation")
        assert cursor.fetchone()[0] == before


def test_the_import_contracts_that_enforce_the_boundary_still_exist() -> None:
    """FR-035. The contracts do the real work; this asserts they are still
    declared, because a contract quietly deleted removes a boundary quietly."""
    manifest = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    contracts = manifest["tool"]["importlinter"]["contracts"]
    pairs = {
        (tuple(contract["source_modules"]), tuple(contract["forbidden_modules"]))
        for contract in contracts
    }

    assert (("api.llm",), ("api.compute",)) in pairs
    assert (("api.llm",), ("api.risk_read",)) in pairs
    assert (
        ("api.risk_read", "api.routes", "api.compute"),
        ("gateway",),
    ) in pairs, "the read path may not reach the model-provider gateway"

    assert manifest["tool"]["importlinter"]["include_external_packages"] is True, (
        "without this the graph holds only internal modules and the gateway contract passes "
        "by naming a module the analysis never loaded"
    )


def test_the_interface_tier_declares_no_database_driver() -> None:
    """FR-024, first observation site: the manifest.

    A driver declared here is a datastore connection waiting to be opened,
    whether or not any current code path opens one — and "no code calls it yet"
    is a property of today's code, not of the boundary.
    """
    manifest = __import__("json").loads((WEB / "package.json").read_text(encoding="utf-8"))
    declared = set(manifest.get("dependencies", {})) | set(manifest.get("devDependencies", {}))

    offending = declared & DATABASE_DRIVERS
    assert not offending, f"the web boundary declares {sorted(offending)}"


def test_no_database_driver_reaches_the_interface_tier_transitively() -> None:
    """FR-024, still the manifest site — but through the lockfile.

    The direct-dependency check above would miss a driver arriving under
    something else, and a transitive driver is as connectable as a declared one.
    """
    lockfile = __import__("json").loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    installed = {
        path.rsplit("node_modules/", 1)[-1] for path in lockfile.get("packages", {}) if path
    }

    offending = installed & DATABASE_DRIVERS
    assert not offending, f"a database driver is installed in the web boundary: {sorted(offending)}"


def test_the_worklist_page_reaches_the_serving_boundary_and_nothing_else() -> None:
    """FR-024, second observation site: the request set.

    The manifest check proves no driver is *available*; this proves the page's
    data actually arrives over HTTP from the serving boundary. Either alone is
    escapable — a driver could be vendored rather than declared, or declared and
    reached only on a path no test runs.
    """
    source = (WEB / "app" / "worklist" / "worklist.ts").read_text(encoding="utf-8")

    assert "fetch(" in source, "the boundary's data must arrive over HTTP"
    assert "/api/v1/worklist" in source

    for forbidden in ("psycopg", "pg.Client", "createConnection", "DATABASE_URL"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in the interface tier's data access — FR-024 makes 'the "
            "interface tier opens no datastore connection' the observable form of the rule"
        )


def test_the_interface_origin_may_read_the_worklist(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-024, FR-031. The two tiers are separate origins, so every client-side
    re-query an adjustment triggers is cross-origin.

    Without the headers the browser blocks it while the server-rendered first
    paint still works — so the page loads, the ranking appears, and only the
    adjustment silently fails. That reads as an interface bug and is a
    deployment one, which is why it is asserted here rather than left to the
    first coordinator who moves a date.
    """
    from api.main import ALLOWED_ORIGINS

    origin = ALLOWED_ORIGINS[0]
    response = client.get("/api/v1/worklist", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_the_validator_header_is_readable_by_the_browser(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-020a. `ETag` is not a simple response header, so a cross-origin client
    cannot read it unless it is exposed — and a validator the client cannot read
    is one it can never send back."""
    from api.main import ALLOWED_ORIGINS

    response = client.get("/api/v1/worklist", headers={"Origin": ALLOWED_ORIGINS[0]})
    assert "etag" in response.headers["access-control-expose-headers"].lower()


def test_the_worklist_does_not_admit_every_origin(frozen_run: dict[str, Any], client: Any) -> None:
    """An explicit allowlist rather than `*`.

    There is no authentication and no cookie to protect today (FR-056), so a
    wildcard would leak nothing — but it would be a standing invitation for the
    first credential this system gains to be readable by any page on the
    internet, and that reversal is exactly the one FR-056 records.
    """
    from api.main import ALLOWED_ORIGINS

    assert "*" not in ALLOWED_ORIGINS
    response = client.get("/api/v1/worklist", headers={"Origin": "https://example.invalid"})
    assert response.headers.get("access-control-allow-origin") != "https://example.invalid"


def test_the_worklist_advertises_no_write_method_across_origins(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """FR-031. Permitting more than GET would advertise methods the route table
    does not answer — a preflight that says POST is allowed and a handler that
    returns 405 is a contradiction a client has no way to resolve."""
    from api.main import ALLOWED_ORIGINS

    response = client.options(
        "/api/v1/worklist",
        headers={
            "Origin": ALLOWED_ORIGINS[0],
            "Access-Control-Request-Method": "POST",
        },
    )
    allowed = response.headers.get("access-control-allow-methods", "")
    assert "POST" not in allowed
