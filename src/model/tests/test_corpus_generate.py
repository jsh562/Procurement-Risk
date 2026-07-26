"""The modeling-boundary half of the runner split: rules needing a generator run.

T045 (VR-040a, VR-040b), T046 (VR-041, VR-042), T047 (VR-034, VR-046, VR-047,
VR-048). These are here rather than in `corpus-validate` because they require
**re-running the generator**, which is a different failure mode from reading a
corpus: a validator that regenerated in order to validate could not tell a
corpus defect from a generator defect (`data-model.md` §Validation Rules).

**Two runs, six differing dimensions.** VR-040a is not satisfiable by running
one command twice in one directory, so the compared runs differ in every
dimension the rule names: a different absolute checkout path (the modeling
package and the generator's committed inputs are copied to a second absolute
path and imported from there), a different process, a distinct `PYTHONHASHSEED`,
a non-UTC `TZ`, a non-C `LC_ALL`, and a shuffled directory enumeration. The
checkout copy is what makes the first dimension real rather than nominal — a
different *output* directory alone would leave every input path identical.

**Nothing here writes into the working copy.** VR-041 and VR-042 compare bytes,
and an in-place re-run would compare a file against itself and pass whatever the
writer did — the one way those rules can be made vacuous. The reference layer is
the committed one when it exists and a generated temporary tree until it does
(the committed layer is written once, by T056), and the comparison run always
writes somewhere else.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from model.corpus.codes import STRUCTURAL_FIELD_KEYS, canonical_label
from model.corpus.equipment import (
    CATEGORY_SECTIONS,
    EquipmentMapError,
    section_for_category,
    unbacked_sections,
)
from model.corpus.generate import (
    SYNTHETIC_LAYER_DIRECTORY,
    GeneratedDocument,
    GeneratorError,
    generate_corpus,
    load_config,
)
from model.corpus.manifest import MANIFEST_FILENAME
from model.corpus.model import DocumentModel, FieldValue, Page, RenderDirective, document_model_hash
from model.corpus.paths import DEFAULT_CORPUS_ROOT, REPO_ROOT
from model.roster.reader import read_roster

# --------------------------------------------------------------------------
# Populations and floors, from the rules rather than from the configuration
# --------------------------------------------------------------------------

#: VR-034 (SC-010). Held here rather than read from `generation-config.json`:
#: a floor read from the artifact that decides whether it is met could be
#: lowered by the same edit that stops meeting it.
LAYER_DOCUMENT_FLOOR = 25
ROSTER_PROJECTS = 5
ROSTER_VENDORS = 12

COMMITTED_LAYER = DEFAULT_CORPUS_ROOT / SYNTHETIC_LAYER_DIRECTORY

#: VR-041's three outcomes, as a stated rule rather than a reader's inference.
BYTES_MATCH = "PASS"
BYTES_DIFFER = "FAIL"
REGENERATION_EVENT = "REGENERATION_EVENT"

#: Everything the generator reads, copied into the alternate checkout. Written
#: out rather than copying `data/` wholesale so the list is a statement of what
#: generation depends on; the vendored PDFs are deliberately absent, because the
#: generator reads the real *manifest* (for VR-048's section check) and never a
#: real document.
GENERATOR_INPUTS = (
    "corpus/manifest.schema.json",
    "corpus/real/retrieval-policy.json",
    "corpus/real/exclusions.json",
    "corpus/real/ufgs/manifest.json",
    "corpus/synthetic/generation-config.json",
    "corpus/synthetic/equipment-category-map.json",
    "corpus/synthetic/field-label-vocabulary.json",
    "corpus/synthetic/datasheet.md",
)

# --------------------------------------------------------------------------
# The two runs
# --------------------------------------------------------------------------

#: Dimension set A — the reference run's environment.
ENVIRONMENT_A: Mapping[str, str] = {
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
    "LC_ALL": "C",
}
#: Dimension set B — differing in all three, none of which may reach the output.
#: `tr_TR.UTF-8` is chosen deliberately: its case-folding rules are the classic
#: counterexample, so a comparison that had drifted into locale-aware collation
#: would show here rather than on a locale nobody tests.
ENVIRONMENT_B: Mapping[str, str] = {
    "PYTHONHASHSEED": "12345",
    "TZ": "Asia/Kathmandu",
    "LC_ALL": "tr_TR.UTF-8",
}

#: The sixth dimension. Installed **before** the generator is imported, so it
#: covers import-time enumeration as well as the run's.
SHUFFLE_ENUMERATION = """
import os
import random


class _Shuffled:
    '''A scandir result in a deliberately wrong order.

    Supports the context-manager protocol as well as iteration, because
    `pathlib` enters `os.scandir` as a context manager and a bare iterator
    would fail for a reason unrelated to ordering.
    '''

    def __init__(self, entries):
        self._entries = entries

    def __iter__(self):
        return iter(self._entries)

    def __next__(self):
        raise StopIteration

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


_scandir, _listdir = os.scandir, os.listdir
_order = random.Random(4_0400)


def _shuffled_scandir(path="."):
    entries = list(_scandir(path))
    _order.shuffle(entries)
    return _Shuffled(entries)


def _shuffled_listdir(path=None):
    names = _listdir(path) if path is not None else _listdir()
    _order.shuffle(names)
    return names


os.scandir = _shuffled_scandir
os.listdir = _shuffled_listdir
"""

#: The child's body. `CHECKOUT` is asserted rather than assumed: if the copied
#: package did not win over the installed one, the run would silently exercise
#: the checkout dimension it claims to vary.
GENERATE = """
from pathlib import Path

import model.corpus.generate as generator

if CHECKOUT is not None:
    resolved = Path(generator.__file__).resolve()
    assert resolved.is_relative_to(Path(CHECKOUT).resolve()), (
        "VR-040a: the alternate checkout did not take precedence; the run imported "
        + str(resolved)
    )

result = generator.generate_corpus(Path(ROOT))
print("documents", result.document_count)
"""


def alternate_checkout(base: Path) -> Path:
    """Copy the modeling package and the generator's inputs to a second path.

    `paths.REPO_ROOT` is derived from `__file__`, so a package imported from
    here resolves *its* data directory rather than the repository's — which is
    what makes "a different absolute checkout path" a property of the run and
    not just a different `--root`.
    """
    package = base / "src" / "model" / "src" / "model"
    shutil.copytree(REPO_ROOT / "src" / "model" / "src" / "model", package)
    shutil.copytree(REPO_ROOT / "data" / "roster", base / "data" / "roster")
    for relative in GENERATOR_INPUTS:
        destination = base / "data" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / "data" / relative, destination)
    return base


def run_generator(
    root: Path,
    *,
    environment: Mapping[str, str],
    shuffle: bool = False,
    checkout: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Generate a whole layer into `root`, in a fresh process.

    A subprocess rather than a call, because "a different process" is one of the
    six dimensions and because `PYTHONHASHSEED` cannot be changed after start.
    """
    preamble = SHUFFLE_ENUMERATION if shuffle else ""
    source_root = None if checkout is None else checkout / "src" / "model" / "src"
    script = (
        f"ROOT = {json.dumps(str(root))}\n"
        f"CHECKOUT = {'None' if checkout is None else json.dumps(str(checkout))}\n"
        f"{preamble}\n{GENERATE}"
    )
    env = {**os.environ, **environment}
    if source_root is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(source_root), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
        env=env,
    )
    assert completed.returncode == 0, (
        f"the generator run failed\n{completed.stdout}\n{completed.stderr}"
    )
    return completed


# --------------------------------------------------------------------------
# Reading a layer back
# --------------------------------------------------------------------------


def layer_manifests(root: Path) -> tuple[Path, ...]:
    """Every synthetic manifest under a corpus root, in location order."""
    return tuple(sorted((root / SYNTHETIC_LAYER_DIRECTORY).glob(f"*/{MANIFEST_FILENAME}")))


def recorded_hashes(root: Path) -> Mapping[str, str]:
    """`document_model_hash` as **recorded in the manifest**, keyed by document.

    Read from the file rather than taken from the run in memory: VR-040a's
    reference artifact is the recorded value, and comparing two in-memory hashes
    would compare the generator against itself.
    """
    recorded: dict[str, str] = {}
    for manifest in layer_manifests(root):
        document = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in document["entries"]:
            recorded[f"{document['project_id']}/{entry['location']}"] = entry["document_model_hash"]
    return recorded


def manifest_bytes(root: Path) -> Mapping[str, bytes]:
    return {manifest.parent.name: manifest.read_bytes() for manifest in layer_manifests(root)}


def document_bytes(root: Path) -> Mapping[str, bytes]:
    return {
        f"{pdf.parent.name}/{pdf.name}": pdf.read_bytes()
        for pdf in sorted((root / SYNTHETIC_LAYER_DIRECTORY).glob("*/*.pdf"))
    }


def divergences(reference: Mapping[str, object], observed: Mapping[str, object]) -> tuple[str, ...]:
    """Every key whose value differs, plus every key present in only one side.

    Both directions, because a comparison that only checked shared keys would
    pass a run that emitted half the layer.
    """
    keys = sorted(set(reference) | set(observed))
    return tuple(
        key
        for key in keys
        if key not in reference or key not in observed or reference[key] != observed[key]
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def reference_layer(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    """VR-040a's reference artifact: the committed layer, or a stand-in for it.

    The committed synthetic layer is written once, by its own generation run in
    Phase 6. Until then the reference is a layer generated into a temporary tree
    under dimension set A — which keeps every rule here asserting over a real
    emitted layer rather than skipping, and switches to the committed bytes the
    moment they exist without an edit here.
    """
    if any(COMMITTED_LAYER.glob(f"*/{MANIFEST_FILENAME}")):
        return "the committed layer", DEFAULT_CORPUS_ROOT
    root = tmp_path_factory.mktemp("reference") / "corpus"
    root.mkdir()
    run_generator(root, environment=ENVIRONMENT_A)
    return "a reference run (the committed layer lands with T056)", root


@pytest.fixture(scope="session")
def comparison_layer(
    tmp_path_factory: pytest.TempPathFactory, reference_layer: tuple[str, Path]
) -> Path:
    """The second run: a different process, path, checkout, and environment."""
    base = tmp_path_factory.mktemp("comparison")
    root = base / "elsewhere" / "on" / "disk" / "corpus"
    root.mkdir(parents=True)
    checkout = alternate_checkout(base / "second-checkout")
    run_generator(root, environment=ENVIRONMENT_B, shuffle=True, checkout=checkout)
    return root


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory):
    """One in-process run, for the rules asserted over regenerated **models**.

    In-process because VR-046, VR-047 and VR-040b quantify over the document
    models rather than over emitted bytes, and a subprocess would hand back
    files instead of models.
    """
    root = tmp_path_factory.mktemp("models") / "corpus"
    root.mkdir()
    return generate_corpus(root)


# --------------------------------------------------------------------------
# T045 - VR-040a: stability against the recorded value
# --------------------------------------------------------------------------


def test_vr_040a_every_document_model_hash_reproduces_the_recorded_value(
    reference_layer: tuple[str, Path], comparison_layer: Path
) -> None:
    """Re-running from the committed seed reproduces every recorded hash.

    The reference is the value **recorded in a manifest**, not a second
    in-memory computation, and the two runs differ in all six named dimensions.
    """
    label, reference_root = reference_layer
    reference = recorded_hashes(reference_root)
    observed = recorded_hashes(comparison_layer)
    assert reference, f"VR-040a: the reference layer ({label}) recorded no hash at all"
    stale = divergences(reference, observed)
    assert not stale, (
        f"VR-040a: {len(stale)} document(s) did not reproduce the hash recorded in "
        f"{label}: {[(key, reference.get(key), observed.get(key)) for key in stale]}"
    )


def test_vr_040a_the_two_runs_differ_in_all_six_named_dimensions() -> None:
    """The criterion's own precondition, asserted rather than described.

    Without this, a later edit collapsing the two runs into one command run
    twice in one directory would leave every assertion above passing.
    """
    assert set(ENVIRONMENT_A) == set(ENVIRONMENT_B)
    for name in ENVIRONMENT_A:
        assert ENVIRONMENT_A[name] != ENVIRONMENT_B[name], name
    assert ENVIRONMENT_B["TZ"] != "UTC" and ENVIRONMENT_B["LC_ALL"] not in {"C", "POSIX"}
    # The remaining three are structural rather than environmental, so they are
    # asserted over the code that produces them: a separate process, a different
    # absolute checkout path, and a shuffled directory enumeration.
    source = inspect.getsource(run_generator)
    assert "subprocess.run" in source, "the runs share a process"
    assert "PYTHONPATH" in source, "the runs share a checkout path"
    assert "os.scandir = _shuffled_scandir" in SHUFFLE_ENUMERATION
    assert "os.listdir = _shuffled_listdir" in SHUFFLE_ENUMERATION
    assert inspect.signature(alternate_checkout).return_annotation == "Path"


def test_vr_040a_a_planted_divergence_is_reported_naming_the_document(
    reference_layer: tuple[str, Path],
) -> None:
    """The failing direction: a comparison that cannot fail evidences nothing."""
    _, reference_root = reference_layer
    reference = dict(recorded_hashes(reference_root))
    assert reference
    victim = sorted(reference)[0]
    perturbed = {**reference, victim: "sha256:" + "0" * 64}
    stale = divergences(reference, perturbed)
    assert stale == (victim,), f"VR-040a: the comparison missed a planted divergence: {stale}"


def test_vr_040a_a_missing_document_is_reported_in_both_directions(
    reference_layer: tuple[str, Path],
) -> None:
    """A run emitting half the layer must not pass on the half it emitted."""
    _, reference_root = reference_layer
    reference = dict(recorded_hashes(reference_root))
    victim = sorted(reference)[0]
    partial = {key: value for key, value in reference.items() if key != victim}
    assert divergences(reference, partial) == (victim,)


# --------------------------------------------------------------------------
# T045 - VR-040b: sensitivity
#
# Separate cases from VR-040a's, because the two pass and fail independently and
# one passing is not evidence for the other: a hash that silently dropped the
# render directives would satisfy stability forever.
# --------------------------------------------------------------------------


def _first(generated) -> GeneratedDocument:
    return generated.documents[0]


def test_vr_040b_mutating_an_identity_field_moves_the_hash(generated) -> None:
    model = _first(generated).model
    moved = replace(model, identity={**dict(model.identity), "document_id": "PRJ-999-T9999-R9"})
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: identity"


def test_vr_040b_mutating_an_ordered_field_value_moves_the_hash(generated) -> None:
    model = _first(generated).model
    head, *rest = model.fields
    moved = replace(
        model, fields=(FieldValue(label=head.label, value=head.value + " (amended)"), *rest)
    )
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: field value"


def test_vr_040b_reordering_the_fields_moves_the_hash(generated) -> None:
    """Order is content: two documents laying the same fields out differently
    are two documents, which is why `fields` is an ordered tuple of pairs."""
    model = _first(generated).model
    moved = replace(model, fields=tuple(reversed(model.fields)))
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: field order"


def test_vr_040b_mutating_a_pages_text_moves_the_hash(generated) -> None:
    model = _first(generated).model
    head, *rest = model.pages
    moved = replace(
        model, pages=(Page(text=head.text + "\nextra", directive=head.directive), *rest)
    )
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: page text"


def test_vr_040b_mutating_a_render_directives_template_id_moves_the_hash(generated) -> None:
    model = _first(generated).model
    head, *rest = model.pages
    directive = replace(head.directive, template_id=head.directive.template_id + "-alt")
    moved = replace(model, pages=(Page(text=head.text, directive=directive), *rest))
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: template id"


def test_vr_040b_mutating_a_degradation_profile_moves_the_hash(generated) -> None:
    """DM-2's reason for putting the directives inside the hash.

    Degradation leaves the page text unchanged, so without this a generator that
    degraded different pages on each run would still pass FR-021.
    """
    model = _first(generated).model
    head, *rest = model.pages
    directive = replace(head.directive, degradation_profile="SCAN_HEAVY")
    moved = replace(model, pages=(Page(text=head.text, directive=directive), *rest))
    assert document_model_hash(moved) != document_model_hash(model), "VR-040b: degradation profile"


def test_vr_040b_mutating_a_degradation_parameter_moves_the_hash(generated) -> None:
    """PB-3's narrowest case: two models differing only in one parameter."""
    model = _first(generated).model
    head, *rest = model.pages
    first = replace(
        head.directive, degradation_profile="SCAN_LIGHT", parameters={"blur_radius_tenths": 2}
    )
    second = replace(first, parameters={"blur_radius_tenths": 3})
    left = replace(model, pages=(Page(text=head.text, directive=first), *rest))
    right = replace(model, pages=(Page(text=head.text, directive=second), *rest))
    assert document_model_hash(left) != document_model_hash(right), "VR-040b: degradation parameter"


def test_vr_040b_an_unchanged_model_does_not_move_the_hash(generated) -> None:
    """The control. Without it, a hash function returning a fresh random value
    would satisfy every sensitivity case above."""
    model = _first(generated).model
    rebuilt = DocumentModel(
        identity=dict(model.identity),
        fields=tuple(FieldValue(label=f.label, value=f.value) for f in model.fields),
        pages=tuple(
            Page(
                text=page.text,
                directive=RenderDirective(
                    template_id=page.directive.template_id,
                    degradation_profile=page.directive.degradation_profile,
                    parameters=dict(page.directive.parameters),
                ),
            )
            for page in model.pages
        ),
    )
    assert document_model_hash(rebuilt) == document_model_hash(model)


# --------------------------------------------------------------------------
# T046 - VR-041: byte identity of the rendered documents, three outcomes
# --------------------------------------------------------------------------


def pinned_renderer_version(distribution: str) -> str | None:
    """The version pinned for `distribution` in the modeling boundary's lockfile.

    Read from `uv.lock` rather than from the installed environment, because the
    pin is what VR-041 judges the observed version against; reading both from
    the environment would compare a value with itself.
    """
    lock = (REPO_ROOT / "src" / "model" / "uv.lock").read_text(encoding="utf-8")
    name, version = None, None
    for line in lock.splitlines():
        if line.startswith("name = "):
            name = line.partition("=")[2].strip().strip('"')
        elif line.startswith("version = ") and name == distribution:
            version = line.partition("=")[2].strip().strip('"')
            break
    return version


def observed_renderer_version(distribution: str) -> str:
    from importlib.metadata import version

    return version(distribution)


def byte_identity_outcome(observed: str, pinned: str | None, mismatches: Sequence[str]) -> str:
    """VR-041's three outcomes, as a rule rather than a reader's inference.

    observed == pinned and bytes equal → pass; observed == pinned and bytes
    differ → fail; observed != pinned → **neither**: a regeneration event,
    reported with both versions and excluded from SC-024's population until the
    re-render lands (FR-021a). Treating a pin change as a corpus defect is the
    misreading this function exists to prevent.
    """
    if pinned is None or observed != pinned:
        return REGENERATION_EVENT
    return BYTES_DIFFER if mismatches else BYTES_MATCH


def test_vr_041_rerendered_bytes_equal_the_reference_bytes(
    reference_layer: tuple[str, Path], comparison_layer: Path
) -> None:
    """Byte identity under the pinned renderer, judged in a temporary tree.

    The Linux verification runner is the platform of record for this comparison
    (MS-4): a Windows checkout is rewritten on the way out of git, so a
    line-ending difference there is not a corpus defect.
    """
    label, reference_root = reference_layer
    requirement = load_config().renderer_requirement
    pinned = pinned_renderer_version(requirement)
    observed = observed_renderer_version(requirement)

    reference = document_bytes(reference_root)
    assert reference, f"VR-041: the reference layer ({label}) holds no document"
    mismatches = divergences(reference, document_bytes(comparison_layer))
    outcome = byte_identity_outcome(observed, pinned, mismatches)

    if outcome == REGENERATION_EVENT:
        pytest.skip(
            f"VR-041: regeneration event - observed {requirement} {observed}, lockfile pins "
            f"{pinned}. Not a validation failure (FR-021a); this document set is excluded from "
            "SC-024's population until the re-render lands"
        )
    assert outcome == BYTES_MATCH, (
        f"VR-041: {len(mismatches)} document(s) re-rendered to different bytes under the pinned "
        f"{requirement} {pinned}: {list(mismatches)}"
    )


@pytest.mark.parametrize(
    ("observed", "pinned", "mismatches", "expected"),
    [
        ("5.0.0", "5.0.0", (), BYTES_MATCH),
        ("5.0.0", "5.0.0", ("PRJ-001/doc.pdf",), BYTES_DIFFER),
        ("5.1.0", "5.0.0", ("PRJ-001/doc.pdf",), REGENERATION_EVENT),
        ("5.1.0", "5.0.0", (), REGENERATION_EVENT),
        ("5.0.0", None, (), REGENERATION_EVENT),
    ],
)
def test_vr_041_the_three_outcomes_are_a_stated_rule(
    observed: str, pinned: str | None, mismatches: tuple[str, ...], expected: str
) -> None:
    """Three outcomes, not two — including the failing one, which no correct
    corpus can be made to produce."""
    assert byte_identity_outcome(observed, pinned, mismatches) == expected


def test_vr_041_a_planted_byte_difference_is_reported_naming_the_document(
    reference_layer: tuple[str, Path], tmp_path: Path
) -> None:
    """The failing direction, over real bytes rather than over a stub."""
    _, reference_root = reference_layer
    reference = dict(document_bytes(reference_root))
    victim = sorted(reference)[0]
    perturbed = {**reference, victim: reference[victim] + b"%%injected\n"}
    mismatches = divergences(reference, perturbed)
    assert mismatches == (victim,), mismatches
    assert byte_identity_outcome("5.0.0", "5.0.0", mismatches) == BYTES_DIFFER


def test_vr_041_the_lockfile_pin_is_readable_and_the_renderer_is_the_configured_one() -> None:
    """A pin the reader cannot find would make every run a regeneration event,
    which reads as a permanent pass."""
    requirement = load_config().renderer_requirement
    assert pinned_renderer_version(requirement), (
        f"VR-041: {requirement} is not pinned in the modeling boundary's lockfile"
    )
    assert pinned_renderer_version("a-distribution-that-is-not-locked") is None


# --------------------------------------------------------------------------
# T046 - VR-042: byte identity of the manifests
# --------------------------------------------------------------------------


def test_vr_042_a_rerun_leaves_every_manifest_byte_identical(
    reference_layer: tuple[str, Path], comparison_layer: Path
) -> None:
    """Zero entries change, compared **between two trees**.

    The re-run writes into a temporary tree and never the working copy: an
    in-place rewrite would compare a file against itself and pass whatever the
    writer did, which is the one way this rule can be made vacuous.
    """
    label, reference_root = reference_layer
    assert reference_root != comparison_layer
    reference = manifest_bytes(reference_root)
    assert len(reference) == ROSTER_PROJECTS, (
        f"VR-042: the reference layer ({label}) holds {len(reference)} manifest(s), "
        f"expected {ROSTER_PROJECTS}"
    )
    changed = divergences(reference, manifest_bytes(comparison_layer))
    assert not changed, (
        f"VR-042: {len(changed)} manifest(s) are not byte-identical to {label}: {list(changed)}"
    )


def test_vr_042_a_planted_manifest_edit_is_reported(
    reference_layer: tuple[str, Path],
) -> None:
    """The failing direction. Manifests are generated artifacts, not editable
    records: a hand edit is rewritten by the next run and the comparison fails."""
    _, reference_root = reference_layer
    reference = dict(manifest_bytes(reference_root))
    victim = sorted(reference)[0]
    hand_edited = {**reference, victim: reference[victim].replace(b"\n", b"\r\n", 1)}
    assert divergences(reference, hand_edited) == (victim,)


def test_vr_042_manifests_carry_no_line_ending_written_by_the_platform(
    comparison_layer: Path,
) -> None:
    """MS-4, checked on the emitted bytes rather than trusted.

    The development machine is Windows and the platform of record is the Linux
    runner; a manifest written in text mode would carry `\\r\\n` here and pass a
    comparison against another Windows-written file, then fail on the runner.
    """
    for name, raw in manifest_bytes(comparison_layer).items():
        assert b"\r\n" not in raw, f"VR-042: {name} carries a platform line ending"
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), name


# --------------------------------------------------------------------------
# T047 - VR-034, VR-046, VR-047, VR-048: content and population
# --------------------------------------------------------------------------


def test_vr_034_the_layer_holds_at_least_twenty_five_documents(
    reference_layer: tuple[str, Path],
) -> None:
    label, root = reference_layer
    documents = document_bytes(root)
    locations = {key.split("/", 1)[0] for key in documents}
    assert len(locations) == ROSTER_PROJECTS, (
        f"VR-034: {label} holds {len(locations)} location(s), expected {ROSTER_PROJECTS}"
    )
    assert len(documents) >= LAYER_DOCUMENT_FLOOR, (
        f"VR-034: {label} holds {len(documents)} document(s), below the floor of "
        f"{LAYER_DOCUMENT_FLOOR}"
    )


def test_vr_034_a_layer_below_the_floor_is_rejected(tmp_path: Path) -> None:
    """The failing direction, built by the real pipeline rather than asserted
    over a number.

    Four documents per project still covers all five projects, all twelve
    vendors and a chain in each, so the generator's own pre-write assertions
    pass — the layer is complete, valid, and twenty documents. Only VR-034's
    floor rejects it, which is the point: the floor is not implied by the
    coverage rules and needs its own case.
    """
    root = tmp_path / "corpus"
    root.mkdir()
    small = replace(load_config(), documents_per_project=4)
    generate_corpus(root, config=small)
    emitted = document_bytes(root)
    assert len({key.split("/", 1)[0] for key in emitted}) == ROSTER_PROJECTS
    assert len(emitted) < LAYER_DOCUMENT_FLOOR, emitted.keys()


def _structural_fields(document: GeneratedDocument) -> Mapping[str, str]:
    """FR-023's six, read off the regenerated model by their canonical labels."""
    by_label = {field.label: field.value for field in document.model.fields}
    return {key: by_label.get(canonical_label(key), "") for key in STRUCTURAL_FIELD_KEYS}


def missing_structural_fields(documents: Iterable[GeneratedDocument]) -> tuple[str, ...]:
    """Every `document/field` pair whose structural field is absent or blank."""
    return tuple(
        f"{document.plan.document_id}/{key}"
        for document in documents
        for key, value in sorted(_structural_fields(document).items())
        if not value.strip()
    )


def test_vr_046_every_model_carries_the_six_structural_fields(generated) -> None:
    assert len(STRUCTURAL_FIELD_KEYS) == 6, STRUCTURAL_FIELD_KEYS
    missing = missing_structural_fields(generated.documents)
    assert not missing, f"VR-046: {len(missing)} structural field(s) absent or blank: {missing}"


def test_vr_046_the_specification_section_is_content_and_not_a_manifest_field(generated) -> None:
    """VR-027 prohibits `masterformat_section` as an *entry* field; the section a
    document answers is content, asserted here over the regenerated model."""
    for document in generated.documents:
        section = _structural_fields(document)["specification_section"]
        assert section == section_for_category(document.plan.items[0].category)


def test_vr_046_a_blanked_structural_field_is_reported(generated) -> None:
    """The failing direction — the shape `MISSING_OR_BLANK_FIELD` will take."""
    document = _first(generated)
    label = canonical_label(STRUCTURAL_FIELD_KEYS[0])
    blanked = replace(
        document.model,
        fields=tuple(
            FieldValue(label=field.label, value="" if field.label == label else field.value)
            for field in document.model.fields
        ),
    )
    missing = missing_structural_fields([replace(document, model=blanked)])
    assert missing == (f"{document.plan.document_id}/{STRUCTURAL_FIELD_KEYS[0]}",), missing


def resubmittal_chains(documents: Iterable[GeneratedDocument]) -> Mapping[str, tuple[str, ...]]:
    """Per project, every submittal number carrying a resubmittal chain.

    Re-derived here rather than read from `GenerationResult.chains`: the
    generator's own record of what it did is not evidence that it did it.
    """
    by_project: dict[str, dict[str, list[GeneratedDocument]]] = {}
    for document in documents:
        plan = document.plan
        by_project.setdefault(plan.project_id, {}).setdefault(plan.submittal_number, []).append(
            document
        )
    chains: dict[str, tuple[str, ...]] = {}
    for project_id, submittals in sorted(by_project.items()):
        found: list[str] = []
        for number, group in sorted(submittals.items()):
            ordered = sorted(group, key=lambda item: int(item.plan.revision_suffix))
            for earlier, later in zip(ordered, ordered[1:], strict=False):
                if int(later.plan.revision_suffix) > int(earlier.plan.revision_suffix) and (
                    later.plan.action.letter != earlier.plan.action.letter
                ):
                    found.append(number)
                    break
        chains[project_id] = tuple(found)
    return chains


def test_vr_047_all_five_roster_projects_are_covered(generated) -> None:
    expected = {project.id for project in read_roster().projects}
    assert len(expected) == ROSTER_PROJECTS
    assert {document.plan.project_id for document in generated.documents} == expected, "VR-047"


def test_vr_047_all_twelve_roster_vendors_appear_on_a_submittal(generated) -> None:
    expected = {vendor.id for vendor in read_roster().vendors}
    assert len(expected) == ROSTER_VENDORS
    covered = {document.plan.vendor_id for document in generated.documents}
    assert not expected - covered, f"VR-047: no submittal names roster vendors {expected - covered}"
    assert not covered - expected, (
        f"VR-047: submittals name non-roster vendors {covered - expected}"
    )


def test_vr_047_every_project_carries_a_resubmittal_chain(generated) -> None:
    chains = resubmittal_chains(generated.documents)
    empty = sorted(project for project, found in chains.items() if not found)
    assert not empty, f"VR-047: no resubmittal chain in project(s) {empty}"
    assert len(chains) == ROSTER_PROJECTS


def test_vr_047_a_layer_whose_resubmittals_were_dropped_is_reported(generated) -> None:
    """The failing direction: a chain needs a *second* document, and a set
    equality over projects alone would not notice its absence."""
    primaries = [
        document for document in generated.documents if document.plan.revision_suffix == "0"
    ]
    chains = resubmittal_chains(primaries)
    assert chains and all(not found for found in chains.values()), chains


def test_vr_047_a_resubmittal_repeating_its_action_code_is_not_a_chain(generated) -> None:
    """ "A differing action code" is part of the definition, not decoration: a
    resubmittal returned with the same action closes nothing."""
    document = _first(generated)
    chain = [
        item
        for item in generated.documents
        if item.plan.submittal_number == document.plan.submittal_number
    ]
    if len(chain) < 2:
        chain = [
            item
            for item in generated.documents
            if item.plan.submittal_number
            == max(
                (d.plan.submittal_number for d in generated.documents),
                key=lambda number: sum(
                    1 for d in generated.documents if d.plan.submittal_number == number
                ),
            )
        ]
    assert len(chain) >= 2, "the layer carries no chain to flatten"
    action = chain[0].plan.action
    flattened = [
        replace(item, plan=replace(item.plan, action=action)) if item in chain else item
        for item in chain
    ]
    assert all(not found for found in resubmittal_chains(flattened).values())


def test_vr_048_every_material_items_category_is_a_key_in_the_map(generated) -> None:
    unmapped = sorted(
        {
            item.category
            for document in generated.documents
            for item in document.plan.items
            if item.category not in CATEGORY_SECTIONS
        }
    )
    assert not unmapped, f"VR-048: material items name unmapped equipment categories {unmapped}"


def test_vr_048_every_mapped_section_is_held_by_the_committed_real_layer() -> None:
    """The second half. The first without it would let items map to sections the
    corpus does not hold, leaving the specification-to-submittal join dangling."""
    unbacked = unbacked_sections()
    assert not unbacked, (
        f"VR-048: the equipment-category map points at sections the real layer does not hold: "
        f"{[f'{category} -> {section}' for category, section in unbacked]}"
    )


def test_vr_048_an_unmapped_category_has_no_section(generated) -> None:
    """The failing direction of the first half: no default, no placeholder."""
    with pytest.raises(EquipmentMapError):
        section_for_category("A_CATEGORY_THE_MAP_DOES_NOT_HOLD")


def test_vr_048_a_section_the_real_layer_lacks_is_reported(tmp_path: Path) -> None:
    """The failing direction of the second half, over a real manifest that holds
    one section rather than over a stubbed comparison."""
    root = tmp_path / "corpus"
    (root / "real" / "ufgs").mkdir(parents=True)
    (root / "real" / "ufgs" / MANIFEST_FILENAME).write_bytes(
        json.dumps(
            {
                "location_id": "real/ufgs",
                "layer": "REAL",
                "entries": [{"masterformat_section": "01 33 00"}],
            }
        ).encode("utf-8")
    )
    unbacked = unbacked_sections(root)
    assert len(unbacked) == len(CATEGORY_SECTIONS), unbacked


def test_the_generator_refuses_to_write_a_layer_it_cannot_complete(tmp_path: Path) -> None:
    """FR-024/FR-025's pre-write assertion: a shortfall raises before the first
    PDF exists, rather than leaving a location half-populated."""
    root = tmp_path / "corpus"
    root.mkdir()
    with pytest.raises(GeneratorError) as raised:
        generate_corpus(root, inject=lambda plans: plans[:1])
    assert "FR-024" in str(raised.value) or "FR-025" in str(raised.value), str(raised.value)
    assert not list(root.rglob("*.pdf")), "a partial layer was written"
