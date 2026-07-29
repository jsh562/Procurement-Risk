"""Reading the verification workflow's declared trigger set and steps.

Shared by the trigger check (FR-034 / FR-034a) and the contract-fixture check
(FR-035), which both need to know what `verify.yml` triggers on — the first to
assert the trigger, the second to establish that the fixtures it runs execute
inside the pull-request run rather than only under a manual dispatch.

Lives in an importable helper rather than inline in either test body so it lands
in the coverage denominator, on the same reasoning as `entries.py`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "verify.yml"


def load_workflow(path: Path = VERIFY_WORKFLOW) -> dict[Any, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{path} did not parse to a mapping")
    return document


def triggers(document: dict[Any, Any]) -> dict[str, Any]:
    """The `on:` mapping, under whichever key the parser produced.

    PyYAML implements YAML 1.1, where a bare `on` is a boolean, so the key
    arrives as `True` rather than `"on"`. GitHub Actions reads YAML 1.2, where
    it stays a string. Both spellings are accepted so the assertions bind to the
    workflow rather than to the parser's schema version — a lookup of only `"on"`
    raises KeyError today, and a lookup of only `True` would start passing
    vacuously the day the parser is swapped.
    """
    for key in ("on", True):
        value = document.get(key)
        if isinstance(value, dict):
            return value
    raise AssertionError(f"no `on:` mapping; keys: {sorted(map(str, document))}")


def admits_branch(trigger: dict[str, Any] | None, branch: str) -> bool:
    """Whether one trigger's branch filters admit `branch`.

    An event mapped to `None` (`pull_request:` with no body) carries no filter
    and therefore admits every base ref, the default branch included.
    """
    if trigger is None:
        return True
    included = trigger.get("branches")
    excluded = trigger.get("branches-ignore") or []
    if branch in excluded:
        return False
    return branch in included if included else True


def run_steps(document: dict[Any, Any]) -> list[dict[str, Any]]:
    """Every step across every job that carries a `run:` command."""
    jobs = document.get("jobs") or {}
    return [
        step
        for job in jobs.values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and step.get("run")
    ]


def steps_running(document: dict[Any, Any], fragment: str) -> list[dict[str, Any]]:
    """Steps whose `run:` body contains `fragment`."""
    return [step for step in run_steps(document) if fragment in step["run"]]


#: How a step names the tier it runs a tool against. `uv run --directory
#: src/model ruff …` runs from that directory; a bare `uv run ruff …` runs from
#: the checkout root, which is the tier the per-entry loops keep omitting.
ROOT_TIER = "."

_FOR_LOOP = re.compile(r"\bfor\s+(\w+)\s+in\s+([^;]+);\s*do\b")
_DONE = re.compile(r"(^|;|\s)done\b")
_DIRECTORY = re.compile(r"--directory[=\s]+\"?([^\s\"]+)\"?")


def _expand(raw: str, bindings: dict[str, list[str]]) -> set[str]:
    """Every directory a `--directory` argument names under the loops in scope.

    `"src/$entry"` inside `for entry in gateway api model` names three
    directories, and a check reading the literal would see one that does not
    exist.
    """
    expansions = {raw}
    for name, words in bindings.items():
        expansions = {
            candidate.replace(f"${{{name}}}", word).replace(f"${name}", word)
            for candidate in expansions
            for word in words
        }
    return expansions


def tiers_running(document: dict[Any, Any], invocation: str) -> set[str]:
    """Every tier some step runs `invocation` against, across every job.

    Args:
        document: a parsed workflow.
        invocation: the command as it is spelled in the step, e.g. `ruff check`
            or `ruff format`.

    Returns:
        Directory strings relative to the checkout — `src/model` for an entry,
        `ROOT_TIER` for a step with no `--directory`.

    The shell is read rather than executed, and the only construct interpreted
    is the `for … in …; do` these steps are written with. That is enough to
    resolve `"src/$entry"`, and it is deliberately not a shell: a check that
    needed one to establish which tiers a lint step covers would be reporting on
    its own emulator.
    """
    tiers: set[str] = set()
    for step in run_steps(document):
        bindings: dict[str, list[str]] = {}
        for line in str(step["run"]).splitlines():
            opener = _FOR_LOOP.search(line)
            if opener is not None:
                bindings[opener.group(1)] = opener.group(2).split()
            if invocation in line:
                directory = _DIRECTORY.search(line)
                tiers |= (
                    _expand(directory.group(1), bindings) if directory is not None else {ROOT_TIER}
                )
            if _DONE.search(line):
                bindings = {}
    return tiers


def unconditional(step: dict[str, Any]) -> bool:
    """Whether a step runs on every triggering event.

    A step carrying an `if:` may be gated on `github.event_name`, which is
    exactly how the dispatch-only injection step is written. A fixture step so
    gated would not execute in the pull-request run, so the distinction is
    load-bearing for FR-035 rather than cosmetic.
    """
    return "if" not in step
