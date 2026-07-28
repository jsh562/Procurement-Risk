"""T127 — FR-042 / DV-016: two digests, two dispositions, evidenced separately.

FR-042 records the committed fixture's own digest **beside** the input row hash
and requires them to be distinct values: equal digests would mean the run hashed
the file, which is precisely the substitution FR-001 forbids — a hand-edited row,
a partial load or a database of another vintage must move the row hash, and none
of them touches the file.

DV-016 is what the pair buys. A moved `input_fixture_digest` against an unchanged
`input_data_hash` is a **provenance warning**: the rows this fit read are
unchanged and only the chain back to the upstream artifact has broken, so the run
completes and names the break. A moved row hash is the other disposition, and the
reason it cannot be a warning is structural rather than a matter of policy —
recorded here as the run row's own shape.

The warning is exercised for real: a repository root is assembled under this
tier's temporary tree with a mutated fixture and an untouched sidecar, and the
job is run against it. That root sits inside the checkout's gitignored `.tmp/`,
so it is still inside the work tree and the run's code revision resolves.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, discard_run
from model.corpus.manifest import DIGEST_PATTERN
from model.forecast.config import CHAINS, DRAWS_PER_CHAIN
from model.forecast.fit import run_fit
from model.forecast.manifest import read_fixture_provenance
from model.forecast.paths import run_report_path
from model.forecast.read import read_lines_and_events
from model.forecast.serialize import input_data_hash
from model.procurement import paths as procurement_paths
from model.procurement.serialize import dataset_content_hash, read_payload

#: Module-level SQL, never assembled from values (Ruff S608).
RUN_DIGESTS_SQL = text(
    "SELECT input_data_hash, input_fixture_digest FROM forecast_run WHERE run_id = :run_id"
)
ROW_HASH_COLUMNS_SQL = text(
    """
    SELECT column_name FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'forecast_run'
      AND (column_name LIKE '%hash%' OR column_name LIKE '%digest%')
    """
)
MOVE_ONE_ROW_SQL = text(
    """
    UPDATE purchase_order_line SET description = description || :suffix
    WHERE po_line_id = (SELECT po_line_id FROM purchase_order_line
                        ORDER BY project_id, po_number, line_number LIMIT 1)
    """
)

#: What a moved *row* looks like: a suffix on one line's description, which is
#: inside the compared-content projection the row hash is defined over.
ROW_MUTATION_SUFFIX = " (moved for DV-016)"

#: The shape the warning run is emitted at. **The committed shape, and US4 is
#: why it is no longer a tiny one.** The disposition under assertion is still
#: decided before the sampler starts — a moved fixture digest against unchanged
#: rows is a warning and not a refusal — but the run has to *complete* for that
#: to be observable, and `run_fit` now refuses below the four-chain minimum
#: before sampling (FR-035) and on any breached blocking diagnostic after it
#: (FR-017). A fifty-draw fit breaches every ESS bar there is, so the only shape
#: that reaches the other side of the warning is one that converges.
WARNING_RUN_CHAINS = CHAINS
WARNING_RUN_DRAWS = DRAWS_PER_CHAIN
WARNING_RUN_SEED = 20260728


@dataclass(frozen=True, slots=True)
class WarnedRun:
    """A run fitted against a fixture whose digest no longer matches its sidecar.

    Carries the diagnostics the job wrote and the report it emitted, because the
    warning has to be *named* somewhere a reader looks: G-16 records that no
    column stores the fact that it fired, so the stream and the file are the
    whole of the evidence.
    """

    run_id: uuid.UUID
    diagnostics: str
    report: Path
    observed_digest: str
    published_digest: str


@pytest.fixture(scope="module")
def warned_run(
    engine: Engine, emitted_run: EmittedRun, moved_fixture_root: Path, tmp_path_factory
) -> Iterator[WarnedRun]:
    """One run fitted against the moved fixture, with its diagnostics captured.

    Driven through `run_fit` rather than through the console script because the
    mutated root is an argument the job takes and the command line does not
    expose — the point is which disposition the job reaches, and that decision is
    made in the same function either way. The run is discarded afterwards: this
    tier leaves `forecast_run` empty.
    """
    provenance = read_fixture_provenance(moved_fixture_root)
    log = io.StringIO()
    report_root = tmp_path_factory.mktemp("moved-fixture-reports")
    run_id = run_fit(
        engine,
        as_of_date=emitted_run.as_of_date,
        seed_entropy=WARNING_RUN_SEED,
        chains=WARNING_RUN_CHAINS,
        draws=WARNING_RUN_DRAWS,
        tune=WARNING_RUN_DRAWS,
        cores=1,
        report_root=report_root,
        repo_root=moved_fixture_root,
        log=log,
    )
    try:
        yield WarnedRun(
            run_id=run_id,
            diagnostics=log.getvalue(),
            report=run_report_path(run_id, report_root),
            observed_digest=provenance.observed_digest,
            published_digest=provenance.published_digest,
        )
    finally:
        discard_run(engine, run_id)


def test_the_fixture_digest_and_the_row_hash_are_distinct_recorded_values(
    engine: Engine, emitted_run: EmittedRun
) -> None:
    """FR-042: two digests beside each other, and neither a copy of the other.

    Each is checked against what it claims to cover — the fixture digest against
    the committed payload, the row hash against the rows read over the connection
    — so "distinct" is a consequence of them measuring different things rather
    than an assertion that two strings happen to differ.
    """
    with engine.connect() as connection:
        row = connection.execute(RUN_DIGESTS_SQL, {"run_id": emitted_run.run_id}).mappings().one()
        from_rows = input_data_hash(read_lines_and_events(connection))
    from_file = dataset_content_hash(
        read_payload(procurement_paths.fixture_path(procurement_paths.REPO_ROOT))
    )

    assert DIGEST_PATTERN.fullmatch(row["input_data_hash"])
    assert DIGEST_PATTERN.fullmatch(row["input_fixture_digest"])
    assert row["input_data_hash"] != row["input_fixture_digest"], (
        "the two digests are equal, which means the run hashed the committed file rather "
        "than the rows it read — the substitution FR-001 exists to forbid"
    )
    assert row["input_data_hash"] == from_rows
    assert row["input_fixture_digest"] == from_file


def test_a_moved_fixture_digest_against_unchanged_rows_warns_and_completes(
    engine: Engine, emitted_run: EmittedRun, warned_run: WarnedRun
) -> None:
    """DV-016's disposition: the run ships, and the break is named.

    Three things together, because any one of them alone would be satisfied by
    the wrong behaviour: the fixture digest really did move, the row hash really
    did not, and the run reached the far side of the decision and wrote a row. A
    job that refused would fail the third; a job that silently proceeded would
    fail the next test.
    """
    with engine.connect() as connection:
        row = connection.execute(RUN_DIGESTS_SQL, {"run_id": warned_run.run_id}).mappings().one()
        shipped = (
            connection.execute(RUN_DIGESTS_SQL, {"run_id": emitted_run.run_id}).mappings().one()
        )

    assert warned_run.observed_digest != warned_run.published_digest
    assert row["input_fixture_digest"] == warned_run.observed_digest
    assert row["input_data_hash"] == shipped["input_data_hash"], (
        "the row hash moved as well, so this run does not exercise DV-016's case — the rule "
        "is about a moved fixture digest against an *unchanged* input row hash"
    )


def test_the_provenance_warning_names_the_break_on_both_of_its_surfaces(
    warned_run: WarnedRun,
) -> None:
    """G-16: no column records that the warning fired, so the pair must.

    A later reader can reconstruct the break by comparing the stored fixture
    digest against E005's published value; what they cannot see is that the job
    named it at the time. The diagnostic stream and the emitted report are the
    whole of that record, and both are checked — a disclosure resting on a file
    nothing inspects rests on nothing.
    """
    diagnostics = warned_run.diagnostics

    assert "provenance warning" in diagnostics
    assert warned_run.observed_digest in diagnostics
    assert warned_run.published_digest in diagnostics
    assert "proceeds" in diagnostics

    report = warned_run.report.read_text(encoding="utf-8")

    assert "Fixture digest agreement" in report
    assert "differs from the digest E005 publishes" in report
    assert warned_run.observed_digest in report


def test_the_shipped_runs_report_records_the_agreeing_disposition_instead(
    emitted_run: EmittedRun,
) -> None:
    """The other side of the same field, so the two dispositions are separable.

    Without this, the warning assertion above would pass against a report that
    printed the break unconditionally. The run fitted against the committed
    fixture records agreement in the same field, which is what makes the field a
    measurement rather than a caption.
    """
    report = run_report_path(emitted_run.run_id, emitted_run.report_root).read_text(
        encoding="utf-8"
    )

    assert "Fixture digest agreement" in report
    assert "agrees with the digest E005 publishes for it" in report
    assert "differs from the digest E005 publishes" not in report


def test_a_moved_row_moves_the_row_hash_and_leaves_the_fixture_digest_alone(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The independence the two dispositions rest on, measured in both directions.

    A mutated fixture moves one digest and not the other — the tests above. A
    mutated *row* moves the other and not the first, because the row hash covers
    the rows and the fixture digest covers the file. Without this the pair could
    be two names for one measurement, and DV-016's precondition — "against an
    unchanged `input_data_hash`" — would be unreachable rather than merely
    untested. The edit is discarded with the test's transaction.
    """
    recorded = db_session.execute(RUN_DIGESTS_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    before = input_data_hash(read_lines_and_events(db_session))
    db_session.execute(MOVE_ONE_ROW_SQL, {"suffix": ROW_MUTATION_SUFFIX})
    after = input_data_hash(read_lines_and_events(db_session))
    from_file = dataset_content_hash(
        read_payload(procurement_paths.fixture_path(procurement_paths.REPO_ROOT))
    )

    assert before == recorded["input_data_hash"]
    assert before != after, (
        "editing a line's description left the input row hash unmoved; the digest covers "
        "E005's compared-content projection, and a hand-edited row that does not move it is "
        "a row FR-023 could never refuse on"
    )
    assert from_file == recorded["input_fixture_digest"]
    assert from_file not in (before, after)


def test_the_row_hash_has_no_published_counterpart_to_warn_against(
    db_session: Session,
) -> None:
    """Why one disposition is a warning and the other cannot be.

    The fixture digest has a second value to compare against at run time — E005's
    committed sidecar — so the job can report a disagreement and continue. The row
    hash has exactly one column on the run row and no published counterpart
    anywhere beside it, so a disagreement between a recorded hash and the current
    rows is not something a run can qualify: it says the rows are not the rows,
    and DV-015 refuses on it.
    """
    columns = set(db_session.execute(ROW_HASH_COLUMNS_SQL).scalars())
    beside_the_row_hash = {name for name in columns if name.startswith("input_data_hash")} - {
        "input_data_hash"
    }
    sidecar = procurement_paths.hash_path(procurement_paths.REPO_ROOT)

    assert "input_data_hash" in columns
    assert not beside_the_row_hash, (
        f"a second column beside the row hash would make its mismatch a reportable "
        f"disagreement rather than a refusal: {sorted(columns)}"
    )
    assert "input_fixture_digest" in columns
    assert "dataset_content_hash" in read_payload(sidecar)
