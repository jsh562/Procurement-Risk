"""The content-hash fixture store: labelled, committed, and offline.

TR-022, TR-033, TR-037, TR-056.

**Content-hash, not sequence-numbered.** A fixture is stored under a digest of
the request that produced it, so looking one up is deciding whether *this*
request has been recorded — not whether the third call in some remembered order
happened to be this one. That is what makes replay robust against reordering,
against a test being added in the middle, and against two tests sharing a
request.

**A miss raises, and no network request is issued** (TR-022). Not "falls back to
the provider", which is the tempting behaviour and the one that makes an offline
suite quietly online — a developer with a credential would never notice, and CI
would fail on the one machine that could not cheat. The fallback does not exist
here; there is no code path from this module to a socket.

**Labelled, never anonymous** (TR-033). Each fixture carries a sidecar naming
the recording date, the resolved model, the gateway revision that produced it,
and the reported token counts. Generated data with no provenance is the thing
Principle I refuses, and a fixture store is generated data.

**Replay token counts come from the sidecar** (TR-056), so a replayed cost is
reproducible — it is priced from what the provider actually reported at
recording time, not from a re-estimate. The *latency* is measured on the replay
execution itself, because that is what actually happened; a replay does not
inherit the original call's duration.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from gateway.compute.hashing import FIXTURE_KEY_PATTERN
from gateway.compute.timing import AttemptUsage
from gateway.errors import GatewayError

__all__ = [
    "FixtureMissError",
    "FixtureProvenance",
    "FixtureStore",
    "StoredFixture",
]

#: One transport attempt (TR-056). A fixture lookup counts as one, which is what
#: makes `transport_attempt_count >= 1` hold on `replay` rows without anyone
#: inferring it from the glossary — a replayed row that recorded zero attempts
#: would violate the column's own `CHECK`.
FIXTURE_LOOKUP_ATTEMPTS: Final[int] = 1

_RESPONSE_SUFFIX: Final[str] = ".response.json"
_PROVENANCE_SUFFIX: Final[str] = ".provenance.json"


class FixtureMissError(GatewayError):
    """No fixture matches this request in `replay` mode (TR-022).

    Names the derived key, because that is the only actionable fact: a miss
    means either the request changed or the fixture was never recorded, and the
    key is what a developer greps the store for.

    Its own type so a caller can tell "this needs recording" from "the provider
    refused" — the two look identical if both arrive as a generic failure, and
    only one of them is fixed by running with the opt-in set.
    """

    def __init__(self, key: str, root: Path) -> None:
        super().__init__(
            f"no fixture for {key} under {root}. In replay mode the gateway "
            f"resolves from committed fixtures and issues no network request "
            f"(TR-022) — record this one in `record` mode, or check whether the "
            f"request changed: the key covers every declared request field, the "
            f"schema digest, and the prompt-template digest."
        )
        self.key = key
        self.root = root


class FixtureProvenance(BaseModel):
    """What TR-033 requires beside every committed fixture.

    Four facts, and each answers a question that becomes unanswerable once the
    fixture is a month old: *when* was this recorded, *which* model actually
    answered, *what* code produced it, and *what did it cost*. Without them the
    store is anonymous generated data, which Principle I refuses.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    recorded_on: date = Field(
        description=(
            "The recording date. Supplies `pricing_timestamp` in replay mode "
            "(TR-043), widened to midnight UTC, so replaying one fixture "
            "reproduces one cost however long afterwards it runs — even across "
            "an effective-from boundary inside the pinned version."
        )
    )
    gen_ai_response_model: str = Field(
        description="The model that actually answered, which may differ from the one requested."
    )
    gateway_revision: str = Field(
        description=(
            "The git commit SHA of the recording run. What makes a fixture "
            "attributable to the code that produced it rather than to whatever "
            "the repository looks like now."
        )
    )
    gen_ai_usage_input_tokens: int = Field(ge=0)
    gen_ai_usage_output_tokens: int = Field(ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)

    def usage(self) -> AttemptUsage:
        """The token counts a replayed invocation is priced from (TR-056).

        Taken from the recording rather than re-estimated, which is what makes a
        replayed cost *reproducible* — an estimate would drift with whatever
        tokenizer the gateway happened to link against.
        """
        return AttemptUsage(
            input_tokens=self.gen_ai_usage_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
            output_tokens=self.gen_ai_usage_output_tokens,
        )

    def pricing_timestamp(self) -> datetime:
        """The recording date as midnight UTC (TR-057).

        The widening is stated here rather than left to whatever the driver does
        with a bare date, because an unstated conversion would resolve a
        different price entry on a machine in a different zone — and the
        boundary it would cross is exactly the one an `effective_from` sits on.
        """
        from datetime import UTC

        return datetime.combine(self.recorded_on, datetime.min.time(), tzinfo=UTC)


class StoredFixture(BaseModel):
    """A committed response and its provenance, read back together."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    content: str
    provenance: FixtureProvenance


class FixtureStore:
    """Reads and writes the committed fixture store.

    **Reading needs no configuration beyond the root.** Writing is `record`
    mode's business and is gated far upstream — this class will happily write if
    asked, because the gate belongs where the decision is made rather than
    scattered across every object that could act on it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _paths(self, key: str) -> tuple[Path, Path]:
        """Where a key's two files live.

        The `sha256:` label is replaced with a directory level rather than
        dropped: a flat store of many thousands of files is slow to list on
        every filesystem that matters, and keeping the algorithm as the
        directory name means a future algorithm change is visible in the layout
        instead of silently mixing two digest spaces in one folder.
        """
        if not FIXTURE_KEY_PATTERN.match(key):
            raise ValueError(
                f"{key!r} is not a fixture key. Expected `sha256:` followed by 64 "
                f"lowercase hex characters, which is the form the invocation "
                f"row's CHECK also enforces."
            )
        algorithm, _, digest = key.partition(":")
        directory = self.root / algorithm / digest[:2]
        return (
            directory / f"{digest}{_RESPONSE_SUFFIX}",
            directory / f"{digest}{_PROVENANCE_SUFFIX}",
        )

    def has(self, key: str) -> bool:
        response, provenance = self._paths(key)
        return response.is_file() and provenance.is_file()

    def load(self, key: str) -> StoredFixture:
        """Resolve a fixture, or raise (TR-022).

        Raises:
            FixtureMissError: No fixture for this key. **No network request is
                issued, and there is no path from here that could issue one** —
                a fallback to the provider is the tempting behaviour and the one
                that makes an offline suite quietly online.
        """
        response, provenance = self._paths(key)
        if not response.is_file() or not provenance.is_file():
            raise FixtureMissError(key, self.root)

        return StoredFixture(
            key=key,
            content=response.read_text(encoding="utf-8"),
            provenance=FixtureProvenance.model_validate_json(
                provenance.read_text(encoding="utf-8")
            ),
        )

    def save(self, key: str, content: str, provenance: FixtureProvenance) -> None:
        """Commit a fixture and its sidecar.

        Both or neither, as far as a single process can manage it: the sidecar
        is written **last**, so an interrupted write leaves a response with no
        provenance, which `has` and `load` both treat as absent. The other order
        would leave a provenance record for a response that does not exist —
        a fixture that claims to be labelled and has nothing to label.
        """
        response_path, provenance_path = self._paths(key)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(content, encoding="utf-8")
        provenance_path.write_text(
            json.dumps(json.loads(provenance.model_dump_json()), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def stored_keys(self) -> list[str]:
        """Every key with both files present, sorted.

        Named `stored_keys` rather than `keys` so the store does not read as a
        mapping it is not — it has no `__getitem__`, no `items`, and no
        `values`, and a name that implied otherwise would invite a caller to
        expect them.

        Used by the fixture-scan check (TR-030) and by the store's own
        consistency test. A response without its sidecar is deliberately absent
        from this list *and* reported by `orphans` below — silently skipping it
        would make an unlabelled fixture invisible to the check that exists to
        find unlabelled fixtures.
        """
        return sorted(
            f"{path.parent.parent.name}:{path.name[: -len(_RESPONSE_SUFFIX)]}"
            for path in self.root.rglob(f"*{_RESPONSE_SUFFIX}")
            if path.with_name(path.name[: -len(_RESPONSE_SUFFIX)] + _PROVENANCE_SUFFIX).is_file()
        )

    def orphans(self) -> list[Path]:
        """Files missing their counterpart, in either direction.

        TR-033 makes provenance mandatory, so an unlabelled response is a defect
        rather than a partial success — and a sidecar with no response is a
        label for nothing. Both are returned so the committed-store check can
        fail on either.
        """
        found: list[Path] = []
        for path in self.root.rglob(f"*{_RESPONSE_SUFFIX}"):
            stem = path.name[: -len(_RESPONSE_SUFFIX)]
            if not path.with_name(stem + _PROVENANCE_SUFFIX).is_file():
                found.append(path)
        for path in self.root.rglob(f"*{_PROVENANCE_SUFFIX}"):
            stem = path.name[: -len(_PROVENANCE_SUFFIX)]
            if not path.with_name(stem + _RESPONSE_SUFFIX).is_file():
                found.append(path)
        return sorted(found)
