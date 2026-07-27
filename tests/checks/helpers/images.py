"""Which image tag this checkout builds and asserts against.

The serving image was tagged `procurement-api:e001` by a literal written in
three places — the workflow's build step and two check helpers. A Docker tag is
a name in a daemon-wide namespace, and several checkouts of this repository sit
on one machine, so the second checkout to build silently re-points the tag and
the first one's checks then assert against an image built from source it has
never seen. That failure is worse than a port collision: a port collision fails
loudly at `up`, while this one **passes**, and the checks report green about the
wrong artifact. It cost two false diagnoses before it was understood.

`ports.py` solves the same class of problem by substituting after detecting a
collision. That shape is wrong here. A port is contended because the OS admits
one listener; a tag is contended only because we chose a name that does not say
which checkout it belongs to. So this resolver removes the collision rather than
reacting to it: **the default tag carries the checkout's own slug**, and two
checkouts can no longer name one image.

Detection is still needed, for the case the naming cannot cover — an image built
before this change, or by a tool that does not use the default. Every build this
repository performs stamps `com.procurement.checkout` with the absolute path it
built from, and `foreign_build` reports an image whose stamp is somebody else's.
A missing stamp is reported too rather than assumed benign: an unstamped image
is exactly what the old tooling produced.

**One source of truth.** `resolve_image_tag` is called by the workflow's build
step and by every check that inspects the image, so the tag built and the tag
asserted cannot drift. The alternative — a literal in the workflow and a
matching literal in the helpers — is the same defect that let the reproducibility
fixture fall behind the generation-input tuple: a second restated copy that the
first one does not reach.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CHECKOUT_LABEL",
    "IMAGE_REPOSITORY",
    "IMAGE_VARIANT",
    "REPO_ROOT",
    "TAG_OVERRIDE",
    "ForeignBuild",
    "checkout_slug",
    "default_image_tag",
    "foreign_build",
    "resolve_image_tag",
]

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The repository and variant the epic documents. Unchanged — what moves is the
#: per-checkout suffix below, so `procurement-api` and `e001` still name the
#: same thing in prose and in the tag.
IMAGE_REPOSITORY = "procurement-api"
IMAGE_VARIANT = "e001"

#: Set to pin an exact tag. Provided so a caller that genuinely wants one shared
#: name — a release build, or a reader reproducing a recorded run — can ask for
#: it, rather than having to defeat the slug by renaming their directory.
TAG_OVERRIDE = "PRC_IMAGE_TAG"

#: Stamped at build time with the absolute path the build ran from. An image
#: carrying somebody else's path is somebody else's image.
CHECKOUT_LABEL = "com.procurement.checkout"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ForeignBuild:
    """An image under our tag that this checkout did not build."""

    tag: str
    built_from: str | None

    def __str__(self) -> str:
        origin = self.built_from or "an unstamped build predating this check"
        return f"{self.tag} was built from {origin}, not {REPO_ROOT}"


def checkout_slug(root: Path | None = None) -> str:
    """A tag-safe token identifying this checkout.

    The directory name rather than a hash of the path: a developer reading
    `docker images` should be able to tell which of their four checkouts owns a
    line without resolving a digest. Docker tags admit only
    `[A-Za-z0-9_.-]`, so everything else folds to a hyphen.
    """
    name = (root or REPO_ROOT).name.lower()
    slug = _SLUG_STRIP.sub("-", name).strip("-")
    return slug or "checkout"


def default_image_tag(root: Path | None = None) -> str:
    """`procurement-api:e001-<checkout>` — unique per checkout by construction."""
    return f"{IMAGE_REPOSITORY}:{IMAGE_VARIANT}-{checkout_slug(root)}"


def resolve_image_tag(root: Path | None = None) -> str:
    """The tag this checkout builds and asserts against.

    Honours `PRC_IMAGE_TAG` so a pinned name stays possible, and falls back to
    the per-checkout default. Called by the build and by every consumer, so the
    two cannot disagree.
    """
    override = os.environ.get(TAG_OVERRIDE, "").strip()
    return override or default_image_tag(root)


def _label(tag: str) -> str | None:
    """`com.procurement.checkout` on `tag`, or None if absent or unreadable.

    A missing daemon, a missing image and a missing label are all None here.
    The caller distinguishes them by asking whether the image exists at all;
    this function's only job is to report the stamp.
    """
    fmt = f'{{{{index .Config.Labels "{CHECKOUT_LABEL}"}}}}'
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "image", "inspect", "--format", fmt, tag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    # Docker prints `<no value>` for a label the image does not carry.
    if not value or value == "<no value>":
        return None
    return value


def foreign_build(tag: str | None = None, root: Path | None = None) -> ForeignBuild | None:
    """Report `tag` if it holds an image this checkout did not build.

    Returns None when the image is ours, and also when no image exists at all —
    "not built yet" is the caller's problem to report, and conflating it with
    "built by someone else" would make the message wrong in the common case.
    """
    resolved = tag or resolve_image_tag(root)
    stamp = _label(resolved)
    if stamp is None:
        # No image, no daemon, or an unstamped image. Only the last is a
        # finding, and it is indistinguishable from the first two without a
        # second call — so ask.
        if not _image_exists(resolved):
            return None
        return ForeignBuild(resolved, None)
    if Path(stamp) == (root or REPO_ROOT):
        return None
    return ForeignBuild(resolved, stamp)


def _image_exists(tag: str) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
