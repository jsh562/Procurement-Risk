"""Where E007's emitted reports live, resolved in one place.

Follows `model.procurement.paths`: every filename this epic writes is named
here and nowhere else, and every function takes an optional report root so a
test can drive the real write path under `tmp_path` without touching the
checkout. DV-041 asserts the emitted set is *exactly* FR-040's three kinds,
which is checkable only against a closed enumeration of filename forms.

Unlike `model.procurement.paths`, one filename here is built from **data** — an
as-of date, a digest and a timestamp — so each component is validated before it
reaches a path. FR-037 makes the refusal report one file per attempt, never
overwritten, so the identifier is what carries that guarantee.

Stdlib only, following the module it is modelled on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

__all__ = [
    "ATTEMPT_TIMESTAMP_FORMAT",
    "DIGEST_PREFIX_LENGTH",
    "REFUSAL_REPORT_PREFIX",
    "REPORT_DIR_PARTS",
    "REPORT_ROOT",
    "REPORT_SUFFIX",
    "REPO_ROOT",
    "RUN_REPORT_PREFIX",
    "ForecastPathError",
    "refusal_report_path",
    "refused_attempt_id",
    "run_report_path",
]


class ForecastPathError(ValueError):
    """Raised when a report path cannot be formed from the values given.

    One type, as `SerializeError` and `CorpusPathError` are: every failure here
    says the same thing — this attempt has no well-formed identity, so writing
    its evidence under a name derived from it would misfile the record FR-037
    exists to keep.
    """


# paths.py sits at src/model/src/model/forecast/, so the repository root is six
# levels up — the entry's own src-layout repeats the package name. Same
# derivation as `model/procurement/paths.py`, deliberately: two modules
# disagreeing about where the repository begins would be two answers to one
# question.
REPO_ROOT = Path(__file__).resolve().parents[5]

#: Held as path *parts* rather than as a string with separators, so no caller
#: has to know which slash this platform uses. Reports sit beside the other
#: emitted artifacts under `data/`, in a tree of their own: they are job output
#: rather than data of record, and mixing them into `data/procurement/` would
#: put run evidence inside the directory `procurement-validate` enumerates.
REPORT_DIR_PARTS = ("data", "forecast-reports")

#: The default root every emitted report resolves against. A *directory*, not a
#: repository root, because `tests/forecast/conftest.py`'s `report_root` fixture
#: hands one directory to the job and then asserts on exactly what landed in it
#: — a job that had to create intermediate directories first would make "which
#: files were emitted" a question about directory creation.
REPORT_ROOT = REPO_ROOT.joinpath(*REPORT_DIR_PARTS)

#: JSON rather than Markdown. SC-026's absence check is a structural predicate
#: over the run report's declared fields rather than a term search (FR-040), and
#: DV-041 validates every field against a closed schema; both read a parsed
#: document. Named once here so the two report writers cannot disagree.
REPORT_SUFFIX = ".json"

RUN_REPORT_PREFIX = "run-report"
REFUSAL_REPORT_PREFIX = "refusal-report"

#: How many leading hex characters of the input row hash enter the refused
#: attempt's name. Sixty-four bits identifies *which input* the attempt read at
#: a glance while keeping the filename short enough to survive a deep temporary
#: directory on Windows; uniqueness between two attempts is carried by the
#: timestamp, and the full digest is recorded inside the report itself.
DIGEST_PREFIX_LENGTH = 16

#: ISO 8601 basic form, UTC, to the microsecond. Basic rather than extended
#: because the extended form's colons are not legal in a Windows filename, and
#: to the microsecond because two refusals of one input in a retry loop are
#: exactly the case FR-037 says must both survive.
ATTEMPT_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"

_DIGEST_ALGORITHM_PREFIX = "sha256:"
_HEX_DIGITS = frozenset("0123456789abcdef")


def _root(report_root: Path | str | None) -> Path:
    """The directory to resolve against: the caller's, or this checkout's.

    `None` means "this checkout's report tree" rather than "the current working
    directory". A relative default would make every path here depend on where
    the job was started, and `forecast-fit` runs from `src/model` while the
    reports belong at the repository root.
    """
    return REPORT_ROOT if report_root is None else Path(report_root)


def _run_id_component(run_id: uuid.UUID | str) -> str:
    """The run's identifier, canonicalized and proved to be one.

    Parsed rather than trusted: the value reaches this module from a database
    round-trip or from a caller's string, and an unvalidated one could carry a
    path separator straight into a filename.
    """
    if isinstance(run_id, uuid.UUID):
        return str(run_id)
    if not isinstance(run_id, str):
        raise ForecastPathError(
            f"a run identifier is a UUID or its string form, found {type(run_id).__name__}"
        )
    try:
        return str(uuid.UUID(run_id))
    except ValueError as exc:
        raise ForecastPathError(f"{run_id!r} is not a UUID: {exc}") from exc


def _as_of_component(as_of_date: date) -> str:
    """The run's as-of date as `YYYY-MM-DD`.

    A `datetime` is refused rather than truncated. It is a subclass of `date`,
    so an accidental one would otherwise pass the type test and render a second
    timestamp into a name that already carries the attempt's own.
    """
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise ForecastPathError(
            f"an as-of date is a `datetime.date`, found {type(as_of_date).__name__}; the "
            f"attempt's instant is carried by `attempted_at`"
        )
    return as_of_date.isoformat()


def _digest_component(input_data_hash: str) -> str:
    """The leading hex of the input row hash, with its algorithm prefix removed.

    The prefix is stripped because `sha256:` carries a colon, which is not a
    legal Windows filename character; the remainder is checked to be hex so a
    truncated, empty or path-bearing value fails here rather than producing a
    plausible filename for evidence nobody can trace back.
    """
    if not isinstance(input_data_hash, str):
        raise ForecastPathError(
            f"an input row hash is a string, found {type(input_data_hash).__name__}"
        )
    digest = input_data_hash.removeprefix(_DIGEST_ALGORITHM_PREFIX)
    if len(digest) < DIGEST_PREFIX_LENGTH or not _HEX_DIGITS.issuperset(digest):
        raise ForecastPathError(
            f"{input_data_hash!r} is not a `sha256:`-prefixed lowercase hex digest; a "
            f"refused attempt is named after the input it read, so a malformed digest has "
            f"no name to file its evidence under"
        )
    return digest[:DIGEST_PREFIX_LENGTH]


def _attempt_component(attempted_at: datetime) -> str:
    """The attempt's instant, in UTC, as ISO 8601 basic form.

    Timezone-aware only. A naive datetime would be rendered as though it were
    UTC, so two refusals recorded on machines in different zones would sort and
    read as though they had happened hours apart from when they did.
    """
    if not isinstance(attempted_at, datetime):
        raise ForecastPathError(
            f"an attempt timestamp is a `datetime.datetime`, found {type(attempted_at).__name__}"
        )
    if attempted_at.tzinfo is None or attempted_at.utcoffset() is None:
        raise ForecastPathError(
            "an attempt timestamp must be timezone-aware; a naive one would be filed as "
            "though the machine that produced it ran on UTC"
        )
    return attempted_at.astimezone(UTC).strftime(ATTEMPT_TIMESTAMP_FORMAT)


def refused_attempt_id(as_of_date: date, input_data_hash: str, attempted_at: datetime) -> str:
    """The identity of one refused attempt (FR-037).

    A refused run has no `run_id` — FR-017 forbids writing the run row — so the
    identifier is constructed from the three facts that do exist: what the run
    was asked to forecast, what it read, and when it tried. The timestamp is
    what makes two refusals of one input distinguishable, which is the case a
    retry loop produces and the case the history matters most in.
    """
    return "-".join(
        (
            _as_of_component(as_of_date),
            _digest_component(input_data_hash),
            _attempt_component(attempted_at),
        )
    )


def run_report_path(run_id: uuid.UUID | str, report_root: Path | str | None = None) -> Path:
    """`<report root>/run-report-<run_id>.json` — the report of a run that ships.

    Named by `run_id` because a shipped run has one, and because that is the
    identifier its stored artifacts, its manifest and the job's single stdout
    line (FR-039) all carry: a reader holding the run identifier reaches the
    report without consulting an index.
    """
    name = f"{RUN_REPORT_PREFIX}-{_run_id_component(run_id)}{REPORT_SUFFIX}"
    return _root(report_root) / name


def refusal_report_path(
    as_of_date: date,
    input_data_hash: str,
    attempted_at: datetime,
    report_root: Path | str | None = None,
) -> Path:
    """`<report root>/refusal-report-<attempt id>.json` — one file per attempt.

    Resolved into the same directory as the run reports, per FR-037: the
    refusal report and the job's stderr are the only surviving record of why a
    run refused (G-8), and retaining that record somewhere the run reports are
    not is how it gets swept up. Nothing here overwrites: two attempts differ in
    the timestamp component, so a later refusal cannot take an earlier one's
    name.
    """
    attempt = refused_attempt_id(as_of_date, input_data_hash, attempted_at)
    return _root(report_root) / f"{REFUSAL_REPORT_PREFIX}-{attempt}{REPORT_SUFFIX}"
