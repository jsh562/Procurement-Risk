"""FR-034 / FR-034a / SC-022 and FR-017 / SC-008: the verification workflow's
declared trigger set and its corpus-validation step.

SC-022's evidence is split three ways, because no committed artifact can assert
that a run occurred. This file is the half that needs no run: it parses
`verify.yml` and asserts three properties of the declared surface — that
`pull_request` is declared against the default branch, that
`pull_request_target` is *not* declared, and that the workflow token is scoped
to `contents: read`.

SC-008 is split the same way and for the same reason, so its checkable half
lives here beside them: the workflow declares a step invoking `corpus-validate`,
that step is unconditional, and it carries no `--layer` scope. Without those
three, "corpus validation runs in 100% of workflow runs" rested on the step
being present today and on nobody deleting or narrowing it.

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
    steps_running,
    triggers,
    unconditional,
)

# The base ref FR-034 names. `main` is this repository's default branch; feature
# branches are cut from it and squash-merged back per the development workflow.
DEFAULT_BRANCH = "main"

# The console entry point FR-017 puts in the workflow. Matched as a `run:`
# fragment rather than by step name, because a step's `name:` is decoration and
# renaming it must not move this assertion.
VALIDATE_STEP = "corpus-validate"

# The flag whose *return* is the regression SC-008 needs guarded, not merely the
# step's deletion. See `test_the_corpus_validation_step_is_unscoped`.
LAYER_FLAG = "--layer"


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


# --- FR-017 / SC-008: the corpus-validation step -------------------------------
# Read through one predicate each, so the controls below can plant a workflow
# and put it through the same code the real assertions use rather than through a
# re-spelling of it that could agree with a broken original.


def validating_steps(document: dict[Any, Any]) -> list[dict[str, Any]]:
    """Every step whose `run:` invokes the `corpus-validate` entry point."""
    return steps_running(document, VALIDATE_STEP)


def layer_scoped(step: dict[str, Any]) -> bool:
    """Whether a step narrows `corpus-validate` to one layer."""
    return LAYER_FLAG in step["run"]


def test_the_workflow_runs_corpus_validation(workflow: dict[Any, Any]) -> None:
    """SC-008's checkable half, and the one property nothing else asserted.

    FR-017 requires corpus validation to run automatically rather than on
    demand. The step exists, but until this assertion its deletion was caught by
    nothing: every rule `corpus-validate` owns — VR-001…VR-033, VR-035…VR-039,
    VR-051…VR-068 — would stop being evaluated in CI while the entry point
    stayed installed, importable, and green for anyone who ran it by hand.
    """
    running = validating_steps(workflow)
    assert running, (
        f"no verify.yml step runs {VALIDATE_STEP!r}; FR-017 requires corpus validation to "
        "run in the workflow, and without a step the 59 rules it owns are evaluated by "
        "nothing on any push or pull request"
    )


def test_the_corpus_validation_step_is_unconditional(workflow: dict[Any, Any]) -> None:
    """SC-008 says *100% of* workflow runs, so a conditional step does not
    satisfy it.

    The dispatch-only injection step in this same file shows how readily a step
    acquires an `if:` on `github.event_name`. One placed here would leave corpus
    validation visibly present in the workflow while removing it from every push
    and pull-request run — the failure mode a "the step is there" check misses.
    """
    running = validating_steps(workflow)
    assert any(unconditional(step) for step in running), (
        f"every step running {VALIDATE_STEP!r} is gated by an `if:`, so corpus validation "
        f"does not run on every triggering event: {[step.get('if') for step in running]}"
    )


def test_the_corpus_validation_step_is_unscoped(workflow: dict[Any, Any]) -> None:
    """No `--layer`. The flag's *return* is the regression, not its presence.

    `--layer REAL` was correct while the synthetic layer did not exist: VR-005
    and VR-066 are built to fail on a corpus missing it, which is their
    entry-criterion job rather than a defect to suppress. The layer landed with
    T056 and the flag came off in the same change. Re-adding it would leave a
    green required check that is blind to all 25 synthetic documents and to the
    seventeen rules asserted only over them — a narrowing that reads as a
    tidy-up in a diff and reports as a pass in CI.
    """
    scoped = [step for step in validating_steps(workflow) if layer_scoped(step)]
    assert not scoped, (
        f"a verify.yml step scopes {VALIDATE_STEP!r} with {LAYER_FLAG}, which makes the "
        "required check blind to the layer it excludes: "
        f"{[step['run'].strip() for step in scoped]}"
    )


# --- failing-direction controls ------------------------------------------------
# File assertions that only ever ran against a compliant file would be
# indistinguishable from ones that cannot fail. Each control plants the
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


# A planted workflow shaped like the real one, so each control differs from the
# compliant case in exactly the property under test. `PLANTED_COMPLIANT` is
# carried too: without it the three controls below would also pass against a
# predicate that rejected everything.
def _planted(step: str) -> dict[Any, Any]:
    return yaml.safe_load(
        "on:\n  push: null\njobs:\n  verify:\n    steps:\n"
        "      - run: uv run --directory src/model ruff check .\n" + step
    )


PLANTED_COMPLIANT = "      - run: uv run --directory src/model corpus-validate\n"


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ("", "a workflow with no corpus-validation step at all"),
        (
            "      - run: uv run --directory src/model corpus-generate\n",
            "a workflow running the generator instead of the validator",
        ),
    ],
)
def test_the_validation_step_assertion_rejects_a_planted_workflow(step: str, reason: str) -> None:
    assert not validating_steps(_planted(step)), f"the step assertion accepted {reason}"


def test_the_unconditional_assertion_rejects_a_planted_conditional_step() -> None:
    """The deletion control above cannot catch this one: the step is present,
    named, and runs the right command — it just never runs on a push."""
    gated = _planted(
        "      - if: ${{ github.event_name == 'workflow_dispatch' }}\n"
        "        run: uv run --directory src/model corpus-validate\n"
    )
    running = validating_steps(gated)
    assert running, "the planted workflow declares no corpus-validation step to gate"
    assert not any(unconditional(step) for step in running), (
        "the unconditional assertion accepted a dispatch-gated corpus-validation step"
    )


@pytest.mark.parametrize(
    "step",
    [
        "      - run: uv run --directory src/model corpus-validate --layer REAL\n",
        "      - run: uv run --directory src/model corpus-validate --layer SYNTHETIC\n",
    ],
)
def test_the_unscoped_assertion_rejects_a_planted_layer_flag(step: str) -> None:
    scoped = [s for s in validating_steps(_planted(step)) if layer_scoped(s)]
    assert scoped, f"the unscoped assertion accepted {step.strip()!r}"


def test_the_three_step_assertions_accept_the_compliant_planted_workflow() -> None:
    """The non-vacuity control for the three above. Each of them passes by
    finding *nothing*, so all three would pass against a predicate that matched
    nothing at all; this is the case that fails if one does."""
    running = validating_steps(_planted(PLANTED_COMPLIANT))
    assert len(running) == 1, running
    assert unconditional(running[0])
    assert not layer_scoped(running[0])
