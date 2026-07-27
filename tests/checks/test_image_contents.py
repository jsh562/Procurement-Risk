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


@pytest.mark.parametrize("module", ["fastapi", "gateway"])
def test_positive_control_required_modules_do_import(module: str) -> None:
    """Without this, every negative above is satisfied by a broken container.

    `anthropic` was removed from this list on 2026-07-26, and the reason
    matters because the change looks like a weakening and is not. This control
    exists to prove the import probe *works* — it is a guard against a vacuous
    pass, not a statement that the serving image must carry any particular
    package. `anthropic` qualified only because it was a guaranteed transitive
    of the gateway, and {SAD:ADR-0014} deliberately ended that: the provider
    SDK is now an optional extra, so a consumer carries it when it declares
    `gateway[provider]`. `/src/api` does not, because it makes no model call
    until E011.

    Two modules serve the purpose exactly as well as three. The alternative —
    declaring the extra on the serving boundary so this line stays green —
    would add a real dependency, and SDK weight to the request-serving image,
    to satisfy a test mechanism. That is the tail wagging the dog, and it would
    also erode the compute envelope {SAD:ADR-0006} spends real effort to hold.
    """
    assert import_succeeds(module), f"{module!r} failed to import; the check itself is broken"


def test_a_relocked_modeling_dependency_passes_the_allowlist_by_design(tmp_path) -> None:
    """OBJ4 VC4 — the blind spot, asserted rather than described.

    If a modeling dependency is added to the serving manifest *and* the lock is
    regenerated, installed and expected agree and the allowlist passes. That is
    not a defect in the allowlist; it is the reason the in-image denylist and
    the scoped build context both exist. Stating it in prose is not the same as
    proving it, and a reader could reasonably assume the allowlist covers this.
    """
    import tomllib

    from tests.checks.helpers.image_contents import API_LOCK

    lock = tomllib.loads(API_LOCK.read_text(encoding="utf-8"))
    names = {p["name"] for p in lock["package"]}
    assert "pymc" not in names, "fixture stale: the serving lock already carries pymc"

    # Re-lock by hand: declare pymc on the serving root and add its entry.
    for package in lock["package"]:
        if package["name"] == "api":
            package.setdefault("dependencies", []).append({"name": "pymc"})
    lock["package"].append({"name": "pymc", "version": "6.2.0"})

    doctored = tmp_path / "uv.lock"
    doctored.write_text(_dump_minimal_lock(lock), encoding="utf-8")

    expected = expected_distributions(lock_path=doctored, root="api")
    assert "pymc" in expected, (
        "a re-locked modeling dependency must appear in the expected set — "
        "which is exactly why the allowlist cannot catch this case"
    )


def _dump_minimal_lock(lock: dict) -> str:
    """Serialize just enough of a uv.lock for the walker to read it back."""
    lines = []
    for package in lock["package"]:
        lines.append("[[package]]")
        lines.append(f'name = "{package["name"]}"')
        lines.append(f'version = "{package.get("version", "0.0.0")}"')
        for dependency in package.get("dependencies", []):
            lines.append("[[package.dependencies]]")
            lines.append(f'name = "{dependency["name"]}"')
            if dependency.get("marker"):
                lines.append(f'marker = "{dependency["marker"]}"')
        lines.append("")
    return "\n".join(lines)
