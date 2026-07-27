"""The closed record types of the dataset fixture, and the `NS_E005` namespace.

`data-model.md` §Envelope, §Line record and §Event record are the normative
enumeration of the payload FR-021 hashes. This module is that enumeration in
code: three frozen record types whose field sets are **closed** — thirteen
envelope keys, fourteen line keys, three event keys, all mandatory, no optional
field and no extension point. A key present in one document and absent from
another is a defect in one of them, never a field that is optionally inside the
hashed payload, because the digest covers the whole parsed payload and an extra
key is not a harmless addition but a different dataset. Widening any of the
three requires a `dataset_schema_version` bump.

The closed key tuples below are derived from the dataclasses with
`dataclasses.fields()` rather than written out a second time. A hand-maintained
list beside the type it describes is a list that eventually disagrees with it,
and this one is what a conformance check reads.

**Generated fields only.** `po_line_id`, `event_id`, `lifecycle_state`,
`is_closed`, `closing_event_id`, `from_state`, `is_terminal`, `prev_sequence_no`
and `note` are deliberately absent. Every one of them is either a pure function
of a field that *is* here or a column the database generates, and the delivered
schema enforces each biconditional that relates them, so recording them would
create a second place for one fact to be wrong.

**What is validated here, and what is not.** The value domains — the identifier
patterns, the 1–5 criticality band, the twenty category keys, the 190–210 line
count — belong to the generator, which refuses to emit rather than writing a
malformed artifact (DV-001…DV-014), and to the delivered `CHECK` constraints at
load. Repeating them here would be a third enforcement point for one rule. What
*is* enforced here is the narrower set that would corrupt the digest silently:

* a `quantity` that is not exactly representable at a scale of one, because
  `numeric` equality ignores trailing zeros — `12.50 = 12.5` in SQL while the
  two are different digests (AD-004, HINT-005) — and because quantizing a
  2-decimal value here would round it without saying so;
* an `occurred_at` that is not an aware UTC instant, because rendering a naive
  or local-zone datetime produces a different string on a different machine;
* a `digest` or `digest_kind` outside its closed form, because a malformed
  roster digest is only rejected far downstream, by `ck_pol__roster_hash_format`
  at load, long after the artifact carrying it was committed.

Each of those is silent in exactly the sense Principle III names: the wrong
value is well-formed and nothing downstream refuses it.

**`uuid.uuid4()` appears nowhere in this package** (HINT-001, AD-003). It reads
`os.urandom` and ignores the seed, so it would move the content hash on every
run while the recorded seed still appeared honoured. `NS_E005` below is the
namespace every surrogate key is derived under, at load rather than here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from decimal import Decimal

from model.corpus.manifest import DIGEST_PATTERN, SYNTHETIC_BASIS_ID, THIRD_PARTY_RIGHTS

__all__ = [
    "DIGEST_KINDS",
    "DIGEST_KIND_CANONICAL_CONTENT",
    "DIGEST_KIND_RAW_BYTES",
    "ENVELOPE_KEYS",
    "EVENT_KEYS",
    "LINE_KEYS",
    "NS_E005",
    "NS_E005_NAME",
    "QUANTITY_EXPONENT",
    "FixtureEnvelope",
    "FixtureEvent",
    "FixtureLine",
    "GenerationInput",
    "LibraryPin",
    "LicenseBasis",
    "OrderDateWindow",
    "ProcurementModelError",
    "quantity_string",
    "rfc3339_utc",
]

#: The string `NS_E005` is derived from. A stable URL naming this feature
#: workspace, so the namespace is *recomputable* rather than a magic constant a
#: reader can only trust: anyone can run `uuid5(NAMESPACE_URL, NS_E005_NAME)`
#: and get the pinned value `6a5c9561-8a6b-58f7-8fbd-db51856db549`.
NS_E005_NAME = (
    "https://github.com/jsh562/Procurement-Risk-Demo/specs/00005-synthetic-procurement-history"
)

#: The namespace every E005 surrogate key is derived under (`data-model.md`
#: §Name construction). Derived here rather than transcribed as a literal: one
#: definition cannot drift from itself, whereas a literal beside a documented
#: derivation is two statements that can disagree. Fixed once and never changed
#: — changing it re-keys every row on every database, which is a regeneration
#: rather than an edit.
NS_E005 = uuid.uuid5(uuid.NAMESPACE_URL, NS_E005_NAME)

#: The two conventions a generation input may be hashed under (AD-010, G-3).
#: Recorded per entry because the roster and the category map genuinely differ,
#: and a reader of the committed artifact would otherwise have no way to know
#: which digest to recompute.
DIGEST_KIND_CANONICAL_CONTENT = "canonical_content"
DIGEST_KIND_RAW_BYTES = "raw_bytes"
DIGEST_KINDS = (DIGEST_KIND_CANONICAL_CONTENT, DIGEST_KIND_RAW_BYTES)

#: `quantity` is written at a fixed scale of exactly one digit after the decimal
#: point — `6.0`, never `6` or `6.00`. Fixed rather than bounded: "at most one
#: decimal place" would leave the loader's `numeric` comparison and the
#: reproducibility oracle able to disagree about the same value.
QUANTITY_EXPONENT = Decimal("0.1")

_UTC_SUFFIX = "+00:00"
_ZERO_OFFSET = timedelta(0)


class ProcurementModelError(ValueError):
    """Raised when a fixture record cannot be built or rendered.

    One exception type rather than one per rule, as `RosterError` and
    `CorpusPathError` are: every failure here means the same thing to a caller —
    this record must not reach the hashed payload.
    """


def quantity_string(value: Decimal) -> str:
    """Render `value` at a fixed scale of exactly one, or refuse it.

    Refusing rather than quantizing is the point. `Decimal("6.25").quantize(...)`
    would silently return `6.2`, and a quantity that quietly changed on its way
    into the artifact is the failure mode AD-004 exists to prevent: the value is
    still positive, still `numeric`-representable, still accepted by
    `ck_pol__quantity_positive`, and wrong. Rounding is the generator's decision
    to make explicitly, upstream of here.
    """
    if not isinstance(value, Decimal):
        raise ProcurementModelError(
            f"quantity must be a Decimal, found {type(value).__name__} — a binary float "
            f"has no exact decimal scale and `round()` on one is not a canonical decimal"
        )
    if not value.is_finite():
        raise ProcurementModelError(f"quantity must be finite, found {value}")
    rescaled = value.quantize(QUANTITY_EXPONENT)
    if rescaled != value:
        raise ProcurementModelError(
            f"quantity {value} is not exactly representable at a scale of one; "
            f"quantizing it here would silently change it to {rescaled}"
        )
    return f"{rescaled:f}"


def rfc3339_utc(value: datetime) -> str:
    """Render an aware UTC instant as `YYYY-MM-DDTHH:MM:SSZ`.

    `timespec="seconds"` is pinned rather than left at the default (HINT-002).
    At `timespec="auto"`, `isoformat()` omits the fractional part when
    microsecond is zero and includes it otherwise, so field width varies row to
    row and the digest moves for a reason unrelated to content.

    A naive datetime is refused rather than assumed to be UTC: assuming it would
    make the rendered instant depend on the machine's zone, which is precisely
    what SC-012 regenerates under a changed time zone to catch.
    """
    if not isinstance(value, datetime):
        raise ProcurementModelError(f"occurred_at must be a datetime, found {type(value).__name__}")
    offset = value.utcoffset()
    if offset is None:
        raise ProcurementModelError(
            f"occurred_at {value.isoformat()} is naive; an instant with no zone renders "
            f"differently on every machine, so UTC is required rather than assumed"
        )
    if offset != _ZERO_OFFSET:
        raise ProcurementModelError(
            f"occurred_at {value.isoformat()} carries offset {offset}; the fixture records "
            f"instants in UTC with a literal Z, and normalizing here would hide the drift"
        )
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise ProcurementModelError(
            f"occurred_at {value.isoformat()} carries a time of day; durations are whole "
            f"days, so anything but midnight is invented precision"
        )
    return value.isoformat(timespec="seconds").removesuffix(_UTC_SUFFIX) + "Z"


@dataclass(frozen=True, slots=True)
class OrderDateWindow:
    """The inclusive calendar window every `order_date` is drawn from.

    Both bounds are committed constants and neither is read from a clock: a
    run-date default would move the committed content hash the day after
    generation while the recorded seed still looked honoured (FR-009).
    """

    first: date
    last: date

    def to_payload(self) -> dict[str, str]:
        return {"first": self.first.isoformat(), "last": self.last.isoformat()}


@dataclass(frozen=True, slots=True)
class GenerationInput:
    """One entry of `generation_inputs`: a path, its digest, and its convention.

    `digest_kind` is carried per entry rather than declared once for the list
    because the two inputs are genuinely hashed differently — the roster over
    canonical re-serialized content, the category map over raw committed bytes —
    each matching the convention its owning epic publishes (AD-010).
    """

    path: str
    digest: str
    digest_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ProcurementModelError("a generation input needs a repository-relative path")
        if not isinstance(self.digest, str) or not DIGEST_PATTERN.fullmatch(self.digest):
            raise ProcurementModelError(
                f"generation input {self.path!r} carries {self.digest!r}, which is not "
                f"`sha256:` followed by 64 lowercase hex characters"
            )
        if self.digest_kind not in DIGEST_KINDS:
            raise ProcurementModelError(
                f"generation input {self.path!r} declares digest_kind {self.digest_kind!r}; "
                f"the closed set is {DIGEST_KINDS}"
            )

    def to_payload(self) -> dict[str, str]:
        return {"path": self.path, "digest": self.digest, "digest_kind": self.digest_kind}


@dataclass(frozen=True, slots=True)
class LibraryPin:
    """The library version the reproducibility claim is scoped to (FR-022).

    NumPy alone, because it is the only library whose stream behaviour the
    generated values depend on. The value written is the version **resolved in
    the generating environment**, not the floor `src/model/pyproject.toml`
    declares; DV-025 asserts the two are equal, so the datasheet cannot record a
    pin nothing ran under.
    """

    numpy: str

    def to_payload(self) -> dict[str, str]:
        return {"numpy": self.numpy}


@dataclass(frozen=True, slots=True)
class LicenseBasis:
    """The same closed shape E002's synthetic manifests already publish.

    The three fixed values are imported from `corpus.manifest` rather than
    restated, so a change to the project's licence basis lands in one place.
    Only the statement is this epic's own.
    """

    statement: str
    basis_id: str = SYNTHETIC_BASIS_ID
    generated_by_this_project: bool = True
    third_party_rights: str = THIRD_PARTY_RIGHTS

    def to_payload(self) -> dict[str, object]:
        return {
            "basis_id": self.basis_id,
            "generated_by_this_project": self.generated_by_this_project,
            "statement": self.statement,
            "third_party_rights": self.third_party_rights,
        }


@dataclass(frozen=True, slots=True)
class FixtureEvent:
    """One lifecycle transition, as the fixture records it.

    Three keys and no more. `from_state` is the previous record's `to_state`,
    `is_terminal` is `to_state == 'delivered'`, `prev_sequence_no` is generated
    by the database, and `note` is `NULL` on every E005 event — recording any of
    them would give the fixture four ways to contradict constraints that will
    reject it anyway.
    """

    sequence_no: int
    to_state: str
    occurred_at: datetime

    def to_payload(self) -> dict[str, object]:
        return {
            "sequence_no": self.sequence_no,
            "to_state": self.to_state,
            "occurred_at": rfc3339_utc(self.occurred_at),
        }


@dataclass(frozen=True, slots=True)
class FixtureLine:
    """One purchase-order line and its whole event chain.

    The natural key is `(project_id, po_number, line_number)` — the key the
    delivered `uq_purchase_order_line__natural` enforces, the key an idempotent
    reload is joined on, the key a divergence refusal names, and the key each
    line's random stream is addressed by. `quantity` is a `Decimal` rather than a
    float throughout; it becomes a fixed-scale decimal *string* in the payload
    and is never rendered as a JSON float.
    """

    project_id: str
    vendor_id: str
    po_number: str
    line_number: int
    material_category: str
    description: str
    manufacturer: str
    part_number: str
    quantity: Decimal
    unit_of_measure: str
    order_date: date
    need_by_date: date
    criticality: int
    events: tuple[FixtureEvent, ...]

    @property
    def natural_key(self) -> tuple[str, str, int]:
        """`(project_id, po_number, line_number)` — the sort key and the join key."""
        return (self.project_id, self.po_number, self.line_number)

    def to_payload(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "vendor_id": self.vendor_id,
            "po_number": self.po_number,
            "line_number": self.line_number,
            "material_category": self.material_category,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "part_number": self.part_number,
            "quantity": quantity_string(self.quantity),
            "unit_of_measure": self.unit_of_measure,
            "order_date": self.order_date.isoformat(),
            "need_by_date": self.need_by_date.isoformat(),
            "criticality": self.criticality,
            "events": [event.to_payload() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class FixtureEnvelope:
    """The whole hashed payload: provenance, the committed constants, and `lines`.

    Thirteen keys, all mandatory, no others. `roster_hash` is **not** among them
    and is not repeated per line: it is read from the roster's
    `generation_inputs` entry and stamped on every row at load, because FR-002 is
    an obligation at the storage boundary and 199 copies of one constant inside a
    hashed artifact is a value that can disagree with itself for no gain.
    """

    dataset_schema_version: int
    layer: str
    generator_id: str
    generator_revision: int
    root_seed: int
    seed_derivation: str
    generation_date: date
    as_of_date: date
    order_date_window: OrderDateWindow
    generation_inputs: tuple[GenerationInput, ...]
    library_pin: LibraryPin
    license_basis: LicenseBasis
    lines: tuple[FixtureLine, ...]

    def to_payload(self) -> dict[str, object]:
        """The payload as plain JSON-ready data, in the order the fields are declared.

        Key order is irrelevant to the digest — `canonical_bytes` sorts keys —
        but the payload is also written to a committed file a reviewer reads, and
        an order that follows the declaration is one fewer thing to explain.
        """
        return {
            "dataset_schema_version": self.dataset_schema_version,
            "layer": self.layer,
            "generator_id": self.generator_id,
            "generator_revision": self.generator_revision,
            "root_seed": self.root_seed,
            "seed_derivation": self.seed_derivation,
            "generation_date": self.generation_date.isoformat(),
            "as_of_date": self.as_of_date.isoformat(),
            "order_date_window": self.order_date_window.to_payload(),
            "generation_inputs": [entry.to_payload() for entry in self.generation_inputs],
            "library_pin": self.library_pin.to_payload(),
            "license_basis": self.license_basis.to_payload(),
            "lines": [line.to_payload() for line in self.lines],
        }


def _keys_of(record: type) -> tuple[str, ...]:
    """The declared field names of a record type, in declaration order.

    Derived rather than transcribed: a closed key set written out beside the
    dataclass it describes is a second statement of the same fact, and the whole
    point of closure is that there is only one.
    """
    return tuple(field.name for field in fields(record))


#: The closed key sets. A conformance check reads these; nothing else defines
#: them. `data-model.md` counts them as thirteen, fourteen and three.
ENVELOPE_KEYS: Sequence[str] = _keys_of(FixtureEnvelope)
LINE_KEYS: Sequence[str] = _keys_of(FixtureLine)
EVENT_KEYS: Sequence[str] = _keys_of(FixtureEvent)
