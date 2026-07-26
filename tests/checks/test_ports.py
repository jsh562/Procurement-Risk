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


def a_free_port() -> int:
    """A port the OS says is free right now, chosen by the OS itself."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


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
