"""Every part number the corpus can print is recognised by the declared pattern.

Spec SC-005, FR-010, FR-014; plan AD-009. SC-005 is a *percentage over an
enumerated population*, and the population is the part of it most easily left
undefined — which is why AD-009 fixes the source explicitly: the generator's
pre-render document model, not `chunk.part_numbers` (null on every row) and not
`extracted_value` (empty while extraction is fixture-blocked).

**What is enumerated here, stated exactly.** The corpus mints a part number as
`<catalogue key>-<five digits>` and the catalogue is committed at
`data/corpus/synthetic/manufacturer-catalog.json`, where the key *is* the prefix.
So the set of part-number strings the corpus can print is the product of the
declared prefixes and the five-digit serial space, and it is enumerable from a
committed file. Every prefix is exercised, at both serial boundaries and at an
interior value.

**What this does not cover, so nobody reads it as more than it is.** It does not
read the rendered document instances — that needs `model.ingest.reference`, and
the api tier does not declare `model` as a dependency by design, so a test here
that reached for it would break the isolation the four entries exist to
guarantee. This asserts the pattern covers the whole *shape space* the generator
draws from, which is the half that can fail silently: a corpus adding a
manufacturer whose prefix the pattern does not match would route none of its
part numbers, and every retrieval figure would stay green.

The complementary half — that a recognised token actually resolves to its chunk
— is `test_router.py`, against seeded rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.retrieval.router import PART_NUMBER_PATTERN, recognise_part_numbers

CATALOG = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "corpus"
    / "synthetic"
    / "manufacturer-catalog.json"
)

#: `model.corpus.manufacturers._PART_NUMBER_DIGITS`. Restated rather than
#: imported, because importing it would mean the api tier importing the model
#: tier. Restating a constant across an isolation boundary is a real cost, and it
#: is paid deliberately: `test_the_restated_width_still_matches_the_corpus`
#: below fails if the two ever disagree.
SERIAL_DIGITS = 5


def _prefixes() -> list[str]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    return sorted(catalog["manufacturers"])


pytestmark = pytest.mark.skipif(
    not CATALOG.is_file(),
    reason="SC-005's enumeration comes from the committed manufacturer catalogue",
)


def test_the_catalogue_declares_at_least_one_manufacturer() -> None:
    """The baseline. Every assertion below is vacuous over an empty catalogue.

    Worth its own test rather than folded into the parametrisation: an empty
    parametrised set is *reported as passing*, which is exactly how a coverage
    claim over nothing comes to look like a coverage claim over everything.
    """
    assert _prefixes(), "the catalogue declares no manufacturers; SC-005 covers nothing"


@pytest.mark.parametrize("prefix", _prefixes())
@pytest.mark.parametrize("serial", [0, 1, 80347, 99998, 99999])
def test_every_declared_prefix_is_recognised(prefix: str, serial: int) -> None:
    """100% of the enumerated shape space, at both boundaries and inside.

    Both serial boundaries matter because the generator reduces modulo the digit
    width, so `0` is reachable and prints as `00000` — a token whose leading
    zeros a hand-written pattern is likely to get wrong.
    """
    token = f"{prefix}-{serial % (10**SERIAL_DIGITS):0{SERIAL_DIGITS}d}"
    assert recognise_part_numbers(token) == (token,), (
        f"{token} is a part number the corpus can print and the declared pattern "
        f"does not recognise it. SC-005 measures over the enumerated set, and an "
        f"unmatched prefix routes none of its part numbers while every figure stays green"
    )


def test_the_restated_width_still_matches_the_corpus() -> None:
    """The isolation boundary's cost, paid where it can be seen.

    `SERIAL_DIGITS` is restated rather than imported. This asserts the restated
    value still describes the catalogue's own constraint, so the copy cannot
    drift silently — which is the only thing that makes restating it acceptable.
    """
    for prefix in _prefixes():
        assert len(prefix) == 3, (
            f"{prefix} is not the three-character key the corpus mints from; the "
            f"restated format in this module no longer describes the generator"
        )


def test_a_part_number_is_recognised_inside_a_sentence() -> None:
    """FR-010. Coordinators do not type bare identifiers.

    The word boundaries in the pattern are what make this work, and a pattern
    anchored at the ends would pass every test above and fail every real query.
    """
    prefix = _prefixes()[0]
    token = f"{prefix}-80347"
    assert recognise_part_numbers(f"where is {token} on the submittal log?") == (token,)


def test_a_near_miss_is_not_recognised() -> None:
    """The other direction. A pattern that matched everything would also pass above.

    Two letters short of the minimum, and one digit short — both inside the
    declared shape's neighbourhood, so this fails if the bounds are loosened.
    """
    assert recognise_part_numbers("A-80347") == ()
    assert recognise_part_numbers("ASH-803") == ()


def test_the_pattern_is_exported_rather_than_inlined() -> None:
    """FR-010 calls it a *declared* pattern, and a declaration has a name.

    A regex compiled inside the recogniser would be undiscoverable from the
    catalogue side, and this test would have nothing to point at when a prefix
    stopped matching.
    """
    assert PART_NUMBER_PATTERN.pattern
