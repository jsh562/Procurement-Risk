"""TR-012 / TR-013 / SC-008: the serving image is accounted for by its lock."""

from __future__ import annotations

import pytest

from tests.checks.helpers.image_contents import (
    expected_distributions,
    import_succeeds,
    installed_distributions,
    modeling_module_names,
)


@pytest.fixture(scope="module")
def installed() -> set[str]:
    return installed_distributions()


def test_the_derived_expectation_is_not_empty() -> None:
    """A check derived from an empty set passes vacuously and proves nothing."""
    assert expected_distributions(), "lock-derived expectation is empty; the walk is broken"


def test_nothing_installed_is_unaccounted_for(installed: set[str]) -> None:
    """The load-bearing direction: no distribution the lock does not explain."""
    unaccounted = installed - expected_distributions() - {"api", "gateway", "pip", "setuptools"}
    assert not unaccounted, f"installed but absent from the lockfile: {sorted(unaccounted)}"


def test_no_modeling_distribution_reached_the_image(installed: set[str]) -> None:
    intrusion = installed & {"pymc", "arviz", "pandas", "numpy"}
    assert not intrusion, f"modeling stack reached the serving image: {sorted(intrusion)}"


def test_the_derived_denylist_is_not_empty() -> None:
    """Guards the vacuous pass: a host without the modeling manifest derives
    nothing, attempts no imports, and the denylist reports success."""
    assert modeling_module_names(), "modeling module derivation produced nothing"


@pytest.mark.parametrize("module", sorted(modeling_module_names()))
def test_modeling_modules_do_not_import_in_the_image(module: str) -> None:
    assert not import_succeeds(module), f"{module!r} imported inside the serving image"


@pytest.mark.parametrize("module", ["fastapi", "gateway", "anthropic"])
def test_positive_control_required_modules_do_import(module: str) -> None:
    """Without this, every negative above is satisfied by a broken container."""
    assert import_succeeds(module), f"{module!r} failed to import; the check itself is broken"
