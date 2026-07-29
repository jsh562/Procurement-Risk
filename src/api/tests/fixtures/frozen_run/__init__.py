"""Loading the frozen fixture through the committed schema.

FR-037. Every row goes in with a plain ``INSERT`` against the migrated tables,
so no test can exercise a state the storage layer forbids. That is the point of
the constraint rather than a nicety: a loader that bypassed the schema could
seed a survival array that is not monotone, or draws that are not sorted, and
the tests reading it would then assert behaviour against artifacts E007 can
never produce.

The document is read and never regenerated here. If ``generate.py`` and
``fixture.json`` disagree, that is a diff to review — not something a test run
should silently repair.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

FIXTURE_PATH: Final[Path] = Path(__file__).parent / "fixture.json"

__all__ = ["FIXTURE_PATH", "load_fixture", "seed_frozen_run"]


@lru_cache(maxsize=1)
def load_fixture() -> dict[str, Any]:
    """The committed fixture document, parsed once."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_INSERT_RUN: Final[str] = """
    INSERT INTO forecast_run (
        run_id, code_commit, code_worktree_dirty, input_data_hash, seed_entropy,
        chain_count, draw_count, tuning_count, library_versions, artifact_hash,
        draw_serialization, artifact_schema_version, model_version, as_of_date,
        horizon_days, wall_clock_seconds, roster_hash, is_active, covariate_names,
        open_line_draw_semantic, input_fixture_digest, input_layer, input_datasheet_ref,
        canonical_serialization, split_seed_entropy, split_assignment_hash,
        held_out_fraction_declared, held_out_fraction_realized,
        held_out_uncensored_event_count, vendor_shrinkage, open_line_count,
        training_line_count
    ) VALUES (
        %(run_id)s, %(code_commit)s, false, %(input_data_hash)s, %(seed)s,
        4, %(draw_count)s, 1000, %(library_versions)s, %(artifact_hash)s,
        'float64-le-c-contiguous', %(artifact_schema_version)s, %(model_version)s, %(as_of_date)s,
        %(horizon_days)s, 42.5, %(roster_hash)s, %(is_active)s,
        ARRAY['vendor_id','material_category'],
        'conditional_remaining_duration_from_run_as_of_date', %(fixture_digest)s, 'SYNTHETIC',
        %(datasheet_ref)s,
        'canonical-json-sorted-keys-utf8', %(split_seed)s, %(split_hash)s,
        0.2, 0.2, 12, %(vendor_shrinkage)s, %(open_line_count)s, %(training_line_count)s
    )
"""

_INSERT_LINE: Final[str] = """
    INSERT INTO purchase_order_line (
        po_line_id, project_id, vendor_id, po_number, line_number,
        material_category, description, manufacturer, part_number,
        quantity, unit_of_measure, order_date, need_by_date,
        criticality, lifecycle_state, is_closed, roster_hash
    ) VALUES (
        %(po_line_id)s, %(project_id)s, %(vendor_id)s, %(po_number)s, %(line_number)s,
        'ductwork', %(description)s, 'Calvex Supply Co', %(part_number)s,
        10, 'EA', %(order_date)s, %(need_by_date)s,
        %(criticality)s, %(lifecycle_state)s, false, %(roster_hash)s
    )
"""

_INSERT_POSTERIOR: Final[str] = """
    INSERT INTO line_posterior (
        run_id, po_line_id, draw_count, horizon_days, draws, survival,
        residual_tail_mass, draw_digest
    ) VALUES (
        %(run_id)s, %(po_line_id)s, %(draw_count)s, %(horizon_days)s, %(draws)s,
        %(survival)s, %(residual_tail_mass)s, %(draw_digest)s
    )
"""


def _digest32(payload: str) -> bytes:
    """A 32-byte digest — the length both `artifact_hash` and `draw_digest` check."""
    return sha256(payload.encode("utf-8")).digest()


def _vendor_shrinkage(lines: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """One shrinkage weight per vendor, each with its interval.

    `fn_vendor_shrinkage_wellformed` requires exactly `median`, `hpdi_low` and
    `hpdi_high` per vendor, each in [0, 1] and correctly ordered — Principle II
    reaching into the run's own metadata, so even a pooling weight cannot be
    recorded as a bare point estimate. The values are derived from the vendor id
    so they are deterministic and differ between vendors.
    """
    vendors = sorted({line["vendor_id"] for line in lines})
    weights: dict[str, dict[str, float]] = {}
    for index, vendor_id in enumerate(vendors):
        median = round(0.30 + 0.12 * index, 4)
        weights[vendor_id] = {
            "median": median,
            "hpdi_low": round(max(0.0, median - 0.08), 4),
            "hpdi_high": round(min(1.0, median + 0.08), 4),
        }
    return weights


def seed_frozen_run(connection: Any, *, is_active: bool = True) -> dict[str, Any]:
    """Load the fixture into an open transaction and return the document.

    Args:
        connection: An open connection. The caller owns the transaction, and the
            test fixtures roll it back.
        is_active: Whether the run is the active one. ``False`` seeds the run and
            its posteriors while leaving the page in the no-active-run state,
            which is how "a run exists but none is active" is reachable at all.

    Returns:
        The parsed fixture, so a test can read expected values from the same
        document the rows came from.
    """
    document = load_fixture()
    run = document["run"]
    lines = document["lines"]
    order_date = date.fromisoformat(run["as_of_date"]) - timedelta(days=30)

    with connection.cursor() as cursor:
        cursor.execute(
            _INSERT_RUN,
            {
                "run_id": run["run_id"],
                "code_commit": sha256(b"frozen-fixture").hexdigest()[:40],
                "input_data_hash": "sha256:" + sha256(b"frozen-input").hexdigest(),
                "seed": str(document["provenance"]["seed"]),
                "draw_count": run["draw_count"],
                "library_versions": json.dumps(
                    {
                        "pymc": "5.16.2",
                        "arviz": "0.19.0",
                        "numpy": "2.1.1",
                        "pandas": "2.2.3",
                        "pytensor": "2.25.4",
                        "blas": "openblas-0.3.27",
                    }
                ),
                "artifact_hash": _digest32(document["row_digest"]),
                "artifact_schema_version": run["artifact_schema_version"],
                "model_version": run["model_version"],
                "as_of_date": run["as_of_date"],
                "horizon_days": run["horizon_days"],
                "roster_hash": run["roster_hash"],
                "is_active": is_active,
                "fixture_digest": document["row_digest"],
                "datasheet_ref": document["provenance"]["generator"],
                "split_seed": str(document["provenance"]["seed"]),
                "split_hash": "sha256:" + sha256(b"frozen-split").hexdigest(),
                "vendor_shrinkage": json.dumps(_vendor_shrinkage(lines)),
                "open_line_count": sum(
                    1 for line in lines if line["lifecycle_state"] != "delivered"
                ),
                "training_line_count": len(lines),
            },
        )

        for line in lines:
            cursor.execute(
                _INSERT_LINE,
                {
                    "po_line_id": line["po_line_id"],
                    "project_id": line["project_id"],
                    "vendor_id": line["vendor_id"],
                    "po_number": line["po_number"],
                    "line_number": line["line_number"],
                    "description": line["description"],
                    "part_number": f"PN-{line['po_number']}-{line['line_number']}",
                    "order_date": order_date,
                    "need_by_date": line["need_by_date"],
                    "criticality": line["criticality"],
                    # Loaded open. A terminal line is closed afterwards by
                    # walking its event chain, because the closing FK and the
                    # two `is_closed` checks make any shortcut unrepresentable.
                    "lifecycle_state": "submitted",
                    "roster_hash": line["roster_hash"],
                },
            )

            posterior = line["posterior"]
            if posterior is None:
                continue
            cursor.execute(
                _INSERT_POSTERIOR,
                {
                    "run_id": run["run_id"],
                    "po_line_id": line["po_line_id"],
                    "draw_count": run["draw_count"],
                    "horizon_days": run["horizon_days"],
                    "draws": posterior["draws"],
                    "survival": posterior["survival"],
                    "residual_tail_mass": posterior["residual_tail_mass"],
                    "draw_digest": _digest32(repr(posterior["draws"])),
                },
            )

    return document


#: The shortest legal path from a submitted line to a delivered one, mirrored
#: from `conftest`'s chain. Repeated here rather than imported so this package
#: has no dependency on the test module that happens to use it first.
DELIVERY_CHAIN: Final[tuple[tuple[str | None, str], ...]] = (
    (None, "submitted"),
    ("submitted", "under_review"),
    ("under_review", "approved"),
    ("approved", "released_for_fabrication"),
    ("released_for_fabrication", "shipped"),
    ("shipped", "delivered"),
)


def close_fixture_line(connection: Any, po_line_id: str, *, anchor: date) -> None:
    """Walk a fixture line to `delivered` — the only way the schema permits."""
    from uuid import uuid4

    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        terminal_event_id = None
        for sequence_no, (from_state, to_state) in enumerate(DELIVERY_CHAIN, start=1):
            event_id = uuid4()
            cursor.execute(
                """
                INSERT INTO lifecycle_event (
                    event_id, po_line_id, sequence_no, from_state, to_state,
                    is_terminal, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    po_line_id,
                    sequence_no,
                    from_state,
                    to_state,
                    to_state == "delivered",
                    datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)
                    + timedelta(days=sequence_no),
                ),
            )
            if to_state == "delivered":
                terminal_event_id = event_id

        cursor.execute(
            """
            UPDATE purchase_order_line
               SET lifecycle_state = 'delivered', is_closed = true, closing_event_id = %s
             WHERE po_line_id = %s
            """,
            (terminal_event_id, po_line_id),
        )
