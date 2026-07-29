"""Where this entry's tests actually write.

project-instructions.md § Temporary Files, at v1.2.8. That rule has been
revised four times, and every revision was the same shape: a claim about "every
writer" that held only for the writers someone had actually measured. v1.2.5 was
declared proven against `tempfile` and pytest and was false for two libraries;
v1.2.6 closed those and was false for the tool harness; v1.2.7 closed that; and
v1.2.8 withdrew half of v1.2.6 as wrong on the merits — a compiled-artifact
cache is not scratch, and requiring an absolute path is what created a flattened
directory inside the source tree that reached `main`.

So this file measures rather than declares. `--basetemp` is a relative,
forward-slashed path in a tracked manifest; these tests resolve it at runtime and
fail if it lands anywhere but `.tmp/`. A fifth revision of the prose would not
have caught what a single resolved path does.

This entry only, but no longer because the others are unpinned: the root,
`/src/model` and `/src/gateway` each pin their own suffixed `--basetemp` too, and
the suffix matters — pytest clears its basetemp at the start of every run, so two
tiers sharing one directory would wipe each other's `tmp_path` if they ever ran
concurrently. What is asserted here is where *this* entry actually lands, which
is the only thing a test in this entry can honestly measure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

#: `parents[2]` is the repository root — this file sits at `src/api/tests/`.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _inside_checkout(path: Path) -> bool:
    return REPO_ROOT in path.resolve().parents or path.resolve() == REPO_ROOT


def test_pytest_writes_its_scratch_inside_the_checkout(tmp_path: Path) -> None:
    """`--basetemp` resolves under the repository, not the system temp directory.

    `tmp_path` is derived from `basetemp`, so this measures the setting rather
    than reading it back from configuration — a value that parsed but resolved
    somewhere unintended would pass a config check and fail this one, which is
    the failure mode v1.2.6 records.
    """
    assert _inside_checkout(tmp_path), (
        f"pytest wrote scratch to {tmp_path}, which is outside {REPO_ROOT}. "
        "project-instructions.md requires it under the checkout's own .tmp/ — several "
        "checkouts share this disk and the system temp directory is shared with everything "
        "else on the machine."
    )


def test_the_scratch_root_is_the_gitignored_tmp_directory(tmp_path: Path) -> None:
    """Not merely inside the checkout — inside `.tmp/`, which is gitignored.

    Scratch landing anywhere else under the repository would be untracked-but-
    visible clutter at best, and committed at worst: v1.2.6 records exactly that
    happening when a malformed redirect created `pytensor/` inside the source
    tree and it reached `main`.
    """
    relative = tmp_path.resolve().relative_to(REPO_ROOT)
    assert relative.parts[0] == ".tmp", (
        f"pytest scratch resolved to {relative}, outside the gitignored .tmp/ root"
    )


def test_the_process_temp_directory_is_redirected_when_the_caller_sets_it() -> None:
    """`TMPDIR`/`TEMP`/`TMP` are the caller's half of the rule, not the
    manifest's — a `pyproject.toml` cannot set them.

    Recorded as a soft check rather than a hard one for that reason: it states
    where this process is writing so a run that forgot the redirect says so in
    its output, and it does not fail a developer who ran pytest directly.
    """
    resolved = Path(tempfile.gettempdir()).resolve()
    if not _inside_checkout(resolved):
        print(
            f"\nNOTE: tempfile.gettempdir() is {resolved}, outside {REPO_ROOT}. "
            "Set TMPDIR/TEMP/TMP to $PWD/.tmp — see project-instructions.md "
            "§ Temporary Files. `--basetemp` is pinned, so pytest's own scratch is "
            "unaffected; this covers anything calling tempfile directly."
        )
