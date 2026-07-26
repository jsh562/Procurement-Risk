"""TR-007: the two image checks are shown failing, not assumed to work.

Both negatives inject into a container started from the real serving image.
Neither modifies the image, so a passing run of test_image_contents.py before
and after is itself evidence the injection was contained.
"""

from __future__ import annotations

from tests.checks.helpers.image_contents import (
    expected_distributions,
    import_succeeds,
    inject_stub_and_probe,
    installed_distributions,
)

STUB = "pymc"


def test_allowlist_would_reject_an_injected_distribution() -> None:
    probe = inject_stub_and_probe(STUB)
    assert STUB in probe["installed"], "stub did not register as an installed distribution"
    accounted = expected_distributions() | {"api", "gateway", "pip", "setuptools"}
    unaccounted = probe["installed"] - accounted
    assert STUB in unaccounted, "the allowlist check would not have flagged the injected stub"


def test_denylist_would_reject_an_injected_module() -> None:
    probe = inject_stub_and_probe(STUB)
    assert probe["imported"], "stub was not importable, so the denylist negative proves nothing"


def test_the_injection_did_not_persist_into_the_image() -> None:
    """Containers are --rm; if this fails the two negatives above poisoned the
    image and every later run of the positive checks is meaningless."""
    assert STUB not in installed_distributions()
    assert not import_succeeds(STUB)
