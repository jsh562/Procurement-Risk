"""The run record, its confidence policy, and the promotion of a generation.

FR-038 / FR-055 owe the row and the generation lifecycle; FR-032 / FR-046 /
FR-057 owe the policy the row carries.

**The policy is columns rather than constants**, and the reason they are
constants. A weight left in code is whatever happens to be checked out:
recomputing a stored confidence after a weight change succeeds and simply agrees
with a policy the row was never scored under — no exception, no symptom, and a
number that is now wrong in a way nothing can see. With the floor and the three
weights on the run row, a stored score is checkable against *the policy that
produced it* rather than against today's.

**Declared before the first run, and written before the first document**
(FR-032). The four values below are this project's declaration: they were chosen
before any figure existed and are not refitted to a distribution. They are
written onto `ingestion_run` in the same `INSERT` that creates the row, because
all four columns are `NOT NULL` — there is no window in which a run exists
without its policy, which is what makes "before the first document" a structural
fact rather than a sequencing promise. The first document's write reads the
policy back off the row (`read_confidence_policy`), so a run whose row is absent
fails at its first write instead of scoring 51 documents against a policy nobody
recorded.

**The two exclusions are not restated here.** FR-057 states the floor by what it
rejects — any repaired invocation, and any value both alternate-labelled and
page-split — and revision `0300` carries that as
`ck_ingestion_run__floor_excludes_repair` and
`ck_ingestion_run__floor_excludes_alt_split`, written over the *columns* and
hard-coding none of the numbers. A second copy of those rules in Python is a
second answer that can drift from the row, and the row is the one that decides
what is storable. A policy that fails either check is refused by the database on
write, which is where the requirement put it.

**The agent identity is composed, never typed** (FR-038, T069). E003's TR-082
dropped its per-row agent column on the explicit grounds that this epic records
identity at run granularity, so this column is the project's **only** record of
who is responsible for a citation — and neither half of the answer is worth
anything alone. `AgentIdentity` builds the declared
`principal=<kind>:<id>; build=<distribution>@<version>+<revision>` grammar from
its parts, so a value naming only a person or only a build is unconstructible
rather than merely rejected. The grammar's own regex is **not** restated here:
`ck_ingestion_run__agent_id_format` is what decides storability, and a second
copy in Python could disagree with it. What this module checks is each part
against the character class the grammar admits it in, which is a check on the
inputs rather than a second opinion about the output.

**The finish is recorded only on completion** (FR-038). `finish_run` writes
`finished_at` and refuses a run that recorded a run-level failure —
`ck_ingestion_run__failed_run_unfinished` refuses it too, and the two are not
redundant: the constraint makes the row unstorable, and the refusal here names
the run and the kind rather than reporting a check violation on a column. Three
run states are therefore readable and `read_run_state` returns them: in flight
(no finish, no failure), aborted (a failure, no finish), complete (a finish, no
failure).

**Promotion is here and not in the writer, and the reason is privilege.**
`promote_generation` performs `data-model.md` §Write Order steps 0a–0g — the
mark, the identifier capture, and the leaf-up removal — inside the caller's
transaction, the same one that then writes the successor. The ingestion job
holds `DELETE` on none of the tables involved, so this runs under the
schema-owning role and is §Operator Procedures 3 ({SAD:ADR-0020}); the writer
**refuses** a resident predecessor rather than attempting a removal it has no
privilege for.

**What this module does not own.** The run-level failure columns of FR-056 are
T077's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from model.compute.confidence import DeductionWeights

__all__ = [
    "AGENT_PART_PATTERN",
    "AGENT_REVISION_PATTERN",
    "DECLARED_CONFIDENCE_FLOOR",
    "DECLARED_DEDUCTION_ALTERNATE_LABEL",
    "DECLARED_DEDUCTION_PAGE_SPLIT",
    "DECLARED_DEDUCTION_REPAIRED",
    "DECLARED_POLICY",
    "GENERATION_STATUSES",
    "PRINCIPAL_KINDS",
    "RUN_POLICY_COLUMNS",
    "RUN_STATES",
    "STATUS_ACTIVE",
    "STATUS_SUPERSEDED",
    "AgentIdentity",
    "ConfidencePolicy",
    "PromotionOutcome",
    "RunError",
    "RunIdentity",
    "active_generation",
    "finish_run",
    "promote_generation",
    "read_confidence_policy",
    "read_run_state",
    "record_confidence_policy",
    "write_run_record",
]

#: FR-032, FR-057. The floor, declared before the first run and not moved in
#: response to the distribution any run produces. Raised from the 0.70 first
#: proposed, which admitted both combinations it claimed to exclude — each
#: scoring 0.75 under the weights below (spec Clarifications, AD-008).
DECLARED_CONFIDENCE_FLOOR: Final[float] = 0.80

#: FR-057's three deductions. The printed field label matched a known alternate
#: rather than the canonical form; the value was assembled across a page break;
#: the invocation validated only after a repair. Alternates resolve against the
#: field-label vocabulary E002 committed, not a list invented here.
DECLARED_DEDUCTION_ALTERNATE_LABEL: Final[float] = 0.15
DECLARED_DEDUCTION_PAGE_SPLIT: Final[float] = 0.10
DECLARED_DEDUCTION_REPAIRED: Final[float] = 0.25

#: The four `ingestion_run` columns this module is responsible for, in the order
#: the report publishes them (FR-046). Named so a reader of the report and a
#: reader of the row are looking at the same four things.
RUN_POLICY_COLUMNS: Final[tuple[str, ...]] = (
    "confidence_floor",
    "deduction_alternate_label",
    "deduction_page_split",
    "deduction_repaired",
)


class RunError(RuntimeError):
    """The run cannot be recorded, or its policy cannot be read back.

    One type, as the rest of this package uses. Each of them means the same
    thing to a caller: this run does not proceed, because proceeding would score
    values against a policy nothing recorded.
    """


@dataclass(frozen=True)
class ConfidencePolicy:
    """One run's declared floor and its three deduction weights.

    Carried together because they are only meaningful together: FR-057 states
    the floor by the combinations the weights make it exclude, so a floor read
    without its weights says nothing about what it rejects.
    """

    floor: float
    weights: DeductionWeights

    def __post_init__(self) -> None:
        floor = float(self.floor)
        if not 0.0 <= floor <= 1.0:
            raise RunError(
                f"confidence_floor is {floor}, outside the [0.0, 1.0] that "
                f"`ck_ingestion_run__confidence_floor_range` admits"
            )
        object.__setattr__(self, "floor", floor)

    @property
    def row_values(self) -> tuple[float, float, float, float]:
        """The four column values, in `RUN_POLICY_COLUMNS` order."""
        return (
            self.floor,
            self.weights.alternate_label,
            self.weights.page_split,
            self.weights.repaired,
        )

    def admits(self, confidence: float) -> bool:
        """Whether a score is persisted with its confidence intact (FR-032).

        At or above the floor is stored; below it is recorded as a failure with
        outcome `confidence_below_threshold` rather than persisted. Inclusive at
        the floor, matching the requirement's "one at or above it is persisted".
        """
        return confidence >= self.floor


#: This project's declaration, assembled once. `DeductionWeights` validates the
#: three values against the same range the run row's checks admit, so a typo in
#: the constants above fails at import rather than at the first write.
DECLARED_POLICY: Final[ConfidencePolicy] = ConfidencePolicy(
    floor=DECLARED_CONFIDENCE_FLOOR,
    weights=DeductionWeights(
        alternate_label=DECLARED_DEDUCTION_ALTERNATE_LABEL,
        page_split=DECLARED_DEDUCTION_PAGE_SPLIT,
        repaired=DECLARED_DEDUCTION_REPAIRED,
    ),
)


#: FR-038's two principal kinds, exactly as `ck_ingestion_run__agent_id_format`
#: alternates them. A third kind is a migration and an amendment, not a new
#: label — which is why the set is written down rather than left to the caller.
PRINCIPAL_KINDS: Final[tuple[str, ...]] = ("human", "automation")

#: The character class the grammar admits for the principal identifier, the
#: distribution and the version. Checked **per part**, deliberately: the whole
#: `agent_id` regex lives on the column as
#: `ck_ingestion_run__agent_id_format` and is what decides what is storable, so
#: a second copy of it here would be a second answer that can drift. What is
#: checked here is the input to a composition whose *shape* is fixed by the
#: f-string below, which is a different thing from re-deciding the output.
AGENT_PART_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")

#: The build's VCS revision: an abbreviated or full lower-case hexadecimal
#: commit identifier. Seven is git's conventional abbreviation floor and forty
#: is a full SHA-1.
AGENT_REVISION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True)
class AgentIdentity:
    """FR-038's composite: who invoked the run, and which build executed it.

    **Composed from parts rather than accepted as a string**, which is what
    makes a half answer unconstructible. E003's TR-082 dropped its per-row agent
    column on the grounds that this epic holds identity at run granularity, so
    this is the project's only record of who is responsible for a citation; a
    presence check alone would accept `x`, and a value naming only a person
    would answer "who asked for this" while leaving "what produced it"
    unrecorded.

    **The provider model is deliberately not a member.** It has its own column,
    and a second copy inside this text would be a second answer nothing
    compares.
    """

    principal_kind: str
    principal_id: str
    distribution: str
    version: str
    vcs_revision: str

    def __post_init__(self) -> None:
        if self.principal_kind not in PRINCIPAL_KINDS:
            raise RunError(
                f"FR-038: principal kind {self.principal_kind!r} is outside "
                f"{PRINCIPAL_KINDS}, which `ck_ingestion_run__agent_id_format` alternates "
                f"between. A run is invoked by a person or by an automation and the "
                f"distinction is part of the answer."
            )
        for name in ("principal_id", "distribution", "version"):
            value = getattr(self, name)
            if not AGENT_PART_PATTERN.fullmatch(value):
                raise RunError(
                    f"FR-038: the agent identity's {name} is {value!r}, outside the "
                    f"{AGENT_PART_PATTERN.pattern} the declared grammar admits. The "
                    f"composed value would be refused by "
                    f"`ck_ingestion_run__agent_id_format` on write."
                )
        if not AGENT_REVISION_PATTERN.fullmatch(self.vcs_revision):
            raise RunError(
                f"FR-038: the build revision is {self.vcs_revision!r}, outside "
                f"{AGENT_REVISION_PATTERN.pattern}. An unabbreviated or upper-cased "
                f"revision is refused rather than normalized here — normalizing it would "
                f"record a revision nobody typed."
            )

    def __str__(self) -> str:
        """The declared grammar, assembled in one place and nowhere else."""
        return (
            f"principal={self.principal_kind}:{self.principal_id}; "
            f"build={self.distribution}@{self.version}+{self.vcs_revision}"
        )


@dataclass(frozen=True)
class RunIdentity:
    """The `ingestion_run` columns that say what this run ran with (FR-038).

    Held as a value rather than as eleven arguments so the run row is
    constructed in one place and a caller cannot supply them in the wrong order.
    Nothing here is validated beyond presence: every one of these columns
    carries its own `CHECK` on the row — the agent-identity grammar, the two
    digest formats, the resolution-mode vocabulary, the trace-identifier form —
    and a second copy of those rules here could disagree with the one that
    decides what is storable.

    `agent_id` accepts an `AgentIdentity` — the supported construction, since
    composing it from parts is what makes a half answer unconstructible — or the
    composed string itself, and stores the string. A literal is admitted rather
    than forbidden because `ck_ingestion_run__agent_id_format` is what decides
    storability either way; what the value class buys is that nothing in this
    repository has to spell the grammar out at a call site.
    """

    agent_id: AgentIdentity | str
    provider_model: str
    chunker_version: str
    embedding_model_id: str
    embedding_model_revision: str
    corpus_manifest_digests: Sequence[str]
    extraction_prompt_digest: str
    extraction_schema_digest: str
    resolution_mode: str
    run_trace_id: str

    def __post_init__(self) -> None:
        if not tuple(self.corpus_manifest_digests):
            raise RunError(
                "FR-038: a run reads at least one committed manifest. "
                "`ck_ingestion_run__corpus_manifest_digests` refuses an empty array — via "
                "`coalesce(array_length(...), 0) >= 1`, because `array_length('{}', 1)` is "
                "NULL and a CHECK accepts NULL."
            )
        object.__setattr__(self, "agent_id", str(self.agent_id))


#: Every column of the run row this module writes. The policy columns are part
#: of this `INSERT` and not a later `UPDATE` because all four are `NOT NULL`:
#: there is no representable state in which a run exists without its policy, and
#: that is what makes FR-032's "before the first document" structural.
_RUN_INSERT = """
INSERT INTO ingestion_run (
    run_id, agent_id, provider_model, chunker_version,
    embedding_model_id, embedding_model_revision, corpus_manifest_digests,
    extraction_prompt_digest, extraction_schema_digest, resolution_mode,
    run_trace_id, confidence_floor, deduction_alternate_label,
    deduction_page_split, deduction_repaired, started_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""

_RUN_POLICY_SELECT = f"SELECT {', '.join(RUN_POLICY_COLUMNS)} FROM ingestion_run WHERE run_id = %s"  # noqa: S608


def write_run_record(
    connection: object,
    *,
    run_id: UUID | str,
    identity: RunIdentity,
    policy: ConfidencePolicy = DECLARED_POLICY,
    started_at: datetime | None = None,
) -> ConfidencePolicy:
    """Write the one `ingestion_run` row for this run (FR-038, FR-032, FR-057).

    Args:
        connection: a psycopg connection. Typed as `object` for the reason
            `report.read_resident_chunks` and `cli.count_recorded_invocations`
            are — this module states the statement and the caller owns the
            connection, and a narrower annotation would make every consumer
            import psycopg to name the parameter.
        run_id: the identifier generated in the job process before the first
            write, never by a database default.
        identity: what this run ran with (FR-038).
        policy: the declared floor and weights. Defaulted to `DECLARED_POLICY`
            rather than required, because a run that declared its own would be
            a run whose policy was chosen at the call site — which is the thing
            FR-032 forbids. The parameter exists so a test can record a run
            under a *different* policy and prove the recomputation reads the
            row rather than the constant.
        started_at: the run start. Defaults to now, in UTC.

    Returns:
        The policy as written, so the caller carries the same object the row
        does rather than re-reading a constant.

    Raises:
        RunError: the insert wrote no row. Every other refusal is the
            database's — the agent-identity grammar, the digest formats, the
            two floor-exclusion checks — and each names the constraint that
            rejected it, which a Python pre-check would replace with a message
            of its own devising.

    **This runs before the first document, and the ordering is not a promise.**
    Every generation row carries `fk_ingestion_run_document__run`, so no
    document can be written under a run whose row does not exist; and the four
    policy columns are `NOT NULL`, so no run row can exist without its policy.
    The two together make the ordering unrepresentable in reverse.

    **No finish is written here, and that is FR-038's rule rather than an
    omission.** `finished_at` is recorded by `finish_run` when the run completes;
    a run that aborted carries none, and a run that recorded a run-level failure
    may not carry one at all — `ck_ingestion_run__failed_run_unfinished`. Every
    other column is present on every run record, aborted or not (SC-022), which
    is why they are all in this one `INSERT`.
    """
    started = datetime.now(UTC) if started_at is None else started_at
    parameters = (
        str(run_id),
        identity.agent_id,
        identity.provider_model,
        identity.chunker_version,
        identity.embedding_model_id,
        identity.embedding_model_revision,
        list(identity.corpus_manifest_digests),
        identity.extraction_prompt_digest,
        identity.extraction_schema_digest,
        identity.resolution_mode,
        identity.run_trace_id,
        *policy.row_values,
        started,
    )
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RUN_INSERT, parameters)
        if cursor.rowcount != 1:
            raise RunError(
                f"FR-038: recording run {run_id} wrote {cursor.rowcount} rows. The run "
                f"record is written once, before the first document, and every "
                f"association resolves to it."
            )
    return policy


def record_confidence_policy(
    connection: object,
    *,
    run_id: UUID | str,
    identity: RunIdentity,
    policy: ConfidencePolicy = DECLARED_POLICY,
    started_at: datetime | None = None,
) -> ConfidencePolicy:
    """`write_run_record` under the name FR-032 reads it by.

    The same single `INSERT`, not a second one. The four policy columns are
    `NOT NULL`, so recording the policy and writing the run record are one
    statement and cannot be separated — which is exactly what makes FR-032's
    "declared before the first document" a structural fact. Both names are kept
    because the two requirements ask different questions of the same row: FR-038
    asks what the run ran with, FR-032 asks what policy its scores were judged
    under, and a caller reading either requirement finds the function it names.
    """
    return write_run_record(
        connection, run_id=run_id, identity=identity, policy=policy, started_at=started_at
    )


#: FR-038's three readable run states. The fourth is disclosed rather than
#: claimed away: a run whose process died before writing its failure columns
#: reads as `in_flight` forever, and its recovery is the same as any abort.
RUN_STATES: Final[tuple[str, ...]] = ("in_flight", "aborted", "complete")

#: FR-038's finish, and the one `UPDATE` the application role holds `ingestion_run`
#: for. `WHERE run_failure_kind IS NULL` is not decoration: it makes the
#: statement itself refuse to finish an aborted run, so the zero-row result is
#: the refusal rather than a `CHECK` violation the caller has to interpret.
_RUN_FINISH = """
UPDATE ingestion_run
   SET finished_at = %s
 WHERE run_id = %s
   AND run_failure_kind IS NULL
"""

_RUN_STATE_SELECT = """
SELECT finished_at IS NOT NULL AS finished, run_failure_kind
  FROM ingestion_run
 WHERE run_id = %s
"""


def finish_run(
    connection: object, run_id: UUID | str, *, finished_at: datetime | None = None
) -> datetime:
    """Record the finish, and only on completion (FR-038, SC-022, SC-044).

    Args:
        connection: a psycopg connection.
        run_id: the run that completed.
        finished_at: the instant it completed. Defaults to now, in UTC.

    Returns:
        The timestamp written, so a caller reports the value the row holds
        rather than re-reading a clock that has since moved.

    Raises:
        RunError: the run has no row, or it recorded a run-level failure. A run
            that failed is **aborted**, not complete, and "the run does not
            report completion" is `ck_ingestion_run__failed_run_unfinished` as a
            database fact — this refusal names the run so the message is about
            the run rather than about a column.

    **A run that aborted carries no finish** (FR-038). That is why this is a
    separate statement from the record's `INSERT` rather than a column filled in
    at the start with an optimistic value: a finish written in advance would
    make an abort indistinguishable from a completion whose process died.
    """
    finished = datetime.now(UTC) if finished_at is None else finished_at
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RUN_FINISH, (finished, str(run_id)))
        if cursor.rowcount != 1:
            state = read_run_state(connection, run_id)
            raise RunError(
                f"FR-038: run {run_id} is {state!r} and cannot record a finish. A run that "
                f"recorded a run-level failure carries none at all — "
                f"`ck_ingestion_run__failed_run_unfinished` refuses the pair — and a run "
                f"with no row has nothing to finish."
            )
    return finished


def read_run_state(connection: object, run_id: UUID | str) -> str:
    """Which of FR-038's three states this run is in.

    Returns:
        `in_flight` (no finish, no failure kind), `aborted` (a failure kind, no
        finish), or `complete` (a finish, no failure kind). Derived from the two
        columns rather than stored, because a stored state is a third answer
        that can disagree with them.

    Raises:
        RunError: the run has no row.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RUN_STATE_SELECT, (str(run_id),))
        row = cursor.fetchone()
    if row is None:
        raise RunError(f"FR-038: run {run_id} has no `ingestion_run` row, so it has no state")
    finished, failure_kind = bool(row[0]), row[1]
    if failure_kind is not None:
        return "aborted"
    return "complete" if finished else "in_flight"


def read_confidence_policy(connection: object, run_id: UUID | str) -> ConfidencePolicy:
    """The floor and weights **this run** was scored under (FR-046, SC-026).

    Args:
        connection: a psycopg connection.
        run_id: the run whose policy is wanted.

    Returns:
        The policy as the row holds it.

    Raises:
        RunError: the run has no row. Read as a refusal rather than as a
            fallback to the declared constants, and that is the whole point of
            this function: falling back would recompute every stored score
            under today's policy and report agreement, which is exactly the
            silent disagreement the columns exist to make visible.

    Every recomputation of a stored confidence reads its weights through here.
    A test that hard-codes the declared numbers passes against a run scored
    under different ones, so SC-026's check is only worth anything if the
    weights come off the row that produced the score.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_RUN_POLICY_SELECT, (str(run_id),))
        row = cursor.fetchone()
    if row is None:
        raise RunError(
            f"FR-032: run {run_id} has no `ingestion_run` row, so the floor and weights it "
            f"was scored under cannot be read. The policy is not defaulted to the declared "
            f"constants here: a score recomputed under today's policy would agree with "
            f"itself and report nothing, which is the disagreement these columns exist to "
            f"expose."
        )
    floor, alternate, page_split, repaired = (float(value) for value in row)
    return ConfidencePolicy(
        floor=floor,
        weights=DeductionWeights(
            alternate_label=alternate, page_split=page_split, repaired=repaired
        ),
    )


# ---------------------------------------------------------------------------
# FR-055 / {SAD:ADR-0020} — generations, and the promotion that replaces one
# ---------------------------------------------------------------------------

#: `ck_ingestion_run_document__status`'s closed pair. Written down because the
#: promotion's delete statements select on `superseded`, so the value is a name
#: the code uses rather than a label only a reader sees.
STATUS_ACTIVE: Final[str] = "active"
STATUS_SUPERSEDED: Final[str] = "superseded"
GENERATION_STATUSES: Final[tuple[str, ...]] = (STATUS_ACTIVE, STATUS_SUPERSEDED)

#: §Write Order step 0a — FR-055's mark. `RETURNING run_id` is what makes this
#: statement also the *identification* of the generation steps 0b–0g act on:
#: each of them resolves from the `(run_id, document_id)` this returns, so the
#: removal has a stated target rather than one reconstructed from a second
#: query that could see a different row.
_GENERATION_SUPERSEDE = """
UPDATE ingestion_run_document
   SET status = 'superseded'
 WHERE document_id = %s AND status = 'active'
RETURNING run_id
"""

_ACTIVE_GENERATION_SELECT = """
SELECT run_id FROM ingestion_run_document
 WHERE document_id = %s AND status = 'active'
"""

#: §Write Order step 0b — the identifier sets, read from the three run-output
#: associations **while they still exist**. The associations are the only thing
#: that says which of E003's rows belong to this generation, and the leaf-up
#: order deletes them at step 0d, before the rows they identify. Capture first
#: or the generation becomes unnameable mid-delete.
_CAPTURE: Final[tuple[tuple[str, str], ...]] = (
    (
        "chunk_ids",
        "SELECT chunk_id FROM ingestion_run_chunk WHERE run_id = %s AND document_id = %s",
    ),
    (
        "value_ids",
        "SELECT extracted_value_id FROM ingestion_run_extracted_value "
        "WHERE run_id = %s AND document_id = %s",
    ),
    (
        "failure_ids",
        "SELECT extraction_failure_id FROM ingestion_run_extraction_failure "
        "WHERE run_id = %s AND document_id = %s",
    ),
)

#: §Write Order step 0c — the two deepest leaves. Both are keyed on
#: `(run_id, document_id)` directly and need no set from step 0b.
_DELETE_BY_GENERATION: Final[tuple[tuple[str, str], ...]] = (
    (
        "line_items",
        "DELETE FROM extracted_value_line_item WHERE run_id = %s AND document_id = %s",
    ),
    (
        "parse_signals",
        "DELETE FROM extracted_value_parse_signal WHERE run_id = %s AND document_id = %s",
    ),
    # 0d — the run-output associations, once their own children are gone. After
    # this the generation is unresolvable from the database, which is why 0b ran.
    (
        "value_associations",
        "DELETE FROM ingestion_run_extracted_value WHERE run_id = %s AND document_id = %s",
    ),
    (
        "failure_associations",
        "DELETE FROM ingestion_run_extraction_failure WHERE run_id = %s AND document_id = %s",
    ),
    (
        "chunk_associations",
        "DELETE FROM ingestion_run_chunk WHERE run_id = %s AND document_id = %s",
    ),
)

#: §Write Order step 0e and 0f — E003's own rows, by the sets captured at 0b.
#:
#: `extracted_value_contributing_chunk` is spelled out even though E003 declares
#: `fk_evcc__value_count ON DELETE CASCADE`: a step that relies on a cascade in a
#: table this epic does not own is a step that changes meaning if that table
#: does. The explicit delete is a no-op when the cascade would have run anyway.
#:
#: `chunk` is deleted **by the captured set** rather than by `document_id = %s`.
#: The two are equal only while the one-resident-generation invariant holds, and
#: a step that assumes the invariant it is enforcing cannot detect its breach.
_DELETE_BY_IDS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "failures",
        "failure_ids",
        "DELETE FROM extraction_failure WHERE extraction_failure_id = ANY(%s::uuid[])",
    ),
    (
        "contributing_chunks",
        "value_ids",
        "DELETE FROM extracted_value_contributing_chunk WHERE extracted_value_id = ANY(%s::uuid[])",
    ),
    (
        "values",
        "value_ids",
        "DELETE FROM extracted_value WHERE extracted_value_id = ANY(%s::uuid[])",
    ),
    ("chunks", "chunk_ids", "DELETE FROM chunk WHERE chunk_id = ANY(%s::uuid[])"),
)

#: §Write Order step 0g — last of the removal. Releases
#: `ix_ingestion_run_document__single_active` for this document, which is what
#: lets step 0h insert the successor as active.
_DELETE_GENERATION = "DELETE FROM ingestion_run_document WHERE run_id = %s AND document_id = %s"


@dataclass(frozen=True)
class PromotionOutcome:
    """What a promotion removed, per table, and which generation it replaced.

    Counts rather than a boolean because {SAD:ADR-0020}'s cost is exactly what
    they measure: the predecessor's rows are **gone**, not retired, and a
    promotion that silently removed nothing is indistinguishable from a first
    ingest unless the numbers are reported. `superseded_run_id` is `None` on a
    first ingest and is the whole difference between the two.
    """

    document_id: str
    superseded_run_id: UUID | None
    removed: Mapping[str, int]

    @property
    def replaced(self) -> bool:
        """Whether a predecessor generation was found, marked and removed."""
        return self.superseded_run_id is not None

    @property
    def rows_removed(self) -> int:
        return sum(self.removed.values())


def active_generation(connection: object, document_id: str) -> UUID | None:
    """The run whose generation of `document_id` is resident, or `None`.

    `None` is legal and meaningful: it says this document has not been ingested
    under the current inputs, and a consumer must be able to tell that apart
    from "ingested under them". It is also the state a document occupies between
    the removal and the write inside a promotion transaction — invisible outside
    it, because both are in the same transaction.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(_ACTIVE_GENERATION_SELECT, (document_id,))
        row = cursor.fetchone()
    return None if row is None else row[0]


def promote_generation(connection: object, document_id: str) -> PromotionOutcome:
    """Mark and remove the resident generation, leaf-up (FR-055, {SAD:ADR-0020}).

    Args:
        connection: a psycopg connection **already inside the transaction that
            will write the successor**. This function opens none of its own, and
            that is the requirement rather than a convenience: a crash at any
            point must roll back to the old generation intact and active, which
            only holds while the removal and the replacing write share one
            transaction.
        document_id: the document being re-ingested.

    Returns:
        What was removed. On a first ingest, `superseded_run_id is None` and
        every count is zero — the whole 0a–0g block is skipped when step 0a
        affects no row.

    Raises:
        RunError: step 0a marked more than one generation. Unreachable while
            `ix_ingestion_run_document__single_active` holds, and checked anyway:
            the index is what makes it unreachable, and a step that assumes the
            invariant it depends on cannot report its breach.

    **The privilege this needs is not the ingestion job's.** `procurement_app`
    holds `DELETE` on none of the tables below and no `UPDATE` on
    `ingestion_run_document`, so this executes under the **schema-owning role**
    and is `data-model.md` §Operator Procedures 3. A run of first ingests and
    skips alone never reaches this function and runs unattended; a run that
    replaces any existing generation does not. First ingestion and re-ingestion
    are not the same operation and this is the line between them.

    **Removal precedes the write, and is not merely convenient there.**
    `CREATE UNIQUE INDEX … WHERE` produces an index rather than a constraint and
    PostgreSQL admits `DEFERRABLE` only on constraints, so no setting rescues the
    reverse order. Deleting *after* writing would additionally put both
    generations' ordinal 0 in `chunk` for the length of a statement, which is the
    collision {SAD:ADR-0020} exists to avoid — `uq_chunk__document_ordinal` is
    scoped to the document, not to the generation.

    **The identifier sets are materialised before any association is deleted.**
    The associations are the only rows that say which of E003's rows belong to
    this generation, and the leaf-up order removes them first; identify as you go
    and the join that would have found the `extracted_value` rows no longer
    exists. That is step 0b, and it is the step most easily lost when the
    procedure is read as an ordering only.
    """
    removed: dict[str, int] = {}
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        # 0a — the mark, which is also the identification.
        cursor.execute(_GENERATION_SUPERSEDE, (document_id,))
        marked = cursor.fetchall()
        if not marked:
            return PromotionOutcome(document_id=document_id, superseded_run_id=None, removed={})
        if len(marked) > 1:
            raise RunError(
                f"FR-055: {document_id} had {len(marked)} active generations, which "
                f"`ix_ingestion_run_document__single_active` makes unrepresentable. The "
                f"index is what stops this, and this check is what reports it if the index "
                f"ever stopped stopping it."
            )
        run_id = marked[0][0]
        generation = (str(run_id), document_id)

        # 0b — capture, before anything is deleted.
        captured: dict[str, list[object]] = {}
        for name, statement in _CAPTURE:
            cursor.execute(statement, generation)
            captured[name] = [row[0] for row in cursor.fetchall()]

        # 0c, 0d — leaf-up, by generation key.
        for name, statement in _DELETE_BY_GENERATION:
            cursor.execute(statement, generation)
            removed[name] = cursor.rowcount

        # 0e, 0f — E003's rows, by the captured sets.
        for name, source, statement in _DELETE_BY_IDS:
            cursor.execute(statement, (captured[source],))
            removed[name] = cursor.rowcount

        # 0g — the generation row itself.
        cursor.execute(_DELETE_GENERATION, generation)
        removed["generation"] = cursor.rowcount

    return PromotionOutcome(document_id=document_id, superseded_run_id=run_id, removed=removed)
