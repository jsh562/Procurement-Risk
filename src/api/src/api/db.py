"""Retrieval's database connection: the breadth, the version gate, the vector.

Three things live here that do not belong in the ranking statement itself.

**The search breadth rides on the connection** (AD-002, spec FR-027). It cannot
be issued per query: FR-002 permits retrieval exactly one statement, and a
`SET hnsw.ef_search = …` before the query is a second one.
`specs/00003-core-data-schema/data-model.md` prescribes setting it "at query
time", which as written would be that second statement; AD-002 declares this
plan normative over that line under {SAD:ADR-0017}.

**The extension version is checked before anything depends on it** (FR-039).
Iterative scan exists only from pgvector 0.8.0. Below that the setting is not
merely off, it is absent, and AD-003's entire filtered-recall approach is
unavailable — so this is a task, not an assumption.

**The query vector binds as text, not through an adapter** (AD-016). See
`query_vector_literal` below; the reasoning is there because that is where
someone would otherwise reach for `register_vector`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import psycopg

from api.config import RetrievalConfig

__all__ = [
    "ITERATIVE_SCAN_MINIMUM_VERSION",
    "PgvectorVersionError",
    "connection_options",
    "pgvector_version",
    "query_vector_literal",
    "supports_iterative_scan",
]

#: The first pgvector release with iterative scan. Below this the setting does
#: not exist, so `SET hnsw.iterative_scan` raises rather than being ignored.
ITERATIVE_SCAN_MINIMUM_VERSION: Final = (0, 8, 0)


class PgvectorVersionError(RuntimeError):
    """The pgvector extension is absent, or its version is unreadable."""


def pgvector_version(connection: psycopg.Connection) -> tuple[int, int, int]:
    """Report the installed pgvector version as a `(major, minor, patch)` tuple.

    Read from the live extension rather than from the image tag, because the tag
    names what was pulled and this names what is installed — a digest-pinned
    image whose extension was upgraded in place would satisfy the first check
    and fail the thing the check is for.

    Raises:
        PgvectorVersionError: The extension is not installed, or its version
            string is not three dot-separated integers. Both are refusals
            rather than defaults: guessing a version here would let iterative
            scan be relied on where it does not exist.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cursor.fetchone()
    if row is None or row[0] is None:
        msg = (
            "the pgvector extension is not installed on this database. The dense "
            "arm cannot run, and FR-039's version gate has nothing to read."
        )
        raise PgvectorVersionError(msg)
    raw = str(row[0])
    parts = raw.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        msg = (
            f"pgvector reported version {raw!r}, which is not three dot-separated "
            f"integers. Refusing rather than guessing: FR-039 gates iterative scan "
            f"on this comparison."
        )
        raise PgvectorVersionError(msg)
    major, minor, patch = (int(part) for part in parts)
    return (major, minor, patch)


def supports_iterative_scan(version: tuple[int, int, int]) -> bool:
    """Whether `version` has iterative scan at all (FR-039).

    Only **strict** order is usable here even when it is available. Relaxed
    order improves filtered recall the most and returns results slightly out of
    distance order, which contradicts FR-020's identical-ordering guarantee —
    so the mode that would help most is the one this epic cannot use.
    """
    return version >= ITERATIVE_SCAN_MINIMUM_VERSION


def connection_options(config: RetrievalConfig) -> str:
    """Build the libpq `options` string carrying the per-connection settings.

    This is the mechanism that keeps retrieval to one statement. Settings
    delivered as connection options are applied by the backend at session start,
    so the ranking query arrives with the breadth already in force and issues no
    `SET` of its own.

    `hnsw.ef_search` is set unconditionally rather than only in approximate
    mode. On the exact path the planner does not consult it, so setting it costs
    nothing — and making it conditional would put a second thing under the
    FR-026 flag's control, which spec FR-026 forbids: that flag governs index
    usage and nothing else.
    """
    return f"-c hnsw.ef_search={config.search_breadth}"


def query_vector_literal(embedding: Sequence[float]) -> str:
    """Render a query embedding as a pgvector text literal.

    **This is deliberately not `pgvector.psycopg.register_vector`** (AD-016).
    `/src/model` declares the `pgvector` distribution for one narrow reason —
    the adapter is what lets `chunk.embedding` be written by *binary COPY*
    rather than parsed as strings — and that is a bulk write path where the
    difference is real. E008 binds one 384-dimension vector per request into a
    `SELECT`, where it is not.

    Declaring `pgvector` in `/src/api` would also fail the build: the
    distribution is declared by `/src/model` and absent from
    `SHARED_INFRASTRUCTURE`, so TR-004's assertion that no modeling
    distribution reaches the serving resolution would fire. `project-instructions.md`
    v1.2.9 excepts a local-inference runtime, its tokenizer and NumPy, and
    `pgvector` is none of the three — so adding it needs a further amendment,
    not a wider exclusion set.

    The literal is cast at the call site, `%s::vector`, so the parameter stays
    a bound parameter and never becomes string-concatenated SQL.
    """
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"
