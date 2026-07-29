"""T068 — DV-031 / FR-044: `artifact_hash` recomputed from what it covers.

A digest that cannot be recomputed from the rows it covers is a label. DV-017
places that obligation on `split_assignment_hash`; this places the same one on
the artifact hash, and it is the harder half, because splitting the artifacts
into two stores means the hash must define an order **across** two populations
rather than within one (ADR-0018 § Consequences/Negative).

The order is `(population_rank, canonical_ordinal)` — rank `0` for
`line_posterior` and `1` for `held_out_prediction` — and it is recomputable from
the stored rows alone: the ordinal is reached by joining each artifact row's
`(run_id, po_line_id)` to `forecast_split_assignment`, which holds every line
under the run. Nothing here re-derives it from the fit's in-memory state, which
is what makes this a check on the *stored* artifact rather than on the job's
memory of it.

**The digest is recomputed twice, by two paths.** Once through `hashlib` over the
stored `draw_digest` bytes in that order, which is the definition written out;
and once through `manifest.artifact_hash_over`, which is the function the job
used. The first is the check — a reader with the table and the definition
reproduces the value — and the second is what shows the function implements the
definition rather than some other consistent ordering of its own.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.manifest import (
    POPULATION_RANK_HELD_OUT_PREDICTION,
    POPULATION_RANK_LINE_POSTERIOR,
    ArtifactDigest,
    artifact_hash_over,
)

#: Module-level SQL, never assembled from values (Ruff S608). The rank is a
#: literal in the projection rather than a value bound in, so the two halves of
#: the union carry their own population's rank and the ordering column pair is
#: produced by the database rather than assembled afterwards.
STORED_ARTIFACT_DIGESTS_SQL = text(
    """
    SELECT 0 AS population_rank, a.canonical_ordinal, p.draw_digest, p.po_line_id
    FROM line_posterior p
    JOIN forecast_split_assignment a
      ON a.run_id = p.run_id AND a.po_line_id = p.po_line_id
    WHERE p.run_id = :run_id
    UNION ALL
    SELECT 1 AS population_rank, a.canonical_ordinal, h.draw_digest, h.po_line_id
    FROM held_out_prediction h
    JOIN forecast_split_assignment a
      ON a.run_id = h.run_id AND a.po_line_id = h.po_line_id
    WHERE h.run_id = :run_id
    ORDER BY population_rank, canonical_ordinal
    """
)
RECORDED_HASH_SQL = text("SELECT artifact_hash FROM forecast_run WHERE run_id = :run_id")
STORE_COUNTS_SQL = text(
    """
    SELECT (SELECT count(*) FROM line_posterior WHERE run_id = :run_id) AS posteriors,
           (SELECT count(*) FROM held_out_prediction WHERE run_id = :run_id) AS predictions
    """
)

#: A SHA-256 digest is 32 bytes, which is what
#: `ck_forecast_run__artifact_hash_length` and both stores'
#: `ck_…__draw_digest_length` require.
DIGEST_BYTES = 32


def _ordered_rows(db_session: Session, emitted_run: EmittedRun) -> list:
    """Every artifact row's digest, in `(population_rank, canonical_ordinal)` order."""
    rows = (
        db_session.execute(STORED_ARTIFACT_DIGESTS_SQL, {"run_id": emitted_run.run_id})
        .mappings()
        .all()
    )
    assert rows, "the run stored no artifact row, so its hash covers nothing"
    return list(rows)


def test_the_recorded_artifact_hash_is_recomputable_from_the_stored_rows(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-031. The definition written out, over rows read back from both stores.

    `hashlib` directly rather than through the epic's own function, so the value
    is reproduced by the recipe a reader would follow from `data-model.md`
    § Hashes and not by the code that produced it. A hash that agrees only with
    its own producer is a value recorded rather than derived.
    """
    rows = _ordered_rows(db_session, emitted_run)
    recorded = bytes(
        db_session.execute(RECORDED_HASH_SQL, {"run_id": emitted_run.run_id}).scalar_one()
    )

    combined = hashlib.sha256()
    for row in rows:
        combined.update(bytes(row["draw_digest"]))

    assert len(recorded) == DIGEST_BYTES
    assert combined.digest() == recorded, (
        f"the artifact hash recomputed over {len(rows)} stored digest(s) in "
        f"(population_rank, canonical_ordinal) order does not reproduce the recorded value. "
        f"Either the recorded hash covers a different set of rows, or it was taken in a "
        f"different order — and neither is recoverable from the artifact"
    )


def test_the_epics_own_function_implements_the_same_ordering(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """`artifact_hash_over` sorts internally, and this is what pins that sort.

    The rows are handed in **reversed**, so a function that concatenated in
    arrival order would produce a different digest. The ordering is a property of
    the function rather than of its call site — which is what stops a caller from
    getting it wrong by building the rows in whatever order it happened to.
    """
    rows = _ordered_rows(db_session, emitted_run)
    recorded = bytes(
        db_session.execute(RECORDED_HASH_SQL, {"run_id": emitted_run.run_id}).scalar_one()
    )
    digests = [
        ArtifactDigest(
            population_rank=int(row["population_rank"]),
            canonical_ordinal=int(row["canonical_ordinal"]),
            draw_digest=bytes(row["draw_digest"]),
        )
        for row in rows
    ]

    assert artifact_hash_over(digests) == recorded
    assert artifact_hash_over(reversed(digests)) == recorded


def test_the_hash_covers_both_populations_and_not_one(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The failing direction: a hash over one store must not reproduce the value.

    Without this, a run that hashed only its `line_posterior` rows would pass the
    recomputation above for any reader who also only looked at one store — and
    `population_rank` would be carrying nothing. The two ranks are also asserted
    to be the documented pair, since a hash taken in the opposite population
    order is self-consistent and is not the published definition.
    """
    rows = _ordered_rows(db_session, emitted_run)
    counts = db_session.execute(STORE_COUNTS_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    recorded = bytes(
        db_session.execute(RECORDED_HASH_SQL, {"run_id": emitted_run.run_id}).scalar_one()
    )
    ranks = {int(row["population_rank"]) for row in rows}

    assert counts["posteriors"] > 0
    assert counts["predictions"] > 0
    assert len(rows) == counts["posteriors"] + counts["predictions"]
    assert ranks == {POPULATION_RANK_LINE_POSTERIOR, POPULATION_RANK_HELD_OUT_PREDICTION}

    for rank in sorted(ranks):
        one_population = hashlib.sha256()
        for row in rows:
            if int(row["population_rank"]) == rank:
                one_population.update(bytes(row["draw_digest"]))

        assert one_population.digest() != recorded, (
            f"the recorded artifact hash is reproduced by population {rank} alone, so it "
            f"covers half the artifact set and no reader could tell"
        )


def test_the_ordering_columns_are_a_permutation_within_each_population(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The order is total, which is what makes the digest's input well defined.

    Two rows at one `(population_rank, canonical_ordinal)` would leave the
    concatenation order undefined and the hash unreproducible;
    `artifact_hash_over` refuses that outright, and this is the assertion that
    the stored rows do not present it. The ordinal is the *line's*, so the two
    populations legitimately have disjoint ordinal sets rather than each
    numbering from 1.
    """
    rows = _ordered_rows(db_session, emitted_run)
    positions = [(int(row["population_rank"]), int(row["canonical_ordinal"])) for row in rows]

    assert len(set(positions)) == len(positions)
    assert positions == sorted(positions), (
        "the database returned the rows out of (population_rank, canonical_ordinal) order, "
        "so the recomputation above concatenated them in some other sequence"
    )
    assert all(ordinal >= 1 for _, ordinal in positions)


def test_every_stored_draw_digest_is_a_full_length_digest(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """Each input to the hash is 32 raw bytes, in both stores.

    `ck_line_posterior__draw_digest_length` and
    `ck_held_out_prediction__draw_digest_length` each require it of their own
    table, and the artifact hash is a concatenation — so a short digest anywhere
    would shift every byte after it and change the value without any single row
    looking wrong.
    """
    for row in _ordered_rows(db_session, emitted_run):
        assert len(bytes(row["draw_digest"])) == DIGEST_BYTES, (
            f"line {row['po_line_id']} in population {row['population_rank']} stores a "
            f"{len(bytes(row['draw_digest']))}-byte digest"
        )
