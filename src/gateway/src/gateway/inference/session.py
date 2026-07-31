"""ONNX Runtime sessions, created only after their artifact verifies.

Spec FR-016, FR-038. Verification happens *before* a session exists, so
unverified bytes never reach the runtime — a check that creates the session and
then validates is decoration.

**Thread counts are set explicitly, never defaulted.** ONNX Runtime's default
intra-op count is one per physical core *the operating system reports*, which
under a CPU quota is the host's count rather than the container's — and picking
the count itself is also what makes the runtime set thread affinity, which is
the pinning that oversubscribes in a container. Setting both counts suppresses
that affinity assignment. Inter-op is one because it matters only under a
parallel execution mode and the default is sequential.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from gateway.inference.artifacts import VerifiedArtifact, verify_artifact
from gateway.inference.encoder import BatchTokenizer, InferenceSession, load_tokenizer

__all__ = [
    "LoadedEncoder",
    "SessionError",
    "load_encoder",
    "session_for",
]


class SessionError(RuntimeError):
    """A session cannot be created from the artifact as committed."""


@dataclass(frozen=True)
class LoadedEncoder:
    """A verified encoder: its session, its tokenizer, and its identity."""

    session: InferenceSession
    tokenizer: BatchTokenizer
    model_id: str
    revision: str
    vector_dimension: int

    @property
    def identity(self) -> tuple[str, str]:
        """FR-007's `(model id, revision)` pair.

        The pair the stored vectors were produced with must equal this, or the
        query lands in a different vector space with no error anywhere.
        """
        return (self.model_id, self.revision)


def session_for(
    graph: Path,
    *,
    intra_op_threads: int,
    inter_op_threads: int,
) -> InferenceSession:
    """Create a CPU session over `graph` with both thread counts pinned.

    The CPU provider is named explicitly rather than left to default discovery,
    so a machine with an accelerator present does not silently produce
    different numbers from the one the figures were measured on.
    """
    # `onnxruntime` ships no py.typed marker, so mypy cannot see its surface.
    # Ignored at the import rather than silenced per call site, and the
    # `InferenceSession` protocol in `encoder.py` is what actually describes
    # the slice of it this package uses.
    import onnxruntime as ort  # type: ignore[import-untyped]

    options = ort.SessionOptions()
    options.intra_op_num_threads = intra_op_threads
    options.inter_op_num_threads = inter_op_threads
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    try:
        created: Any = ort.InferenceSession(
            str(graph),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:  # noqa: BLE001 - any session failure is this module's
        raise SessionError(f"cannot create an ONNX Runtime session for {graph}: {exc}") from exc
    session: InferenceSession = created
    return session


@lru_cache(maxsize=2)
def load_encoder(
    directory: Path,
    *,
    record_name: str = "digests.json",
    intra_op_threads: int = 1,
    inter_op_threads: int = 1,
) -> LoadedEncoder:
    """Verify the encoder artifact and load it once per process.

    Cached, because FR-017 forbids loading a graph on a request path: a session
    built per request would put ~90 MB of weight loading inside the latency
    budget and would make the first request after every deploy an outlier
    nobody could explain from the figures.

    `record_name` defaults to E006's `digests.json` rather than the reranker's
    `provenance.json` — the two artifacts were vendored by different epics and
    their records are named differently. Unified verification, separate records.
    """
    artifact: VerifiedArtifact = verify_artifact(directory, record_name=record_name)
    graph = artifact.path("model.onnx")
    tokenizer_path = artifact.path("tokenizer.json")
    record = _record(directory, record_name)
    cap = int(record.get("effective_sequence_cap", 256))
    return LoadedEncoder(
        session=session_for(
            graph,
            intra_op_threads=intra_op_threads,
            inter_op_threads=inter_op_threads,
        ),
        # The *embedding* tokenizer: truncating at the cap, because the
        # reference encoder truncates there and parity must hold on inputs that
        # reach it. The counting instance in `model.ingest.tokens` passes None.
        tokenizer=load_tokenizer(tokenizer_path, truncate_at=cap),
        model_id=artifact.model_id,
        revision=artifact.revision,
        vector_dimension=int(record.get("vector_dimension", 384)),
    )


def _record(directory: Path, record_name: str) -> dict[str, Any]:
    import json

    document: dict[str, Any] = json.loads((directory / record_name).read_text(encoding="utf-8"))
    return document
