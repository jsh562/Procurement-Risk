"""T046 — FR-034 / DV-032: artifact rows written once, and the grant that says so.

Two things, because the requirement has two and only one of them is a privilege.
The **grant**: each of the three tables this epic creates carries `SELECT,
INSERT, DELETE` for `procurement_app` and no `UPDATE`, read from
`information_schema.role_table_grants` and confirmed through
`has_table_privilege`, which is the question the executor actually asks and the
one an inherited grant would show up in.

The **write**: the rows the shared run committed were inserted by one transaction
and never rewritten. That is measured from PostgreSQL's own row versioning rather
than argued — an `UPDATE` produces a new tuple whose `xmin` is the updating
transaction, so a store whose rows all carry one `xmin` is a store nothing
revisited, and `forecast_run` carrying a *later* one is the epic's single
`UPDATE`: the `is_active` flip, in the transaction of its own AD-010 gives it.

E003 G-11 still applies: the deployed process connects as a superuser, so the
grant is a latent fact about `procurement_app` rather than an active restriction.
That is why the row-version evidence is here beside it.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.write import (
    ACTIVATE_RUN_SQL,
    CLEAR_ACTIVE_RUN_SQL,
    LINE_POSTERIOR_INSERT,
    RUN_INSERT,
    SPLIT_ASSIGNMENT_INSERT,
)

#: Module-level SQL, never assembled from values (Ruff S608).
GRANTED_VERBS_SQL = text(
    """
    SELECT privilege_type FROM information_schema.role_table_grants
    WHERE grantee = :grantee AND table_schema = 'public' AND table_name = :table_name
    """
)
HOLDS_PRIVILEGE_SQL = text("SELECT has_table_privilege(:grantee, :table_name, :verb) AS held")
POSTERIOR_VERSIONS_SQL = text(
    """
    SELECT DISTINCT xmin::text AS inserted_by, age(xmin) AS transactions_since
    FROM line_posterior WHERE run_id = :run_id
    """
)
ASSIGNMENT_VERSIONS_SQL = text(
    """
    SELECT DISTINCT xmin::text AS inserted_by, age(xmin) AS transactions_since
    FROM forecast_split_assignment WHERE run_id = :run_id
    """
)
RUN_VERSION_SQL = text(
    """
    SELECT xmin::text AS inserted_by, age(xmin) AS transactions_since
    FROM forecast_run WHERE run_id = :run_id
    """
)
LIVE_TUPLE_XMAX_SQL = text(
    """
    SELECT count(*) AS marked FROM line_posterior
    WHERE run_id = :run_id AND xmax::text <> '0'
    """
)

#: The role `0009` created and every explicit grant since names.
APPLICATION_ROLE = "procurement_app"

#: The three tables E007 creates, and the only three DV-032(a) ranges over.
#: `forecast_run` and `line_posterior` are E003's and keep the `UPDATE` its
#: `0009` granted — this epic may not revoke a privilege on a delivered table,
#: which is why `line_posterior`'s written-once claim rests on the writer and on
#: the row-version evidence below rather than on a privilege.
CREATED_TABLES = ("forecast_split_assignment", "held_out_prediction", "forecast_diagnostic")

#: The verbs FR-034 grants and the one it withholds.
GRANTED_VERBS = ("SELECT", "INSERT", "DELETE")
WITHHELD_VERB = "UPDATE"


def _granted(db_session: Session, table_name: str) -> set[str]:
    """Every privilege the catalog records for the application role on one table."""
    return set(
        db_session.execute(
            GRANTED_VERBS_SQL, {"grantee": APPLICATION_ROLE, "table_name": table_name}
        ).scalars()
    )


def test_each_created_table_grants_select_insert_and_delete(db_session: Session) -> None:
    """The retained half, asserted alongside the withheld half deliberately.

    `DELETE` is retained so discarding a run is a plain operation rather than a
    reliance on the privilege model of a cascading referential action. Asserting
    the absence of `UPDATE` on its own would pass just as well against a role
    that had never been granted anything at all.
    """
    for table_name in CREATED_TABLES:
        granted = _granted(db_session, table_name)

        assert set(GRANTED_VERBS) <= granted, (
            f"{APPLICATION_ROLE} is missing {sorted(set(GRANTED_VERBS) - granted)} on "
            f"{table_name}; the store is append-and-discard, not read-only"
        )


def test_no_created_table_grants_update_to_the_application_role(db_session: Session) -> None:
    """DV-032(a): `UPDATE` is withheld, and PostgreSQL records no negative grant.

    After a grant that never named `UPDATE` there is simply no `UPDATE` row for
    this grantee — there is no catalogued "denied" — so the claim is precisely
    that the verb is absent from the view *and* that the function the executor
    consults agrees, which is where a privilege arriving through role membership
    would show up and the view would not.
    """
    for table_name in CREATED_TABLES:
        assert WITHHELD_VERB not in _granted(db_session, table_name), (
            f"information_schema.role_table_grants lists {WITHHELD_VERB} on {table_name} for "
            f"{APPLICATION_ROLE}; an artifact row is written once and never edited"
        )
        held = db_session.execute(
            HOLDS_PRIVILEGE_SQL,
            {"grantee": APPLICATION_ROLE, "table_name": table_name, "verb": WITHHELD_VERB},
        ).scalar_one()

        assert not held, (
            f"has_table_privilege reports {APPLICATION_ROLE} holds {WITHHELD_VERB} on "
            f"{table_name} while the view does not, so it is arriving through role membership"
        )


def test_every_artifact_row_of_the_emitted_run_carries_one_insertion(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """ "Inserted once, never updated", measured from PostgreSQL's row versions.

    Both artifact stores under this run report a single `xmin`, and it is the
    same one across the two, so transaction 1 wrote all of them and nothing
    rewrote any of them afterwards. An `UPDATE` — of one column, of one row —
    would have produced a tuple carrying a different transaction and split the
    set.
    """
    parameters = {"run_id": emitted_run.run_id}
    posterior_versions = db_session.execute(POSTERIOR_VERSIONS_SQL, parameters).mappings().all()
    assignment_versions = db_session.execute(ASSIGNMENT_VERSIONS_SQL, parameters).mappings().all()

    assert len(posterior_versions) == 1, (
        f"the run's `line_posterior` rows carry "
        f"{[row['inserted_by'] for row in posterior_versions]} insertions; more than one "
        f"means a row was rewritten after it was stored"
    )
    assert len(assignment_versions) == 1
    assert posterior_versions[0]["inserted_by"] == assignment_versions[0]["inserted_by"], (
        "the split assignments and the artifact rows were written by different transactions; "
        "AD-010 makes them one, which is what makes SC-015's enumeration across stores hold "
        "with no per-store mechanism"
    )
    assert db_session.execute(LIVE_TUPLE_XMAX_SQL, parameters).scalar_one() == 0


def test_the_only_row_a_later_transaction_touched_is_the_run_row(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The epic's one `UPDATE`, located: the pointer, on the run row, afterwards.

    `age(xmin)` counts transactions back from now, so the smaller age is the more
    recent write. The run row's version is younger than every artifact row's,
    which is a later transaction rewriting `is_active` after transaction 1 had
    already made the whole artifact set durable — the ordering AD-010 chose,
    observed rather than asserted from the source. Stated as "a later
    transaction" rather than "transaction 2" because any run this tier emits
    afterwards clears the pointer here as well, and the claim under assertion is
    the ordering rather than the count.
    """
    parameters = {"run_id": emitted_run.run_id}
    run_version = db_session.execute(RUN_VERSION_SQL, parameters).mappings().one()
    posterior_version = db_session.execute(POSTERIOR_VERSIONS_SQL, parameters).mappings().one()

    assert run_version["inserted_by"] != posterior_version["inserted_by"]
    assert run_version["transactions_since"] < posterior_version["transactions_since"], (
        f"the run row was written {run_version['transactions_since']} transactions ago and "
        f"the artifact rows {posterior_version['transactions_since']}; the pointer must be "
        f"set after every artifact is durable, never before"
    )


def test_the_writer_issues_no_update_against_any_artifact_store() -> None:
    """FR-034 in the statements themselves, not merely in the privilege.

    Under a superuser connection the grant restricts nothing (E003 G-11), so the
    guarantee has to be carried by the writer. The three statements that reach an
    artifact store are `INSERT`s and the two that are not name `forecast_run` and
    set `is_active` — which is the one `UPDATE` this epic issues and it is against
    the run row.
    """
    inserts = (RUN_INSERT, SPLIT_ASSIGNMENT_INSERT, LINE_POSTERIOR_INSERT)
    for statement in inserts:
        rendered = str(statement).strip().upper()

        assert rendered.startswith("INSERT INTO")
        assert WITHHELD_VERB not in rendered

    for statement in (CLEAR_ACTIVE_RUN_SQL, ACTIVATE_RUN_SQL):
        rendered = str(statement).strip().upper()

        assert rendered.startswith(f"{WITHHELD_VERB} FORECAST_RUN SET IS_ACTIVE")
