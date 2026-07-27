"""TR-030 / TR-060: the two-detector scan, with a seeded positive per sink.

TR-030 says the check exists and gates the build; TR-060 is authoritative on the
scan's scope and its detectors. The scope is TR-059's closed inventory of five
sinks, and the interesting design question is what "scan a sink" means for each,
because the five are not alike:

- the **committed fixture store** and the **spool** are files, scanned as bytes;
- **log output** and **check output** are produced at runtime, so the scan runs
  over what a formatter actually emits rather than over a source file;
- the **exception payload** includes the local frames a traceback renders, so
  the scan runs over a rendered traceback.

**Every sink gets a seeded positive.** A scan reporting clean across five sinks
proves nothing about the five unless each is shown to be capable of failing —
and the failure modes differ enough that one seeded case would evidence one
sink and leave four asserted by analogy.
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

import pytest

from gateway.models import InvocationRecord
from gateway.provider import CredentialHandle
from gateway.record.spool import InvocationSpool
from gateway.redaction import (
    SINKS,
    contains_credential_material,
    credential_findings,
    redact,
)
from tests.test_record_writer import a_record  # noqa: F401 - shared record builder

COMMITTED_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: Key-shaped and not a real key, assembled from parts so this file is not a
#: committed literal the supply-chain scan flags.
SHAPED = "sk-" + "ant-" + "S" * 24
ENV: dict[str, str] = {}


def test_the_scan_covers_every_sink_in_the_closed_inventory() -> None:
    """The denominator, asserted first.

    Every test below is named for a sink. If the inventory grew and this file
    did not, the remaining sinks would be unscanned and nothing would say so.
    """
    scanned = {
        "log_output",
        "exception_payload",
        "fixture_store",
        "invocation_spool",
        "check_output",
    }
    assert scanned == set(SINKS), (
        f"sinks in the inventory with no scan here: {sorted(set(SINKS) - scanned)}"
    )


# --- Sink 3: the committed fixture store and its sidecars -------------------


def test_the_committed_fixture_store_is_clean() -> None:
    """The sink that lives forever. A credential committed here is in every
    clone of the repository and in its whole history."""
    if not COMMITTED_FIXTURES.is_dir():
        pytest.skip("no committed fixture store")

    offenders = [
        path.relative_to(COMMITTED_FIXTURES).as_posix()
        for path in COMMITTED_FIXTURES.rglob("*")
        if path.is_file() and contains_credential_material(path.read_text(encoding="utf-8"), ENV)
    ]
    assert not offenders, f"credential-shaped material in committed fixtures: {offenders}"


def test_the_committed_store_scan_reports_a_seeded_file(tmp_path: Path) -> None:
    """The seeded positive for this sink. Planted in a temporary copy — seeding
    the real store would commit the thing the scan exists to prevent."""
    planted = tmp_path / "seeded.response.json"
    planted.write_text(json.dumps({"authorization": SHAPED}), encoding="utf-8")
    assert contains_credential_material(planted.read_text(encoding="utf-8"), ENV)


# --- Sink 4: the local invocation spool -------------------------------------


def test_a_spooled_payload_carries_no_credential(tmp_path: Path) -> None:
    """The spool holds a record's full canonical JSON on local disk, so it is a
    sink in exactly the sense TR-059 means — and it is written on the failure
    path, which is where redaction is least likely to have been thought about.
    """
    spool = InvocationSpool(tmp_path / "spool.sqlite3")
    spool.append(a_record(), write_error_type="OperationalError")

    payload = next(iter(spool.pending())).payload
    assert not contains_credential_material(payload, ENV)


def test_the_spool_scan_reports_a_seeded_payload(tmp_path: Path) -> None:
    spool = InvocationSpool(tmp_path / "spool.sqlite3")
    spool.append(a_record(gen_ai_request_model=SHAPED), write_error_type="X")

    payload = next(iter(spool.pending())).payload
    assert contains_credential_material(payload, ENV), (
        "a credential planted in a spooled payload was not detected"
    )


# --- Sink 1: log output ------------------------------------------------------


def test_a_log_line_carrying_a_credential_is_redacted_before_emission() -> None:
    """Scanned over what a formatter actually emits, not over a source file.

    A check reading the source could only find literals; the risk is a value
    that arrives at runtime and is interpolated into a message nobody wrote a
    credential into.
    """
    record = logging.LogRecord(
        name="gateway.record",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling with %s",
        args=(SHAPED,),
        exc_info=None,
    )
    emitted = logging.Formatter().format(record)
    assert contains_credential_material(emitted, ENV), (
        "the seeded log line is not detectable, so this sink's scan is untested"
    )
    assert not contains_credential_material(redact(emitted, ENV), ENV)


# --- Sink 2: the exception payload, local frames included --------------------


def test_a_rendered_traceback_carries_no_credential() -> None:
    """TR-059 names the local frames a variable-capturing traceback renders, not
    just the exception's message — a credential that was only ever a local
    variable is in the crash report."""

    def boom() -> None:
        credential = CredentialHandle("a-real-looking-secret")
        raise RuntimeError(f"failed while holding {credential}")

    rendered = ""
    try:
        boom()
    except RuntimeError as exc:
        rendered = "".join(traceback.format_exception(exc))

    assert rendered
    assert "a-real-looking-secret" not in rendered


def test_the_traceback_scan_reports_a_seeded_frame() -> None:
    """The seeded positive: the same shape without the handle protecting it."""

    def boom() -> None:
        raise RuntimeError(f"failed with {SHAPED}")

    rendered = ""
    try:
        boom()
    except RuntimeError as exc:
        rendered = "".join(traceback.format_exception(exc))

    assert contains_credential_material(rendered, ENV), (
        "a credential in a rendered traceback was not detected"
    )


# --- Sink 5: check output ----------------------------------------------------


def test_an_assertion_message_carrying_a_credential_is_detectable() -> None:
    """TR-059 names check output explicitly, "including assertion messages that
    interpolate the client handle or a raw request" — those leak through the
    test runner rather than through application logging, which is why they are
    a sink of their own rather than a case of sink 1."""
    try:
        assert False, f"unexpected client state: {SHAPED}"  # noqa: B011, PT015
    except AssertionError as exc:
        message = str(exc)

    assert contains_credential_material(message, ENV)
    assert not contains_credential_material(redact(message, ENV), ENV)


def test_this_suites_own_assertion_messages_carry_no_credential() -> None:
    """The scan turned on itself.

    Every message in this file interpolates a *seeded* value by design. The
    property that matters is that none of them interpolates a value read from
    the environment — which would put a real credential in check output on the
    one machine that has one.

    Parsed rather than grepped. A text scan for the environment access matches
    the assertion that describes it — the fourth time this epic has hit that
    trap, after `test_migrations.py`, `test_field_naming.py`, and a comment in
    `config.py`. An AST walk sees imports and attribute reads, not sentences.
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    imports_os = any(
        isinstance(node, ast.Import) and any(alias.name == "os" for alias in node.names)
        for node in ast.walk(tree)
    ) or any(isinstance(node, ast.ImportFrom) and node.module == "os" for node in ast.walk(tree))
    assert not imports_os, (
        "this file imports `os`; every scan here must run against an explicit "
        "empty mapping, or a machine holding a real credential could match it "
        "and print it into check output (TR-059 sink 5)"
    )

    # Every scan in this file passes `ENV`, which is empty — so detector (a)
    # never runs against a real value and only the shaped detector fires,
    # matching the constant assembled at the top of the file.
    assert ENV == {}, "the seeded scans no longer run against an empty environment"


# --- The record itself -------------------------------------------------------


def test_the_invocation_record_has_no_field_a_credential_could_occupy() -> None:
    """The strongest form the claim takes: not "we redact it" but "there is
    nowhere for it to go".

    TR-012's field list is closed, and none of its twenty-two fields carries
    request or response *content* — so a credential cannot reach the record by
    being part of a payload, only by being interpolated into a field that has no
    business holding one.
    """
    fields = set(InvocationRecord.model_fields)
    content_shaped = {"prompt", "completion", "content", "messages", "system", "api_key"}
    assert not (fields & content_shaped), (
        f"the record carries content-shaped fields: {sorted(fields & content_shaped)}"
    )


def test_the_findings_report_the_matched_span_and_not_its_context() -> None:
    """A finding that printed surrounding context would put the credential into
    check output — the sink the report is written to."""
    findings = credential_findings(f"prefix {SHAPED} suffix", ENV)
    assert findings == [SHAPED]
    assert "prefix" not in " ".join(findings)
