"""The vendor and material-category index, and the design matrix built from it.

A-002. A *malformed* graph raises at build time; a **mis-indexed** one does not
— swap two vendors in the mapping and the graph builds, samples, and returns a
posterior plausible in every respect except which vendor each effect belongs to.
So the mapping is extracted here, as a pure function, and property-tested.

The index is a function of the **roster order** and never of the lines read. A
data-derived index would drop a vendor with no training line, and FR-019
requires a shrinkage weight for every vendor including that one. Position `i` in
the roster is column `i`; growth is at the tail, so a thirteenth vendor cannot
renumber the first twelve. Vendor columns come first, then category columns.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray

from model.forecast.read import LineRow

__all__ = [
    "DesignError",
    "category_index",
    "design_matrix",
    "vendor_index",
]


class DesignError(ValueError):
    """Raised when an index or a matrix cannot be built as specified.

    A `ValueError`: a duplicated roster entry, an empty roster, or a line naming
    something outside it are all things the caller supplied. Refusing is the
    point — silently de-duplicating shortens the parameter vector and shifts
    every column after the duplicate, which is the exact mis-index this module
    exists to make impossible.
    """


def _index(members: Sequence[str], what: str) -> dict[str, int]:
    """`{member: position}` in the caller's order, refusing anything ambiguous.

    Insertion-ordered `dict`, built by enumeration rather than from a `set`, so
    the mapping is identical across processes — the run that writes a posterior
    is not the run that reads it, and a mapping stable only within one call
    would misattribute every offset on the way back in.
    """
    entries = tuple(members)
    if not entries:
        raise DesignError(
            f"the {what} index was built from an empty roster; the matrix would then carry "
            f"no columns for this hierarchy and the pooling would range over nothing"
        )
    for position, member in enumerate(entries):
        if not isinstance(member, str) or not member.strip():
            raise DesignError(
                f"{what}[{position}] is {member!r}; an index member is a non-blank "
                f"identifier, and a blank one names a column no line can ever load"
            )
    if len(set(entries)) != len(entries):
        duplicated = sorted({member for member in entries if entries.count(member) > 1})
        raise DesignError(
            f"the {what} roster names {duplicated} more than once. De-duplicating silently "
            f"would shorten the parameter vector by one and shift every column after the "
            f"duplicate, which is a mis-index arrived at by way of a tolerant reader"
        )
    return {member: position for position, member in enumerate(entries)}


def vendor_index(vendor_ids: Sequence[str]) -> dict[str, int]:
    """Each vendor's column position, taken from the roster order it arrived in.

    Never sorted on the way in: sorting would satisfy the bijection and still
    move a column the first time a vendor identifier landed out of alphabetical
    order, which is a renumbering no reader of the stored posterior could see.
    """
    return _index(vendor_ids, "vendor")


def category_index(material_categories: Sequence[str]) -> dict[str, int]:
    """The same rule for the second hierarchy, which pools over material category."""
    return _index(material_categories, "material category")


def design_matrix(
    lines: Iterable[LineRow],
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
) -> NDArray[np.float64]:
    """One row per line: a vendor indicator block, then a category indicator block.

    `(n_lines, n_vendors + n_categories)`, vendor block first, and every column
    is present whether or not a line loads it — DV-009 requires a shrinkage
    entry for every member, and a column that disappears when the data are thin
    is a parameter vector whose length depends on the split.
    """
    vendors_at = vendor_index(vendor_ids)
    categories_at = category_index(material_categories)
    offset = len(vendors_at)

    rows = tuple(lines)
    matrix = np.zeros((len(rows), offset + len(categories_at)), dtype=float)
    for position, line in enumerate(rows):
        if line.vendor_id not in vendors_at:
            raise DesignError(
                f"line {line.natural_key} names vendor {line.vendor_id!r}, which is outside "
                f"the roster this index was built from. Dropping the line, or folding it "
                f"into a catch-all column, would train the population mean on it and report "
                f"nothing — a mis-attribution rather than a visible absence"
            )
        if line.material_category not in categories_at:
            raise DesignError(
                f"line {line.natural_key} names material category "
                f"{line.material_category!r}, which is outside the index this matrix is "
                f"built against"
            )
        matrix[position, vendors_at[line.vendor_id]] = 1.0
        matrix[position, offset + categories_at[line.material_category]] = 1.0
    return matrix
