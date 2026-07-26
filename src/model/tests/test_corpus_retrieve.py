"""The retrieval policy and the hop-walking client, exercised without a network.

FR-002 / FR-002a / FR-002b / FR-004. Every request here is served by a local
stub opener, so the suite runs on a machine with no route to the internet and
two runs of it observe the same bytes. That is not only convenience: these tests
judge a security control, and a control tested against a live host is tested
against whatever that host did today.

Six of `tasks.md` Phase 3's eight conditions are the retrieval client's and are
covered below — a redirect hop re-checked against the allow-list, a hop landing
outside it, a host that merely *ends in* an allow-listed name, a redirect into a
non-`https` scheme, a non-200 status, a sixth hop, and a body past 50 MB. The
eighth, a digest diverging from the recorded `upstream_digest`, belongs to
`test_corpus_reverify.py`.
"""

from __future__ import annotations

import email.message
import json
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest

from model.corpus.manifest import upstream_digest_of_response
from model.corpus.paths import CorpusPathError
from model.corpus.retrieve import (
    EXCLUSION_CAUSES,
    MAX_HOPS,
    MAX_RESPONSE_BYTES,
    REQUEST_HEADERS,
    ExclusionLedger,
    ExclusionRecord,
    RetrievalError,
    _build_opener,
    append_exclusion,
    document_filename,
    fetch_document,
    ledger_path,
    main,
    read_ledger,
    vendor_document,
    write_ledger,
)
from model.corpus.sources import (
    REQUIRED_ANCHOR_SECTION,
    AgencyVariant,
    RetrievalPolicyError,
    load_policy,
    policy_path,
)

ALLOWED_HOST = "host.allowed.test"
ORIGIN_HOST = "origin.allowed.test"
FIRST_URL = f"https://{ALLOWED_HOST}/UFGS%2026%2012%2019.pdf"
ORIGIN_URL = f"https://{ORIGIN_HOST}/bucket/26-12-19.pdf"
BODY = b"%PDF-1.7\nvendored bytes\n"


# ---------------------------------------------------------------------------
# Stub transport. Nothing below opens a socket.
# ---------------------------------------------------------------------------


class _Response:
    """The subset of `http.client.HTTPResponse` the client actually touches."""

    def __init__(
        self,
        status: int,
        body: bytes = b"",
        *,
        location: str | None = None,
        content_length: str | None = None,
        expose_status: bool = True,
    ) -> None:
        headers: dict[str, str] = {}
        if location is not None:
            headers["Location"] = location
        if content_length is not None:
            headers["Content-Length"] = content_length
        self.headers = headers
        self._body = body
        self.closed = False
        if expose_status:
            self.status = status
        self._code = status

    def getcode(self) -> int:
        return self._code

    def read(self, amount: int | None = None) -> bytes:
        return self._body if amount is None else self._body[:amount]

    def close(self) -> None:
        self.closed = True


class _Opener:
    """Maps a URL to a response, an exception, or a sequence of both."""

    def __init__(self, routes: dict[str, object], *, default: object | None = None) -> None:
        self._routes = routes
        self._default = default
        self.requested: list[str] = []
        self.headers_seen: list[dict[str, str]] = []

    def open(self, request: urllib.request.Request, timeout: float | None = None):  # noqa: ANN201
        self.requested.append(request.full_url)
        self.headers_seen.append(dict(request.header_items()))
        outcome = self._routes.get(request.full_url, self._default)
        if outcome is None:
            raise AssertionError(f"the client requested an unrouted URL: {request.full_url}")
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http_error(url: str, status: int) -> urllib.error.HTTPError:
    """A real `HTTPError`, which urllib raises instead of returning a response."""
    return urllib.error.HTTPError(url, status, "refused", email.message.Message(), None)


# ---------------------------------------------------------------------------
# The policy under test, built through `load_policy` rather than by hand
# ---------------------------------------------------------------------------


def _policy_document() -> dict:
    return {
        "source_hosts": [ALLOWED_HOST, ORIGIN_HOST],
        "agency_variants": {
            "UNIFIED": {"section_suffix": "", "issuing_body": "USACE / NAVFAC / AFCEC"},
            "NAVFAC": {"section_suffix": ".00 20", "issuing_body": "NAVFAC"},
        },
        "target_sections": [
            {
                "masterformat_section": "26 12 19",
                "agency_variant": "UNIFIED",
                "title": "Pad-Mounted Transformers",
                "division": "26",
                "source_url": FIRST_URL,
                "lead_time_class": "EXTREME_LEAD",
                "lead_time_justification": "the reference's 128-week transformer figure",
                "resolution_verified_on": "2026-07-26",
            },
            {
                "masterformat_section": "26 12 19",
                "agency_variant": "NAVFAC",
                "title": "Pad-Mounted Transformers, NAVFAC",
                "division": "26",
                "source_url": f"https://{ALLOWED_HOST}/UFGS%2026%2012%2019.00%2020.pdf",
                "lead_time_class": "EXTREME_LEAD",
                "lead_time_justification": "the same assembly under the NAVFAC variant",
                "resolution_verified_on": "2026-07-26",
            },
        ],
        "anchor_section": REQUIRED_ANCHOR_SECTION,
        "anchor": {
            "masterformat_section": REQUIRED_ANCHOR_SECTION,
            "agency_variant": "UNIFIED",
            "title": "Submittal Procedures",
            "division": "01",
            "source_url": f"https://{ALLOWED_HOST}/UFGS%2001%2033%2000.pdf",
            "rationale": "the section every other document is read against",
            "resolution_verified_on": "2026-07-26",
        },
    }


def _write_policy(tmp_path: Path, document: dict, name: str = "policy.json") -> Path:
    target = tmp_path / name
    target.write_bytes(json.dumps(document).encode("utf-8"))
    return target


@pytest.fixture
def policy(tmp_path: Path):
    return load_policy(_write_policy(tmp_path, _policy_document()))


def _load_broken(tmp_path: Path, mutate, name: str):
    document = _policy_document()
    mutate(document)
    return load_policy(_write_policy(tmp_path, document, name))


# ---------------------------------------------------------------------------
# The eight conditions, six of them here
# ---------------------------------------------------------------------------


def test_a_redirect_hop_is_followed_and_re_checked_against_the_allow_list(policy) -> None:
    """Condition 1: the second hop is validated, not inherited from the first."""
    opener = _Opener(
        {
            FIRST_URL: _Response(301, location=ORIGIN_URL),
            ORIGIN_URL: _Response(200, BODY),
        }
    )
    result = fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert opener.requested == [FIRST_URL, ORIGIN_URL]
    # FR-008: the *requested* URL is what an entry records, never the target of
    # the redirect, so the recorded value stays the citable published location.
    assert result.requested_url == FIRST_URL
    assert result.final_url == ORIGIN_URL
    assert result.status == 200
    assert result.body == BODY
    assert result.upstream_digest == upstream_digest_of_response(BODY)
    assert result.byte_count == len(BODY)
    assert result.hop_chain == f"{ALLOWED_HOST}[301] -> {ORIGIN_HOST}[200]"
    assert [hop.index for hop in result.hops] == [1, 2]
    assert result.hops[0].location == ORIGIN_URL
    assert result.retrieved_at.endswith("Z")


def test_a_redirect_landing_outside_the_allow_list_is_refused(policy) -> None:
    """Condition 2: an allow-listed first hop admits nothing after it."""
    elsewhere = "https://elsewhere.invalid/26-12-19.pdf"
    opener = _Opener({FIRST_URL: _Response(302, location=elsewhere)})

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "HOST_NOT_ALLOW_LISTED"
    assert caught.value.hop_index == 2
    # The refused hop was never requested: the check runs before the request.
    assert opener.requested == [FIRST_URL]


@pytest.mark.parametrize(
    "lookalike",
    [
        f"https://{ALLOWED_HOST}.attacker.invalid/x.pdf",
        f"https://evil{ALLOWED_HOST}/x.pdf",
        f"https://prefix.{ALLOWED_HOST}.invalid/x.pdf",
    ],
)
def test_a_host_that_merely_ends_in_an_allow_listed_name_is_refused(policy, lookalike) -> None:
    """Condition 3 (VR-022): membership is exact equality, never containment."""
    opener = _Opener({})
    with pytest.raises(RetrievalError) as caught:
        fetch_document(lookalike, policy=policy, opener=opener)

    assert caught.value.condition == "HOST_NOT_ALLOW_LISTED"
    assert opener.requested == []


@pytest.mark.parametrize("scheme", ["http", "file", "scp", "ftp"])
def test_a_redirect_into_a_non_https_scheme_is_refused(policy, scheme) -> None:
    """Condition 4: clients have auto-followed into `file://`; this one does not."""
    opener = _Opener({FIRST_URL: _Response(307, location=f"{scheme}://{ORIGIN_HOST}/x.pdf")})

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "SCHEME_NOT_HTTPS"
    assert caught.value.hop_index == 2


@pytest.mark.parametrize("status", [204, 403, 404, 410, 500])
def test_a_non_success_status_is_not_a_retrieval(policy, status) -> None:
    """Condition 5 (VR-019): only 200 is a retrieval."""
    opener = _Opener({FIRST_URL: _Response(status, BODY)})

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "NON_SUCCESS_STATUS"
    assert caught.value.hop_index == 1


def test_a_non_success_status_raised_as_an_http_error_is_handled(policy) -> None:
    """urllib raises 4xx and 5xx rather than returning them; both paths are one."""
    opener = _Opener({FIRST_URL: _http_error(FIRST_URL, 403)})

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "NON_SUCCESS_STATUS"
    assert "403" in str(caught.value)


def test_the_sixth_hop_fails_rather_than_being_followed(policy) -> None:
    """Condition 6: the bound is on hops, and the sixth is refused, not followed."""
    routes = {
        f"https://{ALLOWED_HOST}/hop{index}": _Response(
            302, location=f"https://{ALLOWED_HOST}/hop{index + 1}"
        )
        for index in range(MAX_HOPS + 2)
    }
    opener = _Opener(routes)

    with pytest.raises(RetrievalError) as caught:
        fetch_document(f"https://{ALLOWED_HOST}/hop0", policy=policy, opener=opener)

    assert caught.value.condition == "HOP_BOUND_EXCEEDED"
    assert caught.value.hop_index == MAX_HOPS + 1
    assert len(opener.requested) == MAX_HOPS


def test_a_declared_content_length_over_the_bound_is_refused_before_reading(policy) -> None:
    """Condition 7, first half: the claim is checked without reading the body."""
    opener = _Opener({FIRST_URL: _Response(200, BODY, content_length=str(MAX_RESPONSE_BYTES + 1))})

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "BODY_TOO_LARGE"
    assert str(MAX_RESPONSE_BYTES) in str(caught.value)


def test_a_body_past_the_bound_is_refused_even_when_the_header_lies(policy) -> None:
    """Condition 7, second half: a `Content-Length` is a claim, not a bound."""
    opener = _Opener(
        {FIRST_URL: _Response(200, bytes(MAX_RESPONSE_BYTES + 1), content_length="12")}
    )

    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)

    assert caught.value.condition == "BODY_TOO_LARGE"
    assert "Content-Length is a claim" in str(caught.value)


def test_a_body_of_exactly_the_bound_is_admissible(policy) -> None:
    """The cap is inclusive; one byte past it is not."""
    body = bytes(MAX_RESPONSE_BYTES)
    opener = _Opener({FIRST_URL: _Response(200, body)})
    result = fetch_document(FIRST_URL, policy=policy, opener=opener)
    assert result.byte_count == MAX_RESPONSE_BYTES


@pytest.mark.parametrize("declared", ["not-a-number", None])
def test_an_unusable_content_length_falls_through_to_the_byte_bound(policy, declared) -> None:
    opener = _Opener({FIRST_URL: _Response(200, BODY, content_length=declared)})
    assert fetch_document(FIRST_URL, policy=policy, opener=opener).body == BODY


# ---------------------------------------------------------------------------
# The remaining refusal conditions
# ---------------------------------------------------------------------------


def test_a_url_carrying_credentials_is_refused_rather_than_stripped(policy) -> None:
    opener = _Opener({})
    with pytest.raises(RetrievalError) as caught:
        fetch_document(f"https://user:secret@{ALLOWED_HOST}/x.pdf", policy=policy, opener=opener)

    assert caught.value.condition == "CREDENTIALS_IN_URL"
    assert opener.requested == []


def test_a_non_default_port_is_refused(policy) -> None:
    with pytest.raises(RetrievalError) as caught:
        fetch_document(f"https://{ALLOWED_HOST}:8443/x.pdf", policy=policy, opener=_Opener({}))
    assert caught.value.condition == "PORT_NOT_DEFAULT"


def test_the_documented_explicit_443_port_is_admitted(policy) -> None:
    """The origin's `Location` carries an explicit `:443`; that is the default."""
    url = f"https://{ALLOWED_HOST}:443/x.pdf"
    result = fetch_document(url, policy=policy, opener=_Opener({url: _Response(200, BODY)}))
    assert result.body == BODY


def test_a_redirect_without_a_location_header_is_refused(policy) -> None:
    opener = _Opener({FIRST_URL: _Response(302)})
    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=opener)
    assert caught.value.condition == "REDIRECT_WITHOUT_LOCATION"


def test_a_relative_location_is_resolved_before_it_is_checked(policy) -> None:
    """Absolute-then-check, never check-then-absolute."""
    opener = _Opener(
        {
            FIRST_URL: _Response(302, location="/vendored/26-12-19.pdf"),
            f"https://{ALLOWED_HOST}/vendored/26-12-19.pdf": _Response(200, BODY),
        }
    )
    result = fetch_document(FIRST_URL, policy=policy, opener=opener)
    assert result.final_url == f"https://{ALLOWED_HOST}/vendored/26-12-19.pdf"


@pytest.mark.parametrize(
    "url",
    [f"https://{ALLOWED_HOST}:port/x.pdf", "https://[unterminated/x.pdf"],
)
def test_an_unparsable_authority_is_refused_as_malformed(policy, url) -> None:
    with pytest.raises(RetrievalError) as caught:
        fetch_document(url, policy=policy, opener=_Opener({}))
    assert caught.value.condition == "MALFORMED_URL"


def test_a_url_with_no_host_at_all_is_refused(policy) -> None:
    with pytest.raises(RetrievalError) as caught:
        fetch_document("https:///x.pdf", policy=policy, opener=_Opener({}))
    assert caught.value.condition == "HOST_NOT_ALLOW_LISTED"


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("connection refused"), OSError("socket closed")],
)
def test_a_transport_failure_names_its_condition(policy, failure) -> None:
    with pytest.raises(RetrievalError) as caught:
        fetch_document(FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: failure}))
    assert caught.value.condition == "TRANSPORT_FAILURE"


def test_a_response_without_a_status_attribute_falls_back_to_getcode(policy) -> None:
    opener = _Opener({FIRST_URL: _Response(200, BODY, expose_status=False)})
    assert fetch_document(FIRST_URL, policy=policy, opener=opener).status == 200


def test_a_response_object_with_no_close_is_tolerated(policy) -> None:
    """`close` is called when it exists; a response without one is not an error."""

    class _Bare:
        status = 200
        headers: dict[str, str] = {}

        def read(self, amount: int | None = None) -> bytes:
            return BODY

    assert (
        fetch_document(FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: _Bare()})).body == BODY
    )


def test_the_response_is_closed_on_success_and_on_refusal(policy) -> None:
    good = _Response(200, BODY)
    fetch_document(FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: good}))
    assert good.closed

    bad = _Response(404)
    with pytest.raises(RetrievalError):
        fetch_document(FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: bad}))
    assert bad.closed


def test_the_request_carries_no_credential_header(policy) -> None:
    opener = _Opener({FIRST_URL: _Response(200, BODY)})
    fetch_document(FIRST_URL, policy=policy, opener=opener)
    sent = {name.lower() for name in opener.headers_seen[0]}
    assert sent == {name.lower() for name in REQUEST_HEADERS}
    assert not sent & {"cookie", "authorization", "proxy-authorization"}


def test_the_default_opener_follows_no_redirect() -> None:
    """The seam the whole per-hop control depends on."""
    opener = _build_opener()
    handlers = {type(handler).__name__ for handler in opener.handlers}
    assert "_NoRedirect" in handlers
    assert "HTTPCookieProcessor" not in handlers
    redirect = next(h for h in opener.handlers if type(h).__name__ == "_NoRedirect")
    assert redirect.redirect_request(None, None, 301, "moved", {}, "https://x.invalid") is None


def test_an_unknown_refusal_condition_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="unknown refusal condition"):
        RetrievalError("nope", condition="NOT_A_CONDITION")


# ---------------------------------------------------------------------------
# Vendoring (FR-001, FR-008c)
# ---------------------------------------------------------------------------


def test_vendoring_writes_the_retrieved_bytes_unmodified(policy, tmp_path: Path) -> None:
    result = fetch_document(
        FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: _Response(200, BODY)})
    )
    written = vendor_document(result, tmp_path / "nested" / "ufgs-26-12-19.pdf")

    assert written.read_bytes() == BODY
    # FR-008a's equality holds because the two digests were taken from two
    # independently obtained sources: the response body and the committed file.
    assert upstream_digest_of_response(written.read_bytes()) == result.upstream_digest


def test_vendoring_removes_the_file_when_the_write_does_not_round_trip(
    policy, tmp_path: Path, monkeypatch
) -> None:
    result = fetch_document(
        FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: _Response(200, BODY)})
    )
    monkeypatch.setattr(
        "model.corpus.retrieve.content_hash_of_file", lambda _path: "sha256:" + "0" * 64
    )
    target = tmp_path / "ufgs-26-12-19.pdf"

    with pytest.raises(RetrievalError, match="does not hold the retrieved bytes"):
        vendor_document(result, target)
    assert not target.exists()


def test_vendoring_into_an_unwritable_destination_is_a_retrieval_error(
    policy, tmp_path: Path
) -> None:
    result = fetch_document(
        FIRST_URL, policy=policy, opener=_Opener({FIRST_URL: _Response(200, BODY)})
    )
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")

    with pytest.raises(RetrievalError) as caught:
        vendor_document(result, blocker / "inside" / "x.pdf")
    assert caught.value.condition == "TRANSPORT_FAILURE"


@pytest.mark.parametrize(
    ("section", "variant", "expected"),
    [
        ("26 12 19", "UNIFIED", "ufgs-26-12-19.pdf"),
        ("26 12 19", "NAVFAC", "ufgs-26-12-19-00-20.pdf"),
    ],
)
def test_the_committed_filename_carries_no_space_or_separator(
    policy, section, variant, expected
) -> None:
    target = next(t for t in policy.retrieval_targets if t.key == (section, variant))
    name = document_filename(target, policy)
    assert name == expected
    assert " " not in name and "/" not in name and "\\" not in name


# ---------------------------------------------------------------------------
# The exclusion ledger (FR-004, VR-026)
# ---------------------------------------------------------------------------


def _record(identifier: str = "UFGS 23 73 13 (2010-08)") -> ExclusionRecord:
    return ExclusionRecord(
        candidate_identifier=identifier,
        source_location="https://host.allowed.test/UFGS%2023%2073%2013.pdf",
        cause="WITHDRAWN_OR_UNRETRIEVABLE",
        decided_on="2026-07-26",
        note="RETIRED_SUPERSEDED upstream; 403 through the documented redirect.",
    )


def test_a_ledger_record_requires_every_component(tmp_path: Path) -> None:
    for field in ("candidate_identifier", "source_location", "note"):
        payload = _record().payload()
        payload[field] = "   "
        with pytest.raises(RetrievalError, match=field):
            ExclusionRecord(**payload)


def test_a_ledger_record_rejects_a_non_string_component() -> None:
    payload = _record().payload()
    payload["note"] = 7
    with pytest.raises(RetrievalError, match="must be a string"):
        ExclusionRecord(**payload)


def test_a_cause_outside_the_closed_enum_is_refused() -> None:
    payload = _record().payload()
    payload["cause"] = "SEEMED_UNIMPORTANT"
    with pytest.raises(RetrievalError, match="FR-004"):
        ExclusionRecord(**payload)


def test_every_closed_cause_is_constructible() -> None:
    for cause in EXCLUSION_CAUSES:
        payload = _record().payload()
        payload["cause"] = cause
        assert ExclusionRecord(**payload).cause == cause


def test_a_decided_on_that_is_not_a_calendar_date_is_refused() -> None:
    payload = _record().payload()
    payload["decided_on"] = "2026-02-31"
    with pytest.raises(RetrievalError, match="not a calendar date"):
        ExclusionRecord(**payload)


def test_a_refusal_becomes_a_ledger_record_naming_its_condition() -> None:
    error = RetrievalError(
        "host is not allow-listed", condition="HOST_NOT_ALLOW_LISTED", hop_index=2
    )
    record = ExclusionRecord.from_error("UFGS 26 12 19 (2024-05)", FIRST_URL, error)

    assert record.cause == "RETRIEVAL_FAILED"
    assert "HOST_NOT_ALLOW_LISTED" in record.note
    assert "hop 2" in record.note


def test_a_ledger_sorts_its_records_and_refuses_a_duplicate_candidate() -> None:
    ledger = ExclusionLedger(
        records=(_record("UFGS 26 35 33 (2008-04)"), _record("UFGS 23 64 00 (2016-11)"))
    )
    assert [record.candidate_identifier for record in ledger.records] == [
        "UFGS 23 64 00 (2016-11)",
        "UFGS 26 35 33 (2008-04)",
    ]

    with pytest.raises(RetrievalError, match="must be unique"):
        ExclusionLedger(records=(_record(), _record()))


def test_a_ledger_refuses_a_record_of_the_wrong_type() -> None:
    with pytest.raises(RetrievalError, match="must be an ExclusionRecord"):
        ExclusionLedger(records=({"candidate_identifier": "x"},))


def test_the_ledger_round_trips_through_its_canonical_serialization(tmp_path: Path) -> None:
    target = tmp_path / "exclusions.json"
    ledger = ExclusionLedger(records=(_record(),), description="every candidate not vendored")
    write_ledger(target, ledger)

    raw = target.read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw
    reread = read_ledger(target)
    assert reread.records == ledger.records
    assert reread.description == ledger.description
    # MS-style stability: writing what was read moves no byte.
    write_ledger(target, reread)
    assert target.read_bytes() == raw


def test_appending_rewrites_the_whole_ledger_in_order(tmp_path: Path) -> None:
    target = tmp_path / "exclusions.json"
    write_ledger(target, ExclusionLedger(records=(_record("UFGS 26 35 33 (2008-04)"),)))
    append_exclusion(_record("UFGS 23 64 00 (2016-11)"), target)

    identifiers = [record.candidate_identifier for record in read_ledger(target).records]
    assert identifiers == ["UFGS 23 64 00 (2016-11)", "UFGS 26 35 33 (2008-04)"]


def test_writing_something_that_is_not_a_ledger_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RetrievalError, match="expected an ExclusionLedger"):
        write_ledger(tmp_path / "exclusions.json", {"exclusions": []})


def test_writing_into_an_unwritable_path_is_a_ledger_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    with pytest.raises(RetrievalError, match="cannot write the exclusion ledger"):
        write_ledger(blocker / "inside" / "exclusions.json", ExclusionLedger(records=(_record(),)))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\xfe not json", "not valid UTF-8 JSON"),
        (b"[]", "must hold an object"),
        (b'{"exclusions": {}}', "must be an array"),
        (b'{"exclusions": ["text"]}', "must be an object"),
        (b'{"exclusions": [{"candidate_identifier": "x", "surprise": 1}]}', "unexpected keys"),
    ],
)
def test_a_malformed_ledger_is_an_error_not_an_empty_one(
    tmp_path: Path, content: bytes, message: str
) -> None:
    target = tmp_path / "exclusions.json"
    target.write_bytes(content)
    with pytest.raises(RetrievalError, match=message):
        read_ledger(target)


def test_a_missing_ledger_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RetrievalError, match="cannot read the exclusion ledger"):
        read_ledger(tmp_path / "absent.json")


def test_the_ledger_path_resolves_inside_the_corpus_root(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "exclusions.json").write_bytes(b"{}")
    assert ledger_path(tmp_path) == (tmp_path / "real" / "exclusions.json").resolve()


def test_an_unresolvable_ledger_path_is_a_retrieval_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "model.corpus.retrieve.resolve_within",
        lambda *_args: (_ for _ in ()).throw(CorpusPathError("escapes the root")),
    )
    with pytest.raises(RetrievalError, match="cannot resolve the exclusion ledger"):
        ledger_path(tmp_path)


# ---------------------------------------------------------------------------
# The console entry point, driven by a stub `fetch_document`
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_corpus(tmp_path: Path, monkeypatch):
    """A corpus root with an empty ledger, and no route to the network."""
    (tmp_path / "real" / "ufgs").mkdir(parents=True)
    write_ledger(tmp_path / "real" / "exclusions.json", ExclusionLedger(records=(_record(),)))
    monkeypatch.setattr("model.corpus.retrieve.corpus_root", lambda root=None: tmp_path)
    monkeypatch.setattr("model.corpus.retrieve.REQUEST_INTERVAL_SECONDS", 0)
    return tmp_path


def _stub_fetch(failing: set[str] | None = None):
    from model.corpus.retrieve import FetchResult, Hop

    refused = failing or set()

    def fetch(url: str, **_kwargs) -> FetchResult:
        if url in refused:
            raise RetrievalError(
                "the section is retired upstream",
                condition="NON_SUCCESS_STATUS",
                hop_index=2,
            )
        body = BODY + url.encode("utf-8")
        return FetchResult(
            requested_url=url,
            final_url=url,
            status=200,
            body=body,
            upstream_digest=upstream_digest_of_response(body),
            retrieved_at="2026-07-26T12:00:00Z",
            hops=(Hop(1, url, ALLOWED_HOST, 200),),
        )

    return fetch


def test_the_entry_point_vendors_every_target_and_reports_one_line_each(
    offline_corpus, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("model.corpus.retrieve.fetch_document", _stub_fetch())

    assert main([]) == 0

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    from model.corpus.sources import POLICY

    assert len(lines) == len(POLICY.retrieval_targets)
    assert {line["retrieval_response_status"] for line in lines} == {200}
    vendored = sorted(path.name for path in (offline_corpus / "real" / "ufgs").glob("*.pdf"))
    assert len(vendored) == len(POLICY.retrieval_targets)
    assert all(line["location"] in vendored for line in lines)
    # The anchor FR-002 names is fetched with the target sections, not apart.
    assert any(line["masterformat_section"] == REQUIRED_ANCHOR_SECTION for line in lines)


def test_a_failed_candidate_is_recorded_in_the_ledger_and_exits_non_zero(
    offline_corpus, monkeypatch, capsys
) -> None:
    from model.corpus.sources import POLICY

    doomed = POLICY.retrieval_targets[0]
    monkeypatch.setattr("model.corpus.retrieve.fetch_document", _stub_fetch({doomed.source_url}))

    assert main() == 1

    captured = capsys.readouterr()
    assert "not vendored" in captured.err
    ledger = read_ledger(offline_corpus / "real" / "exclusions.json")
    recorded = {record.source_location: record for record in ledger.records}
    assert doomed.source_url in recorded
    assert recorded[doomed.source_url].cause == "RETRIEVAL_FAILED"
    assert "NON_SUCCESS_STATUS" in recorded[doomed.source_url].note
    # The batch continued: every other target still produced a line.
    assert len(captured.out.splitlines()) == len(POLICY.retrieval_targets) - 1


def test_a_ledger_that_cannot_be_written_is_reported_rather_than_swallowed(
    offline_corpus, monkeypatch, capsys
) -> None:
    from model.corpus.sources import POLICY

    monkeypatch.setattr(
        "model.corpus.retrieve.fetch_document",
        _stub_fetch({target.source_url for target in POLICY.retrieval_targets}),
    )
    (offline_corpus / "real" / "exclusions.json").unlink()

    assert main() == 1
    assert "cannot record exclusion" in capsys.readouterr().err


def test_the_batch_pauses_between_candidates(offline_corpus, monkeypatch, capsys) -> None:
    """A 26-document run is a paced sequence, not a burst at a public host."""
    slept: list[float] = []
    monkeypatch.setattr("model.corpus.retrieve.fetch_document", _stub_fetch())
    monkeypatch.setattr("model.corpus.retrieve.REQUEST_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr("model.corpus.retrieve.time.sleep", slept.append)

    assert main() == 0
    capsys.readouterr()

    from model.corpus.sources import POLICY

    # One pause between each pair of candidates, and none before the first.
    assert slept == [0.001] * (len(POLICY.retrieval_targets) - 1)


def test_an_unreadable_corpus_root_exits_two(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "model.corpus.retrieve.corpus_root",
        lambda root=None: (_ for _ in ()).throw(CorpusPathError("corpus root does not exist")),
    )
    assert main() == 2
    assert "corpus-retrieve:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The policy itself (FR-002, FR-002a) — the single source of truth
# ---------------------------------------------------------------------------


def test_the_policy_exposes_what_it_read(policy) -> None:
    assert policy.source_hosts == (ALLOWED_HOST, ORIGIN_HOST)
    # Two agency variants of one section number are two documents but one
    # section — the counting rule VR-025 states.
    assert policy.target_sections == ("26 12 19",)
    assert len(policy.targets) == 2
    assert len(policy.retrieval_targets) == 3
    assert policy.anchor_section == REQUIRED_ANCHOR_SECTION
    assert policy.anchor.lead_time_class == "ANCHOR"
    assert policy.anchor.is_anchor is True


@pytest.mark.parametrize(
    ("host", "admitted"),
    [
        (ALLOWED_HOST, True),
        (ALLOWED_HOST.upper(), True),
        (f"  {ALLOWED_HOST} ", True),
        (f"{ALLOWED_HOST}.invalid", False),
        (f"sub.{ALLOWED_HOST}", False),
        ("", False),
        (None, False),
    ],
)
def test_host_membership_is_exact_lowercased_equality(policy, host, admitted) -> None:
    assert policy.allows_host(host) is admitted


def test_an_unknown_agency_variant_names_the_closed_set(policy) -> None:
    with pytest.raises(RetrievalPolicyError, match="VR-021"):
        policy.variant("AFCEC")


@pytest.mark.parametrize(
    ("token", "section", "revision", "expected"),
    [
        ("UNIFIED", "01 33 00", "2021-02", "UFGS 01 33 00 (2021-02)"),
        ("NAVFAC", "26 11 13", "2026-02", "UFGS 26 11 13.00 20 (2026-02)"),
    ],
)
def test_the_document_identifier_is_composed_from_the_policy_row(
    policy, token, section, revision, expected
) -> None:
    assert policy.variant(token).document_identifier(section, revision) == expected


@pytest.mark.parametrize(
    ("section", "revision"),
    [("26 12 19", "2024-5"), ("26 12 19", "2024"), ("261219", "2024-05"), ("26 12 19 ", "2024-05")],
)
def test_the_document_identifier_refuses_a_malformed_component(policy, section, revision) -> None:
    with pytest.raises(RetrievalPolicyError):
        policy.variant("UNIFIED").document_identifier(section, revision)


def test_the_unified_variant_carries_no_suffix() -> None:
    # Suppression rationale: `token` here is the agency-variant token — UNIFIED, USACE, NAVFAC —
    # matched by name rather than by meaning. Nothing in this epic holds a
    # credential, and FR-002a forbids allow-listing a source that needs one.
    with pytest.raises(RetrievalPolicyError, match="must be the empty string"):
        AgencyVariant(token="UNIFIED", section_suffix=".00 10", issuing_body="USACE")  # noqa: S106
    with pytest.raises(RetrievalPolicyError, match="must be non-empty"):
        AgencyVariant(token="USACE", section_suffix="", issuing_body="USACE")  # noqa: S106


def test_a_missing_policy_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RetrievalPolicyError, match="cannot read the retrieval policy"):
        load_policy(tmp_path / "absent.json")


def test_a_policy_that_is_not_utf8_json_is_an_error(tmp_path: Path) -> None:
    target = tmp_path / "policy.json"
    target.write_bytes(b"\xff\xfe{")
    with pytest.raises(RetrievalPolicyError, match="not valid UTF-8 JSON"):
        load_policy(target)


def test_the_policy_path_resolves_inside_the_corpus_root(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "retrieval-policy.json").write_bytes(b"{}")
    assert policy_path(tmp_path) == (tmp_path / "real" / "retrieval-policy.json").resolve()


def test_an_unresolvable_policy_path_is_a_policy_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "model.corpus.sources.resolve_within",
        lambda *_args: (_ for _ in ()).throw(CorpusPathError("escapes the root")),
    )
    with pytest.raises(RetrievalPolicyError, match="cannot resolve the retrieval policy"):
        policy_path(tmp_path)


def _drop(key: str):
    def mutate(document: dict) -> None:
        document.pop(key)

    return mutate


def _set(path: tuple, value):
    def mutate(document: dict) -> None:
        cursor = document
        for step in path[:-1]:
            cursor = cursor[step]
        cursor[path[-1]] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set(("source_hosts",), "www.wbdg.org"), "must be an array"),
        (_set(("source_hosts",), []), "must not be empty"),
        (_set(("source_hosts",), ["WWW.WBDG.ORG"]), "must match"),
        (_set(("source_hosts",), [ALLOWED_HOST, ALLOWED_HOST]), "repeats"),
        (_set(("agency_variants",), []), "must be an object"),
        (_set(("agency_variants",), {}), "must not be empty"),
        (
            _set(("agency_variants", "NAVFAC", "section_suffix"), ".0020"),
            "section_suffix must match",
        ),
        (_set(("agency_variants", "NAVFAC", "issuing_body"), ""), "must not be empty"),
        (_set(("agency_variants", "NAVFAC", "issuing_body"), 7), "must be a string"),
        (_set(("target_sections",), []), "must not be empty"),
        (_set(("target_sections", 0), ["not", "an", "object"]), "must be an object"),
        (_set(("target_sections", 0, "masterformat_section"), "26-12-19"), "must match"),
        (_set(("target_sections", 0, "title"), "  "), "must not be empty"),
        (_set(("target_sections", 0, "resolution_verified_on"), "26 July 2026"), "must match"),
        (_set(("target_sections", 0, "agency_variant"), "AFCEC"), "outside the closed set"),
        (
            _set(("target_sections", 0, "source_url"), "http://host.allowed.test/x.pdf"),
            "must be https",
        ),
        (
            _set(("target_sections", 0, "source_url"), "https://elsewhere.invalid/x.pdf"),
            "is not in source_hosts",
        ),
        (_set(("anchor_section",), "01 33 01"), "anchor_section must be"),
        (_set(("anchor", "masterformat_section"), "01 33 13"), "does not equal"),
        (_drop("anchor_section"), "anchor_section must be a string"),
    ],
)
def test_a_malformed_policy_is_refused_with_the_rule_it_broke(
    tmp_path: Path, mutate, message: str
) -> None:
    with pytest.raises(RetrievalPolicyError, match=message):
        _load_broken(tmp_path, mutate, "broken.json")


def test_a_policy_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "policy.json"
    target.write_bytes(b"[]")
    with pytest.raises(RetrievalPolicyError, match="must be an object"):
        load_policy(target)


def test_a_target_carrying_an_unexpected_key_is_refused(tmp_path: Path) -> None:
    document = _policy_document()
    document["target_sections"][0]["surprise"] = "value"
    with pytest.raises(RetrievalPolicyError, match="unexpected keys"):
        load_policy(_write_policy(tmp_path, document))


def test_two_variants_sharing_one_suffix_are_refused(tmp_path: Path) -> None:
    document = _policy_document()
    document["agency_variants"]["USACE"] = {"section_suffix": ".00 20", "issuing_body": "USACE"}
    with pytest.raises(RetrievalPolicyError, match="share one section_suffix"):
        load_policy(_write_policy(tmp_path, document))


def test_two_policy_entries_describing_one_document_are_refused(tmp_path: Path) -> None:
    document = _policy_document()
    document["target_sections"].append(deepcopy(document["target_sections"][0]))
    with pytest.raises(RetrievalPolicyError, match="same document"):
        load_policy(_write_policy(tmp_path, document))


def test_the_anchor_may_not_also_be_a_target_section(tmp_path: Path) -> None:
    document = _policy_document()
    anchor_as_target = deepcopy(document["target_sections"][0])
    anchor_as_target["masterformat_section"] = REQUIRED_ANCHOR_SECTION
    anchor_as_target["agency_variant"] = "NAVFAC"
    document["target_sections"].append(anchor_as_target)
    with pytest.raises(RetrievalPolicyError, match="is also listed under"):
        load_policy(_write_policy(tmp_path, document))


def test_the_committed_policy_is_the_one_the_client_uses() -> None:
    """`sources.py` restates no target list; it exposes the committed file."""
    from model.corpus.sources import POLICY, RETRIEVAL_TARGETS, SOURCE_HOSTS, TARGET_SECTIONS

    assert POLICY.target_sections == TARGET_SECTIONS
    assert POLICY.retrieval_targets == RETRIEVAL_TARGETS
    assert POLICY.source_hosts == SOURCE_HOSTS
    assert POLICY.anchor_section == REQUIRED_ANCHOR_SECTION
    assert len(POLICY.target_sections) >= 6
    assert len(POLICY.retrieval_targets) >= 20
