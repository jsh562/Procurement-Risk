"""FR-027 / FR-028: as printed, no normalized twin, and no identity claimed.

T043. Two prohibitions, and prohibitions are the hardest thing to test — the
absence of a behaviour looks exactly like a test that forgot to check for it. So
each is asserted three ways: over the **types** (there is nowhere to put a
normalized form), over the **source** (nothing in the ingestion packages reaches
for a matcher or for E009's join surface), and over the **corpus** (the
committed catalogue's differently-spelled names survive the pipeline distinct).

**FR-027 — stored exactly as printed, with no normalized form alongside.** The
prohibition is scoped to text-kind values, which manufacturer and part number
are; numeric and date kinds are FR-062's, and a date's ISO-8601 canonical form
is not a violation of a rule that does not reach it. What is forbidden is a
second column, field, or attribute holding a cleaned copy — because a cleaned
copy is what a later reader joins on, and the join would be an identity claim
nobody wrote down.

**FR-028 — no assertion that two spellings are the same manufacturer.** The
committed catalogue prints `Verrikon Electric Co`, `Verrikon Electric` and
`Verrikon Elec.` for one key, and the generator draws among them deliberately.
Deciding they are one company is E009's work, done under
`resolved_entity`/`resolved_entity_member` with its own evidence and its own
review. This epic stores three strings and says nothing.

`model.corpus.manufacturers.canonical_key_for_printed_name` **does** resolve a
spelling to a catalogue key, and it is a corpus *validator*, not an ingestion
path — it re-derives what the generator recorded in order to check the
generator. The source scan below is scoped to the three packages this epic adds
for exactly that reason: the rule is about what ingestion asserts, not about
what the corpus tooling knows.
"""

from __future__ import annotations

import ast
import glob
from collections import Counter
from pathlib import Path

import pytest

from model.compute.coerce import coerce_value
from model.corpus.manufacturers import MANUFACTURERS, printed_names
from model.ingest.baseline import BaselinePage, extract_document
from model.ingest.documents import mint_document_id
from model.ingest.parse import read_pages
from model.ingest.writer import EXTRACTED_VALUE_COLUMNS
from model.llm.schemas import ExtractedField

ENTRY_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENTRY_ROOT.parents[1]
MODEL_PACKAGE = ENTRY_ROOT / "src" / "model"
EXTRACTION_REVISION = MODEL_PACKAGE / "schema" / "versions" / "0006_extraction.py"

#: The packages this epic adds. The rule is about what *ingestion* asserts, so
#: the corpus generator and its validators are deliberately outside the scan.
SCANNED_PACKAGES = ("ingest", "llm", "compute")

#: E009's sanctioned join surface. An ingestion module naming either of these
#: would be writing an identity claim, whatever it called the function.
IDENTITY_TABLES = ("resolved_entity", "resolved_entity_member")

#: Approximate-matching libraries. Named individually rather than by a pattern:
#: a scan for "fuzzy" would miss every one of them, and each is a dependency a
#: reviewer would have to notice in a lockfile diff otherwise.
MATCHING_LIBRARIES = (
    "difflib",
    "rapidfuzz",
    "fuzzywuzzy",
    "Levenshtein",
    "jellyfish",
    "metaphone",
    "unidecode",
)

#: Names that would hold a cleaned copy of a printed value. Matched against
#: column names and model field names, not against arbitrary identifiers —
#: `normalize_page_text` is a *comparison* form used for a substring containment
#: test and stores nothing, and a scan that flagged it would be crying wolf on
#: the one normalization FR-008 requires.
TWIN_MARKERS = ("normal", "canonical", "folded", "cleaned", "slug", "standardi")


def python_files() -> list[Path]:
    return [
        path
        for package in SCANNED_PACKAGES
        for path in sorted((MODEL_PACKAGE / package).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


# ---------------------------------------------------------------------------
# FR-027 — there is nowhere to put a normalized twin
# ---------------------------------------------------------------------------


def test_the_stored_column_set_holds_one_text_value_and_no_twin() -> None:
    """The writer's own column list, which is what actually reaches a row."""
    twins = [
        column
        for column in EXTRACTED_VALUE_COLUMNS
        if any(marker in column.lower() for marker in TWIN_MARKERS)
    ]
    assert not twins, f"FR-027: the value write would store a normalized twin: {twins}"
    assert {column for column in EXTRACTED_VALUE_COLUMNS if column.startswith("value_")} == {
        "value_kind",
        "value_text",
        "value_number",
    }, (
        "FR-027: `extracted_value` carries exactly two value-bearing columns — the "
        "canonical text and the typed numeric FR-062 governs — beside the kind that "
        "says which of them is populated. A fourth would be the twin."
    )


def test_the_extraction_table_declares_no_normalized_column() -> None:
    """And the schema agrees, read from the revision that created it.

    Asserted against the migration source rather than a live database so the
    prohibition is checked on every run, including the ones with no server.
    """
    source = EXTRACTION_REVISION.read_text(encoding="utf-8")
    table = source.split("CREATE TABLE extracted_value (", 1)[1].split("CREATE TABLE", 1)[0]
    declarations = [
        line.strip().split()[0]
        for line in table.splitlines()
        if line.strip() and not line.strip().startswith(("-", "C", ")"))
    ]
    twins = [
        name for name in declarations if any(marker in name.lower() for marker in TWIN_MARKERS)
    ]
    assert not twins, f"FR-027: `extracted_value` declares a normalized twin column: {twins}"


def test_the_model_output_carries_no_normalized_field() -> None:
    """The model is never asked for a cleaned form either.

    A model that returned both the printed text and a "standardised" name would
    put the twin in the pipeline a step earlier than the database, and the
    prohibition would be true of storage and false in effect.
    """
    twins = [
        name
        for name in ExtractedField.model_fields
        if any(marker in name.lower() for marker in TWIN_MARKERS)
    ]
    assert not twins, f"FR-027: the extraction schema asks for a normalized twin: {twins}"


@pytest.mark.parametrize("key", ["VRK", "HVD", "NRH"])
def test_every_printed_spelling_survives_coercion_unchanged(key: str) -> None:
    """FR-027, over the committed catalogue's own spellings.

    Character for character. `Verrikon Elec.` keeps its full stop, `VERRIKON
    ELECTRIC` keeps its case, and neither becomes the other.
    """
    for spelling in printed_names(key):
        coerced = coerce_value(spelling, "text")
        assert coerced.value_text == spelling
        assert coerced.printed == spelling
        assert coerced.value_number is None


@pytest.mark.parametrize("key", ["VRK", "HVD", "NRH"])
def test_distinct_spellings_stay_distinct_through_coercion(key: str) -> None:
    """FR-028 at the value layer: two spellings never collapse into one.

    The catalogue gives one manufacturer several printed names *on purpose*, so
    a coercion that folded case or stripped punctuation would silently assert
    they are the same company — the exact claim FR-028 forbids, made by a
    function nobody would think to look at.
    """
    spellings = printed_names(key)
    stored = {coerce_value(spelling, "text").value_text for spelling in spellings}
    assert len(stored) == len(set(spellings))


# ---------------------------------------------------------------------------
# FR-028 — nothing in the ingestion packages asserts an identity
# ---------------------------------------------------------------------------


def test_no_ingestion_module_names_the_identity_resolution_tables() -> None:
    """`resolved_entity` and `resolved_entity_member` are E009's, with their own
    evidence and their own review. A row this epic wrote into either would be an
    identity claim made by a job that has no basis for one."""
    offenders = {
        path.relative_to(MODEL_PACKAGE).as_posix(): table
        for path in python_files()
        for table in IDENTITY_TABLES
        if table in path.read_text(encoding="utf-8")
    }
    assert not offenders, f"FR-028: an ingestion module names E009's join surface: {offenders}"


def test_no_ingestion_module_imports_an_approximate_matcher() -> None:
    """Read from the syntax tree, so the docstring above does not report itself.

    An approximate matcher has exactly one use on a manufacturer name, and it is
    the use FR-028 forbids. Its presence is therefore treated as the claim
    rather than as a tool that happens to be available.
    """
    offenders: dict[str, str] = {}
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in MATCHING_LIBRARIES:
                    offenders[path.relative_to(MODEL_PACKAGE).as_posix()] = name
    assert not offenders, f"FR-028: an ingestion module imports an approximate matcher: {offenders}"


def test_the_scan_reads_a_real_population() -> None:
    """A prohibition scan that read nothing would pass identically.

    Every package this epic adds must contribute at least one file, or the two
    scans above are assertions about the empty set.
    """
    scanned = python_files()
    assert scanned
    for package in SCANNED_PACKAGES:
        assert [path for path in scanned if (MODEL_PACKAGE / package) in path.parents], (
            f"the scan reads no file under `model.{package}`, so a violation placed "
            f"there would be invisible"
        )


# ---------------------------------------------------------------------------
# FR-027 / FR-028 over the corpus itself
# ---------------------------------------------------------------------------


def synthetic_documents() -> list[Path]:
    return [
        Path(path) for path in sorted(glob.glob(str(REPO_ROOT / "data/corpus/synthetic/*/*.pdf")))
    ]


def test_the_corpus_prints_more_than_one_spelling_of_one_manufacturer() -> None:
    """The premise the next test rests on, asserted rather than assumed.

    If the committed layer happened to print one spelling per manufacturer,
    "distinct spellings stay distinct" would be true of an empty population and
    FR-028's real risk would go untested.
    """
    printed: Counter[str] = Counter()
    for path in synthetic_documents():
        pages = [BaselinePage(number=page.number, lines=page.lines) for page in read_pages(path)]
        for value in extract_document(pages):
            if value.field_name == "manufacturer":
                printed[value.value_text] += 1
    assert len(printed) > len({name.upper().replace(".", "") for name in printed}), (
        "the committed synthetic layer prints one spelling per manufacturer, so the "
        "identity-claim risk FR-028 addresses is not present in the corpus this test "
        "ranges over"
    )


def test_extraction_over_the_corpus_collapses_no_two_spellings() -> None:
    """FR-028, end to end over the whole synthetic layer — not a sample.

    Every manufacturer string the pipeline produces is compared against the
    spellings the committed catalogue actually prints. Zero produced strings sit
    outside that set, which is what makes "no spelling was rewritten into
    another" a statement about every value rather than about the ones someone
    looked at.
    """
    catalogue = {spelling for key in MANUFACTURERS for spelling in printed_names(key)}
    produced: set[str] = set()
    for path in synthetic_documents():
        document_id = mint_document_id(path.stem)
        pages = [BaselinePage(number=page.number, lines=page.lines) for page in read_pages(path)]
        for value in extract_document(pages):
            if value.field_name == "manufacturer":
                assert value.value_text.strip() == value.value_text, (
                    f"{document_id}: a manufacturer value was trimmed on the way through"
                )
                produced.add(value.value_text)
    assert produced, "no manufacturer value was read from the synthetic layer at all"
    unknown = produced - catalogue
    assert not unknown, (
        f"FR-027: {sorted(unknown)} are manufacturer strings the pipeline produced that "
        f"the committed catalogue does not print. A value that is neither what the page "
        f"shows nor what the catalogue holds has been rewritten somewhere between them."
    )
