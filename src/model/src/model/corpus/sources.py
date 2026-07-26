"""The retrieval policy, read from its committed file — not restated here.

FR-002 / FR-002a. `data/corpus/real/retrieval-policy.json` carries the host
allow-list, the closed agency-variant map, the justified target sections and the
anchor section. This module is the one place that file is parsed, and it exposes
what it found rather than declaring a second copy.

**Why there is no literal section list in this file.** The tasks phase flagged
that a `sources.py` holding its own list and a policy holding another would give
the epic two target-section lists with neither declared authoritative and no
rule comparing them, so they could drift apart silently — the validator would
check the corpus against the policy while the retrieval run worked from the
module. Naming the policy the single source of truth and reading it here is what
closes that: `TARGET_SECTIONS` cannot disagree with `retrieval-policy.json`,
because it *is* `retrieval-policy.json`.

Exactly one value is stated in code rather than read: `REQUIRED_ANCHOR_SECTION`.
FR-002 fixes `01 33 00` at requirement level, so the policy's `anchor_section`
is **checked against** it instead of being trusted — a restatement that is
compared is a cross-check, while a restatement that is merely repeated is the
drift this module exists to prevent.

The policy is read at import. A malformed or missing policy is therefore an
import-time failure of every module that retrieves, which is the intent: no
fetch may proceed against a policy nobody could parse (FR-002a).

Stdlib only, following `model/roster/reader.py`: one error type, frozen
dataclasses, results ordered deterministically.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from model.corpus.manifest import MASTERFORMAT_PATTERN
from model.corpus.paths import CorpusPathError, corpus_root, resolve_within

__all__ = [
    "AGENCY_VARIANTS",
    "ANCHOR",
    "ANCHOR_SECTION",
    "POLICY_RELATIVE_PATH",
    "REQUIRED_ANCHOR_SECTION",
    "RETRIEVAL_TARGETS",
    "SOURCE_HOSTS",
    "TARGET_SECTIONS",
    "AgencyVariant",
    "RetrievalPolicy",
    "RetrievalPolicyError",
    "RetrievalTarget",
    "load_policy",
    "policy_path",
]

# Relative to the corpus root, resolved through `resolve_within` so the same
# containment and link rules that govern corpus documents govern the artifact
# that decides where documents may come from (VR-009, VR-067).
POLICY_RELATIVE_PATH = "real/retrieval-policy.json"

# FR-002 names this section. Held in code so the committed policy is compared
# against the requirement rather than defining it — the one deliberate
# restatement in this module, and it exists to be checked.
REQUIRED_ANCHOR_SECTION = "01 33 00"

# A hostname, lowercase, as `urlsplit(...).hostname` yields it. Uppercase fails
# here rather than being folded, so the committed allow-list is already in the
# form the per-hop comparison uses and no normalization sits between the file
# and the check.
HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
# `""` for UNIFIED; otherwise a dotted agency designator such as `.00 10`. No
# parenthesis, because FR-011's `document_identifier` wraps the revision date in
# the only parentheses that composition may contain.
SECTION_SUFFIX_PATTERN = re.compile(r"^(\.[0-9]{2} [0-9]{2})?$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

UNIFIED_VARIANT = "UNIFIED"


class RetrievalPolicyError(ValueError):
    """Raised when the retrieval policy is missing, unreadable, or malformed.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — no retrieval may be
    performed and nothing may be recorded from this policy.
    """


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise RetrievalPolicyError(f"{what} must be a string, found {type(value).__name__}")
    if not value.strip():
        raise RetrievalPolicyError(f"{what} must not be empty or whitespace-only")
    return value


def _string(value: object, what: str) -> str:
    """Type check only. For `section_suffix`, whose UNIFIED value is `""`."""
    if not isinstance(value, str):
        raise RetrievalPolicyError(f"{what} must be a string, found {type(value).__name__}")
    return value


def _matching(value: object, pattern: re.Pattern[str], what: str) -> str:
    text = _string(value, what)
    if not pattern.fullmatch(text):
        raise RetrievalPolicyError(f"{what} must match {pattern.pattern}, found {text!r}")
    return text


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RetrievalPolicyError(f"{what} must be an object, found {type(value).__name__}")
    return value


def _sequence(value: object, what: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise RetrievalPolicyError(f"{what} must be an array, found {type(value).__name__}")
    if not value:
        raise RetrievalPolicyError(f"{what} must not be empty")
    return value


@dataclass(frozen=True)
class AgencyVariant:
    """One admissible `agency_variant` token and what it implies.

    `section_suffix` is what FR-011's `document_identifier` composition appends
    (VR-023); `issuing_body` is what VR-062 holds an entry's `issuing_body`
    equal to. The two travel together because a variant naming one agency and an
    issuing body naming another describes two different documents, and both
    fields are otherwise well-formed.
    """

    token: str
    section_suffix: str
    issuing_body: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "token", _text(self.token, "an agency_variants key"))
        object.__setattr__(
            self,
            "section_suffix",
            _matching(
                self.section_suffix,
                SECTION_SUFFIX_PATTERN,
                f"agency_variants[{self.token!r}].section_suffix",
            ),
        )
        object.__setattr__(
            self,
            "issuing_body",
            _text(self.issuing_body, f"agency_variants[{self.token!r}].issuing_body"),
        )
        if self.token == UNIFIED_VARIANT and self.section_suffix != "":
            raise RetrievalPolicyError(
                f"agency_variants[{UNIFIED_VARIANT!r}].section_suffix must be the empty string, "
                f"found {self.section_suffix!r}"
            )
        if self.token != UNIFIED_VARIANT and not self.section_suffix:
            raise RetrievalPolicyError(
                f"agency_variants[{self.token!r}].section_suffix must be non-empty; only "
                f"{UNIFIED_VARIANT!r} carries no suffix"
            )

    def document_identifier(self, masterformat_section: str, revision_date: str) -> str:
        """FR-011's canonical composition, from the one place the suffix lives.

        Composed here rather than in the retrieval script so the value written
        into a manifest and the value VR-023 recomputes come from the same
        function and the same policy row.
        """
        section = _matching(masterformat_section, MASTERFORMAT_PATTERN, "masterformat_section")
        revision = _matching(revision_date, re.compile(r"^[0-9]{4}-[0-9]{2}$"), "revision_date")
        return f"UFGS {section}{self.section_suffix} ({revision})"


@dataclass(frozen=True)
class RetrievalTarget:
    """One document the retrieval run is expected to vendor.

    Identity is `(masterformat_section, agency_variant)`; the revision date is
    not known until the document is in hand, so it is not part of a target.
    `lead_time_justification` is required and non-empty — FR-002's "weighted
    toward long-lead equipment" carries no threshold, so the judgement lives
    here and must be reviewable rather than checkable.
    """

    masterformat_section: str
    agency_variant: str
    title: str
    division: str
    source_url: str
    lead_time_class: str
    lead_time_justification: str
    resolution_verified_on: str
    is_anchor: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.masterformat_section, self.agency_variant)


@dataclass(frozen=True)
class RetrievalPolicy:
    """The parsed contents of `retrieval-policy.json`.

    Every collection is ordered or frozen at construction, so two readers of one
    file see one order — the same reason `discover_locations` sorts.
    """

    source_hosts: tuple[str, ...]
    agency_variants: Mapping[str, AgencyVariant]
    targets: tuple[RetrievalTarget, ...]
    anchor: RetrievalTarget
    path: Path

    @property
    def anchor_section(self) -> str:
        return self.anchor.masterformat_section

    @property
    def target_sections(self) -> tuple[str, ...]:
        """Distinct `masterformat_section` values, ascending.

        Deduplicated because two agency variants of one number are two
        documents but one section — the counting rule VR-025 states.
        """
        return tuple(sorted({target.masterformat_section for target in self.targets}))

    @property
    def retrieval_targets(self) -> tuple[RetrievalTarget, ...]:
        """Every document to fetch: the target sections plus the anchor.

        The anchor is kept out of `targets` because it is not a long-lead
        section and listing it among them would misrepresent the weighting FR-002
        asks for; it is added back here because the retrieval run must fetch it.
        """
        return (*self.targets, self.anchor)

    def variant(self, token: str) -> AgencyVariant:
        try:
            return self.agency_variants[token]
        except KeyError:
            raise RetrievalPolicyError(
                f"agency_variant {token!r} is not in the closed set "
                f"{sorted(self.agency_variants)} (VR-021)"
            ) from None

    def allows_host(self, host: str | None) -> bool:
        """VR-022's comparison: exact equality after lowercasing, never suffix.

        A host that merely *ends in* an allow-listed name — `wbdg.org.example
        .invalid` against `wbdg.org` — is a different host and is refused. The
        comparison is stated as a method so the retrieval client, the
        re-verification client, and the validator cannot each implement it
        differently.
        """
        if not host:
            return False
        return host.strip().lower() in self.source_hosts


def policy_path(root: Path | None = None) -> Path:
    """Resolve the policy inside the corpus root, with VR-009's ordering."""
    base = corpus_root(root)
    try:
        return resolve_within(base, POLICY_RELATIVE_PATH)
    except CorpusPathError as exc:
        raise RetrievalPolicyError(f"cannot resolve the retrieval policy: {exc}") from exc


def _target(payload: object, what: str, *, is_anchor: bool) -> RetrievalTarget:
    record = _mapping(payload, what)
    known = {
        "masterformat_section",
        "agency_variant",
        "title",
        "division",
        "source_url",
        "lead_time_class",
        "lead_time_justification",
        "rationale",
        "resolution_verified_on",
    }
    unexpected = sorted(set(record) - known)
    if unexpected:
        raise RetrievalPolicyError(f"{what} carries unexpected keys {unexpected}")

    section = _matching(
        record.get("masterformat_section"), MASTERFORMAT_PATTERN, f"{what}.masterformat_section"
    )
    # The anchor records a `rationale` rather than a `lead_time_justification`,
    # because it is deliberately not a long-lead section; requiring the
    # long-lead field of it would invite a justification that is not true.
    justification_key = "rationale" if is_anchor else "lead_time_justification"
    return RetrievalTarget(
        masterformat_section=section,
        agency_variant=_text(record.get("agency_variant"), f"{what}.agency_variant"),
        title=_text(record.get("title"), f"{what}.title"),
        division=_text(record.get("division"), f"{what}.division"),
        source_url=_text(record.get("source_url"), f"{what}.source_url"),
        lead_time_class=(
            "ANCHOR"
            if is_anchor
            else _text(record.get("lead_time_class"), f"{what}.lead_time_class")
        ),
        lead_time_justification=_text(record.get(justification_key), f"{what}.{justification_key}"),
        resolution_verified_on=_matching(
            record.get("resolution_verified_on"),
            DATE_PATTERN,
            f"{what}.resolution_verified_on",
        ),
        is_anchor=is_anchor,
    )


def load_policy(path: Path | None = None, *, root: Path | None = None) -> RetrievalPolicy:
    """Read, validate and return the retrieval policy.

    Every check below is a precondition of a rule that runs later: an unparsable
    host makes VR-022 undecidable, a variant with no issuing body makes VR-062
    undecidable, and a target whose `source_url` is not on an allow-listed host
    would have the policy authorize a fetch its own allow-list refuses.
    """
    target_path = Path(path) if path is not None else policy_path(root)
    try:
        raw = target_path.read_bytes()
    except OSError as exc:
        raise RetrievalPolicyError(
            f"cannot read the retrieval policy {target_path}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalPolicyError(f"{target_path} is not valid UTF-8 JSON: {exc}") from exc
    document = _mapping(payload, "the retrieval policy")

    hosts: list[str] = []
    for index, value in enumerate(_sequence(document.get("source_hosts"), "source_hosts")):
        host = _matching(value, HOST_PATTERN, f"source_hosts[{index}]")
        if host in hosts:
            raise RetrievalPolicyError(f"source_hosts repeats {host!r}")
        hosts.append(host)

    variants: dict[str, AgencyVariant] = {}
    raw_variants = _mapping(document.get("agency_variants"), "agency_variants")
    if not raw_variants:
        raise RetrievalPolicyError("agency_variants must not be empty")
    for token in sorted(raw_variants):
        entry = _mapping(raw_variants[token], f"agency_variants[{token!r}]")
        variants[token] = AgencyVariant(
            token=token,
            section_suffix=entry.get("section_suffix"),
            issuing_body=entry.get("issuing_body"),
        )
    suffixes = [variant.section_suffix for variant in variants.values()]
    if len(set(suffixes)) != len(suffixes):
        raise RetrievalPolicyError(
            "two agency variants share one section_suffix; the suffix is what FR-011 "
            f"composes into document_identifier, so it must identify the variant: {suffixes}"
        )

    targets = tuple(
        _target(item, f"target_sections[{index}]", is_anchor=False)
        for index, item in enumerate(_sequence(document.get("target_sections"), "target_sections"))
    )
    anchor = _target(document.get("anchor"), "anchor", is_anchor=True)

    declared_anchor = _matching(
        document.get("anchor_section"), MASTERFORMAT_PATTERN, "anchor_section"
    )
    if declared_anchor != REQUIRED_ANCHOR_SECTION:
        raise RetrievalPolicyError(
            f"anchor_section must be {REQUIRED_ANCHOR_SECTION!r} (FR-002), "
            f"found {declared_anchor!r}"
        )
    if anchor.masterformat_section != declared_anchor:
        raise RetrievalPolicyError(
            f"anchor.masterformat_section {anchor.masterformat_section!r} does not equal "
            f"anchor_section {declared_anchor!r}"
        )

    seen: set[tuple[str, str]] = set()
    for target in (*targets, anchor):
        if target.agency_variant not in variants:
            raise RetrievalPolicyError(
                f"target {target.key} names agency_variant {target.agency_variant!r}, which is "
                f"outside the closed set {sorted(variants)}"
            )
        if target.key in seen:
            raise RetrievalPolicyError(
                f"two policy entries describe the same document {target.key}"
            )
        seen.add(target.key)
        split = urlsplit(target.source_url)
        if split.scheme != "https":
            raise RetrievalPolicyError(
                f"target {target.key} source_url must be https, found {target.source_url!r}"
            )
        if not split.hostname or split.hostname.lower() not in hosts:
            raise RetrievalPolicyError(
                f"target {target.key} source_url host {split.hostname!r} is not in source_hosts "
                f"{hosts}; the policy would authorize a fetch its own allow-list refuses"
            )

    if anchor.masterformat_section in {target.masterformat_section for target in targets}:
        raise RetrievalPolicyError(
            f"the anchor section {anchor.masterformat_section!r} is also listed under "
            "target_sections; it is recorded once, as the anchor"
        )

    return RetrievalPolicy(
        source_hosts=tuple(hosts),
        agency_variants=MappingProxyType(variants),
        targets=targets,
        anchor=anchor,
        path=target_path,
    )


POLICY = load_policy()

# The committed list, exposed rather than restated. `TARGET_SECTIONS` is the
# distinct section numbers — the population VR-025 counts — while
# `RETRIEVAL_TARGETS` carries one record per document, including the anchor.
TARGET_SECTIONS: tuple[str, ...] = POLICY.target_sections
RETRIEVAL_TARGETS: tuple[RetrievalTarget, ...] = POLICY.retrieval_targets
SOURCE_HOSTS: tuple[str, ...] = POLICY.source_hosts
AGENCY_VARIANTS: Mapping[str, AgencyVariant] = POLICY.agency_variants
ANCHOR: RetrievalTarget = POLICY.anchor
ANCHOR_SECTION: str = POLICY.anchor_section
