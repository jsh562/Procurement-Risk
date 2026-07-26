"""The one module in this repository that opens the roster fixture.

TR-016 / TR-027. Two consuming epics read this roster — E002 for the
synthesized project-document layer and E005 for procurement history — and both
must agree on identifiers and on what "unchanged" means. A second reader with
its own parse would be a second definition of the same data.

Stdlib only, on purpose. The offline generators that consume this run in the
modeling boundary, and a roster reader that pulled in a parsing dependency
would put that dependency in the modeling resolution for no reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# reader.py sits at src/model/src/model/roster/, so the repository root is six
# levels up — the entry's own src-layout repeats the package name.
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_ROSTER_PATH = REPO_ROOT / "data" / "roster" / "project-vendor-roster.json"

EXPECTED_PROJECTS = 5
EXPECTED_VENDORS = 12
TOP_LEVEL_KEYS = frozenset({"projects", "vendors"})
ENTRY_KEYS = frozenset({"id", "name"})


class RosterError(ValueError):
    """Raised when the roster is unreadable or fails validation.

    A single exception type rather than one per rule: every failure here means
    the same thing to a caller — do not generate data from this file.
    """


@dataclass(frozen=True)
class Entry:
    id: str
    name: str


@dataclass(frozen=True)
class Roster:
    projects: tuple[Entry, ...]
    vendors: tuple[Entry, ...]
    content_hash: str

    def identifiers(self) -> set[str]:
        return {e.id for e in self.projects} | {e.id for e in self.vendors}


def canonical_bytes(payload: dict) -> bytes:
    """Serialize for hashing, deterministically.

    Pinned rather than left to defaults because the digest is compared across
    machines and across time. Keys sorted, separators compact so insignificant
    whitespace cannot move the hash, non-ASCII preserved rather than escaped,
    UTF-8 encoded. The digest covers this canonical form — a re-serialization
    of parsed content — so the source file's byte layout does not affect it.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def content_hash(payload: dict) -> str:
    """Return ``sha256:`` followed by 64 lowercase hexadecimal characters."""
    return "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _validate(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RosterError(f"roster must be a JSON object, found {type(payload).__name__}")

    keys = set(payload)
    if keys != TOP_LEVEL_KEYS:
        unexpected = sorted(keys - TOP_LEVEL_KEYS)
        missing = sorted(TOP_LEVEL_KEYS - keys)
        raise RosterError(f"top-level keys wrong; unexpected={unexpected} missing={missing}")

    for key, expected in (("projects", EXPECTED_PROJECTS), ("vendors", EXPECTED_VENDORS)):
        rows = payload[key]
        if not isinstance(rows, list):
            raise RosterError(f"{key!r} must be a list, found {type(rows).__name__}")
        if len(rows) != expected:
            raise RosterError(f"{key!r} must hold exactly {expected} entries, found {len(rows)}")
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != ENTRY_KEYS:
                raise RosterError(f"{key}[{index}] must carry exactly {sorted(ENTRY_KEYS)}")
            if not all(isinstance(row[k], str) and row[k].strip() for k in ENTRY_KEYS):
                raise RosterError(f"{key}[{index}] has an empty or non-string field")

    identifiers = [row["id"] for key in TOP_LEVEL_KEYS for row in payload[key]]
    duplicates = sorted({i for i in identifiers if identifiers.count(i) > 1})
    if duplicates:
        raise RosterError(f"identifiers must be unique across the roster; repeated: {duplicates}")

    return payload


def read_roster(path: Path | None = None) -> Roster:
    """Parse, validate, and hash the roster in one step.

    Returning the roster and its hash together is deliberate: a caller that
    could obtain one without the other would eventually record data without
    provenance, which is the failure this design exists to prevent.
    """
    roster_path = path or DEFAULT_ROSTER_PATH
    try:
        raw = roster_path.read_bytes()
    except OSError as exc:
        raise RosterError(f"roster unreadable at {roster_path}: {exc}") from exc

    if raw.startswith(b"\xef\xbb\xbf"):
        # Encoding gates the parse, not the digest. A BOM is a read failure so
        # that it is caught at the boundary rather than surviving into a hash
        # that silently differs from every other machine's.
        raise RosterError(f"roster at {roster_path} carries a UTF-8 byte-order mark")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RosterError(f"roster at {roster_path} is not valid UTF-8 JSON: {exc}") from exc

    validated = _validate(payload)
    return Roster(
        projects=tuple(Entry(**row) for row in validated["projects"]),
        vendors=tuple(Entry(**row) for row in validated["vendors"]),
        content_hash=content_hash(validated),
    )
