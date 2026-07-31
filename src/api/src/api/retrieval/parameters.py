"""The ranking parameters in force, published with every result set.

Spec FR-004 and FR-029. Three of these are **pre-registered** rather than
chosen: the fusion constant is fixed at 60 by `specs/sad.md`'s sequence
diagram, so Principle VI's "fix the parameters before you tune" is discharged
against a registered document rather than against a promise.

Every value here is a **lowercase identifier token**, not prose, and that is
load-bearing. `chunk_id ascending` and `ascending by chunk_id` name one rule and
compare unequal, so a re-wording would be indistinguishable from a re-tuning to
anything comparing two runs. The contract constrains both to
`^[a-z][a-z0-9_]*$` for the same reason.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FUSION_CONSTANT",
    "MISSING_ARM_CONVENTION",
    "TIE_BREAK_KEY",
    "RankingParameters",
]

#: Reciprocal rank fusion's `k`. Fixed at 60 by `specs/sad.md` before any
#: measurement, which is what makes it pre-registered rather than tuned.
#:
#: Worth knowing when reading a fused ordering: at a fetch depth of 50 this
#: constant makes fusion nearly uniform. Rank 1 contributes 1/61 and rank 50
#: contributes 1/110 — a ratio of 1.8 — so the fused ordering is weak *by
#: construction* and the reranker is what carries ranking quality. No criterion
#: may rest on fusion-only ordering being good, and FR-036 labels fusion-only
#: the weak comparator for exactly this reason.
FUSION_CONSTANT: Final = 60

#: The total order applied inside *each arm's* CTE as well as to the final
#: ordering. Per-arm is the half that is easy to miss: a row limit needs an
#: ordering that constrains rows into a unique order, so without a tie-break
#: inside each arm a tie at the fiftieth position changes the candidate *set*
#: between runs, not merely its order — and the reranker then scores different
#: rows each time.
TIE_BREAK_KEY: Final = "chunk_id_ascending"

#: What a candidate scores on an arm that did not return it. Zero, which is the
#: reciprocal-rank formula's own convention for an absent rank rather than a
#: choice made here.
MISSING_ARM_CONVENTION: Final = "absent_scores_zero"


class RankingParameters(BaseModel):
    """The parameters a result set was produced under.

    Emitted with **every** response rather than only with results an evaluation
    consumes, because whether a result will be consumed by an evaluation is not
    knowable at the moment it is produced.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fusion_constant: int = Field(
        default=FUSION_CONSTANT,
        gt=0,
        description="Reciprocal rank fusion's k, pre-registered at 60 by specs/sad.md.",
    )
    tie_break_key: str = Field(
        default=TIE_BREAK_KEY,
        pattern=r"^[a-z][a-z0-9_]*$",
        description=(
            "The total order, applied per arm and to the fused result. An "
            "identifier token so two runs compare mechanically."
        ),
    )
    missing_arm_convention: str = Field(
        default=MISSING_ARM_CONVENTION,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="What a candidate scores on an arm that did not return it.",
    )
    fetch_depth: int = Field(
        gt=0,
        description="Candidates fetched per arm (FR-003).",
    )
    reranked_count: int = Field(
        gt=0,
        description="Candidates scored, top-N of the fused ordering (FR-018).",
    )
    search_breadth: int = Field(
        gt=0,
        description="The approximate index's search breadth, at or above the fetch depth.",
    )
    index_mode: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Whether the dense arm used the vector index (FR-026).",
    )
