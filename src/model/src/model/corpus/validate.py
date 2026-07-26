"""Corpus validation: the rule registry, the runner, and the reporting contract.

FR-015, and the three rules that are properties of the *runner* rather than of
any corpus: VR-056 (every failure names its rule and, where a document is
attributable, its location; **all** failures are collected), VR-057 (read
failures are reported distinctly and short-circuit their location), and VR-066
(every rule quantifying over a population reports that population's observed
size and fails when it is empty).

**The validator never re-runs the generator.** Everything here reads committed
artifacts only. FR-031a requires re-derivation to be independent of what the
generator recorded, so a validator that regenerated in order to validate could
not distinguish a corpus defect from a generator defect — which is why
`data-model.md` splits the 72 rules across two runners and gives this one
VR-001…VR-039 and VR-051…VR-068. The rules needing a generator run live in
`src/model/tests/`.

**Why a registry rather than a script.** Three properties fall out of it that a
straight-line script would have to re-establish per rule: every rule declares
the population it quantifies over, so VR-066's non-vacuity guard is applied by
the runner once instead of being remembered forty times; every failure is
returned rather than raised, so VR-056's "all failures are collected" is
structural rather than a discipline; and every rule reports its observed
population size on a passing run, so a green run is evidence the rule had
something to say rather than evidence it found nothing to look at.

**Read failures are a different kind (VR-057).** VR-001, VR-002 and VR-003 are
reported in their own section rather than among the validation failures. A
location whose manifest yields **no payload at all** — VR-001 or VR-003 — is
short-circuited: it is excluded from every rule's population, because there is
nothing to evaluate. A location whose manifest parses but fails the schema is
*not* emptied; see `LocationReading` for why that reading of VR-057 is the one
that keeps `data-model.md`'s "asserted twice" requirement true.

Failure is never fatal to the run. `CorpusPathError`, `ManifestError`,
`RetrievalPolicyError`, `RetrievalError` and `RosterError` are caught at the
rule boundary and converted into failures, so one unreadable artifact cannot
hide the state of the rest of the corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from model.corpus.manifest import (
    COMMON_FIELDS,
    DIGEST_PATTERN,
    GENERATION_DATE_PATTERN,
    GENERATION_INPUT_PATHS,
    LAYER_REAL,
    LAYER_SYNTHETIC,
    MASTERFORMAT_PATTERN,
    POINT_OF_USE_CHECK,
    REAL_ONLY_FIELDS,
    RETRIEVED_AT_PATTERN,
    REVISION_DATE_PATTERN,
    STATUTE_FOR_BASIS,
    SYNTHETIC_BASIS_ID,
    SYNTHETIC_ONLY_FIELDS,
    THIRD_PARTY_RIGHTS,
    ManifestError,
    content_hash_of_file,
    sha256_of_file,
)
from model.corpus.paths import (
    DEFAULT_CORPUS_ROOT,
    MANIFEST_FILENAME,
    CorpusLocation,
    CorpusPathError,
    corpus_root,
    discover_locations,
    find_symlinks,
    repository_relative_path,
    resolve_within,
)

# One non-following walk for the whole package, imported rather than rewritten.
# `find_symlinks` and `discover_locations` are built on it; the corpus-root
# closure rules (VR-060, VR-064, VR-065) need the directory and file lists it
# already produces, and a second walk here would be a second definition of
# "non-following" that could drift from the one VR-067 is asserted over.
from model.corpus.paths import _scan as _non_following_scan
from model.corpus.retrieve import read_ledger
from model.corpus.sources import RetrievalPolicyError, load_policy
from model.roster.reader import read_roster

__all__ = [
    "DATASHEET_RELATIVE_PATH",
    "LAYERS",
    "PREPROCESSING_DISCLOSURE",
    "SCHEMA_RELATIVE_PATH",
    "STATED_LIMITS_DISCLOSURES",
    "Corpus",
    "Failure",
    "LocationReading",
    "Report",
    "Rule",
    "RuleOutcome",
    "RuleResult",
    "main",
    "registered_rules",
    "validate",
]

LAYERS: tuple[str, ...] = (LAYER_REAL, LAYER_SYNTHETIC)

# Validator-owned literals, resolved through `resolve_within` so the artifacts
# deciding what a manifest may say are governed by the same containment and link
# rules as the documents they govern (VR-009, VR-061, VR-065, VR-067).
SCHEMA_RELATIVE_PATH = "manifest.schema.json"
DATASHEET_RELATIVE_PATH = "synthetic/datasheet.md"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED = "SKIPPED"

#: VR-057's three. Their failures are read failures — reported in their own
#: section and short-circuiting their location — rather than validation
#: failures over a payload that was never successfully read.
READ_RULE_IDS = frozenset({"VR-001", "VR-002", "VR-003"})

#: VR-025's two floors, held in code rather than read from the policy: the
#: policy restates them under `coverage_floor` so its target list can be read
#: against them, and a floor read from the artifact it governs could be lowered
#: by the same edit that removes a section (`data-model.md` §Drift story).
REAL_DOCUMENT_FLOOR = 20
DISTINCT_SECTION_FLOOR = 6


# ---------------------------------------------------------------------------
# Failures — VR-056's shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    """One rule violation, in the form VR-056 requires.

    `location_id` and `location` are carried separately rather than
    pre-formatted into the message so a caller can group by document without
    parsing prose, and so the "where a document is attributable" half of VR-056
    is a property of the record rather than of how it was written.

    Not validated at construction: VR-056 is the rule that checks it, and a
    guard here would make that rule unfailable and therefore unevidenceable
    (SC-025). The renderer degrades rather than raising, so a malformed failure
    is *reported* rather than lost.
    """

    rule_id: str
    message: str
    location_id: str | None = None
    location: str | None = None

    @property
    def attributable(self) -> bool:
        """True when this failure names a specific document."""
        return self.location is not None

    def render(self) -> str:
        rule = self.rule_id or "<UNNAMED RULE>"
        if self.location_id and self.location:
            where = f" [{self.location_id}/{self.location}]"
        elif self.location_id:
            where = f" [{self.location_id}]"
        else:
            where = ""
        return f"{rule}:{where} {self.message}"


@dataclass(frozen=True)
class RuleOutcome:
    """What a rule observed: the size of its population and what failed in it.

    `observed` is mandatory rather than derived from the failure list, because
    VR-066 needs the size of the population a rule *looked at*, which a list of
    failures cannot supply — zero failures over zero documents and zero failures
    over forty documents are the two outcomes it exists to tell apart.
    """

    observed: int
    failures: tuple[Failure, ...] = ()
    skipped: str | None = None

    @classmethod
    def skip(cls, reason: str) -> RuleOutcome:
        return cls(observed=0, failures=(), skipped=reason)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

CheckFn = Callable[["Corpus"], RuleOutcome]


@dataclass(frozen=True)
class Rule:
    """A numbered validation rule and the population it quantifies over."""

    rule_id: str
    summary: str
    population: str
    check: CheckFn
    layers: frozenset[str] = frozenset()
    empty_population_fails: bool = True
    meta: bool = False


_RULES: dict[str, Rule] = {}


def rule(
    rule_id: str,
    *,
    summary: str,
    population: str,
    layers: Sequence[str] = (),
    empty_population_fails: bool = True,
    meta: bool = False,
) -> Callable[[CheckFn], CheckFn]:
    """Register one numbered rule.

    `population` is prose describing what the rule quantifies over and is
    printed beside the observed count on every run, passing or failing —
    VR-066's reporting half. `empty_population_fails` defaults to True and is
    turned off only where an empty population is a legitimate outcome, with the
    reason stated at the registration site.
    """

    def decorator(fn: CheckFn) -> CheckFn:
        if rule_id in _RULES:  # pragma: no cover - a programming error, not a corpus one
            raise RuntimeError(f"{rule_id} is registered twice")
        _RULES[rule_id] = Rule(
            rule_id=rule_id,
            summary=summary,
            population=population,
            check=fn,
            layers=frozenset(layers),
            empty_population_fails=empty_population_fails,
            meta=meta,
        )
        return fn

    return decorator


def registered_rules() -> tuple[Rule, ...]:
    """Every registered rule, in rule-identifier order."""
    return tuple(_RULES[key] for key in sorted(_RULES))


# ---------------------------------------------------------------------------
# The read phase (VR-001, VR-002, VR-003) — short-circuits its location
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    """VR-001's `object_pairs_hook`.

    A last-wins merge would silently discard content the schema then never
    sees: a manifest carrying two `entries` arrays would validate against
    whichever one came second.
    """
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _read_json_document(
    path: Path, rule_id: str, location_id: str | None
) -> tuple[Mapping[str, object] | None, tuple[Failure, ...]]:
    """Read one JSON artifact as UTF-8 without BOM, rejecting duplicate keys."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, (Failure(rule_id, f"cannot read {path}: {exc}", location_id),)
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, (Failure(rule_id, f"{path} begins with a UTF-8 BOM", location_id),)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, (Failure(rule_id, f"{path} is not valid UTF-8: {exc}", location_id),)
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        return None, (Failure(rule_id, f"{path} does not parse as JSON: {exc}", location_id),)
    if not isinstance(document, dict):
        return None, (
            Failure(
                rule_id,
                f"{path} must hold a JSON object, found {type(document).__name__}",
                location_id,
            ),
        )
    return document, ()


@dataclass(frozen=True)
class LocationReading:
    """One corpus location after the read phase.

    `document` is the **parsed** manifest: UTF-8, no BOM, no duplicate key, a
    JSON object. It is `None` exactly when VR-001 or VR-003 failed, which is
    the state that short-circuits a location under VR-057 — there is no payload
    to evaluate at all.

    **A VR-002 failure is a read failure but does not empty the location.**
    That is the operative half of VR-057's own wording, "which has no payload
    to evaluate without a successful parse": a schema-invalid manifest *has* a
    successful parse. It is also what keeps `data-model.md`'s repeated
    requirement true — VR-017 and VR-027 are to be "asserted twice, by the
    schema's layer conditional and by an explicit prohibited-key check, so
    removing the conditional does not silently remove the prohibition" — and a
    second assertion that only ever runs on payloads the first one already
    accepted is not a second assertion. It is a rule that never runs, with no
    failing direction for SC-025 to point at. The same reasoning reaches every
    rule the schema restates: VR-009's traversal refusal above all, which is a
    security control and has to be demonstrable failing under its own
    identifier rather than only as a pattern violation.

    The cost is accepted deliberately: one badly formed manifest is reported by
    VR-002 *and* by whichever rules its defect trips. Concurrent defects are
    what VR-056 requires collected, so more attributions is the intended
    direction, not noise.
    """

    location: CorpusLocation
    document: Mapping[str, object] | None
    schema_valid: bool
    read_failures: tuple[Failure, ...]

    @property
    def location_id(self) -> str:
        return self.location.location_id

    @property
    def path(self) -> Path:
        return self.location.path

    @property
    def manifest_path(self) -> Path:
        return self.location.manifest_path

    @property
    def readable(self) -> bool:
        return self.document is not None

    @property
    def declared_layer(self) -> str | None:
        if self.document is None:
            return None
        layer = self.document.get("layer")
        return layer if isinstance(layer, str) else None

    @property
    def entry_payloads(self) -> tuple[Mapping[str, object], ...]:
        if self.document is None:
            return ()
        entries = self.document.get("entries")
        if not isinstance(entries, list):
            return ()
        return tuple(item for item in entries if isinstance(item, dict))


@dataclass(frozen=True)
class EntryRef:
    """One manifest entry with the location it was found in.

    Rules take these rather than bare payloads so that every failure can name
    its `location_id` and `location` without each rule re-deriving them —
    VR-056's attribution held in one place.
    """

    reading: LocationReading
    index: int
    payload: Mapping[str, object]

    @property
    def location_id(self) -> str:
        return self.reading.location_id

    @property
    def location(self) -> str:
        value = self.payload.get("location")
        return value if isinstance(value, str) else f"entries[{self.index}]"

    @property
    def layer(self) -> str | None:
        value = self.payload.get("layer")
        return value if isinstance(value, str) else None

    @property
    def document_path(self) -> Path:
        return self.reading.path / self.location

    def failure(self, rule_id: str, message: str) -> Failure:
        return Failure(rule_id, message, self.location_id, self.location)


# ---------------------------------------------------------------------------
# The corpus under validation
# ---------------------------------------------------------------------------


@dataclass
class Corpus:
    """Everything the rules read, gathered once.

    Gathered once rather than per rule for two reasons: forty rules each
    walking the tree would let two of them disagree about what is on disk, and
    the walk is non-following (VR-067), which is a property worth having in one
    place rather than forty.
    """

    root: Path
    layers: frozenset[str]
    readings: tuple[LocationReading, ...]
    schema: Mapping[str, object] | None
    schema_failures: tuple[Failure, ...]
    symlinks: tuple[Path, ...]
    directories: tuple[Path, ...]
    files: tuple[Path, ...]
    # Filled by the runner before the meta rules execute, so VR-056 and VR-066
    # judge the run they are part of rather than a run they cannot see.
    interim_results: tuple[RuleResult, ...] = ()
    interim_failures: tuple[Failure, ...] = ()
    _cache: dict[str, object] = field(default_factory=dict)

    # -- location views ----------------------------------------------------

    @property
    def readable_readings(self) -> tuple[LocationReading, ...]:
        return tuple(reading for reading in self.readings if reading.readable)

    @property
    def in_scope_readings(self) -> tuple[LocationReading, ...]:
        """Readable locations whose declared layer is in scope.

        A location declaring a layer outside `--layer` is excluded from every
        entry- and document-level rule. The corpus-wide rules — VR-004,
        VR-059, VR-060, VR-064, VR-065, VR-067 — deliberately do **not** use
        this view: scoping must not be able to hide a location from the rules
        that decide whether it should exist at all.
        """
        return tuple(
            reading for reading in self.readable_readings if reading.declared_layer in self.layers
        )

    def location_dirs(self) -> tuple[Path, ...]:
        return tuple(reading.path for reading in self.readings)

    def is_inside_a_location(self, path: Path) -> bool:
        return any(path.is_relative_to(directory) for directory in self.location_dirs())

    # -- entry views -------------------------------------------------------

    def entries(self, layer: str | None = None) -> Iterator[EntryRef]:
        """Every entry of every readable manifest, scoped by the **entry's** layer.

        Scoping on the entry rather than on its manifest is what keeps an entry
        whose `layer` is not a layer at all inside the population: it is not
        "the other layer, out of scope", it is a value VR-014 must reject.
        """
        for reading in self.readable_readings:
            for index, payload in enumerate(reading.entry_payloads):
                ref = EntryRef(reading, index, payload)
                if ref.layer in LAYERS and ref.layer not in self.layers:
                    continue
                if layer is not None and ref.layer != layer:
                    continue
                yield ref

    def entry_list(self, layer: str | None = None) -> tuple[EntryRef, ...]:
        return tuple(self.entries(layer))

    # -- lazily loaded supporting artifacts --------------------------------

    def cached(self, key: str, load: Callable[[], object]) -> object:
        """Load a supporting artifact once, remembering the failure too.

        Three rules read the retrieval policy and two read the roster; loading
        per rule would report one unreadable artifact three times and read it
        three times.
        """
        if key not in self._cache:
            try:
                self._cache[key] = load()
            except Exception as exc:  # noqa: BLE001 - re-raised as a failure by the caller
                self._cache[key] = exc
        return self._cache[key]


def _read_corpus(root: Path, layers: frozenset[str]) -> Corpus:
    """The read phase: walk the tree once, then parse and schema-check."""
    base = corpus_root(root)
    scan = _non_following_scan(base)
    locations = discover_locations(base)

    schema, schema_failures = _load_schema(base)
    validator = _schema_validator(schema) if schema is not None else None

    readings: list[LocationReading] = []
    for location in locations:
        parsed, schema_valid, failures = _read_location(location, validator, schema_failures)
        readings.append(
            LocationReading(
                location=location,
                document=parsed,
                schema_valid=schema_valid,
                read_failures=failures,
            )
        )

    return Corpus(
        root=base,
        layers=layers,
        readings=tuple(readings),
        schema=schema,
        schema_failures=schema_failures,
        symlinks=find_symlinks(base),
        directories=scan.directories,
        files=scan.files,
    )


def _load_schema(base: Path) -> tuple[Mapping[str, object] | None, tuple[Failure, ...]]:
    """VR-003: the committed schema is itself a valid draft 2020-12 schema.

    A malformed schema must fail loudly rather than silently accept every
    manifest, so this is a read failure over the whole corpus rather than a
    per-location one.
    """
    try:
        path = resolve_within(base, SCHEMA_RELATIVE_PATH)
    except CorpusPathError as exc:
        return None, (Failure("VR-003", f"cannot resolve the manifest schema: {exc}"),)
    document, failures = _read_json_document(path, "VR-003", None)
    if document is None:
        return None, failures

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        return None, (
            Failure("VR-003", f"{path} is not a valid draft 2020-12 schema: {exc.message}"),
        )
    declared = document.get("$schema")
    if declared != "https://json-schema.org/draft/2020-12/schema":
        return None, (
            Failure(
                "VR-003",
                f"{path} declares $schema {declared!r}; draft 2020-12 is required",
            ),
        )
    return document, ()


def _schema_validator(schema: Mapping[str, object]) -> object:
    from jsonschema import Draft202012Validator

    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _read_location(
    location: CorpusLocation,
    validator: object | None,
    schema_failures: tuple[Failure, ...],
) -> tuple[Mapping[str, object] | None, bool, tuple[Failure, ...]]:
    """VR-001 then VR-002. Returns (parsed payload, schema-valid, read failures)."""
    manifest_path = location.manifest_path
    location_id = location.location_id

    if manifest_path.is_symlink():
        return (
            None,
            False,
            (
                Failure(
                    "VR-001",
                    f"{manifest_path} is a symbolic link; a manifest is a committed file",
                    location_id,
                ),
            ),
        )
    parsed, failures = _read_json_document(manifest_path, "VR-001", location_id)
    if parsed is None:
        return None, False, failures

    if validator is None:
        return (
            parsed,
            False,
            (
                Failure(
                    "VR-002",
                    "the manifest schema did not load, so this manifest could not be "
                    f"validated ({len(schema_failures)} schema read failure(s) reported "
                    "under VR-003)",
                    location_id,
                ),
            ),
        )

    # `iter_errors` rather than `best_match`: siblings are concurrent defects,
    # and collapsing them would hide every one but the deepest (AD-005, VR-002).
    errors = sorted(validator.iter_errors(parsed), key=lambda err: list(err.absolute_path))
    if errors:
        return (
            parsed,
            False,
            tuple(
                Failure(
                    "VR-002",
                    f"schema violation at {_json_pointer(error)}: {error.message}",
                    location_id,
                )
                for error in errors
            ),
        )
    return parsed, True, ()


def _json_pointer(error: object) -> str:
    path = getattr(error, "json_path", None)
    return str(path) if path else "$"


# ---------------------------------------------------------------------------
# Results and the report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    status: str
    observed: int
    failures: tuple[Failure, ...]
    skipped_reason: str | None = None

    @property
    def rule_id(self) -> str:
        return self.rule.rule_id

    def population_label(self) -> str:
        """What the run prints beside the observed count.

        A skipped rule prints its reason here rather than being omitted: a rule
        that did not run must be distinguishable from one that ran and passed.
        """
        if self.status == STATUS_SKIPPED:
            return f"{self.rule.population} - SKIPPED: {self.skipped_reason}"
        return self.rule.population


@dataclass(frozen=True)
class Report:
    """The outcome of one validation run."""

    root: Path
    layers: frozenset[str]
    results: tuple[RuleResult, ...]

    @property
    def read_failures(self) -> tuple[Failure, ...]:
        """VR-057's distinct kind: the failures of VR-001, VR-002 and VR-003.

        Derived from the results rather than collected separately, so a read
        failure is reported exactly once and the three read rules still appear
        in the population table like every other rule.
        """
        return tuple(
            failure
            for result in self.results
            if result.rule_id in READ_RULE_IDS
            for failure in result.failures
        )

    @property
    def rule_failures(self) -> tuple[Failure, ...]:
        return tuple(
            failure
            for result in self.results
            if result.rule_id not in READ_RULE_IDS
            for failure in result.failures
        )

    @property
    def all_failures(self) -> tuple[Failure, ...]:
        return (*self.read_failures, *self.rule_failures)

    @property
    def failed_rule_ids(self) -> frozenset[str]:
        return frozenset(failure.rule_id for failure in self.all_failures)

    @property
    def exit_code(self) -> int:
        return 1 if self.all_failures else 0

    def render(self) -> str:
        lines: list[str] = [
            f"corpus-validate - {self.root}",
            f"layers in scope: {', '.join(sorted(self.layers))}",
            "",
        ]

        # VR-057: read failures are reported distinctly from validation
        # failures, and each one short-circuits its location.
        lines.append(
            f"Read failures (VR-057 - reported apart from validation failures; each "
            f"short-circuits its location, which has no payload to evaluate): "
            f"{len(self.read_failures)}"
        )
        lines.extend(f"  {failure.render()}" for failure in self.read_failures)
        lines.append("")

        # VR-066: every rule reports its observed population size, passing or
        # failing, so a green run is evidence the rule had something to look at.
        lines.append("Rule      Status   Observed  Population")
        for result in self.results:
            lines.append(
                f"{result.rule_id:<9} {result.status:<8} {result.observed:>8}  "
                f"{result.population_label()}"
            )
        lines.append("")

        failures = self.rule_failures
        lines.append(
            f"Failures (VR-056 - every one names its rule and, where a document is "
            f"attributable, its location_id and location; all are collected, never only "
            f"the first): {len(failures)}"
        )
        lines.extend(f"  {failure.render()}" for failure in failures)
        lines.append("")

        passed = sum(1 for result in self.results if result.status == STATUS_PASS)
        failed = sum(1 for result in self.results if result.status == STATUS_FAIL)
        skipped = sum(1 for result in self.results if result.status == STATUS_SKIPPED)
        lines.append(
            f"{len(self.results)} rules: {passed} passed, {failed} failed, {skipped} skipped; "
            f"{len(self.all_failures)} failure(s) total."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def _run_rule(rule_to_run: Rule, corpus: Corpus) -> RuleResult:
    """Execute one rule, converting any escape into a failure of that rule.

    A rule that raises would otherwise abort the run and hide every rule after
    it, which is precisely the "only the first failure" behaviour VR-056
    forbids.
    """
    missing = sorted(rule_to_run.layers - corpus.layers)
    if missing:
        return RuleResult(
            rule=rule_to_run,
            status=STATUS_SKIPPED,
            observed=0,
            failures=(),
            skipped_reason=f"layer(s) {', '.join(missing)} not in scope",
        )

    try:
        outcome = rule_to_run.check(corpus)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return RuleResult(
            rule=rule_to_run,
            status=STATUS_FAIL,
            observed=0,
            failures=(
                Failure(
                    rule_to_run.rule_id,
                    f"the rule could not be evaluated: {type(exc).__name__}: {exc}",
                ),
            ),
        )

    if outcome.skipped is not None:
        return RuleResult(
            rule=rule_to_run,
            status=STATUS_SKIPPED,
            observed=outcome.observed,
            failures=(),
            skipped_reason=outcome.skipped,
        )

    failures = outcome.failures
    if outcome.observed == 0 and rule_to_run.empty_population_fails:
        # VR-066, applied by the runner so it is enforced once rather than
        # remembered forty times. The failure is attributed to the rule whose
        # population is empty, and VR-066's own meta-rule collects them.
        failures = (
            *failures,
            Failure(
                rule_to_run.rule_id,
                f"VR-066: population is empty - observed 0 {rule_to_run.population}, "
                "required at least 1; a rule over an empty set passes over nothing",
            ),
        )

    return RuleResult(
        rule=rule_to_run,
        status=STATUS_FAIL if failures else STATUS_PASS,
        observed=outcome.observed,
        failures=failures,
    )


def validate(
    root: Path | None = None,
    *,
    layers: Sequence[str] | None = None,
) -> Report:
    """Validate a corpus and return everything observed, failing nothing fast.

    `layers` restricts which layers' populations are evaluated. It defaults to
    both, so the strict reading is the default and a narrowed run is always an
    explicit request. Rules whose declared layer is out of scope are reported
    SKIPPED with the reason, never silently passed.
    """
    selected = frozenset(layers) if layers else frozenset(LAYERS)
    unknown = sorted(selected - set(LAYERS))
    if unknown:
        raise ValueError(f"unknown layer(s) {unknown}; expected a subset of {list(LAYERS)}")

    corpus = _read_corpus(Path(root) if root is not None else DEFAULT_CORPUS_ROOT, selected)

    results: list[RuleResult] = []
    ordinary = [each for each in registered_rules() if not each.meta]
    meta = [each for each in registered_rules() if each.meta]

    for each in ordinary:
        results.append(_run_rule(each, corpus))

    corpus.interim_results = tuple(results)
    corpus.interim_failures = tuple(failure for result in results for failure in result.failures)
    for each in meta:
        results.append(_run_rule(each, corpus))

    results.sort(key=lambda result: result.rule_id)
    return Report(root=corpus.root, layers=selected, results=tuple(results))


# ---------------------------------------------------------------------------
# VR-056, VR-057, VR-066 — the runner's own three rules
# ---------------------------------------------------------------------------


@rule(
    "VR-056",
    summary="Every failure names its rule and, where attributable, its document; "
    "all failures are collected",
    population="failures emitted by this run",
    # A clean corpus emits none, and that is the intended outcome rather than a
    # vacuous one — the only rule here where an empty population is correct.
    empty_population_fails=False,
    meta=True,
)
def _vr_056(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    for observed in corpus.interim_failures:
        if not observed.rule_id:
            failures.append(
                Failure(
                    "VR-056",
                    f"a failure was emitted naming no rule: {observed.message!r}",
                )
            )
        if observed.location is not None and not observed.location_id:
            failures.append(
                Failure(
                    "VR-056",
                    f"a failure names document {observed.location!r} but no location_id: "
                    f"{observed.message!r}",
                )
            )
    return RuleOutcome(observed=len(corpus.interim_failures), failures=tuple(failures))


@rule(
    "VR-057",
    summary="Read failures are reported distinctly and short-circuit their location",
    population="corpus locations whose manifest was read",
)
def _vr_057(corpus: Corpus) -> RuleOutcome:
    failures = tuple(
        Failure(
            "VR-057",
            "the manifest yielded no payload at all, so this location was short-circuited "
            f"and no validation rule was evaluated over it ({len(reading.read_failures)} "
            "read failure(s) reported separately, under VR-001 or VR-003)",
            reading.location_id,
        )
        for reading in corpus.readings
        if not reading.readable
    )
    return RuleOutcome(observed=len(corpus.readings), failures=failures)


@rule(
    "VR-066",
    summary="Every rule over a population reports its size and fails when it is empty",
    population="registered rules that quantify over a population",
    meta=True,
)
def _vr_066(corpus: Corpus) -> RuleOutcome:
    guarded = [
        result
        for result in corpus.interim_results
        if result.rule.empty_population_fails and result.status != STATUS_SKIPPED
    ]
    empty = [result for result in guarded if result.observed == 0]
    failures = tuple(
        Failure(
            "VR-066",
            f"{result.rule_id} quantifies over {result.rule.population} and observed 0; "
            "an empty or partially fetched corpus fails here rather than passing silently",
        )
        for result in empty
    )
    return RuleOutcome(observed=len(guarded), failures=failures)


# ---------------------------------------------------------------------------
# T027 - schema conformance and field sets
# VR-001, VR-002, VR-003, VR-014, VR-015, VR-016, VR-017, VR-027, VR-058, VR-063
#
# The last seven are the rules `data-model.md` requires "asserted twice - by
# the schema's layer conditional and by an explicit check - so removing the
# conditional does not silently remove the prohibition". They therefore read
# the *parsed* manifest rather than the schema-accepted one; see
# `LocationReading`.
# ---------------------------------------------------------------------------

#: The four drift markers VR-058 asserts absent. `additionalProperties: false`
#: already rejects all four; the absence is asserted again so it survives an
#: edit that loosens the strict-key rule.
DRIFT_MARKERS: tuple[str, ...] = ("generated_at", "revision", "updated", "version")


def _applicable_fields(layer: str | None) -> frozenset[str] | None:
    if layer == LAYER_REAL:
        return COMMON_FIELDS | REAL_ONLY_FIELDS
    if layer == LAYER_SYNTHETIC:
        return COMMON_FIELDS | SYNTHETIC_ONLY_FIELDS
    return None


def _blank_strings(value: object, path: str) -> Iterator[str]:
    """Every empty or whitespace-only string reachable from `value`.

    A semantic strip-check rather than `minLength: 1`, which admits `"  "`
    exactly (VR-015). Recursive because `license_basis` and `generation_inputs`
    are objects whose members are as required as the fields holding them.
    """
    if isinstance(value, str):
        if not value.strip():
            yield path
    elif isinstance(value, Mapping):
        for key, member in value.items():
            yield from _blank_strings(member, f"{path}.{key}")
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _blank_strings(member, f"{path}[{index}]")


def _digest_fields(payload: Mapping[str, object]) -> Iterator[tuple[str, object]]:
    """Every digest-valued field an entry carries, whatever its layer."""
    for name in ("content_hash", "upstream_digest", "roster_hash", "document_model_hash"):
        if name in payload:
            yield name, payload[name]
    inputs = payload.get("generation_inputs")
    if isinstance(inputs, Mapping):
        for key, value in inputs.items():
            yield f"generation_inputs[{key!r}]", value


@rule(
    "VR-001",
    summary="One manifest per location, UTF-8 without BOM, JSON with no duplicate key",
    population="corpus locations",
)
def _vr_001(corpus: Corpus) -> RuleOutcome:
    failures = tuple(
        failure
        for reading in corpus.readings
        for failure in reading.read_failures
        if failure.rule_id == "VR-001"
    )
    return RuleOutcome(observed=len(corpus.readings), failures=failures)


@rule(
    "VR-002",
    summary="Every manifest validates against the committed schema, all errors collected",
    population="manifests that parsed and reached schema validation",
)
def _vr_002(corpus: Corpus) -> RuleOutcome:
    failures = tuple(
        failure
        for reading in corpus.readings
        for failure in reading.read_failures
        if failure.rule_id == "VR-002"
    )
    return RuleOutcome(observed=len(corpus.readable_readings), failures=failures)


@rule(
    "VR-003",
    summary="The committed schema is itself a valid draft 2020-12 schema",
    population="committed manifest schemas",
)
def _vr_003(corpus: Corpus) -> RuleOutcome:
    # Exactly one schema exists (AD-009), so the population is 1 whether it
    # loaded or not: a schema that failed to load was still looked for, and
    # reporting 0 here would fire VR-066 for a reason that is not vacuity.
    return RuleOutcome(observed=1, failures=corpus.schema_failures)


@rule(
    "VR-014",
    summary="layer is exactly one of the closed two, in manifests and in entries",
    population="declared layer values (manifests and entries)",
)
def _vr_014(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    observed = 0
    for reading in corpus.readable_readings:
        observed += 1
        declared = reading.document.get("layer") if reading.document else None
        if declared not in LAYERS:
            failures.append(
                Failure(
                    "VR-014",
                    f"manifest layer {declared!r} is outside the closed set {list(LAYERS)}",
                    reading.location_id,
                )
            )
    for entry in corpus.entries():
        observed += 1
        if entry.payload.get("layer") not in LAYERS:
            failures.append(
                entry.failure(
                    "VR-014",
                    f"entry layer {entry.payload.get('layer')!r} is outside the closed set "
                    f"{list(LAYERS)}",
                )
            )
    return RuleOutcome(observed=observed, failures=tuple(failures))


@rule(
    "VR-015",
    summary="No field in an entry's applicable set is absent, null, empty or whitespace-only",
    population="required entry fields across every entry",
)
def _vr_015(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    observed = 0
    for entry in corpus.entries():
        applicable = _applicable_fields(entry.layer)
        if applicable is None:
            # The layer is not a layer; VR-014 reports that, and there is no
            # applicable set to check membership of.
            continue
        for name in sorted(applicable):
            observed += 1
            if name not in entry.payload:
                failures.append(entry.failure("VR-015", f"{name} is absent; no value is defaulted"))
                continue
            value = entry.payload[name]
            if value is None:
                failures.append(entry.failure("VR-015", f"{name} is null"))
                continue
            if isinstance(value, Mapping | list) and not value and name != "irregularity_classes":
                failures.append(entry.failure("VR-015", f"{name} is empty"))
                continue
            failures.extend(
                entry.failure("VR-015", f"{where} is empty or whitespace-only")
                for where in _blank_strings(value, name)
            )
    return RuleOutcome(observed=observed, failures=tuple(failures))


@rule(
    "VR-016",
    summary="Every digest field matches ^sha256:[0-9a-f]{64}$; uppercase hex fails",
    population="digest-valued entry fields",
)
def _vr_016(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    observed = 0
    for entry in corpus.entries():
        for name, value in _digest_fields(entry.payload):
            observed += 1
            if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
                failures.append(
                    entry.failure(
                        "VR-016",
                        f"{name} is {value!r}; the recorded form is "
                        f"{DIGEST_PATTERN.pattern} - uppercase hexadecimal fails",
                    )
                )
    return RuleOutcome(observed=observed, failures=tuple(failures))


def _field_set_outcome(corpus: Corpus, layer: str, rule_id: str) -> RuleOutcome:
    """VR-017 and VR-027, which are one rule read in two directions.

    Written once because the asymmetry is symmetric: each layer carries exactly
    the four common fields plus its own set, and **none** of the other layer's.
    Two hand-written copies would be two places for the field lists to drift
    from `manifest.py`'s, which is what the exported constants exist to prevent.
    """
    own = REAL_ONLY_FIELDS if layer == LAYER_REAL else SYNTHETIC_ONLY_FIELDS
    prohibited = SYNTHETIC_ONLY_FIELDS if layer == LAYER_REAL else REAL_ONLY_FIELDS
    applicable = COMMON_FIELDS | own

    failures: list[Failure] = []
    entries = corpus.entry_list(layer)
    for entry in entries:
        present = set(entry.payload)
        missing = sorted(applicable - present)
        if missing:
            failures.append(
                entry.failure(rule_id, f"a {layer} entry is missing required field(s) {missing}")
            )
        carried = sorted(present & prohibited)
        if carried:
            failures.append(
                entry.failure(
                    rule_id,
                    f"a {layer} entry carries prohibited field(s) {carried}; a document that "
                    "was not obtained that way must not record provenance it does not have",
                )
            )
        extra = sorted(present - applicable - prohibited)
        if extra:
            failures.append(
                entry.failure(
                    rule_id,
                    f"a {layer} entry carries field(s) {extra} outside its applicable set",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-017",
    summary="A REAL entry carries the four common fields plus FR-008's eight, and none of "
    "FR-009's seven",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_017(corpus: Corpus) -> RuleOutcome:
    return _field_set_outcome(corpus, LAYER_REAL, "VR-017")


@rule(
    "VR-027",
    summary="A SYNTHETIC entry carries the four common fields plus FR-009's seven, and none "
    "of FR-008's eight",
    population="SYNTHETIC entries",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_027(corpus: Corpus) -> RuleOutcome:
    return _field_set_outcome(corpus, LAYER_SYNTHETIC, "VR-027")


@rule(
    "VR-058",
    summary="Manifests carry no version, revision, generated_at or updated field",
    population="manifests",
)
def _vr_058(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    for reading in corpus.readable_readings:
        document = reading.document or {}
        present = sorted(marker for marker in DRIFT_MARKERS if marker in document)
        if present:
            failures.append(
                Failure(
                    "VR-058",
                    f"the manifest carries hand-maintained drift marker(s) {present}; such a "
                    "marker records drift without detecting it, and generated_at would "
                    "additionally rewrite the file on every run",
                    reading.location_id,
                )
            )
    return RuleOutcome(observed=len(corpus.readable_readings), failures=tuple(failures))


@rule(
    "VR-063",
    summary="Each manifest's entries are sorted ascending by location in codepoint order",
    population="manifests",
)
def _vr_063(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    for reading in corpus.readable_readings:
        locations = [
            payload.get("location")
            for payload in reading.entry_payloads
            if isinstance(payload.get("location"), str)
        ]
        if locations != sorted(locations):
            failures.append(
                Failure(
                    "VR-063",
                    f"entries are not sorted ascending by location: recorded {locations}, "
                    f"expected {sorted(locations)}",
                    reading.location_id,
                )
            )
    return RuleOutcome(observed=len(corpus.readable_readings), failures=tuple(failures))


# ---------------------------------------------------------------------------
# T028 - location topology and file<->entry reconciliation
# VR-004, VR-005, VR-006, VR-007, VR-010, VR-011, VR-013, VR-059, VR-060,
# VR-064, VR-065
#
# Every stat below is non-following. A symbolic link to a regular file passes a
# link-following `is_file()` test exactly, which is why VR-067 is a separate
# rule and why nothing here may be written with `Path.is_file()`.
# ---------------------------------------------------------------------------

#: FR-018a's closure, by exact path relative to the corpus root rather than by
#: basename: a `datasheet.md` in the wrong directory is a stray file, and the
#: paths are fixed by `data-model.md` §Physical Artifacts.
SUPPORTING_ARTIFACTS: tuple[str, ...] = (
    "manifest.schema.json",
    "real/exclusions.json",
    "real/retrieval-policy.json",
    "synthetic/datasheet.md",
    "synthetic/equipment-category-map.json",
    "synthetic/field-label-vocabulary.json",
    "synthetic/generation-config.json",
)


def _is_regular_file(path: Path) -> bool:
    """A regular file under a **non-following** stat (VR-010, VR-067)."""
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:  # pragma: no cover - a path the platform refuses to stat
        return False


@rule(
    "VR-004",
    summary="Discovered locations equal declared ones; location_id equals the directory path",
    population="corpus locations discovered by the walk",
)
def _vr_004(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    readings = corpus.readable_readings
    for reading in readings:
        declared = reading.document.get("location_id") if reading.document else None
        if declared != reading.location_id:
            failures.append(
                Failure(
                    "VR-004",
                    f"the manifest declares location_id {declared!r} but sits at "
                    f"{reading.location_id!r} relative to the corpus root",
                    reading.location_id,
                )
            )
    declared_ids = [reading.document.get("location_id") for reading in readings if reading.document]
    duplicates = sorted({value for value in declared_ids if declared_ids.count(value) > 1})
    if duplicates:
        failures.append(
            Failure("VR-004", f"two manifests declare the same location_id: {duplicates}")
        )
    return RuleOutcome(observed=len(readings), failures=tuple(failures))


@rule(
    "VR-005",
    summary="Exactly five SYNTHETIC locations, in bijection with the roster's projects",
    population="SYNTHETIC corpus locations",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_005(corpus: Corpus) -> RuleOutcome:
    synthetic = [
        reading for reading in corpus.readable_readings if reading.declared_layer == LAYER_SYNTHETIC
    ]
    roster = corpus.cached("roster", read_roster)
    if isinstance(roster, Exception):
        return RuleOutcome(
            observed=len(synthetic),
            failures=(Failure("VR-005", f"the roster could not be read: {roster}"),),
        )

    expected = {project.id for project in roster.projects}
    recorded = [reading.document.get("project_id") for reading in synthetic if reading.document]
    failures: list[Failure] = []
    if len(synthetic) != len(expected):
        failures.append(
            Failure(
                "VR-005",
                f"the roster names {len(expected)} projects but {len(synthetic)} SYNTHETIC "
                "location(s) were discovered; neither a missing nor a sixth location passes",
            )
        )
    if set(recorded) != expected:
        failures.append(
            Failure(
                "VR-005",
                f"SYNTHETIC project_id values {sorted(map(str, set(recorded)))} are not a "
                f"bijection onto the roster's project ids {sorted(expected)}",
            )
        )
    elif len(recorded) != len(set(recorded)):
        failures.append(
            Failure("VR-005", f"two SYNTHETIC locations record one project_id: {recorded}")
        )
    return RuleOutcome(observed=len(synthetic), failures=tuple(failures))


@rule(
    "VR-006",
    summary="project_id is present iff SYNTHETIC, equals the final path segment, and is a "
    "roster project id",
    population="manifests",
)
def _vr_006(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    readings = corpus.in_scope_readings
    roster = corpus.cached("roster", read_roster)
    known = None if isinstance(roster, Exception) else {p.id for p in roster.projects}

    for reading in readings:
        document = reading.document or {}
        project_id = document.get("project_id")
        synthetic = reading.declared_layer == LAYER_SYNTHETIC
        if synthetic and project_id is None:
            failures.append(
                Failure("VR-006", "a SYNTHETIC manifest carries no project_id", reading.location_id)
            )
            continue
        if not synthetic:
            if project_id is not None:
                failures.append(
                    Failure(
                        "VR-006",
                        f"a {reading.declared_layer} manifest carries project_id "
                        f"{project_id!r}; the field is SYNTHETIC-only",
                        reading.location_id,
                    )
                )
            continue
        final_segment = reading.location_id.rsplit("/", 1)[-1]
        if project_id != final_segment:
            failures.append(
                Failure(
                    "VR-006",
                    f"project_id {project_id!r} does not equal the final segment of "
                    f"location_id ({final_segment!r})",
                    reading.location_id,
                )
            )
        if known is not None and project_id not in known:
            failures.append(
                Failure(
                    "VR-006",
                    f"project_id {project_id!r} is not one of the roster's project ids "
                    f"{sorted(known)}",
                    reading.location_id,
                )
            )
    return RuleOutcome(observed=len(readings), failures=tuple(failures))


@rule(
    "VR-007",
    summary="Every entry's layer equals its manifest's, so a location holds exactly one layer",
    population="entries",
)
def _vr_007(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list()
    failures = tuple(
        entry.failure(
            "VR-007",
            f"the entry declares layer {entry.layer!r} but its manifest declares "
            f"{entry.reading.declared_layer!r}; a corpus location holds exactly one layer",
        )
        for entry in entries
        if entry.layer != entry.reading.declared_layer
    )
    return RuleOutcome(observed=len(entries), failures=failures)


@rule(
    "VR-010",
    summary="Every entry names an existing regular file",
    population="entries",
)
def _vr_010(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list()
    failures = tuple(
        entry.failure(
            "VR-010",
            f"names no existing regular file at {entry.document_path} "
            "(tested without following links)",
        )
        for entry in entries
        if not _is_regular_file(entry.document_path)
    )
    return RuleOutcome(observed=len(entries), failures=failures)


@rule(
    "VR-011",
    summary="Every file in a location has exactly one entry and every entry exactly one file",
    population="files and entries reconciled across every location",
)
def _vr_011(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    observed = 0
    for reading in corpus.in_scope_readings:
        # Non-recursive, which VR-064's flatness is what makes determinable.
        on_disk = {
            path.name
            for path in corpus.files
            if path.parent == reading.path and path.name != MANIFEST_FILENAME
        }
        recorded = [
            payload.get("location")
            for payload in reading.entry_payloads
            if isinstance(payload.get("location"), str)
        ]
        observed += len(on_disk) + len(recorded)

        unmanifested = sorted(on_disk - set(recorded))
        failures.extend(
            Failure(
                "VR-011",
                "the file is present in the location but no entry describes it; "
                f"{MANIFEST_FILENAME} is the single exemption FR-006 names",
                reading.location_id,
                name,
            )
            for name in unmanifested
        )
        missing = sorted(set(recorded) - on_disk)
        failures.extend(
            Failure(
                "VR-011",
                "the entry describes a file that is not present in the location",
                reading.location_id,
                name,
            )
            for name in missing
        )
        duplicates = sorted({name for name in recorded if recorded.count(name) > 1})
        failures.extend(
            Failure(
                "VR-011",
                "two entries in one manifest name this file; location is the entry's key",
                reading.location_id,
                name,
            )
            for name in duplicates
        )
    return RuleOutcome(observed=observed, failures=tuple(failures))


@rule(
    "VR-013",
    summary="Every corpus file begins with %PDF- and opens under the PDF reader",
    population="corpus documents opened",
)
def _vr_013(corpus: Corpus) -> RuleOutcome:
    import pdfplumber

    failures: list[Failure] = []
    observed = 0
    for entry in corpus.entry_list():
        path = entry.document_path
        if not _is_regular_file(path):
            # VR-010 reports the absence; opening it here would report the same
            # defect twice under two rules.
            continue
        observed += 1
        try:
            head = path.open("rb").read(5)
        except OSError as exc:
            failures.append(entry.failure("VR-013", f"cannot be read: {exc}"))
            continue
        if head != b"%PDF-":
            failures.append(
                entry.failure(
                    "VR-013",
                    f"does not begin with %PDF- (found {head!r}); a .pdf extension is not "
                    "evidence of format",
                )
            )
            continue
        # FR-001a: the vendored layer is untrusted third-party input and there
        # is no sanitization pass between the committed bytes and the reader. A
        # document that will not open is an enumerated failure, never a
        # document skipped so the run can continue.
        try:
            with pdfplumber.open(path) as document:
                len(document.pages)
        except Exception as exc:  # noqa: BLE001 - any reader failure is this rule's
            failures.append(
                entry.failure(
                    "VR-013", f"does not open under the PDF reader: {type(exc).__name__}: {exc}"
                )
            )
    return RuleOutcome(observed=observed, failures=tuple(failures))


def _git(toplevel: Path, *args: str) -> tuple[int, str] | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(toplevel), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    return completed.returncode, completed.stdout


@rule(
    "VR-059",
    summary="Every corpus file and every manifest is tracked by git and not ignored",
    population="files under the corpus root",
)
def _vr_059(corpus: Corpus) -> RuleOutcome:
    """Tracked, not merely present (FR-028, SC-016).

    Only the *tracked* half is tested, and that is sufficient rather than
    partial: a tracked file ships to a clone whatever `.gitignore` says, and an
    ignored file that is not tracked fails the tracked test already.

    **Stated limit.** The rule reports SKIPPED, never PASS, in three cases it
    cannot judge: git is absent, the corpus root is not inside a work tree, or
    the work tree tracks no file under the corpus root at all. The third is the
    one that matters, and it is why the rule cannot be made to pass vacuously
    over a temporary tree that happens to sit beneath somebody's home-directory
    repository: a corpus root with zero tracked files is not a corpus this
    repository ships, and reporting forty untracked files for it would make the
    rule fire on every fixture rather than on a defect.
    """
    probe = _git(corpus.root, "rev-parse", "--show-toplevel")
    if probe is None:
        return RuleOutcome.skip("git is not available on this machine")
    code, out = probe
    if code != 0 or not out.strip():
        return RuleOutcome.skip("the corpus root is not inside a git work tree")
    toplevel = Path(out.strip())

    listing = _git(toplevel, "ls-files", "-z", "--", str(corpus.root))
    if listing is None or listing[0] != 0:
        return RuleOutcome.skip("git could not list the tracked files under the corpus root")
    tracked = {(toplevel / Path(entry)).resolve() for entry in listing[1].split("\0") if entry}
    if not tracked:
        return RuleOutcome.skip(
            "the work tree tracks no file under this corpus root, so it is not a corpus "
            "this repository ships and tracking cannot be judged over it"
        )

    failures: list[Failure] = []
    for path in corpus.files:
        if path.resolve() not in tracked:
            failures.append(
                Failure(
                    "VR-059",
                    f"{path.relative_to(corpus.root).as_posix()} is not tracked by git; a "
                    "clone that has never run the generator must hold the complete corpus",
                )
            )
    return RuleOutcome(observed=len(corpus.files), failures=tuple(failures))


def _files_outside_locations(corpus: Corpus) -> tuple[Path, ...]:
    return tuple(path for path in corpus.files if not corpus.is_inside_a_location(path))


@rule(
    "VR-060",
    summary="No *.pdf exists under the corpus root outside a corpus location",
    population="files under the corpus root outside every location",
)
def _vr_060(corpus: Corpus) -> RuleOutcome:
    outside = _files_outside_locations(corpus)
    failures = tuple(
        Failure(
            "VR-060",
            f"{path.relative_to(corpus.root).as_posix()} is a PDF outside every corpus "
            "location, so it requires no manifest entry and would sit unmanifested",
        )
        for path in outside
        if path.suffix.lower() == ".pdf"
    )
    return RuleOutcome(observed=len(outside), failures=failures)


@rule(
    "VR-064",
    summary="Every corpus location is flat: it contains no subdirectory",
    population="corpus locations",
)
def _vr_064(corpus: Corpus) -> RuleOutcome:
    location_dirs = corpus.location_dirs()
    failures = tuple(
        Failure(
            "VR-064",
            f"holds subdirectory {directory.name!r}; a nested directory could carry an "
            "unmanifested document that VR-060 does not reach",
            directory.parent.relative_to(corpus.root).as_posix(),
        )
        for directory in corpus.directories
        if directory.parent in location_dirs
    )
    return RuleOutcome(observed=len(location_dirs), failures=failures)


@rule(
    "VR-065",
    summary="Outside every location the corpus root holds only the seven supporting artifacts",
    population="files under the corpus root outside every location",
)
def _vr_065(corpus: Corpus) -> RuleOutcome:
    outside = _files_outside_locations(corpus)
    admissible = set(SUPPORTING_ARTIFACTS)
    failures = tuple(
        Failure(
            "VR-065",
            f"{path.relative_to(corpus.root).as_posix()} is not one of the seven committed "
            f"supporting artifacts {list(SUPPORTING_ARTIFACTS)}; any other file here sits "
            "outside a location requiring no entry, whatever its extension",
        )
        for path in outside
        if path.relative_to(corpus.root).as_posix() not in admissible
    )
    return RuleOutcome(observed=len(outside), failures=failures)


# ---------------------------------------------------------------------------
# T029 - path containment, the symbolic-link prohibition, and case collision
# VR-009, VR-067, VR-068
#
# All three read the **parsed** manifest rather than the schema-accepted one.
# The schema's `location` pattern already rejects a separator, a `..` segment
# and an absolute path, so a rule reading only schema-accepted payloads could
# never be shown failing on the traversal sequence it exists to refuse — and
# VR-009 is a security control (CWE-22, CWE-23, CWE-36), not a restatement. A
# control whose failing direction cannot be demonstrated is a control nobody
# can check (SC-025).
# ---------------------------------------------------------------------------


@rule(
    "VR-009",
    summary="location resolves to a real path inside its own location directory, resolved "
    "first and compared second",
    population="entry location values",
)
def _vr_009(corpus: Corpus) -> RuleOutcome:
    failures: list[Failure] = []
    entries = corpus.entry_list()
    for entry in entries:
        recorded = entry.payload.get("location")
        if not isinstance(recorded, str):
            failures.append(entry.failure("VR-009", f"location is not a string: {recorded!r}"))
            continue
        try:
            # The declared base is one value: the entry's own corpus location
            # directory. `data/corpus/` and `data/` are consequences of that,
            # not alternatives to it.
            resolve_within(entry.reading.path, recorded)
        except CorpusPathError as exc:
            failures.append(entry.failure("VR-009", str(exc)))
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-067",
    summary="No symbolic link anywhere under the corpus root",
    population="paths tested under the corpus root",
)
def _vr_067(corpus: Corpus) -> RuleOutcome:
    """A link is a failure naming the path, never a silently resolved indirection.

    Stated separately from `CHECK(is a regular file)` because a link-following
    stat cannot tell the two apart: a symbolic link to a regular file elsewhere
    on the machine satisfies a regular-file test exactly, and its `content_hash`
    would then be computed over bytes that live outside the repository and are
    not what a clone receives (CWE-59, CWE-61, CWE-64, CWE-73).
    """
    failures = tuple(
        Failure(
            "VR-067",
            f"{path.relative_to(corpus.root).as_posix()} is a symbolic link; a link's bytes "
            "live outside the repository and are not what a clone receives",
        )
        for path in corpus.symlinks
    )
    observed = len(corpus.files) + len(corpus.directories) + len(corpus.symlinks)
    return RuleOutcome(observed=observed, failures=failures)


@rule(
    "VR-068",
    summary="location values are NFC and matched by exact codepoint equality; no two collide "
    "under case folding within one manifest",
    population="entry location values",
)
def _vr_068(corpus: Corpus) -> RuleOutcome:
    """Unicode form and case, decided here rather than by the filesystem.

    Without the second half, VR-011's entry-to-file bijection is decided by the
    platform: on a case-folding or NFD-normalizing filesystem two
    codepoint-distinct entries can name one file, which makes VR-011 pass on one
    machine and fail on another over identical committed content.
    """
    import unicodedata

    failures: list[Failure] = []
    observed = 0
    for reading in corpus.readable_readings:
        recorded: list[str] = []
        on_disk = {
            path.name
            for path in corpus.files
            if path.parent == reading.path and path.name != MANIFEST_FILENAME
        }
        for index, payload in enumerate(reading.entry_payloads):
            value = payload.get("location")
            if not isinstance(value, str):
                continue
            observed += 1
            entry = EntryRef(reading, index, payload)
            normalized = unicodedata.normalize("NFC", value)
            if normalized != value:
                failures.append(
                    entry.failure(
                        "VR-068",
                        f"location is not NFC-normalized; recorded {value!r}, NFC is "
                        f"{normalized!r}",
                    )
                )
            recorded.append(normalized)
            if on_disk and normalized not in on_disk:
                folded = {name for name in on_disk if name.casefold() == normalized.casefold()}
                if folded:
                    failures.append(
                        entry.failure(
                            "VR-068",
                            f"location matches a directory entry only under case folding "
                            f"({sorted(folded)}); matching is exact codepoint equality after "
                            "NFC normalization, never case-insensitive or locale-aware "
                            "collation",
                        )
                    )

        grouped: dict[str, list[str]] = {}
        for value in recorded:
            grouped.setdefault(value.casefold(), []).append(value)
        for folded, values in sorted(grouped.items()):
            distinct = sorted(set(values))
            if len(distinct) > 1:
                failures.append(
                    Failure(
                        "VR-068",
                        f"location values {distinct} differ in codepoint order but are equal "
                        f"under case folding ({folded!r}); on a case-folding filesystem they "
                        "name one file, which would make VR-011 platform-dependent",
                        reading.location_id,
                    )
                )
    return RuleOutcome(observed=observed, failures=tuple(failures))


# ---------------------------------------------------------------------------
# T030 - digest recomputation
# VR-012, VR-018
# ---------------------------------------------------------------------------


@rule(
    "VR-012",
    summary="content_hash is recomputed from the file's raw bytes and compared",
    population="corpus documents digested",
)
def _vr_012(corpus: Corpus) -> RuleOutcome:
    """The recorded value is never trusted as evidence of itself (FR-007).

    Recomputed on every run rather than compared against a stored side-record,
    because a stored side-record is a second copy of the same claim.
    """
    failures: list[Failure] = []
    observed = 0
    for entry in corpus.entry_list():
        path = entry.document_path
        if not _is_regular_file(path):
            continue  # VR-010 reports the absence; this rule has nothing to digest
        observed += 1
        recorded = entry.payload.get("content_hash")
        try:
            recomputed = content_hash_of_file(path)
        except ManifestError as exc:
            failures.append(entry.failure("VR-012", f"cannot be digested: {exc}"))
            continue
        if recorded != recomputed:
            failures.append(
                entry.failure(
                    "VR-012",
                    f"content_hash records {recorded!r} but the committed file digests to "
                    f"{recomputed!r}; the file was modified after it was manifested",
                )
            )
    return RuleOutcome(observed=observed, failures=tuple(failures))


@rule(
    "VR-018",
    summary="content_hash equals upstream_digest for every REAL entry",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_018(corpus: Corpus) -> RuleOutcome:
    """An **internal-consistency check, not a provenance proof.**

    Its force is conditional on FR-008c — that the digest was taken from the
    response body at retrieval, before the file was written. Nothing offline can
    distinguish such a value from one back-filled out of the committed file, and
    back-filled this comparison is a tautology that always passes. The residual
    is published under `data-model.md` §Uncovered Requirements (FR-001,
    FR-008a / FR-008c) rather than counted as coverage here; only FR-008b's
    re-fetch reaches the source, and it is deliberately outside the required
    check.
    """
    entries = corpus.entry_list(LAYER_REAL)
    failures = tuple(
        entry.failure(
            "VR-018",
            f"content_hash {entry.payload.get('content_hash')!r} differs from "
            f"upstream_digest {entry.payload.get('upstream_digest')!r}",
        )
        for entry in entries
        if entry.payload.get("content_hash") != entry.payload.get("upstream_digest")
    )
    return RuleOutcome(observed=len(entries), failures=failures)


# ---------------------------------------------------------------------------
# T031 - license basis
# VR-008, VR-023, VR-024, VR-028
# ---------------------------------------------------------------------------


def _basis(entry: EntryRef) -> Mapping[str, object]:
    value = entry.payload.get("license_basis")
    return value if isinstance(value, Mapping) else {}


@rule(
    "VR-008",
    summary="Within one manifest the distinct license_basis.basis_id set has cardinality 1",
    population="manifests",
)
def _vr_008(corpus: Corpus) -> RuleOutcome:
    """The no-mixed-licenses rule, over the governing component only.

    FR-011 makes the REAL basis carry a per-document identifier and FR-013
    forbids more than one basis per location; read over whole bases those two
    would make every real location illegal, because every document's identifier
    differs by construction. The comparison is therefore over `basis_id`, the
    governing component FR-012a mandates (`data-model.md` §License Basis).
    """
    failures: list[Failure] = []
    readings = corpus.in_scope_readings
    for reading in readings:
        ids = sorted(
            {
                payload["license_basis"]["basis_id"]
                for payload in reading.entry_payloads
                if isinstance(payload.get("license_basis"), Mapping)
                and isinstance(payload["license_basis"].get("basis_id"), str)
            }
        )
        if len(ids) > 1:
            failures.append(
                Failure(
                    "VR-008",
                    f"the location mixes license bases: distinct basis_id values {ids}; "
                    "licenses must not be mixed within a corpus location",
                    reading.location_id,
                )
            )
    return RuleOutcome(observed=len(readings), failures=tuple(failures))


@rule(
    "VR-023",
    summary="A REAL license basis carries four non-empty components, a closed statute "
    "agreeing with basis_id, and a document_identifier equal to its canonical composition",
    population="REAL license bases",
    layers=(LAYER_REAL,),
)
def _vr_023(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    policy = corpus.cached("policy", lambda: load_policy(root=corpus.root))
    failures: list[Failure] = []
    for entry in entries:
        basis = _basis(entry)
        missing = sorted(
            name
            for name in ("basis_id", "statute", "document_identifier", "point_of_use_check")
            if not str(basis.get(name) or "").strip()
        )
        if missing:
            failures.append(
                entry.failure("VR-023", f"the REAL license basis is missing component(s) {missing}")
            )
            continue

        basis_id = basis["basis_id"]
        statute = basis["statute"]
        expected_statute = STATUTE_FOR_BASIS.get(basis_id)
        if expected_statute is None:
            failures.append(
                entry.failure(
                    "VR-023",
                    f"basis_id {basis_id!r} is not the REAL basis; no statute is defined for it",
                )
            )
        elif statute != expected_statute:
            failures.append(
                entry.failure(
                    "VR-023",
                    f"statute {statute!r} does not agree with basis_id {basis_id!r}, whose "
                    f"single statute is {expected_statute!r}; a citation conferring no "
                    "public-domain status would otherwise satisfy every stated rule",
                )
            )

        if isinstance(policy, Exception):
            failures.append(
                entry.failure(
                    "VR-023",
                    f"the retrieval policy could not be read, so the canonical composition of "
                    f"document_identifier could not be recomputed: {policy}",
                )
            )
            continue
        variant_token = entry.payload.get("agency_variant")
        try:
            variant = policy.variant(str(variant_token))
            composed = variant.document_identifier(
                str(entry.payload.get("masterformat_section")),
                str(entry.payload.get("revision_date")),
            )
        except (RetrievalPolicyError, ManifestError) as exc:
            failures.append(
                entry.failure("VR-023", f"document_identifier could not be recomposed: {exc}")
            )
            continue
        if basis["document_identifier"] != composed:
            failures.append(
                entry.failure(
                    "VR-023",
                    f"document_identifier records {basis['document_identifier']!r} but the "
                    f"canonical composition of this entry's own section, variant suffix and "
                    f"revision date is {composed!r}; the restatement has drifted from what it "
                    "restates",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-024",
    summary="point_of_use_check is NO_COPYRIGHTED_EXCERPT_FOUND, the single admissible value",
    population="REAL license bases",
    layers=(LAYER_REAL,),
)
def _vr_024(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    failures = tuple(
        entry.failure(
            "VR-024",
            f"point_of_use_check is {_basis(entry).get('point_of_use_check')!r}; any outcome "
            f"other than {POINT_OF_USE_CHECK!r} means the candidate was excluded under FR-005 "
            "and its record belongs in the exclusion ledger, never in a manifest",
        )
        for entry in entries
        if _basis(entry).get("point_of_use_check") != POINT_OF_USE_CHECK
    )
    return RuleOutcome(observed=len(entries), failures=failures)


@rule(
    "VR-028",
    summary="A SYNTHETIC license basis carries the project basis id, both const assertions, "
    "and a non-empty statement",
    population="SYNTHETIC license bases",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_028(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_SYNTHETIC)
    failures: list[Failure] = []
    for entry in entries:
        basis = _basis(entry)
        if basis.get("basis_id") != SYNTHETIC_BASIS_ID:
            failures.append(
                entry.failure(
                    "VR-028",
                    f"basis_id is {basis.get('basis_id')!r}, expected {SYNTHETIC_BASIS_ID!r}",
                )
            )
        if basis.get("generated_by_this_project") is not True:
            failures.append(
                entry.failure(
                    "VR-028",
                    f"generated_by_this_project is "
                    f"{basis.get('generated_by_this_project')!r}, expected true",
                )
            )
        if basis.get("third_party_rights") != THIRD_PARTY_RIGHTS:
            failures.append(
                entry.failure(
                    "VR-028",
                    f"third_party_rights is {basis.get('third_party_rights')!r}, expected "
                    f"{THIRD_PARTY_RIGHTS!r}",
                )
            )
        if not str(basis.get("statement") or "").strip():
            failures.append(
                entry.failure(
                    "VR-028",
                    "statement is absent or empty; the two constants are the machine-checkable "
                    "half and the statement is the human-readable one",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


# ---------------------------------------------------------------------------
# T032 - REAL field values
# VR-019, VR-020, VR-021, VR-022
# ---------------------------------------------------------------------------


@rule(
    "VR-019",
    summary="retrieval_response_status is 200",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_019(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    failures = tuple(
        entry.failure(
            "VR-019",
            f"retrieval_response_status is "
            f"{entry.payload.get('retrieval_response_status')!r}; a non-200 means the document "
            "was not retrieved and its record belongs in the exclusion ledger",
        )
        for entry in entries
        if entry.payload.get("retrieval_response_status") != 200
    )
    return RuleOutcome(observed=len(entries), failures=failures)


@rule(
    "VR-020",
    summary="retrieved_at is RFC 3339, UTC with a Z suffix, and not in the future",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_020(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    now = datetime.now(UTC)
    failures: list[Failure] = []
    for entry in entries:
        value = entry.payload.get("retrieved_at")
        if not isinstance(value, str) or not RETRIEVED_AT_PATTERN.fullmatch(value):
            failures.append(
                entry.failure(
                    "VR-020",
                    f"retrieved_at is {value!r}; RFC 3339 in UTC with a Z suffix is required, "
                    "so a numeric offset is rejected rather than normalized",
                )
            )
            continue
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            failures.append(entry.failure("VR-020", f"retrieved_at is not a real instant: {exc}"))
            continue
        if moment > now:
            failures.append(
                entry.failure(
                    "VR-020",
                    f"retrieved_at {value} is in the future as of {now.isoformat()}; it is a "
                    "historical constant recorded once, never re-read",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-021",
    summary="(masterformat_section, agency_variant, revision_date) is unique across the real "
    "layer, each in its recorded form",
    population="REAL document identities",
    layers=(LAYER_REAL,),
)
def _vr_021(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    policy = corpus.cached("policy", lambda: load_policy(root=corpus.root))
    known = None if isinstance(policy, Exception) else set(policy.agency_variants)

    failures: list[Failure] = []
    identities: dict[tuple[str, str, str], list[EntryRef]] = {}
    for entry in entries:
        section = entry.payload.get("masterformat_section")
        variant = entry.payload.get("agency_variant")
        revision = entry.payload.get("revision_date")
        if not isinstance(section, str) or not MASTERFORMAT_PATTERN.fullmatch(section):
            failures.append(
                entry.failure(
                    "VR-021",
                    f"masterformat_section is {section!r}; the bare six-digit form is required "
                    "so agency variants of one number count once toward coverage",
                )
            )
        if not isinstance(revision, str) or not REVISION_DATE_PATTERN.fullmatch(revision):
            failures.append(
                entry.failure(
                    "VR-021",
                    f"revision_date is {revision!r}; month precision is the precision UFGS "
                    "publishes at",
                )
            )
        if known is not None and variant not in known:
            failures.append(
                entry.failure(
                    "VR-021",
                    f"agency_variant {variant!r} is outside the closed set {sorted(known)} the "
                    "retrieval policy declares",
                )
            )
        identities.setdefault((str(section), str(variant), str(revision)), []).append(entry)

    for identity, sharing in sorted(identities.items()):
        if len(sharing) > 1:
            failures.extend(
                entry.failure(
                    "VR-021",
                    f"the real identity {identity} is recorded by "
                    f"{len(sharing)} entries; two agency variants of one number are two "
                    "documents, but one identity is one document",
                )
                for entry in sharing
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-022",
    summary="source_location is an absolute https URL whose host is in the policy allow-list "
    "by exact equality",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_022(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    policy = corpus.cached("policy", lambda: load_policy(root=corpus.root))
    if isinstance(policy, Exception):
        return RuleOutcome(
            observed=len(entries),
            failures=(Failure("VR-022", f"the retrieval policy could not be read: {policy}"),),
        )

    failures: list[Failure] = []
    for entry in entries:
        value = entry.payload.get("source_location")
        if not isinstance(value, str):
            failures.append(entry.failure("VR-022", f"source_location is {value!r}"))
            continue
        split = urlsplit(value)
        if split.scheme != "https" or not split.hostname:
            failures.append(
                entry.failure(
                    "VR-022",
                    f"source_location {value!r} is not an absolute https URL",
                )
            )
            continue
        # Exact host equality after lowercasing, never suffix or substring
        # containment: `wbdg.org.example.invalid` is a different host from an
        # allow-listed `wbdg.org` and is refused.
        if not policy.allows_host(split.hostname):
            failures.append(
                entry.failure(
                    "VR-022",
                    f"source_location host {split.hostname!r} is not in the policy allow-list "
                    f"{list(policy.source_hosts)}; membership is exact host equality, never a "
                    "suffix match",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


# ---------------------------------------------------------------------------
# T033 - policy agreement and the exclusion ledger
# VR-025, VR-026, VR-062
#
# Note on the policy's shape: `retrieval-policy.json`'s `target_sections` is an
# **array of objects** keyed by `masterformat_section`, each carrying its own
# lead-time justification - not the `array<string>` `data-model.md` types it as.
# `sources.py` parses the committed shape and exposes the distinct section
# numbers as `RetrievalPolicy.target_sections`, which is the population VR-025
# counts; this module codes against that property rather than against the
# document type.
# ---------------------------------------------------------------------------


@rule(
    "VR-025",
    summary="The real layer holds at least 20 documents over at least 6 distinct target "
    "sections and includes the anchor 01 33 00",
    population="REAL documents",
    layers=(LAYER_REAL,),
)
def _vr_025(corpus: Corpus) -> RuleOutcome:
    entries = corpus.entry_list(LAYER_REAL)
    policy = corpus.cached("policy", lambda: load_policy(root=corpus.root))
    if isinstance(policy, Exception):
        return RuleOutcome(
            observed=len(entries),
            failures=(Failure("VR-025", f"the retrieval policy could not be read: {policy}"),),
        )

    recorded = {
        entry.payload["masterformat_section"]
        for entry in entries
        if isinstance(entry.payload.get("masterformat_section"), str)
    }
    targeted = recorded & set(policy.target_sections)

    failures: list[Failure] = []
    if len(entries) < REAL_DOCUMENT_FLOOR:
        failures.append(
            Failure(
                "VR-025",
                f"the real layer holds {len(entries)} document(s), below the floor of "
                f"{REAL_DOCUMENT_FLOOR}",
            )
        )
    if len(targeted) < DISTINCT_SECTION_FLOOR:
        failures.append(
            Failure(
                "VR-025",
                f"the real layer spans {len(targeted)} distinct target section(s) "
                f"{sorted(targeted)}, below the floor of {DISTINCT_SECTION_FLOOR}; distinct "
                "sections are counted by section number, so agency variants count once",
            )
        )
    if policy.anchor_section not in recorded:
        failures.append(
            Failure(
                "VR-025",
                f"the anchor section {policy.anchor_section!r} is absent; it defines the "
                "submittal conventions every other document is read against",
            )
        )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-026",
    summary="The exclusion ledger parses, every record is complete, and no excluded candidate "
    "appears in the real manifest",
    population="exclusion-ledger records",
    layers=(LAYER_REAL,),
)
def _vr_026(corpus: Corpus) -> RuleOutcome:
    """The ledger's **integrity**; its completeness is not checkable.

    A candidate dropped without a record leaves no artifact, so nothing here can
    observe it. That half is published under `data-model.md` §Uncovered
    Requirements (FR-004 / SC-003) rather than claimed.
    """
    ledger = corpus.cached("ledger", lambda: read_ledger(root=corpus.root))
    if isinstance(ledger, Exception):
        return RuleOutcome(
            observed=0,
            failures=(Failure("VR-026", f"the exclusion ledger did not parse: {ledger}"),),
        )

    manifested = {
        payload["license_basis"]["document_identifier"]
        for entry in corpus.entry_list(LAYER_REAL)
        for payload in (entry.payload,)
        if isinstance(payload.get("license_basis"), Mapping)
        and isinstance(payload["license_basis"].get("document_identifier"), str)
    }
    failures = tuple(
        Failure(
            "VR-026",
            f"excluded candidate {record.candidate_identifier!r} (cause {record.cause}) also "
            "appears in the real manifest; a document cannot be both excluded and vendored",
        )
        for record in ledger.records
        if record.candidate_identifier in manifested
    )
    # Per-record completeness is enforced by `ExclusionRecord.__post_init__`,
    # which `read_ledger` raises through - a malformed record is the parse
    # failure reported above rather than a record this loop could see.
    return RuleOutcome(observed=len(ledger.records), failures=failures)


@rule(
    "VR-062",
    summary="issuing_body equals what the policy records for the entry's agency_variant",
    population="REAL entries",
    layers=(LAYER_REAL,),
)
def _vr_062(corpus: Corpus) -> RuleOutcome:
    """An entry naming one agency's variant and another's issuing body describes
    two different documents, and both fields are otherwise well-formed, so
    nothing else catches the contradiction."""
    entries = corpus.entry_list(LAYER_REAL)
    policy = corpus.cached("policy", lambda: load_policy(root=corpus.root))
    if isinstance(policy, Exception):
        return RuleOutcome(
            observed=len(entries),
            failures=(Failure("VR-062", f"the retrieval policy could not be read: {policy}"),),
        )

    failures: list[Failure] = []
    for entry in entries:
        token = entry.payload.get("agency_variant")
        try:
            variant = policy.variant(str(token))
        except RetrievalPolicyError:
            continue  # VR-021 reports a variant outside the closed set
        if entry.payload.get("issuing_body") != variant.issuing_body:
            failures.append(
                entry.failure(
                    "VR-062",
                    f"issuing_body is {entry.payload.get('issuing_body')!r} but the policy "
                    f"records {variant.issuing_body!r} for agency_variant {token!r}",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


# ---------------------------------------------------------------------------
# T042 - roster and generation-input drift
# VR-029, VR-030, VR-061
#
# **The distinction this group exists to keep.** `roster_hash` is the reader's
# canonical-content value and `generation_inputs.*` are raw-byte digests, and
# they are computed differently on purpose (`data-model.md` §The Digest Kinds,
# Kept Distinct). Conflating them makes a roster *reformat* read as drift, which
# the lifecycle table declares it is not, and makes a generation-input edit
# invisible to `sha256sum`. Two rules rather than one is what keeps the two
# procedures legible from the field name.
#
# Both name **every** stale document rather than stopping at the first: a run
# that reported one of twenty-five would be run twenty-five times.
# ---------------------------------------------------------------------------


@rule(
    "VR-029",
    summary="Every SYNTHETIC roster_hash equals read_roster().content_hash as evaluated now",
    population="SYNTHETIC entries",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_029(corpus: Corpus) -> RuleOutcome:
    """Roster drift, against a **live reader call** rather than a stored copy.

    The comparison is against `read_roster().content_hash` — a digest over the
    roster's canonical re-serialized content, not over the roster file's bytes.
    That is why a roster reformat moves nothing here by design (FR-020, and
    `data-model.md` §State & Lifecycle), and why this rule is not the same check
    as VR-061's over the other three generation inputs.

    Detection, not reconciliation: a stale entry is named and the run fails.
    Nothing is regenerated, because a validator that regenerated to validate
    could not tell a corpus defect from a generator defect.
    """
    entries = corpus.entry_list(LAYER_SYNTHETIC)
    roster = corpus.cached("roster", read_roster)
    if isinstance(roster, Exception):
        return RuleOutcome(
            observed=len(entries),
            failures=(Failure("VR-029", f"the roster could not be read: {roster}"),),
        )

    current = roster.content_hash
    failures = tuple(
        entry.failure(
            "VR-029",
            f"roster_hash records {entry.payload.get('roster_hash')!r} but read_roster() now "
            f"yields {current!r}; this document is stale and must be regenerated or the roster "
            "reverted",
        )
        for entry in entries
        if entry.payload.get("roster_hash") != current
    )
    return RuleOutcome(observed=len(entries), failures=failures)


@rule(
    "VR-030",
    summary="generator_id, seed and generation_date equal the committed generation-config values",
    population="SYNTHETIC entries",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_030(corpus: Corpus) -> RuleOutcome:
    """FR-009a's committed constant, checked against the artifact that holds it.

    A generator that stamped a wall-clock date fails here on the first run after
    the constant's date, and would additionally rewrite every manifest on every
    run — the regression VR-042 exists to catch downstream of this one.

    The configuration is read through the generator's own `load_config`, not a
    second parser: a validator that accepted a configuration the generator
    rejects would report agreement with a file the generator could not have
    used. The import is local because it pulls the renderer in behind it, and
    the validator must stay importable without one.
    """
    from model.corpus.generate import load_config

    entries = corpus.entry_list(LAYER_SYNTHETIC)
    config = corpus.cached("generation-config", lambda: load_config(root=corpus.root))
    if isinstance(config, Exception):
        return RuleOutcome(
            observed=len(entries),
            failures=(
                Failure(
                    "VR-030",
                    f"the committed generation configuration could not be read: {config}",
                ),
            ),
        )

    expected = {
        "generator_id": config.generator_id,
        "seed": config.seed,
        "generation_date": config.generation_date,
    }
    failures: list[Failure] = []
    for entry in entries:
        for name, value in expected.items():
            if entry.payload.get(name) != value:
                failures.append(
                    entry.failure(
                        "VR-030",
                        f"{name} records {entry.payload.get(name)!r} but the committed "
                        f"generation configuration holds {value!r}",
                    )
                )
        recorded_date = entry.payload.get("generation_date")
        if not isinstance(recorded_date, str) or not GENERATION_DATE_PATTERN.fullmatch(
            recorded_date
        ):
            failures.append(
                entry.failure(
                    "VR-030",
                    f"generation_date is {recorded_date!r}; "
                    f"{GENERATION_DATE_PATTERN.pattern} is required and the value is a "
                    "committed constant, never a wall-clock read",
                )
            )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


@rule(
    "VR-061",
    summary="generation_inputs names exactly the three committed inputs and each digest equals "
    "that file's current raw bytes",
    population="SYNTHETIC entries",
    layers=(LAYER_SYNTHETIC,),
)
def _vr_061(corpus: Corpus) -> RuleOutcome:
    """Supporting-artifact drift, with the key set treated as untrusted input.

    **Order of operations, stated because it is the control** (CWE-73): every
    key is compared as a **literal string** against the closed three-value set
    of repository-relative paths *before* any filesystem access. A key outside
    that set fails on set equality and is never opened, so no traversal sequence
    in a manifest-supplied key ever reaches a resolution step. Only a key inside
    the set is resolved, and it is resolved through `repository_relative_path`,
    which applies VR-009's ordering and VR-067's link prohibition.

    The roster is **not** a key here. It is the fourth generation input and its
    digest is `roster_hash`, compared by VR-029 against a live reader call,
    because the reader's value is over canonical content while every value in
    this mapping is over raw bytes. A mapping whose values were computed by two
    procedures depending on the key is the conflation the field split prevents.

    Attribution is **by recorded field**: a drifted input names every document
    whose own entry records it, never every synthetic document on the assumption
    that they all share every input.
    """
    entries = corpus.entry_list(LAYER_SYNTHETIC)
    expected = frozenset(GENERATION_INPUT_PATHS)

    failures: list[Failure] = []
    recorders: dict[str, list[EntryRef]] = {}
    for entry in entries:
        recorded = entry.payload.get("generation_inputs")
        if not isinstance(recorded, Mapping):
            failures.append(
                entry.failure(
                    "VR-061",
                    f"generation_inputs is {recorded!r}; a mapping of repository-relative path "
                    "to raw-byte digest is required",
                )
            )
            continue
        keys = frozenset(str(key) for key in recorded)
        if keys != expected:
            failures.append(
                entry.failure(
                    "VR-061",
                    f"generation_inputs names {sorted(keys)}; exactly "
                    f"{sorted(expected)} is required. Unexpected key(s) "
                    f"{sorted(keys - expected)} fail on set equality and are never opened, and "
                    f"missing key(s) {sorted(expected - keys)} leave an input undigested",
                )
            )
        for key in sorted(keys & expected):
            recorders.setdefault(key, []).append(entry)

    for key in sorted(recorders):
        current = corpus.cached(
            f"generation-input:{key}",
            lambda relative=key: sha256_of_file(repository_relative_path(relative, corpus.root)),
        )
        if isinstance(current, Exception):
            failures.append(
                Failure(
                    "VR-061",
                    f"the generation input {key} could not be digested: {current}",
                )
            )
            continue
        for entry in recorders[key]:
            inputs = entry.payload["generation_inputs"]
            if inputs[key] != current:
                failures.append(
                    entry.failure(
                        "VR-061",
                        f"generation_inputs[{key!r}] records {inputs[key]!r} but that file's "
                        f"current raw bytes digest to {current!r}; the input drifted and this "
                        "document was generated from the earlier one",
                    )
                )
    return RuleOutcome(observed=len(entries), failures=tuple(failures))


# ---------------------------------------------------------------------------
# T043 - the synthetic corpus datasheet
# VR-051, VR-052, VR-053, VR-054, VR-055
#
# Presence is a heading check, not a reading (VR-051), and the two disclosures
# of VR-052 are **stated sub-conditions rather than reader judgement**: the
# phrases each must carry are held below as data, so a datasheet that gestures
# at a limit without stating it fails, and so an author knows what the rule
# requires without reading the rule.
# ---------------------------------------------------------------------------

#: FR-027's eight, in the order the datasheet is expected to present them.
DATASHEET_SECTIONS: tuple[str, ...] = (
    "Motivation",
    "Composition",
    "Generation Process",
    "Preprocessing",
    "Intended Uses",
    "Distribution",
    "Maintenance",
    "Stated Limits",
)

#: VR-052's two, each as `(what it discloses, the phrases that state it)`.
#: Matched case-insensitively over the `Stated Limits` body only, so a phrase
#: appearing elsewhere in the document does not discharge a limit.
STATED_LIMITS_DISCLOSURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "FR-023a - the transmittal codes and field labels are a documented approximation of "
        "federal practice, not a reproduction of a live form (the form revision returned 403 "
        "to automated retrieval, so the codes could not be verified)",
        ("transmittal code", "field label", "documented approximation", "not a reproduction"),
    ),
    (
        "FR-032a - the retained text layer carries no recognition error, so the corpus "
        "evidences no robustness to genuine scan noise",
        ("retained text layer", "no recognition error"),
    ),
)

#: VR-053's answer, which is "none" and must say so. The four verbs are the
#: ones `data-model.md` names; both spellings of the third are admitted because
#: the disclosure is about the answer, not about orthography.
PREPROCESSING_DISCLOSURE: tuple[tuple[str, ...], ...] = (
    ("no source dataset",),
    ("cleaned",),
    ("filtered",),
    ("labelled", "labeled"),
    ("sampled",),
)

#: VR-054's second half. `sha256:` is the first.
_HEX_RUN = re.compile(r"[0-9a-fA-F]{64}")


def _datasheet_text(corpus: Corpus, rule_id: str) -> tuple[str | None, tuple[Failure, ...]]:
    """The datasheet's text, or this rule's own failure explaining why not.

    Resolved through `resolve_within` rather than joined: the datasheet is a
    validator-owned literal path, and VR-065 enumerates it, but it is held to
    the same containment ordering and link prohibition as a corpus document.
    """
    cached = corpus.cached(
        "datasheet",
        lambda: resolve_within(corpus.root, DATASHEET_RELATIVE_PATH).read_text(encoding="utf-8"),
    )
    if isinstance(cached, Exception):
        return None, (
            Failure(
                rule_id,
                f"the synthetic corpus datasheet at {DATASHEET_RELATIVE_PATH} could not be "
                f"read: {cached}",
            ),
        )
    return str(cached), ()


def _heading_pattern(heading: str) -> re.Pattern[str]:
    return re.compile(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _section_body(text: str, heading: str) -> str | None:
    """The text under one level-2 heading, up to the next level-2 heading.

    `None` when the heading is absent — VR-051's finding, not this helper's, so
    the caller reports it under its own rule rather than twice.
    """
    match = _heading_pattern(heading).search(text)
    if match is None:
        return None
    rest = text[match.end() :]
    following = re.search(r"^##[ \t]+\S", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


@rule(
    "VR-051",
    summary="The datasheet carries all eight required disclosures as level-2 headings",
    population="required datasheet disclosures",
)
def _vr_051(corpus: Corpus) -> RuleOutcome:
    text, failures = _datasheet_text(corpus, "VR-051")
    if text is None:
        return RuleOutcome(observed=len(DATASHEET_SECTIONS), failures=failures)
    missing = tuple(
        Failure(
            "VR-051",
            f"the datasheet carries no level-2 heading {heading!r}; presence is a heading "
            "check, not a reading, and all eight of "
            f"{list(DATASHEET_SECTIONS)} are required",
        )
        for heading in DATASHEET_SECTIONS
        if _heading_pattern(heading).search(text) is None
    )
    return RuleOutcome(observed=len(DATASHEET_SECTIONS), failures=missing)


@rule(
    "VR-052",
    summary="Stated Limits carries the approximation disclosure and the text-layer disclosure",
    population="required Stated Limits disclosures",
)
def _vr_052(corpus: Corpus) -> RuleOutcome:
    """Two disclosures, each as stated sub-conditions rather than judgement.

    Whether the approximation is a *good* one is unverifiable here — the live
    form returned 403 — which is exactly why FR-023a demands disclosure rather
    than fidelity, and why this rule checks that the limit is stated rather than
    that it is met.
    """
    text, failures = _datasheet_text(corpus, "VR-052")
    if text is None:
        return RuleOutcome(observed=len(STATED_LIMITS_DISCLOSURES), failures=failures)

    body = _section_body(text, "Stated Limits")
    if body is None:
        return RuleOutcome(
            observed=len(STATED_LIMITS_DISCLOSURES),
            failures=(
                Failure(
                    "VR-052",
                    "the datasheet has no 'Stated Limits' section to carry the two required "
                    "disclosures (VR-051 reports the missing heading)",
                ),
            ),
        )

    folded = body.casefold()
    collected: list[Failure] = []
    for what, phrases in STATED_LIMITS_DISCLOSURES:
        absent = [phrase for phrase in phrases if phrase.casefold() not in folded]
        if absent:
            collected.append(
                Failure(
                    "VR-052",
                    f"'Stated Limits' does not state {what}: the phrase(s) {absent} are absent. "
                    "The disclosure is a stated sub-condition, not a reader's inference",
                )
            )
    return RuleOutcome(observed=len(STATED_LIMITS_DISCLOSURES), failures=tuple(collected))


@rule(
    "VR-053",
    summary="Preprocessing is non-empty and states that no source dataset was preprocessed",
    population="required Preprocessing disclosures",
)
def _vr_053(corpus: Corpus) -> RuleOutcome:
    """Required even though the answer is "none".

    The opposite of E001's roster datasheet, where the category was omitted with
    a reason: a reader cannot tell an unanswered question from one whose answer
    happens to be nothing, so this section is required and must say so.
    """
    text, failures = _datasheet_text(corpus, "VR-053")
    if text is None:
        return RuleOutcome(observed=len(PREPROCESSING_DISCLOSURE), failures=failures)

    body = _section_body(text, "Preprocessing")
    if body is None:
        return RuleOutcome(
            observed=len(PREPROCESSING_DISCLOSURE),
            failures=(
                Failure(
                    "VR-053",
                    "the datasheet has no 'Preprocessing' section; it is required here even "
                    "though the answer is 'none' (VR-051 reports the missing heading)",
                ),
            ),
        )
    if not body.strip():
        return RuleOutcome(
            observed=len(PREPROCESSING_DISCLOSURE),
            failures=(
                Failure("VR-053", "'Preprocessing' is present but empty; it must state 'none'"),
            ),
        )

    folded = body.casefold()
    absent = tuple(
        Failure(
            "VR-053",
            f"'Preprocessing' does not state that no source dataset was {list(spellings)}: "
            "the section must say that nothing was cleaned, filtered, labelled or sampled, "
            "not merely that preprocessing is out of scope",
        )
        for spellings in PREPROCESSING_DISCLOSURE
        if not any(spelling.casefold() in folded for spelling in spellings)
    )
    return RuleOutcome(observed=len(PREPROCESSING_DISCLOSURE), failures=absent)


@rule(
    "VR-054",
    summary="The datasheet carries no literal digest - no sha256: value and no 64-hex run",
    population="datasheets scanned for a literal digest",
)
def _vr_054(corpus: Corpus) -> RuleOutcome:
    """A committed digest goes stale, and a stale one is indistinguishable from
    real drift. E001 VR-016's reasoning, applied to this datasheet: the
    manifests record every digest, and documentation describes them."""
    text, failures = _datasheet_text(corpus, "VR-054")
    if text is None:
        return RuleOutcome(observed=1, failures=failures)

    collected: list[Failure] = []
    if "sha256:" in text.casefold():
        collected.append(
            Failure(
                "VR-054",
                "the datasheet carries a literal 'sha256:' value; a digest copied into prose is "
                "a second source of truth that nothing updates",
            )
        )
    run = _HEX_RUN.search(text)
    if run is not None:
        collected.append(
            Failure(
                "VR-054",
                f"the datasheet carries a 64-character hexadecimal run at offset {run.start()} "
                f"({run.group()[:12]}...); a committed digest goes stale",
            )
        )
    return RuleOutcome(observed=1, failures=tuple(collected))


@rule(
    "VR-055",
    summary="The datasheet resolves under the corpus root and outside every corpus location",
    population="datasheets located",
)
def _vr_055(corpus: Corpus) -> RuleOutcome:
    """Outside every location, so it is not itself a corpus document.

    Resolution is `resolve_within`'s, whose declared base is the corpus root —
    fixed at `data/corpus/` by FR-018 — so a real path under it is under `data/`
    by construction. That is the same reasoning VR-009 uses to keep one declared
    base rather than three competing ones.
    """
    try:
        path = resolve_within(corpus.root, DATASHEET_RELATIVE_PATH)
    except CorpusPathError as exc:
        return RuleOutcome(
            observed=1,
            failures=(
                Failure("VR-055", f"the datasheet does not resolve under the corpus root: {exc}"),
            ),
        )
    if not _is_regular_file(path):
        return RuleOutcome(
            observed=1,
            failures=(
                Failure(
                    "VR-055",
                    f"{DATASHEET_RELATIVE_PATH} is not an existing regular file "
                    "(tested without following links)",
                ),
            ),
        )
    if corpus.is_inside_a_location(path):
        inside = next(
            directory.relative_to(corpus.root).as_posix()
            for directory in corpus.location_dirs()
            if path.is_relative_to(directory)
        )
        return RuleOutcome(
            observed=1,
            failures=(
                Failure(
                    "VR-055",
                    f"the datasheet sits inside corpus location {inside!r}, which would make it "
                    "a corpus document requiring a manifest entry; it belongs outside every "
                    "location",
                    inside,
                    path.name,
                ),
            ),
        )
    return RuleOutcome(observed=1, failures=())


# ---------------------------------------------------------------------------
# Console entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """`corpus-validate`. Exit 0 when every rule passes, 1 otherwise (FR-015)."""
    parser = argparse.ArgumentParser(
        prog="corpus-validate",
        description=(
            "Validate the committed corpus against the numbered rules of "
            "data-model.md. Reads committed artifacts only; never runs the generator."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="corpus root (default: the repository's data/corpus/)",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=list(LAYERS),
        default=None,
        dest="layers",
        help=(
            "restrict the run to one layer's populations; repeatable. "
            "Defaults to every layer, so a narrowed run is always explicit and "
            "out-of-scope rules are reported SKIPPED rather than passed."
        ),
    )
    args = parser.parse_args(argv)

    try:
        report = validate(args.root, layers=args.layers)
    except (CorpusPathError, ValueError) as exc:
        print(f"corpus-validate: {exc}", file=sys.stderr)
        return 2

    print(report.render())
    if report.exit_code:
        print(
            f"corpus-validate: FAILED with {len(report.all_failures)} failure(s)",
            file=sys.stderr,
        )
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
