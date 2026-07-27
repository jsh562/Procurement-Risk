"""The image tag is per-checkout, and a foreign build is detected.

`test_ports.py` asserts the same property for host ports. The two differ in
kind and the tests say so: a port collision is detected and worked around,
because the OS admits one listener and nothing we name can change that; a tag
collision is *removed*, because it existed only because we chose a name that
did not say which checkout owned it.

What must not regress: two checkouts of this repository sitting on one machine
must not resolve to one tag, and an image built by somebody else must not be
silently asserted against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.checks.helpers.images import (
    CHECKOUT_LABEL,
    IMAGE_REPOSITORY,
    IMAGE_VARIANT,
    REPO_ROOT,
    TAG_OVERRIDE,
    ForeignBuild,
    checkout_slug,
    default_image_tag,
    foreign_build,
    resolve_image_tag,
)


def test_two_checkouts_on_one_machine_resolve_to_different_tags() -> None:
    """The defect this exists to prevent, stated directly.

    Four sibling checkouts named `...Risk`, `...Risk1`, `...Risk2`, `...Risk3`
    is the real arrangement that produced it.
    """
    tags = {
        default_image_tag(Path(f"S:/claudecode/KayaDemoProcurementRisk{suffix}"))
        for suffix in ("", "1", "2", "3")
    }
    assert len(tags) == 4, f"checkouts collapsed onto {sorted(tags)}"


def test_the_documented_repository_and_variant_survive_in_the_tag() -> None:
    """The suffix is added, not substituted.

    E001 documents a `procurement-api` image at variant `e001`; a resolver that
    renamed it would make the prose wrong to fix the collision.
    """
    tag = default_image_tag(Path("/tmp/anything"))
    assert tag.startswith(f"{IMAGE_REPOSITORY}:{IMAGE_VARIANT}-")


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("KayaDemoProcurementRisk3", "kayademoprocurementrisk3"),
        ("Procurement Risk Demo", "procurement-risk-demo"),
        ("repo.with.dots", "repo-with-dots"),
        ("__weird__", "weird"),
    ],
)
def test_a_checkout_slug_is_tag_safe(directory: str, expected: str) -> None:
    """Docker admits `[A-Za-z0-9_.-]` in a tag; everything else folds."""
    assert checkout_slug(Path("/somewhere") / directory) == expected


def test_a_directory_with_no_usable_characters_still_yields_a_tag() -> None:
    """Refusing to resolve would be worse than a dull name."""
    assert checkout_slug(Path("/somewhere/---")) == "checkout"


def test_an_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned name stays possible — a release build, or reproducing a
    recorded run — without having to rename a directory to defeat the slug."""
    monkeypatch.setenv(TAG_OVERRIDE, "procurement-api:pinned")
    assert resolve_image_tag() == "procurement-api:pinned"


def test_a_blank_override_is_not_an_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty variable is an unset one, not a request for an empty tag."""
    monkeypatch.setenv(TAG_OVERRIDE, "   ")
    assert resolve_image_tag() == default_image_tag()


def test_an_image_built_from_another_checkout_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failing direction: a stamp naming somebody else's path."""
    monkeypatch.setattr(
        "tests.checks.helpers.images._label", lambda tag: "S:/claudecode/KayaDemoProcurementRisk2"
    )
    finding = foreign_build("procurement-api:e001-x")
    assert isinstance(finding, ForeignBuild)
    assert "KayaDemoProcurementRisk2" in str(finding)


def test_an_unstamped_image_is_reported_rather_than_assumed_benign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstamped is what the tooling produced before this check existed, so
    treating it as ours would exempt exactly the images at risk."""
    monkeypatch.setattr("tests.checks.helpers.images._label", lambda tag: None)
    monkeypatch.setattr("tests.checks.helpers.images._image_exists", lambda tag: True)
    finding = foreign_build("procurement-api:e001-x")
    assert isinstance(finding, ForeignBuild)
    assert "unstamped" in str(finding)


def test_no_image_at_all_is_not_a_foreign_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Not built yet" is the caller's problem to report. Conflating it with
    "built by someone else" would make the message wrong in the common case."""
    monkeypatch.setattr("tests.checks.helpers.images._label", lambda tag: None)
    monkeypatch.setattr("tests.checks.helpers.images._image_exists", lambda tag: False)
    assert foreign_build("procurement-api:e001-x") is None


def test_our_own_stamp_is_not_foreign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tests.checks.helpers.images._label", lambda tag: str(REPO_ROOT))
    assert foreign_build("procurement-api:e001-x") is None


def test_the_workflow_builds_the_tag_it_asserts_and_stamps_the_label() -> None:
    """One source of truth, asserted rather than trusted.

    A literal in the workflow beside a literal in the helpers is the defect
    that let the reproducibility fixture fall behind the generation-input
    tuple. This asserts the workflow resolves the tag instead of writing it.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    build = workflow[workflow.index("Build serving image") :]
    build = build[: build.index("- name:", 1)]
    assert "resolve_image_tag" in build, (
        "the build step writes a tag literal instead of resolving it"
    )
    assert f'--label "{CHECKOUT_LABEL}=' in build, (
        "the build step does not stamp the checkout label"
    )
    assert f"-t procurement-api:{IMAGE_VARIANT} " not in build, "a bare shared tag is still built"
