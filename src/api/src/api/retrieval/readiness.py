"""Readiness: both graphs warm before traffic, and a degraded system says so.

Spec FR-017, FR-021 to FR-024. Two rules that pull in opposite directions and
are reconciled here.

**Readiness is withheld until warm-up completes.** The framework's lifespan hook
runs before the application receives requests, which is a stronger gate than a
readiness endpoint: there is no window in which traffic arrives unwarmed.

**A reranker that fails to load does not make the process unready.** FR-021: it
reports **ready-degraded** and serves fusion-only orderings. Raising in the
lifespan hook would produce not-ready, and an orchestrator would restart the
container in a loop over a condition restarting cannot fix — while a working
fusion-only service sat unused.

So the hook catches, records, and still yields. Ready-degraded is a **success
response carrying a state field**, never a status code, because orchestrator
probes are ternary with no partial state and there is no standard
representation for "serving, but less well".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "ReadinessState",
    "RerankerFailure",
    "RetrievalReadiness",
    "UnrerankedReason",
    "readiness",
    "warm_rerankers",
]


class ReadinessState(StrEnum):
    """What a probe is told.

    `READY_DEGRADED` is a success state. It exists because the alternative —
    reporting not-ready — removes a service that works from rotation over a
    fault that restarting does not clear.
    """

    READY = "ready"
    READY_DEGRADED = "ready_degraded"
    NOT_READY = "not_ready"


class UnrerankedReason(StrEnum):
    """Why a response carries no reranked ordering.

    A closed vocabulary, because FR-022 requires the distinction be machine
    readable. The three differ in what a consumer should do about them, which is
    exactly why one flag could not carry all three.
    """

    #: The reranker never loaded. The system is degraded and says so.
    RERANKER_UNAVAILABLE = "reranker_unavailable"
    #: A session was lost mid-request. The request still completes as a degraded
    #: success rather than a fault — the work was done, the ordering is weaker.
    RERANKER_FAILED_DURING_REQUEST = "reranker_failed_during_request"
    #: The caller asked for an arm that does not rerank. Nothing is wrong, and
    #: claiming fusion-only degradation here would be a false alarm.
    ARM_EXCLUDES_RERANKING = "arm_excludes_reranking"
    #: Fusion returned nothing, so there was nothing to score. Also not a fault,
    #: and also not degradation.
    NO_CANDIDATES_TO_SCORE = "no_candidates_to_score"


@dataclass(frozen=True)
class RerankerFailure:
    """One graph that did not load, and why."""

    precision: str
    reason: str
    detail: str


@dataclass
class RetrievalReadiness:
    """The retrieval subsystem's readiness, assembled during startup."""

    sessions: dict[str, Any] = field(default_factory=dict)
    failures: list[RerankerFailure] = field(default_factory=list)
    encoder_ready: bool = False

    @property
    def state(self) -> ReadinessState:
        """Ready, ready-degraded, or not ready.

        **The encoder is load-bearing and the reranker is not.** Without a query
        embedding the dense arm cannot run at all, so an encoder failure is
        genuinely not-ready. Without a reranker the system still answers, less
        well — which is degradation, not failure.
        """
        if not self.encoder_ready:
            return ReadinessState.NOT_READY
        if not self.sessions:
            return ReadinessState.READY_DEGRADED
        if self.failures:
            # Partially available: one graph loaded and another did not. The
            # process is *not* degraded for the arm that works -- pulling it
            # from service would trade a working product for a missing
            # measurement -- and a request for the unavailable arm is refused
            # explicitly rather than silently served by the other, because an
            # evaluation that silently fell back would put a quantized figure
            # in a full-precision row.
            return ReadinessState.READY
        return ReadinessState.READY

    @property
    def degraded(self) -> bool:
        """Whether responses must state that they are fusion-only."""
        return self.encoder_ready and not self.sessions

    def session_for(self, precision: str) -> Any | None:
        return self.sessions.get(precision)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.state),
            "degraded": self.degraded,
            "encoder_ready": self.encoder_ready,
            "available_arms": sorted(self.sessions),
            "failures": [
                {"precision": f.precision, "reason": f.reason, "detail": f.detail}
                for f in self.failures
            ],
            "statement": _statement(self),
        }


def _statement(state: RetrievalReadiness) -> str:
    """A sentence a human reads, matching what the flags say.

    Required rather than decorative: FR-022 makes the fusion-only claim explicit
    in every degraded response, and a flag with no sentence beside it is read by
    machines and skipped by people.
    """
    if not state.encoder_ready:
        return "Not ready: the query encoder did not load, so no search can run."
    if not state.sessions:
        return (
            "Ready, degraded: no reranker loaded. Orderings are fusion-only, which "
            "this epic's own risk register calls a weak ordering — no figure from "
            "this process may be read as reranked."
        )
    if state.failures:
        missing = ", ".join(sorted(f.precision for f in state.failures))
        return (
            f"Ready: serving with {', '.join(sorted(state.sessions))}. "
            f"The {missing} arm did not load and requests for it are refused rather "
            f"than served by the other."
        )
    return "Ready: encoder and both reranker graphs are warm."


#: Module-level, because the lifespan hook fills it once and every request reads
#: it. A per-request construction would reload the graphs, which FR-017 forbids.
readiness = RetrievalReadiness()


def warm_rerankers(
    directory: Path,
    *,
    intra_op_threads: int = 1,
    inter_op_threads: int = 1,
    warm_batch: int = 50,
    state: RetrievalReadiness | None = None,
) -> RetrievalReadiness:
    """Load and warm both graphs, recording any that fail.

    Each graph is attempted independently. AD-011 ships both and AD-013 keeps
    both resident; loading them as a pair would make one failure lose the other,
    and the full-precision arm exists precisely to be compared against.
    """
    from gateway.inference.reranker import Precision, load_reranker

    target = readiness if state is None else state
    for precision in (Precision.INT8, Precision.FP32):
        try:
            target.sessions[str(precision)] = load_reranker(
                directory,
                precision,
                intra_op_threads=intra_op_threads,
                inter_op_threads=inter_op_threads,
                warm_batch=warm_batch,
            )
        except Exception as exc:  # noqa: BLE001 - any load failure degrades rather than raises
            target.failures.append(
                RerankerFailure(
                    precision=str(precision),
                    reason="load_failed",
                    detail=str(exc)[:300],
                )
            )
    return target


def encoder_directory() -> Path:
    configured = os.environ.get("PRC_ENCODER_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "data" / "encoder"


def reranker_directory() -> Path:
    configured = os.environ.get("PRC_RERANKER_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "data" / "reranker"
