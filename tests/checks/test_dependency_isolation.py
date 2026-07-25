"""TR-002 / TR-003 / TR-004 / SC-001: the boundaries resolve independently."""

from __future__ import annotations

import pytest

from tests.checks.helpers.entries import (
    PYTHON_ENTRIES,
    declared_third_party,
    first_party_sources,
    locked_distributions,
    manifest,
    normalize,
)


def test_no_modeling_dependency_reaches_the_serving_resolution() -> None:
    """TR-004. Compared against *declared* third-party names.

    Shared transitives are legitimate and are not the failure this detects;
    what would matter is the modeling boundary's own stack arriving in the
    boundary that serves requests.
    """
    leaked = declared_third_party("model") & locked_distributions("api")
    assert not leaked, f"modeling distributions present in the serving resolution: {sorted(leaked)}"


def test_neither_python_boundary_declares_the_other() -> None:
    assert "model" not in {normalize(n) for n in manifest("api")["project"]["dependencies"]}
    assert "api" not in {normalize(n) for n in manifest("model")["project"]["dependencies"]}


@pytest.mark.parametrize("boundary", ["api", "model"])
def test_both_boundaries_declare_the_gateway_as_a_path_dependency(boundary: str) -> None:
    assert "gateway" in first_party_sources(boundary)


def test_gateway_carries_no_modeling_stack() -> None:
    """TR-003. The modeling stack is derived, never hand-listed."""
    intrusion = locked_distributions("gateway") & declared_third_party("model")
    assert not intrusion, f"gateway resolved set carries the modeling stack: {sorted(intrusion)}"


def test_gateway_carries_no_web_framework() -> None:
    """TR-003. Derived from what the serving boundary declares to serve HTTP."""
    web_framework = {"fastapi", "uvicorn"}
    assert web_framework <= declared_third_party("api"), "serving boundary changed; update the term"
    intrusion = locked_distributions("gateway") & web_framework
    assert not intrusion, f"gateway resolved set carries a web framework: {sorted(intrusion)}"


@pytest.mark.parametrize("entry", PYTHON_ENTRIES)
def test_first_party_names_are_excluded_from_every_derived_set(entry: str) -> None:
    """The exclusion that STF-001 and STF-002 were filed about."""
    assert not (declared_third_party(entry) & first_party_sources(entry))
