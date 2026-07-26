"""The hop-walking retrieval client, the vendoring step, and the exclusion ledger.

FR-001 / FR-002b / FR-003 / FR-004 / FR-005 / FR-008 / FR-008c. Three jobs in
one module because they are one procedure: fetch under a policy, digest what
came back **before** anything is written, and record what was not fetched.

**FR-002b is a security control, not a convenience.** A redirect bypasses the
validation applied to the original URL (CWE-918), so an allow-list checked once
against the first-hop URL governs nothing after the first hop. Automatic
redirect following is therefore **disabled** and the chain is walked here, one
request at a time, with all five conditions re-evaluated before each request is
issued:

1. the scheme is exactly `https` — a redirect into `file://` or `scp://` is
   refused, because clients have a history of auto-following into both and a
   scheme restriction that is assumed rather than stated is not a restriction;
2. the host is compared by **exact lowercased equality** against
   `RetrievalPolicy.source_hosts` — never suffix or substring containment, so
   `www.wbdg.org.example.invalid` is not admitted by an allow-listed
   `www.wbdg.org` (VR-022);
3. at most **5 hops**; the sixth fails rather than being followed;
4. at most **50 MB** of response body, checked against `Content-Length` when the
   server offers one and enforced again against the bytes actually read, since a
   header is a claim and not a bound;
5. **no credentials**: no cookie jar, no authentication handler, no proxy, and a
   URL carrying userinfo is refused rather than having its credentials stripped.

The four bounds above are module constants and are deliberately **not** read
from `retrieval-policy.json`. That file is read by the validator, sits outside
every recorded digest, and its loosening direction is caught by nothing but
review (`data-model.md` §Drift story) — a hop bound or a size cap living there
could be widened by editing a data file. The host allow-list *is* data, because
it is a list of hosts that changes with the corpus; the bounds are not.

**FR-008c: the digest comes from the response body, in memory, before the
write.** `fetch_document` computes `upstream_digest` from the bytes it received
and hands it back with them; `vendor_document` writes those same bytes
unmodified (FR-001) and then recomputes `content_hash` **from the file** as a
write-integrity check. The two values are computed from two different sources on
purpose: the recorded provenance never comes from the committed file, because a
digest back-filled that way would make FR-008a's equality a tautology. Nothing
offline can tell the two apart afterwards — that exposure is published in
`data-model.md` §Uncovered Requirements — but this module must not be what
causes it.

**FR-004: what was not vendored is recorded.** A refused hop, a non-200, an
oversize body or a candidate whose license basis could not be established is an
exclusion with a closed cause and a non-empty note, never a silent drop. The
ledger's *integrity* is checkable; its *completeness* is not, and is published
as such.

Stdlib only, following `model/roster/reader.py`: one error type, frozen
dataclasses, `Path.write_bytes` for every committed file (HINT-004).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from model.corpus.manifest import content_hash_of_file, upstream_digest_of_response
from model.corpus.paths import CorpusPathError, corpus_root, resolve_within
from model.corpus.sources import (
    POLICY,
    RetrievalPolicy,
    RetrievalPolicyError,
    RetrievalTarget,
)

__all__ = [
    "EXCLUSION_CAUSES",
    "LEDGER_RELATIVE_PATH",
    "MAX_HOPS",
    "MAX_RESPONSE_BYTES",
    "REAL_LOCATION_RELATIVE_PATH",
    "REDIRECT_STATUSES",
    "REFUSAL_CONDITIONS",
    "REQUEST_INTERVAL_SECONDS",
    "REQUIRED_SCHEME",
    "ExclusionLedger",
    "ExclusionRecord",
    "FetchResult",
    "Hop",
    "RetrievalError",
    "append_exclusion",
    "document_filename",
    "fetch_document",
    "ledger_path",
    "main",
    "read_ledger",
    "vendor_document",
    "write_ledger",
]

REQUIRED_SCHEME = "https"
MAX_HOPS = 5
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
# 303 and 307/308 are included with the historical three: every one of them
# names a new URL the client is invited to request, which is the thing being
# bounded. Which method the redirect preserves is irrelevant to the control.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_TIMEOUT = 120

# A courtesy pause between documents in a batch run. Deliberately *not* one of
# the four bounds above: those are security controls that must not be loosened,
# while this only decides how considerate a 26-document run looks to a public
# host. Read from the module at call time so a test can set it to zero without
# a network fixture inheriting a two-second wait per candidate.
REQUEST_INTERVAL_SECONDS = 2.0

# No cookie, no authorization, no conditional header. Sent as the complete
# header set, so nothing a caller passes can add a credential.
REQUEST_HEADERS: Mapping[str, str] = {
    "User-Agent": "corpus-retrieve/1.0 (+public-domain UFGS corpus vendoring)",
    "Accept": "application/pdf,*/*",
}

# The closed set of reasons this client refuses to continue. Named rather than
# free text because the Error Handling strategy requires the retrieval log to
# say which condition fired, and a prose reason is not partitionable.
REFUSAL_CONDITIONS: tuple[str, ...] = (
    "BODY_TOO_LARGE",
    "CREDENTIALS_IN_URL",
    "HOP_BOUND_EXCEEDED",
    "HOST_NOT_ALLOW_LISTED",
    "MALFORMED_URL",
    "NON_SUCCESS_STATUS",
    "PORT_NOT_DEFAULT",
    "REDIRECT_WITHOUT_LOCATION",
    "SCHEME_NOT_HTTPS",
    "TRANSPORT_FAILURE",
)

# FR-004's closed cause enum. Held here, in code, rather than in the ledger file
# it governs: a closed set that lives inside the artifact it constrains can be
# extended by the same edit that adds a record needing the new value.
EXCLUSION_CAUSES: tuple[str, ...] = (
    "LICENSE_BASIS_NOT_ESTABLISHABLE",
    "REPRODUCES_COPYRIGHTED_STANDARD",
    "RETRIEVAL_FAILED",
    "WITHDRAWN_OR_UNRETRIEVABLE",
)

LEDGER_RELATIVE_PATH = "real/exclusions.json"
REAL_LOCATION_RELATIVE_PATH = "real/ufgs"


class RetrievalError(ValueError):
    """Raised when a fetch is refused or fails, or when the ledger is malformed.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — this document was not
    retrieved, nothing may be written for it, and its record belongs in the
    exclusion ledger.

    `condition` carries which of the closed `REFUSAL_CONDITIONS` fired, and
    `hop_index` which hop it fired on, so the retrieval log can partition
    failures without a second exception class per rule. Both are `None` for the
    ledger's own errors, which are not refusals of anything.
    """

    def __init__(
        self, message: str, *, condition: str | None = None, hop_index: int | None = None
    ) -> None:
        if condition is not None and condition not in REFUSAL_CONDITIONS:
            raise ValueError(f"unknown refusal condition {condition!r}")
        super().__init__(f"{condition}: {message}" if condition else message)
        self.condition = condition
        self.hop_index = hop_index


@dataclass(frozen=True)
class Hop:
    """One request in a redirect chain, recorded whether it was followed or not.

    The chain is returned on success so a run can report the route its bytes
    actually travelled, rather than only the URL it asked for — FR-008 records
    the **requested** URL as `source_location`, so the rest of the route would
    otherwise be unobservable after the fact.
    """

    index: int
    url: str
    host: str
    status: int
    location: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """A completed retrieval: the bytes, their digest, and how they were reached.

    `upstream_digest` is computed in `fetch_document` from `body`, before any
    file exists (FR-008c). `retrieved_at` is read from the clock here, which is
    the one place in this package a clock read is correct: it is the moment the
    response arrived, and it becomes a historical constant the instant it is
    recorded (MS-5).
    """

    requested_url: str
    final_url: str
    status: int
    body: bytes
    upstream_digest: str
    retrieved_at: str
    hops: tuple[Hop, ...]

    @property
    def byte_count(self) -> int:
        return len(self.body)

    @property
    def hop_chain(self) -> str:
        return " -> ".join(f"{hop.host}[{hop.status}]" for hop in self.hops)


def _utc_now() -> str:
    """RFC 3339, UTC, `Z` suffix, second precision — the form VR-020 requires."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_hop(url: str, hop_index: int, policy: RetrievalPolicy) -> str:
    """Re-evaluate every per-hop condition. Returns the accepted host.

    Called before **each** request, including the first: the first-hop URL is
    caller-supplied and gets no more trust than a redirect target does.
    """
    try:
        split = urlsplit(url)
    except ValueError as exc:
        raise RetrievalError(
            condition="MALFORMED_URL",
            message=f"cannot parse {url!r}: {exc}",
            hop_index=hop_index,
        ) from exc

    scheme = (split.scheme or "").lower()
    if scheme != REQUIRED_SCHEME:
        raise RetrievalError(
            condition="SCHEME_NOT_HTTPS",
            message=f"hop {hop_index} names scheme {scheme!r} in {url!r}; "
            f"only {REQUIRED_SCHEME!r} is followed (CWE-918)",
            hop_index=hop_index,
        )
    if split.username or split.password:
        raise RetrievalError(
            condition="CREDENTIALS_IN_URL",
            message=f"hop {hop_index} carries userinfo in {url!r}; this client sends no "
            "credentials and does not strip them silently",
            hop_index=hop_index,
        )

    try:
        host = split.hostname
        port = split.port
    except ValueError as exc:
        raise RetrievalError(
            condition="MALFORMED_URL",
            message=f"hop {hop_index}: {url!r} has no usable authority: {exc}",
            hop_index=hop_index,
        ) from exc

    if not policy.allows_host(host):
        raise RetrievalError(
            condition="HOST_NOT_ALLOW_LISTED",
            message=f"hop {hop_index} host {host!r} is not in the allow-list "
            f"{list(policy.source_hosts)}; membership is exact lowercased equality, "
            "never a suffix match (VR-022)",
            hop_index=hop_index,
        )
    if port not in (None, 443):
        raise RetrievalError(
            condition="PORT_NOT_DEFAULT",
            message=f"hop {hop_index} names port {port} on {host!r}; only the default "
            "https port is followed",
            hop_index=hop_index,
        )
    return str(host)


def _build_opener() -> urllib.request.OpenerDirector:
    """An opener that follows nothing and carries nothing.

    Redirects are disabled by a handler that returns `None` from
    `redirect_request`, which is urllib's documented way of refusing a
    redirect — an auto-following client cannot re-validate per hop, so this is
    the seam the whole control depends on. `ProxyHandler({})` suppresses
    environment proxies: a proxy read from `HTTPS_PROXY` would route the request
    somewhere the allow-list never saw. No `HTTPCookieProcessor` and no
    authentication handler is installed, so there is nothing to send.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, D102
            return None

    return urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _read_bounded(response: object, hop_index: int) -> bytes:
    """Read at most `MAX_RESPONSE_BYTES`, refusing anything longer.

    One byte past the cap is requested deliberately: a body of exactly the cap
    is admissible and a body that would exceed it is detected without ever
    holding the whole of it.
    """
    headers = getattr(response, "headers", None)
    declared = headers.get("Content-Length") if headers is not None else None
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError):
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > MAX_RESPONSE_BYTES:
            raise RetrievalError(
                condition="BODY_TOO_LARGE",
                message=f"hop {hop_index} declares Content-Length {declared_bytes} over the "
                f"{MAX_RESPONSE_BYTES}-byte bound",
                hop_index=hop_index,
            )

    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RetrievalError(
            condition="BODY_TOO_LARGE",
            message=f"hop {hop_index} body exceeds the {MAX_RESPONSE_BYTES}-byte bound; a declared "
            "Content-Length is a claim, so the bytes read are bounded too",
            hop_index=hop_index,
        )
    return body


def fetch_document(
    url: str,
    *,
    policy: RetrievalPolicy | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> FetchResult:
    """Fetch one document, walking every redirect by hand (FR-002b, FR-008c).

    Returns the response body together with a digest taken **from that body**,
    before any file is written. Raises `RetrievalError` — naming the condition
    and the hop it fired on — for every refusal; no partial file is written,
    because this function writes no file at all.
    """
    active_policy = policy if policy is not None else POLICY
    active_opener = opener if opener is not None else _build_opener()

    hops: list[Hop] = []
    current = url
    hop_index = 0

    while True:
        hop_index += 1
        if hop_index > MAX_HOPS:
            raise RetrievalError(
                condition="HOP_BOUND_EXCEEDED",
                message=f"redirect chain from {url!r} would need hop {hop_index}; "
                f"at most {MAX_HOPS} are followed",
                hop_index=hop_index,
            )

        host = _check_hop(current, hop_index, active_policy)
        request = urllib.request.Request(current, headers=dict(REQUEST_HEADERS), method="GET")
        try:
            response = active_opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except urllib.error.URLError as exc:
            raise RetrievalError(
                condition="TRANSPORT_FAILURE",
                message=f"hop {hop_index} to {host!r} failed: {exc.reason}",
                hop_index=hop_index,
            ) from exc
        except OSError as exc:
            raise RetrievalError(
                condition="TRANSPORT_FAILURE",
                message=f"hop {hop_index} to {host!r} failed: {exc}",
                hop_index=hop_index,
            ) from exc

        try:
            status = int(getattr(response, "status", None) or response.getcode())
            if status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                hops.append(Hop(hop_index, current, host, status, location))
                if not location:
                    raise RetrievalError(
                        condition="REDIRECT_WITHOUT_LOCATION",
                        message=f"hop {hop_index} returned {status} with no Location header",
                        hop_index=hop_index,
                    )
                # Resolved against the current URL, so a relative Location is
                # made absolute before it is checked rather than after.
                current = urljoin(current, location)
                continue

            hops.append(Hop(hop_index, current, host, status))
            if status != 200:
                raise RetrievalError(
                    condition="NON_SUCCESS_STATUS",
                    message=f"hop {hop_index} to {host!r} returned {status}; "
                    "only 200 is a retrieval (VR-019)",
                    hop_index=hop_index,
                )
            body = _read_bounded(response, hop_index)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        # FR-008c: the digest is taken here, from the bytes in hand, and never
        # re-read from a committed file afterwards.
        return FetchResult(
            requested_url=url,
            final_url=current,
            status=status,
            body=body,
            upstream_digest=upstream_digest_of_response(body),
            retrieved_at=_utc_now(),
            hops=tuple(hops),
        )


def document_filename(target: RetrievalTarget, policy: RetrievalPolicy | None = None) -> str:
    """The committed filename for a target: lowercase, hyphenated, no spaces.

    The manifest's `location` pattern admits no space and no path separator, so
    the published name `UFGS 26 11 13.00 20.pdf` cannot be the committed one.
    Filenames carry no semantics validation depends on (US4 AS4) — partitioning
    is always by recorded field — so this is a naming convention and nothing
    reads meaning back out of it.
    """
    active = policy if policy is not None else POLICY
    suffix = active.variant(target.agency_variant).section_suffix
    stem = f"{target.masterformat_section}{suffix}".replace(".", " ").replace(" ", "-")
    return f"ufgs-{stem}.pdf"


def vendor_document(result: FetchResult, destination: Path) -> Path:
    """Write the retrieved bytes unmodified and verify the write (FR-001).

    The bytes go to disk exactly as they arrived — no normalization, no
    re-encoding, no linearization — and `content_hash` is then recomputed **from
    the file** and compared against the digest `fetch_document` took from the
    response body. That comparison is a write-integrity check, not the recorded
    provenance: the manifest records `result.upstream_digest`, which came from
    memory, so FR-008a stays a comparison of two independently obtained values
    rather than a value compared against itself.
    """
    target = Path(destination)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.body)
    except OSError as exc:
        raise RetrievalError(
            condition="TRANSPORT_FAILURE",
            message=f"cannot vendor {result.requested_url} to {target}: {exc}",
        ) from exc

    written = content_hash_of_file(target)
    if written != result.upstream_digest:
        target.unlink(missing_ok=True)
        raise RetrievalError(
            condition="TRANSPORT_FAILURE",
            message=f"{target} does not hold the retrieved bytes: file {written}, response "
            f"{result.upstream_digest}; the partial file has been removed",
        )
    return target


# ---------------------------------------------------------------------------
# The exclusion ledger (FR-004, FR-005, VR-026)
# ---------------------------------------------------------------------------


def _ledger_text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise RetrievalError(f"{what} must be a string, found {type(value).__name__}")
    if not value.strip():
        raise RetrievalError(f"{what} must not be empty or whitespace-only")
    return value


@dataclass(frozen=True)
class ExclusionRecord:
    """One candidate that was considered and not vendored.

    Five required components, every one of them non-empty (VR-026). `note` is
    required text rather than optional because the cause enum is closed and
    coarse: `WITHDRAWN_OR_UNRETRIEVABLE` says what happened, and only the note
    says what was observed and when.
    """

    candidate_identifier: str
    source_location: str
    cause: str
    decided_on: str
    note: str

    def __post_init__(self) -> None:
        for field in ("candidate_identifier", "source_location", "note"):
            object.__setattr__(self, field, _ledger_text(getattr(self, field), field))
        cause = _ledger_text(self.cause, "cause")
        if cause not in EXCLUSION_CAUSES:
            raise RetrievalError(
                f"cause must be one of {list(EXCLUSION_CAUSES)}, found {cause!r} (FR-004)",
            )
        object.__setattr__(self, "cause", cause)
        decided_on = _ledger_text(self.decided_on, "decided_on")
        try:
            date.fromisoformat(decided_on)
        except ValueError as exc:
            raise RetrievalError(
                f"decided_on is not a calendar date: {decided_on!r} ({exc})"
            ) from exc
        object.__setattr__(self, "decided_on", decided_on)

    def payload(self) -> dict[str, str]:
        return {
            "candidate_identifier": self.candidate_identifier,
            "source_location": self.source_location,
            "cause": self.cause,
            "decided_on": self.decided_on,
            "note": self.note,
        }

    @classmethod
    def from_error(
        cls,
        candidate_identifier: str,
        source_location: str,
        error: RetrievalError,
        *,
        decided_on: str | None = None,
        note: str | None = None,
    ) -> ExclusionRecord:
        """Turn a refusal into a ledger record, keeping the condition in the note.

        A rejected hop is a retrieval failure that belongs in the ledger, never
        a document admitted because its first hop was allow-listed.
        """
        return cls(
            candidate_identifier=candidate_identifier,
            source_location=source_location,
            cause="RETRIEVAL_FAILED",
            decided_on=decided_on or datetime.now(UTC).strftime("%Y-%m-%d"),
            note=note or f"{error} (hop {error.hop_index})",
        )


@dataclass(frozen=True)
class ExclusionLedger:
    """The parsed ledger: its records plus the prose the file carries.

    Records are sorted by `candidate_identifier` at construction and duplicates
    are refused, so the file is stable across rewrites and one candidate cannot
    be recorded twice with two causes.
    """

    records: tuple[ExclusionRecord, ...]
    ledger_id: str = "ufgs-real-layer-exclusion-ledger"
    description: str = ""

    def __post_init__(self) -> None:
        records = tuple(self.records)
        for index, record in enumerate(records):
            if not isinstance(record, ExclusionRecord):
                raise RetrievalError(
                    f"exclusions[{index}] must be an ExclusionRecord, found "
                    f"{type(record).__name__}",
                )
        identifiers = [record.candidate_identifier for record in records]
        duplicates = sorted({name for name in identifiers if identifiers.count(name) > 1})
        if duplicates:
            raise RetrievalError(f"candidate_identifier values must be unique: {duplicates}")
        object.__setattr__(
            self, "records", tuple(sorted(records, key=lambda r: r.candidate_identifier))
        )

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"ledger_id": self.ledger_id}
        if self.description:
            payload["description"] = self.description
        payload["exclusions"] = [record.payload() for record in self.records]
        return payload


def ledger_path(root: Path | None = None) -> Path:
    """Resolve the ledger inside the corpus root, with VR-009's ordering."""
    base = corpus_root(root)
    try:
        return resolve_within(base, LEDGER_RELATIVE_PATH)
    except CorpusPathError as exc:
        raise RetrievalError(f"cannot resolve the exclusion ledger: {exc}") from exc


def read_ledger(path: Path | None = None, *, root: Path | None = None) -> ExclusionLedger:
    """Parse the committed ledger. A malformed ledger is an error, not an empty one."""
    target = Path(path) if path is not None else ledger_path(root)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise RetrievalError(f"cannot read the exclusion ledger {target}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalError(f"{target} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise RetrievalError(f"{target} must hold an object, found {type(document).__name__}")
    raw_records = document.get("exclusions")
    if isinstance(raw_records, str) or not isinstance(raw_records, Sequence):
        raise RetrievalError(
            f"{target}: exclusions must be an array, found {type(raw_records).__name__}"
        )
    records = []
    for index, item in enumerate(raw_records):
        if not isinstance(item, Mapping):
            raise RetrievalError(f"{target}: exclusions[{index}] must be an object")
        unexpected = sorted(set(item) - set(ExclusionRecord.__dataclass_fields__))
        if unexpected:
            raise RetrievalError(
                f"{target}: exclusions[{index}] carries unexpected keys {unexpected}"
            )
        records.append(
            ExclusionRecord(
                candidate_identifier=item.get("candidate_identifier"),
                source_location=item.get("source_location"),
                cause=item.get("cause"),
                decided_on=item.get("decided_on"),
                note=item.get("note"),
            )
        )
    return ExclusionLedger(
        records=tuple(records),
        ledger_id=str(document.get("ledger_id") or "ufgs-real-layer-exclusion-ledger"),
        description=str(document.get("description") or ""),
    )


def write_ledger(path: Path, ledger: ExclusionLedger) -> Path:
    """Write the ledger canonically, as bytes (HINT-004).

    `indent=2`, `sort_keys=True`, `ensure_ascii=False`, one trailing newline —
    the same serialization `canonical_manifest_bytes` uses, so the repository
    has one JSON style rather than three, and `Path.write_bytes` rather than a
    text-mode write, because the development machine is Windows and a CRLF
    rewrite would move the file's bytes for a reason unrelated to content.
    """
    if not isinstance(ledger, ExclusionLedger):
        raise RetrievalError(f"expected an ExclusionLedger, found {type(ledger).__name__}")
    target = Path(path)
    text = json.dumps(ledger.payload(), indent=2, sort_keys=True, ensure_ascii=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((text + "\n").encode("utf-8"))
    except OSError as exc:
        raise RetrievalError(f"cannot write the exclusion ledger {target}: {exc}") from exc
    return target


def append_exclusion(
    record: ExclusionRecord, path: Path | None = None, *, root: Path | None = None
) -> Path:
    """Add one record to the committed ledger and rewrite it canonically.

    Read-modify-write rather than an append to the end of the file: the ledger
    is JSON, its records are sorted by `candidate_identifier`, and a duplicate
    candidate fails at construction instead of being appended a second time.
    """
    target = Path(path) if path is not None else ledger_path(root)
    ledger = read_ledger(target)
    return write_ledger(
        target,
        ExclusionLedger(
            records=(*ledger.records, record),
            ledger_id=ledger.ledger_id,
            description=ledger.description,
        ),
    )


# ---------------------------------------------------------------------------
# Console entry point (FR-015's sibling: `corpus-retrieve`)
# ---------------------------------------------------------------------------


def _retrieve_one(
    target: RetrievalTarget, location: Path, policy: RetrievalPolicy
) -> tuple[FetchResult, Path]:
    result = fetch_document(target.source_url, policy=policy)
    path = vendor_document(result, location / document_filename(target, policy))
    return result, path


def main(argv: Sequence[str] | None = None) -> int:
    """Vendor every document the policy names, recording what could not be had.

    Emits one JSON object per line on stdout — section, variant, requested URL,
    status, hop chain, digest, byte count, retrieval instant and committed
    filename — so the manifest-writing step consumes a stream rather than a
    committed log. A retrieval log under `data/corpus/` would be an eighth file
    outside every location, which VR-065 closes the corpus root against.

    Exits non-zero when any candidate fails, after attempting all of them: a
    refusal is recorded in the ledger and the batch continues, because stopping
    at the first would leave the remaining candidates neither vendored nor
    recorded. `REQUEST_INTERVAL_SECONDS` separates one candidate from the next,
    so a full run is a paced sequence of requests to a public host rather than a
    burst.
    """
    _ = argv
    try:
        policy = POLICY
        root = corpus_root()
        location = root.joinpath(*REAL_LOCATION_RELATIVE_PATH.split("/"))
        ledger_file = ledger_path()
    except (CorpusPathError, RetrievalPolicyError, RetrievalError) as exc:
        print(f"corpus-retrieve: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for index, target in enumerate(policy.retrieval_targets):
        if index and REQUEST_INTERVAL_SECONDS > 0:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        try:
            result, path = _retrieve_one(target, location, policy)
        except RetrievalError as exc:
            failures += 1
            record = ExclusionRecord.from_error(
                candidate_identifier=f"UFGS {target.masterformat_section}"
                f"{policy.variant(target.agency_variant).section_suffix}",
                source_location=target.source_url,
                error=exc,
            )
            try:
                append_exclusion(record, ledger_file)
            except RetrievalError as ledger_exc:
                print(f"corpus-retrieve: cannot record exclusion: {ledger_exc}", file=sys.stderr)
            print(f"corpus-retrieve: {target.key} not vendored: {exc}", file=sys.stderr)
            continue

        print(
            json.dumps(
                {
                    "masterformat_section": target.masterformat_section,
                    "agency_variant": target.agency_variant,
                    "issuing_body": policy.variant(target.agency_variant).issuing_body,
                    "source_location": result.requested_url,
                    "retrieval_response_status": result.status,
                    "retrieved_at": result.retrieved_at,
                    "upstream_digest": result.upstream_digest,
                    "bytes": result.byte_count,
                    "hop_chain": result.hop_chain,
                    "location": path.name,
                },
                sort_keys=True,
            )
        )

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - console entry is `corpus-retrieve`
    raise SystemExit(main())
