"""Content-addressed random streams: one independent generator per line.

`data-model.md` § Determinism specifies this exactly, and the specification is
worth reading as a prohibition before reading it as a construction. FR-019
forbids **positional derivation** and names two forms:

* `root_seed + i` — entropy values one apart are not independent, so adjacent
  lines draw correlated numbers;
* a single stream consumed line by line in emission order — every line's draws
  then depend on how many lines preceded it, so inserting one line at the front
  re-randomises the entire dataset.

Both fail the same way and neither is visible in the output. That is the whole
difficulty: an overlapping stream still produces plausible durations, plausible
slack fractions and plausible criticality bands. A reviewer looking at the
generated data cannot tell. So the derivation is what carries the guarantee,
and `test_seeds_properties.py` asserts the derivation rather than the draws.

**The construction.** A line's stream is spawned from the root seed under a
`spawn_key` derived from the line's own natural key:

    SeedSequence(entropy=root_seed, spawn_key=(line_stream_key,))

where `line_stream_key` is the first eight bytes of
`sha256("<project_id>|<po_number>|<line_number>")`, big-endian. `SeedSequence`
is designed for exactly this — its spawn keys are hashed into the entropy pool
rather than added to it, so keys that differ by one produce unrelated streams.

**Why the natural key and nothing else.** `vendor_id` is deliberately absent.
Including it would still produce unique keys and would still look correct, and
it would mean a line's draws changed if the allocation dealt it to a different
vendor — positional derivation wearing a different hat. The natural key is the
key the delivered `uq_purchase_order_line__natural` enforces, the key a
divergence refusal names, and the key an idempotent reload joins on; using the
same key here means a line's identity and its randomness cannot diverge.

**Ordering (FR-020).** This module hands out streams; it does not iterate. The
callers emit lines sorted by `(project_id, po_number, line_number)` and events
by `sequence_no`. No set, no hash-randomised mapping and no work queue reaches
the write path, because each of the three reorders output between runs at one
seed while every individual draw stays correct.
"""

from __future__ import annotations

import hashlib

import numpy as np

__all__ = ["STREAM_KEY_BYTES", "line_generator", "line_stream_key"]

#: How many leading digest bytes form the spawn key. Eight gives an unsigned
#: 64-bit value — wide enough that a collision across a two-hundred-line dataset
#: is not a practical concern, and narrow enough to stay an ordinary Python int
#: in the recorded derivation scheme the datasheet publishes.
STREAM_KEY_BYTES = 8


def line_stream_key(project_id: str, po_number: str, line_number: int) -> int:
    """The spawn key for one line, derived from its natural key alone.

    The separator is a literal `|`, and the components are joined in
    natural-key order. Both are part of the derivation the datasheet publishes,
    so a reader can recompute any line's key from the committed artifact — which
    is what makes the determinism claim checkable rather than merely asserted.
    """
    material = f"{project_id}|{po_number}|{line_number}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:STREAM_KEY_BYTES], "big")


def line_generator(
    root_seed: int, project_id: str, po_number: str, line_number: int
) -> np.random.Generator:
    """An independent generator for one line, spawned from `root_seed`.

    Returns a fresh generator on every call rather than caching one per line.
    A cached generator would carry consumption state, so a line's values would
    depend on what had already been drawn from it — which is the single-stream
    failure mode at the granularity of one line instead of the whole dataset.
    Callers that need a line's values more than once must draw them once and
    pass them along.
    """
    sequence = np.random.SeedSequence(
        entropy=root_seed,
        spawn_key=(line_stream_key(project_id, po_number, line_number),),
    )
    return np.random.default_rng(sequence)
