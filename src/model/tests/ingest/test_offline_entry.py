"""US6's decisions that need no database: the mode, the tuple, and the ledger.

Four subjects, all of them pure, and each is the half of a US6 requirement that
a schema test cannot reach:

* **FR-045 / T079** — the console entry selects `record` or `replay` explicitly,
  and refuses `record` without the provider opt-in. The literals `model.ingest`
  has to spell out for itself are checked against `gateway.config`, which is the
  module that owns them and the module `model.ingest` may not import.
* **FR-043 / T076** — the per-document input tuple, its digest, and the skip
  decision taken against the **recorded** digest rather than against the file.
* **FR-056 / T077** — the closed five as a value, each constructed through a
  function that requires the subject that kind is obliged to name.
* **FR-073 / T086** — the four-way disposition ledger, whose sum is asserted at
  construction rather than printed for a reader to add up.

The database halves live in `src/model/tests/schema/`: `test_failure_domains.py`
for the two `CHECK` domains and `test_privileges.py` for the thirteen refusals.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from gateway.config import (
    MODE_ENV_VAR,
    PRICE_TABLE_PIN_ENV_VAR,
    PROVIDER_OPT_IN_ENV_VAR,
    PROVIDER_OPT_IN_PERMITTED_VALUE,
    RECORD_MODE,
    REPLAY_MODE,
)
from gateway.provider import DEFAULT_MODEL

from model.ingest.cli import (
    DISPOSITIONS,
    EXIT_ABORTED,
    EXIT_OK,
    EXIT_REFUSED,
    OrchestrationError,
    RunLevelFailure,
    RunOutcome,
    build_disposition_ledger,
    build_revision,
    build_run_identity,
    corpus_digest_mismatch,
    document_id_collision,
    fixture_missing,
    main,
    oversized_sentence,
    provider_unreachable,
    require_price_table_pin,
    resolve_resolution_mode,
)
from model.ingest.cli import (
    PRICE_TABLE_PIN_ENV_VAR as INGEST_PRICE_PIN_VAR,
)
from model.ingest.cli import (
    PROVIDER_MODEL as INGEST_PROVIDER_MODEL,
)
from model.ingest.cli import (
    PROVIDER_OPT_IN_ENV_VAR as INGEST_OPT_IN_VAR,
)
from model.ingest.cli import (
    PROVIDER_OPT_IN_PERMITTED_VALUE as INGEST_OPT_IN_VALUE,
)
from model.ingest.cli import (
    RESOLUTION_MODE_ENV_VAR as INGEST_MODE_VAR,
)
from model.ingest.cli import (
    RESOLUTION_MODES as INGEST_MODES,
)
from model.ingest.documents import DocumentRecord
from model.ingest.runs import (
    INPUT_TUPLE_MEMBERS,
    RUN_FAILURE_KINDS,
    DocumentPlan,
    InputTuple,
    RunError,
    RunIdentity,
    input_tuple_for,
)
from model.ingest.writer import DocumentOutcome
from model.llm.prompts import prompt_template_digest
from model.llm.schemas import TRANSMITTAL_FIELD_SUBSET, output_schema_digest

TUPLE = InputTuple(
    content_hash=f"sha256:{'a' * 64}",
    chunker_version="e006-chunker/1+pysbd-0.3.4",
    embedding_model_id="sentence-transformers/all-MiniLM-L6-v2",
    embedding_model_revision="e4ce9877abf3edfe10b0d82785e83bdcb973e22e",
    provider_model="claude-opus-5",
    extraction_prompt_digest=f"sha256:{'b' * 64}",
    extraction_schema_digest=f"sha256:{'c' * 64}",
)


# ---------------------------------------------------------------------------
# FR-045 / T079 — the two modes, and the literals the ingest package restates
# ---------------------------------------------------------------------------


def test_the_restated_gateway_controls_match_the_module_that_owns_them() -> None:
    """The second copy is checked rather than trusted.

    `model.ingest` may not import `gateway` — only `model.llm` may (FR-023,
    AD-001) — so the job that has to *select* a resolution mode cannot read the
    variable's name from the module that publishes it, and spells it out. A test
    may import what a source module may not, which is what closes the gap: a
    renamed control fails here rather than in a run that silently reaches the
    wrong mode.
    """
    assert INGEST_MODE_VAR == MODE_ENV_VAR
    assert INGEST_OPT_IN_VAR == PROVIDER_OPT_IN_ENV_VAR
    assert INGEST_OPT_IN_VALUE == PROVIDER_OPT_IN_PERMITTED_VALUE
    assert set(INGEST_MODES) == {RECORD_MODE, REPLAY_MODE}
    assert INGEST_PRICE_PIN_VAR == PRICE_TABLE_PIN_ENV_VAR
    assert INGEST_PROVIDER_MODEL == DEFAULT_MODEL, (
        "the job names the model it addresses on every invocation and records it on the run; "
        "a literal that had drifted from the gateway's own would record one model while "
        "addressing another"
    )


def test_replay_needs_no_opt_in_and_is_written_into_the_environment() -> None:
    """FR-045: replay resolves from committed fixtures and reaches no network."""
    env: dict[str, str] = {}
    assert resolve_resolution_mode(REPLAY_MODE, env) == REPLAY_MODE
    assert env[MODE_ENV_VAR] == REPLAY_MODE
    assert PROVIDER_OPT_IN_ENV_VAR not in env


def test_record_is_refused_without_the_opt_in() -> None:
    """TR-027 / TR-063: two independent decisions for one network call.

    Selecting `record` is a configuration slip; selecting it *and* setting the
    opt-in is a choice. The refusal is here rather than left to the gateway so
    the message is about the run — by the time the first invocation leaves, the
    job has already enumerated 51 documents.
    """
    env: dict[str, str] = {}
    with pytest.raises(OrchestrationError, match="FR-045"):
        resolve_resolution_mode(RECORD_MODE, env)
    assert MODE_ENV_VAR not in env, "a refused mode is not written into the environment"


def test_record_is_permitted_only_at_the_opt_in_s_exact_value() -> None:
    """The opt-in has a fixed form; a truthy-looking value is not it."""
    for value in ("0", "true", "yes", "", "1 "):
        env = {PROVIDER_OPT_IN_ENV_VAR: value}
        with pytest.raises(OrchestrationError, match="FR-045"):
            resolve_resolution_mode(RECORD_MODE, env)
    env = {PROVIDER_OPT_IN_ENV_VAR: PROVIDER_OPT_IN_PERMITTED_VALUE}
    assert resolve_resolution_mode(RECORD_MODE, env) == RECORD_MODE
    assert env[MODE_ENV_VAR] == RECORD_MODE


def test_a_mode_outside_the_two_is_refused() -> None:
    """No default and no third value (TR-021)."""
    env: dict[str, str] = {}
    with pytest.raises(OrchestrationError, match="FR-045"):
        resolve_resolution_mode("dry-run", env)


# ---------------------------------------------------------------------------
# TR-048 / T097 — the price pin, refused before the corpus is enumerated
# ---------------------------------------------------------------------------


def test_a_run_with_no_price_pin_is_refused() -> None:
    """The refusal that costs nothing, in place of the one that costs a corpus.

    The gateway asks the same question, and asks it on the first invocation —
    which this job reaches only after committing every document extraction does
    not reach. Measured on the committed corpus that was 26 documents and 6,391
    chunks written before a missing environment variable was reported, and
    reported as `provider_unreachable` rather than as a configuration nobody set.
    """
    for value in ({}, {PRICE_TABLE_PIN_ENV_VAR: "   "}):
        with pytest.raises(OrchestrationError, match="TR-048"):
            require_price_table_pin(value)


def test_a_configured_price_pin_is_returned_unvalidated() -> None:
    """Whether it *resolves* is the gateway's question, asked on its own connection."""
    assert require_price_table_pin({PRICE_TABLE_PIN_ENV_VAR: " 2026-07-26-published "}) == (
        "2026-07-26-published"
    )


# ---------------------------------------------------------------------------
# FR-038 / T097 — the build revision and the assembled run identity
# ---------------------------------------------------------------------------


def test_the_declared_revision_is_taken_before_git_is_consulted() -> None:
    """A run outside a checkout states its revision rather than losing it."""
    assert build_revision({"INGEST_VCS_REVISION": " 0123abc "}) == "0123abc"


def test_the_revision_falls_back_to_the_checkout() -> None:
    """With nothing declared, the commit under test is what the run records."""
    revision = build_revision({})
    assert 7 <= len(revision) <= 40
    assert set(revision) <= set("0123456789abcdef")


def test_the_run_identity_is_derived_from_committed_things_alone() -> None:
    """FR-038: nothing at the call site could have written any of these."""
    identity = build_run_identity(
        mode=REPLAY_MODE,
        trace_id="a" * 32,
        fields=TRANSMITTAL_FIELD_SUBSET,
        manifest_digests=(f"sha256:{'f' * 64}",),
        env={"INGEST_VCS_REVISION": "0123abc"},
    )
    assert identity.resolution_mode == REPLAY_MODE
    assert identity.run_trace_id == "a" * 32
    assert identity.provider_model == DEFAULT_MODEL
    assert identity.extraction_prompt_digest == prompt_template_digest(TRANSMITTAL_FIELD_SUBSET)
    assert identity.extraction_schema_digest == output_schema_digest()
    assert str(identity.agent_id).endswith("+0123abc")
    assert "principal=automation:ingest" in str(identity.agent_id)


def test_a_narrowed_vocabulary_moves_the_prompt_digest() -> None:
    """FR-043's tuple has to move when every resolved prompt does.

    The identity is assembled from the terms the run will *attempt*, after the
    run-time retirement filter — an identity built from the committed
    declaration would sit still while every request changed.
    """
    wide = build_run_identity(
        mode=REPLAY_MODE,
        trace_id="a" * 32,
        fields=TRANSMITTAL_FIELD_SUBSET,
        manifest_digests=(f"sha256:{'f' * 64}",),
        env={"INGEST_VCS_REVISION": "0123abc"},
    )
    narrow = build_run_identity(
        mode=REPLAY_MODE,
        trace_id="a" * 32,
        fields=TRANSMITTAL_FIELD_SUBSET[:-1],
        manifest_digests=(f"sha256:{'f' * 64}",),
        env={"INGEST_VCS_REVISION": "0123abc"},
    )
    assert wide.extraction_prompt_digest != narrow.extraction_prompt_digest


# ---------------------------------------------------------------------------
# T097 — the three exit codes an operator reads from a runbook
# ---------------------------------------------------------------------------


def test_the_exit_codes_are_three_distinct_values_and_exclude_one() -> None:
    """0, 2 and 3. Never 1, which is what an unhandled traceback exits with."""
    assert {EXIT_OK, EXIT_REFUSED, EXIT_ABORTED} == {0, 2, 3}
    assert 1 not in {EXIT_OK, EXIT_REFUSED, EXIT_ABORTED}


def _ledger() -> object:
    return build_disposition_ledger(
        enumerated=["a"],
        plans=[DocumentPlan(document_id="a", digest=TUPLE.digest, recorded_digest=None)],
        outcomes=[_outcome("a")],
    )


def test_a_run_that_resolved_exits_zero_and_one_that_aborted_exits_three() -> None:
    """The distinction a runbook acts on: retry a refusal, resume a partial run."""
    resolved = RunOutcome(run_id=uuid4(), ledger=_ledger())
    assert resolved.complete and resolved.exit_code == EXIT_OK

    recorded = RunOutcome(
        run_id=uuid4(),
        ledger=_ledger(),
        failure=fixture_missing(resolution_key=f"sha256:{'c' * 64}"),
        detail="WriterError: FR-056",
    )
    assert not recorded.complete and recorded.exit_code == EXIT_ABORTED

    unrecorded = RunOutcome(run_id=uuid4(), ledger=_ledger(), detail="ChunkerError: FR-014")
    assert not unrecorded.complete and unrecorded.exit_code == EXIT_ABORTED
    assert unrecorded.failure is None, (
        "an abort outside FR-056's closed five is recorded nowhere, which is why the two "
        "fields are separate rather than one nullable string"
    )


def test_the_entry_refuses_before_it_reads_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each refusal is an exit code rather than a traceback (FR-044)."""
    monkeypatch.delenv(PROVIDER_OPT_IN_ENV_VAR, raising=False)
    monkeypatch.delenv(PRICE_TABLE_PIN_ENV_VAR, raising=False)
    assert main(["--mode", RECORD_MODE]) == EXIT_REFUSED, "record without its opt-in"
    assert main(["--mode", REPLAY_MODE]) == EXIT_REFUSED, "replay with no price pin"

    monkeypatch.setenv(PRICE_TABLE_PIN_ENV_VAR, "2026-07-26-published")
    assert main(["--mode", REPLAY_MODE, "--corpus-root", str(tmp_path)]) == EXIT_REFUSED, (
        "a corpus root holding no manifest is refused before the first transaction"
    )


# ---------------------------------------------------------------------------
# FR-043 / T076 — the per-document input tuple
# ---------------------------------------------------------------------------


def test_the_digest_is_stable_and_takes_the_form_the_column_admits() -> None:
    """`ck_ingestion_run_document__tuple_digest_format`, and determinism."""
    assert TUPLE.digest == TUPLE.digest
    assert TUPLE.digest.startswith("sha256:")
    assert len(TUPLE.digest) == len("sha256:") + 64


@pytest.mark.parametrize("member", INPUT_TUPLE_MEMBERS)
def test_every_member_moves_the_digest(member: str) -> None:
    """All seven, one case each — the provider model included.

    The provider model is the member most easily left out, and leaving it out is
    not a smaller tuple but a wrong one: an unchanged document would be skipped
    under a model whose fixtures differ, and its stored values would be
    attributed to a model that never produced them.
    """
    changed = InputTuple(**{**TUPLE.__dict__, member: "moved"})
    assert changed.digest != TUPLE.digest, f"the digest ignores {member!r}"


def test_the_member_names_are_digested_beside_their_values() -> None:
    """Two members swapping values must not digest the same.

    A digest over the values alone treats a chunker version of `x` with a
    provider model of `y` as identical to the pair reversed — improbable, and
    free to exclude.
    """
    swapped = InputTuple(
        **{
            **TUPLE.__dict__,
            "chunker_version": TUPLE.provider_model,
            "provider_model": TUPLE.chunker_version,
        }
    )
    assert swapped.digest != TUPLE.digest


def test_a_blank_member_is_refused() -> None:
    with pytest.raises(RunError, match="FR-043"):
        InputTuple(**{**TUPLE.__dict__, "provider_model": "  "})


def test_the_tuple_is_built_from_the_document_and_the_run_s_own_identity() -> None:
    """One member is the document's; six are the run's (FR-043).

    Taking the six from `RunIdentity` rather than from arguments is what keeps
    the digest a run computes and the columns its run record carries from
    disagreeing.
    """
    record = DocumentRecord(
        document_id="prj-001-t0001-r0",
        document_type="transmittal",
        project_id="PRJ-001",
        title="Transmittal",
        source_kind="SYNTHETIC",
        license_basis="synthetic",
        content_hash=f"sha256:{'a' * 64}",
        path=Path("data/corpus/synthetic/prj-001-t0001-r0.pdf"),
        generator_id="model.corpus.generate",
        generation_seed="1",
        generated_at=date(2026, 7, 28),
        fixture_hashes=(f"sha256:{'d' * 64}",),
        roster_hash=f"sha256:{'e' * 64}",
    )
    identity = RunIdentity(
        agent_id="principal=automation:t076; build=model@0.1.0+0123abc",
        provider_model=TUPLE.provider_model,
        chunker_version=TUPLE.chunker_version,
        embedding_model_id=TUPLE.embedding_model_id,
        embedding_model_revision=TUPLE.embedding_model_revision,
        corpus_manifest_digests=[f"sha256:{'f' * 64}"],
        extraction_prompt_digest=TUPLE.extraction_prompt_digest,
        extraction_schema_digest=TUPLE.extraction_schema_digest,
        resolution_mode="replay",
        run_trace_id="a" * 32,
    )
    assert input_tuple_for(record, identity).digest == TUPLE.digest


def test_unchanged_is_a_property_of_the_recorded_digest_not_of_the_file() -> None:
    """FR-043's skip condition, and the mistake it is written to exclude.

    A document whose bytes are untouched under a new chunker version is a
    document that must be reloaded. The plan compares the computed digest with
    the one recorded on the document's active generation, so a run at a new
    chunker version reloads everything even though every file is byte-identical.
    """
    skipped = DocumentPlan(document_id="d", digest=TUPLE.digest, recorded_digest=TUPLE.digest)
    assert skipped.unchanged and not skipped.reloads and not skipped.promotes

    moved = InputTuple(**{**TUPLE.__dict__, "chunker_version": "e006-chunker/2"})
    reload = DocumentPlan(document_id="d", digest=moved.digest, recorded_digest=TUPLE.digest)
    assert reload.reloads and reload.resident and reload.promotes, (
        "a resident document whose tuple moved is a promotion, which needs the schema-owning "
        "role for the whole run"
    )

    first = DocumentPlan(document_id="d", digest=TUPLE.digest, recorded_digest=None)
    assert first.reloads and not first.resident and not first.promotes, (
        "a first ingest replaces nothing, so it runs unattended under the application role"
    )


# ---------------------------------------------------------------------------
# FR-056 / T077 — the closed five, each with the subject it must name
# ---------------------------------------------------------------------------


def test_each_kind_is_built_with_the_subject_its_requirement_names() -> None:
    """FR-056's required diagnostic content, one constructor per kind."""
    built = [
        corpus_digest_mismatch(
            document_id="prj-001-t0001-r0",
            path="data/corpus/synthetic/prj-001-t0001-r0.pdf",
            recorded=f"sha256:{'a' * 64}",
            observed=f"sha256:{'b' * 64}",
        ),
        document_id_collision(identifier="ufgs-23-52-00", paths=["a.pdf", "b.pdf"]),
        oversized_sentence(document_id="ufgs-23-52-00", page_number=4, structural_unit="2.4.7"),
        fixture_missing(resolution_key=f"sha256:{'c' * 64}"),
        provider_unreachable(provider="gateway", model="claude-opus-5"),
    ]
    assert {failure.kind for failure in built} == set(RUN_FAILURE_KINDS)
    for failure in built:
        assert failure.detail.strip()


def test_the_document_in_flight_is_named_where_one_exists() -> None:
    """FR-056: the detail names the document in flight wherever there is one."""
    failure = oversized_sentence(
        document_id="ufgs-23-52-00", page_number=4, structural_unit="2.4.7"
    )
    assert "ufgs-23-52-00" in failure.recorded_detail

    corpus_wide = document_id_collision(identifier="x-y", paths=["a.pdf", "b.pdf"])
    assert corpus_wide.document_id is None, (
        "the collision check is corpus-wide and precedes the first transaction, so no "
        "document is in flight"
    )


def test_a_collision_naming_one_file_is_refused() -> None:
    """One file plus an identifier does not say what it collided with."""
    with pytest.raises(OrchestrationError, match="FR-056"):
        document_id_collision(identifier="x-y", paths=["a.pdf"])


def test_a_per_field_outcome_cannot_be_recorded_as_a_run_level_kind() -> None:
    """The five and the seven share zero values, enforced at construction."""
    with pytest.raises(OrchestrationError, match="FR-056"):
        RunLevelFailure(kind="schema_violation", detail="the model returned nothing valid")


def test_a_failure_with_no_detail_is_refused() -> None:
    with pytest.raises(OrchestrationError, match="FR-056"):
        RunLevelFailure(kind="provider_unreachable", detail="   ")


# ---------------------------------------------------------------------------
# FR-073 / T086 — the four-way ledger that has to sum
# ---------------------------------------------------------------------------


def _outcome(document_id: str, *, error: str | None = None) -> DocumentOutcome:
    return DocumentOutcome(
        document_id=document_id,
        chunks_written=0 if error else 3,
        containment=None,
        error=error,
    )


def test_the_ledger_partitions_the_enumerated_corpus() -> None:
    """Every enumerated document under exactly one disposition, and the sum."""
    enumerated = ["a", "b", "c", "d", "e"]
    plans = [
        DocumentPlan(document_id="a", digest=TUPLE.digest, recorded_digest=TUPLE.digest),
        DocumentPlan(document_id="b", digest=TUPLE.digest, recorded_digest=None),
        DocumentPlan(document_id="c", digest=TUPLE.digest, recorded_digest=None),
        DocumentPlan(document_id="d", digest=TUPLE.digest, recorded_digest=None),
        DocumentPlan(document_id="e", digest=TUPLE.digest, recorded_digest=None),
    ]
    outcomes = [_outcome("b"), _outcome("c", error="WriterError: containment miss")]
    ledger = build_disposition_ledger(enumerated=enumerated, plans=plans, outcomes=outcomes)

    assert ledger.counts == {
        "ingested": 1,
        "skipped_unchanged": 1,
        "rolled_back": 1,
        "not_reached": 2,
    }
    assert sum(ledger.counts.values()) == ledger.population == len(enumerated)
    assert set(ledger.counts) == set(DISPOSITIONS), "all four are published, zeros included"
    assert ledger.not_reached == ("d", "e"), (
        "not_reached is what remains after the other three are assigned; the loop stops at "
        "the abort and never begins them"
    )


def test_a_disposition_holding_none_is_published_as_a_zero() -> None:
    """An omitted row and a zero row read the same, and only one is a measurement."""
    enumerated = ["a", "b"]
    plans = [
        DocumentPlan(document_id="a", digest=TUPLE.digest, recorded_digest=None),
        DocumentPlan(document_id="b", digest=TUPLE.digest, recorded_digest=None),
    ]
    ledger = build_disposition_ledger(
        enumerated=enumerated, plans=plans, outcomes=[_outcome("a"), _outcome("b")]
    )
    assert ledger.counts["rolled_back"] == 0
    assert ledger.counts["skipped_unchanged"] == 0
    assert set(ledger.counts) == set(DISPOSITIONS)


def test_a_document_under_two_dispositions_is_refused() -> None:
    """The defect four summing counts would hide.

    A document both skipped and ingested is counted twice; the four counts can
    still add up to the enumerated total if another document is missing, which
    is exactly how a lost document stays invisible.
    """
    with pytest.raises(OrchestrationError, match="FR-073"):
        build_disposition_ledger(
            enumerated=["a", "b"],
            plans=[
                DocumentPlan(document_id="a", digest=TUPLE.digest, recorded_digest=TUPLE.digest),
                DocumentPlan(document_id="b", digest=TUPLE.digest, recorded_digest=None),
            ],
            outcomes=[_outcome("a"), _outcome("b")],
        )


def test_a_disposition_for_a_document_outside_the_corpus_is_refused() -> None:
    """A surplus inflates the sum to match and hides the enumerated document missing."""
    with pytest.raises(OrchestrationError, match="FR-073"):
        build_disposition_ledger(
            enumerated=["a"],
            plans=[DocumentPlan(document_id="a", digest=TUPLE.digest, recorded_digest=None)],
            outcomes=[_outcome("a"), _outcome("z")],
        )


def test_an_empty_enumeration_is_refused() -> None:
    """FR-068 reaching the ledger: four zeros summing to zero balance for no reason."""
    with pytest.raises(OrchestrationError, match="FR-073"):
        build_disposition_ledger(enumerated=[], plans=[], outcomes=[])
