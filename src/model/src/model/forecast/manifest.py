"""The run manifest: five requirements' worth of fields, assembled once.

FR-014 is the row-serialization hash and its convention label; FR-042 the
committed fixture file's own digest recorded **beside** it, so a run in which
the two are equal is a run that hashed the file; FR-043 the provenance identity
a reproduction compares for exact equality; FR-044 the frame and sampling shape;
FR-045 the input's layer label and datasheet reference. Grouped that way here
because each fails separately. The grid horizon is read from `schema_constants`
over the connection and never written as a literal (AD-009).
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import subprocess
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from model.corpus.manifest import DIGEST_PATTERN, LAYER_REAL, LAYER_SYNTHETIC
from model.forecast.censoring import censoring_indicator
from model.forecast.config import HELD_OUT_FRACTION, read_run_shape, split_seed_entropy
from model.forecast.read import ProcurementInput
from model.forecast.serialize import CANONICAL_SERIALIZATION
from model.forecast.shrinkage import VendorShrinkage
from model.forecast.split import HELD_OUT, TRAIN, SplitResult
from model.procurement import paths as procurement_paths
from model.procurement.serialize import dataset_content_hash, read_payload
from model.roster.reader import canonical_bytes

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "DRAW_SERIALIZATION",
    "LIBRARY_VERSION_KEYS",
    "MODEL_VERSION",
    "OPEN_LINE_DRAW_SEMANTIC",
    "POPULATION_RANK_HELD_OUT_PREDICTION",
    "POPULATION_RANK_LINE_POSTERIOR",
    "VENDOR_SHRINKAGE_HDI_PROBABILITY",
    "ArtifactDigest",
    "CodeRevision",
    "FixtureProvenance",
    "ManifestError",
    "RunManifest",
    "artifact_hash_over",
    "build_manifest",
    "code_revision",
    "draw_bytes",
    "draw_digest",
    "library_versions",
    "read_fixture_provenance",
    "roster_hash_of",
]


class ManifestError(RuntimeError):
    """Raised when a provenance field cannot be recorded honestly.

    A `RuntimeError` rather than a `ValueError` because most cases are the
    environment's rather than the caller's: git is absent, the committed fixture
    is missing, NumPy publishes no BLAS record. Principle I is what makes each of
    them a refusal — a manifest field invented because its source was unreachable
    is exactly the unattributable value the principle exists to exclude.
    """


# ---------------------------------------------------------------------------
# Labels and versions this epic owns
# ---------------------------------------------------------------------------

#: The byte layout every draw digest and the artifact hash are taken over, and
#: the one value `ck_forecast_run__draw_serialization` admits. Named beside the
#: functions that implement it, so the label and the layout are one fact.
DRAW_SERIALIZATION = "float64-le-c-contiguous"

#: `forecast_run.model_version`. Names AD-001's structure rather than a release:
#: a reader comparing two runs needs to know whether the same model produced
#: them, and "the multi-state sojourn model, revision 1" is what answers that.
MODEL_VERSION = "sojourn-lognormal-hierarchical-1"

#: `forecast_run.artifact_schema_version` — the version of the *format*, not of
#: the model. One is the first artifact format; the two move independently, which
#: is why E003 gave them separate columns.
ARTIFACT_SCHEMA_VERSION = 1

#: What an open line's stored draw means, and the one value
#: `ck_forecast_run__open_line_semantic` admits. It rides on the run because the
#: open population lives in the delivered `line_posterior`, which E007 may not
#: alter; the held-out counterpart is a column on `held_out_prediction`.
OPEN_LINE_DRAW_SEMANTIC = "conditional_remaining_duration_from_run_as_of_date"

#: The six keys `ck_forecast_run__library_versions_shape` requires present. The
#: constraint checks presence only, so the values are recorded verbatim rather
#: than parsed — but a missing key fails the insert, which is why the tuple is
#: here and the collection below is quantified over it.
LIBRARY_VERSION_KEYS: tuple[str, ...] = ("pymc", "arviz", "numpy", "pandas", "pytensor", "blas")

#: The five keys above that are distribution versions, resolvable from installed
#: metadata without importing the package. `blas` is not one of them: it is a
#: property of the NumPy build rather than of a distribution.
_DISTRIBUTION_KEYS: tuple[str, ...] = ("pymc", "arviz", "numpy", "pandas", "pytensor")

#: The credible level the per-vendor shrinkage interval is reported at. Stated
#: rather than defaulted because "wider" is undefined between intervals of
#: different mass (SC-005), and `shrinkage.py` refuses to assume one. 0.94 is
#: ArviZ's own default, chosen there precisely so an interval is not misread as a
#: 95% frequentist confidence interval.
VENDOR_SHRINKAGE_HDI_PROBABILITY = 0.94

#: The two artifact stores' order inside the artifact hash (`data-model.md`
#: § Hashes). `0` for the delivered `line_posterior`, `1` for
#: `held_out_prediction`, so the digest is recomputable from the stored rows
#: alone by joining each row's `(run_id, po_line_id)` to the split assignment.
POPULATION_RANK_LINE_POSTERIOR = 0
POPULATION_RANK_HELD_OUT_PREDICTION = 1

#: 40 lowercase hex, the whole of a git object name — the form
#: `ck_forecast_run__commit_format` pins.
_COMMIT_LENGTH = 40
_HEX_DIGITS = frozenset("0123456789abcdef")

#: How long git is given to answer. A hung child process would leave the job
#: waiting before it had read a single row, with no output explaining why.
_GIT_TIMEOUT_SECONDS = 30.0

#: A SHA-256 digest is 32 bytes; `ck_line_posterior__draw_digest_length` and
#: `ck_forecast_run__artifact_hash_length` both check exactly that.
_DIGEST_BYTES = 32

#: The widest decimal seed `ck_forecast_run__seed_entropy_format` admits — 39
#: digits covers 0 through 2^128 − 1, which is a `SeedSequence` entropy.
_SEED_DIGITS_MAX = 39


# ---------------------------------------------------------------------------
# FR-043 — provenance identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CodeRevision:
    """Which code produced a run, and whether the worktree agreed with it.

    Two fields rather than one because they answer different questions and only
    the pair is honest: `commit` says which revision, `worktree_dirty` says
    whether the files on disk were that revision. A run fitted from a modified
    worktree is not reproducible from the commit alone, and E003 gave
    `code_worktree_dirty` its own NOT NULL column so silence is not an option.
    """

    commit: str
    worktree_dirty: bool


def _git(repo_root: Path, *arguments: str) -> str:
    """One git command's stdout, or a refusal naming what could not be read.

    Refusing rather than degrading: `code_commit` is `char(40)` with a format
    check and `code_worktree_dirty` is NOT NULL, so there is no value to fall
    back to and a fabricated one would be worse than no run.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            # Suppression rationale: resolving `git` through PATH is intended —
            # the question is which revision *this checkout* is at, and a pinned
            # absolute path would answer for a different installation.
            ["git", "-C", str(repo_root), *arguments],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ManifestError(
            f"`git {' '.join(arguments)}` could not be run in {repo_root}: {exc}. The run's "
            f"code revision is a NOT NULL manifest field with no honest substitute, so the "
            f"fit refuses rather than recording a placeholder"
        ) from exc
    if completed.returncode != 0:
        raise ManifestError(
            f"`git {' '.join(arguments)}` exited {completed.returncode} in {repo_root}: "
            f"{completed.stderr.strip() or '(no output)'}. A fit must record which revision "
            f"produced it; a checkout that is not a work tree cannot answer that"
        )
    return completed.stdout


def code_revision(repo_root: Path | str | None = None) -> CodeRevision:
    """The checkout's commit and whether its worktree is modified (FR-043).

    `status --porcelain` rather than `diff --quiet`: the porcelain form reports
    untracked files as well as modifications, and an untracked module on the
    import path changes what ran just as surely as an edited one does.
    """
    root = _repo_root(repo_root)
    commit = _git(root, "rev-parse", "HEAD").strip()
    if len(commit) != _COMMIT_LENGTH or not _HEX_DIGITS.issuperset(commit):
        raise ManifestError(
            f"git reported HEAD as {commit!r}, which is not 40 lowercase hex characters. "
            f"`ck_forecast_run__commit_format` would refuse it, and a truncated revision "
            f"identifies no code"
        )
    modified = _git(root, "status", "--porcelain").strip()
    return CodeRevision(commit=commit, worktree_dirty=bool(modified))


def _blas_version() -> str:
    """The BLAS the numerical stack resolved to, from NumPy's own build record.

    Read from `numpy.__config__` rather than guessed: BLAS choice moves floating
    results, which is why `library_versions` carries it as a required key. NumPy
    publishing no record is a refusal for the same reason a missing commit is —
    `ck_forecast_run__library_versions_shape` requires the key present, and a
    key present with a made-up value is worse than a run that did not happen.
    """
    try:
        configuration = np.__config__.show(mode="dicts")
        record = configuration["Build Dependencies"]["blas"]
        name, version = record["name"], record["version"]
    except (AttributeError, KeyError, TypeError) as exc:
        raise ManifestError(
            f"NumPy {np.__version__} publishes no BLAS record under "
            f"`__config__.show(mode='dicts')` ({type(exc).__name__}: {exc}), so the "
            f"`blas` key `ck_forecast_run__library_versions_shape` requires cannot be "
            f"filled from a measurement"
        ) from exc
    return f"{name}-{version}"


def library_versions() -> dict[str, str]:
    """The numerical stack a run was fitted on, keyed as the constraint requires.

    Resolved from installed distribution metadata rather than from each package's
    `__version__`, so nothing here imports PyMC or ArviZ to ask — the manifest is
    assembled on a path a unit test must be able to reach cheaply.
    """
    try:
        versions = {name: metadata.version(name) for name in _DISTRIBUTION_KEYS}
    except metadata.PackageNotFoundError as exc:
        raise ManifestError(
            f"a declared dependency is not installed in this environment: {exc}. The run's "
            f"library versions are what scope FR-032's digest claim, so an unresolvable one "
            f"leaves the claim unbounded"
        ) from exc
    versions["blas"] = _blas_version()
    return versions


def roster_hash_of(procurement_input: ProcurementInput) -> str:
    """The line roster this run was fitted against, taken from the rows read.

    A measurement rather than a second read of E001's roster file: every line
    carries the `roster_hash` E005 loaded it under, so this is the roster the fit
    actually saw. Lines disagreeing is a database assembled from two loads, which
    no single manifest field can describe.
    """
    hashes = {line.roster_hash for line in procurement_input.lines}
    if len(hashes) != 1:
        raise ManifestError(
            f"the lines read carry {len(hashes)} distinct roster hashes "
            f"({sorted(hashes)[:3]}…); one run is fitted against one roster, so a mixed "
            f"population has no single value for `forecast_run.roster_hash`"
        )
    roster_hash = hashes.pop()
    if not DIGEST_PATTERN.fullmatch(roster_hash):
        raise ManifestError(
            f"{roster_hash!r} is not a `sha256:`-prefixed lowercase hex digest, so "
            f"`ck_forecast_run__roster_hash_format` would refuse it"
        )
    return roster_hash


# ---------------------------------------------------------------------------
# FR-042, FR-045 — the committed fixture, its layer and its datasheet
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixtureProvenance:
    """What the committed fixture says about itself, observed at run time.

    Both digests are carried because the pair is the evidence FR-023's *warning*
    disposition rests on: `observed` is taken over the fixture payload on disk
    now, `published` is the value E005's sidecar records. They differ exactly when
    the file moved without the sidecar, which warns and completes rather than
    refusing — the rows the fit read are unchanged, and only the chain back to the
    upstream artifact has broken (G-16).
    """

    observed_digest: str
    published_digest: str
    layer: str
    datasheet_ref: str

    @property
    def digest_matches_published(self) -> bool:
        """Whether the fixture on disk still digests to its own sidecar's value."""
        return self.observed_digest == self.published_digest


def read_fixture_provenance(repo_root: Path | str | None = None) -> FixtureProvenance:
    """E005's fixture digest, layer label and datasheet reference (FR-042, FR-045).

    The digest is `dataset_content_hash` over the *parsed* payload — E005's own
    convention, copied rather than re-invented, so git end-of-line normalisation
    cannot move it and one file does not carry two digest conventions (E005's
    G-3). Computed here rather than copied out of the sidecar, because a value
    read from the sidecar could never disagree with the sidecar and the
    provenance warning would be unreachable.
    """
    root = _repo_root(repo_root)
    fixture_path = procurement_paths.fixture_path(root)
    hash_path = procurement_paths.hash_path(root)
    try:
        payload = read_payload(fixture_path)
        published = read_payload(hash_path)["dataset_content_hash"]
    except (OSError, KeyError, ValueError) as exc:
        raise ManifestError(
            f"the committed fixture and its digest sidecar could not both be read from "
            f"{fixture_path.parent} ({type(exc).__name__}: {exc}). FR-042 records the "
            f"fixture's own digest beside the row hash, and a run that cannot read it has "
            f"no provenance chain back to the datasheet"
        ) from exc

    layer = payload.get("layer")
    if layer not in (LAYER_REAL, LAYER_SYNTHETIC):
        raise ManifestError(
            f"the fixture declares layer {layer!r}; `ck_forecast_run__input_layer` admits "
            f"only {LAYER_REAL!r} and {LAYER_SYNTHETIC!r}, and the label is what tells a "
            f"reader of a forecast whether every number descending from it is "
            f"synthetic-derived"
        )
    observed = dataset_content_hash(payload)
    if not (DIGEST_PATTERN.fullmatch(observed) and DIGEST_PATTERN.fullmatch(str(published))):
        raise ManifestError(
            f"the fixture digests to {observed!r} against a published {published!r}; both "
            f"must be `sha256:`-prefixed lowercase hex for "
            f"`ck_forecast_run__fixture_digest_format` to admit either"
        )
    return FixtureProvenance(
        observed_digest=observed,
        published_digest=str(published),
        layer=layer,
        # Repository-relative and POSIX, so the recorded reference reads the same
        # on every platform and resolves against a clone rather than this disk.
        datasheet_ref=procurement_paths.datasheet_path(root).relative_to(root).as_posix(),
    )


def _repo_root(repo_root: Path | str | None) -> Path:
    """The checkout to resolve committed artifacts and git questions against.

    `None` means "this checkout", derived exactly as `model.procurement.paths`
    derives it, rather than the working directory: `forecast-fit` runs from
    `src/model` while the fixture and the datasheet live at the repository root.
    """
    return procurement_paths.REPO_ROOT if repo_root is None else Path(repo_root)


# ---------------------------------------------------------------------------
# FR-044 — the artifact hash and the draw digests it covers
# ---------------------------------------------------------------------------


def draw_bytes(draws: ArrayLike) -> bytes:
    """One line's draw vector in the layout `DRAW_SERIALIZATION` names.

    Little-endian IEEE-754 doubles, C order, no padding. Digests are taken over
    these bytes and never over a text rendering: a rendering depends on
    `extra_float_digits` and the session's locale, so the same draws would digest
    differently in two sessions.
    """
    values = np.asarray(draws, dtype=np.dtype("<f8"))
    if values.ndim != 1:
        raise ManifestError(
            f"a draw digest covers one line's vector, found {values.ndim} dimensions; a "
            f"frame of several lines would digest to a value belonging to no row"
        )
    if values.size == 0:
        raise ManifestError(
            "the draw serialization was asked for over zero draws; the digest of nothing is "
            "a constant every empty artifact row would share"
        )
    if not np.all(np.isfinite(values)):
        raise ManifestError(
            "every draw must be finite before it is digested; a NaN has more than one "
            "byte pattern, so its digest is not a function of its value"
        )
    return np.ascontiguousarray(values).tobytes()


def draw_digest(draws: ArrayLike) -> bytes:
    """32 raw bytes of SHA-256 over `draw_bytes(draws)` — never hex text.

    `bytea` and not `text`, matching the delivered `line_posterior.draw_digest`:
    the digest covers bytes, and a hex column would invite the question of which
    rendering it was taken over.
    """
    return hashlib.sha256(draw_bytes(draws)).digest()


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    """One artifact row's digest, with the two values that order it.

    The ordering columns travel *with* the digest rather than being applied by
    whoever calls `artifact_hash_over`, so a caller cannot get the order wrong by
    concatenating in the sequence it happened to build the rows in — which is the
    one way a recomputable digest becomes a label (DV-031).
    """

    population_rank: int
    canonical_ordinal: int
    draw_digest: bytes


def artifact_hash_over(digests: Iterable[ArtifactDigest]) -> bytes:
    """`forecast_run.artifact_hash`: SHA-256 over every row's digest, in order.

    `(population_rank, canonical_ordinal)` ascending, which is recomputable from
    the stored rows alone — the ordinal is reached by joining each artifact row to
    `forecast_split_assignment`. Sorting happens here rather than at the call
    site, so the order is a property of the function.
    """
    rows = tuple(digests)
    if not rows:
        raise ManifestError(
            "the artifact hash was asked for over zero artifact rows; a run with no stored "
            "forecast cannot be represented at all "
            "(`ck_forecast_run__open_line_count_positive`)"
        )
    positions = [(row.population_rank, row.canonical_ordinal) for row in rows]
    if len(set(positions)) != len(positions):
        repeated = sorted({position for position in positions if positions.count(position) > 1})
        raise ManifestError(
            f"two artifact rows claim the position(s) {repeated}; the hash is defined over "
            f"an ordered sequence, so a repeated position leaves its input undefined"
        )
    for row in rows:
        if len(row.draw_digest) != _DIGEST_BYTES:
            raise ManifestError(
                f"the digest at position ({row.population_rank}, {row.canonical_ordinal}) is "
                f"{len(row.draw_digest)} bytes; both stores' "
                f"`ck_…__draw_digest_length` require exactly {_DIGEST_BYTES}"
            )
    combined = hashlib.sha256()
    for row in sorted(rows, key=lambda entry: (entry.population_rank, entry.canonical_ordinal)):
        combined.update(row.draw_digest)
    return combined.digest()


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Every `forecast_run` column one fit writes, grouped by the requirement.

    `is_active` and `created_at` are deliberately absent: both carry a delivered
    `DEFAULT`, a run is inserted inactive by design (FR-015), and `created_at` is
    the database's answer to "when" rather than the job's. Frozen because the
    manifest is the provenance record — one a caller could edit after the write
    would describe a run that did not happen.
    """

    run_id: uuid.UUID

    # FR-014 — the row-serialization hash and its convention label.
    input_data_hash: str
    canonical_serialization: str

    # FR-042 — the fixture file's own digest, beside it and distinct from it.
    input_fixture_digest: str

    # FR-043 — provenance identity; the set a reproduction compares exactly.
    code_commit: str
    code_worktree_dirty: bool
    seed_entropy: str
    split_seed_entropy: str
    library_versions: dict[str, str]
    model_version: str
    artifact_schema_version: int
    roster_hash: str

    # FR-044 — the run frame and the sampling shape.
    chain_count: int
    draw_count: int
    tuning_count: int
    as_of_date: date
    horizon_days: int
    artifact_hash: bytes
    draw_serialization: str
    wall_clock_seconds: float

    # FR-045 — the input layer label and its datasheet reference.
    input_layer: str
    input_datasheet_ref: str

    # The fit's own measurements, each with its own requirement and column.
    covariate_names: tuple[str, ...]
    open_line_draw_semantic: str
    split_assignment_hash: str
    held_out_fraction_declared: float
    held_out_fraction_realized: float
    held_out_uncensored_event_count: int
    vendor_shrinkage: dict[str, VendorShrinkage]
    open_line_count: int
    training_line_count: int

    def vendor_shrinkage_payload(self) -> dict[str, dict[str, float]]:
        """The shrinkage weights as `fn_vendor_shrinkage_wellformed` reads them.

        Exactly three keys per member, in the order the helper names them, so a
        fourth key — which the helper counts and refuses — cannot arrive by way
        of a dataclass gaining a field.
        """
        return {
            vendor: {
                "median": float(weight.median),
                "hpdi_low": float(weight.hpdi_low),
                "hpdi_high": float(weight.hpdi_high),
            }
            for vendor, weight in self.vendor_shrinkage.items()
        }

    def row_parameters(self) -> dict[str, Any]:
        """Bind parameters for the run row, keyed by column name.

        The two `jsonb` columns are rendered here under the *same* canonical
        serialization the manifest's own label names, rather than by whatever
        `json.dumps` default a call site would reach for. `covariate_names` stays
        a list: psycopg adapts it to `text[]`, and a hand-built array literal
        would be a second encoding of a value the driver already encodes.
        """
        return {
            "run_id": self.run_id,
            "code_commit": self.code_commit,
            "code_worktree_dirty": self.code_worktree_dirty,
            "input_data_hash": self.input_data_hash,
            "seed_entropy": self.seed_entropy,
            "chain_count": self.chain_count,
            "draw_count": self.draw_count,
            "tuning_count": self.tuning_count,
            "library_versions": canonical_bytes(dict(self.library_versions)).decode("utf-8"),
            "artifact_hash": self.artifact_hash,
            "draw_serialization": self.draw_serialization,
            "artifact_schema_version": self.artifact_schema_version,
            "model_version": self.model_version,
            "as_of_date": self.as_of_date,
            "horizon_days": self.horizon_days,
            "wall_clock_seconds": self.wall_clock_seconds,
            "roster_hash": self.roster_hash,
            "covariate_names": list(self.covariate_names),
            "open_line_draw_semantic": self.open_line_draw_semantic,
            "input_fixture_digest": self.input_fixture_digest,
            "input_layer": self.input_layer,
            "input_datasheet_ref": self.input_datasheet_ref,
            "canonical_serialization": self.canonical_serialization,
            "split_seed_entropy": self.split_seed_entropy,
            "split_assignment_hash": self.split_assignment_hash,
            "held_out_fraction_declared": self.held_out_fraction_declared,
            "held_out_fraction_realized": self.held_out_fraction_realized,
            "held_out_uncensored_event_count": self.held_out_uncensored_event_count,
            "vendor_shrinkage": canonical_bytes(self.vendor_shrinkage_payload()).decode("utf-8"),
            "open_line_count": self.open_line_count,
            "training_line_count": self.training_line_count,
        }


def _whole(name: str, value: object, floor: int) -> int:
    """One count, proved a whole number at or above its floor.

    `bool` is excluded explicitly because it is an `int` subclass, and
    `chain_count=True` would otherwise record a one-chain run as deliberate.
    """
    if isinstance(value, bool) or not isinstance(value, int | np.integer):
        raise ManifestError(
            f"{name} is a whole number, found {type(value).__name__}; a fractional count "
            f"has no column to land in"
        )
    if int(value) < floor:
        raise ManifestError(
            f"{name} must be at least {floor}, found {value}; the delivered "
            f"`forecast_run` checks refuse anything below it"
        )
    return int(value)


def _seed_entropy(seed_entropy: int | str) -> str:
    """A seed in the decimal-digit form `ck_forecast_run__seed_entropy_format` pins.

    Text and not an integer column: 128 bits of `SeedSequence` entropy does not
    fit in `bigint`, per-chain streams are *spawned* rather than derived by
    arithmetic, and nothing ever adds to the stored value.
    """
    rendered = str(seed_entropy).strip()
    if not rendered.isdigit() or not 1 <= len(rendered) <= _SEED_DIGITS_MAX:
        raise ManifestError(
            f"{seed_entropy!r} is not 1 to 39 decimal digits, which is what "
            f"`ck_forecast_run__seed_entropy_format` admits. The seed is what makes a "
            f"re-fit a re-fit, so an unrecordable one is a refusal rather than a rounding"
        )
    return rendered


def _covariate_names(covariate_names: Sequence[str]) -> tuple[str, ...]:
    """The covariate set the fit's design matrix carried, checked for shape.

    Shape only: three plausible strings satisfy
    `ck_forecast_run__covariates_non_empty` whatever the fit used, which is why
    DV-036 compares the recorded list against the design matrix itself. What is
    closed here is the empty list, a blank element and a `None`.
    """
    names = tuple(covariate_names)
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ManifestError(
            f"the covariate list is {names!r}; `ck_forecast_run__covariates_non_empty` "
            f"refuses an empty list, a NULL element and an all-blank one, because each "
            f"records a covariate set that names nothing"
        )
    return names


def _split_measurements(
    procurement_input: ProcurementInput, split: SplitResult
) -> tuple[int, float, int]:
    """`training_line_count`, `held_out_fraction_realized` and the event count.

    Derived from the assignment rather than accepted from the caller: DV-028
    compares all three against the child rows the same write inserts, and a
    caller able to pass a fourth opinion is a caller able to make that comparison
    fail on a value nobody computed twice.

    The event count joins on the line's **`is_closed` column**, not on the dated
    censoring indicator, because that is the population `held_out_prediction`
    holds — its anchor foreign key references `(po_line_id, order_date,
    is_closed)`, so the column is what the stored rows are proved against.
    """
    delivered = {line.po_line_id: line.is_closed for line in procurement_input.lines}
    assignments = split.assignments
    unknown = [
        assignment.po_line_id
        for assignment in assignments
        if assignment.po_line_id not in delivered
    ]
    if unknown:
        raise ManifestError(
            f"{len(unknown)} split assignment(s) name a line the fit did not read — first "
            f"{unknown[0]}. The realized fractions and the event count are counts over both, "
            f"so a partial overlap makes each of them a count of something else"
        )
    if len(assignments) != len(delivered):
        raise ManifestError(
            f"the split assigns {len(assignments)} lines against {len(delivered)} read; every "
            f"line is assigned exactly once per run (DV-006), so an unequal pair means the "
            f"assignment and the input frame saw different populations"
        )
    held_out = [assignment for assignment in assignments if assignment.split_side == HELD_OUT]
    training = sum(1 for assignment in assignments if assignment.split_side == TRAIN)
    uncensored = sum(1 for assignment in held_out if delivered[assignment.po_line_id])
    return training, len(held_out) / len(assignments), uncensored


def build_manifest(
    connection: Connection | Session,
    *,
    procurement_input: ProcurementInput,
    input_data_hash: str,
    as_of_date: date,
    split: SplitResult,
    covariate_names: Sequence[str],
    vendor_shrinkage: Mapping[str, VendorShrinkage],
    open_line_count: int,
    seed_entropy: int | str,
    chain_count: int,
    draw_count: int,
    tuning_count: int,
    artifact_hash: bytes,
    wall_clock_seconds: float,
    run_id: uuid.UUID | None = None,
    repo_root: Path | str | None = None,
    fixture: FixtureProvenance | None = None,
) -> RunManifest:
    """Assemble one run's manifest, reading what it must and deriving the rest.

    Three fields are **read** rather than passed: the grid horizon comes from
    `schema_constants` over `connection` (AD-009, never the literal 365), the code
    revision from git, and the fixture digest, layer and datasheet reference from
    E005's committed artifacts. Six more are **derived** from the split and the
    rows — the roster hash, the split hash and seed, both fractions, the training
    line count and the held-out uncensored event count — because DV-028 compares
    them against the child rows this same write inserts, and a caller's second
    opinion is what would make that comparison fail.

    `open_line_count` is passed *and* verified against the censoring indicator at
    the anchor, since it must equal the number of `line_posterior` rows written
    (DV-001) and only the caller knows how many it built.

    `draw_count` is the **realized** number of draws per line rather than the
    declared constant: the array length checks compare each stored array against
    this row's own value, so recording a shape the run did not produce would be
    refused by the schema. DV-014 is what asserts the realized pair equals the
    published one on a run at the epic's committed shape.

    `fixture` may be supplied by a caller that has already read it — the fit does,
    because it reports the provenance warning — so one run parses the committed
    fixture once. Two reads would also admit the case where the file changed
    between them, which is a run describing two different inputs.
    """
    if not DIGEST_PATTERN.fullmatch(str(input_data_hash)):
        raise ManifestError(
            f"{input_data_hash!r} is not a `sha256:`-prefixed lowercase hex digest; it is "
            f"the one value FR-023 refuses on, so a malformed one has nothing to refuse "
            f"against"
        )
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise ManifestError(
            f"an as-of date is a `datetime.date`, found {type(as_of_date).__name__}; the "
            f"run's anchor is a calendar day and `forecast_run.as_of_date` is a `date`"
        )
    if len(artifact_hash) != _DIGEST_BYTES:
        raise ManifestError(
            f"the artifact hash is {len(artifact_hash)} bytes; "
            f"`ck_forecast_run__artifact_hash_length` requires exactly {_DIGEST_BYTES}"
        )
    if not isinstance(wall_clock_seconds, float | int) or not np.isfinite(wall_clock_seconds):
        raise ManifestError(
            f"the wall clock is {wall_clock_seconds!r}; `forecast_run.wall_clock_seconds` is "
            f"a finite non-negative measurement of how long the run took"
        )
    if float(wall_clock_seconds) < 0.0:
        raise ManifestError(
            f"the wall clock is {wall_clock_seconds}; a negative duration is a clock that "
            f"ran backwards, which `ck_forecast_run__wall_clock_non_negative` refuses"
        )
    if not vendor_shrinkage:
        raise ManifestError(
            "no vendor shrinkage weights were passed; FR-019 records one per vendor "
            "including a vendor with no training line, and an empty object satisfies the "
            "membership question by asking about nobody"
        )

    open_lines = sum(1 for line in procurement_input.lines if censoring_indicator(line, as_of_date))
    if _whole("open_line_count", open_line_count, 1) != open_lines:
        raise ManifestError(
            f"the caller reports {open_line_count} open lines and the censoring indicator "
            f"finds {open_lines} at {as_of_date}. `open_line_count` must equal the number of "
            f"`line_posterior` rows written (DV-001), so the two cannot be allowed to differ"
        )

    shape = read_run_shape(connection)
    revision = code_revision(repo_root)
    provenance = fixture if fixture is not None else read_fixture_provenance(repo_root)
    training_line_count, realized_fraction, uncensored_events = _split_measurements(
        procurement_input, split
    )

    return RunManifest(
        run_id=run_id if run_id is not None else uuid.uuid4(),
        input_data_hash=str(input_data_hash),
        canonical_serialization=CANONICAL_SERIALIZATION,
        input_fixture_digest=provenance.observed_digest,
        code_commit=revision.commit,
        code_worktree_dirty=revision.worktree_dirty,
        seed_entropy=_seed_entropy(seed_entropy),
        split_seed_entropy=split_seed_entropy(),
        library_versions=library_versions(),
        model_version=MODEL_VERSION,
        artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
        roster_hash=roster_hash_of(procurement_input),
        chain_count=_whole("chain_count", chain_count, 1),
        draw_count=_whole("draw_count", draw_count, 1),
        tuning_count=_whole("tuning_count", tuning_count, 0),
        as_of_date=as_of_date,
        horizon_days=shape.horizon_days,
        artifact_hash=bytes(artifact_hash),
        draw_serialization=DRAW_SERIALIZATION,
        wall_clock_seconds=float(wall_clock_seconds),
        input_layer=provenance.layer,
        input_datasheet_ref=provenance.datasheet_ref,
        covariate_names=_covariate_names(covariate_names),
        open_line_draw_semantic=OPEN_LINE_DRAW_SEMANTIC,
        split_assignment_hash=split.split_assignment_hash,
        held_out_fraction_declared=float(HELD_OUT_FRACTION),
        held_out_fraction_realized=realized_fraction,
        held_out_uncensored_event_count=uncensored_events,
        vendor_shrinkage=dict(vendor_shrinkage),
        open_line_count=open_lines,
        training_line_count=training_line_count,
    )
