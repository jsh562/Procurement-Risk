"""Gateway configuration, read once and passed down.

TR-034. The per-request deadline is the whole of this module in Phase 2's
successor; mode selection, the provider opt-in, the fixture-store roots and the
price-table pin attach here in later phases, and the type is shaped for that
rather than reshaped three times.

**Read, never ambient.** Every value here arrives as a field on a
`GatewayConfig` a caller can construct explicitly, and `load_config` is the one
place that consults the process environment. A module that read
`os.environ` where it needed a value would make the effective configuration a
property of import order — and would make the deadline untestable without
mutating global state, which is how a test suite acquires an ordering
dependency it cannot see.

**The deadline is the gateway's, not the SDK's** (TR-034, explicitly). A
provider SDK ships its own default timeout, and delegating to it would put the
number outside this repository, outside the record, and outside anyone's reach
when it needs changing. It would also break the accounting TR-010 requires: an
SDK-internal timeout is an SDK-internal retry, invisible to the transport
attempt count the invocation record carries.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gateway.errors import GatewayConfigError

__all__ = [
    "CONTENT_CAPTURE_ENABLED_VALUE",
    "CONTENT_CAPTURE_ENV_VAR",
    "CREDENTIAL_ENV_VAR",
    "configuration_failure_message",
    "content_capture_enabled",
    "credential_is_present",
    "MODES",
    "MODE_ENV_VAR",
    "PROVIDER_OPT_IN_ENV_VAR",
    "PROVIDER_OPT_IN_PERMITTED_VALUE",
    "RECORD_MODE",
    "REPLAY_MODE",
    "provider_calls_permitted",
    "require_no_credential_in_replay",
    "require_provider_opt_in",
    "resolve_mode",
    "DATABASE_URL_ENV_VAR",
    "DEADLINE_ENV_VAR",
    "FIXTURE_ROOT_ENV_VAR",
    "PRICE_TABLE_PIN_ENV_VAR",
    "SPOOL_PATH_ENV_VAR",
    "DEFAULT_REQUEST_DEADLINE_SECONDS",
    "OTEL_GENAI_SEMCONV_VERSION",
    "GatewayConfig",
    "load_config",
]

#: TR-070. The OpenTelemetry generative-AI semantic-convention release the
#: recorded column names follow. One of exactly three places this value is
#: written, and the three must agree: here, the `COMMENT ON TABLE
#: llm_invocation` mirror, and TR-070 itself. `test_field_naming.py` (T048)
#: asserts the agreement, so a bump that updates one and forgets another fails
#: the build rather than leaving a database whose comment describes a different
#: convention from the code that wrote it.
#:
#: **Corrected from 1.36.0 to 1.37.0 by T026**, which is the verification
#: TR-070 requires rather than a preference. The registry at tag `v1.36.0`
#: defines `gen_ai.system`, not `gen_ai.provider.name` — the very attribute the
#: pin was selected for. `v1.37.0` defines `gen_ai.provider.name` and marks
#: `gen_ai.system` deprecated and replaced by it. 1.37.0 is the first release
#: satisfying the criterion; picking a later one would have changed more than
#: the evidence called for.
#:
#: Deliberately **not** readable from the environment. Every other value in this
#: module is configuration; this one is a pin, and a pin an operator can move
#: without a migration is not a pin — the column names in a migrated database
#: would no longer follow the version the configuration claims.
OTEL_GENAI_SEMCONV_VERSION: Final[str] = "1.37.0"

#: TR-034 states the number. Held as a named constant rather than a literal
#: default in the field, so the requirement's value is greppable and the two
#: cannot drift.
DEFAULT_REQUEST_DEADLINE_SECONDS: Final[float] = 120.0

#: The environment variable the deadline is read from. `GATEWAY_`-prefixed like
#: the mode and opt-in variables the spec names (TR-021, TR-063), so the
#: gateway's configuration surface is one greppable prefix rather than a set of
#: unrelated names.
DEADLINE_ENV_VAR: Final[str] = "GATEWAY_REQUEST_DEADLINE_SECONDS"

#: Deliberately **not** `GATEWAY_`-prefixed. This is E001's frozen variable, the
#: one the migration runner and every entry already read; a second spelling for
#: the same connection would let the gateway write records to one database while
#: the migrations built another, and the two would agree right up until they did
#: not.
DATABASE_URL_ENV_VAR: Final[str] = "DATABASE_URL"

#: The price-table pin (TR-048). Prefixed, because unlike the connection this is
#: the gateway's own setting and no other entry has an opinion about it.
PRICE_TABLE_PIN_ENV_VAR: Final[str] = "GATEWAY_PRICE_TABLE_VERSION"

SPOOL_PATH_ENV_VAR: Final[str] = "GATEWAY_SPOOL_PATH"

#: Where committed fixtures are read from and recorded to (TR-022, TR-033).
#:
#: **It needs an override because the default is derived from this package's own
#: location, and that location moves.** `orchestrator.DEFAULT_FIXTURE_ROOT`
#: walks up from `orchestrator.py` to the gateway entry root, which is correct
#: in this checkout and wrong in every consumer that installs the package:
#: resolved from `src/model/.venv/Lib/site-packages/gateway/`, the same walk
#: lands on `src/model/.venv/Lib/fixtures`, which does not exist. A consumer
#: keeping its own fixtures — E006 keeps them at `src/model/fixtures/` — had no
#: way to say so, so `replay` mode missed on every request and each miss
#: reported itself as an unrecorded one.
#:
#: A default, not a fallback. An unset variable keeps the gateway's own store,
#: so nothing that works today changes; a set one is used as given, and a set
#: one naming a directory that does not exist is a miss reported by
#: `FixtureMissError` naming the root, which is the actionable message.
FIXTURE_ROOT_ENV_VAR: Final[str] = "GATEWAY_FIXTURE_ROOT"

#: TR-021. Exactly two, and **no default**. Not `record`, not `replay`, not
#: "whichever the credential suggests" — a default here would mean an operator
#: who configured nothing gets one of the two behaviours anyway, and the two
#: differ by whether real money is spent.
MODE_ENV_VAR: Final[str] = "GATEWAY_MODE"
RECORD_MODE: Final[str] = "record"
REPLAY_MODE: Final[str] = "replay"
MODES: Final[frozenset[str]] = frozenset({RECORD_MODE, REPLAY_MODE})

#: TR-063. The opt-in gating `record` mode, as **one named control with a fixed
#: form**: permitted only when set to exactly `1`, with absence or any other
#: value denying it.
#:
#: Deliberately separate from mode selection (TR-027), so reaching the provider
#: takes two independent decisions rather than one. Selecting `record` by
#: accident is a configuration slip; selecting it *and* setting this is a
#: choice. The fixed form is what lets `tests/checks/test_ci_provider_gate_
#: absent.py` assert the control's absence from every CI environment — an
#: unfixed "some separate opt-in" is not something a check can look for.
PROVIDER_OPT_IN_ENV_VAR: Final[str] = "GATEWAY_ALLOW_PROVIDER_CALLS"
PROVIDER_OPT_IN_PERMITTED_VALUE: Final[str] = "1"

#: The provider credential's variable. Named here so the `replay` guard can
#: assert its **absence**; its *value* is never read by this module, never
#: stored on the configuration, and never logged (TR-061).
#:
#: That this is the *only* mention of the provider's name outside
#: `provider.py` is not incidental. `tests/checks/test_single_import_site.py`
#: requires exactly one file in `/src` to name the distribution, matching
#: case-sensitively as a whole word — so the uppercase spelling here is outside
#: the match and this constant does not become a second naming site. The
#: credential's variable name is fixed by the provider and cannot be spelled
#: otherwise, which is why it is a constant here rather than a literal at each
#: use.
#:
#: The first draft of this comment named the distribution in prose while
#: explaining that rule, and the scan caught it — the same self-referential trap
#: three of this epic's checks hit. Do not restate the rule using the word.
CREDENTIAL_ENV_VAR: Final[str] = "ANTHROPIC_API_KEY"

#: TR-026. Prompt and completion content in log output, **off by default**. The
#: default is the requirement, not a preference: logs are the largest and
#: least-reviewed sink in the system.
CONTENT_CAPTURE_ENV_VAR: Final[str] = "GATEWAY_CAPTURE_CONTENT"
CONTENT_CAPTURE_ENABLED_VALUE: Final[str] = "1"


class GatewayConfig(BaseModel):
    """Everything the gateway needs to know before it builds a request.

    Frozen, because configuration that can change mid-invocation makes the
    deadline a moving target and the record's account of it a guess.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_deadline_seconds: float = Field(
        default=DEFAULT_REQUEST_DEADLINE_SECONDS,
        gt=0,
        description=(
            "The outer bound on one invocation, covering every transport attempt "
            "TR-010 permits rather than each attempt separately."
        ),
    )

    otel_genai_semconv_version: str = Field(
        default=OTEL_GENAI_SEMCONV_VERSION,
        description=(
            "TR-070's pin, carried on the configuration so a caller can read "
            "which convention release the recorded field names follow without "
            "querying the database for its table comment."
        ),
    )

    database_url: str | None = Field(
        default=None,
        description=(
            "Where the gateway opens its **own** connection (TR-035). Its own, "
            "not a caller's: the record must commit in a transaction "
            "independent of any the caller holds, so a caller rollback cannot "
            "erase a trace of a call that was billed."
        ),
    )

    price_table_version_id: str | None = Field(
        default=None,
        description=(
            "The pinned price-table version every cost is computed against. "
            "TR-048 requires it to resolve to an existing row *before* any "
            "provider request is constructed — an unresolvable pin is a "
            "configuration error on an invocation that never billed, rather "
            "than a foreign-key failure on the write path after one did."
        ),
    )

    spool_path: Path | None = Field(
        default=None,
        description=(
            "The local append-only spool (TR-041), under the gateway's own "
            "root. `None` means the default beside the working directory; the "
            "field exists so a test can point it somewhere disposable without "
            "the module reading an environment variable at import time."
        ),
    )

    fixture_root: Path | None = Field(
        default=None,
        description=(
            "Where committed fixtures are resolved from (TR-022). `None` means "
            "the gateway's own store, which is what every existing caller gets "
            "and why this is a default rather than a fallback. A consumer that "
            "keeps its own fixtures sets it, because the gateway's default is "
            "derived from this package's installed location and resolves to a "
            "directory that does not exist once the package is installed "
            "anywhere but this checkout."
        ),
    )


def load_config(env: Mapping[str, str] | None = None) -> GatewayConfig:
    """Build the configuration from the environment.

    Args:
        env: The mapping to read. Defaults to the process environment. Taken as
            a parameter so a test can supply one without mutating `os.environ`
            — a suite that mutates it acquires an ordering dependency between
            tests that nothing in the suite makes visible.

    Returns:
        The resolved configuration. Absent variables take their documented
        defaults; a present-but-unusable value is an error rather than a
        silent fallback, because falling back would let a typo run for months
        under a deadline nobody chose.

    Raises:
        GatewayConfigError: A variable is present and unusable. The message
            names the key and the constraint. TR-065's exclusion set is scoped
            to the credential key and to values read from it; a malformed
            deadline is neither, so its value is echoed — withholding it would
            cost the reader the one fact that identifies the typo, and would
            be secrecy theatre over a number.
    """
    source = os.environ if env is None else env

    # Read once here rather than at each use. TR-065 bounds what a configuration
    # error may say about the *credential* key; a database URL carries a
    # password too, so it is never echoed in a message either — the key's name
    # is permitted and the value is not.
    database_url = (source.get(DATABASE_URL_ENV_VAR) or "").strip() or None
    price_pin = (source.get(PRICE_TABLE_PIN_ENV_VAR) or "").strip() or None
    spool = (source.get(SPOOL_PATH_ENV_VAR) or "").strip()
    spool_path = Path(spool) if spool else None
    fixtures = (source.get(FIXTURE_ROOT_ENV_VAR) or "").strip()
    fixture_root = Path(fixtures) if fixtures else None

    raw = source.get(DEADLINE_ENV_VAR)
    if raw is None:
        return GatewayConfig(
            database_url=database_url,
            price_table_version_id=price_pin,
            spool_path=spool_path,
            fixture_root=fixture_root,
        )

    try:
        deadline = float(raw)
    except ValueError:
        raise GatewayConfigError(
            f"{DEADLINE_ENV_VAR} must be a number of seconds; got {raw!r}"
        ) from None

    try:
        return GatewayConfig(
            request_deadline_seconds=deadline,
            database_url=database_url,
            price_table_version_id=price_pin,
            spool_path=spool_path,
            fixture_root=fixture_root,
        )
    except ValidationError:
        # Re-raised as a gateway-owned error rather than allowed to escape:
        # `ValidationError` is pydantic's type, and a caller catching
        # `GatewayError` would miss it (TR-002). `from None` is safe here in a
        # way it is not in `provider.py` — this raise is inside the handler, so
        # `__context__` is set, and unlike the provider path there is no
        # requirement forbidding it and nothing sensitive in the chain.
        raise GatewayConfigError(
            f"{DEADLINE_ENV_VAR} must be greater than zero; got {raw!r}"
        ) from None


def resolve_mode(env: Mapping[str, str] | None = None) -> str:
    """The resolution mode, chosen explicitly or not at all (TR-021).

    **No default and no implicit fallback.** The two modes differ by whether the
    invocation spends real money, so an unset variable is a decision nobody
    made rather than a decision to do the cheaper thing. Defaulting to `replay`
    would be the safe-looking choice and would still be wrong: a `record`-mode
    run that silently replayed would report costs and fixtures for calls it
    never made.

    Raises:
        GatewayConfigError: No mode selected, or a value outside the two. Both
            fail *before* any request is constructed, so neither costs a
            provider call — an invocation with no mode never happens, which is
            why it is outside TR-011's denominator.
    """
    source = os.environ if env is None else env
    selected = (source.get(MODE_ENV_VAR) or "").strip()

    if not selected:
        raise GatewayConfigError(
            f"{MODE_ENV_VAR} is not set. It has no default: `{RECORD_MODE}` reaches "
            f"the provider and `{REPLAY_MODE}` resolves from committed fixtures, and "
            f"guessing between them either spends money nobody authorised or "
            f"reports results for calls that never happened (TR-021)."
        )
    if selected not in MODES:
        raise GatewayConfigError(
            f"{MODE_ENV_VAR} is {selected!r}, which is not one of "
            f"{sorted(MODES)}. There is no nearest match and no fallback (TR-021)."
        )
    return selected


def provider_calls_permitted(env: Mapping[str, str] | None = None) -> bool:
    """Whether the `record`-mode opt-in is set to exactly its permitted value.

    TR-063 fixes the form: `1` permits, and **absence or any other value
    denies**. `true`, `yes`, `on` and `TRUE` all deny — deliberately, because a
    control whose spelling is negotiable is one a check cannot assert the
    absence of, and asserting its absence from every CI environment is half of
    what this control is for.
    """
    source = os.environ if env is None else env
    return source.get(PROVIDER_OPT_IN_ENV_VAR) == PROVIDER_OPT_IN_PERMITTED_VALUE


def require_provider_opt_in(env: Mapping[str, str] | None = None) -> None:
    """Refuse `record` mode unless opted in (TR-027, TR-063).

    Raises:
        GatewayConfigError: The opt-in is absent or malformed. Named, so an
            operator learns which control to set — TR-065 permits naming the
            key, and this one carries no credential material.
    """
    if not provider_calls_permitted(env):
        raise GatewayConfigError(
            f"{RECORD_MODE} mode reaches the provider and costs money, so it is "
            f"gated behind {PROVIDER_OPT_IN_ENV_VAR}={PROVIDER_OPT_IN_PERMITTED_VALUE} "
            f"— a control separate from mode selection, so reaching the provider "
            f"takes two decisions rather than one (TR-027, TR-063)."
        )


def require_no_credential_in_replay(env: Mapping[str, str] | None = None) -> None:
    """Refuse `replay` mode when a provider credential is present (TR-023).

    **Why a present credential is a failure in a mode that never uses one.** It
    is the only evidence available that the offline claim is being tested
    offline. `replay` resolving from fixtures is easy to believe and hard to
    verify — a gateway that quietly fell back to the provider would produce the
    same results, faster, and cost money nobody was watching for. Refusing to
    run at all when the means of cheating is present makes the claim structural.

    The **absence** is checked; the value is never read (TR-061). A
    developer machine holding a credential can still run the full offline suite
    — the harness executes in a child environment with the variable removed
    (`tests/conftest.py`), so the guard is satisfied by the harness rather than
    by anyone remembering to unset their shell.

    Raises:
        GatewayConfigError: A credential is present. The message names the key
            and never any part of the value, not even its length (TR-065).
    """
    source = os.environ if env is None else env
    if (source.get(CREDENTIAL_ENV_VAR) or "").strip():
        raise GatewayConfigError(
            f"{REPLAY_MODE} mode refuses to run while {CREDENTIAL_ENV_VAR} is set "
            f"in this process's environment. The mode resolves from committed "
            f"fixtures and reaches no network, and its absence is the only "
            f"evidence that the offline claim is being tested offline (TR-023). "
            f"Run the harness in a child environment with the variable removed "
            f"rather than unsetting it in your shell."
        )


def credential_is_present(env: Mapping[str, str] | None = None) -> bool:
    """Whether the one credential key holds a usable value (TR-062).

    **Exactly one key, matched by exact name.** No prefix match, no
    case-insensitive match, no scan of neighbouring names — so TR-023's guard
    has one checkable subject rather than an intent, and cannot disagree with
    the SDK's own resolution, which reads the same name.

    Absent means unset, empty, or whitespace-only; present means anything else.
    An exported-but-blank variable is a broken shell far more often than a
    considered instruction, and treating it as present would refuse `replay`
    mode on a machine that has no credential at all.

    **Disclosed limit** (TR-062). This observes the gateway's own process
    environment and nothing else. A credential reaching the SDK by another path
    — an SDK configuration file, an explicit argument, a platform keychain —
    is neither observed nor blocked. `replay` mode's no-credential property is
    therefore a property of *this* environment, not a proof that no credential
    exists anywhere the process could reach.
    """
    source = os.environ if env is None else env
    return bool((source.get(CREDENTIAL_ENV_VAR) or "").strip())


def content_capture_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether prompt and completion content may appear in log output (TR-026).

    **Off by default**, and the default is the requirement rather than a
    preference: content in logs is the largest, least-reviewed sink in the
    system, and a capture toggle that defaulted on would put prompt text in
    every operator's log aggregator before anyone decided it should be there.

    Enabling it does not make it unconstrained (TR-066): captured content
    remains subject to redaction and to the fail-closed rule, must not be
    enabled in continuous integration or in any run that writes to the committed
    repository, and its output must not be committed.

    Scoped to **log output alone**. The invocation record and the normalized
    gateway error carry no prompt or completion content at any setting of this
    toggle, because both field sets are closed and neither names one — so this
    toggle cannot widen them even if someone wanted it to.
    """
    source = os.environ if env is None else env
    return source.get(CONTENT_CAPTURE_ENV_VAR) == CONTENT_CAPTURE_ENABLED_VALUE


def configuration_failure_message(key: str, detail: str = "") -> str:
    """A configuration-failure message bounded by TR-065's exclusion set.

    The rule the set states, in the order it is stated: the message **MUST NOT**
    contain the credential value, nor any substring, truncation, prefix, suffix,
    hash, or length of it, nor any other value read from the credential
    configuration key. It **MUST** name the key that is missing or unusable.

    Naming the key is what makes the message actionable; withholding the value
    is what keeps it safe. Both halves are needed — a message that named neither
    would be safe and useless.

    The value is never passed to this function, which is stronger than
    redacting it here: a redaction step can be forgotten, and a parameter that
    does not exist cannot be.
    """
    if key == CREDENTIAL_ENV_VAR and detail:
        raise ValueError(
            "a configuration-failure message for the credential key must carry no "
            "detail drawn from its value — not a truncation, not a length, not a "
            "hash (TR-065). Name the key alone."
        )
    return f"{key} is missing or unusable{f': {detail}' if detail else ''}"
