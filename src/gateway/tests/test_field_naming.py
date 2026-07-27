"""TR-013/070/071/072/073 (OBJ3 VC7): the pin, the transform, the classification.

**The transform is forward-only, and that is a correctness rule rather than a
style.** A column name is derived from an attribute by lowercasing it and
replacing every `.` with `_`. That is not invertible: several convention
attributes carry underscores *inside* their own segments, so
`gen_ai.usage.input_tokens` and a hypothetical `gen_ai.usage_input.tokens`
transform to the same column. Recovering an attribute from a column name looks
reasonable and is wrong, so this file only ever transforms in one direction —
attributes into column names — and additionally fails when two attributes
collide onto one column.

**The pin's three recording sites must agree.** `gateway.config`, the
`COMMENT ON TABLE llm_invocation` mirror, and TR-070 itself. A bump that updates
one and forgets another leaves a database whose comment describes a different
convention release from the code that wrote its rows, and nothing but this check
would notice.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

from gateway.config import OTEL_GENAI_SEMCONV_VERSION
from gateway.record.writer import COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "specs" / "00004-traced-model-gateway" / "spec.md"
MIGRATION_PATH = (
    REPO_ROOT / "src" / "model" / "src" / "model" / "schema" / "versions" / "0102_llm_invocation.py"
)

#: The attributes the pinned release defines that this epic records. Held as
#: *attributes*, in their convention spelling — the column names are derived
#: from them below, never the other way round (TR-073).
#:
#: Verified against the published `v1.37.0` registry by T026, whose findings are
#: recorded in `data-model.md`. `gen_ai.provider.name` is the one that made the
#: pin move: `v1.36.0` defines `gen_ai.system` instead.
CONVENTION_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "gen_ai.provider.name",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    }
)

#: Sourced from the *general* attribute registry of the same release rather than
#: the generative-AI one, and named individually rather than inheriting the pin
#: silently (TR-071). Its column carries no `gen_ai_` prefix, which is what makes
#: the prefix a reliable signal of set membership.
GENERAL_REGISTRY_ATTRIBUTES: dict[str, str] = {"error.type": "error_type"}

#: TR-072's development class: the upstream convention guarantees no
#: attribute-key stability, so a pin bump may rename or reclassify these.
#: `cache_read_input_tokens` is here despite being gateway-local today, because
#: it moves into the convention-named set the moment a pinned release defines a
#: cached-input-tokens attribute — checked at 1.36.0 and 1.37.0, neither does.
DEVELOPMENT_CLASS_EXTRA: frozenset[str] = frozenset({"cache_read_input_tokens"})


def transform(attribute: str) -> str:
    """TR-073's rule, in the only direction it is valid in."""
    return attribute.lower().replace(".", "_")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL is not set; the COMMENT mirror needs a live database")
    return url


# --- TR-073: the transform ---------------------------------------------------


@pytest.mark.parametrize("attribute", sorted(CONVENTION_ATTRIBUTES))
def test_every_convention_attribute_transforms_to_a_recorded_column(
    attribute: str,
) -> None:
    """The forward direction, which is the only one the check may run in."""
    assert transform(attribute) in COLUMNS, (
        f"{attribute!r} transforms to {transform(attribute)!r}, which the record "
        f"does not carry. Either the column is missing or the classification in "
        f"data-model.md is wrong (TR-071)."
    )


def test_no_two_attributes_transform_to_one_column() -> None:
    """TR-073's injectivity check.

    The transform is not invertible, so a collision would make a match
    *ambiguous* rather than wrong — and an ambiguous match is worse, because it
    passes. A pin bump introducing two attributes that lowercase-and-underscore
    to one name must surface as a build failure here.
    """
    everything = CONVENTION_ATTRIBUTES | set(GENERAL_REGISTRY_ATTRIBUTES)
    transformed: dict[str, list[str]] = {}
    for attribute in everything:
        transformed.setdefault(transform(attribute), []).append(attribute)

    collisions = {column: names for column, names in transformed.items() if len(names) > 1}
    assert not collisions, (
        f"these attributes transform to the same column name: {collisions}. The "
        f"transform is forward-only and not invertible, so a collision is an "
        f"ambiguous match rather than a detectable one (TR-073)."
    )


def test_the_collision_check_can_actually_fail() -> None:
    """A check that cannot fail proves nothing — and this one guards a case that
    does not exist on a correct tree, so it would otherwise pass forever."""
    planted = {"gen_ai.usage.input_tokens", "gen_ai.usage_input.tokens"}
    assert len({transform(a) for a in planted}) == 1, (
        "the planted collision no longer collides; the transform has changed"
    )


def test_the_transform_is_never_applied_in_reverse() -> None:
    """Stated as a property of this module rather than trusted to discipline.

    An inverse transform — underscores back into dots — is the
    reasonable-looking mistake TR-073 exists to forbid, so the module is checked
    for one.

    Parsed rather than grepped, and the first version of this test is why: a
    text scan matched the sentence in this very docstring describing what to
    look for, so the check fired on the prose explaining it. The same trap
    `test_migrations.py` hit. An AST walk sees calls and not sentences.
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    inversions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and len(node.args) == 2
        and all(isinstance(arg, ast.Constant) for arg in node.args)
        and [getattr(arg, "value", None) for arg in node.args] == ["_", "."]
    ]
    assert not inversions, (
        "this module reconstructs an attribute from a column name. The transform "
        "is not invertible — attributes carry underscores inside their own "
        "segments — so the recovered attribute may not be the one that existed."
    )


def test_the_inverse_scan_would_catch_a_real_inversion() -> None:
    """The control for the check above, which passes on a correct tree and so
    would pass just as well if the AST walk matched nothing at all."""
    import ast

    tree = ast.parse('column.replace("_", ".")')
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and [getattr(arg, "value", None) for arg in node.args] == ["_", "."]
    ]
    assert found, "the inverse-transform detector matches nothing, including an inversion"


# --- TR-071: the classification is honest -----------------------------------


def test_every_gen_ai_column_comes_from_a_convention_attribute() -> None:
    """The prefix is a signal, not decoration (TR-071).

    A `gen_ai_`-prefixed column that no pinned attribute produces would make the
    prefix mean "looks like a convention name" rather than "is one" — and E013
    reads the prefix to decide which names are stable.
    """
    derived = {transform(attribute) for attribute in CONVENTION_ATTRIBUTES}
    prefixed = {column for column in COLUMNS if column.startswith("gen_ai_")}
    assert prefixed == derived, (
        f"prefixed but not derived from a pinned attribute: {sorted(prefixed - derived)}; "
        f"derived but not present as a column: {sorted(derived - prefixed)}"
    )


def test_no_gateway_local_column_wears_the_convention_prefix() -> None:
    """The same rule from the other side, and the one that makes the prefix
    usable as a set membership test rather than a hint."""
    convention_columns = {transform(a) for a in CONVENTION_ATTRIBUTES}
    local = [
        column
        for column in COLUMNS
        if column.startswith("gen_ai_") and column not in convention_columns
    ]
    assert not local, f"gateway-local columns carrying the gen_ai_ prefix: {local}"


def test_the_general_registry_column_carries_no_gen_ai_prefix() -> None:
    """`error_type` is convention-named but from a *different* registry with its
    own stability guarantee, so it must not be mistaken for part of the gen-AI
    set a pin bump can rename (TR-071, TR-072)."""
    for attribute, column in GENERAL_REGISTRY_ATTRIBUTES.items():
        assert column in COLUMNS
        assert not column.startswith("gen_ai_"), (
            f"{attribute!r} is sourced from the general registry, so its column "
            f"must not carry the gen-AI prefix"
        )


def test_the_development_class_is_exactly_the_pin_sensitive_columns() -> None:
    """TR-072. The class is what tells E013 which names it must not hard-code,
    so it has to be derivable rather than remembered."""
    development = {c for c in COLUMNS if c.startswith("gen_ai_")} | DEVELOPMENT_CLASS_EXTRA
    stable = set(COLUMNS) - development

    assert "error_type" in stable, (
        "error_type's upstream source carries its own stability guarantee, so it "
        "belongs to the stable class despite being convention-named (TR-072)"
    )
    assert "trace_id" in stable, "trace_id is W3C Trace Context Level 1's, not the convention's"
    assert "cache_write_input_tokens" in stable
    assert "cache_read_input_tokens" in development, (
        "cache_read_input_tokens is pin-sensitive in the other direction: it moves "
        "into the convention-named set if a pinned release defines a "
        "cached-input-tokens attribute"
    )


# --- TR-070: the three recording sites agree --------------------------------


def test_the_pin_is_recorded_in_the_requirement() -> None:
    assert SPEC_PATH.is_file(), f"{SPEC_PATH} is missing"
    spec = SPEC_PATH.read_text(encoding="utf-8")
    tr070 = next(line for line in spec.splitlines() if line.startswith("- **TR-070**"))
    assert f"**{OTEL_GENAI_SEMCONV_VERSION}**" in tr070, (
        f"TR-070 does not record {OTEL_GENAI_SEMCONV_VERSION!r} as the pinned "
        f"version; the configuration and the requirement disagree (TR-070)"
    )


def test_the_pin_is_recorded_in_the_migration_that_mirrors_it() -> None:
    """Read from the migration source rather than only from the live comment, so
    the agreement is checkable with no database — the site that most often
    drifts is the one nobody can see without connecting."""
    assert MIGRATION_PATH.is_file(), f"{MIGRATION_PATH} is missing"
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    assert f'"{OTEL_GENAI_SEMCONV_VERSION}"' in migration, (
        f"migration 0102 does not carry {OTEL_GENAI_SEMCONV_VERSION!r}"
    )


def test_the_pin_in_the_live_table_comment_agrees() -> None:
    """OBJ3 VC7's substance: the mirror exists so a database inspected *without*
    this repository still states which convention release its column names
    follow. Checked against the live comment, because a comment that never made
    it into the database describes nothing."""
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            "SELECT obj_description('llm_invocation'::regclass, 'pg_class')"
        ).fetchone()

    assert row is not None and row[0], (
        "llm_invocation carries no table comment, so a database inspected "
        "without this repository cannot say which convention its columns follow"
    )
    comment = row[0]
    versions = set(re.findall(r"\b\d+\.\d+\.\d+\b", comment))
    assert versions == {OTEL_GENAI_SEMCONV_VERSION}, (
        f"the table comment records {versions or 'no version'} while the "
        f"configuration records {OTEL_GENAI_SEMCONV_VERSION!r} (TR-070)"
    )
