"""The opt-in re-verification job: re-fetch recorded sources, compare digests.

FR-008b. Every REAL entry records where its bytes came from (`source_location`)
and what they hashed to when they arrived (`upstream_digest`). This module is
the only later observation of that source: it requests each recorded URL again
and reports whether the source still serves the bytes the manifest recorded.

**It re-fetches through `model.corpus.retrieve.fetch_document`, deliberately.**
FR-002b binds *both* network paths — the same https-only rule, the same per-hop
allow-list re-evaluation, the same 5-hop bound, the same 50 MB body bound, the
same zero-credential posture — so the two cannot diverge by one of them
inheriting a control the other merely states. Re-implementing the walk here with
"the same" rules would be a second implementation of a security control, and the
second one is always the one that falls behind.

**A divergence is a change at the source, never corpus drift.** The vendored
copy stays authoritative and its `revision_date` stays truthful; upstream
publishing a newer revision of a section is the ordinary way this job reports
`DIVERGED` (`data-model.md` §Drift story). Reading it the other way — as
evidence the committed file changed — is the misinterpretation this module's
output is worded to prevent. The check that the *committed* file still matches
its recorded digest is VR-012's, offline, and belongs to the validator.

**No workflow may invoke this.** A required check may not depend on the network,
so `corpus-reverify` appears in no per-push run. That exclusion is the reason the
obligation is stated as cadence and owner instead: the repository administrator
runs it before each release tag and records its outcome — sources re-fetched and
every divergence, or explicitly none — in that release's record. Nothing
committed can observe that the run happened; a release record carrying no
re-verification outcome is the only observable failure of the obligation.

Stdlib only, following `model/roster/reader.py`: one error type, frozen
dataclasses, results ordered deterministically.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from model.corpus.manifest import LAYER_REAL
from model.corpus.paths import CorpusPathError, corpus_root, discover_locations
from model.corpus.retrieve import (
    DEFAULT_TIMEOUT,
    REQUEST_INTERVAL_SECONDS,
    RetrievalError,
    fetch_document,
)
from model.corpus.sources import POLICY, RetrievalPolicy, RetrievalPolicyError

__all__ = [
    "STATUSES",
    "STATUS_DIVERGED",
    "STATUS_MATCH",
    "STATUS_UNREACHABLE",
    "RecordedSource",
    "ReverificationError",
    "ReverificationOutcome",
    "main",
    "recorded_sources",
    "reverify_source",
]

# The closed set of per-source outcomes. `UNREACHABLE` is kept apart from
# `DIVERGED` because they mean opposite things about the source: one says the
# source answered with different bytes, the other says it did not answer at all,
# and a release record that conflated them would report a divergence nobody
# observed.
STATUS_MATCH = "MATCH"
STATUS_DIVERGED = "DIVERGED"
STATUS_UNREACHABLE = "UNREACHABLE"
STATUSES: tuple[str, ...] = (STATUS_DIVERGED, STATUS_MATCH, STATUS_UNREACHABLE)


class ReverificationError(ValueError):
    """Raised when the corpus or one of its manifests cannot be read.

    One type for every failure, as `RetrievalError` and `ManifestError` are: a
    caller learns the same thing from each of them — the recorded sources could
    not be established, so no re-verification outcome may be reported. A source
    that fails to *re-fetch* is not this error; it is an `UNREACHABLE` outcome,
    because the job's job is to report it rather than to stop.
    """


@dataclass(frozen=True)
class RecordedSource:
    """One REAL entry's recorded provenance, as the manifest holds it.

    Carries only what re-verification needs — where to look and what to expect —
    rather than the whole entry, so nothing here can be mistaken for a rewritten
    manifest record.
    """

    location_id: str
    location: str
    source_location: str
    upstream_digest: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.location_id, self.location)


@dataclass(frozen=True)
class ReverificationOutcome:
    """What one recorded source did when it was asked again.

    `observed_digest` is `None` exactly when the source did not answer; a
    divergence always carries both digests, so the release record can state what
    changed rather than only that something did.
    """

    source: RecordedSource
    status: str
    observed_digest: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ReverificationError(
                f"status must be one of {list(STATUSES)}, found {self.status!r}"
            )
        if self.status == STATUS_UNREACHABLE and self.observed_digest is not None:
            raise ReverificationError("an unreachable source has no observed digest")
        if self.status != STATUS_UNREACHABLE and self.observed_digest is None:
            raise ReverificationError(f"a {self.status} outcome carries an observed digest")

    @property
    def diverged(self) -> bool:
        return self.status == STATUS_DIVERGED

    def payload(self) -> dict[str, object]:
        return {
            "location_id": self.source.location_id,
            "location": self.source.location,
            "source_location": self.source.source_location,
            "recorded_upstream_digest": self.source.upstream_digest,
            "observed_digest": self.observed_digest,
            "status": self.status,
            "detail": self.detail,
        }


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReverificationError(f"{what} must be a non-empty string, found {value!r}")
    return value


def _read_manifest(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReverificationError(f"cannot read {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReverificationError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ReverificationError(f"{path} must hold an object, found {type(document).__name__}")
    return document


def recorded_sources(root: Path | None = None) -> tuple[RecordedSource, ...]:
    """Every REAL entry's recorded source, across every corpus location.

    Read from the committed manifests rather than from the retrieval policy: the
    policy says what *should* have been vendored, and this job must re-fetch
    what actually was. A SYNTHETIC location contributes nothing and is skipped
    rather than rejected — it has no source to re-fetch, which is the layer
    asymmetry working, not a defect.

    Ordered by `(location_id, location)` so two runs report in one order.
    """
    try:
        base = corpus_root(root)
        locations = discover_locations(base)
    except CorpusPathError as exc:
        raise ReverificationError(f"cannot read the corpus: {exc}") from exc

    sources: list[RecordedSource] = []
    for location in locations:
        document = _read_manifest(location.manifest_path)
        if document.get("layer") != LAYER_REAL:
            continue
        entries = document.get("entries")
        if isinstance(entries, str) or not isinstance(entries, Sequence):
            raise ReverificationError(
                f"{location.manifest_path}: entries must be an array, "
                f"found {type(entries).__name__}"
            )
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ReverificationError(
                    f"{location.manifest_path}: entries[{index}] must be an object"
                )
            what = f"{location.location_id} entries[{index}]"
            sources.append(
                RecordedSource(
                    location_id=location.location_id,
                    location=_text(entry.get("location"), f"{what}.location"),
                    source_location=_text(entry.get("source_location"), f"{what}.source_location"),
                    upstream_digest=_text(entry.get("upstream_digest"), f"{what}.upstream_digest"),
                )
            )
    return tuple(sorted(sources, key=lambda source: source.key))


def reverify_source(
    source: RecordedSource,
    *,
    policy: RetrievalPolicy | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ReverificationOutcome:
    """Re-fetch one recorded source and compare its digest against the record.

    The comparison is over `upstream_digest` — the digest taken from the
    response body at first retrieval — against the digest taken from the
    response body now, so both sides of the equality come from the network and
    neither comes from the committed file. Comparing against `content_hash`
    instead would compare the source against the repository, which is VR-012's
    question and not this one.
    """
    if not isinstance(source, RecordedSource):
        raise ReverificationError(f"expected a RecordedSource, found {type(source).__name__}")
    try:
        result = fetch_document(
            source.source_location,
            policy=policy if policy is not None else POLICY,
            opener=opener,
            timeout=timeout,
        )
    except RetrievalError as exc:
        return ReverificationOutcome(
            source=source,
            status=STATUS_UNREACHABLE,
            detail=f"{exc}",
        )

    if result.upstream_digest == source.upstream_digest:
        return ReverificationOutcome(
            source=source,
            status=STATUS_MATCH,
            observed_digest=result.upstream_digest,
            detail=f"{result.byte_count} bytes over {result.hop_chain}",
        )
    return ReverificationOutcome(
        source=source,
        status=STATUS_DIVERGED,
        observed_digest=result.upstream_digest,
        detail=(
            "the source now serves different bytes; this is a change at the source, "
            "not corpus drift — the vendored copy stays authoritative"
        ),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    policy: RetrievalPolicy | None = None,
) -> int:
    """Re-fetch every recorded source and print the release record's material.

    Emits one JSON object per source, then a summary object carrying the two
    numbers SC-009 asks a release record to state — sources re-fetched, and
    every divergence *or explicitly none*. "Explicitly none" is why the summary
    is printed even when nothing diverged: an empty list is a reported outcome
    and a missing line is not.

    Exit codes are for the operator, not for a gate — nothing automatic invokes
    this. `0` when every recorded source answered with the recorded bytes, `1`
    when any source diverged or could not be reached, `2` when the corpus itself
    could not be read and no outcome could be established at all.

    `root`, `opener` and `policy` exist so the entry point itself can be
    exercised offline; the console script passes none of them and the committed
    policy governs, which is the arrangement that keeps this path and first
    retrieval on one set of rules.
    """
    _ = argv
    try:
        active_policy = policy if policy is not None else POLICY
        sources = recorded_sources(root)
    except (ReverificationError, RetrievalPolicyError) as exc:
        print(f"corpus-reverify: {exc}", file=sys.stderr)
        return 2

    outcomes: list[ReverificationOutcome] = []
    for index, source in enumerate(sources):
        if index and REQUEST_INTERVAL_SECONDS > 0:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        outcome = reverify_source(source, policy=active_policy, opener=opener)
        outcomes.append(outcome)
        print(json.dumps(outcome.payload(), sort_keys=True))

    divergences = [outcome for outcome in outcomes if outcome.status == STATUS_DIVERGED]
    unreachable = [outcome for outcome in outcomes if outcome.status == STATUS_UNREACHABLE]
    print(
        json.dumps(
            {
                "sources_recorded": len(sources),
                "sources_refetched": len(outcomes) - len(unreachable),
                "divergences": [outcome.payload() for outcome in divergences],
                "unreachable": [outcome.payload() for outcome in unreachable],
                "statement": (
                    f"{len(outcomes) - len(unreachable)} of {len(sources)} recorded sources "
                    f"re-fetched; "
                    + (
                        "no digest divergence"
                        if not divergences
                        else f"{len(divergences)} digest divergence(s) at the source"
                    )
                    + (f"; {len(unreachable)} unreachable" if unreachable else "")
                ),
            },
            sort_keys=True,
        )
    )
    return 1 if divergences or unreachable else 0


if __name__ == "__main__":  # pragma: no cover - console entry is `corpus-reverify`
    raise SystemExit(main())
