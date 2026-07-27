"""The single module in this repository permitted to import the model provider.

Every traced call in every later epic reaches the provider through here. The
``protected`` import-linter contract in this package's ``pyproject.toml`` names
the provider distribution protected and this module its only allowed importer,
so a second import site fails the build rather than passing review.

Two properties this module exists to hold, both of which are asserted elsewhere
rather than merely intended here:

**The import is lazy** (TR-003, ADR-0014). The provider SDK is an optional
extra, and the import happens inside the call below rather than at module
scope, so the package imports and type-checks in an environment resolved
without it. A module-scope import would defeat that — and so would a
``TYPE_CHECKING``-guarded one, since ``exclude_type_checking_imports`` defaults
to false and the contract counts it.

**No SDK type escapes** (TR-002). The client is typed against `ProviderClient`,
a protocol defined here, not against the SDK's own class. Nothing this module
returns carries an SDK type into a signature a consumer can see.

This module also does no arithmetic. Cost, content hashing and duration live in
``gateway.compute``, which the computation-boundary contract forbids this
module from reaching (TR-032) — the orchestration module above both composes
them instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol, runtime_checkable

from gateway.config import CREDENTIAL_ENV_VAR
from gateway.errors import ProviderError, ProviderUnavailableError

# Pinned here rather than at each call site so the model in use is a property
# of the boundary, readable without grepping the callers.
DEFAULT_MODEL: Final[str] = "claude-opus-5"

#: The distribution that provides the client. Held as a name so this module can
#: report a useful install hint without a second file in the repository
#: spelling it out — `tests/checks/test_single_import_site.py` scans all of
#: `/src`, tests included, and asserts exactly one file names it.
_PROVIDER_DISTRIBUTION: Final[str] = "anthropic"

#: TR-010. Two retries, so three transport attempts, per model request. Both
#: names exist because the requirement states both numbers and a reader
#: checking the code against it should not have to do the arithmetic — that is
#: exactly where an off-by-one hides.
MAX_TRANSPORT_RETRIES: Final[int] = 2
MAX_TRANSPORT_ATTEMPTS: Final[int] = MAX_TRANSPORT_RETRIES + 1

__all__ = [
    "CredentialHandle",
    "DEFAULT_MODEL",
    "MAX_TRANSPORT_ATTEMPTS",
    "MAX_TRANSPORT_RETRIES",
    "ProviderClient",
    "RemainingTime",
    "load_client_class",
    "native_output_schema",
    "with_transport_budget",
]


@runtime_checkable
class ProviderClient(Protocol):
    """The shape this boundary needs from a provider client.

    A locally defined protocol rather than the SDK's class, and rather than a
    ``TYPE_CHECKING`` import of it, for two reasons that point the same way:
    the contract counts a guarded import as an import, and a signature naming
    the SDK's class would put an SDK type on a surface that must not carry one.

    Structural, so the real client satisfies it without being told to.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...


class RemainingTime(Protocol):
    """How much of the invocation's deadline is left, in seconds.

    A protocol taken as an argument rather than a clock consulted here, and
    that is a structural requirement rather than a testing convenience. TR-032
    forbids this module from reaching `gateway.compute`, where TR-028 places
    all duration arithmetic — so a deadline computed here would either put
    duration arithmetic in the module that must not hold it, or break the
    contract to fetch it. Asking an injected callable does neither: the
    orchestrator owns the clock, `gateway.compute` owns the subtraction, and
    this module only ever compares a number to zero and hands it onward.

    Returns a value that may be zero or negative, meaning the deadline has
    passed. Not raising on expiry is deliberate — TR-034 makes expiry a
    transport failure counted against TR-010's budget, and a callable that
    raised would decide that classification here instead of at the retry loop
    that owns it.
    """

    def __call__(self) -> float: ...


def native_output_schema(schema: Any) -> Mapping[str, Any] | None:
    """The caller's schema as the native structured-output mode will see it.

    TR-005. Submission itself passes the caller's schema straight to the
    client, which applies this transform internally — this function exists for
    the *other* half of the requirement: the constraints the mode cannot
    express are silently demoted to prose in the schema's description, and
    `gateway.validation.residual_constraints` needs the transformed form to say
    which ones those were.

    Returns:
        The transformed schema, or `None` when this SDK build does not expose
        its transform.

    **Best-effort, and the reason is disclosed rather than hidden.** The
    transform lives behind a private module path, so an SDK upgrade may move or
    rename it. `None` is returned rather than an exception because nothing
    *enforced* depends on this: `validate_or_repair` validates against the
    caller's schema in full, so every residual constraint is caught whether or
    not it was named in advance. Losing this loses an explanation, not a check.

    The fragility is nonetheless observed rather than tolerated —
    `tests/test_validation_repair.py` asserts the transform is reachable, so an
    upgrade that moves it fails a test instead of quietly returning `None`
    forever.
    """
    transform: Callable[[Any], Mapping[str, Any]] | None = None
    try:
        from anthropic.lib._parse._transform import transform_schema
    except (ImportError, ModuleNotFoundError):  # pragma: no cover - exercised by T015
        pass
    else:
        transform = transform_schema

    if transform is None:
        return None
    return transform(schema)


def with_transport_budget(
    attempt: Callable[[float], Any],
    remaining: RemainingTime,
    *,
    retryable: Callable[[BaseException], bool],
) -> tuple[Any, int]:
    """Run one model request under TR-010's budget and TR-034's deadline.

    Args:
        attempt: Issues one request. Receives the seconds remaining, which it
            passes to the client as that request's timeout — TR-034 forbids
            delegating the deadline to the SDK's own default, and the way to
            not delegate it is to state it on every call.
        remaining: Seconds left in the invocation's deadline.
        retryable: Whether a raised exception is worth another attempt. Injected
            because deciding it requires knowing the SDK's exception types, and
            a `retryable` written here would need them named in a second place.

    Returns:
        The successful attempt's return value and the number of transport
        attempts consumed — 1 through `MAX_TRANSPORT_ATTEMPTS`. Recorded on the
        invocation row (TR-010), which is why it is returned rather than
        counted internally and discarded.

    Raises:
        ProviderError: Every attempt failed, or the deadline expired. Normalized
            (TR-025) — no SDK exception escapes this module.

    Two properties TR-010 states that the loop is shaped to hold:

    **A request that succeeds first time issues exactly one request.** There is
    no speculative second call and no warm-up; the loop's body runs once and
    returns.

    **The transport budget is independent of the repair budget.** This function
    knows nothing about validation and `validate_or_repair` knows nothing about
    transport, so neither budget can consume the other's — a repair is a fresh
    call to this function with its own three attempts, which is what TR-010's
    "per model request" means.

    Deadline expiry is checked *before* each attempt rather than inferred from
    a timeout afterwards. Checking after would spend an attempt discovering
    time was already gone, and would make the attempt count depend on how long
    the provider took to fail.
    """
    last_status: int | None = None
    for attempt_number in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
        seconds_left = remaining()
        if seconds_left <= 0:
            # TR-034: an expiry is a transport failure, so it is reported with
            # the attempts already spent rather than as its own category.
            raise ProviderError(
                f"the per-request deadline expired after {attempt_number - 1} transport "
                f"attempt(s)",
                error_type="deadline_exceeded",
                status=last_status,
            )

        # Built inside the handler, raised outside it, for the same reason
        # `load_client_class` does: `raise ... from None` here would clear
        # `__cause__` and leave `__context__` holding the SDK exception, which
        # TR-064 forbids as squarely as an explicit chain. A provider error body
        # can echo request headers, so the difference is not academic — the
        # traceback the normalization exists to keep clean would render it.
        normalized: ProviderError | None = None
        try:
            return attempt(seconds_left), attempt_number
        except ProviderError:
            # Already normalized by an inner boundary; re-raising unchanged
            # keeps one normalization rather than wrapping a wrapped error.
            raise
        except BaseException as exc:  # noqa: BLE001 - narrowed by `retryable`
            if not retryable(exc) or attempt_number == MAX_TRANSPORT_ATTEMPTS:
                normalized = _normalized(exc, attempt_number)

        if normalized is not None:
            raise normalized

    raise AssertionError("unreachable: the loop returns or raises on every path")


def _normalized(exc: BaseException, attempts: int) -> ProviderError:
    """Turn an SDK exception into the gateway's own (TR-025, TR-064).

    Only status, error type, and a provider-issued request identifier cross
    this boundary. The original is neither chained nor stored: the caller
    raises `from None` and this function copies three scalars out, so nothing
    holds a reference that a traceback or a `repr` could render.

    `getattr` rather than isinstance checks against SDK exception classes,
    which would need the SDK named in a signature — the coupling TR-002
    forbids. Reading three attributes off an object works on any exception
    shape and needs no import.
    """
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    return ProviderError(
        f"the provider request failed after {attempts} transport attempt(s)",
        status=status if isinstance(status, int) else None,
        error_type="transport_failed",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def load_client_class() -> type[Any]:
    """Return the provider client class, importing the SDK on first use.

    The import is function-local by design (TR-003). Returning the class rather
    than an instance keeps this callable in an environment with no credential:
    constructing a client reads one from the environment, and the offline suite
    runs with none present (TR-023).

    Raises:
        ProviderUnavailableError: the ``provider`` extra is not installed.
            Raised as a gateway-owned error rather than letting
            ``ModuleNotFoundError`` escape, because an SDK-shaped failure
            crossing this boundary is the coupling the boundary exists to
            prevent. ADR-0014 records this runtime failure as the accepted cost
            of making the SDK optional.
    """
    # Raised *outside* the handler, and that placement is the whole point.
    # TR-064 forbids retaining the original as `__cause__` **or** as
    # `__context__`. `raise ... from None` inside the `except` block satisfies
    # only the first: it sets `__suppress_context__`, which stops the default
    # traceback renderer from printing the original, while `__context__` still
    # holds it and `exc.__context__` still hands it back. Leaving the handler
    # before raising is what actually clears it, so the property holds against
    # inspection and not only against rendering.
    client_class: type[Any] | None = None
    try:
        import anthropic
    except ModuleNotFoundError:  # pragma: no cover - exercised by T015
        pass
    else:
        client_class = anthropic.Anthropic

    if client_class is None:
        raise ProviderUnavailableError(
            "the provider SDK is not installed; add the extra with "
            f"`uv add 'gateway[provider]'` (missing distribution: {_PROVIDER_DISTRIBUTION})"
        )

    return client_class


class CredentialHandle:
    """The credential, read once and held where nothing renders it.

    TR-061. The requirement has three parts and each rules out an
    implementation that would otherwise be natural:

    **Read exactly once, at construction.** Not on each call. A value re-read
    per request would be re-read from an environment that can change under a
    long-running process, so two invocations in one run could use different
    credentials with nothing recording which.

    **Held off every object the gateway reprs, serializes, logs, or spools** —
    *the client handle included*, which must expose no attribute carrying the
    value. That last clause is why this class exists rather than the value
    living on the client: a client's `repr` is written by the SDK, changes
    between versions, and is exactly what a traceback renders when a frame
    holding one is captured.

    **`__repr__` and `__str__` are both overridden.** Overriding one is the
    common half-measure: `repr` appears in tracebacks and debuggers, `str` in
    f-strings and log messages, and a value safe in one and not the other leaks
    through whichever the next author reaches for.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> CredentialHandle:
        """Read the credential once, from the one key TR-062 fixes.

        Raises:
            ProviderUnavailableError: No credential. A configuration error —
                the fault is in how the environment was resolved, and it is
                detectable before a request is built. The message names the key
                and nothing about the value (TR-065).
        """
        import os

        source = os.environ if env is None else env
        value = (source.get(CREDENTIAL_ENV_VAR) or "").strip()
        if not value:
            raise ProviderUnavailableError(
                f"{CREDENTIAL_ENV_VAR} is not set, so no provider request can be "
                f"constructed. This is a configuration error rather than a "
                f"provider failure: nothing was called and nothing was billed."
            )
        return cls(value)

    def reveal(self) -> str:
        """The value, for the one call that constructs a client.

        Named `reveal` rather than exposed as a property or an attribute so
        every use is a visible verb at the call site — an attribute read looks
        like any other and would not draw a reviewer's eye.
        """
        return self._value

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {CREDENTIAL_ENV_VAR}=[REDACTED]>"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        """Covers `f"{handle}"` and `format(handle)`, which bypass `__str__`
        when a format spec is present — the case an override of `__str__` alone
        would miss."""
        del spec
        return repr(self)

    def __reduce__(self) -> tuple[Any, ...]:
        """Refuse to pickle.

        Serialization is one of the sinks TR-061 names, and a handle that
        pickled would put the value in whatever the pickle was written to —
        a cache, a queue, a crash dump. Refusing is the fail-closed direction.
        """
        raise TypeError(
            "a credential handle must not be serialized (TR-061); the value is "
            "read once at construction and held off every serialized object"
        )
