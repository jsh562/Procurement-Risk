"""FR-066 / SC-024 / VR-011 (T085): the thirteen privilege refusals, measured
under `SET LOCAL ROLE procurement_app`.

Revision `0304` follows E003's `0009` shape exactly — grant the ordinary four
verbs, then take two back — so the append-only rule reads as a deliberate revoke
rather than as an omission, and an omission is indistinguishable from having
forgotten. What that leaves is **thirteen refusals**, and they are enumerated
below one parameter case each, every case naming the object and the privilege it
covers:

* six append-only tables x `UPDATE` and `DELETE` — twelve
* `DELETE` on `ingestion_run` — the thirteenth

`ingestion_run` keeps `UPDATE`, and that is not a provenance edit: `finished_at`
and the two run-failure columns are written after the row is inserted, the last
of them in a fresh transaction after a rollback. Withholding it would make
FR-056's run-level failure unwritable by the job that has to write it.

**`ingestion_run_document` is among the twelve, and the reason is {SAD:ADR-0020}.**
An earlier design granted it `UPDATE` for write-order step 0a's
`active -> superseded` flip. It no longer can hold one: the flip names a
generation that steps 0c-0g then delete, and this role holds `DELETE` on none of
those tables, so a re-ingest under it would flip the row and then abort on the
first delete — a pointless privilege in front of a failed transaction. The flip
moved to the schema-owning role together with the removal it names.

**Measured under an explicit role switch, which is the only configuration in
which the revoke binds** (SC-024, G-6). The deployed connection role is the
SUPERUSER `procurement`, which bypasses every privilege check; asserting these
grants as the connected role would report a guarantee that is not operative
where the application actually runs. `SET LOCAL ROLE` scopes the switch to this
test's transaction, which the harness rolls back regardless.

**The grants that are held are asserted too, and that is not padding.** A
refusal test over a role with no privileges at all passes for the wrong reason:
every verb is refused when the role cannot reach the table. The positive cases
are what make the thirteen negatives measurements of a *revoke* rather than of
an absent grant.
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: The role `0009` creates and `0304` constrains.
APPLICATION_ROLE = "procurement_app"

#: The six tables E006 adds beyond the run record. Append-only by privilege,
#: matching the three tables `0009` already covers: these rows *are* provenance,
#: and an association silently repointed at a different run makes SC-021 true
#: and meaningless.
APPEND_ONLY_TABLES = (
    "ingestion_run_document",
    "ingestion_run_chunk",
    "ingestion_run_extracted_value",
    "ingestion_run_extraction_failure",
    "extracted_value_line_item",
    "extracted_value_parse_signal",
)

#: The thirteen, each naming the object and the privilege it covers, and each
#: carrying the reason that refusal exists. Built from the six rather than typed
#: out twelve times, so a table added to the epic and not to this list is a
#: shorter enumeration rather than a silently missing case — `test_the_refusals
#: _number_thirteen` is what turns that into a failure.
REFUSALS: tuple[tuple[str, str, str], ...] = (
    *(
        (table, verb, "append-only provenance: a generation's rows are written once")
        for table in APPEND_ONLY_TABLES
        for verb in ("UPDATE", "DELETE")
    ),
    (
        "ingestion_run",
        "DELETE",
        "promotion stops at the generation row and never removes a run "
        "(§Operator Procedures, step 6), so the privilege would authorise only the one "
        "deletion the design forbids",
    ),
)

#: What the role **does** hold. Asserted so the thirteen above are measurements
#: of a revoke rather than of a role that cannot reach these tables at all.
GRANTS: tuple[tuple[str, str], ...] = (
    *((table, verb) for table in APPEND_ONLY_TABLES for verb in ("SELECT", "INSERT")),
    ("ingestion_run", "SELECT"),
    ("ingestion_run", "INSERT"),
    ("ingestion_run", "UPDATE"),
)

#: The reader-filtering view, read-only by intent and by grant. Not swept into
#: the four-verb grant and then narrowed: an auto-updatable view can carry
#: `INSERT` in PostgreSQL, and granting one even to revoke it a statement later
#: would state that writing through the view had been contemplated.
VIEW = "v_active_ingestion_generation"

_SWITCH = text(f"SET LOCAL ROLE {APPLICATION_ROLE}")
_PRIVILEGE = text("SELECT has_table_privilege(current_user, :object, :privilege)")
_CURRENT_USER = text("SELECT current_user")


@pytest.fixture
def as_application_role(db_session: Session) -> Session:
    """The session, switched to the application role for this transaction.

    `SET LOCAL` binds the switch to the transaction the harness already opened
    and rolls back in teardown, so no `RESET ROLE` is needed and a failing test
    cannot leak the switch into the next one.
    """
    db_session.execute(_SWITCH)
    assert db_session.execute(_CURRENT_USER).scalar_one() == APPLICATION_ROLE, (
        "the role switch did not take effect, so every assertion below would be measuring "
        "the superuser the harness connects as — which bypasses every privilege check"
    )
    return db_session


def _holds(session: Session, object_name: str, privilege: str) -> bool:
    return bool(
        session.execute(_PRIVILEGE, {"object": object_name, "privilege": privilege}).scalar_one()
    )


def test_the_refusals_number_thirteen() -> None:
    """FR-066's count, stated where a reader can compare it with the revision.

    Twelve from six tables and two verbs, plus `DELETE` on the run record.
    Asserted rather than left implicit because the number appears in `0304`'s
    own docstring and in the data model, and three places agreeing is worth
    something only if one of them fails when they stop.
    """
    assert len(REFUSALS) == 13
    assert len({(table, verb) for table, verb, _ in REFUSALS}) == 13


@pytest.mark.parametrize(
    ("object_name", "privilege", "reason"),
    REFUSALS,
    ids=[f"{table}-{verb.lower()}" for table, verb, _ in REFUSALS],
)
def test_the_application_role_is_refused(
    as_application_role: Session, object_name: str, privilege: str, reason: str
) -> None:
    """One case per refusal, named for the object and the privilege it covers."""
    assert not _holds(as_application_role, object_name, privilege), (
        f"FR-066 / VR-011: `{APPLICATION_ROLE}` holds {privilege} on `{object_name}`, which "
        f"revision 0304 revokes — {reason}."
    )


@pytest.mark.parametrize(
    ("object_name", "privilege"),
    GRANTS,
    ids=[f"{table}-{verb.lower()}" for table, verb in GRANTS],
)
def test_the_application_role_holds_what_the_job_needs(
    as_application_role: Session, object_name: str, privilege: str
) -> None:
    """The positive controls, without which the refusals prove nothing.

    A role that cannot reach a table is refused every verb on it, so a suite of
    negatives alone would pass against a revision that granted nothing at all —
    and the ingestion job would then fail on its first insert.
    """
    assert _holds(as_application_role, object_name, privilege), (
        f"FR-066: `{APPLICATION_ROLE}` lacks {privilege} on `{object_name}`, which the "
        f"ingestion job needs; the thirteen refusals would then be measuring an absent "
        f"grant rather than a deliberate revoke"
    )


def test_the_run_record_keeps_update_and_only_for_three_columns(
    as_application_role: Session,
) -> None:
    """`ingestion_run` is the one table with `UPDATE`, and why (FR-066).

    `finished_at` and the two run-failure columns are written after the row is
    inserted — the last of them in a fresh transaction after a rollback — so the
    job must be able to issue the statement. That the *permitted* updates are
    four and no others is a design rule this grant cannot express: PostgreSQL
    column-level privileges were not used, and the narrowing is carried by there
    being exactly two statements in `ingest/runs.py` that update this table.
    Recorded here so the gap is disclosed rather than implied by a passing test.
    """
    assert _holds(as_application_role, "ingestion_run", "UPDATE")
    assert not _holds(as_application_role, "ingestion_run", "DELETE")


def test_the_generation_view_is_readable_and_not_writable(
    as_application_role: Session,
) -> None:
    """The view is `SELECT` and nothing else ({SAD:ADR-0019} reader contract)."""
    assert _holds(as_application_role, VIEW, "SELECT")
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not _holds(as_application_role, VIEW, verb), (
            f"`{APPLICATION_ROLE}` holds {verb} on `{VIEW}`; the reader-filtering view is "
            f"read-only by intent and by grant"
        )


def test_a_revoked_verb_is_refused_by_the_server_and_not_only_by_the_catalog(
    db_session: Session,
) -> None:
    """One live statement, so the catalog answer is not the only evidence.

    `has_table_privilege` is the catalog's account of the grant; this is the
    server acting on it. Held to one case rather than thirteen because the two
    are the same mechanism and thirteen aborted statements would cost thirteen
    savepoints to say one thing — but zero live cases would leave the whole
    module resting on a function that could, in principle, disagree with the
    executor.
    """
    savepoint = db_session.begin_nested()
    db_session.execute(_SWITCH)
    try:
        with pytest.raises(Exception) as caught:  # noqa: PT011 - class asserted below
            db_session.execute(text("DELETE FROM ingestion_run_chunk"))
    finally:
        savepoint.rollback()
    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege), (  # type: ignore[attr-defined]
        f"a DELETE issued as `{APPLICATION_ROLE}` was not refused with SQLSTATE 42501; the "
        f"catalog and the executor disagree about the revoke"
    )
