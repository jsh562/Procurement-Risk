"""Unit tests for the host-port resolver.

A resolver that has only ever been observed returning its preferred port has
not been shown resolving anything. Every case here occupies a port for real —
by binding a socket — rather than stubbing `is_bindable`, because the property
under test is agreement with the operating system, and a stub agrees with
itself by construction.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.checks.helpers.ports import (
    CONVENTIONAL,
    MAX_PORT,
    SEARCH_SPAN,
    Holder,
    Resolution,
    is_bindable,
    resolve_host_port,
)


@contextmanager
def occupied(port: int) -> Iterator[None]:
    """Hold `port` for the duration of the block."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        yield
    finally:
        holder.close()


#: How far above a drawn port the cases below walk. Every one of them either
#: hands the port to `resolve_host_port`, which searches `SEARCH_SPAN` upward,
#: or occupies that span plus one to exhaust it.
WALKED_ABOVE = SEARCH_SPAN + 2


def a_free_port(headroom: int = WALKED_ABOVE) -> int:
    """A port the OS says is free right now, with `headroom` ports above it.

    The headroom is the fix for a real flake, not a precaution. The OS draws
    from the ephemeral range, whose top *is* `MAX_PORT`, so roughly one draw in
    two hundred landed within a search span of the ceiling — and then
    `range(start, start + SEARCH_SPAN + 2)` produced numbers above 65535, which
    `bind` rejects with `OverflowError` rather than the `OSError` the occupying
    loop catches. It failed once and passed on the next two runs.

    Redrawn rather than clamped: a port the OS did not hand out is not known to
    be free, and computing one would have the cases probe a port some unrelated
    process may hold, which is the false failure this module exists to avoid.
    """
    for _ in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port + headroom <= MAX_PORT:
            return port
    pytest.skip(f"every ephemeral draw landed within {headroom} ports of {MAX_PORT}")


def test_a_free_preferred_port_is_returned_unchanged() -> None:
    port = a_free_port()
    resolution = resolve_host_port(port, name="db")
    assert resolution.port == port
    assert not resolution.substituted
    assert resolution.holder is None


def test_an_occupied_preferred_port_is_substituted() -> None:
    port = a_free_port()
    with occupied(port):
        resolution = resolve_host_port(port, name="db")
    assert resolution.substituted
    assert resolution.port != port
    assert resolution.port > port


def test_the_substitute_is_actually_bindable() -> None:
    """The returned port must be usable, not merely different."""
    port = a_free_port()
    with occupied(port):
        resolution = resolve_host_port(port, name="db")
        assert is_bindable(resolution.port)


@contextmanager
def occupied_dual_stack(port: int) -> Iterator[None]:
    """Hold `port` the way a Node server does: `::` with `IPV6_V6ONLY` cleared.

    This is the shape that defeated the IPv4-only probe. Skips rather than fails
    where the machine has no usable IPv6 stack, because a bind that never
    succeeded proves nothing about a resolver.
    """
    holder = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        try:
            holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            holder.bind(("::", port))
        except OSError as exc:
            pytest.skip(f"no usable dual-stack IPv6 on this machine: {exc}")
        holder.listen(1)
        yield
    finally:
        holder.close()


def test_a_dual_stack_ipv6_listener_is_seen_as_occupied() -> None:
    """The false negative that cost a misdiagnosis.

    An orphaned `next` dev server binds `::` dual-stack. Probing only
    `127.0.0.1` — or even `0.0.0.0` — succeeds against that holder on Windows, so
    `is_bindable` reported the port free while `docker compose up` still failed
    to publish it. Four orchestration checks failed and the dev server was twice
    ruled out as the cause.

    Asserted at both the probe and the resolver, because a resolver that walked
    on a correct probe would still be wrong if it walked to another held port.
    """
    port = a_free_port()
    with occupied_dual_stack(port):
        assert not is_bindable(port), (
            f"{port} is held by a dual-stack IPv6 listener and was reported bindable"
        )
        resolution = resolve_host_port(port, name="web")
        assert resolution.substituted
        assert resolution.port != port
        assert is_bindable(resolution.port)


def test_an_unusable_ipv6_stack_does_not_condemn_every_port() -> None:
    """The failure mode the fix must not introduce.

    Only `EADDRINUSE` may count as occupied. If any IPv6 error meant "taken",
    a machine without IPv6 would report every port unavailable and the search
    would raise instead of resolving — turning a portability gap into a hard
    stop. A port the OS just handed out must still read as free.
    """
    port = a_free_port()
    assert is_bindable(port)
    assert resolve_host_port(port, name="db").port == port


def test_a_substitute_is_never_a_conventional_default() -> None:
    """SC-010's guarantee survives substitution.

    Walking upward from 5431 reaches 5432 and 5433 first — both conventional,
    both usually free on a machine that moved off them. A resolver that only
    asked "is it free" would hand back exactly the port the criterion forbids.
    """
    with occupied(5431):
        resolution = resolve_host_port(5431, name="db")
    assert resolution.port not in CONVENTIONAL
    assert resolution.port > 5433


def test_every_conventional_port_is_skipped_when_walking_past_it() -> None:
    for start in (79, 442, 5431, 7999, 8079):
        if not is_bindable(start):
            continue
        with occupied(start):
            resolution = resolve_host_port(start, name="probe")
        assert resolution.port not in CONVENTIONAL, (
            f"walking from {start} landed on conventional {resolution.port}"
        )


def test_a_holder_that_is_not_a_container_is_reported_as_unidentified() -> None:
    """A socket held by this process is not a container, and resolution must
    still succeed — failing to name the holder is not failing to resolve."""
    port = a_free_port()
    with occupied(port):
        resolution = resolve_host_port(port, name="db")
    assert resolution.substituted
    described = resolution.describe()
    assert str(resolution.port) in described
    assert str(port) in described


def test_an_exhausted_search_fails_rather_than_returning_a_taken_port() -> None:
    """The failure path is a real answer, not a hang or a bad port."""
    start = a_free_port()
    span = range(start, start + SEARCH_SPAN + 2)
    assert span[-1] <= MAX_PORT, (
        f"the span to occupy runs past {MAX_PORT}; `a_free_port` is supposed to "
        "leave room for it, and binding an out-of-range port raises OverflowError "
        "rather than the OSError below"
    )
    held = []
    try:
        for candidate in span:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", candidate))
                sock.listen(1)
                held.append(sock)
            except OSError:
                sock.close()
        if len(held) < SEARCH_SPAN + 1:
            pytest.skip("could not occupy a contiguous span on this machine")
        with pytest.raises(RuntimeError, match="no free host port"):
            resolve_host_port(start, name="db")
    finally:
        for sock in held:
            sock.close()


def test_a_preferred_port_near_the_ceiling_fails_cleanly_rather_than_overflowing() -> None:
    """The search stops at `MAX_PORT` instead of generating numbers past it.

    A preferred port within `SEARCH_SPAN` of the ceiling used to put candidates
    above 65535 into the loop. Those are not ports that happen to be taken:
    `bind` rejects them with `OverflowError`, which is not an availability answer
    and which nothing in the resolver or its callers catches.

    Every port from the preferred one to the ceiling is declared taken through
    `published`, so the case exercises the boundary rather than depending on
    which high ports this machine happens to hold.
    """
    preferred = MAX_PORT - 3
    published = {port: Holder("ceiling") for port in range(preferred, MAX_PORT + 1)}
    with pytest.raises(RuntimeError, match="no free host port"):
        resolve_host_port(preferred, name="db", published=published)


def test_a_docker_published_port_is_refused_even_when_the_socket_binds() -> None:
    """The case that made the first version of this resolver wrong.

    On Docker Desktop a published port lives in the VM's namespace, so binding
    it on the host succeeds while `docker compose up` still fails with "port is
    already allocated". Measured, not assumed: a socket probe reported 5434 free
    while a sibling checkout was publishing it. Docker's own listing is
    therefore consulted first, and it must win.
    """
    port = a_free_port()
    assert is_bindable(port), "precondition: the socket layer says this port is free"
    published = {port: Holder("sibling-db-1", "kayademoprocurementrisk1")}

    resolution = resolve_host_port(port, name="db", published=published)

    assert resolution.substituted, "a Docker-published port must not be handed back"
    assert resolution.port != port
    assert resolution.holder is not None
    assert "sibling-db-1" in resolution.describe()


def test_the_search_skips_every_docker_published_port_on_the_way_past() -> None:
    base = a_free_port()
    published = {
        base: Holder("first"),
        base + 1: Holder("second"),
        base + 2: Holder("third"),
    }
    resolution = resolve_host_port(base, name="db", published=published)
    assert resolution.port >= base + 3
    assert resolution.port not in published


def test_both_sources_must_agree_before_a_port_is_used() -> None:
    """Neither source alone is sufficient, so neither alone may clear a port."""
    port = a_free_port()
    with occupied(port):
        # Docker says nothing is published; the socket layer says taken.
        resolution = resolve_host_port(port, name="db", published={})
    assert resolution.substituted, "a socket-held port must not be handed back"


def test_the_published_map_parses_a_real_docker_ps_shape() -> None:
    """Guards the parse against Docker's actual multi-binding output."""
    from tests.checks.helpers.ports import PORT_PUBLICATION

    line = "0.0.0.0:5434->5432/tcp, [::]:5434->5432/tcp"
    assert [int(m.group(1)) for m in PORT_PUBLICATION.finditer(line)] == [5434, 5434]


def test_is_bindable_agrees_with_the_operating_system() -> None:
    port = a_free_port()
    assert is_bindable(port)
    with occupied(port):
        assert not is_bindable(port)
    assert is_bindable(port)


def test_a_substitution_describes_the_default_it_replaced() -> None:
    """The announcement must carry enough to act on: which default, which
    substitute, and who is holding the default."""
    resolution = Resolution(
        name="db", preferred=5434, port=5435, holder=Holder("other-db", "siblingproject")
    )
    described = resolution.describe()
    assert "5434" in described
    assert "5435" in described
    assert "other-db" in described
    assert "siblingproject" in described


def test_an_unsubstituted_resolution_says_so_plainly() -> None:
    assert "committed default" in Resolution(name="db", preferred=5434, port=5434).describe()
