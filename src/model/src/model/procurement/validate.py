"""`procurement-validate` — the reproduction oracle, and every provenance check.

FR-021 reduces to one comparison: the digest of the canonical serialization of
the *regenerated* payload against the digest committed beside the fixture. The
regeneration is what makes it an oracle rather than a checksum — re-reading the
file and hashing it again would prove only that the disk had not changed.

Three checks, each exiting non-zero rather than warning:

* **DV-015** — the digest reproduces from a regeneration and matches the sidecar
* **DV-016** — every recorded generation input still digests to what was recorded,
  each under the convention it was recorded with
* **DV-025** — the datasheet's provenance values equal the envelope's, and the
  recorded `library_pin` equals the version actually resolved

The pinned-library scope limit is **reported, never claimed**: the observed
version is an injected parameter, so a test can supply one outside the pin
without reinstalling anything, and the report says what was not verified rather
than asserting reproduction under a version nobody ran.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from model.corpus.equipment import EQUIPMENT_MAP_INPUT_PATH
from model.corpus.manifest import sha256_of_file
from model.corpus.manufacturers import MANUFACTURER_CATALOG_INPUT_PATH
from model.procurement import paths
from model.procurement.model import DIGEST_KIND_CANONICAL_CONTENT, DIGEST_KIND_RAW_BYTES
from model.procurement.serialize import dataset_content_hash, read_payload
from model.roster.reader import read_roster

__all__ = [
    "ROSTER_INPUT_PATH",
    "ValidationError",
    "ValidationReport",
    "check_datasheet",
    "check_input_drift",
    "check_pin_resolved",
    "check_provenance_agreement",
    "check_reproduction",
    "check_truth_binding",
    "main",
    "scope_limit",
    "validate",
]

ROSTER_INPUT_PATH = "data/roster/project-vendor-roster.json"

#: How each recorded input is recomputed. Keyed by the path the envelope records,
#: so the check iterates `generation_inputs` rather than naming inputs — an input
#: added to the envelope joins the check without editing this table.
_RECOMPUTE: Mapping[str, tuple[str, Callable[[Path], str]]] = {
    ROSTER_INPUT_PATH: (
        DIGEST_KIND_CANONICAL_CONTENT,
        lambda _root: read_roster().content_hash,
    ),
    EQUIPMENT_MAP_INPUT_PATH: (
        DIGEST_KIND_RAW_BYTES,
        lambda root: sha256_of_file(root / EQUIPMENT_MAP_INPUT_PATH),
    ),
    MANUFACTURER_CATALOG_INPUT_PATH: (
        DIGEST_KIND_RAW_BYTES,
        lambda root: sha256_of_file(root / MANUFACTURER_CATALOG_INPUT_PATH),
    ),
}

#: Envelope fields the datasheet must agree with, verbatim (DV-025).
PROVENANCE_FIELDS = (
    "generator_id",
    "generator_revision",
    "root_seed",
    "seed_derivation",
    "generation_date",
    "as_of_date",
)


class ValidationError(RuntimeError):
    """Raised on any failed check. The entry point exits non-zero, never warns."""


@dataclass
class ValidationReport:
    dataset_content_hash: str
    inputs_checked: int
    scope_limits: list[str] = field(default_factory=list)

    @property
    def reproduction_claimed(self) -> bool:
        """False whenever a scope limit applies.

        FR-022 scopes the reproducibility claim to the pinned library version.
        Running under a different one does not make the dataset wrong — it makes
        the claim unverified, and reporting it as verified would be the lie the
        scope limit exists to prevent.
        """
        return not self.scope_limits


def check_reproduction(fixture: Path | None = None, root: Path | None = None) -> str:
    """DV-015. Regenerate, recompute over the **parsed** payload, compare.

    Compares against the committed sidecar rather than against the fixture's own
    bytes: a digest over file bytes moves when git normalises a line ending on a
    Windows checkout, and a digest over parsed content cannot.
    """
    from model.procurement.generate import generate

    committed = read_payload(paths.hash_path(root))["dataset_content_hash"]
    on_disk = dataset_content_hash(read_payload(fixture or paths.fixture_path(root)))
    if on_disk != committed:
        raise ValidationError(
            f"the committed fixture digests to {on_disk} but its sidecar records "
            f"{committed}; one of the two was edited after the other was written"
        )

    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        regenerated = dataset_content_hash(generate(root=Path(scratch)))
    if regenerated != committed:
        raise ValidationError(
            f"regeneration produced {regenerated} against the committed {committed}. The "
            f"dataset does not reproduce from its recorded seed, so FR-021's claim is false"
        )
    return committed


def check_input_drift(envelope: Mapping[str, Any], root: Path | None = None) -> int:
    """DV-016. Every recorded input, each under its own `digest_kind`.

    Driven by iterating `generation_inputs`, so the count is whatever the
    envelope records — an input added there is checked here without this function
    being edited, which is the failure FR-027 was corrected for.
    """
    base = paths.REPO_ROOT if root is None else Path(root)
    entries = envelope["generation_inputs"]
    if not entries:
        raise ValidationError(
            "the envelope records no generation input, so nothing binds this dataset to "
            "the files it was generated from"
        )

    for entry in entries:
        known = _RECOMPUTE.get(entry["path"])
        if known is None:
            raise ValidationError(
                f"generation input {entry['path']!r} has no recomputation rule, so its "
                f"recorded digest cannot be verified. Unverifiable provenance is worse "
                f"than absent provenance"
            )
        expected_kind, recompute = known
        if entry["digest_kind"] != expected_kind:
            raise ValidationError(
                f"generation input {entry['path']} records digest_kind "
                f"{entry['digest_kind']!r} but is published as {expected_kind!r}. "
                f"Recomputing under the wrong convention reports a false mismatch"
            )
        actual = recompute(base)
        if actual != entry["digest"]:
            raise ValidationError(
                f"generation input {entry['path']} has drifted: recorded {entry['digest']}, "
                f"recomputed {actual} under {expected_kind}. Refusing rather than "
                f"validating a dataset whose inputs moved"
            )
    return len(entries)


def check_truth_binding(root: Path | None = None) -> int:
    """DV-017 — the record binds to this fixture and covers the roster's vendors.

    A record whose `dataset_content_hash` names a different dataset is worse than
    no record: it supports a recovery claim against data it did not describe.
    """
    import json

    from model.procurement.truth import validate_truth_record
    from model.roster.reader import read_roster

    record = json.loads(paths.truth_path(root).read_text(encoding="utf-8"))
    validate_truth_record(record, [entry.id for entry in read_roster().vendors])

    committed = read_payload(paths.hash_path(root))["dataset_content_hash"]
    if record["dataset_content_hash"] != committed:
        raise ValidationError(
            f"the ground-truth record binds to {record['dataset_content_hash']} but the "
            f"committed dataset digests to {committed}; the record describes a different run"
        )
    return len(record["vendor_offsets"])


def check_datasheet(root: Path | None = None) -> int:
    """DV-019 — all seven sections present, every limitation record complete.

    Reads the emitted markdown rather than the objects that produced it. A check
    over the in-memory records would pass on a datasheet whose renderer dropped a
    section, which is the failure mode that matters: the artifact is what a
    reader audits.
    """
    from model.procurement.datasheet import (
        ACTIVE_LIMITATIONS,
        LIMITATION_PARTS,
        SECTION_TITLES,
        check_limitations,
    )

    target = paths.datasheet_path(root)
    if not target.is_file():
        raise ValidationError(f"no datasheet at {target}")
    text = target.read_text(encoding="utf-8")

    missing = [title for title in SECTION_TITLES if f". {title}" not in text]
    if missing:
        raise ValidationError(
            f"the datasheet omits section(s) {', '.join(missing)}; FR-014 names seven and "
            f"a datasheet short a section discloses less than it claims to"
        )

    check_limitations(ACTIVE_LIMITATIONS)
    for record in ACTIVE_LIMITATIONS:
        if f"### {record.identifier}" not in text:
            raise ValidationError(f"limitation {record.identifier} is not in the emitted text")
    labels = (
        "Scope decision",
        "Supporting evidence",
        "Reversal trigger",
        "Production-scale alternative",
    )
    for label in labels:
        rendered = text.count(f"**{label}**")
        if rendered != len(ACTIVE_LIMITATIONS):
            raise ValidationError(
                f"'{label}' appears {rendered} time(s) against {len(ACTIVE_LIMITATIONS)} "
                f"limitation records; 100% must carry all {len(LIMITATION_PARTS)} parts"
            )
    return len(ACTIVE_LIMITATIONS)


def check_pin_resolved(envelope: Mapping[str, Any], observed_numpy: str | None = None) -> None:
    """The recorded pin is the version that actually resolved (DV-025).

    Separate from the datasheet comparison, and deliberately **not** called when
    a scope limit applies. Out of pin, this same disagreement is the scope limit
    itself — raising here as well would turn a reported limitation into a hard
    failure and make `validate` unable to do the one thing FR-022 asks of it.
    """
    recorded = envelope["library_pin"]["numpy"]
    resolved = np.__version__ if observed_numpy is None else observed_numpy
    if recorded != resolved:
        raise ValidationError(
            f"the fixture records numpy=={recorded} but the resolved version is "
            f"{resolved}; the datasheet would publish a pin nothing ran under"
        )


def check_provenance_agreement(
    envelope: Mapping[str, Any],
    datasheet_values: Mapping[str, Any] | None = None,
    observed_numpy: str | None = None,
) -> None:
    """DV-025. The datasheet agrees with the envelope, and the pin was resolved.

    `datasheet_values` is optional so this check is usable before the datasheet
    exists.
    """
    check_pin_resolved(envelope, observed_numpy)
    if datasheet_values is None:
        return
    for name in PROVENANCE_FIELDS:
        if name not in datasheet_values:
            raise ValidationError(f"the datasheet omits the provenance field {name!r}")
        if str(datasheet_values[name]) != str(envelope[name]):
            raise ValidationError(
                f"the datasheet records {name}={datasheet_values[name]!r} against the "
                f"envelope's {envelope[name]!r}; a datasheet that disagrees with the "
                f"artifact it describes is worse than no datasheet"
            )


def scope_limit(envelope: Mapping[str, Any], observed_numpy: str | None = None) -> list[str]:
    """The scope limits that apply to this run — **reported, never claimed**.

    The observed version is injected rather than read, so NC-10 can demonstrate
    the report without reinstalling numpy. A run outside the pin does not fail:
    the dataset is not wrong, the reproducibility claim is simply unverified, and
    saying so is the whole point.
    """
    recorded = envelope["library_pin"]["numpy"]
    observed = np.__version__ if observed_numpy is None else observed_numpy
    if observed == recorded:
        return []
    return [
        f"FR-021's reproduction claim is scoped to numpy=={recorded} and this run observed "
        f"numpy=={observed}. The digest comparison was not performed under the pinned "
        f"version, so reproduction is unverified here rather than verified or refuted"
    ]


def validate(root: Path | None = None, observed_numpy: str | None = None) -> ValidationReport:
    envelope = read_payload(paths.fixture_path(root))
    limits = scope_limit(envelope, observed_numpy)

    inputs = check_input_drift(envelope, root)
    if not limits:
        check_pin_resolved(envelope, observed_numpy)

    if limits:
        # Outside the pin, the digest comparison would be evidence about the
        # wrong environment. Report the limit and make no claim.
        return ValidationReport(
            dataset_content_hash=read_payload(paths.hash_path(root))["dataset_content_hash"],
            inputs_checked=inputs,
            scope_limits=limits,
        )

    check_datasheet(root)
    check_truth_binding(root)
    digest = check_reproduction(root=root)
    return ValidationReport(dataset_content_hash=digest, inputs_checked=inputs)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = validate()
    except ValidationError as error:
        print(f"procurement-validate failed: {error}")
        return 1
    for limit in report.scope_limits:
        print(f"scope limit: {limit}")
    if report.reproduction_claimed:
        print(f"reproduced {report.dataset_content_hash}")
    else:
        print(f"not verified under this environment: {report.dataset_content_hash}")
    print(f"  {report.inputs_checked} generation input(s) verified")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
