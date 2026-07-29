"""T097: the whole ingestion pipeline, driven from `cli.run_ingestion`.

Every component of this epic existed before this module and nothing ran them in
order. What is asserted here is the *order* and its two closures — the run
record and its policy written before the first document (FR-038, FR-032), the
per-document plan (FR-043), one transaction per document with extraction
feeding its value rows (FR-042, FR-054), and either `finish_run` or the
run-level failure `abort_run` records (FR-056) — together with the disposition
ledger that says what happened to every enumerated document (FR-073).

**A real database, real corpus documents, and a substituted provider.** The
database is a scratch one per test with the chain applied through the `migrate`
entry, as `test_write_order.py` does and for the same reasons: the connection
must be autocommit and the rows must really commit, so `db_session`'s
never-committed outer transaction is exactly wrong here. The documents are the
committed synthetic transmittals, read and chunked and embedded for real,
because `prepare_document` is part of what T097 wires and a constructed
`PreparedDocument` would skip it. The provider is the one thing substituted, at
the same injected-invoker seam `tests/ingest/test_extraction_stage.py` uses —
and it has to be, since **zero extraction fixtures are committed** (T081), so a
`replay` run against the real gateway reaches `fixture_missing` before it
reaches anything this module is about. That path is asserted too, with the same
error the gateway raises.

**Three documents at most per test, and that is a cost decision.** Chunking and
embedding one transmittal is seconds; the whole corpus is minutes. Three is the
smallest number that distinguishes FR-073's `not_reached` from its
`rolled_back` — one committed before the abort, one in flight, one never begun.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import psycopg
import pytest
from gateway.api import InvocationRequest, InvocationResult
from gateway.config import MODE_ENV_VAR, REPLAY_MODE, SPOOL_PATH_ENV_VAR
from gateway.fixtures import FixtureMissError
from sqlalchemy import URL

from model.corpus.paths import DEFAULT_CORPUS_ROOT
from model.ingest import publish
from model.ingest.chunker import ChunkerError
from model.ingest.cli import (
    EXIT_ABORTED,
    EXIT_OK,
    PRICE_TABLE_PIN_ENV_VAR,
    VCS_REVISION_ENV_VAR,
    ExtractionScope,
    OrchestrationError,
    RunOutcome,
    main,
    run_ingestion,
    unretired_field_names,
)
from model.ingest.documents import DocumentRecord, build_documents
from model.ingest.manifest_reader import iter_entries
from model.ingest.report import REPORT_CONTENTS, REPORT_PATH, RESULTS_MANIFEST_PATH
from model.ingest.runs import read_run_state
from model.ingest.writer import connect
from model.schema.cli import main as migrate

MANIFEST_DIGESTS = (f"sha256:{'c' * 64}",)
TRACE_ID = "6b658d817ffb432b932b4debbdaf2953"

#: `price_table_version.version_id` as E003's revision 0103 seeds it. Every
#: invocation is priced against a pinned version (TR-048) — a replayed one from
#: the fixture's recorded token counts — so a run in either mode needs it set.
SEEDED_PRICE_TABLE_VERSION = "2026-07-26-published"

#: One value, on the first invocation of the run and no other. Enough to prove a
#: value reaches `extracted_value` through the hook, and few enough that the
#: other nine attempted terms land as `no_value_found` records — which is the
#: other half of FR-069's "every attempt resolves to exactly one of the two".
FIRST_VALUE = (
    '{"values": [{"field_name": "submittal_number", "printed_label": "Submittal Number", '
    '"value_text": "SUB-0001", "item_ordinal": 0}]}'
)
NO_VALUES = '{"values": []}'


@lru_cache(maxsize=1)
def _transmittals() -> tuple[DocumentRecord, ...]:
    """The committed synthetic transmittals, in enumeration order.

    Cached for the module: reading the manifests is cheap but repeating it once
    per test buys nothing, and every test wants the same first few documents.
    """
    return tuple(
        record
        for record in build_documents(tuple(iter_entries()))
        if record.document_type == "transmittal"
    )


def _records(count: int) -> tuple[DocumentRecord, ...]:
    records = _transmittals()[:count]
    assert len(records) == count, "the committed corpus holds fewer transmittals than this test"
    return records


@lru_cache(maxsize=1)
def _specification() -> DocumentRecord:
    """One committed real specification, for the tests that need both layers.

    FR-053 publishes every chunking figure **per layer as well as pooled**, and
    a percentile over an empty layer is not published — so a report over a
    transmittal-only corpus is a report with items 8 and 9 missing, correctly.
    A test asserting that all twenty-one items can be emitted therefore has to
    enumerate a document on each layer, which is also what every real run does.
    """
    found = next(
        record
        for record in build_documents(tuple(iter_entries()))
        if record.document_type == "specification"
    )
    return found


def _scope(attempted: tuple[DocumentRecord, ...], excluded: tuple[DocumentRecord, ...] = ()):
    return ExtractionScope(attempted=attempted, excluded=excluded)


class OneValueThenNothing:
    """Answers the first invocation with a value and every later one with none.

    Keyed on arrival order rather than on the request, so a run that issued a
    different number of invocations changes what this returns and the test
    notices. `raise_on` makes the *n*th invocation raise instead, which is how
    the abort path is reached without a fixture store.
    """

    def __init__(self, *, raise_on: int | None = None) -> None:
        self.calls = 0
        self.raise_on = raise_on

    def __call__(self, request: InvocationRequest) -> InvocationResult:
        self.calls += 1
        if self.raise_on is not None and self.calls >= self.raise_on:
            raise FixtureMissError(f"sha256:{'1' * 64}", Path("src/gateway/fixtures"))
        return InvocationResult(
            invocation_id=f"a3f1c0de-0000-4000-8000-{self.calls:012d}",
            trace_id=request.trace_id or TRACE_ID,
            content=FIRST_VALUE if self.calls == 1 else NO_VALUES,
            outcome="valid",
            resolution_mode="replay",
        )


@pytest.fixture
def migrated(empty_scratch_database: URL) -> Iterator[psycopg.Connection]:
    """A migrated scratch database and one **autocommit** connection to it."""
    assert migrate(["head"]) == 0, "the migration chain did not apply to the scratch database"
    with connect(empty_scratch_database) as connection:
        yield connection


def _count(connection: psycopg.Connection, statement: str, *parameters: object) -> int:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _run(
    connection: psycopg.Connection,
    records: tuple[DocumentRecord, ...],
    scope: ExtractionScope,
    invoke: OneValueThenNothing,
    *,
    report_root: Path,
) -> RunOutcome:
    """One run, with its report directed into the test's own scratch.

    `report_root` is not optional here on purpose. `run_ingestion` now publishes
    the report as its last step (FR-071), and the default root is the checkout —
    so a test that let the default stand would replace the committed
    `ingestion-report.md` and `results-manifest.json` with figures computed over
    a two-document corpus, on a machine where the tests happened to make every
    item buildable. Requiring the argument makes that impossible to forget.
    """
    return run_ingestion(
        connection,
        records=records,
        scope=scope,
        mode="replay",
        trace_id=TRACE_ID,
        manifest_digests=MANIFEST_DIGESTS,
        invoke=invoke,
        report_root=report_root,
    )


# ---------------------------------------------------------------------------
# The complete run: values written, finish recorded, every document ingested
# ---------------------------------------------------------------------------


def test_a_complete_run_writes_values_finishes_and_accounts_for_every_document(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """FR-044 end to end, and FR-022's excluded side inside the same run.

    The second document is placed on the excluded side of the partition, so the
    hook returns `None` for it and it commits its chunks with zero values —
    which is the state FR-022 requires to be *recorded* rather than inferred.
    Its zero is asserted here against the same run that wrote another document's
    values, which is the only place the two are distinguishable by observation.
    """
    connection = migrated
    records = _records(2)
    invoke = OneValueThenNothing()
    outcome = _run(
        connection, records, _scope(records[:1], records[1:]), invoke, report_root=tmp_path
    )

    assert outcome.complete and outcome.exit_code == EXIT_OK
    assert outcome.ledger.counts == {
        "ingested": 2,
        "skipped_unchanged": 0,
        "rolled_back": 0,
        "not_reached": 0,
    }
    assert read_run_state(connection, outcome.run_id) == "complete"
    assert outcome.values_written == 1, "the one value the invoker returned reached a row"
    assert outcome.chunks_written > 0
    assert len(outcome.extractions) == 1, "extraction ran on the attempted document alone"
    assert outcome.invocations == invoke.calls, (
        "FR-069: one invocation per chunk, counted from the stage's own outcome rather than "
        "from the loop that issued the calls"
    )

    stored = _count(
        connection,
        "SELECT count(*) FROM ingestion_run_extracted_value WHERE run_id = %s",
        str(outcome.run_id),
    )
    assert stored == 1, "FR-039: the value resolves to this run through its association"

    # Asked through the association tables and not of `extracted_value`, which
    # carries no `document_id` at all: E003 owns that table and this epic
    # attributes its rows by association (FR-039). The question "what did this
    # document produce" is therefore only answerable inside a generation, which
    # is the property the association exists to create.
    for table in ("ingestion_run_extracted_value", "ingestion_run_extraction_failure"):
        assert (
            _count(
                connection,
                f"SELECT count(*) FROM {table} WHERE document_id = %s",  # noqa: S608
                records[1].document_id,
            )
            == 0
        ), (
            f"FR-022: the excluded document carries no {table} row — zero values, zero "
            f"failures, and complete in that state rather than short (SC-042)"
        )
    assert (
        _count(
            connection,
            "SELECT count(*) FROM ingestion_run_chunk WHERE document_id = %s",
            records[1].document_id,
        )
        > 0
    ), "and it carries chunks and their run associations like any other document"
    assert invoke.calls > 0, "the attempted document did reach the provider seam"


def test_a_second_run_over_an_unchanged_corpus_writes_nothing_and_still_completes(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """FR-043's skip, from the entry rather than from `plan_documents` alone.

    The second run is the one that proves the ledger's four are a partition and
    not a synonym set: every document is `skipped_unchanged`, none is
    `ingested`, and the run still completes. A run that reported them as
    ingested would be claiming rows it did not write.
    """
    connection = migrated
    records = _records(2)
    first = _run(
        connection,
        records,
        _scope(records[:1], records[1:]),
        OneValueThenNothing(),
        report_root=tmp_path,
    )
    assert first.complete

    second = _run(
        connection,
        records,
        _scope(records[:1], records[1:]),
        OneValueThenNothing(),
        report_root=tmp_path,
    )
    assert second.complete and second.exit_code == EXIT_OK
    assert second.ledger.counts == {
        "ingested": 0,
        "skipped_unchanged": 2,
        "rolled_back": 0,
        "not_reached": 0,
    }
    assert second.chunks_written == 0 and second.values_written == 0
    assert second.run_id != first.run_id
    assert read_run_state(connection, second.run_id) == "complete"


# ---------------------------------------------------------------------------
# FR-056 / FR-073 — the abort, and the three dispositions it produces
# ---------------------------------------------------------------------------


def test_a_missing_fixture_aborts_the_run_and_leaves_the_earlier_document_durable(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """The partial run, which is what a fixture-less `replay` actually is.

    One document commits, the next is the one in flight when the run aborts, and
    the third is never begun. The failure is recorded on `ingestion_run` in a
    transaction of its own after the rollback (HINT-002), which is why it
    survives the document it explains — and the run carries no finish, because a
    run that aborted did not complete.
    """
    connection = migrated
    records = _records(3)
    invoke = OneValueThenNothing(raise_on=2)
    outcome = _run(connection, records, _scope(records), invoke, report_root=tmp_path)

    assert not outcome.complete and outcome.exit_code == EXIT_ABORTED
    assert outcome.failure is not None and outcome.failure.kind == "fixture_missing"
    assert outcome.failure.document_id in {records[0].document_id, records[1].document_id}
    assert outcome.ledger.counts["rolled_back"] == 1
    assert outcome.ledger.counts["not_reached"] >= 1
    assert sum(outcome.ledger.counts.values()) == 3

    assert read_run_state(connection, outcome.run_id) == "aborted"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_failure_kind, run_failure_detail, finished_at FROM ingestion_run "
            "WHERE run_id = %s",
            (str(outcome.run_id),),
        )
        row = cursor.fetchone()
    assert row is not None
    kind, detail, finished_at = row
    assert kind == "fixture_missing"
    assert outcome.ledger.rolled_back[0] in detail, "FR-056 names the document in flight"
    assert "sha256:" in detail, "and the resolution key that missed"
    assert finished_at is None, "an aborted run carries no finish"

    for document_id in outcome.ledger.rolled_back + outcome.ledger.not_reached:
        assert (
            _count(connection, "SELECT count(*) FROM chunk WHERE document_id = %s", document_id)
            == 0
        ), f"{document_id} has no rows: it rolled back or was never begun"
    for document_id in outcome.ledger.ingested:
        assert (
            _count(connection, "SELECT count(*) FROM chunk WHERE document_id = %s", document_id) > 0
        ), f"{document_id} committed before the abort and stays durable (FR-042)"


# ---------------------------------------------------------------------------
# The run-time vocabulary, read from the database rather than declared
# ---------------------------------------------------------------------------


def test_the_attempted_vocabulary_is_read_from_the_database(
    migrated: psycopg.Connection,
) -> None:
    """FR-024: retirement is a run-time fact, so the names come from the row.

    Sorted, so two runs against one database derive the same prompt digest —
    the digest goes into every document's input tuple, and one that depended on
    a query plan would reload the corpus at random.
    """
    names = unretired_field_names(migrated)
    assert len(names) == 22, "E003's revision 0005 seeds 22 terms, none retired"
    assert list(names) == sorted(names)


def test_a_resident_generation_this_run_may_not_replace_is_refused_before_the_record(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """{SAD:ADR-0020}: the unattended run refuses rather than half-writing.

    The refusal precedes the write of any document, so the alternative it
    excludes is visible: a run that committed every fresh document and then
    aborted on the first resident one, having needed a privilege it does not
    hold to get any further.
    """
    connection = migrated
    records = _records(1)
    assert _run(
        connection, records, _scope(records), OneValueThenNothing(), report_root=tmp_path
    ).complete

    # A different manifest digest moves nothing — the digest is not in the input
    # tuple — so the tuple is moved through the member that is: the run's
    # provider model reaches it, and so does the prompt digest. Retiring a term
    # is the supported way to move the prompt digest, and it is what a run at a
    # narrowed vocabulary would do.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE field_vocabulary SET retired_at = current_date WHERE field_name = %s",
            ("submittal_status",),
        )
    with pytest.raises(OrchestrationError, match="FR-055"):
        _run(connection, records, _scope(records), OneValueThenNothing(), report_root=tmp_path)


# ---------------------------------------------------------------------------
# FR-044 — the console entry itself, over a one-document corpus
# ---------------------------------------------------------------------------


def _one_document_corpus(root: Path) -> str:
    """A corpus root holding one committed transmittal, manifest and all.

    A *copy* of a committed location with its entry list trimmed to one, rather
    than a hand-built manifest: every field a manifest carries is provenance
    (`project-instructions.md` §Data Provenance), and a fabricated one would
    make this test pass against a shape the real corpus does not have. The
    document's own bytes are copied unchanged, so FR-005's hash verification is
    the real check rather than a recomputed agreement.
    """
    source = DEFAULT_CORPUS_ROOT / "synthetic" / "PRJ-001"
    payload = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    payload["entries"] = [entry]
    location = root / "synthetic" / "PRJ-001"
    location.mkdir(parents=True)
    (location / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    shutil.copyfile(source / entry["location"], location / entry["location"])
    return entry["location"]


def test_the_console_entry_runs_the_pipeline_and_exits_three_on_a_partial_run(
    migrated: psycopg.Connection,
    empty_scratch_database: URL,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T097 end to end, through `main`, against the **real** gateway.

    No injected invoker here, deliberately: this is the one assertion that the
    entry an operator types reaches the traced path at all. With zero fixtures
    committed (T081) that path resolves to `fixture_missing`, which is FR-045's
    designed outcome — the run aborts, records one of FR-056's five, publishes a
    ledger that sums, and exits 3 rather than raising.

    One document rather than the committed 51, because chunking and embedding
    the whole corpus is minutes and none of what this asserts needs a second
    document. `run_ingestion`'s multi-document behaviour is asserted above.

    `migrated` supplies the scratch database *and* repoints `DATABASE_URL` at
    it, which is the channel `main` resolves its connection through — the same
    one an operator sets.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _one_document_corpus(corpus)

    # `main` takes no report root — an operator's run writes the committed
    # artifacts, which is the whole of FR-071 — so the root itself is repointed
    # for the duration of this test. Without it a one-document run that happened
    # to build all twenty-one items would overwrite `ingestion-report.md` with
    # figures over one transmittal.
    monkeypatch.setattr(publish, "_REPO_ROOT", tmp_path / "checkout")

    # Set through `monkeypatch` so teardown restores them: `main` writes
    # `GATEWAY_MODE` into `os.environ` itself, and a test that left it set would
    # hand the next one a resolution mode it never chose.
    monkeypatch.setenv(MODE_ENV_VAR, REPLAY_MODE)
    monkeypatch.setenv(PRICE_TABLE_PIN_ENV_VAR, SEEDED_PRICE_TABLE_VERSION)
    monkeypatch.setenv(VCS_REVISION_ENV_VAR, "0123abc")
    # The gateway's spool is created in the working directory (ADR-0015); pointed
    # at the test's own scratch so the run leaves nothing in the entry root.
    monkeypatch.setenv(SPOOL_PATH_ENV_VAR, str(tmp_path / "spool.sqlite3"))
    # The **libpq** spelling of the same scratch database, which is what an
    # operator's environment holds (`src/model/README.md`). `empty_scratch_
    # database` exports the SQLAlchemy rendering, `postgresql+psycopg://…`, and
    # the two boundaries disagree about it: `ingest.writer._conninfo` puts the
    # scheme back before handing it to psycopg, while the gateway's record
    # writer passes `DATABASE_URL` to libpq verbatim and fails with `missing "="
    # after …`. Repointed here so this test exercises the traced path rather
    # than that disagreement — which is real, is E004's, and is reported rather
    # than worked around silently.
    monkeypatch.setenv(
        "DATABASE_URL",
        empty_scratch_database.set(drivername="postgresql").render_as_string(hide_password=False),
    )

    code = main(["--mode", REPLAY_MODE, "--corpus-root", str(corpus)])
    printed = capsys.readouterr()

    assert code == EXIT_ABORTED, printed.out + printed.err
    assert "documents=1 REAL=0 SYNTHETIC=1 extraction_attempted=1 excluded=0" in printed.out
    assert (
        "dispositions ingested=0 skipped_unchanged=0 rolled_back=1 not_reached=0 enumerated=1"
        in printed.out
    )
    assert "fixture_missing" in printed.err, "FR-045: an empty fixture store is a named abort"

    assert _count(migrated, "SELECT count(*) FROM chunk") == 0, (
        "the one document was the one in flight, so its transaction rolled back whole"
    )
    with migrated.cursor() as cursor:
        cursor.execute("SELECT run_failure_kind, finished_at, resolution_mode FROM ingestion_run")
        row = cursor.fetchone()
    assert row == ("fixture_missing", None, REPLAY_MODE)


# ---------------------------------------------------------------------------
# FR-071 — the report driver, which had no production caller at all
# ---------------------------------------------------------------------------


def test_a_complete_run_emits_the_report_and_the_results_manifest(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """`build_report` reaching a file, which is what it never did.

    `report.py` is 3,977 lines behind twenty-one section builders and
    `REPORT_PATH` was declared and never written — the module says so itself:
    "this module writes no file". `results_manifest` had zero call sites. This
    is the assertion that the whole publish layer now runs from the run that
    produces the numbers, over a two-document corpus small enough to be a test
    and real enough that every figure in it is measured.

    Twenty-one items and no placeholders: the enumeration and the exclusion, the
    chunk counts and the leaf-length distribution over what this run cut, the
    near-duplicate clusters over the vectors it committed, the printed fields
    nobody attempted, the confidence distribution, the attempt and invocation
    ledgers, precision and recall against the verified reference set beside the
    baseline's, the disposition ledger, and the three censuses built from the
    rest.

    **One document on each layer**, because FR-053 publishes every chunking
    figure per layer as well as pooled and a percentile over an empty layer is
    not published — so a transmittal-only corpus is a corpus whose report is
    correctly two items short.
    """
    connection = migrated
    transmittal = _records(1)
    records = (_specification(), *transmittal)
    outcome = _run(
        connection,
        records,
        _scope(transmittal, (_specification(),)),
        OneValueThenNothing(),
        report_root=tmp_path,
    )
    assert outcome.complete

    publication = outcome.publication
    assert publication is not None, "the run must not finish without saying what it published"
    assert publication.emitted, publication.rendered()
    assert publication.gaps == ()
    assert publication.items_published == tuple(range(1, 22))

    report = tmp_path / REPORT_PATH
    manifest = tmp_path / RESULTS_MANIFEST_PATH
    assert report.is_file() and manifest.is_file()

    rendered = report.read_text(encoding="utf-8")
    assert f"**Run**: `{outcome.run_id}`" in rendered
    for item in REPORT_CONTENTS:
        assert f"## {item.number}. {item.title}" in rendered

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["run_id"] == str(outcome.run_id)
    assert payload["figures"], "FR-074: the manifest carries every figure the report publishes"
    labels = [figure["label"] for figure in payload["figures"]]
    assert len(labels) == len(set(labels)), (
        "FR-074: the manifest is keyed on the label, so a duplicate makes a reproduction "
        "unable to say which of the two moved"
    )
    for figure in payload["figures"]:
        assert figure["tolerance"] and figure["unit"] and figure["layer"]


def test_a_partial_run_names_every_item_that_has_no_data_instead_of_emitting_nothing(
    migrated: psycopg.Connection,
    tmp_path: Path,
) -> None:
    """FR-071's refusal, published rather than silent.

    With no fixtures committed (T081) a `replay` run aborts at the first
    transmittal, so the items needing extracted values have nothing to publish
    and `build_report` refuses the incomplete content list. That refusal is
    correct. What was missing is that it reached nobody: no file appeared, and
    nothing said why. The refusal now names which items, what obliges each, why
    each has no data, and what stopped the run.

    The items that need no extracted value are still built — the assertion below
    that sixteen of the twenty-one were published is what stops the driver from
    quietly short-circuiting on the first gap and never exercising them. This is
    the same shape the full-corpus `replay` run produces: 26 specifications
    ingested, the first transmittal `rolled_back` on `fixture_missing`, and five
    items with no data.
    """
    connection = migrated
    transmittal = _records(1)
    records = (_specification(), *transmittal)
    outcome = _run(
        connection,
        records,
        _scope(transmittal, (_specification(),)),
        OneValueThenNothing(raise_on=1),
        report_root=tmp_path,
    )

    publication = outcome.publication
    assert publication is not None
    assert not publication.emitted
    assert not (tmp_path / REPORT_PATH).exists()
    assert not (tmp_path / RESULTS_MANIFEST_PATH).exists()

    missing = {gap.item for gap in publication.gaps}
    assert missing == {6, 7, 10, 12, 13}, (
        "only the items needing an extracted value are missing on a fixture-blocked run"
    )
    assert len(publication.items_published) == 16, (
        "the items needing no extracted value are assembled from real data, not skipped "
        "once the refusal is known"
    )
    rendered = publication.rendered()
    assert "report not emitted" in rendered
    assert "fixture_missing" in rendered, "the refusal names what stopped the run"
    for gap in publication.gaps:
        assert gap.obliged_by, "every named gap says what obliges the item it names"
        assert gap.reason.strip()


# ---------------------------------------------------------------------------
# FR-056 / FR-014 — the oversized sentence, recorded under its own kind
# ---------------------------------------------------------------------------


def test_an_oversized_sentence_is_recorded_as_one_of_the_closed_five(
    migrated: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-014's abort reaching `ingestion_run.run_failure_kind` (FR-042, FR-056).

    `oversized_sentence` is one of the five kinds the column admits and it was
    never written by anything. The chunker raised with the document, page and
    structural unit interpolated into prose; the orchestrator caught it as an
    unclassified preparation failure, marked the document `rolled_back`, and
    wrote no kind at all — so a classified abort read as `in_flight` forever.

    The failure is injected at `prepare_document` rather than by finding a
    document with an oversized sentence, because the committed corpus has none:
    every real specification chunks legally, which is what FR-053's measured
    profile reports. What is under test is the routing, and the routing is
    driven by the three attributes the real raise site now sets — asserted
    against that raise site in `tests/ingest/test_chunking.py`.
    """
    connection = migrated
    records = _records(1)
    failing_id = records[0].document_id

    def refuse(record: DocumentRecord, pages: object = None):
        raise ChunkerError(
            f"FR-014: {record.document_id} page 3 unit 'a-2-1' holds a single sentence",
            document_id=record.document_id,
            page_number=3,
            structural_unit="a-2-1",
        )

    monkeypatch.setattr("model.ingest.writer.prepare_document", refuse)
    outcome = _run(
        connection, records, _scope(records), OneValueThenNothing(), report_root=tmp_path
    )

    assert not outcome.complete and outcome.exit_code == EXIT_ABORTED
    assert outcome.failure is not None
    assert outcome.failure.kind == "oversized_sentence"
    assert outcome.failure.document_id == failing_id
    assert outcome.ledger.counts["rolled_back"] == 1

    assert read_run_state(connection, outcome.run_id) == "aborted", (
        "a classified abort does not read as `in_flight`"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_failure_kind, run_failure_detail FROM ingestion_run WHERE run_id = %s",
            (str(outcome.run_id),),
        )
        row = cursor.fetchone()
    assert row is not None
    kind, detail = row
    assert kind == "oversized_sentence"
    assert failing_id in detail, "FR-056 names the document in flight"
    assert "page 3" in detail and "'a-2-1'" in detail, "and the page and structural unit"
