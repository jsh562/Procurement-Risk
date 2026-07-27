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
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gateway.errors import GatewayConfigError

__all__ = [
    "DEADLINE_ENV_VAR",
    "DEFAULT_REQUEST_DEADLINE_SECONDS",
    "GatewayConfig",
    "load_config",
]

#: TR-034 states the number. Held as a named constant rather than a literal
#: default in the field, so the requirement's value is greppable and the two
#: cannot drift.
DEFAULT_REQUEST_DEADLINE_SECONDS: Final[float] = 120.0

#: The environment variable the deadline is read from. `GATEWAY_`-prefixed like
#: the mode and opt-in variables the spec names (TR-021, TR-063), so the
#: gateway's configuration surface is one greppable prefix rather than a set of
#: unrelated names.
DEADLINE_ENV_VAR: Final[str] = "GATEWAY_REQUEST_DEADLINE_SECONDS"


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

    raw = source.get(DEADLINE_ENV_VAR)
    if raw is None:
        return GatewayConfig()

    try:
        deadline = float(raw)
    except ValueError:
        raise GatewayConfigError(
            f"{DEADLINE_ENV_VAR} must be a number of seconds; got {raw!r}"
        ) from None

    try:
        return GatewayConfig(request_deadline_seconds=deadline)
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
