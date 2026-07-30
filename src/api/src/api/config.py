"""Retrieval configuration, read once at the top of an invocation.

Follows E004's `Resolution.from_environment` precedent: configuration is read
once rather than reached for, and a variable that is present but unusable is an
error rather than a silent fallback — a typo that falls back runs for months
under a setting nobody chose.

Three of these values are **not** tuning knobs and are validated as such. The
fetch depth, the reranked count and the search breadth are one derived
constraint (spec FR-037): the two arms fetch `fetch_depth` candidates each, the
reranker scores `reranked_count` of the fused ordering, and the approximate
index's search breadth must be at least the fetch depth or the dense arm
silently returns fewer rows than asked for. `breadth >= depth == reranked_count`
is asserted here, at construction, because a configuration that violates it
produces no error at query time — only worse ranking.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "DEFAULT_FETCH_DEPTH",
    "DEFAULT_RERANKED_COUNT",
    "DEFAULT_SEARCH_BREADTH",
    "RESIDENT_MEMORY_BUDGET_BYTES",
    "RetrievalConfig",
    "RetrievalConfigError",
    "load_retrieval_config",
]

#: Candidates fetched per arm (spec FR-003). Part of the ranking definition, not
#: a tuning knob: changing it changes the workload the latency budget is stated
#: for, and FR-018 requires a re-measured quality figure and a re-measured
#: latency figure together.
DEFAULT_FETCH_DEPTH: Final = 50

#: Candidates the reranker scores, equal to the fetch depth (spec FR-018). The
#: fused set of two 50-candidate arms may hold up to 100 distinct candidates, so
#: this names the top 50 *of the fused ordering* rather than the whole set.
DEFAULT_RERANKED_COUNT: Final = 50

#: The approximate index's search breadth (spec FR-027). pgvector's own default
#: is 40, which is **below** the fetch depth — so the shipped default silently
#: under-serves the dense arm, returning fewer than 50 candidates and costing
#: recall with nothing reporting it. This floors it at the depth.
DEFAULT_SEARCH_BREADTH: Final = 50

#: Steady-state resident set size for the serving container, from
#: `specs/sad.md` §Quality Attributes. Covers **every** model session the
#: process holds — the query encoder plus both reranker graphs — and is not
#: apportioned between them; spec FR-033 requires the report itemize them
#: against this one total instead.
RESIDENT_MEMORY_BUDGET_BYTES: Final = 400 * 1024 * 1024

_ENV_PREFIX: Final = "PRC_RETRIEVAL_"


class RetrievalConfigError(ValueError):
    """A retrieval variable is present and unusable.

    Distinct from a missing variable, which takes its documented default. The
    message names the key and the constraint, so the failure identifies the
    thing to change rather than the place it was noticed.
    """


class RetrievalConfig(BaseModel):
    """Everything retrieval needs to know before it builds a statement.

    Frozen, because a fetch depth that can change mid-request makes the figures
    a run publishes an account of a workload that no longer exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    index_mode: Literal["exact", "approximate"] = Field(
        default="exact",
        description=(
            "Whether the dense arm uses the vector index. Spec FR-026 makes this "
            "control index usage and nothing else: filters, fusion, fetch depth "
            "and reranking are shared code across both settings, which "
            "test_flag_parity.py asserts rather than assumes."
        ),
    )

    fetch_depth: int = Field(
        default=DEFAULT_FETCH_DEPTH,
        gt=0,
        description="Candidates fetched per arm before fusion (FR-003).",
    )

    reranked_count: int = Field(
        default=DEFAULT_RERANKED_COUNT,
        gt=0,
        description="Candidates the reranker scores, top-N of the fused ordering (FR-018).",
    )

    search_breadth: int = Field(
        default=DEFAULT_SEARCH_BREADTH,
        gt=0,
        description=(
            "The approximate index's search breadth (FR-027). Carried on the "
            "connection rather than issued per query — a per-query SET is a "
            "second statement and FR-002 permits exactly one."
        ),
    )

    intra_op_threads: int = Field(
        default=1,
        gt=0,
        description=(
            "ONNX Runtime intra-op threads (FR-038). Set explicitly from the "
            "container's CPU quota rather than defaulted: the runtime's own "
            "default is one per physical core *the operating system reports*, "
            "which under a quota is the host's count, and picking the count "
            "itself is also what makes it set thread affinity — the pinning "
            "that oversubscribes in a container."
        ),
    )

    inter_op_threads: int = Field(
        default=1,
        gt=0,
        description=(
            "ONNX Runtime inter-op threads (FR-038). One: inter-op threading "
            "matters only under a parallel execution mode and the default is "
            "sequential, so more than one buys nothing here."
        ),
    )

    resident_memory_budget_bytes: int = Field(
        default=RESIDENT_MEMORY_BUDGET_BYTES,
        gt=0,
        description="The whole-process RSS budget every model session shares (SC-016).",
    )

    @model_validator(mode="after")
    def _derived_constraint_holds(self) -> RetrievalConfig:
        """Assert `breadth >= depth == reranked_count` (spec FR-037).

        Checked here because none of the three fails visibly on its own. A
        breadth below the depth returns fewer candidates than asked for with no
        error; a reranked count above the depth scores rows the arms never
        fetched. Both degrade ranking silently, which is the failure mode this
        constraint exists to make loud.
        """
        if self.reranked_count != self.fetch_depth:
            msg = (
                f"reranked_count ({self.reranked_count}) must equal fetch_depth "
                f"({self.fetch_depth}): FR-018 fixes the reranked count at the fetch "
                f"depth, and scoring more than the arms fetched is not possible."
            )
            raise RetrievalConfigError(msg)
        if self.search_breadth < self.fetch_depth:
            msg = (
                f"search_breadth ({self.search_breadth}) is below fetch_depth "
                f"({self.fetch_depth}): the dense arm would return fewer candidates "
                f"than requested, with no error and only degraded recall as the "
                f"symptom. FR-027 floors the breadth at the depth."
            )
            raise RetrievalConfigError(msg)
        return self


def _read_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(_ENV_PREFIX + key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{_ENV_PREFIX + key} is not an integer: {raw!r}"
        raise RetrievalConfigError(msg) from exc


def load_retrieval_config(env: Mapping[str, str] | None = None) -> RetrievalConfig:
    """Build the retrieval configuration from the environment.

    Args:
        env: The mapping to read. Defaults to the process environment. Taken as
            a parameter so a test can supply one without mutating `os.environ`
            — a suite that mutates it acquires an ordering dependency between
            tests that nothing in the suite makes visible.

    Returns:
        The resolved configuration. Absent variables take their documented
        defaults.

    Raises:
        RetrievalConfigError: A variable is present and unusable, or the three
            derived values do not satisfy `breadth >= depth == reranked_count`.
    """
    source = os.environ if env is None else env
    mode = source.get(_ENV_PREFIX + "INDEX_MODE", "exact")
    if mode not in {"exact", "approximate"}:
        msg = f"{_ENV_PREFIX}INDEX_MODE must be 'exact' or 'approximate', not {mode!r}"
        raise RetrievalConfigError(msg)
    try:
        return RetrievalConfig(
            index_mode=mode,
            fetch_depth=_read_int(source, "FETCH_DEPTH", DEFAULT_FETCH_DEPTH),
            reranked_count=_read_int(source, "RERANKED_COUNT", DEFAULT_RERANKED_COUNT),
            search_breadth=_read_int(source, "SEARCH_BREADTH", DEFAULT_SEARCH_BREADTH),
            intra_op_threads=_read_int(source, "INTRA_OP_THREADS", 1),
            inter_op_threads=_read_int(source, "INTER_OP_THREADS", 1),
            resident_memory_budget_bytes=_read_int(
                source, "MEMORY_BUDGET_BYTES", RESIDENT_MEMORY_BUDGET_BYTES
            ),
        )
    except ValidationError as exc:
        # Pydantic wraps whatever a `model_validator` raises, so the derived
        # constraint's own message would reach a caller inside a
        # `ValidationError` and this function's documented `Raises:` would be
        # false. Unwrapped rather than re-worded: the constraint's message says
        # which of the three values to change and why, and a caller catching
        # `RetrievalConfigError` gets exactly what the docstring promises.
        raise RetrievalConfigError(str(exc)) from exc
