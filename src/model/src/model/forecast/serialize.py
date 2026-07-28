"""The two digests FR-023 refuses on: the input row hash and the split hash.

**The canonicalization rule set is reused, never re-authored.** Both digests are
`sha256_of_bytes(canonical_bytes(...))` — sorted keys, compact separators,
`ensure_ascii=False`, UTF-8 — taken from `model.roster.reader`, which
`model.procurement.serialize` already delegates to for the same reason (E005
AD-001): the rule set exists twice in this repository identically, and a third
copy would be the defect rather than the fix. What this module adds is the part
neither of them needs — a rendering of the row types the delivered schema
returns, and the canonical order the digests are defined over.

`created_at` is outside the serialization structurally rather than by omission:
`HASHED_LINE_FIELDS` is E005's compared-content projection, `read.py` never
selects the column, and so it cannot reach a payload here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from model.corpus.manifest import sha256_of_bytes
from model.forecast.read import (
    HASHED_EVENT_FIELDS,
    HASHED_LINE_FIELDS,
    LifecycleEventRow,
    ProcurementInput,
)
from model.roster.reader import canonical_bytes

__all__ = [
    "CANONICAL_SERIALIZATION",
    "EVENT_TABLE_KEY",
    "LINE_TABLE_KEY",
    "SPLIT_ASSIGNMENT_FIELDS",
    "SerializeError",
    "SplitAssignmentRow",
    "input_data_hash",
    "split_assignment_hash",
]


class SerializeError(ValueError):
    """Raised when a value cannot be rendered into the hashed payload.

    One type, as `RosterError` and E005's `SerializeError` are: every failure
    here says the same thing — this row has no canonical form, so any digest
    taken over it would be a number nobody can reproduce.
    """


#: The label `forecast_run.canonical_serialization` records beside every digest
#: this module produces (`data-model.md` § Hashes). Stated here because it names
#: the rule set implemented here; a writer copies it rather than retyping a
#: string that would then be a second opinion about what was hashed.
CANONICAL_SERIALIZATION = "canonical-json-sorted-keys-utf8"

#: The two envelope keys of the input row hash's payload — the delivered table
#: names, so a reader of the digest's definition and a reader of the schema are
#: reading the same two words.
LINE_TABLE_KEY = "purchase_order_line"
EVENT_TABLE_KEY = "lifecycle_event"

#: The five fields the split assignment hash covers, in `data-model.md`
#: § Hashes' own list. `canonical_ordinal` is deliberately absent from the
#: serialized object: it is the *order*, and serializing a position alongside
#: the sequence it orders would record the same fact twice.
SPLIT_ASSIGNMENT_FIELDS = (
    "project_id",
    "po_number",
    "line_number",
    "split_side",
    "is_censored",
)


class SplitAssignmentRow(Protocol):
    """What `split_assignment_hash` needs of a record, and nothing more.

    A protocol rather than an import of `split.SplitAssignment`, so the digest
    can also be recomputed from a row read back out of
    `forecast_split_assignment` — DV-017 requires exactly that, and a hash
    function that only accepted the in-memory type could not discharge it.
    """

    project_id: str
    po_number: str
    line_number: int
    split_side: str
    is_censored: bool
    canonical_ordinal: int


def _instant(moment: datetime, where: str) -> str:
    """An aware instant in UTC, ISO 8601. A naive one has no single rendering.

    `read.py` has already normalized every `occurred_at` it returns, so a naive
    value reaching here means the row came from somewhere else — and rendering
    it as though it were UTC would make the digest a function of the machine's
    time zone, which is the one property the hash exists to not have.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise SerializeError(
            f"{where} is a naive datetime; an instant with no zone renders differently on "
            f"two machines, so it has no canonical form and cannot enter a digest"
        )
    return moment.astimezone(UTC).isoformat()


def _json_scalar(value: object, where: str) -> Any:
    """One column value as the JSON scalar the canonical bytes are taken over.

    The rendering rule lives here and only here, so `quantity` is a decimal
    string on both digests and a date is an ISO day on both. A `float` falls
    through to the refusal deliberately (E005 AD-004): its repr is not a
    canonical decimal, and no column in either projection is one.
    """
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, datetime):
        return _instant(value, where)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise SerializeError(
        f"{where} is a {type(value).__name__} ({value!r}), which has no canonical JSON "
        f"rendering. Add the rule here rather than at the call site, so both digests "
        f"render the type the same way"
    )


def _projected(row: object, fields: tuple[str, ...], where: str) -> dict[str, Any]:
    """`row` reduced to `fields`, each rendered. Quantified over the projection.

    Read with `getattr` over the imported field tuple rather than written out,
    so a column added to E005's compared-content set enters both this payload
    and the loader's comparison from one edit.
    """
    return {field: _json_scalar(getattr(row, field), f"{where}.{field}") for field in fields}


def _canonical_input(procurement_input: ProcurementInput) -> dict[str, Any]:
    """The payload `data-model.md` § Hashes defines the input row hash over.

    Lines ascending by natural key, events flattened in `(line, sequence_no)`
    order. Sorted here rather than trusted from the caller: `read.py` orders its
    query, but a digest that inherited whatever order it was handed would be a
    function of the query plan, and FR-023 would then refuse on a re-read that
    changed nothing.
    """
    lines = sorted(procurement_input.lines, key=lambda line: line.natural_key)
    events: list[LifecycleEventRow] = [
        event
        for line in lines
        for event in sorted(line.events, key=lambda event: event.sequence_no)
    ]
    return {
        LINE_TABLE_KEY: [
            _projected(line, HASHED_LINE_FIELDS, f"{LINE_TABLE_KEY}[{ordinal}]")
            for ordinal, line in enumerate(lines)
        ],
        EVENT_TABLE_KEY: [
            _projected(event, HASHED_EVENT_FIELDS, f"{EVENT_TABLE_KEY}[{ordinal}]")
            for ordinal, event in enumerate(events)
        ],
    }


def input_data_hash(procurement_input: ProcurementInput) -> str:
    """`sha256:` + 64 hex over the rows the fit read (FR-014, `data-model.md`).

    Covers the rows, never the fixture file: a hand-edited row, a partial load
    or a database of another vintage must move this digest, which is what makes
    FR-023's refusal mean "the rows are not the rows" rather than "the file on
    disk changed".
    """
    if not isinstance(procurement_input, ProcurementInput):
        raise SerializeError(
            f"the input row hash covers a `ProcurementInput`, found "
            f"{type(procurement_input).__name__}; `read.py` is what builds one, and "
            f"hashing a loose sequence would skip the canonical order it establishes"
        )
    if not procurement_input.lines:
        raise SerializeError(
            "the input row hash was asked to cover zero lines; `read.py` refuses an empty "
            "`purchase_order_line` for the same reason, because the digest of nothing is a "
            "constant that two unrelated empty runs would reproduce"
        )
    return sha256_of_bytes(canonical_bytes(_canonical_input(procurement_input)))


def _ordinals(rows: tuple[SplitAssignmentRow, ...]) -> list[int]:
    """Each record's `canonical_ordinal`, proved to be a usable ordering.

    A duplicate makes "ordered by `canonical_ordinal`" ambiguous, and a
    non-positive one is a value `ck_forecast_split_assignment__ordinal_positive`
    would reject on the way into the table this digest is recomputable from.
    """
    ordinals: list[int] = []
    for row in rows:
        ordinal = getattr(row, "canonical_ordinal", None)
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise SerializeError(
                f"an assignment record carries canonical_ordinal={ordinal!r}; the digest is "
                f"defined over the sequence that ordinal orders, so it must be an integer "
                f"of at least 1"
            )
        ordinals.append(ordinal)
    if len(set(ordinals)) != len(ordinals):
        duplicated = sorted({value for value in ordinals if ordinals.count(value) > 1})
        raise SerializeError(
            f"canonical_ordinal is repeated at {duplicated}; two records claiming one "
            f"position leave the hashed sequence undefined, which is what "
            f"`uq_forecast_split_assignment__run_ordinal` forbids in the table"
        )
    return ordinals


def split_assignment_hash(rows: Iterable[SplitAssignmentRow]) -> str:
    """`sha256:` + 64 hex over the split, ordered by `canonical_ordinal`.

    The ordering is imposed here rather than assumed of the caller, so the same
    assignment read back from `forecast_split_assignment` in whatever order the
    planner returned it reproduces the recorded digest (DV-017). `is_censored`
    is inside the serialization because the stratum is part of the claim: a
    split whose strata moved is a different split.
    """
    records = tuple(rows)
    if not records:
        raise SerializeError(
            "the split assignment hash was asked to cover zero records; every run assigns "
            "every line, so an empty assignment is a caller defect rather than a run whose "
            "digest happens to be the digest of `[]`"
        )
    _ordinals(records)
    ordered = sorted(records, key=lambda row: row.canonical_ordinal)
    payload: Any = [
        _projected(row, SPLIT_ASSIGNMENT_FIELDS, f"split_assignment[{row.canonical_ordinal}]")
        for row in ordered
    ]
    return sha256_of_bytes(canonical_bytes(payload))
