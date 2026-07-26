"""TR-012 / TR-013 / SC-008: the serving image is accounted for by its lock."""

from __future__ import annotations

import pytest

from tests.checks.helpers.image_contents import (
    expected_distributions,
    import_succeeds,
    installed_distributions,
    modeling_module_names,
    normalize,
)


@pytest.fixture(scope="module")
def installed() -> set[str]:
    return installed_distributions()


def test_the_derived_expectation_is_not_empty() -> None:
    """A check derived from an empty set passes vacuously and proves nothing."""
    assert expected_distributions(), "lock-derived expectation is empty; the walk is broken"


def test_installed_set_equals_the_lock_derived_set(installed: set[str]) -> None:
    """TR-012 / SC-007 assert equality, so assert equality — not containment.

    Only `api` is exempt: it is the serving boundary's own distribution, the
    root the closure is walked from, so it is installed but never appears as
    its own dependency. `pip` and `setuptools` were exempted here previously
    and are not installed at all; a dead exemption inside an allowlist is a
    standing permission for exactly the thing the allowlist exists to refuse.
    """
    expected = expected_distributions() | {"api"}
    unaccounted = installed - expected
    missing = expected - installed
    assert not unaccounted, f"installed but absent from the lockfile: {sorted(unaccounted)}"
    assert not missing, f"lockfile expects distributions the image lacks: {sorted(missing)}"


def test_no_modeling_distribution_reached_the_image(installed: set[str]) -> None:
    # Derived, not hand-listed — a literal set here would silently stop
    # covering the modeling boundary the moment it declares something new.
    intrusion = installed & {normalize(m) for m in modeling_module_names()}
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
