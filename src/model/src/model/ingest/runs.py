"""The run record's confidence policy: the declared floor and the three weights.

FR-032 / FR-046 / FR-057, and the reason they are **columns** rather than
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

**What this module does not own.** `write_run_record`'s remaining obligations —
the composite principal-and-build agent identity as a constructed value, the
finish timestamp recorded only on completion, and the run-level failure columns
of FR-056 — are T069's and T077's. This module owns the policy and the row that
carries it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from model.compute.confidence import DeductionWeights

__all__ = [
    "DECLARED_CONFIDENCE_FLOOR",
    "DECLARED_DEDUCTION_ALTERNATE_LABEL",
    "DECLARED_DEDUCTION_PAGE_SPLIT",
    "DECLARED_DEDUCTION_REPAIRED",
    "DECLARED_POLICY",
    "RUN_POLICY_COLUMNS",
    "ConfidencePolicy",
    "RunError",
    "RunIdentity",
    "read_confidence_policy",
    "record_confidence_policy",
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
    """

    agent_id: str
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


def record_confidence_policy(
    connection: object,
    *,
    run_id: UUID | str,
    identity: RunIdentity,
    policy: ConfidencePolicy = DECLARED_POLICY,
    started_at: datetime | None = None,
) -> ConfidencePolicy:
    """Write the run row carrying the declared floor and weights (FR-032, FR-057).

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
