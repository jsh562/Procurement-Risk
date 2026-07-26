"""FR-015 / SC-025: a negative corpus per rule, each naming the rule it trips.

**What this file is for.** SC-025 requires every numbered validation rule to
have at least one test that exercises it in its *failing* direction and names
the rule in the failure output, because "a rule asserted only over the passing
population is indistinguishable from one that never runs". Forty rules land in
Phase 4, so the cost that matters is the cost of the forty-first case: the
harness below builds one **valid** corpus in `tmp_path`, and a case is a
mutation of it plus one assertion.

**The baseline corpus is derived, not transcribed.** Its REAL entries are built
from the committed `retrieval-policy.json` — sections, agency variants, issuing
bodies, and the FR-011 document identifier all come from the policy through
`AgencyVariant.document_identifier`, the same function the manifest writer uses.
A fixture that restated those values by hand would drift from the policy and
start failing VR-023 or VR-062 for a reason that is not a defect in the
validator.

**The synthetic half is a real generated layer, filtered — not a stub.** Phase 6
added eight rules that re-derive structure from the emitted PDF and three that
read its text layer, and none of them can be exercised against a placeholder
document: a one-string PDF carries no field label, so the deriver would report
`MISSING_OR_BLANK_FIELD` for every entry and the baseline would fail. The
pristine corpus therefore generates the layer once and copies **one document per
project**, chosen by the class set it records so that all five classes appear
across five locations and exactly one raster is committed to the fixture.

**Mutations are made on parsed JSON, never through the typed entry classes.**
`manifest.py`'s `RealEntry` and `SyntheticEntry` refuse most of the defects
below at construction — that is their job — so a harness built on them could
not express a corpus carrying them. The validator's population is committed
JSON written by anything, so the harness writes JSON.

Symbolic-link cases skip where the platform refuses to create one; the Linux
verification runner is the platform of record and grants it.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from model.corpus import validate as validate_module
from model.corpus.manifest import GENERATION_INPUT_PATHS, sha256_of_bytes
from model.corpus.paths import DEFAULT_CORPUS_ROOT, MANIFEST_FILENAME
from model.corpus.sources import load_policy
from model.corpus.validate import (
    STATUS_SKIPPED,
    Failure,
    Report,
    RuleOutcome,
    registered_rules,
    validate,
)

# --------------------------------------------------------------------------
# Fixture constants
# --------------------------------------------------------------------------

#: A digest of the right surface form that is not any real file's.
OTHER_DIGEST = "sha256:" + "0" * 64
#: The seven supporting artifacts FR-018a closes the corpus root over (VR-065),
#: every one of them copied from the committed tree rather than stubbed. Three
#: are read by the validator and three by the generator; the seventh is the
#: datasheet VR-051…VR-055 read. A stub would drift from the artifact the rules
#: actually compare against and would start failing for a reason that is not a
#: defect in the validator.
COMMITTED_SUPPORTING_ARTIFACTS = (
    "manifest.schema.json",
    "real/retrieval-policy.json",
    "real/exclusions.json",
    "synthetic/generation-config.json",
    "synthetic/equipment-category-map.json",
    "synthetic/field-label-vocabulary.json",
    "synthetic/datasheet.md",
)
REAL_DOCUMENT_COUNT = 20
SYNTHETIC_PROJECTS = 5
RETRIEVED_AT = "2026-07-26T12:00:00Z"
REVISION_DATE = "2024-01"

#: One document per synthetic location, selected by the class set it records
#: rather than by index. Between them the five carry all five classes (VR-032)
#: and four of five carry at least one (VR-033's floor exactly, so the rule is
#: at its boundary rather than comfortably inside it). Exactly one carries a
#: raster, which keeps the fixture that every case copies under half a megabyte.
SYNTHETIC_FIXTURE_CLASSES: tuple[tuple[str, ...], ...] = (
    ("MISSING_OR_BLANK_FIELD",),
    ("INCONSISTENT_FIELD_LABEL",),
    ("OUT_OF_ORDER_DATE",),
    ("PAGE_SPLIT_FIELD", "SCAN_DEGRADATION"),
    (),
)

#: Sentinel for `edit`/`edit_entry`: remove the key rather than set it.
DELETE = object()


def _minimal_pdf() -> bytes:
    """A real, openable PDF, because VR-013 opens every corpus file.

    Rendered rather than hand-written: `%PDF-` magic bytes alone satisfy half
    of VR-013 and fail the other half, and a fixture that could not pass the
    rule it is a control for would make every other case ambiguous.
    """
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, invariant=1)
    canvas.setCreator("corpus-validate test fixture")
    canvas.drawString(72, 720, "corpus-validate fixture document")
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _canonical_json(payload: object) -> bytes:
    """MS-3's serialization, written as bytes (HINT-004, MS-4)."""
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


# --------------------------------------------------------------------------
# The builder
# --------------------------------------------------------------------------


def _build_pristine_corpus(root: Path, committed_root: Path) -> Path:
    """One valid corpus: six locations, twenty REAL and five SYNTHETIC documents."""
    pdf = _minimal_pdf()
    digest = sha256_of_bytes(pdf)

    root.mkdir(parents=True)
    (root / "real").mkdir()
    (root / "synthetic").mkdir()

    # All seven supporting artifacts are copied rather than invented: the schema
    # is the contract under test, the policy and ledger are what VR-022, VR-023,
    # VR-025, VR-026 and VR-062 compare against, the three generation inputs are
    # what VR-030 and VR-061 compare against, and the datasheet is VR-051…VR-055's
    # whole population.
    for relative in COMMITTED_SUPPORTING_ARTIFACTS:
        shutil.copyfile(committed_root / relative, root / relative)

    policy = load_policy(path=root / "real" / "retrieval-policy.json")
    # The anchor is included explicitly rather than by slicing `retrieval_targets`:
    # VR-025 requires `01 33 00` present by name, and the anchor sits at the end
    # of that tuple, so any prefix slice would omit exactly the document the rule
    # checks for separately from the distinct-section floor.
    targets = (*policy.targets[: REAL_DOCUMENT_COUNT - 1], policy.anchor)
    assert len(targets) == REAL_DOCUMENT_COUNT, "the committed policy names too few targets"

    real_dir = root / "real" / "ufgs"
    real_dir.mkdir()
    real_entries = []
    for index, target in enumerate(targets):
        name = f"doc-{index:02d}.pdf"
        (real_dir / name).write_bytes(pdf)
        variant = policy.variant(target.agency_variant)
        real_entries.append(
            {
                "location": name,
                "layer": "REAL",
                "license_basis": {
                    "basis_id": "us-gov-17usc105-ufgs",
                    "statute": "17 U.S.C. §105(a)",
                    "document_identifier": variant.document_identifier(
                        target.masterformat_section, REVISION_DATE
                    ),
                    "point_of_use_check": "NO_COPYRIGHTED_EXCERPT_FOUND",
                },
                "content_hash": digest,
                "source_location": target.source_url,
                "retrieval_response_status": 200,
                "retrieved_at": RETRIEVED_AT,
                "issuing_body": variant.issuing_body,
                "masterformat_section": target.masterformat_section,
                "agency_variant": target.agency_variant,
                "revision_date": REVISION_DATE,
                "upstream_digest": digest,
            }
        )
    (real_dir / MANIFEST_FILENAME).write_bytes(
        _canonical_json({"location_id": "real/ufgs", "layer": "REAL", "entries": real_entries})
    )

    _build_synthetic_layer(root)
    return root


def _build_synthetic_layer(root: Path) -> None:
    """Generate the layer once into scratch, then copy one document per project.

    Generated rather than assembled by hand because eleven rules read the
    emitted PDF: VR-035a…VR-035d re-derive its structure, VR-036 looks for a
    raster, VR-037 for the citation anchor, and VR-039 compares its retained
    text layer against the regenerated document model. A hand-built entry beside
    a placeholder PDF cannot satisfy any of them, and a baseline that failed
    eleven rules would make every mutation case below ambiguous.

    Only one document per project is kept. The rules quantify per document, so
    five is as evidential as twenty-five, and every case in this file copies the
    tree it works on.
    """
    from model.corpus.generate import generate_corpus

    scratch = root.parent / "generated"
    result = generate_corpus(scratch)
    layer = scratch / "synthetic"

    by_project: dict[str, list[str]] = {}
    for document in result.documents:
        by_project.setdefault(document.plan.project_id, []).append(document.plan.location)

    for project_id, wanted in zip(sorted(by_project), SYNTHETIC_FIXTURE_CLASSES, strict=True):
        source = layer / project_id
        manifest = json.loads((source / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        chosen = next(
            (
                entry
                for entry in manifest["entries"]
                if tuple(entry["irregularity_classes"]) == wanted
            ),
            None,
        )
        assert chosen is not None, (
            f"{project_id} holds no generated document recording exactly {list(wanted)}; "
            f"the fixture's class selection has drifted from the injector's rotation"
        )
        directory = root / "synthetic" / project_id
        directory.mkdir()
        shutil.copyfile(source / chosen["location"], directory / chosen["location"])
        (directory / MANIFEST_FILENAME).write_bytes(
            _canonical_json({**manifest, "entries": [chosen]})
        )
    shutil.rmtree(scratch)


@dataclass
class CorpusFixture:
    """A writable corpus tree plus the two-line mutations a case is built from."""

    root: Path

    # -- paths -------------------------------------------------------------

    def dir(self, location_id: str) -> Path:
        return self.root.joinpath(*location_id.split("/"))

    def manifest_path(self, location_id: str) -> Path:
        return self.dir(location_id) / MANIFEST_FILENAME

    def document_path(self, location_id: str, index: int = 0) -> Path:
        return self.dir(location_id) / self.entry(location_id, index)["location"]

    # -- manifest access ---------------------------------------------------

    def read(self, location_id: str) -> dict:
        return json.loads(self.manifest_path(location_id).read_text(encoding="utf-8"))

    def write(self, location_id: str, document: object) -> None:
        self.manifest_path(location_id).write_bytes(_canonical_json(document))

    def write_raw(self, location_id: str, raw: bytes) -> None:
        self.manifest_path(location_id).write_bytes(raw)

    def entry(self, location_id: str, index: int = 0) -> dict:
        return self.read(location_id)["entries"][index]

    # `/` makes the addressing arguments positional-only, so a case may mutate a
    # field actually called `location_id` or `index` without colliding with them.
    def edit(self, location_id: str, /, **fields: object) -> None:
        """Set or, with `DELETE`, remove top-level manifest keys."""
        document = self.read(location_id)
        _apply(document, fields)
        self.write(location_id, document)

    def edit_entry(self, location_id: str, index: int = 0, /, **fields: object) -> None:
        """Set or, with `DELETE`, remove fields of one entry."""
        document = self.read(location_id)
        _apply(document["entries"][index], fields)
        self.write(location_id, document)

    def edit_basis(self, location_id: str, index: int = 0, /, **fields: object) -> None:
        document = self.read(location_id)
        _apply(document["entries"][index]["license_basis"], fields)
        self.write(location_id, document)

    def mutate(self, location_id: str, change: Callable[[dict], None]) -> None:
        document = self.read(location_id)
        change(document)
        self.write(location_id, document)

    def add_entry(self, location_id: str, entry: dict) -> None:
        document = self.read(location_id)
        document["entries"].append(entry)
        self.write(location_id, document)


def _apply(target: dict, fields: dict) -> None:
    for key, value in fields.items():
        if value is DELETE:
            target.pop(key, None)
        else:
            target[key] = value


# --------------------------------------------------------------------------
# Fixtures and assertions
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pristine_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Built once. Every case works on a copy, so cases cannot interfere."""
    return _build_pristine_corpus(
        tmp_path_factory.mktemp("pristine") / "corpus", DEFAULT_CORPUS_ROOT
    )


@pytest.fixture
def corpus(tmp_path: Path, pristine_corpus: Path) -> CorpusFixture:
    root = tmp_path / "corpus"
    shutil.copytree(pristine_corpus, root, symlinks=True)
    return CorpusFixture(root=root)


def run(corpus: CorpusFixture, *, layers: Sequence[str] | None = None) -> Report:
    return validate(corpus.root, layers=layers)


def assert_fails_naming(
    corpus: CorpusFixture, rule_id: str, *, layers: Sequence[str] | None = None
) -> Report:
    """SC-025's assertion: the run fails **and** the output names the rule.

    Three halves, not two. A non-zero exit alone does not evidence the rule —
    any other defect would produce one. A rule id in prose without a failing
    exit evidences nothing. And the attribution must be the rule's *own*
    finding: the runner's VR-066 non-vacuity guard attaches a failure carrying
    the rule's identifier whenever its population is empty, so a mutation that
    merely emptied a population would otherwise look exactly like one the rule
    caught.
    """
    report = run(corpus, layers=layers)
    rendered = report.render()
    assert report.exit_code != 0, f"expected a failing run naming {rule_id}\n{rendered}"
    substantive = [
        failure
        for failure in report.all_failures
        if not failure.message.startswith("VR-066: population is empty")
    ]
    named = sorted({failure.rule_id for failure in substantive})
    assert rule_id in named, f"expected {rule_id} among {named}\n{rendered}"
    assert rule_id in rendered, f"{rule_id} is not named in the rendered report\n{rendered}"
    return report


def assert_passes(corpus: CorpusFixture, *, layers: Sequence[str] | None = None) -> Report:
    report = run(corpus, layers=layers)
    assert report.exit_code == 0, report.render()
    return report


def link(source: Path, target: Path) -> Path:
    """Create a symbolic link or skip: the control cannot be shown otherwise."""
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"this platform does not permit creating symbolic links: {exc}")
    return source


# --------------------------------------------------------------------------
# The harness's own controls
# --------------------------------------------------------------------------


def test_the_baseline_corpus_passes_every_rule(corpus: CorpusFixture) -> None:
    """Without this, a mutation that trips nothing is indistinguishable from
    one that trips everything, and no case below carries information."""
    report = assert_passes(corpus)
    assert report.read_failures == ()


def test_every_registered_rule_reports_its_population_size(corpus: CorpusFixture) -> None:
    """VR-066's reporting half, over the passing run rather than a failing one."""
    report = assert_passes(corpus)
    for result in report.results:
        assert result.status != STATUS_SKIPPED or result.skipped_reason
        if result.rule.empty_population_fails and result.status != STATUS_SKIPPED:
            assert result.observed > 0, f"{result.rule_id} observed nothing\n{report.render()}"


def test_the_committed_corpus_is_reachable_and_reports_populations() -> None:
    """The committed corpus, **unscoped**, over both layers.

    Scoped to `REAL` until T056, because the synthetic layer did not exist and
    VR-005 and VR-066 are built to fail on a corpus missing it. The layer landed
    with T056's single generation run and the scoping came off here and in
    `verify.yml` in the same change: a narrowed run would leave every synthetic
    rule reporting SKIPPED and evidence nothing about the layer this epic
    exists to ship.
    """
    report = validate()
    assert report.exit_code == 0, report.render()
    assert not any(result.status == STATUS_SKIPPED for result in report.results), report.render()
    for result in report.results:
        assert result.observed > 0 or not result.rule.empty_population_fails, result.rule_id


# --------------------------------------------------------------------------
# T025 - VR-056, VR-057, VR-066: the runner's own three rules
# --------------------------------------------------------------------------


def _temporarily_register(rule_id: str, check, **kwargs):
    """Install a rule for one test. Fault injection is the only way to make the
    runner's own invariants fail, since nothing a corpus contains can break
    them."""

    class _Scope:
        def __enter__(self):
            validate_module.rule(rule_id, summary="fault injection", **kwargs)(check)
            return self

        def __exit__(self, *exc):
            validate_module._RULES.pop(rule_id, None)
            return False

    return _Scope()


def test_vr_056_a_failure_naming_no_rule_is_itself_reported(corpus: CorpusFixture) -> None:
    def _unnamed(_: validate_module.Corpus) -> RuleOutcome:
        return RuleOutcome(observed=1, failures=(Failure("", "a failure that names no rule"),))

    with _temporarily_register("VR-999-unnamed", _unnamed, population="injected failures"):
        assert_fails_naming(corpus, "VR-056")


def test_vr_056_a_failure_naming_a_document_but_no_location_is_reported(
    corpus: CorpusFixture,
) -> None:
    def _unattributed(_: validate_module.Corpus) -> RuleOutcome:
        return RuleOutcome(
            observed=1,
            failures=(Failure("VR-999", "names a document but no location", None, "doc-00.pdf"),),
        )

    with _temporarily_register("VR-999-unattributed", _unattributed, population="injected"):
        assert_fails_naming(corpus, "VR-056")


def test_vr_056_collects_every_failure_and_names_each_document(corpus: CorpusFixture) -> None:
    """`all` failures, not the first: two defects in two locations, both reported."""
    corpus.edit_entry("real/ufgs", 0, content_hash=OTHER_DIGEST)
    corpus.edit_entry("real/ufgs", 1, content_hash=OTHER_DIGEST)
    report = assert_fails_naming(corpus, "VR-012")
    offenders = {f.location for f in report.all_failures if f.rule_id == "VR-012"}
    assert offenders == {"doc-00.pdf", "doc-01.pdf"}, report.render()
    for failure in report.all_failures:
        assert failure.rule_id, report.render()
        if failure.attributable:
            assert failure.location_id, report.render()
    assert "VR-056" in report.render()


def test_vr_057_an_unreadable_manifest_short_circuits_its_location(
    corpus: CorpusFixture,
) -> None:
    corpus.write_raw("real/ufgs", b"{ this is not JSON\n")
    report = assert_fails_naming(corpus, "VR-057")
    assert any(failure.rule_id == "VR-001" for failure in report.read_failures), report.render()
    # Short-circuited: the entries of that location are evaluated by nothing,
    # so no rule reports a second failure with the same cause.
    assert not [
        failure
        for failure in report.rule_failures
        if failure.location_id == "real/ufgs" and failure.rule_id not in {"VR-057", "VR-066"}
    ], report.render()


def test_vr_057_read_failures_are_reported_apart_from_validation_failures(
    corpus: CorpusFixture,
) -> None:
    corpus.write_raw("synthetic/PRJ-001", b"\xef\xbb\xbf{}\n")
    report = assert_fails_naming(corpus, "VR-057")
    assert report.read_failures, report.render()
    assert all(failure not in report.rule_failures for failure in report.read_failures), (
        report.render()
    )


def test_vr_066_an_empty_population_fails_rather_than_passing_silently(
    tmp_path: Path,
) -> None:
    """The entry criterion: an empty or partially fetched checkout fails here."""
    empty = tmp_path / "corpus"
    empty.mkdir()
    report = validate(empty)
    assert report.exit_code != 0
    assert "VR-066" in {failure.rule_id for failure in report.all_failures}, report.render()


def test_vr_066_reports_the_observed_count_for_the_rule_it_names(tmp_path: Path) -> None:
    empty = tmp_path / "corpus"
    empty.mkdir()
    report = validate(empty)
    messages = [f.message for f in report.all_failures if f.rule_id == "VR-066"]
    assert messages, report.render()
    assert any("observed 0" in message for message in messages), messages


def test_the_registry_holds_every_rule_this_phase_discharges() -> None:
    """Forty rules land in Phase 4; the registry is what the report enumerates."""
    registered = {each.rule_id for each in registered_rules()}
    expected = {
        "VR-001",
        "VR-002",
        "VR-003",
        "VR-004",
        "VR-005",
        "VR-006",
        "VR-007",
        "VR-008",
        "VR-009",
        "VR-010",
        "VR-011",
        "VR-012",
        "VR-013",
        "VR-014",
        "VR-015",
        "VR-016",
        "VR-017",
        "VR-018",
        "VR-019",
        "VR-020",
        "VR-021",
        "VR-022",
        "VR-023",
        "VR-024",
        "VR-025",
        "VR-026",
        "VR-027",
        "VR-028",
        "VR-056",
        "VR-057",
        "VR-058",
        "VR-059",
        "VR-060",
        "VR-062",
        "VR-063",
        "VR-064",
        "VR-065",
        "VR-066",
        "VR-067",
        "VR-068",
    }
    assert len(expected) == 40
    assert expected <= registered, sorted(expected - registered)


def test_the_registry_holds_the_eleven_rules_phase_six_adds_to_this_runner() -> None:
    """T053's eight and T054's three.

    VR-035 itself is not registered and is not missing: it *is* VR-035a…VR-035d,
    which decide one class each in both directions and together are the set
    equality it states. Registering a fifth rule that recomputed the same
    comparison would report one defect twice under two identifiers.

    VR-049 and VR-050 belong to the **other** runner — the layout-variety
    assertion and the injector unit tests both need a generator run — and are
    deliberately absent here.
    """
    registered = {each.rule_id for each in registered_rules()}
    expected = {
        "VR-031",
        "VR-032",
        "VR-033",
        "VR-035a",
        "VR-035b",
        "VR-035c",
        "VR-035d",
        "VR-036",
        "VR-037",
        "VR-038",
        "VR-039",
    }
    assert len(expected) == 11
    assert expected <= registered, sorted(expected - registered)
    assert not {"VR-035", "VR-049", "VR-050"} & registered


def test_the_registry_holds_the_eight_rules_phase_five_adds_to_this_runner() -> None:
    """T042's three and T043's five.

    Phase 5's other eleven rules belong to the **other** runner — the modeling
    boundary's test suite — because they require re-running the generator, which
    a validator must not do. They are asserted in `test_corpus_generate.py` and
    `test_corpus_offline.py` and are deliberately absent from this registry.
    """
    registered = {each.rule_id for each in registered_rules()}
    expected = {"VR-029", "VR-030", "VR-061", "VR-051", "VR-052", "VR-053", "VR-054", "VR-055"}
    assert expected <= registered, sorted(expected - registered)
    assert not {"VR-040a", "VR-040b", "VR-041", "VR-042", "VR-043"} & registered


def test_an_unknown_layer_is_refused(corpus: CorpusFixture) -> None:
    with pytest.raises(ValueError, match="unknown layer"):
        validate(corpus.root, layers=["NEITHER"])


def test_the_console_entry_point_exits_non_zero_on_failure(
    corpus: CorpusFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-015: repeatable on demand, non-zero on failure, naming the rule."""
    corpus.edit_entry("real/ufgs", 0, content_hash=OTHER_DIGEST)
    assert validate_module.main(["--root", str(corpus.root)]) == 1
    assert "VR-012" in capsys.readouterr().out


def test_the_console_entry_point_exits_zero_on_a_clean_corpus(
    corpus: CorpusFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    assert validate_module.main(["--root", str(corpus.root)]) == 0
    assert "VR-056" in capsys.readouterr().out


def test_a_missing_corpus_root_is_refused_before_any_rule_runs(tmp_path: Path) -> None:
    assert validate_module.main(["--root", str(tmp_path / "absent")]) == 2


# --------------------------------------------------------------------------
# T027 - schema conformance and field sets
# VR-001, 002, 003, 014, 015, 016, 017, 027, 058, 063
# --------------------------------------------------------------------------


def test_vr_001_a_manifest_that_does_not_parse(corpus: CorpusFixture) -> None:
    corpus.write_raw("real/ufgs", b'{"location_id": "real/ufgs",\n')
    assert_fails_naming(corpus, "VR-001")


def test_vr_001_a_duplicate_key_is_not_a_last_wins_merge(corpus: CorpusFixture) -> None:
    """A last-wins merge would discard content the schema then never sees."""
    body = corpus.manifest_path("real/ufgs").read_text(encoding="utf-8")
    doubled = body.rstrip()[:-1].rstrip().rstrip(",") + ',\n  "layer": "SYNTHETIC"\n}\n'
    corpus.write_raw("real/ufgs", doubled.encode("utf-8"))
    report = assert_fails_naming(corpus, "VR-001")
    assert any("duplicate key" in f.message for f in report.read_failures), report.render()


def test_vr_001_a_utf8_bom_is_refused(corpus: CorpusFixture) -> None:
    raw = corpus.manifest_path("real/ufgs").read_bytes()
    corpus.write_raw("real/ufgs", b"\xef\xbb\xbf" + raw)
    assert_fails_naming(corpus, "VR-001")


def test_vr_002_a_manifest_violating_the_schema(corpus: CorpusFixture) -> None:
    corpus.edit("real/ufgs", entries=[])
    assert_fails_naming(corpus, "VR-002")


def test_vr_002_collects_every_schema_error_not_only_the_first(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, masterformat_section="260513", revision_date="2024")
    report = assert_fails_naming(corpus, "VR-002")
    assert len([f for f in report.read_failures if f.rule_id == "VR-002"]) >= 2, report.render()


def test_vr_003_a_schema_that_is_not_a_valid_draft_2020_12_schema(corpus: CorpusFixture) -> None:
    (corpus.root / "manifest.schema.json").write_bytes(
        _canonical_json({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": 42})
    )
    assert_fails_naming(corpus, "VR-003")


def test_vr_014_a_third_layer_value(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, layer="ARCHIVAL")
    assert_fails_naming(corpus, "VR-014")


def test_vr_015_a_whitespace_only_value_is_not_a_value(corpus: CorpusFixture) -> None:
    """`minLength: 1` admits `"  "` exactly; the strip-check is why VR-015 exists."""
    corpus.edit_entry("real/ufgs", 0, issuing_body="   ")
    assert_fails_naming(corpus, "VR-015")


def test_vr_016_uppercase_hexadecimal_is_not_the_recorded_form(corpus: CorpusFixture) -> None:
    digest = corpus.entry("real/ufgs", 0)["content_hash"]
    corpus.edit_entry("real/ufgs", 0, content_hash=digest.upper().replace("SHA256", "sha256"))
    assert_fails_naming(corpus, "VR-016")


def test_vr_017_a_real_entry_carrying_a_generation_field(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, seed=1)
    assert_fails_naming(corpus, "VR-017")


def test_vr_017_a_real_entry_missing_a_retrieval_field(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, issuing_body=DELETE)
    assert_fails_naming(corpus, "VR-017")


def test_vr_027_a_synthetic_entry_carrying_a_retrieval_field(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, issuing_body="USACE")
    assert_fails_naming(corpus, "VR-027")


def test_vr_027_a_synthetic_entry_missing_a_generation_field(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, roster_hash=DELETE)
    assert_fails_naming(corpus, "VR-027")


@pytest.mark.parametrize("marker", ["version", "revision", "generated_at", "updated"])
def test_vr_058_a_hand_maintained_drift_marker(corpus: CorpusFixture, marker: str) -> None:
    corpus.edit("real/ufgs", **{marker: "2026-07-26"})
    assert_fails_naming(corpus, "VR-058")


def test_vr_063_entries_out_of_codepoint_order(corpus: CorpusFixture) -> None:
    corpus.mutate("real/ufgs", lambda document: document["entries"].reverse())
    assert_fails_naming(corpus, "VR-063")


# --------------------------------------------------------------------------
# T028 - location topology and file<->entry reconciliation
# VR-004, 005, 006, 007, 010, 011, 013, 059, 060, 064, 065
# --------------------------------------------------------------------------


def test_vr_004_a_manifest_declaring_another_location(corpus: CorpusFixture) -> None:
    corpus.edit("real/ufgs", location_id="real/elsewhere")
    assert_fails_naming(corpus, "VR-004")


def test_vr_005_a_sixth_synthetic_location(corpus: CorpusFixture) -> None:
    """Neither a missing nor a sixth location passes."""
    source = corpus.dir("synthetic/PRJ-001")
    shutil.copytree(source, corpus.root / "synthetic" / "PRJ-006")
    document = corpus.read("synthetic/PRJ-001")
    document["location_id"] = "synthetic/PRJ-006"
    document["project_id"] = "PRJ-006"
    (corpus.root / "synthetic" / "PRJ-006" / MANIFEST_FILENAME).write_bytes(
        _canonical_json(document)
    )
    assert_fails_naming(corpus, "VR-005")


def test_vr_005_a_deleted_synthetic_location(corpus: CorpusFixture) -> None:
    """No aggregate index exists, so a deleted location is simply not discovered;
    this population rule is what makes its absence visible."""
    shutil.rmtree(corpus.dir("synthetic/PRJ-005"))
    assert_fails_naming(corpus, "VR-005")


def test_vr_006_a_project_id_that_is_not_the_final_path_segment(corpus: CorpusFixture) -> None:
    corpus.edit("synthetic/PRJ-001", project_id="PRJ-002")
    assert_fails_naming(corpus, "VR-006")


def test_vr_006_a_project_id_that_is_not_a_roster_project(corpus: CorpusFixture) -> None:
    """The half no schema can express: membership in `read_roster().projects`.

    The "present iff SYNTHETIC" half is expressed by the schema's layer
    conditional and therefore fails as a VR-002 read failure, which
    short-circuits the location before this rule sees it. Both halves are
    enforced; only this one has a failing direction VR-006 alone reports.
    """
    directory = corpus.root / "synthetic" / "PRJ-404"
    shutil.copytree(corpus.dir("synthetic/PRJ-001"), directory)
    document = corpus.read("synthetic/PRJ-001")
    document["location_id"] = "synthetic/PRJ-404"
    document["project_id"] = "PRJ-404"
    (directory / MANIFEST_FILENAME).write_bytes(_canonical_json(document))
    assert_fails_naming(corpus, "VR-006")


def test_vr_007_an_entry_whose_layer_differs_from_its_manifest(corpus: CorpusFixture) -> None:
    """A location holds exactly one layer, so this is the mixed-layer case."""
    synthetic = corpus.entry("synthetic/PRJ-001", 0)
    synthetic["location"] = "doc-99.pdf"
    shutil.copyfile(
        corpus.document_path("synthetic/PRJ-001", 0), corpus.dir("real/ufgs") / "doc-99.pdf"
    )
    corpus.add_entry("real/ufgs", synthetic)
    assert_fails_naming(corpus, "VR-007")


def test_vr_010_an_entry_naming_no_existing_file(corpus: CorpusFixture) -> None:
    corpus.document_path("real/ufgs", 0).unlink()
    assert_fails_naming(corpus, "VR-010")


def test_vr_011_a_file_with_no_entry(corpus: CorpusFixture) -> None:
    shutil.copyfile(
        corpus.document_path("real/ufgs", 0), corpus.dir("real/ufgs") / "unmanifested.pdf"
    )
    assert_fails_naming(corpus, "VR-011")


def test_vr_011_an_entry_with_no_file(corpus: CorpusFixture) -> None:
    """The other direction of the same asymmetry; both fail."""
    corpus.document_path("real/ufgs", 0).unlink()
    assert_fails_naming(corpus, "VR-011")


def test_vr_013_a_file_that_is_not_a_pdf(corpus: CorpusFixture) -> None:
    """A `.pdf` extension is not evidence of format."""
    corpus.document_path("real/ufgs", 0).write_bytes(b"not a PDF at all\n")
    assert_fails_naming(corpus, "VR-013")


def test_vr_013_a_pdf_that_does_not_open(corpus: CorpusFixture) -> None:
    """FR-001a: an unopenable document is an enumerated failure, never a skip."""
    corpus.document_path("real/ufgs", 0).write_bytes(b"%PDF-1.7\ntruncated before the xref\n")
    assert_fails_naming(corpus, "VR-013")


def test_vr_059_an_untracked_corpus_file(tmp_path: Path, pristine_corpus: Path) -> None:
    """Tracked, not merely present: an untracked output satisfies every other
    rule locally while shipping nothing to a clone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if _git(repo, "init", "-q") is None:
        pytest.skip("git is not available on this machine")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    root = repo / "data" / "corpus"
    root.parent.mkdir(parents=True)
    shutil.copytree(pristine_corpus, root, symlinks=True)
    fixture = CorpusFixture(root=root)
    _git(repo, "add", "-A")
    # Committed so `ls-files` reports the tree, then one file added afterwards
    # and deliberately left untracked.
    _git(repo, "commit", "-qm", "fixture corpus")
    shutil.copyfile(
        fixture.document_path("real/ufgs", 0), fixture.dir("real/ufgs") / "untracked.pdf"
    )
    fixture.add_entry(
        "real/ufgs",
        {**fixture.entry("real/ufgs", 0), "location": "untracked.pdf"},
    )
    assert_fails_naming(fixture, "VR-059")


def test_vr_059_is_skipped_rather_than_passed_outside_the_tracked_tree(
    corpus: CorpusFixture,
) -> None:
    """A rule that could not run must not report as one that ran and passed."""
    report = assert_passes(corpus)
    result = next(r for r in report.results if r.rule_id == "VR-059")
    assert result.status == STATUS_SKIPPED, report.render()
    assert result.skipped_reason


def test_vr_060_a_pdf_outside_every_location(corpus: CorpusFixture) -> None:
    shutil.copyfile(corpus.document_path("real/ufgs", 0), corpus.root / "stray.pdf")
    assert_fails_naming(corpus, "VR-060")


def test_vr_064_a_subdirectory_inside_a_location(corpus: CorpusFixture) -> None:
    (corpus.dir("real/ufgs") / "nested").mkdir()
    assert_fails_naming(corpus, "VR-064")


def test_vr_065_a_stray_non_pdf_outside_every_location(corpus: CorpusFixture) -> None:
    """VR-060 closes this hole for PDFs; VR-065 closes it for every other format."""
    (corpus.root / "notes.txt").write_bytes(b"a stray file\n")
    assert_fails_naming(corpus, "VR-065")


# --------------------------------------------------------------------------
# T029 - path containment, the link prohibition, and case collision
# VR-009, 067, 068
# --------------------------------------------------------------------------


def test_vr_009_a_traversal_sequence_in_a_location(corpus: CorpusFixture) -> None:
    """A prefix test on the raw path is defeated by segments the filesystem
    evaluates afterwards, which is why resolution comes first."""
    corpus.edit_entry("real/ufgs", 0, location="../../manifest.schema.json")
    assert_fails_naming(corpus, "VR-009")


def test_vr_009_an_absolute_location(corpus: CorpusFixture) -> None:
    """Joining an absolute path onto a base silently discards the base."""
    corpus.edit_entry("real/ufgs", 0, location=str(corpus.root / "manifest.schema.json"))
    assert_fails_naming(corpus, "VR-009")


def test_vr_009_a_location_resolving_to_its_own_directory(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, location=".")
    assert_fails_naming(corpus, "VR-009")


def test_vr_067_a_symlink_to_a_regular_file_outside_the_corpus(
    corpus: CorpusFixture, tmp_path: Path
) -> None:
    """The exact shape a link-following `is_file()` test admits."""
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(corpus.document_path("real/ufgs", 0).read_bytes())
    link(corpus.dir("real/ufgs") / "linked.pdf", outside)
    assert_fails_naming(corpus, "VR-067")


def test_vr_067_a_symlinked_directory_under_the_corpus_root(
    corpus: CorpusFixture, tmp_path: Path
) -> None:
    """The walk does not descend into it, so it cannot introduce a location."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link(corpus.root / "linked-layer", elsewhere)
    assert_fails_naming(corpus, "VR-067")


def test_vr_068_two_locations_colliding_under_case_folding(corpus: CorpusFixture) -> None:
    """Codepoint-distinct, fold-equal: one file on a case-folding filesystem."""
    entry = dict(corpus.entry("real/ufgs", 0))
    entry["location"] = entry["location"].upper()
    corpus.add_entry("real/ufgs", entry)
    assert_fails_naming(corpus, "VR-068")


def test_vr_068_a_location_matching_a_file_only_under_case_folding(
    corpus: CorpusFixture,
) -> None:
    document = corpus.document_path("real/ufgs", 0)
    document.rename(document.with_name(document.name.upper()))
    assert_fails_naming(corpus, "VR-068")


def test_vr_068_a_location_that_is_not_nfc_normalized(corpus: CorpusFixture) -> None:
    """NFD reaches VR-068 through the parsed payload; the schema's filename
    pattern rejects the combining mark too, and both are reported."""
    corpus.edit_entry("real/ufgs", 0, location="doc-é.pdf")
    assert_fails_naming(corpus, "VR-068")


# --------------------------------------------------------------------------
# T030 - digest recomputation
# VR-012, 018
# --------------------------------------------------------------------------


def test_vr_012_a_file_edited_after_it_was_manifested(corpus: CorpusFixture) -> None:
    """The recorded value is never trusted as evidence of itself."""
    path = corpus.document_path("real/ufgs", 0)
    path.write_bytes(path.read_bytes() + b"% appended\n")
    assert_fails_naming(corpus, "VR-012")


def test_vr_012_a_recorded_hash_that_is_not_the_file_s(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, content_hash=OTHER_DIGEST)
    assert_fails_naming(corpus, "VR-012")


def test_vr_018_an_upstream_digest_diverging_from_the_content_hash(
    corpus: CorpusFixture,
) -> None:
    """Internal consistency, not a provenance proof: back-filled from the
    committed file the comparison is a tautology, and no offline check can tell
    that apart. The residual is published, not covered."""
    corpus.edit_entry("real/ufgs", 0, upstream_digest=OTHER_DIGEST)
    assert_fails_naming(corpus, "VR-018")


# --------------------------------------------------------------------------
# T031 - license basis
# VR-008, 023, 024, 028
# --------------------------------------------------------------------------


def test_vr_008_a_location_mixing_two_license_bases(corpus: CorpusFixture) -> None:
    """Compared over basis_id only: the per-document components of a REAL basis
    differ by construction, so comparing whole bases would fail every location.

    Built by placing a well-formed SYNTHETIC entry beside the REAL ones — each
    validates against its own branch of the layer conditional, so the schema is
    satisfied and the mixed basis is a defect only this rule reports.
    """
    synthetic = dict(corpus.entry("synthetic/PRJ-001", 0))
    synthetic["location"] = "zz-mixed.pdf"
    shutil.copyfile(
        corpus.document_path("synthetic/PRJ-001", 0), corpus.dir("real/ufgs") / "zz-mixed.pdf"
    )
    corpus.add_entry("real/ufgs", synthetic)
    assert_fails_naming(corpus, "VR-008")


def test_vr_023_a_document_identifier_that_drifted_from_its_own_fields(
    corpus: CorpusFixture,
) -> None:
    corpus.edit_basis("real/ufgs", 0, document_identifier="UFGS 99 99 99 (1999-01)")
    assert_fails_naming(corpus, "VR-023")


def test_vr_023_a_statute_that_does_not_agree_with_the_basis_id(
    corpus: CorpusFixture,
) -> None:
    """Non-emptiness alone would admit a citation conferring no public-domain
    status while satisfying every stated rule."""
    corpus.edit_basis("real/ufgs", 0, statute="17 U.S.C. §106")
    assert_fails_naming(corpus, "VR-023")


def test_vr_024_a_point_of_use_outcome_other_than_the_one_admissible_value(
    corpus: CorpusFixture,
) -> None:
    corpus.edit_basis("real/ufgs", 0, point_of_use_check="COPYRIGHTED_EXCERPT_FOUND")
    assert_fails_naming(corpus, "VR-024")


def test_vr_028_a_synthetic_basis_asserting_third_party_rights(
    corpus: CorpusFixture,
) -> None:
    corpus.edit_basis("synthetic/PRJ-001", 0, third_party_rights="SOME")
    assert_fails_naming(corpus, "VR-028")


def test_vr_028_a_synthetic_basis_that_was_not_generated_by_this_project(
    corpus: CorpusFixture,
) -> None:
    corpus.edit_basis("synthetic/PRJ-001", 0, generated_by_this_project=False)
    assert_fails_naming(corpus, "VR-028")


# --------------------------------------------------------------------------
# T032 - REAL field values
# VR-019, 020, 021, 022
# --------------------------------------------------------------------------


def test_vr_019_a_non_200_retrieval_status(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, retrieval_response_status=403)
    assert_fails_naming(corpus, "VR-019")


def test_vr_020_a_retrieval_date_in_the_future(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, retrieved_at="2999-01-01T00:00:00Z")
    assert_fails_naming(corpus, "VR-020")


def test_vr_020_a_retrieval_date_carrying_a_numeric_offset(corpus: CorpusFixture) -> None:
    """Rejected rather than normalized, so two entries cannot record one instant
    in two forms."""
    corpus.edit_entry("real/ufgs", 0, retrieved_at="2026-07-26T12:00:00+00:00")
    assert_fails_naming(corpus, "VR-020")


def test_vr_021_two_entries_sharing_one_real_identity(corpus: CorpusFixture) -> None:
    """Two agency variants of one number are two documents; one identity is one."""
    first = corpus.entry("real/ufgs", 0)
    second = dict(corpus.entry("real/ufgs", 1))
    second["masterformat_section"] = first["masterformat_section"]
    second["agency_variant"] = first["agency_variant"]
    second["revision_date"] = first["revision_date"]
    second["issuing_body"] = first["issuing_body"]
    second["license_basis"] = dict(first["license_basis"])
    corpus.mutate("real/ufgs", lambda document: document["entries"].__setitem__(1, second))
    assert_fails_naming(corpus, "VR-021")


def test_vr_021_an_agency_variant_outside_the_closed_set(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, agency_variant="AFCEC")
    assert_fails_naming(corpus, "VR-021")


def test_vr_022_a_host_that_merely_ends_in_an_allow_listed_name(
    corpus: CorpusFixture,
) -> None:
    """Membership is exact host equality, never a suffix match."""
    corpus.edit_entry(
        "real/ufgs", 0, source_location="https://www.wbdg.org.example.invalid/UFGS.pdf"
    )
    assert_fails_naming(corpus, "VR-022")


def test_vr_022_a_source_location_outside_the_allow_list(corpus: CorpusFixture) -> None:
    corpus.edit_entry("real/ufgs", 0, source_location="https://example.invalid/UFGS.pdf")
    assert_fails_naming(corpus, "VR-022")


# --------------------------------------------------------------------------
# T033 - policy agreement and the exclusion ledger
# VR-025, 026, 062
# --------------------------------------------------------------------------


def test_vr_025_a_real_layer_below_the_document_floor(corpus: CorpusFixture) -> None:
    def _drop(document: dict) -> None:
        for entry in document["entries"][5:]:
            (corpus.dir("real/ufgs") / entry["location"]).unlink()
        document["entries"] = document["entries"][:5]

    corpus.mutate("real/ufgs", _drop)
    assert_fails_naming(corpus, "VR-025")


def test_vr_025_a_real_layer_without_the_anchor_section(corpus: CorpusFixture) -> None:
    anchor = load_policy(path=corpus.root / "real" / "retrieval-policy.json").anchor_section

    def _drop_anchor(document: dict) -> None:
        keep = [e for e in document["entries"] if e["masterformat_section"] != anchor]
        for entry in document["entries"]:
            if entry["masterformat_section"] == anchor:
                (corpus.dir("real/ufgs") / entry["location"]).unlink()
        document["entries"] = keep

    corpus.mutate("real/ufgs", _drop_anchor)
    assert_fails_naming(corpus, "VR-025")


def test_vr_026_an_excluded_candidate_that_also_appears_in_the_manifest(
    corpus: CorpusFixture,
) -> None:
    """A document cannot be both excluded and vendored."""
    ledger = json.loads((corpus.root / "real" / "exclusions.json").read_text(encoding="utf-8"))
    ledger["exclusions"][0]["candidate_identifier"] = corpus.entry("real/ufgs", 0)["license_basis"][
        "document_identifier"
    ]
    (corpus.root / "real" / "exclusions.json").write_bytes(_canonical_json(ledger))
    assert_fails_naming(corpus, "VR-026")


def test_vr_026_a_ledger_record_missing_its_cause(corpus: CorpusFixture) -> None:
    ledger = json.loads((corpus.root / "real" / "exclusions.json").read_text(encoding="utf-8"))
    ledger["exclusions"][0]["cause"] = "BECAUSE"
    (corpus.root / "real" / "exclusions.json").write_bytes(_canonical_json(ledger))
    assert_fails_naming(corpus, "VR-026")


def test_an_unreadable_retrieval_policy_is_reported_by_every_rule_that_needs_it(
    corpus: CorpusFixture,
) -> None:
    """One unreadable supporting artifact must not abort the run.

    The policy is read by four rules. If a parse failure escaped, the run would
    stop at the first of them and every rule after it would report nothing,
    which is the "only the first failure" behaviour VR-056 forbids.
    """
    (corpus.root / "real" / "retrieval-policy.json").write_bytes(b"{ not json\n")
    report = run(corpus)
    assert report.exit_code != 0
    named = {failure.rule_id for failure in report.all_failures}
    assert {"VR-022", "VR-023", "VR-025"} <= named, report.render()
    assert not [
        result
        for result in report.results
        if any("could not be evaluated" in f.message for f in result.failures)
    ], report.render()


def test_vr_011_two_entries_naming_one_file(corpus: CorpusFixture) -> None:
    """`location` is the entry's key within a manifest."""
    duplicate = dict(corpus.entry("real/ufgs", 0))
    duplicate["masterformat_section"] = "99 99 99"
    corpus.add_entry("real/ufgs", duplicate)
    corpus.mutate(
        "real/ufgs", lambda document: document["entries"].sort(key=lambda e: e["location"])
    )
    assert_fails_naming(corpus, "VR-011")


def test_vr_062_an_issuing_body_naming_another_agency(corpus: CorpusFixture) -> None:
    """An entry naming one agency's variant and another's issuing body describes
    two different documents, and both fields are otherwise well-formed."""
    corpus.edit_entry("real/ufgs", 0, issuing_body="Some Other Agency")
    assert_fails_naming(corpus, "VR-062")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - platform without git
        return None


# --------------------------------------------------------------------------
# T042 - VR-029, VR-030, VR-061: roster and generation-input drift
#
# The two digests are computed differently on purpose, and the pair of cases
# below is what keeps that distinction evidenced rather than described:
# `roster_hash` is the reader's canonical-content value, so it is compared
# against a live `read_roster()`; `generation_inputs.*` are raw-byte digests, so
# they are compared against the files' bytes.
# --------------------------------------------------------------------------

SYNTHETIC_LOCATIONS = ("synthetic/PRJ-001", "synthetic/PRJ-002")


def test_vr_029_a_stale_roster_hash(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, roster_hash=OTHER_DIGEST)
    assert_fails_naming(corpus, "VR-029")


def test_vr_029_names_every_stale_document_not_the_first(corpus: CorpusFixture) -> None:
    """ "On mismatch the run names every stale document and fails; it does not
    stop at the first" — the half a single-document case cannot evidence."""
    for location_id in SYNTHETIC_LOCATIONS:
        corpus.edit_entry(location_id, 0, roster_hash=OTHER_DIGEST)
    report = assert_fails_naming(corpus, "VR-029")
    named = {f.location_id for f in report.all_failures if f.rule_id == "VR-029"}
    assert named == set(SYNTHETIC_LOCATIONS), report.render()


def test_vr_029_a_roster_hash_of_the_right_shape_but_the_wrong_kind(
    corpus: CorpusFixture,
) -> None:
    """The conflation the field split exists to catch.

    A raw-byte digest of the roster *file* has the right surface form and is
    wrong: `roster_hash` is over the reader's canonical re-serialization, so a
    generator that reached for `sha256sum` would record a plausible value that
    no reader call ever produces.
    """
    from model.roster.reader import DEFAULT_ROSTER_PATH

    corpus.edit_entry(
        "synthetic/PRJ-001", 0, roster_hash=sha256_of_bytes(DEFAULT_ROSTER_PATH.read_bytes())
    )
    assert_fails_naming(corpus, "VR-029")


def test_vr_030_a_seed_that_disagrees_with_the_committed_configuration(
    corpus: CorpusFixture,
) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, seed=999)
    assert_fails_naming(corpus, "VR-030")


def test_vr_030_a_wall_clock_generation_date(corpus: CorpusFixture) -> None:
    """FR-009a's regression, in the shape it actually appears in.

    The planted value is the day **after** the committed constant rather than
    `date.today()`: today is the constant's own date until the calendar moves
    past it, so a case written against the clock would be vacuous on the day it
    was written and would start failing later for a reason that is not a defect.
    "A wall-clock read fails here on the first run after the constant's date" is
    the condition, so that is the date this case plants.
    """
    from datetime import date, timedelta

    committed = json.loads(
        (corpus.root / "synthetic" / "generation-config.json").read_text(encoding="utf-8")
    )["generation_date"]
    tomorrow = (date.fromisoformat(committed) + timedelta(days=1)).isoformat()
    corpus.edit_entry("synthetic/PRJ-001", 0, generation_date=tomorrow)
    assert_fails_naming(corpus, "VR-030")


def test_vr_030_a_generation_date_of_the_wrong_shape(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, generation_date="2026-07")
    assert_fails_naming(corpus, "VR-030")


def test_vr_061_a_drifted_generation_input(corpus: CorpusFixture) -> None:
    """The loosening direction VR-061 exists to close: an edit to a generation
    input that no existing document fails on and no other digest moves for."""
    vocabulary = corpus.root / "synthetic" / "field-label-vocabulary.json"
    vocabulary.write_bytes(vocabulary.read_bytes() + b"\n")
    report = assert_fails_naming(corpus, "VR-061")
    messages = [f.message for f in report.all_failures if f.rule_id == "VR-061"]
    assert any("field-label-vocabulary.json" in message for message in messages), messages


def test_vr_061_names_every_document_generated_from_the_drifted_input(
    corpus: CorpusFixture,
) -> None:
    """Attribution is per entry and covers every document recording the input."""
    config = corpus.root / "synthetic" / "generation-config.json"
    config.write_bytes(config.read_bytes() + b"\n")
    report = assert_fails_naming(corpus, "VR-061")
    named = {f.location_id for f in report.all_failures if f.rule_id == "VR-061"}
    assert len(named) == SYNTHETIC_PROJECTS, report.render()


def test_vr_061_a_missing_generation_input_key(corpus: CorpusFixture) -> None:
    """Three keys exactly. Two of three passes every per-key comparison while
    leaving one input undigested."""
    corpus.mutate(
        "synthetic/PRJ-001",
        lambda document: document["entries"][0]["generation_inputs"].pop(GENERATION_INPUT_PATHS[0]),
    )
    assert_fails_naming(corpus, "VR-061")


def test_vr_061_the_roster_is_not_a_generation_inputs_key(corpus: CorpusFixture) -> None:
    """The roster is the fourth generation input and is covered by `roster_hash`.

    Admitting it here would put a canonical-content digest into a mapping every
    other value of which is over raw bytes — the conflation `data-model.md`
    names as the one most easily lost.

    The offending key is **composed** from the reader's own path rather than
    written as a literal: VR-045 requires exactly one source file under `/src`
    to name the roster's filename, and a test that spelled it out would be the
    second namer, failing that rule to evidence this one.
    """
    from model.roster.reader import DEFAULT_ROSTER_PATH

    corpus.mutate(
        "synthetic/PRJ-001",
        lambda document: document["entries"][0]["generation_inputs"].update(
            {f"data/corpus/synthetic/{DEFAULT_ROSTER_PATH.name}": OTHER_DIGEST}
        ),
    )
    assert_fails_naming(corpus, "VR-061")


def test_vr_061_a_traversal_sequence_in_a_key_is_never_opened(corpus: CorpusFixture) -> None:
    """CWE-73. The key is compared as a literal string against the closed set
    **before** any filesystem access, so it fails on set equality and no
    resolution step is ever reached."""
    corpus.mutate(
        "synthetic/PRJ-001",
        lambda document: document["entries"][0]["generation_inputs"].update(
            {"data/corpus/../../../etc/passwd": OTHER_DIGEST}
        ),
    )
    report = assert_fails_naming(corpus, "VR-061")
    messages = [f.message for f in report.all_failures if f.rule_id == "VR-061"]
    assert any("never opened" in message for message in messages), messages


# --------------------------------------------------------------------------
# T043 - VR-051…VR-055: the synthetic corpus datasheet
# --------------------------------------------------------------------------

DATASHEET = "synthetic/datasheet.md"


def _datasheet(corpus: CorpusFixture) -> Path:
    return corpus.root.joinpath(*DATASHEET.split("/"))


def _rewrite_datasheet(corpus: CorpusFixture, transform: Callable[[str], str]) -> None:
    path = _datasheet(corpus)
    path.write_bytes(transform(path.read_text(encoding="utf-8")).encode("utf-8"))


@pytest.mark.parametrize("heading", validate_module.DATASHEET_SECTIONS)
def test_vr_051_a_missing_required_disclosure(corpus: CorpusFixture, heading: str) -> None:
    """One case per required heading: eight disclosures, eight failing
    directions, because "all eight are required" is eight conditions."""
    _rewrite_datasheet(corpus, lambda text: text.replace(f"## {heading}\n", "## Elsewhere\n"))
    assert_fails_naming(corpus, "VR-051")


def test_vr_051_a_missing_datasheet(corpus: CorpusFixture) -> None:
    _datasheet(corpus).unlink()
    assert_fails_naming(corpus, "VR-051")


@pytest.mark.parametrize(
    ("phrase", "what"),
    [
        (phrase, what)
        for what, phrases in validate_module.STATED_LIMITS_DISCLOSURES
        for phrase in phrases
    ],
)
def test_vr_052_a_stated_limit_that_stops_short(
    corpus: CorpusFixture, phrase: str, what: str
) -> None:
    """Each required phrase removed on its own, so the rule is evidenced as
    stated sub-conditions rather than as one all-or-nothing string match."""
    _rewrite_datasheet(corpus, lambda text: _drop_phrase(text, phrase))
    report = assert_fails_naming(corpus, "VR-052")
    assert what[:10] in report.render(), report.render()


def _drop_phrase(text: str, phrase: str) -> str:
    """Remove one phrase case-insensitively, leaving the rest of the prose."""
    return re.sub(re.escape(phrase), "[redacted]", text, flags=re.IGNORECASE)


def test_vr_052_a_disclosure_stated_outside_stated_limits_does_not_count(
    corpus: CorpusFixture,
) -> None:
    """The phrases are matched over the `Stated Limits` body only.

    A limit mentioned in passing under `Composition` is not a stated limit, and
    a rule matching the whole document could not tell the two apart.
    """

    def relocate(text: str) -> str:
        limits = text.index("## Stated Limits")
        return text[:limits] + "## Stated Limits\n\nSee above.\n"

    _rewrite_datasheet(corpus, relocate)
    assert_fails_naming(corpus, "VR-052")


@pytest.mark.parametrize("spellings", validate_module.PREPROCESSING_DISCLOSURE)
def test_vr_053_a_preprocessing_section_that_does_not_say_none(
    corpus: CorpusFixture, spellings: tuple[str, ...]
) -> None:
    def redact(text: str) -> str:
        for spelling in spellings:
            text = _drop_phrase(text, spelling)
        return text

    _rewrite_datasheet(corpus, redact)
    assert_fails_naming(corpus, "VR-053")


def test_vr_053_an_empty_preprocessing_section(corpus: CorpusFixture) -> None:
    """The section is required *and* non-empty: a heading alone answers nothing,
    which is the difference from E001's datasheet, where the category was
    omitted with a reason."""

    def empty(text: str) -> str:
        start = text.index("## Preprocessing")
        end = text.index("## Intended Uses")
        return text[:start] + "## Preprocessing\n\n" + text[end:]

    _rewrite_datasheet(corpus, empty)
    assert_fails_naming(corpus, "VR-053")


def test_vr_054_a_literal_sha256_value_in_the_datasheet(corpus: CorpusFixture) -> None:
    _rewrite_datasheet(corpus, lambda text: text + f"\nRoster hash: {OTHER_DIGEST}\n")
    assert_fails_naming(corpus, "VR-054")


def test_vr_054_a_bare_64_character_hexadecimal_run(corpus: CorpusFixture) -> None:
    """Without the prefix, so the two halves of the rule are evidenced apart."""
    _rewrite_datasheet(corpus, lambda text: text + f"\nRecorded as {'a1' * 32}.\n")
    assert_fails_naming(corpus, "VR-054")


def test_vr_055_a_datasheet_inside_a_corpus_location(corpus: CorpusFixture) -> None:
    """A manifest beside it turns its directory into a corpus location, which
    would make the datasheet a corpus document requiring an entry."""
    (corpus.root / "synthetic" / MANIFEST_FILENAME).write_bytes(
        _canonical_json({"location_id": "synthetic", "layer": "SYNTHETIC", "entries": []})
    )
    assert_fails_naming(corpus, "VR-055")


def test_vr_055_a_datasheet_that_is_not_there(corpus: CorpusFixture) -> None:
    _datasheet(corpus).unlink()
    assert_fails_naming(corpus, "VR-055")


# --------------------------------------------------------------------------
# T053 - VR-031, VR-032, VR-033, VR-035a...VR-035d, VR-036
#
# **Every case here mutates the *record*, never the document.** The comparison
# these rules make is between a recorded class set and one re-derived from the
# emitted bytes, so a case that edited the PDF would be testing the deriver
# (which `test_corpus_derive.py` owns, against hand-authored fixtures and a
# fixture vocabulary) rather than the comparison. Editing the record is also the
# defect these rules exist to catch: a manifest claiming a document carries
# something it does not.
# --------------------------------------------------------------------------


def _classes(corpus: CorpusFixture, location_id: str) -> list[str]:
    return list(corpus.entry(location_id, 0)["irregularity_classes"])


def _location_recording(corpus: CorpusFixture, class_name: str) -> str:
    """A synthetic location whose single entry records `class_name`."""
    for index in range(SYNTHETIC_PROJECTS):
        location_id = f"synthetic/PRJ-{index + 1:03d}"
        if class_name in _classes(corpus, location_id):
            return location_id
    raise AssertionError(f"the fixture records {class_name} nowhere")


def _location_not_recording(corpus: CorpusFixture, class_name: str) -> str:
    for index in range(SYNTHETIC_PROJECTS):
        location_id = f"synthetic/PRJ-{index + 1:03d}"
        if class_name not in _classes(corpus, location_id):
            return location_id
    raise AssertionError(f"every fixture entry records {class_name}")


def test_vr_031_a_class_outside_the_closed_five(corpus: CorpusFixture) -> None:
    corpus.edit_entry("synthetic/PRJ-001", 0, irregularity_classes=["SMUDGED_MARGIN"])
    assert_fails_naming(corpus, "VR-031")


def test_vr_031_a_repeated_class(corpus: CorpusFixture) -> None:
    corpus.edit_entry(
        "synthetic/PRJ-001", 0, irregularity_classes=["OUT_OF_ORDER_DATE", "OUT_OF_ORDER_DATE"]
    )
    assert_fails_naming(corpus, "VR-031")


def test_vr_031_classes_out_of_ascending_order(corpus: CorpusFixture) -> None:
    """MS-2 sorts the list at construction, so one content has one serialization;
    an unsorted list read back would break VR-042's byte comparison silently."""
    corpus.edit_entry(
        "synthetic/PRJ-001",
        0,
        irregularity_classes=["SCAN_DEGRADATION", "MISSING_OR_BLANK_FIELD"],
    )
    assert_fails_naming(corpus, "VR-031")


@pytest.mark.parametrize("class_name", validate_module.IRREGULARITY_CLASSES)
def test_vr_032_a_layer_missing_one_of_the_five(corpus: CorpusFixture, class_name: str) -> None:
    """Each of the five in turn, because "all five" fails five different ways and
    a single case would evidence only whichever one it happened to drop."""
    for index in range(SYNTHETIC_PROJECTS):
        location_id = f"synthetic/PRJ-{index + 1:03d}"
        recorded = _classes(corpus, location_id)
        if class_name in recorded:
            corpus.edit_entry(
                location_id,
                0,
                irregularity_classes=[value for value in recorded if value != class_name],
            )
    assert_fails_naming(corpus, "VR-032")


def test_vr_033_a_layer_below_the_classed_floor(corpus: CorpusFixture) -> None:
    """Four of five carry a class in the fixture, which is exactly the floor;
    dropping one takes it to 60% and the rule fires."""
    location_id = _location_recording(corpus, "MISSING_OR_BLANK_FIELD")
    corpus.edit_entry(location_id, 0, irregularity_classes=[])
    assert_fails_naming(corpus, "VR-033")


STRUCTURAL_RULE_CLASSES = [
    ("VR-035a", "MISSING_OR_BLANK_FIELD"),
    ("VR-035b", "INCONSISTENT_FIELD_LABEL"),
    ("VR-035c", "OUT_OF_ORDER_DATE"),
    ("VR-035d", "PAGE_SPLIT_FIELD"),
]


@pytest.mark.parametrize(("rule_id", "class_name"), STRUCTURAL_RULE_CLASSES)
def test_vr_035x_a_class_recorded_but_not_derivable_from_the_document(
    corpus: CorpusFixture, rule_id: str, class_name: str
) -> None:
    """Direction one: the manifest claims a defect the emitted document does not
    carry. A comparison that only checked the other direction would pass a
    manifest recording every class on every document."""
    location_id = _location_not_recording(corpus, class_name)
    corpus.edit_entry(
        location_id, 0, irregularity_classes=sorted({*_classes(corpus, location_id), class_name})
    )
    assert_fails_naming(corpus, rule_id)


@pytest.mark.parametrize(("rule_id", "class_name"), STRUCTURAL_RULE_CLASSES)
def test_vr_035x_a_class_the_document_carries_but_the_manifest_omits(
    corpus: CorpusFixture, rule_id: str, class_name: str
) -> None:
    """Direction two: the document carries the defect and the record is silent.
    This is the direction that makes downstream results unpartitionable, which is
    why FR-031a requires disagreement in **either** direction to fail."""
    location_id = _location_recording(corpus, class_name)
    remaining = [value for value in _classes(corpus, location_id) if value != class_name]
    corpus.edit_entry(location_id, 0, irregularity_classes=remaining)
    assert_fails_naming(corpus, rule_id)


def test_vr_035x_scan_degradation_is_never_compared_against_the_derived_set(
    corpus: CorpusFixture,
) -> None:
    """The narrowing FR-031a states: derived == recorded intersected with the four.

    The fixture's degraded document records `SCAN_DEGRADATION`, which no
    structural derivation can recover. Comparing against the whole recorded set
    would fail it — for something that is not a defect — so the passing baseline
    is itself this assertion, made explicit here rather than left implicit in the
    control.
    """
    location_id = _location_recording(corpus, "SCAN_DEGRADATION")
    assert "SCAN_DEGRADATION" in _classes(corpus, location_id)
    assert_passes(corpus)


def test_vr_035x_a_document_that_opens_but_cannot_be_derived_is_named_not_skipped(
    corpus: CorpusFixture,
) -> None:
    """FR-001a's posture: never skipped so the run can continue.

    A truncated document still opens far enough for some readers and then fails
    structural derivation; skipping it would silently exempt it from every rule
    asserted over emitted documents.
    """
    path = corpus.document_path("synthetic/PRJ-001", 0)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 3])
    corpus.edit_entry("synthetic/PRJ-001", 0, content_hash=sha256_of_bytes(path.read_bytes()))
    report = run(corpus)
    assert report.exit_code != 0
    named = {failure.rule_id for failure in report.all_failures}
    assert named & {"VR-013", "VR-035a", "VR-035b", "VR-035c", "VR-035d"}, report.render()


def test_vr_036_scan_degradation_recorded_on_a_document_with_no_raster(
    corpus: CorpusFixture,
) -> None:
    location_id = _location_not_recording(corpus, "SCAN_DEGRADATION")
    corpus.edit_entry(
        location_id,
        0,
        irregularity_classes=sorted({*_classes(corpus, location_id), "SCAN_DEGRADATION"}),
    )
    assert_fails_naming(corpus, "VR-036")


def test_vr_036_a_raster_on_a_document_recording_no_degradation(corpus: CorpusFixture) -> None:
    """The other half. Without it a generator that rasterized every page while
    recording the class nowhere would satisfy the rule."""
    location_id = _location_recording(corpus, "SCAN_DEGRADATION")
    remaining = [value for value in _classes(corpus, location_id) if value != "SCAN_DEGRADATION"]
    corpus.edit_entry(location_id, 0, irregularity_classes=remaining)
    assert_fails_naming(corpus, "VR-036")


# --------------------------------------------------------------------------
# T054 - VR-037, VR-038, VR-039: the citation anchor and the retained text layer
#
# These three are evidenced by replacing a committed document with one rendered
# to break exactly one of them, because the property under test is a property of
# the *file* and no manifest edit can express its absence.
# --------------------------------------------------------------------------


def _replace_document(corpus: CorpusFixture, location_id: str, raw: bytes) -> None:
    """Swap a corpus document's bytes and keep its `content_hash` honest.

    Without the digest update VR-012 fires and the case would be evidenced by
    the wrong rule.
    """
    path = corpus.document_path(location_id, 0)
    path.write_bytes(raw)
    corpus.edit_entry(location_id, 0, content_hash=sha256_of_bytes(raw))


def _render_pages(
    lines_per_page: Sequence[Sequence[str]],
    *,
    image: bool = False,
    anchor_baseline: float = 756.0,
) -> bytes:
    """A hand-built multi-page PDF, optionally with a page-sized raster.

    Hand-built rather than generated: every case below needs a document that is
    *wrong* in one specific way, and the generator refuses to emit one. The
    first line of each page is drawn at `anchor_baseline`, so moving that
    parameter into the body region is how "the anchor lies inside the raster"
    is expressed without a second renderer.
    """
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(612, 792), invariant=1)
    for lines in lines_per_page:
        cursor = anchor_baseline
        for text in lines:
            obj = canvas.beginText()
            obj.setTextRenderMode(0)
            obj.setFont("Helvetica", 9)
            obj.setTextOrigin(60, cursor)
            obj.textOut(text)
            canvas.drawText(obj)
            cursor -= 14.0
        if image:
            canvas.drawImage(
                ImageReader(Image.new("L", (100, 140), 200)),
                54,
                54,
                width=504,
                height=684,
                preserveAspectRatio=False,
                anchor="sw",
                mask=None,
            )
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _anchored_pages(corpus: CorpusFixture, location_id: str) -> list[list[str]]:
    identifier = corpus.entry(location_id, 0)["location"][:-4]
    return [
        [f"{identifier} | Page 1 of 2", "Transmittal Record"],
        [f"{identifier} | Page 2 of 2", "Submitted Items"],
    ]


def test_vr_037_a_page_whose_citation_anchor_is_absent(corpus: CorpusFixture) -> None:
    _replace_document(
        corpus, "synthetic/PRJ-005", _render_pages([["Transmittal Record"], ["Submitted Items"]])
    )
    assert_fails_naming(corpus, "VR-037")


def test_vr_037_a_page_that_extracts_no_text_at_all(corpus: CorpusFixture) -> None:
    """The recognition case FR-032 exists to forbid: a raster with no text layer
    under it. The file still opens, so nothing else notices."""
    _replace_document(corpus, "synthetic/PRJ-005", _render_pages([[], []], image=True))
    assert_fails_naming(corpus, "VR-037")


def test_vr_037_an_anchor_lying_inside_the_raster_rectangle(corpus: CorpusFixture) -> None:
    """A raster covering the one text object the searchable-scan construction
    exists to keep readable. Expressed by drawing the anchor into the body band
    rather than by growing the raster, which is the same defect and needs no
    second renderer."""
    location_id = "synthetic/PRJ-005"
    _replace_document(
        corpus,
        location_id,
        _render_pages(_anchored_pages(corpus, location_id), image=True, anchor_baseline=400.0),
    )
    assert_fails_naming(corpus, "VR-037")


def test_vr_038_a_layer_carrying_no_degraded_page(corpus: CorpusFixture) -> None:
    """Non-vacuity, evidenced by removing the only raster in the fixture.

    Without VR-038, VR-037's "no degraded page requires recognition" would be
    true over an empty set — the defect STF-001 found once already.
    """
    location_id = _location_recording(corpus, "SCAN_DEGRADATION")
    _replace_document(corpus, location_id, _render_pages(_anchored_pages(corpus, location_id)))
    assert_fails_naming(corpus, "VR-038")


def test_vr_039_a_text_layer_that_does_not_match_the_model(corpus: CorpusFixture) -> None:
    """The case VR-037 cannot see: a page carrying its anchor and extracting
    text, whose retained layer is not the document's."""
    location_id = "synthetic/PRJ-005"
    _replace_document(corpus, location_id, _render_pages(_anchored_pages(corpus, location_id)))
    assert_fails_naming(corpus, "VR-039")


def test_vr_039_a_document_the_generator_no_longer_produces(corpus: CorpusFixture) -> None:
    """A committed file with no corresponding model is a corpus committed from a
    generator that has moved on, not a rendering defect."""
    location_id = "synthetic/PRJ-005"
    document = corpus.read(location_id)
    entry = dict(document["entries"][0])
    renamed = "zz-not-a-generated-document.pdf"
    shutil.move(str(corpus.document_path(location_id, 0)), str(corpus.dir(location_id) / renamed))
    entry["location"] = renamed
    document["entries"] = [entry]
    corpus.write(location_id, document)
    assert_fails_naming(corpus, "VR-039")
