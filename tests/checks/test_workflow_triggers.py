"""FR-034 / FR-034a / SC-022: the verification workflow's declared trigger set.

SC-022's evidence is split three ways, because no committed artifact can assert
that a run occurred. This file is the half that needs no run: it parses
`verify.yml` and asserts three properties of the declared surface — that
`pull_request` is declared against the default branch, that
`pull_request_target` is *not* declared, and that the workflow token is scoped
to `contents: read`.

Lives at the repository root rather than inside an entry because the artifact
under assertion is a repository workflow, which no `/src` entry owns — the same
exception `test_orchestration.py`, `test_layout.py`, and
`test_network_free_required_checks.py` sit under.

**The assertions are over the parsed trigger mapping, never over the file's
text.** `verify.yml` names `pull_request_target` in a comment explaining why it
is not used, so a textual scan would report a violation against the very
sentence that documents the decision — and, worse, would pass on a file that
declared the trigger under a spelling the scan did not anticipate. Parsing is
both the stricter and the honest reading.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from tests.checks.helpers.workflow import (
    VERIFY_WORKFLOW,
    admits_branch,
    load_workflow,
    triggers,
)

# The base ref FR-034 names. `main` is this repository's default branch; feature
# branches are cut from it and squash-merged back per the development workflow.
DEFAULT_BRANCH = "main"


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    assert VERIFY_WORKFLOW.is_file(), f"{VERIFY_WORKFLOW} is missing"
    return load_workflow()


# --- FR-034: the trigger is declared, against the default branch --------------


def test_the_workflow_triggers_on_pull_request(workflow: dict[Any, Any]) -> None:
    """FR-034's checkable half. Without this trigger the merge gate has no
    authoritative run: a push run covers a commit, not the merge."""
    declared = triggers(workflow)
    assert "pull_request" in declared, (
        "verify.yml declares no `pull_request` trigger; the pull-request run is "
        f"FR-034's authoritative merge gate. Declared: {sorted(map(str, declared))}"
    )


def test_the_pull_request_trigger_covers_the_default_branch(workflow: dict[Any, Any]) -> None:
    """FR-034 states the base ref, so the branch filter is asserted rather than
    left to whatever the file happens to list."""
    trigger = triggers(workflow)["pull_request"]
    assert admits_branch(trigger, DEFAULT_BRANCH), (
        f"the `pull_request` trigger does not admit {DEFAULT_BRANCH!r}: {trigger}"
    )


def test_the_push_and_dispatch_triggers_are_retained(workflow: dict[Any, Any]) -> None:
    """FR-034 says *in addition to*. The E001 triggers are intended
    carry-forward, so losing one while adding `pull_request` is a regression
    this file is placed to catch rather than a tidy-up."""
    declared = triggers(workflow)
    for event in ("push", "workflow_dispatch"):
        assert event in declared, f"the `{event}` trigger was dropped: {sorted(map(str, declared))}"


# --- FR-034a(b): the surface that must NOT be declared -------------------------


def test_the_workflow_does_not_trigger_on_pull_request_target(workflow: dict[Any, Any]) -> None:
    """FR-034a(b). Not a stylistic preference between two spellings.

    `pull_request_target` runs the *base* ref's workflow definition with the
    base repository's token and secret access while checking out head content.
    That is a different execution surface from the one FR-034a states, and it is
    the surface on which an unreviewed head can reach something worth taking.
    """
    declared = triggers(workflow)
    assert "pull_request_target" not in declared, (
        "verify.yml declares `pull_request_target`, which FR-034a(b) forbids: it "
        "grants the base repository's token and secrets to a run that checks out "
        "unreviewed head content"
    )


# --- FR-034a(c): the permission surface ---------------------------------------


def test_the_workflow_token_is_scoped_to_contents_read(workflow: dict[Any, Any]) -> None:
    """FR-034a(c). This is what bounds the residual exposure FR-034a(d) records:
    a hostile head can alter what the run installs and then judges, and the
    consequence stops at a wrong verdict on its own pull request precisely
    because the run holds no write scope and no secret."""
    assert workflow.get("permissions") == {"contents": "read"}, (
        f"workflow permissions are {workflow.get('permissions')!r}, not {{'contents': 'read'}}"
    )


# --- failing-direction controls ------------------------------------------------
# Three file assertions that only ever ran against a compliant file would be
# indistinguishable from three that cannot fail. Each control plants the
# violation its assertion exists to catch.


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("on:\n  push: null\n", "a workflow with no pull_request trigger"),
        (
            "on:\n  pull_request:\n    branches: [release]\n",
            "a trigger scoped off the default branch",
        ),
        (
            "on:\n  pull_request:\n    branches: [main]\n    branches-ignore: [main]\n",
            "a trigger that excludes the default branch it lists",
        ),
    ],
)
def test_the_pull_request_assertions_reject_a_planted_workflow(source: str, reason: str) -> None:
    declared = triggers(yaml.safe_load(source))
    admitted = "pull_request" in declared and admits_branch(
        declared["pull_request"], DEFAULT_BRANCH
    )
    assert not admitted, f"the trigger assertions accepted {reason}"


def test_the_target_assertion_rejects_a_planted_pull_request_target() -> None:
    declared = triggers(yaml.safe_load("on:\n  pull_request_target:\n    branches: [main]\n"))
    assert "pull_request_target" in declared, "the forbidden-trigger assertion cannot fail"


@pytest.mark.parametrize(
    "source",
    [
        "on:\n  push: null\npermissions:\n  contents: write\n",
        "on:\n  push: null\npermissions:\n  contents: read\n  id-token: write\n",
        "on:\n  push: null\n",
    ],
)
def test_the_permission_assertion_rejects_a_widened_scope(source: str) -> None:
    """A widened scope, an extra scope, and an absent block all fail. The third
    matters: an omitted `permissions` block inherits the repository default,
    which is not necessarily read-only."""
    document = yaml.safe_load(source)
    assert document.get("permissions") != {"contents": "read"}
