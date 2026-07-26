"""The opt-in re-verification job, exercised against local fixtures only.

FR-008b. The eighth of `tasks.md` Phase 3's conditions lands here: a source
whose bytes no longer hash to the recorded `upstream_digest`. The other seven
are the shared client's and are covered in `test_corpus_retrieve.py`, which is
the point of FR-002b binding both network paths — there is one hop walker, so
there is one place its rules are tested.

Nothing here opens a socket. A stub opener answers every request, so the suite
runs offline and two runs see the same bytes; a job whose entire purpose is
detecting that a remote answer changed cannot be tested against a remote answer
that may change for unrelated reasons.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_corpus_retrieve import (
    ALLOWED_HOST,
    BODY,
    FIRST_URL,
    ORIGIN_URL,
    _Opener,
    _policy_document,
    _Response,
    _write_policy,
)

from model.corpus.manifest import (
    Manifest,
    RealEntry,
    RealLicenseBasis,
    upstream_digest_of_response,
    write_manifest,
)
from model.corpus.paths import CorpusPathError
from model.corpus.reverify import (
    STATUS_DIVERGED,
    STATUS_MATCH,
    STATUS_UNREACHABLE,
    RecordedSource,
    ReverificationError,
    ReverificationOutcome,
    main,
    recorded_sources,
    reverify_source,
)
from model.corpus.sources import load_policy

RECORDED_DIGEST = upstream_digest_of_response(BODY)
OTHER_BODY = b"%PDF-1.7\nthe source published a newer revision\n"


@pytest.fixture
def policy(tmp_path: Path):
    return load_policy(_write_policy(tmp_path, _policy_document(), "policy.json"))


def _source(location: str = "ufgs-26-12-19.pdf", digest: str = RECORDED_DIGEST) -> RecordedSource:
    return RecordedSource(
        location_id="real/ufgs",
        location=location,
        source_location=FIRST_URL,
        upstream_digest=digest,
    )


def _real_entry(location: str, body: bytes, section: str = "26 12 19") -> RealEntry:
    digest = upstream_digest_of_response(body)
    return RealEntry(
        location=location,
        license_basis=RealLicenseBasis(document_identifier=f"UFGS {section} (2024-05)"),
        content_hash=digest,
        source_location=FIRST_URL,
        retrieval_response_status=200,
        retrieved_at="2026-07-26T12:00:00Z",
        issuing_body="USACE / NAVFAC / AFCEC",
        masterformat_section=section,
        agency_variant="UNIFIED",
        revision_date="2024-05",
        upstream_digest=digest,
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A corpus root holding one REAL location with one recorded source."""
    location = tmp_path / "real" / "ufgs"
    location.mkdir(parents=True)
    (location / "ufgs-26-12-19.pdf").write_bytes(BODY)
    write_manifest(
        location,
        Manifest(location_id="real/ufgs", entries=(_real_entry("ufgs-26-12-19.pdf", BODY),)),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Condition 8: a digest diverging from the recorded `upstream_digest`
# ---------------------------------------------------------------------------


def test_a_source_serving_different_bytes_is_reported_as_diverged(policy) -> None:
    opener = _Opener({FIRST_URL: _Response(200, OTHER_BODY)})
    outcome = reverify_source(_source(), policy=policy, opener=opener)

    assert outcome.status == STATUS_DIVERGED
    assert outcome.diverged is True
    assert outcome.observed_digest == upstream_digest_of_response(OTHER_BODY)
    assert outcome.observed_digest != outcome.source.upstream_digest
    # The wording is load-bearing: a divergence means the source changed, never
    # that the committed copy did.
    assert "change at the source" in outcome.detail
    assert "not corpus drift" in outcome.detail


def test_a_source_still_serving_the_recorded_bytes_matches(policy) -> None:
    opener = _Opener({FIRST_URL: _Response(200, BODY)})
    outcome = reverify_source(_source(), policy=policy, opener=opener)

    assert outcome.status == STATUS_MATCH
    assert outcome.observed_digest == RECORDED_DIGEST
    assert f"{len(BODY)} bytes" in outcome.detail


def test_the_comparison_is_against_the_recorded_digest_not_the_committed_file(
    policy, corpus: Path
) -> None:
    """VR-012 asks whether the *file* changed; this job asks about the source."""
    (corpus / "real" / "ufgs" / "ufgs-26-12-19.pdf").write_bytes(b"a locally edited file")
    opener = _Opener({FIRST_URL: _Response(200, BODY)})

    outcome = reverify_source(recorded_sources(corpus)[0], policy=policy, opener=opener)
    assert outcome.status == STATUS_MATCH


# ---------------------------------------------------------------------------
# The re-fetch is bound by FR-002b exactly as first retrieval is
# ---------------------------------------------------------------------------


def test_the_refetch_walks_the_same_redirect_chain_under_the_same_rules(policy) -> None:
    opener = _Opener(
        {FIRST_URL: _Response(301, location=ORIGIN_URL), ORIGIN_URL: _Response(200, BODY)}
    )
    outcome = reverify_source(_source(), policy=policy, opener=opener)

    assert opener.requested == [FIRST_URL, ORIGIN_URL]
    assert outcome.status == STATUS_MATCH


@pytest.mark.parametrize(
    "response",
    [
        _Response(302, location="https://elsewhere.invalid/x.pdf"),
        _Response(302, location=f"http://{ALLOWED_HOST}/x.pdf"),
        _Response(403),
    ],
)
def test_a_source_the_shared_client_refuses_is_unreachable_not_diverged(policy, response) -> None:
    """A refused hop is not evidence that the source changed."""
    outcome = reverify_source(_source(), policy=policy, opener=_Opener({FIRST_URL: response}))

    assert outcome.status == STATUS_UNREACHABLE
    assert outcome.observed_digest is None
    assert outcome.detail


def test_a_recorded_url_off_the_allow_list_is_refused_by_the_shared_policy(policy) -> None:
    """The allow-list governs the re-fetch too, not only first retrieval."""
    rogue = RecordedSource(
        location_id="real/ufgs",
        location="ufgs-26-12-19.pdf",
        source_location="https://elsewhere.invalid/x.pdf",
        upstream_digest=RECORDED_DIGEST,
    )
    opener = _Opener({})
    outcome = reverify_source(rogue, policy=policy, opener=opener)

    assert outcome.status == STATUS_UNREACHABLE
    assert "HOST_NOT_ALLOW_LISTED" in outcome.detail
    assert opener.requested == []


def test_something_that_is_not_a_recorded_source_is_refused(policy) -> None:
    with pytest.raises(ReverificationError, match="expected a RecordedSource"):
        reverify_source({"source_location": FIRST_URL}, policy=policy)


# ---------------------------------------------------------------------------
# Reading the recorded sources out of the committed manifests
# ---------------------------------------------------------------------------


def test_every_real_entry_contributes_one_recorded_source(corpus: Path) -> None:
    sources = recorded_sources(corpus)
    assert len(sources) == 1
    assert sources[0].location_id == "real/ufgs"
    assert sources[0].source_location == FIRST_URL
    assert sources[0].upstream_digest == RECORDED_DIGEST
    assert sources[0].key == ("real/ufgs", "ufgs-26-12-19.pdf")


def test_sources_are_ordered_so_two_runs_report_in_one_order(tmp_path: Path) -> None:
    location = tmp_path / "real" / "ufgs"
    location.mkdir(parents=True)
    entries = tuple(
        _real_entry(f"ufgs-26-12-{index}.pdf", BODY + str(index).encode()) for index in (21, 19, 20)
    )
    write_manifest(location, Manifest(location_id="real/ufgs", entries=entries))

    assert [source.location for source in recorded_sources(tmp_path)] == [
        "ufgs-26-12-19.pdf",
        "ufgs-26-12-20.pdf",
        "ufgs-26-12-21.pdf",
    ]


def test_a_synthetic_location_contributes_nothing(corpus: Path) -> None:
    """The layer asymmetry working: a generated document has no source."""
    synthetic = corpus / "synthetic" / "PRJ-001"
    synthetic.mkdir(parents=True)
    (synthetic / "manifest.json").write_bytes(
        json.dumps(
            {
                "location_id": "synthetic/PRJ-001",
                "layer": "SYNTHETIC",
                "project_id": "PRJ-001",
                "entries": [{"location": "t-001.pdf"}],
            }
        ).encode("utf-8")
    )
    assert len(recorded_sources(corpus)) == 1


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"\xff\xfe not json", "not valid UTF-8 JSON"),
        (b"[]", "must hold an object"),
        (b'{"layer": "REAL", "entries": {}}', "entries must be an array"),
        (b'{"layer": "REAL", "entries": ["text"]}', "must be an object"),
        (b'{"layer": "REAL", "entries": [{"source_location": "x"}]}', "location must be"),
        (
            b'{"layer": "REAL", "entries": [{"location": "a.pdf", "upstream_digest": "d"}]}',
            "source_location must be",
        ),
        (
            b'{"layer": "REAL", "entries": [{"location": "a.pdf", "source_location": "u"}]}',
            "upstream_digest must be",
        ),
    ],
)
def test_a_malformed_manifest_stops_the_job_rather_than_shortening_it(
    tmp_path: Path, document: bytes, message: str
) -> None:
    location = tmp_path / "real" / "ufgs"
    location.mkdir(parents=True)
    (location / "manifest.json").write_bytes(document)

    with pytest.raises(ReverificationError, match=message):
        recorded_sources(tmp_path)


def test_an_unreadable_corpus_is_a_reverification_error(tmp_path: Path) -> None:
    with pytest.raises(ReverificationError, match="cannot read the corpus"):
        recorded_sources(tmp_path / "absent")


def test_an_unreadable_manifest_is_a_reverification_error(tmp_path: Path, monkeypatch) -> None:
    location = tmp_path / "real" / "ufgs"
    location.mkdir(parents=True)
    (location / "manifest.json").write_bytes(b"{}")

    def _refuse(_self, *_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_bytes", _refuse)
    with pytest.raises(ReverificationError, match="cannot read"):
        recorded_sources(tmp_path)


def test_a_corpus_path_failure_is_reported_as_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "model.corpus.reverify.discover_locations",
        lambda _root: (_ for _ in ()).throw(CorpusPathError("cannot read the tree")),
    )
    with pytest.raises(ReverificationError, match="cannot read the corpus"):
        recorded_sources(tmp_path)


# ---------------------------------------------------------------------------
# The outcome type's own invariants
# ---------------------------------------------------------------------------


def test_an_outcome_outside_the_closed_status_set_is_refused() -> None:
    with pytest.raises(ReverificationError, match="status must be one of"):
        ReverificationOutcome(source=_source(), status="PROBABLY_FINE")


def test_an_unreachable_outcome_carries_no_observed_digest() -> None:
    with pytest.raises(ReverificationError, match="has no observed digest"):
        ReverificationOutcome(
            source=_source(), status=STATUS_UNREACHABLE, observed_digest=RECORDED_DIGEST
        )


def test_a_reachable_outcome_must_carry_what_it_observed() -> None:
    with pytest.raises(ReverificationError, match="carries an observed digest"):
        ReverificationOutcome(source=_source(), status=STATUS_MATCH)


# ---------------------------------------------------------------------------
# The console entry point and the release record's material (SC-009)
# ---------------------------------------------------------------------------


def _run(corpus: Path, opener, monkeypatch, capsys, policy=None):
    monkeypatch.setattr("model.corpus.reverify.REQUEST_INTERVAL_SECONDS", 0)
    code = main(root=corpus, opener=opener, policy=policy)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return code, lines[:-1], lines[-1]


def test_the_job_states_no_divergence_explicitly(policy, corpus: Path, monkeypatch, capsys) -> None:
    """ "Or explicitly none" is why the summary prints when nothing diverged."""
    code, per_source, summary = _run(
        corpus, _Opener({FIRST_URL: _Response(200, BODY)}), monkeypatch, capsys, policy
    )

    assert code == 0
    assert [entry["status"] for entry in per_source] == [STATUS_MATCH]
    assert summary["sources_recorded"] == 1
    assert summary["sources_refetched"] == 1
    assert summary["divergences"] == []
    assert summary["unreachable"] == []
    assert "no digest divergence" in summary["statement"]


def test_the_job_reports_every_divergence_and_exits_non_zero(
    policy, corpus: Path, monkeypatch, capsys
) -> None:
    code, per_source, summary = _run(
        corpus, _Opener({FIRST_URL: _Response(200, OTHER_BODY)}), monkeypatch, capsys, policy
    )

    assert code == 1
    assert per_source[0]["recorded_upstream_digest"] == RECORDED_DIGEST
    assert per_source[0]["observed_digest"] == upstream_digest_of_response(OTHER_BODY)
    assert len(summary["divergences"]) == 1
    assert "1 digest divergence(s) at the source" in summary["statement"]


def test_an_unreachable_source_is_counted_apart_from_a_divergence(
    policy, corpus: Path, monkeypatch, capsys
) -> None:
    code, per_source, summary = _run(
        corpus, _Opener({FIRST_URL: _Response(503)}), monkeypatch, capsys, policy
    )

    assert code == 1
    assert per_source[0]["status"] == STATUS_UNREACHABLE
    assert summary["sources_refetched"] == 0
    assert summary["divergences"] == []
    assert len(summary["unreachable"]) == 1
    assert "1 unreachable" in summary["statement"]


def test_the_job_pauses_between_sources(policy, tmp_path: Path, monkeypatch, capsys) -> None:
    location = tmp_path / "real" / "ufgs"
    location.mkdir(parents=True)
    entries = tuple(
        _real_entry(f"ufgs-26-12-{index}.pdf", BODY, section=f"26 12 {index}") for index in (19, 21)
    )
    write_manifest(location, Manifest(location_id="real/ufgs", entries=entries))

    slept: list[float] = []
    monkeypatch.setattr("model.corpus.reverify.REQUEST_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr("model.corpus.reverify.time.sleep", slept.append)

    assert main(root=tmp_path, opener=_Opener({}, default=_Response(200, BODY)), policy=policy) == 0
    capsys.readouterr()
    assert slept == [0.001]


def test_a_corpus_that_cannot_be_read_exits_two(tmp_path: Path, capsys) -> None:
    assert main(root=tmp_path / "absent") == 2
    assert "corpus-reverify:" in capsys.readouterr().err


def test_the_entry_point_takes_no_arguments_and_ignores_any(
    policy, corpus: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("model.corpus.reverify.REQUEST_INTERVAL_SECONDS", 0)
    opener = _Opener({FIRST_URL: _Response(200, BODY)})
    assert main(["--whatever"], root=corpus, opener=opener, policy=policy) == 0
    capsys.readouterr()
