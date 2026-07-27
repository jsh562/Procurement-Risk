"""The reproducibility oracle: canonical bytes, the dataset content hash, the file.

FR-013 and FR-021. Everything this epic claims about reproducing a dataset
reduces to one comparison — the digest of the canonical serialization of the
regenerated payload against the digest committed beside the fixture — so this
module is where that claim is either true or quietly false.

**The rule set is reused, never re-authored** (AD-001). `canonical_bytes` here
delegates to `model.roster.reader.canonical_bytes`: sorted keys, compact
separators, `ensure_ascii=False`, UTF-8, over a re-serialization of *parsed*
content. That rule set already exists twice in this repository, identically, at
`roster/reader.py` and `corpus/model.py`; a third copy would be the defect
rather than the fix, and the two digests would drift the first time one copy was
touched. What this module adds on top of it is the part the roster does not
need: a refusal of every value JSON can carry but this fixture may not.

**Two byte strings, one digest, and that is the mechanism rather than an
inconsistency.** The canonical form is compact and carries no trailing newline.
The committed file is indented, sorted, and carries exactly one. Neither may be
changed to match the other: a digest over *file* bytes moves when git normalises
a line ending on a Windows checkout, and a digest over *parsed* content cannot.
That is what lets the development machine and the Linux verification runner
agree on the same number, and it is why `read_payload` parses before hashing
instead of digesting the file it just read.

**No JSON float reaches the payload** (AD-004). `round()` on a binary float is
not a canonical decimal — `round(2.675, 2)` is `2.67` — and `json.dumps` emits
the non-standard `NaN` and `Infinity` tokens unless told not to. Rather than
carry three rules, this module refuses the type: `quantity` is a fixed-scale
decimal string, durations are whole-day integers, and a float anywhere in the
payload is an error in both directions — refused on the way out by
`canonical_bytes`, and refused on the way in by `parse_payload`, so a fixture
that acquired one by hand-editing does not silently become the oracle's input.

Stdlib only, following the modules it reuses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model.corpus.manifest import sha256_of_bytes
from model.roster.reader import canonical_bytes as _roster_canonical_bytes

__all__ = [
    "MAX_NESTING_DEPTH",
    "SerializeError",
    "canonical_payload_bytes",
    "committed_file_bytes",
    "dataset_content_hash",
    "parse_payload",
    "read_payload",
    "write_payload",
]

#: How deep a payload may nest before this module refuses it. The fixture nests
#: four levels — envelope, `lines[]`, a line, `events[]` — so the cap is a long
#: way above anything legitimate. It exists to *terminate*: the walk below runs
#: before `json.dumps` does, so without a bound a payload that referenced itself
#: would hang here instead of raising `json`'s "Circular reference detected". A
#: depth cap is preferred to tracking visited objects because two keys sharing
#: one sub-object is legal and would be a false refusal.
MAX_NESTING_DEPTH = 32

_BOM = b"\xef\xbb\xbf"

#: Exactly what a JSON document may hold, and nothing more. `bool` is listed
#: ahead of `int` for the reader's benefit only — it is a subclass, so it is
#: admitted either way. `tuple` is deliberately **absent** even though
#: `json.dumps` would happily write one as an array: a tuple in and a list out
#: breaks `parse(canonical_bytes(x)) == x`, so the round-trip property would
#: fail on a value that serialized perfectly well.
_ADMISSIBLE_SCALARS = (str, bool, int, type(None))


class SerializeError(ValueError):
    """Raised when a payload cannot be canonicalized, parsed, or written.

    One type, as `RosterError` and `CorpusPathError` are: every failure here
    tells a caller the same thing — this payload must not become, or must not be
    trusted as, the reproducibility oracle's input.
    """


def _reject_inadmissible(payload: object) -> None:
    """Walk `payload` and refuse anything the fixture may not carry.

    Iterative rather than recursive, and depth-bounded, so a pathological input
    raises rather than exhausting the interpreter stack. Every refusal names the
    offending location — `$.lines[7].quantity` — because "a float somewhere in a
    six-hundred-kilobyte artifact" is not an actionable message.
    """
    pending: list[tuple[object, str, int]] = [(payload, "$", 0)]
    while pending:
        value, where, depth = pending.pop()
        if depth > MAX_NESTING_DEPTH:
            raise SerializeError(
                f"{where} nests deeper than {MAX_NESTING_DEPTH} levels; the fixture nests "
                f"four, so this is a cycle or a structure this serializer will not write"
            )
        if isinstance(value, float):
            raise SerializeError(
                f"{where} is a float ({value!r}); the fixture carries no JSON float — "
                f"`quantity` is a fixed-scale decimal string and every other number is an "
                f"integer, because float repr is not a canonical decimal (AD-004)"
            )
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SerializeError(
                        f"{where} has a {type(key).__name__} key ({key!r}); JSON object keys "
                        f"are strings, and `json.dumps` would coerce this one silently"
                    )
                pending.append((item, f"{where}.{key}", depth + 1))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                pending.append((item, f"{where}[{index}]", depth + 1))
        elif not isinstance(value, _ADMISSIBLE_SCALARS):
            raise SerializeError(
                f"{where} is a {type(value).__name__} ({value!r}), which is not a JSON "
                f"value. Render it to `str`, `int`, `bool` or `None` at the record type, "
                f"where the rendering rule can be stated once"
            )


def _require_mapping(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SerializeError(
            f"the hashed payload is one JSON object, found {type(payload).__name__}; "
            f"`canonical_bytes` takes one payload, so 'the payload' must name something"
        )
    return payload


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes the dataset content hash is taken over.

    Compact, sorted, `ensure_ascii=False`, UTF-8, **no trailing newline**. This
    is an in-memory byte string and is deliberately not what any file holds; see
    `committed_file_bytes`.
    """
    mapping = _require_mapping(payload)
    _reject_inadmissible(mapping)
    return _roster_canonical_bytes(mapping)


def dataset_content_hash(payload: dict[str, Any]) -> str:
    """`sha256:` + 64 lowercase hex over `canonical_payload_bytes(payload)`.

    The value FR-021 compares against, DV-015 recomputes, and the ground-truth
    record carries as its binding to one dataset. Both halves are borrowed:
    `roster.reader.canonical_bytes` decides the bytes and
    `corpus.manifest.sha256_of_bytes` decides the surface form, so this digest is
    the same kind of string as every other digest in the repository.
    """
    return sha256_of_bytes(canonical_payload_bytes(payload))


def committed_file_bytes(payload: dict[str, Any]) -> bytes:
    """The bytes of the committed artifact — indented, sorted, one trailing newline.

    A reviewer reads this form, so it is indented; the digest does not cover it,
    so indenting costs nothing. `sort_keys=True` here as well as in the canonical
    form, so the file a human diffs is ordered the same way the hashed bytes are
    and a reordered key shows up as no diff at all rather than as a whole-file
    churn.
    """
    mapping = _require_mapping(payload)
    _reject_inadmissible(mapping)
    text = json.dumps(mapping, indent=2, sort_keys=True, ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


def _refuse_float(raw: str) -> float:
    raise SerializeError(
        f"the payload carries the JSON number {raw!r}, which parses as a float; the "
        f"fixture carries decimal strings and integers only (AD-004)"
    )


def _refuse_constant(name: str) -> float:
    raise SerializeError(
        f"the payload carries the non-standard JSON token {name!r}; `NaN` and `Infinity` "
        f"are not JSON and have no canonical digest"
    )


def parse_payload(raw: bytes) -> dict[str, Any]:
    """Parse the committed bytes into the payload the digest is recomputed over.

    Refuses on the way in as well as on the way out. `parse_float` and
    `parse_constant` are wired to raise rather than to build a value, so a
    hand-edited fixture carrying `12.5` as a bare number is rejected here instead
    of becoming an input the oracle happily hashes into a stable, wrong answer.

    A UTF-8 byte-order mark is a read failure for the same reason
    `roster.reader` makes it one: it must be caught at the boundary rather than
    surviving into a digest that silently differs from every other machine's.
    """
    if not isinstance(raw, bytes | bytearray):
        raise SerializeError(f"a payload is parsed from bytes, found {type(raw).__name__}")
    if bytes(raw).startswith(_BOM):
        raise SerializeError("the payload carries a UTF-8 byte-order mark")
    try:
        parsed = json.loads(
            bytes(raw).decode("utf-8"),
            parse_float=_refuse_float,
            parse_constant=_refuse_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SerializeError(f"the payload is not valid UTF-8 JSON: {exc}") from exc
    return _require_mapping(parsed)


def write_payload(path: Path, payload: dict[str, Any]) -> Path:
    """Write the committed form to `path`, creating its directory if needed.

    `write_bytes`, never text mode. On Windows, text mode translates `\\n` to
    `\\r\\n` on the way out, so the emitted file would differ from the Linux
    runner's byte for byte — which DV-023 compares directly, and which is a
    difference no amount of `.gitattributes` can undo after the fact.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(committed_file_bytes(payload))
    return target


def read_payload(path: Path) -> dict[str, Any]:
    """Read and parse a committed payload.

    `read_bytes` for the mirror-image reason `write_payload` uses `write_bytes`:
    text mode would silently normalise the line endings of whatever is on disk,
    which would make a CRLF checkout indistinguishable from an LF one at exactly
    the point the distinction is being tested.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise SerializeError(f"payload unreadable at {target}: {exc}") from exc
    return parse_payload(raw)
